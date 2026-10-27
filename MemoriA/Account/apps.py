from django.apps import AppConfig


class AccountConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Account'
    verbose_name = 'Comptes'

    def ready(self):
        # Branche les signaux (création du profil)
        from . import signals  # noqa: F401
