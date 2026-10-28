# Appels aux services IA du module Reminder
#   - Meilleure heure de rappel : KMeans (scikit-learn) sur les heures d'écriture des entrées
#   - Question personnalisée : entités nommées (camembert-ner) des 3 dernières entrées + modèles de phrases
import math
from datetime import time as dtime
from functools import lru_cache

from django.conf import settings
from django.utils import timezone

from Account.services import AIServiceError, log_ai_call, run_with_fallback
from Journal.models import JournalEntry

from .models import DailyPrompt

MIN_ENTRIES = 5             # en dessous, pas assez de données pour apprendre une habitude
MAX_ENTRIES = 60            # on regarde les 60 dernières entrées
ENTITY_THRESHOLD = 0.8      # score minimal d'une entité nommée
ENTITY_LABELS = {'PER': 'personne', 'LOC': 'lieu', 'ORG': 'organisation', 'MISC': 'événement'}


# ---------- Meilleure heure (KMeans) ----------

def minutes_to_point(minutes):
    """Une heure de la journée devient un point sur un cercle : 23 h 55 et 0 h 05 sont proches."""
    angle = 2 * math.pi * minutes / 1440
    return [math.cos(angle), math.sin(angle)]


def point_to_minutes(x, y):
    return round((math.degrees(math.atan2(y, x)) % 360) / 360 * 1440) % 1440


def choose_kmeans(points, max_clusters):
    """Essaie 2 puis 3 groupes et garde le découpage le plus net (score de silhouette le plus haut).
    Avec un nombre fixe de groupes, KMeans pourrait couper une seule habitude en deux."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    distinct = len({tuple(round(v, 4) for v in p) for p in points})
    best, best_score = KMeans(n_clusters=1, n_init=1, random_state=0).fit(points), 0.5   # 1 groupe par défaut
    for k in range(2, min(max_clusters, distinct - 1) + 1):
        model = KMeans(n_clusters=k, n_init=10, random_state=0).fit(points)
        score = silhouette_score(points, model.labels_)
        if score > best_score:
            best, best_score = model, score
    return best


def best_minutes(minutes_list, clusters=3):
    """KMeans sur les heures d'écriture : le centre du groupe le plus nombreux = l'habitude principale.
    Renvoie (minutes depuis minuit, part des entrées dans ce groupe)."""
    if len(minutes_list) < MIN_ENTRIES:
        raise AIServiceError(f"Il faut au moins {MIN_ENTRIES} entrées pour apprendre votre heure d'écriture.")
    points = [minutes_to_point(m) for m in minutes_list]
    model = choose_kmeans(points, clusters)
    labels = list(model.labels_)
    biggest = max(set(labels), key=labels.count)
    center = model.cluster_centers_[biggest]
    return point_to_minutes(center[0], center[1]), labels.count(biggest) / len(labels)


def writing_minutes(user):
    """Heures (en minutes depuis minuit, heure de Tunis) où l'utilisateur a écrit ses dernières entrées."""
    entries = JournalEntry.objects.filter(user=user).order_by('-creation_date')[:MAX_ENTRIES]
    return [timezone.localtime(e.creation_date).hour * 60 + timezone.localtime(e.creation_date).minute
            for e in entries]


