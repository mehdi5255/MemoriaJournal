#!/bin/sh
# Démarrage sur Render (offre gratuite) : un seul conteneur pour le site ET le worker Celery,
# car Render ne propose pas de « Background Worker » gratuit. Voir docs/DEPLOIEMENT.md.
set -e
cd /app/MemoriA

# Base de données (SQLite, effacée à chaque redémarrage du service gratuit)
python manage.py migrate --noinput

# Superutilisateur créé à partir des variables DJANGO_SUPERUSER_* (ignoré s'il existe déjà)
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    python manage.py createsuperuser --noinput || true
fi

# Worker Celery (+ Celery Beat avec -B pour les rappels) en arrière-plan, un seul processus
celery -A MemoriA worker -B -l info --pool=solo --schedule /data/celerybeat-schedule &

# Site web : gunicorn sur le port donné par Render (1 processus pour rester sous 512 Mo)
exec gunicorn MemoriA.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --timeout 120
