# Appels aux services IA du module Memory
#   - Récit de la journée : Gemini (texte + transcriptions + photos)
#   - Illustration : Stable Diffusion XL (Hugging Face)
#   - Rétrospective : mots-clés KeyBERT + statistiques d'humeur
import io
from collections import Counter
from functools import lru_cache

from django.conf import settings
from PIL import Image

from Account.services import AIServiceError, log_ai_call
from Face.models import Person
from Journal.models import JournalEntry
from Voice.models import MoodRecord


# ---------- Clients (créés une seule fois) ----------

@lru_cache(maxsize=1)
def get_gemini_client():
    from google import genai
    from google.genai import types
    if not settings.GEMINI_API_KEY:
        raise AIServiceError("La clé GEMINI_API_KEY est absente du fichier .env.")
    return genai.Client(api_key=settings.GEMINI_API_KEY,
                        http_options=types.HttpOptions(timeout=90_000))      # 90 s


@lru_cache(maxsize=1)
def get_hf_client():
    from huggingface_hub import InferenceClient
    if not settings.HF_TOKEN:
        raise AIServiceError("La clé HF_TOKEN est absente du fichier .env.")
    return InferenceClient(token=settings.HF_TOKEN, timeout=180)


@lru_cache(maxsize=1)
def get_local_text_to_image():
    # SD-Turbo : poids fp16 téléchargés une fois (~2,5 Go), calculs en float32 sur le processeur
    import torch
    from diffusers import AutoPipelineForText2Image
    pipe = AutoPipelineForText2Image.from_pretrained(settings.LOCAL_IMAGE_MODEL, variant='fp16',
                                                     torch_dtype=torch.float32)
    pipe.set_progress_bar_config(disable=True)
    return pipe.to('cpu')


@lru_cache(maxsize=1)
def get_local_image_to_image():
    # Réutilise les poids déjà chargés du pipeline texte vers image
    from diffusers import AutoPipelineForImage2Image
    return AutoPipelineForImage2Image.from_pipe(get_local_text_to_image())


@lru_cache(maxsize=1)
def get_keybert_model():
    # Le modèle (environ 500 Mo) est chargé une seule fois par processus
    from keybert import KeyBERT
    return KeyBERT(model=settings.KEYBERT_MODEL)


# ---------- Récit (Story) ----------

STORY_STYLES = {
    'Simple': "un style simple, chaleureux et naturel",
    'Poetique': "un style poétique, avec des images et des métaphores douces",
    'Humoristique': "un style humoristique et léger, sans moquerie",
}

MAX_PHOTOS = 3


def build_story_prompt(entry, style):
    """Rassemble tout ce que l'on sait de la journée : texte, voix, humeur, personnes, photos."""
    lines = [
        "Tu es l'auteur d'un journal intime. Écris, en français, le récit de cette journée",
        f"à la première personne, dans {STORY_STYLES.get(style, STORY_STYLES['Simple'])}.",
        "Reste fidèle aux faits : n'invente ni personne, ni lieu, ni événement.",
        "Longueur : entre 120 et 250 mots, en 2 ou 3 paragraphes. Pas de titre.",
        "",
        f"Date : {entry.entry_date:%d/%m/%Y}",
        f"Titre de l'entrée : {entry.title}",
    ]
    if entry.location:
        lines.append(f"Lieu : {entry.location}")
    lines += ["", "Texte écrit par l'utilisateur :", entry.content]

    transcriptions = [note.transcription for note in entry.voice_notes.filter(status='Termine')
                      if note.transcription]
    if transcriptions:
        lines += ["", "Notes vocales transcrites :"] + [f"- {t}" for t in transcriptions]

    mood = entry.mood_records.order_by('-creation_date').first()
    if mood:
        lines.append(f"\nHumeur de la journée : {mood.get_emotion_display()}")

    persons = Person.objects.filter(face_tags__photo__entry=entry).distinct().values_list('name', flat=True)
    if persons:
        lines.append("Personnes présentes sur les photos : " + ', '.join(persons))

    captions = [photo.caption for photo in entry.photos.all()[:MAX_PHOTOS] if photo.caption]
    if captions:
        lines += ["", "Description des photos :"] + [f"- {c}" for c in captions]
    if entry.photos.exists():
        lines.append("Les photos de la journée sont jointes : utilise ce que tu y vois.")
    return '\n'.join(lines)


def photo_to_bytes(photo, max_size=1024):
    """Photo réduite en JPEG (moins de données envoyées à l'API)."""
    with photo.image.open('rb') as file:
        image = Image.open(file)
        image = image.convert('RGB')
        image.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=85)
    return buffer.getvalue()


def call_gemini(contents, model=None):
    try:
        response = get_gemini_client().models.generate_content(model=model or settings.GEMINI_MODEL,
                                                               contents=contents)
    except AIServiceError:
        raise
    except Exception as error:
        if '429' in str(error) or 'RESOURCE_EXHAUSTED' in str(error):
            raise AIServiceError("Le quota gratuit de Gemini est atteint pour le moment. "
                                 "Réessayez plus tard (il se renouvelle chaque jour).") from error
        raise AIServiceError("Le service de rédaction (Gemini) ne répond pas. Réessayez dans un instant.") from error
    text = (response.text or '').strip()
    if not text:
        raise AIServiceError("Le service de rédaction n'a renvoyé aucun texte. Réessayez.")
    return text


