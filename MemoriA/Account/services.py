# Services communs : mesure des appels aux services IA
import time

from .models import ActivityLog


class AIServiceError(Exception):
    """Erreur d'un service IA avec un message clair en français, affiché tel quel à l'utilisateur.

    Commune à toutes les apps : la vue ai/task/<task_id>/ renvoie ce message à la page.
    """


def launch_task(request, task, *args):
    """Lance une tâche Celery et renvoie son identifiant, ou None si le service est indisponible.

    Utilisée par les vues de toutes les apps : un message clair est affiché au lieu d'une erreur 500.
    """
    from django.contrib import messages
    try:
        return task.delay(*args).id
    except AIServiceError as error:
        # En mode « eager » (tests), l'erreur de l'IA remonte directement
        messages.error(request, str(error))
    except Exception:
        messages.error(request, "Le service de génération est indisponible pour le moment "
                                "(Redis ou le worker Celery ne répond pas). Réessayez dans un instant.")
    return None


def log_ai_call(user, action, service, func, *args, **kwargs):
    """Appelle func(*args, **kwargs), mesure sa durée et crée une ligne ActivityLog.

    En cas d'erreur, l'échec est enregistré puis l'exception est relancée :
    c'est à l'appelant (services.py / tasks.py de l'app) d'afficher un message clair.
    """
    start = time.perf_counter()
    status = 'Succes'
    try:
        return func(*args, **kwargs)
    except Exception:
        status = 'Echec'
        raise
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        ActivityLog.objects.create(user=user, action=action, ai_service=service,
                                   status=status, duration_ms=duration_ms)
