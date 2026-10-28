# Tâches Celery de Journal : embeddings, recherche par le sens et suggestion d'étiquettes
from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string

from . import services
from .models import JournalEntry


@worker_ready.connect
def preload_journal_models(**kwargs):
    """Charge le modèle d'embeddings (toujours local) au démarrage du worker."""
    if getattr(settings, 'TESTING', False) or not settings.PRELOAD_AI_MODELS:
        return
    loaders = [services.get_embedder]
    if settings.JOURNAL_BACKEND == 'local':
        loaders.append(services.get_local_zero_shot)
    for load in loaders:
        try:
            load()
        except Exception:
            pass        # chargé à la première utilisation


@shared_task
def compute_embedding_task(entry_id, user_id):
    """Recalcule l'embedding après la création ou la modification d'une entrée."""
    user = User.objects.get(pk=user_id)
    entry = JournalEntry.objects.get(pk=entry_id, user=user)
    services.compute_embedding(entry, user)
    similar = services.similar_entries(entry)
    html = render_to_string('Journal/includes/similar.html', {'similar': similar, 'entry': entry})
    return {'html': html, 'message': "Souvenirs similaires trouvés." if similar else "Entrée analysée."}


@shared_task
def semantic_search_task(user_id, query):
    """Recherche par le sens : renvoie le fragment HTML des résultats."""
    user = User.objects.get(pk=user_id)
    results = services.semantic_search(user, query)
    html = render_to_string('Journal/includes/search_results.html', {'results': results, 'query': query})
    return {'html': html, 'message': f"{len(results)} entrée{'s' if len(results) > 1 else ''} trouvée"
                                     f"{'s' if len(results) > 1 else ''} par le sens."}


@shared_task
def suggest_tags_task(user_id, text):
    """Suggestion d'étiquettes : les cases correspondantes sont cochées par la page (tag_ids)."""
    user = User.objects.get(pk=user_id)
    found = services.suggest_tags(user, text)
    html = render_to_string('Journal/includes/tag_suggestion.html', {'found': found})
    names = ', '.join(tag.name for tag, _ in found)
    return {'html': html, 'tag_ids': [tag.pk for tag, _ in found],
            'message': f"Étiquettes proposées : {names}." if found else "Aucune étiquette ne correspond vraiment."}