def generate_story_text(entry, style, user):
    """Écrit le récit de l'entrée. Renvoie (texte, prompt)."""
    from google.genai import types
    prompt = build_story_prompt(entry, style)
    contents = [prompt]
    for photo in entry.photos.all()[:MAX_PHOTOS]:
        try:
            contents.append(types.Part.from_bytes(data=photo_to_bytes(photo), mime_type='image/jpeg'))
        except (OSError, ValueError):
            continue        # photo illisible : on l'ignore, le récit reste possible
    text = log_ai_call(user, 'Récit de la journée', 'Gemini', call_gemini, contents)
    return text, prompt


# ---------- Illustration (GeneratedImage) ----------

IMAGE_STYLES = {
    'Realiste': "realistic photograph, natural light, detailed",
    'Aquarelle': "soft watercolor painting, pastel colors, paper texture",
    'Dessin': "hand-drawn pencil and ink illustration, sketchbook style",
}


def build_image_prompt(story, user):
    """Gemini résume le récit en une courte description visuelle en anglais (mieux compris par SDXL)."""
    instruction = ("Describe in English, in one sentence of at most 40 words, a single peaceful scene "
                   "that illustrates this diary story. Only visual elements, no text, no names.\n\n" + story.text)
    try:
        return log_ai_call(user, "Description de l'illustration", 'Gemini', call_gemini, [instruction])
    except AIServiceError:
        # Sans Gemini, on garde le début du récit : l'illustration reste possible
        return story.text[:300]


def call_text_to_image(prompt):
    try:
        return get_hf_client().text_to_image(
            prompt, model=settings.HF_IMAGE_MODEL,
            negative_prompt="text, watermark, signature, blurry, deformed")
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service d'illustration ne répond pas. Réessayez dans un instant.") from error


def call_image_to_image(photo_bytes, prompt):
    try:
        return get_hf_client().image_to_image(photo_bytes, prompt=prompt, model=settings.HF_IMAGE_TO_IMAGE_MODEL)
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de transformation de photo ne répond pas. Réessayez dans un instant.") from error


def call_local_text_to_image(prompt):
    try:
        # SD-Turbo produit une image en 1 seule étape, sans guidage
        return get_local_text_to_image()(prompt, num_inference_steps=1, guidance_scale=0.0,
                                         width=512, height=512).images[0]
    except Exception as error:
        raise AIServiceError("Le modèle d'illustration local n'a pas pu créer l'image. "
                             "Vérifiez qu'il est bien téléchargé, puis réessayez.") from error


def call_local_image_to_image(photo_bytes, prompt):
    try:
        photo = Image.open(io.BytesIO(photo_bytes)).convert('RGB').resize((512, 512))
        # strength x étapes doit valoir au moins 1 : 0,5 x 2 = 1 étape réelle
        return get_local_image_to_image()(prompt, image=photo, num_inference_steps=2, strength=0.5,
                                          guidance_scale=0.0).images[0]
    except Exception as error:
        raise AIServiceError("Le modèle local n'a pas pu transformer la photo. Réessayez.") from error


def create_image(user, prompt, photo_bytes=None):
    """Choisit le moteur selon IMAGE_BACKEND (huggingface, local ou auto) et renvoie une image PIL."""
    backend = settings.IMAGE_BACKEND
    action = 'Transformation de photo' if photo_bytes else 'Illustration du récit'
    remote = (call_image_to_image, photo_bytes, prompt) if photo_bytes else (call_text_to_image, prompt)
    local = (call_local_image_to_image, photo_bytes, prompt) if photo_bytes else (call_local_text_to_image, prompt)

    if backend == 'local':
        return log_ai_call(user, action, 'SD-Turbo (local)', *local)
    try:
        return log_ai_call(user, action, 'Stable Diffusion', *remote)
    except AIServiceError:
        if backend != 'auto':
            raise
        # Solution de secours : l'échec de l'API est déjà enregistré, on passe au modèle local
        return log_ai_call(user, action, 'SD-Turbo (local)', *local)


def generate_illustration(story, style, user, source_photo=None):
    """Crée l'illustration du récit. Renvoie (octets PNG, prompt utilisé)."""
    scene = build_image_prompt(story, user).strip().rstrip('.')
    prompt = f"{scene}, {IMAGE_STYLES.get(style, IMAGE_STYLES['Realiste'])}"
    photo_bytes = photo_to_bytes(source_photo) if source_photo else None
    image = create_image(user, prompt, photo_bytes)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue(), prompt


# ---------- Rétrospective (Retrospective) ----------

def retrospective_texts(retrospective):
    """Tous les textes de la période : entrées, récits et transcriptions."""
    texts = []
    for entry in retrospective.entries.all():
        texts.append(f"{entry.title}. {entry.content}")
        if hasattr(entry, 'story'):
            texts.append(entry.story.text)
        texts += [n.transcription for n in entry.voice_notes.all() if n.transcription]
    return texts


