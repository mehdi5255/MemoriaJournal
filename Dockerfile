# Image de l'application MemoriA (web, worker Celery et beat utilisent la même image)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances d'abord : cette couche reste en cache tant que requirements.txt ne change pas
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Code de l'application
COPY MemoriA/ ./MemoriA/
WORKDIR /app/MemoriA

# Fichiers statiques collectés dans l'image (servis par whitenoise)
# et modèles ONNX des visages (YuNet + SFace, ~39 Mo) téléchargés une fois
RUN SECRET_KEY=build-only DEBUG=False python manage.py collectstatic --noinput \
    && SECRET_KEY=build-only python manage.py download_face_models

# Script de démarrage utilisé par Render (site + worker dans un seul conteneur)
COPY render-start.sh /app/render-start.sh

# Utilisateur sans droits d'administration (bonne pratique de sécurité)
RUN useradd --create-home memoria && mkdir -p /data /app/MemoriA/media \
    && chown -R memoria:memoria /data /app/MemoriA/media
USER memoria

EXPOSE 8000

# Par défaut : migrations puis serveur gunicorn
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn MemoriA.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120"]
