# Tâches Celery de Reminder : IA en arrière-plan et envoi planifié (Celery Beat)
from celery import shared_task
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.utils import timezone

from . import services
from .models import Reminder


@shared_task
def suggest_time_task(reminder_id, user_id):
    """Calcule l'heure conseillée (KMeans) et l'enregistre dans suggested_time."""
    user = User.objects.get(pk=user_id)
    reminder = Reminder.objects.get(pk=reminder_id, user=user)
    suggested, share = services.suggest_time(user)
    reminder.suggested_time = suggested
    reminder.save(update_fields=['suggested_time'])
    html = render_to_string('Reminder/includes/suggestion.html', {'reminder': reminder, 'share': share})
    return {'html': html, 'message': f"Heure conseillée : {suggested:%H:%M}."}


@shared_task
def generate_prompt_task(reminder_id, user_id):
    """Crée la question du jour personnalisée (NER sur les 3 dernières entrées)."""
    user = User.objects.get(pk=user_id)
    reminder = Reminder.objects.get(pk=reminder_id, user=user)
    prompt, created = services.create_daily_prompt(reminder, timezone.localdate())
    html = render_to_string('Reminder/includes/generated_prompt.html', {'prompt': prompt, 'created': created})
    return {'html': html, 'message': "Question du jour prête."}


@shared_task
def send_reminders_task():
    """Lancée toutes les 5 minutes par Celery Beat (voir CELERY_BEAT_SCHEDULE dans settings.py)."""
    return {'envoyes': services.send_due_reminders()}
