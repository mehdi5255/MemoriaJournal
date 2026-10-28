# Tâches Celery de Face : l'analyse des photos ne bloque jamais la requête HTTP
from celery import shared_task
from celery.signals import worker_ready
from django.conf import settings
from django.contrib.auth.models import User
from django.template.loader import render_to_string

from Account.services import AIServiceError

from . import services
from .models import Person, Photo


@worker_ready.connect
def preload_face_models(**kwargs):
    """Charge YuNet et SFace (légers) au démarrage du worker, et BLIP, DETR et la traduction en mode local."""
    if getattr(settings, 'TESTING', False) or not settings.PRELOAD_AI_MODELS:
        return
    loaders = [services.get_face_detector, services.get_face_recognizer]
    if settings.FACE_BACKEND == 'local':
        loaders += [services.get_local_captioner, services.get_local_object_detector, services.get_translator]
    for load in loaders:
        try:
            load()
        except Exception:
            pass        # chargé à la première utilisation


@shared_task
def analyze_photo_task(photo_id, user_id):
    """Légende (si vide), objets détectés, visages et « C'est X ? ».
    Chaque partie est indépendante : si la légende échoue, les visages sont quand même détectés."""
    from .views import faces_with_boxes
    user = User.objects.get(pk=user_id)
    photo = Photo.objects.select_related('entry').get(pk=photo_id, entry__user=user)
    errors = []

    if not photo.caption:
        try:
            photo.caption = services.caption_photo(photo, user)[:255]
        except AIServiceError as error:
            errors.append(str(error))
    try:
        photo.detected_objects = services.detect_objects(photo, user)
    except AIServiceError as error:
        errors.append(str(error))
    photo.save(update_fields=['caption', 'detected_objects'])

    try:
        new_faces = services.analyze_faces(photo, user)
    except AIServiceError as error:
        errors.append(str(error))
        new_faces = None

    if len(errors) == 3:
        raise AIServiceError(errors[0])

    html = render_to_string('Face/includes/photo_analysis.html', {
        'photo': photo, 'faces': faces_with_boxes(photo), 'errors': errors,
        'new_count': len(new_faces) if new_faces is not None else None,
        'suggested': [f for f in (new_faces or []) if f.person],
    })
    return {'html': html, 'message': "Analyse de la photo terminée."}


@shared_task
def update_person_encoding_task(person_id, user_id):
    """Recalcule l'empreinte d'une personne après une confirmation (meilleure reconnaissance ensuite)."""
    user = User.objects.get(pk=user_id)
    person = Person.objects.get(pk=person_id, user=user)
    count = services.update_person_encoding(person, user)
    return {'message': f"Empreinte de {person.name} mise à jour ({count} visage(s))."}


@shared_task
def group_unknown_faces_task(user_id):
    """Regroupe les visages sans nom qui se ressemblent (DBSCAN)."""
    user = User.objects.get(pk=user_id)
    groups = services.group_unknown_faces(user)
    return {'groups': groups, 'message': f"{len(groups)} groupe(s) de visages trouvé(s)."}
