"""Télécharge le modèle d'illustration local (SD-Turbo, ~2,5 Go) avant la première utilisation.

Utilisation : python manage.py download_image_model
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Télécharge le modèle SD-Turbo utilisé quand IMAGE_BACKEND vaut local ou auto."

    def handle(self, *args, **options):
        from huggingface_hub import snapshot_download

        model = settings.LOCAL_IMAGE_MODEL
        self.stdout.write(f"Téléchargement de {model} (poids fp16 uniquement)…")
        try:
            path = snapshot_download(model, allow_patterns=[
                '*.json', '*.txt',
                'unet/*.fp16.safetensors', 'text_encoder/*.fp16.safetensors', 'vae/*.fp16.safetensors',
            ])
        except Exception as error:
            raise CommandError(f"Le téléchargement a échoué : {error}. Relancez la commande pour reprendre.")
        self.stdout.write(self.style.SUCCESS(f"Modèle prêt dans {path}"))
