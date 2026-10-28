"""
Réglages du projet MemoriA.

Les valeurs sensibles (SECRET_KEY, DEBUG, ALLOWED_HOSTS, clés API) sont lues
depuis le fichier .env à la racine du dépôt (voir .env.example).
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# BASE_DIR = dossier MemoriA/ (celui qui contient manage.py)
BASE_DIR = Path(__file__).resolve().parent.parent

# Le fichier .env est à la racine du dépôt, un niveau au-dessus de BASE_DIR
load_dotenv(BASE_DIR.parent / '.env')


# --- Sécurité : valeurs lues depuis .env ---

SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY manquante : copiez .env.example en .env et remplissez-la.")

DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

# Nécessaire en production derrière HTTPS (ex. https://memoria.onrender.com)
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o.strip()]

# Render donne l'adresse publique du site dans RENDER_EXTERNAL_HOSTNAME (voir docs/DEPLOIEMENT.md)
RENDER_EXTERNAL_HOSTNAME = os.getenv('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
    CSRF_TRUSTED_ORIGINS.append(f'https://{RENDER_EXTERNAL_HOSTNAME}')

# Hébergement gratuit (peu de mémoire) : ne pas charger les modèles IA locaux au démarrage du worker
PRELOAD_AI_MODELS = os.getenv('PRELOAD_AI_MODELS', 'True').lower() in ('true', '1', 'yes')
# Sans serveur de fichiers séparé (Render gratuit), Django sert lui-même les fichiers de media/
SERVE_MEDIA = os.getenv('SERVE_MEDIA', 'False').lower() in ('true', '1', 'yes')

# Clés des services IA
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')
HF_TOKEN = os.getenv('HF_TOKEN', '')

# Modèles IA (modifiables dans .env si un modèle n'est plus disponible)
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
# Modèle « juge » de l'évaluation (evaluate_memory) : différent du modèle évalué, avec son propre quota
GEMINI_JUDGE_MODEL = os.getenv('GEMINI_JUDGE_MODEL', 'gemini-3.5-flash-lite')
HF_IMAGE_MODEL = os.getenv('HF_IMAGE_MODEL', 'stabilityai/stable-diffusion-xl-base-1.0')
HF_IMAGE_TO_IMAGE_MODEL = os.getenv('HF_IMAGE_TO_IMAGE_MODEL', 'stabilityai/stable-diffusion-xl-refiner-1.0')
KEYBERT_MODEL = os.getenv('KEYBERT_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')

# Moteur des illustrations :
#   huggingface : Stable Diffusion XL via l'API Hugging Face (par défaut)
#   local       : SD-Turbo sur le processeur, gratuit et sans Internet (plus lent)
#   auto        : Hugging Face, puis SD-Turbo local si l'API échoue (ex. plus de crédit)
IMAGE_BACKEND = os.getenv('IMAGE_BACKEND', 'huggingface').lower()
LOCAL_IMAGE_MODEL = os.getenv('LOCAL_IMAGE_MODEL', 'stabilityai/sd-turbo')

# Module Voice : transcription et émotions (mêmes valeurs possibles : huggingface, local, auto)
VOICE_BACKEND = os.getenv('VOICE_BACKEND', 'huggingface').lower()
HF_WHISPER_MODEL = os.getenv('HF_WHISPER_MODEL', 'openai/whisper-large-v3')
LOCAL_WHISPER_MODEL = os.getenv('LOCAL_WHISPER_MODEL', 'openai/whisper-base')
EMOTION_MODEL = os.getenv('EMOTION_MODEL', 'MilaNLProc/xlm-emo-t')

# Module Face : légende et objets (huggingface, local ou auto) ; visages toujours en local avec OpenCV
FACE_BACKEND = os.getenv('FACE_BACKEND', 'huggingface').lower()
CAPTION_MODEL = os.getenv('CAPTION_MODEL', 'Salesforce/blip-image-captioning-base')
OBJECT_MODEL = os.getenv('OBJECT_MODEL', 'facebook/detr-resnet-50')
TRANSLATION_MODEL = os.getenv('TRANSLATION_MODEL', 'Helsinki-NLP/opus-mt-en-fr')

# Module Reminder : entités nommées des entrées (huggingface, local ou auto)
REMINDER_BACKEND = os.getenv('REMINDER_BACKEND', 'huggingface').lower()
NER_MODEL = os.getenv('NER_MODEL', 'Jean-Baptiste/camembert-ner')

# Module Journal : recherche par le sens (local) et suggestion d'étiquettes (huggingface, local ou auto)
JOURNAL_BACKEND = os.getenv('JOURNAL_BACKEND', 'huggingface').lower()
EMBEDDING_MODEL = os.getenv('EMBEDDING_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
ZERO_SHOT_MODEL = os.getenv('ZERO_SHOT_MODEL', 'MoritzLaurer/mDeBERTa-v3-base-mnli-xnli')


# --- Applications ---

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Apps du projet
    'Account',
    'Journal',
    'Voice',
    'Face',
    'Memory',
    'Reminder',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',      # fichiers statiques en production
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'MemoriA.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'MemoriA.wsgi.application'


# --- Base de données ---

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        # Avec Docker, la base est placée dans un volume partagé (SQLITE_PATH=/data/db.sqlite3)
        'NAME': os.getenv('SQLITE_PATH', BASE_DIR / 'db.sqlite3'),
    }
}


# --- Mots de passe ---

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# --- Langue et fuseau horaire ---

LANGUAGE_CODE = 'fr'
TIME_ZONE = 'Africa/Tunis'
USE_I18N = True
USE_TZ = True


# --- Fichiers statiques et médias ---

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}

MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Taille max d'un upload gardé en mémoire (les fichiers plus gros passent par le disque)
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --- Connexion ---

LOGIN_URL = 'account:login'
LOGIN_REDIRECT_URL = 'account:home'
LOGOUT_REDIRECT_URL = 'account:login'


# --- Emails (console en développement) ---

EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'MemoriA <noreply@memoria.local>')
# Adresse du site dans les e-mails de rappel
SITE_URL = os.getenv('SITE_URL', 'http://127.0.0.1:8000').rstrip('/')


# --- Celery + Redis (tâches IA asynchrones) ---

CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300           # une tâche IA ne dure jamais plus de 5 minutes
# Si Redis est arrêté, la page affiche vite un message clair au lieu d'attendre longtemps
CELERY_BROKER_CONNECTION_TIMEOUT = 3
CELERY_TASK_PUBLISH_RETRY_POLICY = {'max_retries': 1, 'interval_start': 0, 'interval_step': 1, 'interval_max': 1}
CELERY_REDIS_SOCKET_CONNECT_TIMEOUT = 3
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {'retry_policy': {'max_retries': 1, 'interval_start': 0,
                                                            'interval_step': 1, 'interval_max': 1}}

# Celery Beat : envoi des rappels toutes les 5 minutes (celery -A MemoriA beat -l info)
CELERY_BEAT_SCHEDULE = {
    'envoyer-les-rappels': {'task': 'Reminder.tasks.send_reminders_task', 'schedule': 300.0},
}

# Pendant les tests : tâches exécutées tout de suite, sans Redis
TESTING = len(sys.argv) > 1 and sys.argv[1] == 'test'
if TESTING:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_TASK_STORE_EAGER_RESULT = True
    # Tests identiques sur tous les PC : jamais de modèle local chargé
    IMAGE_BACKEND = 'huggingface'
    VOICE_BACKEND = 'huggingface'
    FACE_BACKEND = 'huggingface'
    REMINDER_BACKEND = 'huggingface'
    JOURNAL_BACKEND = 'huggingface'
    # Jamais de vrai appel IA pendant les tests (CLAUDE.md : tous les appels sont mockés)
    GEMINI_API_KEY = ''
    HF_TOKEN = ''
    # Pas de dossier staticfiles/ pendant les tests : on masque l'avertissement de whitenoise
    import warnings
    warnings.filterwarnings('ignore', message='No directory at')
    CELERY_BROKER_URL = 'memory://'
    CELERY_RESULT_BACKEND = 'cache+memory://'
    # Mots de passe hashés plus vite pour accélérer les tests
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}


# --- Réglages de sécurité en production (OWASP) ---

if not DEBUG:
    # Pas de redirection HTTPS pendant les tests (le client de test parle en HTTP)
    SECURE_SSL_REDIRECT = not TESTING and os.getenv('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 'yes')
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = 'DENY'

SESSION_COOKIE_HTTPONLY = True
SECURE_REFERRER_POLICY = 'same-origin'
