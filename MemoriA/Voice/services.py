# Appels aux services IA du module Voice
#   - Transcription de la voix : Whisper (Hugging Face) ou Whisper local sur le processeur
#   - Émotion d'un texte : xlm-emo-t (Hugging Face ou local)
#   - Alerte : 3 jours négatifs de suite (statistiques, sans IA)
import io
import re
from collections import Counter
from datetime import timedelta
from functools import lru_cache

from django.conf import settings
from django.utils import timezone

from Account.services import AIServiceError, log_ai_call, run_with_fallback

from .models import MoodRecord

# Étiquettes du modèle xlm-emo-t -> émotions de MoodRecord
EMOTION_LABELS = {'joy': 'Joie', 'sadness': 'Tristesse', 'anger': 'Colere', 'fear': 'Peur'}
# Sous ce score, le modèle hésite : on considère l'humeur comme neutre.
# Valeur choisie avec python manage.py evaluate_voice (voir docs/metrics/voice.md).
NEUTRAL_THRESHOLD = 0.6
NEGATIVE_EMOTIONS = {'Tristesse', 'Colere', 'Peur'}
ALERT_DAYS = 3


# ---------- Clients et modèles (chargés une seule fois) ----------

@lru_cache(maxsize=1)
def get_hf_client():
    from huggingface_hub import InferenceClient
    if not settings.HF_TOKEN:
        raise AIServiceError("La clé HF_TOKEN est absente du fichier .env.")
    return InferenceClient(token=settings.HF_TOKEN, timeout=180)


@lru_cache(maxsize=1)
def get_local_whisper():
    from transformers import pipeline
    # chunk_length_s : découpe les notes longues (Whisper lit 30 s à la fois)
    return pipeline('automatic-speech-recognition', model=settings.LOCAL_WHISPER_MODEL,
                    chunk_length_s=30, device='cpu')


@lru_cache(maxsize=1)
def get_local_emotion():
    from transformers import pipeline
    return pipeline('text-classification', model=settings.EMOTION_MODEL, top_k=None, truncation=True, device='cpu')


# ---------- Transcription ----------

def decode_audio(data):
    """Décode n'importe quel format (WebM, OGG, MP3, WAV, M4A) en son mono 16 kHz pour Whisper.
    PyAV contient FFmpeg : rien d'autre à installer sous Windows."""
    import av
    import numpy as np
    resampler = av.AudioResampler(format='flt', layout='mono', rate=16000)
    samples = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            samples += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
    samples += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]   # fin du flux
    if not samples:
        raise AIServiceError("Le fichier audio est vide ou illisible.")
    return np.concatenate(samples)


def call_hf_whisper(data):
    try:
        return get_hf_client().automatic_speech_recognition(data, model=settings.HF_WHISPER_MODEL).text
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de transcription ne répond pas. Réessayez dans un instant.") from error


def call_local_whisper(data):
    try:
        audio = decode_audio(data)
        return get_local_whisper()({'raw': audio, 'sampling_rate': 16000},
                                   generate_kwargs={'task': 'transcribe'})['text']
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le modèle de transcription local n'a pas pu lire cette note. "
                             "Vérifiez qu'il est téléchargé, puis réessayez.") from error


def detect_language(text):
    """Langue de la transcription (fr, en ou ar) : alphabet arabe, puis mots les plus fréquents."""
    if re.search(r'[؀-ۿ]', text):
        return 'ar'
    words = re.findall(r"[a-zàâçéèêëîïôûùüÿœ']+", text.lower())
    french = sum(w in {'le', 'la', 'les', 'et', 'je', 'est', 'une', 'des', 'nous', 'avec', 'pour', 'très'} for w in words)
    english = sum(w in {'the', 'and', 'is', 'i', 'was', 'with', 'for', 'we', 'it', 'very', 'to', 'of'} for w in words)
    return 'en' if english > french else 'fr'


def run_with_backend(user, action, remote, local, *args):
    """Choix du moteur selon VOICE_BACKEND : huggingface, local, ou auto (secours local si l'API échoue)."""
    return run_with_fallback(settings.VOICE_BACKEND, user, action, remote, local, *args)


def transcribe(data, user):
    """Transcrit une note vocale. Renvoie (texte, langue)."""
    text = run_with_backend(user, 'Transcription', ('Whisper', call_hf_whisper),
                            ('Whisper (local)', call_local_whisper), data)
    text = (text or '').strip()
    if not text:
        raise AIServiceError("Aucune parole n'a été reconnue dans cette note vocale.")
    return text, detect_language(text)


# ---------- Émotion ----------

def call_hf_emotion(text):
    try:
        results = get_hf_client().text_classification(text[:2000], model=settings.EMOTION_MODEL, top_k=4)
        return [{'label': r.label, 'score': r.score} for r in results]
    except AIServiceError:
        raise
    except Exception as error:
        raise AIServiceError("Le service de détection d'émotion ne répond pas. Réessayez dans un instant.") from error


def call_local_emotion(text):
    try:
        results = get_local_emotion()(text)
        return results[0] if results and isinstance(results[0], list) else results
    except Exception as error:
        raise AIServiceError("Le modèle d'émotion local n'a pas pu analyser ce texte.") from error


