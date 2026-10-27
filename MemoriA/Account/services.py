# Services communs : mesure des appels aux services IA
import time

from .models import ActivityLog


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
