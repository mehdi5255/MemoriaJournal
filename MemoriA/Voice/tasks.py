# Tâches Celery de Voice : transcription et émotion ne bloquent jamais la requête HTTP
from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.utils import timezone

from Account.services import AIServiceError
from Journal.models import JournalEntry

from . import services
from .models import MoodRecord, VoiceNote


@worker_ready.connect
def preload_voice_models(**kwargs):
    """En mode local, charge Whisper et le modèle d'émotion au démarrage du worker."""
    if getattr(settings, 'TESTING', False) or not settings.PRELOAD_AI_MODELS or settings.VOICE_BACKEND != 'local':
        return
    for load in (services.get_local_whisper, services.get_local_emotion):
        try:
            load()
        except Exception:
            pass        # chargé à la première utilisation


@shared_task
def transcribe_voice_note_task(note_id, user_id):
    """Transcrit la note (Whisper), puis crée ou met à jour l'humeur détectée (source « Voix »)."""
    user = User.objects.get(pk=user_id)
    note = VoiceNote.objects.select_related('entry').get(pk=note_id, entry__user=user)
    note.status = 'EnAttente'
    note.save(update_fields=['status'])
    try:
        with note.audio.open('rb') as file:
            text, language = services.transcribe(file.read(), user)
    except (AIServiceError, OSError) as error:
        note.status = 'Echec'
        note.save(update_fields=['status'])
        if isinstance(error, OSError):
            raise AIServiceError("Le fichier audio est introuvable sur le serveur.") from error
        raise

    note.transcription = text
    note.language = language
    note.status = 'Termine'
    note.save(update_fields=['transcription', 'language', 'status'])

    # L'émotion est un bonus : si elle échoue, la transcription reste enregistrée
    mood = None
    try:
        emotion, score = services.detect_emotion(text, user)
        mood, _ = MoodRecord.objects.update_or_create(voice_note=note, defaults={
            'user': user, 'entry': note.entry, 'source': 'Voix', 'emotion': emotion, 'score': score,
            'record_date': timezone.localtime(note.creation_date).date(),
        })
    except AIServiceError:
        pass

    html = render_to_string('Voice/includes/transcription.html',
                            {'note': note, 'mood': mood, 'alert': services.mood_alert(user)})
    return {'html': html, 'message': "Transcription terminée."}


@shared_task
def detect_entry_mood_task(entry_id, user_id):
    """Détecte l'humeur à partir du texte d'une entrée (source « Texte »)."""
    user = User.objects.get(pk=user_id)
    entry = JournalEntry.objects.get(pk=entry_id, user=user)
    emotion, score = services.detect_emotion(f"{entry.title}. {entry.content}", user)
    mood, _ = MoodRecord.objects.update_or_create(user=user, entry=entry, source='Texte', defaults={
        'emotion': emotion, 'score': score, 'record_date': entry.entry_date,
    })
    html = render_to_string('Voice/includes/detected_mood.html',
                            {'mood': mood, 'alert': services.mood_alert(user)})
    return {'html': html, 'message': "Humeur détectée."}


@shared_task
def transcribe_dictation_task(user_id, token):
    """Dictée du formulaire d'entrée : renvoie le texte, que la page ajoute dans « Ma journée »."""
    user = User.objects.get(pk=user_id)
    text, language = services.transcribe_dictation(user, token)
    return {'text': text, 'language': language, 'token': token,
            'message': "Transcription ajoutée au texte de votre journée."}
