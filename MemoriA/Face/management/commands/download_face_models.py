"""Télécharge les modèles ONNX de détection (YuNet) et de reconnaissance (SFace) des visages.

Utilisation : python manage.py download_face_models
Les fichiers vont dans Face/ai_models/ (ils ne sont pas dans Git).
"""
import urllib.request
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

# Modèles officiels d'OpenCV (dépôt opencv_zoo)
ZOO = 'https://github.com/opencv/opencv_zoo/raw/main/models'
MODELS = {
    'face_detection_yunet_2023mar.onnx': f'{ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx',
    'face_recognition_sface_2021dec.onnx': f'{ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx',
}
MIN_SIZE = 100 * 1024        # un fichier plus petit est une page d'erreur, pas un modèle


def models_dir():
    return Path(settings.BASE_DIR) / 'Face' / 'ai_models'


class Command(BaseCommand):
    help = "Télécharge les modèles YuNet et SFace dans Face/ai_models/."

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help="Télécharge même si le fichier existe déjà")

    def handle(self, *args, **options):
        folder = models_dir()
        folder.mkdir(parents=True, exist_ok=True)
        for name, url in MODELS.items():
            path = folder / name
            if path.exists() and path.stat().st_size >= MIN_SIZE and not options['force']:
                self.stdout.write(f"Déjà présent : {name}")
                continue
            self.stdout.write(f"Téléchargement de {name}…")
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    data = response.read()
            except OSError as error:
                raise CommandError(f"Le téléchargement de {name} a échoué : {error}. Relancez la commande.")
            if len(data) < MIN_SIZE:
                raise CommandError(f"Le fichier reçu pour {name} est trop petit : ce n'est pas un modèle ONNX.")
            path.write_bytes(data)
            self.stdout.write(self.style.SUCCESS(f"  {name} : {len(data) / 1024 / 1024:.1f} Mo"))
        self.stdout.write(self.style.SUCCESS(f"Modèles prêts dans {folder}"))
