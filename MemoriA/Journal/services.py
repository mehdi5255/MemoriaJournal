# Appels aux services IA du module Journal
#   - Recherche par le sens : embeddings sentence-transformers (modèle local) + similarité cosinus
#   - Suggestion d'étiquettes : classification zero-shot (mDeBERTa) parmi les étiquettes de l'utilisateur
from functools import lru_cache

from django.conf import settings

from Account.services import AIServiceError, log_ai_call, run_with_fallback

from .models import JournalEntry

MIN_SIMILARITY = 0.30       # en dessous, le souvenir n'a pas vraiment de rapport
TAG_THRESHOLD = 0.30        # score minimal pour proposer une étiquette (choisi par evaluate_journal)
MULTI_LABEL = False         # False : les scores se partagent entre les étiquettes (somme = 1), voir docs/metrics/journal.md
HYPOTHESIS = "Ce texte parle de {}."


# ---------- Embeddings (recherche par le sens) ----------

@lru_cache(maxsize=1)
def get_embedder():
    """Le modèle n'est chargé qu'une fois par processus (≈ 470 Mo, téléchargé au premier lancement)."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(settings.EMBEDDING_MODEL, device='cpu')


def encode(texts):
    """Liste de textes -> liste de vecteurs normalisés (longueur 1) : le produit scalaire = similarité cosinus."""
    try:
        vectors = get_embedder().encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return [[round(float(x), 5) for x in vector] for vector in vectors]
    except Exception as error:
        raise AIServiceError("Le modèle de recherche par le sens n'a pas pu lire le texte. Réessayez dans un instant.") \
            from error


def entry_text(entry):
    return f"{entry.title}. {entry.content} {entry.location}".strip()


def cosine(a, b):
    """Similarité cosinus de deux vecteurs normalisés (entre -1 et 1)."""
    return sum(x * y for x, y in zip(a, b))


def compute_embedding(entry, user):
    """Calcule et enregistre l'embedding d'une entrée (création ou modification)."""
    vector = log_ai_call(user, "Embedding d'une entrée", 'MiniLM (local)', encode, [entry_text(entry)])[0]
    JournalEntry.objects.filter(pk=entry.pk).update(embedding=vector)
    entry.embedding = vector
    return vector


def fill_missing_embeddings(user):
    """Entrées écrites quand le worker était arrêté : on calcule leurs embeddings en une seule fois."""
    missing = list(JournalEntry.objects.filter(user=user, embedding__isnull=True))
    if missing:
        vectors = log_ai_call(user, "Embedding d'une entrée", 'MiniLM (local)', encode, [entry_text(e) for e in missing])
        for entry, vector in zip(missing, vectors):
            entry.embedding = vector
        JournalEntry.objects.bulk_update(missing, ['embedding'])
    return len(missing)


def rank(query_vector, entries, limit, exclude=None):
    """Les entrées les plus proches du vecteur, avec leur score, au-dessus de MIN_SIMILARITY."""
    scored = [(entry, cosine(query_vector, entry.embedding)) for entry in entries
              if entry.embedding and entry.pk != exclude]
    scored = [(entry, score) for entry, score in scored if score >= MIN_SIMILARITY]
    return sorted(scored, key=lambda pair: pair[1], reverse=True)[:limit]


def semantic_search(user, query, limit=10):
    """« Recherche par le sens » : les entrées dont le sens est le plus proche de la requête."""
    query = query.strip()
    if len(query) < 2:
        raise AIServiceError("Écrivez au moins un mot à rechercher.")
    fill_missing_embeddings(user)
    vector = log_ai_call(user, 'Recherche par le sens', 'MiniLM (local)', encode, [query])[0]
    return rank(vector, JournalEntry.objects.filter(user=user).prefetch_related('tags'), limit)


def similar_entries(entry, limit=3):
    """Les 3 souvenirs les plus proches : aucun appel au modèle, les embeddings sont déjà enregistrés."""
    if not entry.embedding:
        return []
    others = JournalEntry.objects.filter(user=entry.user, embedding__isnull=False).exclude(pk=entry.pk)
    return rank(entry.embedding, others, limit)


# ---------- Suggestion d'étiquettes (zero-shot) ----------

@lru_cache(maxsize=1)
def get_hf_client():
    from huggingface_hub import InferenceClient
    if not settings.HF_TOKEN:
        raise AIServiceError("La clé HF_TOKEN est absente du fichier .env.")
    return InferenceClient(token=settings.HF_TOKEN, timeout=60)


@lru_cache(maxsize=1)
def get_local_zero_shot():
    from transformers import pipeline
    return pipeline('zero-shot-classification', model=settings.ZERO_SHOT_MODEL, device='cpu')


def call_hf_zero_shot(text, labels, multi_label=MULTI_LABEL):
    try:
        results = get_hf_client().zero_shot_classification(text[:2000], labels, model=settings.ZERO_SHOT_MODEL,
                                                           multi_label=multi_label, hypothesis_template=HYPOTHESIS)
        return {r.label: float(r.score) for r in results}
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de suggestion d'étiquettes ne répond pas. Réessayez dans un instant.") from error


def call_local_zero_shot(text, labels, multi_label=MULTI_LABEL):
    try:
        result = get_local_zero_shot()(text[:2000], candidate_labels=list(labels), multi_label=multi_label,
                                       hypothesis_template=HYPOTHESIS)
        return {label: float(score) for label, score in zip(result['labels'], result['scores'])}
    except Exception as error:
        raise AIServiceError("Le modèle local de suggestion d'étiquettes n'a pas pu analyser le texte.") from error


def tag_scores(text, labels, user, multi_label=MULTI_LABEL):
    """Score de chaque étiquette entre 0 et 1.
    multi_label=False : les scores se partagent entre les étiquettes (leur somme vaut 1).
    multi_label=True : chaque étiquette est jugée seule (plusieurs scores peuvent être proches de 1)."""
    return run_with_fallback(settings.JOURNAL_BACKEND, user, "Suggestion d'étiquettes",
                             ('mDeBERTa zero-shot', call_hf_zero_shot),
                             ('mDeBERTa zero-shot (local)', call_local_zero_shot), text, labels, multi_label)


def suggest_tags(user, text, threshold=TAG_THRESHOLD):
    """Étiquettes de l'utilisateur qui correspondent au texte : [(tag, score), ...] du plus sûr au moins sûr."""
    tags = list(user.tags.all())
    if not tags:
        raise AIServiceError("Créez d'abord quelques étiquettes : l'IA choisit parmi vos étiquettes.")
    if len(text.strip()) < 10:
        raise AIServiceError("Écrivez un peu plus de texte pour que l'IA puisse proposer des étiquettes.")
    scores = tag_scores(text, [tag.name for tag in tags], user)
    found = [(tag, scores.get(tag.name, 0)) for tag in tags if scores.get(tag.name, 0) >= threshold]
    return sorted(found, key=lambda pair: pair[1], reverse=True)