def call_keybert(text, top_n):
    try:
        keywords = get_keybert_model().extract_keywords(
            text, keyphrase_ngram_range=(1, 2), top_n=top_n, use_mmr=True, diversity=0.5,
            stop_words=FRENCH_STOP_WORDS)
    except Exception as error:
        raise AIServiceError("L'extraction des thèmes a échoué. Réessayez dans un instant.") from error
    return [word for word, score in keywords]


def extract_keywords(retrospective, user, top_n=8):
    text = '\n'.join(retrospective_texts(retrospective))
    if not text.strip():
        raise AIServiceError("Aucun texte dans cette période : ajoutez des entrées à la rétrospective.")
    return log_ai_call(user, 'Thèmes de la rétrospective', 'KeyBERT', call_keybert, text, top_n)


def retrospective_stats(retrospective):
    """Statistiques simples (sans IA) : humeurs, moments forts, nombre d'entrées."""
    moods = MoodRecord.objects.filter(user=retrospective.user,
                                      record_date__range=(retrospective.start_date, retrospective.end_date))
    counts = Counter(moods.values_list('emotion', flat=True))
    total = sum(counts.values())
    labels = dict(MoodRecord.EMOTION_CHOICES)
    emotions = [{'emotion': emotion, 'label': labels[emotion], 'count': count,
                 'percent': round(100 * count / total)}
                for emotion, count in counts.most_common()]

    entries = retrospective.entries.all()
    joyful_ids = set(moods.filter(emotion='Joie').values_list('entry_id', flat=True))
    highlights = [e for e in entries if e.is_favorite or e.pk in joyful_ids][:3]
    return {
        'entry_count': entries.count(),
        'emotions': emotions,
        'dominant': emotions[0]['label'] if emotions else None,
        'highlights': highlights,
    }


def build_summary(retrospective, keywords, stats):
    """Résumé automatique à partir des thèmes et des statistiques."""
    parts = [f"{stats['entry_count']} entrée(s) écrite(s) du {retrospective.start_date:%d/%m} "
             f"au {retrospective.end_date:%d/%m/%Y}."]
    if stats['dominant']:
        parts.append(f"Humeur dominante : {stats['dominant'].lower()}.")
    if keywords:
        parts.append("Thèmes principaux : " + ', '.join(keywords[:5]) + '.')
    if stats['highlights']:
        parts.append("Moments forts : " + ', '.join(e.title for e in stats['highlights']) + '.')
    return ' '.join(parts)


def entries_in_period(retrospective):
    return JournalEntry.objects.filter(user=retrospective.user,
                                       entry_date__range=(retrospective.start_date, retrospective.end_date))


# Mots vides français (KeyBERT n'en a pas par défaut)
FRENCH_STOP_WORDS = [
    'a', 'à', 'ai', 'aie', 'au', 'aux', 'avec', 'avoir', 'avons', 'c', 'ce', 'ces', 'cet', 'cette', 'd', 'dans',
    'de', 'des', 'du', 'elle', 'elles', 'en', 'encore', 'est', 'et', 'été', 'être', 'eu', 'fait', 'il', 'ils',
    'j', 'je', 'l', 'la', 'le', 'les', 'leur', 'lui', 'm', 'ma', 'mais', 'me', 'même', 'mes', 'moi', 'mon',
    'n', 'ne', 'nos', 'notre', 'nous', 'on', 'ont', 'ou', 'où', 'par', 'pas', 'peu', 'plus', 'pour', 'qu',
    'que', 'qui', 's', 'sa', 'sans', 'se', 'ses', 'si', 'son', 'sont', 'sur', 't', 'ta', 'te', 'tes', 'toi',
    'ton', 'tous', 'tout', 'très', 'tu', 'un', 'une', 'vos', 'votre', 'vous', 'y', 'était', 'étais', 'avait',
    'avais', 'suis', 'es', 'sommes', 'êtes', 'fut', 'cela', 'ça', 'comme', 'aussi', 'bien', 'puis', 'alors',
    'après', 'avant', 'aujourd', 'hui', 'jour', 'journée', 'beaucoup', 'vraiment', 'ensuite', 'donc',
    # Mots de temps : fréquents dans un journal mais ce ne sont pas des thèmes
    'matin', 'midi', 'soir', 'soirée', 'nuit', 'hier', 'demain', 'lendemain', 'semaine', 'fois', 'moment',
    # Mots de liaison et nombres repérés pendant l'évaluation (docs/metrics/memory.md)
    'toute', 'toutes', 'chez', 'jusqu', 'jusque', 'premier', 'première', 'dernier', 'dernière', 'deux', 'trois',
    'dix', 'enfin', 'vu', 'fait', 'faire', 'mes', 'notre', 'nouveau', 'nouvelle', 'grand', 'petit', 'petite',
    'impressionnante', 'magnifique', 'terrible', 'total', 'bord',
]