def suggest_time(user):
    """Heure conseillée, arrondie à 5 minutes. Renvoie (datetime.time, part des entrées expliquées)."""
    minutes, share = log_ai_call(user, "Meilleure heure de rappel", 'KMeans', best_minutes, writing_minutes(user))
    minutes = (5 * round(minutes / 5)) % 1440
    return dtime(minutes // 60, minutes % 60), round(share, 2)


# ---------- Entités nommées (NER) ----------

@lru_cache(maxsize=1)
def get_hf_client():
    from huggingface_hub import InferenceClient
    if not settings.HF_TOKEN:
        raise AIServiceError("La clé HF_TOKEN est absente du fichier .env.")
    return InferenceClient(token=settings.HF_TOKEN, timeout=60)


@lru_cache(maxsize=1)
def get_local_ner():
    from transformers import pipeline
    return pipeline('token-classification', model=settings.NER_MODEL, aggregation_strategy='simple', device='cpu')


def call_hf_ner(text):
    try:
        results = get_hf_client().token_classification(text[:2000], model=settings.NER_MODEL)
        return [{'type': r.entity_group, 'text': r.word, 'score': float(r.score)} for r in results]
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de reconnaissance des noms ne répond pas. Réessayez dans un instant.") from error


def call_local_ner(text):
    try:
        return [{'type': r['entity_group'], 'text': r['word'], 'score': float(r['score'])}
                for r in get_local_ner()(text[:2000])]
    except Exception as error:
        raise AIServiceError("Le modèle de reconnaissance des noms local n'a pas pu analyser le texte.") from error


def clean_entities(results, threshold=ENTITY_THRESHOLD):
    """Entités sûres, sans doublons, dans l'ordre d'apparition : [{'type': 'PER', 'text': 'Sarah'}, ...]."""
    seen, entities = set(), []
    for r in results:
        text = r['text'].strip(" .,;:!?'\"«»")
        key = (r['type'], text.lower())
        if r['score'] >= threshold and len(text) >= 2 and r['type'] in ENTITY_LABELS and key not in seen:
            seen.add(key)
            entities.append({'type': r['type'], 'text': text})
    return entities


def raw_entities(text, user):
    """Toutes les entités trouvées par le modèle, avec leur score (avant le seuil)."""
    return run_with_fallback(settings.REMINDER_BACKEND, user, 'Entités nommées', ('CamemBERT NER', call_hf_ner),
                             ('CamemBERT NER (local)', call_local_ner), text)


def extract_entities(text, user):
    return clean_entities(raw_entities(text, user))


# ---------- Question personnalisée ----------

TEMPLATES = {
    'PER': ["Comment va {} depuis la dernière fois ?", "As-tu eu des nouvelles de {} aujourd'hui ?",
            "Qu'aimerais-tu partager avec {} cette semaine ?"],
    'LOC': ["Que retiens-tu de ton passage à {} ?", "As-tu envie de retourner à {} ?"],
    'ORG': ["Où en es-tu avec {} ?", "Comment se passe ta semaine avec {} ?"],
    'MISC': ["Que t'a laissé {} ?", "Comment as-tu vécu {} ?"],
}
GENERIC = ["Quel moment de ta journée aimerais-tu garder en mémoire ?",
           "Qu'est-ce qui t'a fait sourire aujourd'hui ?",
           "Qu'as-tu appris de nouveau aujourd'hui ?"]


def build_question(entities, day):
    """Choisit une entité (personne en priorité) et un modèle de phrase ; change chaque jour (day = date)."""
    rotation = day.toordinal()
    for entity_type in ('PER', 'LOC', 'MISC', 'ORG'):
        candidates = [e for e in entities if e['type'] == entity_type]
        if candidates:
            entity = candidates[rotation % len(candidates)]
            templates = TEMPLATES[entity_type]
            return templates[rotation % len(templates)].format(entity['text'])
    return GENERIC[rotation % len(GENERIC)]


def generate_question(user, day):
    """Question du jour à partir des 3 dernières entrées. Renvoie (question, entités en texte)."""
    entries = JournalEntry.objects.filter(user=user).order_by('-entry_date', '-creation_date')[:3]
    text = '\n'.join(f"{e.title}. {e.content}" for e in entries)
    entities = extract_entities(text, user) if text.strip() else []
    labels = [f"{ENTITY_LABELS[e['type']]} : {e['text']}" for e in entities]
    return build_question(entities, day), labels


def create_daily_prompt(reminder, day=None):
    """Crée la question du jour d'un rappel (une seule par jour). Si l'IA échoue, une question générale est posée."""
    day = day or timezone.localdate()
    existing = DailyPrompt.objects.filter(reminder=reminder, prompt_date=day).first()
    if existing:
        return existing, False
    try:
        question, labels = generate_question(reminder.user, day)
    except AIServiceError:
        question, labels = GENERIC[day.toordinal() % len(GENERIC)], []
    prompt = DailyPrompt.objects.create(user=reminder.user, reminder=reminder, prompt_date=day,
                                        question=question, detected_entities=labels)
    return prompt, True


# ---------- Envoi des rappels (commande send_reminders et Celery Beat) ----------

def send_due_reminders(now=None, force=False):
    """Pour chaque rappel actif prévu aujourd'hui, dont l'heure est passée et pas encore envoyé aujourd'hui :
    crée la question du jour et envoie un e-mail si le canal est Email. Renvoie le nombre de rappels envoyés."""
    from django.core.mail import send_mail

    from .models import Reminder

    now = timezone.localtime(now or timezone.now())
    sent = 0
    for reminder in Reminder.objects.filter(is_active=True).select_related('user'):
        already = reminder.last_sent and timezone.localtime(reminder.last_sent).date() == now.date()
        if not force and (already or not reminder.is_due_on(now.date()) or now.time() < reminder.reminder_time):
            continue
        prompt, _ = create_daily_prompt(reminder, now.date())
        if reminder.channel == 'Email' and reminder.user.email:
            body = f"Bonjour {reminder.user.username},\n\n{reminder.message or 'Prends cinq minutes pour ton journal.'}" \
                   f"\n\nQuestion du jour : {prompt.question}\n\n{settings.SITE_URL}/reminder/questions/{prompt.pk}/repondre/"
            send_mail("MémoriA : ta question du jour", body, settings.DEFAULT_FROM_EMAIL, [reminder.user.email],
                      fail_silently=True)
        reminder.last_sent = now
        reminder.save(update_fields=['last_sent'])
        sent += 1
    return sent
