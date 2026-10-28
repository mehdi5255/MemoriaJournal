# Tâches Celery de Memory : les appels IA longs ne bloquent jamais la requête HTTP
from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction
from django.template.loader import render_to_string

from Face.models import Photo
from Journal.models import JournalEntry

from . import services
from .models import GeneratedImage, Retrospective, Story


@worker_ready.connect
def preload_models(**kwargs):
    """Charge les modèles locaux au démarrage du worker : la première tâche ne paie pas le chargement
    (~25 s pour KeyBERT, ~90 s pour SD-Turbo)."""
    if getattr(settings, 'TESTING', False) or not settings.PRELOAD_AI_MODELS:
        return
    loaders = [services.get_keybert_model]
    if settings.IMAGE_BACKEND in ('local', 'auto'):
        loaders.append(services.get_local_text_to_image)
    for load in loaders:
        try:
            load()
        except Exception:
            pass        # le modèle sera chargé à la première utilisation


@shared_task
def generate_story_task(entry_id, style, user_id, show_link=False):
    """Crée le récit de l'entrée, ou le régénère (version + 1)."""
    user = User.objects.get(pk=user_id)
    entry = JournalEntry.objects.get(pk=entry_id, user=user)
    text, prompt = services.generate_story_text(entry, style, user)

    with transaction.atomic():
        story, created = Story.objects.get_or_create(entry=entry, defaults={'text': text, 'style': style,
                                                                            'prompt': prompt})
        if not created:
            story.text = text
            story.style = style
            story.prompt = prompt
            story.version += 1
            story.save()

    html = render_to_string('Memory/includes/story_text.html', {'story': story, 'show_link': show_link})
    return {'html': html, 'story_id': story.pk,
            'message': f"Récit généré (version {story.version})."}


@shared_task
def generate_image_task(story_id, style, user_id, source_photo_id=None):
    """Crée une illustration du récit avec Stable Diffusion."""
    user = User.objects.get(pk=user_id)
    story = Story.objects.get(pk=story_id, entry__user=user)
    source_photo = Photo.objects.filter(pk=source_photo_id, entry=story.entry).first() if source_photo_id else None

    png, prompt = services.generate_illustration(story, style, user, source_photo=source_photo)

    image = GeneratedImage(story=story, prompt=prompt, style=style, source_photo=source_photo,
                           is_main=not story.images.exists())
    image.image.save(f'story_{story.pk}_{style.lower()}.png', ContentFile(png), save=True)

    html = render_to_string('Memory/includes/new_image.html', {'image': image})
    return {'html': html, 'image_id': image.pk, 'message': "Nouvelle illustration ajoutée."}


@shared_task
def analyze_retrospective_task(retrospective_id, user_id):
    """Thèmes (KeyBERT) + statistiques d'humeur, puis résumé automatique."""
    user = User.objects.get(pk=user_id)
    retrospective = Retrospective.objects.get(pk=retrospective_id, user=user)

    # Sans entrées choisies, on prend toutes celles de la période
    if not retrospective.entries.exists():
        retrospective.entries.set(services.entries_in_period(retrospective))

    keywords = services.extract_keywords(retrospective, user)
    stats = services.retrospective_stats(retrospective)

    retrospective.keywords = keywords
    if not retrospective.summary:
        retrospective.summary = services.build_summary(retrospective, keywords, stats)
    retrospective.save()

    html = render_to_string('Memory/includes/retrospective_analysis.html',
                            {'retrospective': retrospective, 'stats': stats,
                             'entries': retrospective.entries.order_by('entry_date')})
    return {'html': html, 'message': "Analyse terminée."}