def to_emotion(scores):
    """Convertit la sortie du modèle en (émotion de MoodRecord, score = confiance du modèle entre 0 et 1)."""
    best = max(scores, key=lambda r: r['score'])
    score = round(best['score'], 2)
    if best['score'] < NEUTRAL_THRESHOLD or best['label'] not in EMOTION_LABELS:
        return 'Neutre', score
    return EMOTION_LABELS[best['label']], score


def detect_emotion(text, user):
    if not text.strip():
        raise AIServiceError("Le texte est vide : impossible de détecter une émotion.")
    scores = run_with_backend(user, "Détection d'émotion", ('Émotions', call_hf_emotion),
                              ('Émotions (local)', call_local_emotion), text)
    return to_emotion(scores)


# ---------- Alerte : jours négatifs de suite ----------

def dominant_by_day(user, start, end):
    """{date: émotion la plus fréquente} entre deux dates (en cas d'égalité, la plus récente)."""
    days = {}
    for mood in MoodRecord.objects.filter(user=user, record_date__range=(start, end)).order_by('creation_date'):
        days.setdefault(mood.record_date, []).append(mood.emotion)
    result = {}
    for day, emotions in days.items():
        counts = Counter(emotions)
        best = max(counts.values())
        result[day] = next(e for e in reversed(emotions) if counts[e] == best)
    return result


def negative_streak(user, today=None):
    """Nombre de jours négatifs consécutifs se terminant aujourd'hui (ou hier si rien n'est noté aujourd'hui)."""
    today = today or timezone.localdate()
    days = dominant_by_day(user, today - timedelta(days=30), today)
    day = today if today in days else today - timedelta(days=1)
    streak = 0
    while days.get(day) in NEGATIVE_EMOTIONS:
        streak += 1
        day -= timedelta(days=1)
    return streak


def mood_alert(user):
    """Message d'alerte si l'humeur est négative depuis au moins 3 jours, sinon None."""
    streak = negative_streak(user)
    if streak < ALERT_DAYS:
        return None
    return {'days': streak,
            'message': f"Votre humeur est négative depuis {streak} jours de suite. Prenez soin de vous : "
                       "parlez-en à un proche, et n'hésitez pas à consulter un professionnel de santé "
                       "si ce sentiment dure."}


# ---------- Dictée dans le formulaire d'une entrée ----------
# L'entrée n'existe pas encore pendant la dictée : l'audio est gardé dans media/dictations/<utilisateur>/,
# puis transformé en VoiceNote quand l'entrée est enregistrée (attach_dictations).

DICTATION_TOKEN = re.compile(r'^[0-9a-f]{32}$')


def dictation_path(user, token, suffix):
    return f"dictations/{user.pk}/{token}.{suffix}"


def save_dictation(user, data, audio_format, seconds):
    """Garde l'enregistrement et ses informations (format, durée). Renvoie le jeton de la dictée."""
    import json
    import uuid
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    token = uuid.uuid4().hex
    default_storage.save(dictation_path(user, token, audio_format), ContentFile(data))
    info = {'format': audio_format, 'duration': seconds, 'text': '', 'language': ''}
    default_storage.save(dictation_path(user, token, 'json'), ContentFile(json.dumps(info).encode()))
    return token


def read_dictation(user, token):
    import json
    from django.core.files.storage import default_storage
    if not DICTATION_TOKEN.match(token or ''):
        return None
    path = dictation_path(user, token, 'json')
    if not default_storage.exists(path):
        return None
    with default_storage.open(path) as file:
        return json.loads(file.read())


def transcribe_dictation(user, token):
    """Transcrit la dictée et garde le texte avec l'audio. Renvoie (texte, langue)."""
    import json
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    info = read_dictation(user, token)
    if info is None:
        raise AIServiceError("L'enregistrement est introuvable : recommencez la dictée.")
    with default_storage.open(dictation_path(user, token, info['format'])) as file:
        text, language = transcribe(file.read(), user)
    info.update(text=text, language=language)
    path = dictation_path(user, token, 'json')
    default_storage.delete(path)
    default_storage.save(path, ContentFile(json.dumps(info).encode()))
    return text, language


def attach_dictations(entry, user, tokens):
    """Après l'enregistrement de l'entrée : chaque dictée devient une note vocale de l'entrée."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    from .models import VoiceNote
    notes = []
    for number, token in enumerate(t for t in tokens.split(',') if t):
        info = read_dictation(user, token)
        if info is None:
            continue                                   # jeton inconnu ou d'un autre utilisateur : ignoré
        audio_path = dictation_path(user, token, info['format'])
        note = VoiceNote(entry=entry, title=f"Dictée du {timezone.localdate():%d/%m/%Y}"
                                            + (f" ({number + 1})" if number else ''),
                         duration=info['duration'], transcription=info['text'], language=info['language'],
                         status='Termine' if info['text'] else 'Echec')
        with default_storage.open(audio_path) as file:
            note.audio.save(f"dictee.{info['format']}", ContentFile(file.read()), save=False)
        note.save()
        notes.append(note)
        default_storage.delete(audio_path)
        default_storage.delete(dictation_path(user, token, 'json'))
    return notes
