# Métriques d'évaluation du module Memory (utilisées par la commande evaluate_memory)
import json
import re
import statistics
import unicodedata
from functools import lru_cache

from .services import FRENCH_STOP_WORDS, STORY_STYLES, AIServiceError, call_gemini

LENGTH_MIN, LENGTH_MAX = 120, 250        # longueur demandée à Gemini dans le prompt


def normalize(text):
    """Minuscules sans accents : « Grand-Mère » -> « grand-mere »."""
    text = unicodedata.normalize('NFKD', text.lower())
    return ''.join(c for c in text if not unicodedata.combining(c)).replace('œ', 'oe').replace('’', "'")


# ---------- Récits : grille qualitative ----------

def fact_recall(story, facts):
    """Part des faits clés retrouvés dans le récit. Chaque fait = liste de formulations acceptées."""
    text = normalize(story)
    missing = [forms[0] for forms in facts if not any(normalize(f) in text for f in forms)]
    found = len(facts) - len(missing)
    return found / len(facts), missing


def inventions_found(story, inventions):
    """Éléments absents de l'entrée qui apparaissent quand même dans le récit (hallucinations simples)."""
    text = normalize(story)
    return [word for word in inventions if normalize(word) in text]


def word_count(text):
    return len(re.findall(r"\w+(?:['-]\w+)*", text))


def length_score(words):
    """Note sur 5 : 5 dans la fourchette demandée, puis -1 par tranche de 15 % d'écart."""
    if LENGTH_MIN <= words <= LENGTH_MAX:
        return 5
    gap = (LENGTH_MIN - words) / LENGTH_MIN if words < LENGTH_MIN else (words - LENGTH_MAX) / LENGTH_MAX
    return max(1, 5 - 1 - int(gap / 0.15))


def recall_to_score(recall, inventions=0):
    """Fidélité sur 5 : 1 + 4 x rappel des faits, moins 1 point par invention (minimum 1)."""
    return max(1, min(5, round(1 + 4 * recall) - inventions))


JUDGE_PROMPT = """Tu évalues un récit de journal intime écrit par une IA.
Style demandé : {style}, c'est-à-dire {definition}.
Texte original de l'utilisateur :
{original}

Récit à évaluer :
{story}

Donne une note de 1 à 5 pour le respect du style demandé (5 = parfaitement dans le style, naturel et agréable à lire).
Réponds UNIQUEMENT en JSON, sans texte autour : {{"style": <1-5>, "commentaire": "<une phrase en français>"}}"""


def judge_style(original, story, style):
    """Note de style par un autre modèle Gemini (méthode « LLM-as-a-judge »).
    Le juge reçoit la même définition du style que celle donnée au rédacteur."""
    from django.conf import settings
    definition = STORY_STYLES.get(style, STORY_STYLES['Simple'])
    prompt = JUDGE_PROMPT.format(style=style, definition=definition, original=original, story=story)
    answer = call_gemini([prompt], model=settings.GEMINI_JUDGE_MODEL)
    match = re.search(r'\{.*\}', answer, re.S)
    if not match:
        raise AIServiceError("Le juge n'a pas renvoyé de JSON.")
    data = json.loads(match.group(0))
    return max(1, min(5, int(data['style']))), str(data.get('commentaire', ''))


# ---------- Rétrospectives : Precision@5 des mots-clés ----------

def keyword_matches(keyword, forms):
    """Vrai si le mot-clé correspond à une des formes d'un thème (inclusion ou même racine)."""
    kw = normalize(keyword)
    tokens = re.findall(r'\w+', kw)
    for form in map(normalize, forms):
        if form in kw:
            return True
        if any(len(t) >= 4 and (t.startswith(form[:5]) and len(form) >= 4) for t in tokens):
            return True
    return False


STOP = {normalize(word) for word in FRENCH_STOP_WORDS}


def content_tokens(text):
    """Mots porteurs de sens (3 lettres ou plus, hors mots vides)."""
    return [t for t in re.findall(r'\w+', normalize(text)) if len(t) >= 3 and t not in STOP]


def token_matches(token, themes):
    """Vrai si le mot appartient à un thème annoté (même racine sur 5 lettres)."""
    for forms in themes:
        for form in forms:
            for part in content_tokens(form):
                if token.startswith(part[:5]) or part.startswith(token[:5]):
                    return True
    return False


def keyword_correct(keyword, themes, strict=True):
    """Strict : TOUS les mots porteurs de sens du mot-clé appartiennent à des thèmes annotés
    (« robe mariée » oui, « famille pleuré » non). Souple : au moins un thème est touché."""
    if not strict:
        return any(keyword_matches(keyword, forms) for forms in themes)
    tokens = content_tokens(keyword)
    return bool(tokens) and all(token_matches(t, themes) for t in tokens)


def precision_recall_at_k(keywords, themes, k=5, strict=True):
    """Precision@k : part des k premiers mots-clés qui sont des thèmes attendus.
    Recall@k : part des thèmes attendus retrouvés parmi les mots-clés corrects."""
    top = keywords[:k]
    hits = [kw for kw in top if keyword_correct(kw, themes, strict)]
    covered = [forms for forms in themes if any(keyword_matches(kw, forms) for kw in hits)]
    return len(hits) / k, len(covered) / len(themes), hits


# ---------- Illustrations : CLIP score ----------

@lru_cache(maxsize=1)
def get_clip():
    from transformers import CLIPModel, CLIPProcessor
    name = 'openai/clip-vit-base-patch32'
    return CLIPModel.from_pretrained(name), CLIPProcessor.from_pretrained(name)


def clip_score(image, text):
    """CLIP score = 100 x similarité cosinus entre l'image et le texte (0 si négative).
    Entre 25 et 35 : bonne correspondance ; sous 20 : image sans rapport."""
    import torch
    model, processor = get_clip()
    inputs = processor(text=[text], images=[image], return_tensors='pt', padding=True, truncation=True)
    with torch.no_grad():
        outputs = model(**inputs)          # image_embeds et text_embeds : vecteurs projetés dans le même espace
    image_emb, text_emb = outputs.image_embeds, outputs.text_embeds
    image_emb = image_emb / image_emb.norm(dim=-1, keepdim=True)
    text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)
    return max(0.0, 100 * float((image_emb * text_emb).sum()))


# ---------- Temps ----------

def time_summary(durations_ms):
    if not durations_ms:
        return {'moyenne_ms': 0, 'max_ms': 0, 'n': 0}
    return {'moyenne_ms': round(statistics.mean(durations_ms)), 'max_ms': max(durations_ms), 'n': len(durations_ms)}
