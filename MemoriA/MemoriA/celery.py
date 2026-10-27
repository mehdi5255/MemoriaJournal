"""Configuration de Celery pour MemoriA (tâches IA asynchrones)."""

import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'MemoriA.settings')

app = Celery('MemoriA')

# Lit tous les réglages qui commencent par CELERY_ dans settings.py
app.config_from_object('django.conf:settings', namespace='CELERY')

# Cherche automatiquement les fichiers tasks.py de chaque app
app.autodiscover_tasks()
