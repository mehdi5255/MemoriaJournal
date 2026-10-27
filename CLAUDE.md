# CLAUDE.md — Projet MémoriA

Ce fichier décrit le projet. Lis-le en entier avant chaque tâche et respecte-le strictement.
Le projet est noté avec une **grille d'évaluation** (section 10) : chaque tâche doit contribuer à un critère de cette grille.

## 1. Le projet

MémoriA est un **journal personnel web intelligent et multimodal** (projet universitaire Django, classe 5TWIN, équipe de 5, 4 semaines).
L'utilisateur raconte sa journée avec du texte, sa voix et ses photos ; l'IA transcrit, analyse l'humeur, reconnaît les visages, écrit un récit, génère une illustration et envoie des rappels personnalisés.

Acteurs : **Utilisateur**, **Administrateur**, **Services IA** (acteur secondaire).

## 2. Stack imposée (ne pas changer)

- Python 3.12, **Django 5.2**, Pillow, base **SQLite**
- Environnement virtuel `env/` créé avec `virtualenv`, **à côté** du dossier projet
- Développement sous **Windows** (`env\Scripts\activate`, `python`, pas `python3`)
- Front : templates Django + **Bootstrap 5 via CDN** + JavaScript simple (pas de React, pas de npm)
- Tâches IA asynchrones : **Celery + Redis**
- Secrets et réglages dans `.env` lu avec `python-dotenv` : `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `GEMINI_API_KEY`, `HF_TOKEN`, `REDIS_URL`
- Tests : `unittest` de Django (`python manage.py test`) + **coverage.py**
- Intégration et déploiement : **GitHub Actions** (CI) + **Docker / docker-compose** + mise en ligne
- Pas d'AWS S3 : les fichiers vont dans `media/`

## 3. Structure du projet

```
memoria/
├── env/
├── requirements.txt
├── .gitignore            (env/, __pycache__/, *.pyc, db.sqlite3, media/, .env, htmlcov/, .coverage)
├── .env  /  .env.example
├── Dockerfile
├── docker-compose.yml    (web, worker Celery, redis)
├── .github/workflows/ci.yml
├── CLAUDE.md
├── docs/                 metrics/ (rapports IA), accessibilite/ (audits), DEPLOIEMENT.md
└── MemoriA/
    ├── manage.py
    ├── MemoriA/          settings.py, urls.py, celery.py, __init__.py
    ├── Account/          Partie commune       : Profile, ActivityLog
    ├── Journal/          Khalil Fraj          : JournalEntry, Tag
    ├── Voice/            Seif Eddine Ben Fraj : VoiceNote, MoodRecord
    ├── Face/             Sabri Trabelsi       : Photo, Person, FaceTag
    ├── Memory/           Mehdi Ayachi         : Story, GeneratedImage, Retrospective
    ├── Reminder/         Emir Djebbi          : Reminder, DailyPrompt
    ├── templates/        base.html, home.html, registration/, 404.html, 500.html, 403.html
    ├── static/           css/memoria.css, js/ai-status.js, js/recorder.js
    └── media/
```

Chaque app contient : `models.py`, `admin.py`, `forms.py`, `views.py`, `urls.py`,
`services.py` (appels IA), `tasks.py` (tâches Celery), `templates/<App>/...`,
`tests/` (`test_models.py`, `test_views.py`, `test_services.py`),
`evaluation/` (jeu de données étiqueté pour les métriques IA),
`management/commands/evaluate_<app>.py`.

## 4. Conventions de code (style du projet exemple GestionEvenement)

- Noms d'apps avec majuscule : `Account`, `Journal`, `Voice`, `Face`, `Memory`, `Reminder`.
- Listes de choix en majuscules en haut de la classe : `CATEGORY_CHOICES = [('Valeur','Libellé'), ...]`.
- Dates : `creation_date = models.DateTimeField(auto_now_add=True)` et `update_date = models.DateTimeField(auto_now=True)`.
- Toujours un `related_name` sur les ForeignKey / ManyToMany.
- Règles métier dans `clean()` avec `raise ValidationError("message en français")`.
- `__str__` sur chaque modèle. Noms de champs en anglais, textes affichés en **français**.
- Vues : **vues génériques à base de classes** (`ListView`, `DetailView`, `CreateView`, `UpdateView`, `DeleteView`).
- URLs nommées avec `app_name` (ex. `journal:entry_list`).
- Messages de confirmation avec `django.contrib.messages`.
- Code simple et lisible par des étudiants, commentaires courts en français. Pas de sur-ingénierie.

## 5. Règles pour l'IA

- **Tous** les appels IA sont dans `services.py` de l'app ; les appels longs sont lancés par une tâche Celery dans `tasks.py`. Jamais d'appel IA dans un template.
- **Asynchrone** : la vue lance la tâche (`.delay()`) et répond tout de suite. La page affiche un **indicateur de chargement** (`aria-live="polite"`) et interroge toutes les 2 s une vue JSON commune `ai/task/<task_id>/` (dans Account) qui renvoie `PENDING / SUCCESS / FAILURE`. Script commun : `static/js/ai-status.js`.
- **Erreurs** : chaque appel est dans un `try/except` ; si l'API échoue, l'application **continue de fonctionner** : message clair (« Le service de transcription ne répond pas. Réessayez dans un instant. ») et bouton **Réessayer**. Jamais de page plantée.
- **Mesure** : chaque appel crée une ligne `ActivityLog` (action, service, statut, **durée en ms**) via `Account/services.py → log_ai_call(user, action, service, func, *args)`.
- **Clés** uniquement dans `.env`, jamais dans le code.
- Hugging Face : `huggingface_hub.InferenceClient(token=HF_TOKEN)` sauf mention contraire. Gemini : SDK `google-genai`, modèle `gemini-2.5-flash`.
- Les noms de modèles Hugging Face ci-dessous sont des propositions : si un modèle n'est plus disponible, en choisir un équivalent et le signaler.
- En test, `CELERY_TASK_ALWAYS_EAGER = True` et **tous les appels IA sont mockés**.

## 6. Modèles (à créer exactement ainsi)

### Account/models.py (partie commune)

```python
from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):

    LANGUAGE_CHOICES = [('fr', 'Français'),
                        ('en', 'English'),
                        ('ar', 'Arabe')]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True)
    bio = models.TextField(blank=True)
    birth_date = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default='fr')
    creation_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.user.username


class ActivityLog(models.Model):

    STATUS_CHOICES = [('Succes', 'Succès'),
                      ('Echec', 'Échec')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_logs')
    action = models.CharField(max_length=100)
    ai_service = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Succes')
    duration_ms = models.PositiveIntegerField(default=0)
    creation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.action}"
```

### Journal/models.py (Khalil)

```python
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone


class Tag(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tags')
    name = models.CharField(max_length=50)
    color = models.CharField(max_length=7, default='#2F6FD6')
    creation_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'name')

    def __str__(self):
        return self.name


class JournalEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='entries')
    title = models.CharField(max_length=150)
    content = models.TextField()
    entry_date = models.DateField(default=timezone.localdate)
    location = models.CharField(max_length=100, blank=True)
    is_favorite = models.BooleanField(default=False)
    embedding = models.JSONField(null=True, blank=True)   # IA : recherche par le sens
    tags = models.ManyToManyField(Tag, blank=True, related_name='entries')
    creation_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-entry_date']

    def clean(self):
        if self.entry_date and self.entry_date > timezone.localdate():
            raise ValidationError("La date de l'entrée ne peut pas être dans le futur.")

    def __str__(self):
        return self.title
```

### Voice/models.py (Seif)

```python
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from Journal.models import JournalEntry


class VoiceNote(models.Model):

    STATUS_CHOICES = [('EnAttente', 'En attente'),
                      ('Termine', 'Terminé'),
                      ('Echec', 'Échec')]

    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='voice_notes')
    title = models.CharField(max_length=100)
    audio = models.FileField(upload_to='voice_notes/')
    duration = models.PositiveIntegerField(default=0)        # en secondes
    transcription = models.TextField(blank=True)             # IA : Whisper
    language = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='EnAttente')
    creation_date = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.duration > 600:
            raise ValidationError("Une note vocale ne doit pas dépasser 10 minutes.")

    def __str__(self):
        return self.title


class MoodRecord(models.Model):

    SOURCE_CHOICES = [('Voix', 'Voix'),
                      ('Texte', 'Texte'),
                      ('Manuel', 'Manuel')]

    EMOTION_CHOICES = [('Joie', 'Joie'),
                       ('Tristesse', 'Tristesse'),
                       ('Colere', 'Colère'),
                       ('Peur', 'Peur'),
                       ('Surprise', 'Surprise'),
                       ('Neutre', 'Neutre')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mood_records')
    entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='mood_records')
    voice_note = models.OneToOneField(VoiceNote, on_delete=models.SET_NULL, null=True, blank=True, related_name='mood_record')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='Manuel')
    emotion = models.CharField(max_length=15, choices=EMOTION_CHOICES)
    score = models.FloatField(default=0)                     # entre 0 et 1
    note = models.TextField(blank=True)
    record_date = models.DateField(default=timezone.localdate)
    creation_date = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.score < 0 or self.score > 1:
            raise ValidationError("Le score doit être compris entre 0 et 1.")

    def __str__(self):
        return f"{self.emotion} - {self.record_date}"
```

### Face/models.py (Sabri)

```python
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from Journal.models import JournalEntry


class Photo(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='photos/')
    caption = models.CharField(max_length=255, blank=True)              # IA : légende automatique
    detected_objects = models.JSONField(default=list, blank=True)       # IA : objets détectés
    taken_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)
    faces_analyzed = models.BooleanField(default=False)
    creation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.caption or f"Photo {self.id}"


class Person(models.Model):

    RELATION_CHOICES = [('Famille', 'Famille'),
                        ('Ami', 'Ami'),
                        ('Collegue', 'Collègue'),
                        ('Autre', 'Autre')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='persons')
    name = models.CharField(max_length=100)
    relation = models.CharField(max_length=10, choices=RELATION_CHOICES, default='Ami')
    birth_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    cover_photo = models.ImageField(upload_to='persons/', null=True, blank=True)
    face_encoding = models.JSONField(null=True, blank=True)             # IA : empreinte du visage
    creation_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'name')

    def __str__(self):
        return self.name


class FaceTag(models.Model):
    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name='face_tags')
    person = models.ForeignKey(Person, on_delete=models.SET_NULL, null=True, blank=True, related_name='face_tags')
    x = models.PositiveIntegerField()
    y = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    confidence = models.FloatField(default=0)                           # IA : score de confiance
    is_confirmed = models.BooleanField(default=False)
    creation_date = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.confidence < 0 or self.confidence > 1:
            raise ValidationError("La confiance doit être comprise entre 0 et 1.")

    def __str__(self):
        return f"Visage sur photo {self.photo_id}"
```

### Memory/models.py (Mehdi)

```python
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from Journal.models import JournalEntry
from Face.models import Photo


class Story(models.Model):

    STYLE_CHOICES = [('Simple', 'Simple'),
                     ('Poetique', 'Poétique'),
                     ('Humoristique', 'Humoristique')]

    entry = models.OneToOneField(JournalEntry, on_delete=models.CASCADE, related_name='story')
    text = models.TextField()                                           # IA : Gemini
    style = models.CharField(max_length=15, choices=STYLE_CHOICES, default='Simple')
    prompt = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    creation_date = models.DateTimeField(auto_now_add=True)
    update_date = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Récit : {self.entry.title}"


class GeneratedImage(models.Model):

    STYLE_CHOICES = [('Realiste', 'Réaliste'),
                     ('Aquarelle', 'Aquarelle'),
                     ('Dessin', 'Dessin')]

    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='images')
    source_photo = models.ForeignKey(Photo, on_delete=models.SET_NULL, null=True, blank=True)  # image vers image
    image = models.ImageField(upload_to='generated/')                  # IA : Stable Diffusion
    prompt = models.TextField()
    style = models.CharField(max_length=10, choices=STYLE_CHOICES, default='Realiste')
    is_main = models.BooleanField(default=False)
    creation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Image {self.id} - {self.style}"


class Retrospective(models.Model):

    TYPE_CHOICES = [('Semaine', 'Semaine'),
                    ('Mois', 'Mois')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='retrospectives')
    period_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    start_date = models.DateField()
    end_date = models.DateField()
    title = models.CharField(max_length=150)
    summary = models.TextField(blank=True)
    keywords = models.JSONField(default=list, blank=True)              # IA : KeyBERT
    entries = models.ManyToManyField(JournalEntry, blank=True, related_name='retrospectives')
    cover_image = models.ForeignKey(GeneratedImage, on_delete=models.SET_NULL, null=True, blank=True)
    creation_date = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValidationError("La date de fin doit être après la date de début.")

    def __str__(self):
        return self.title
```

### Reminder/models.py (Emir)

```python
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from Journal.models import JournalEntry


class Reminder(models.Model):

    CHANNEL_CHOICES = [('Email', 'Email'),
                       ('Application', 'Application')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reminders')
    reminder_time = models.TimeField()
    days = models.CharField(max_length=20, default='0,1,2,3,4,5,6')    # 0 = lundi ... 6 = dimanche
    channel = models.CharField(max_length=15, choices=CHANNEL_CHOICES, default='Application')
    message = models.TextField(blank=True)
    suggested_time = models.TimeField(null=True, blank=True)           # IA : meilleure heure
    is_active = models.BooleanField(default=True)
    last_sent = models.DateTimeField(null=True, blank=True)
    creation_date = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.reminder_time}"


class DailyPrompt(models.Model):

    STATUS_CHOICES = [('Envoye', 'Envoyé'),
                      ('Repondu', 'Répondu'),
                      ('Ignore', 'Ignoré')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_prompts')
    reminder = models.ForeignKey(Reminder, on_delete=models.CASCADE, related_name='prompts')
    prompt_date = models.DateField(default=timezone.localdate)
    question = models.TextField()                                       # IA : question personnalisée
    detected_entities = models.JSONField(default=list, blank=True)     # IA : NER
    answer_entry = models.ForeignKey(JournalEntry, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_prompts')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Envoye')
    creation_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('reminder', 'prompt_date')

    def __str__(self):
        return f"Question du {self.prompt_date}"
```

## 7. Fonctionnalités par module

Chaque classe des modules 1 à 5 a un **CRUD complet** (liste, détail, création, modification, suppression avec page de confirmation), **une fonctionnalité IA** et **une métrique d'évaluation**.

### Partie commune — Account (toute l'équipe)
- Inscription (`UserCreationForm` + création automatique du `Profile` par signal), connexion, déconnexion, profil modifiable, changement de mot de passe.
- **Rôles** : groupes Django `Utilisateur` et `Administrateur`, créés par une migration de données. L'inscription ajoute au groupe `Utilisateur`.
- Page **Statistiques** (`PermissionRequiredMixin`, permission `Account.view_activitylog`, groupe Administrateur) : utilisateurs, entrées, appels IA, taux d'échec, **temps de réponse moyen par service IA**.
- Vue JSON `ai/task/<task_id>/` pour suivre les tâches Celery.
- `base.html`, `home.html` (tableau de bord : les 7 derniers jours avec une pastille d'humeur, bouton « + Nouvelle entrée », dernières entrées, question du jour), pages `403.html`, `404.html`, `500.html`.

### Module 1 — Journal (Khalil Fraj)
| Classe | Fonctionnalité IA | Outil | Métrique |
|---|---|---|---|
| JournalEntry | **Recherche par le sens** (similarité cosinus entre embeddings) + « 3 souvenirs similaires ». Embedding recalculé à chaque création/modification (tâche Celery). | `sentence-transformers`, `paraphrase-multilingual-MiniLM-L12-v2` (local) | Precision@3 et MRR sur 20 requêtes annotées |
| Tag | **Suggestion d'étiquettes** : classe le texte parmi les étiquettes de l'utilisateur, cases pré-cochées. | HF zero-shot, `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` | Precision / Recall / F1 + matrice de confusion sur 40 textes étiquetés |
- Filtres : date, étiquette, favori.

### Module 2 — Voice (Seif Eddine Ben Fraj)
| Classe | Fonctionnalité IA | Outil | Métrique |
|---|---|---|---|
| VoiceNote | **Transcription** + langue détectée ; `status` `Termine` ou `Echec` ; bouton Réessayer. | HF ASR, `openai/whisper-large-v3` | WER (taux d'erreur de mots, `jiwer`) sur 10 enregistrements + temps moyen |
| MoodRecord | **Détection de l'émotion** depuis la transcription ou le texte (création auto), création manuelle possible. **Alerte** si 3 jours négatifs de suite. Graphique du mois (Chart.js via CDN, avec tableau de données accessible). | HF classification, modèle multilingue d'émotions (ex. `MilaNLProc/xlm-emo-t`) | Precision / Recall / F1 + matrice de confusion sur 60 phrases étiquetées |
- Enregistrement avec `MediaRecorder` (boutons utilisables au clavier) **ou** import d'un fichier. Limite 10 minutes, formats audio vérifiés.

### Module 3 — Face (Sabri Trabelsi)
| Classe | Fonctionnalité IA | Outil | Métrique |
|---|---|---|---|
| Photo | **Légende automatique** et **objets détectés** à l'upload. | HF `Salesforce/blip-image-captioning-base` + `facebook/detr-resnet-50` | Précision des objets détectés + évaluation qualitative des légendes (note 1 à 5 sur 20 photos) |
| FaceTag | **Détection des visages** avec score de confiance, cadres sur la photo. | OpenCV `cv2.FaceDetectorYN` (ONNX YuNet) | Precision / Recall de détection sur 20 photos annotées |
| Person | **Reconnaissance** (« C'est Sarah ? ») + **regroupement** des visages inconnus. Page « Mes proches » = un album par personne. | OpenCV `cv2.FaceRecognizerSF` (ONNX SFace) + `sklearn` DBSCAN | Accuracy d'identification + matrice de confusion entre personnes |
- **Pas de `face_recognition`/`dlib`** (installation trop difficile sous Windows). Fichiers ONNX dans `Face/ai_models/`, téléchargés par `python manage.py download_face_models`.
- Formats d'image vérifiés, taille max 10 Mo. Alt automatique = légende IA (modifiable).

### Module 4 — Memory (Mehdi Ayachi)
| Classe | Fonctionnalité IA | Outil | Métrique |
|---|---|---|---|
| Story | **Récit de la journée** à partir du texte, des transcriptions **et des photos** (multimodal), dans le style choisi ; « Régénérer » incrémente `version` ; texte modifiable. | Gemini `gemini-2.5-flash` | Grille qualitative (fidélité, style, longueur, note 1 à 5) sur 15 entrées + temps moyen |
| GeneratedImage | **Illustration** du récit dans le style choisi ; plusieurs images, une seule `is_main`. Bonus : transformation d'une vraie photo. | HF `stabilityai/stable-diffusion-xl-base-1.0` | Score de correspondance texte-image (CLIP score) + temps moyen |
| Retrospective | **Bilan semaine/mois** : thèmes extraits, moments forts, répartition des émotions. | `keybert` + statistiques | Precision@5 des mots-clés comparés à des thèmes annotés |

### Module 5 — Reminder (Emir Djebbi)
| Classe | Fonctionnalité IA | Outil | Métrique |
|---|---|---|---|
| Reminder | **Meilleure heure proposée** (`suggested_time`) apprise des heures d'écriture ; bouton « Appliquer ». | `scikit-learn` (`KMeans` sur les heures) | Erreur moyenne en minutes (MAE) sur des données de test |
| DailyPrompt | **Question personnalisée** à partir des personnes, lieux et événements des 3 dernières entrées. Répondre ouvre une entrée pré-remplie (statut `Repondu`). | HF NER, `Jean-Baptiste/camembert-ner` | Precision / Recall / F1 par type d'entité sur 30 phrases annotées |
- Commande `python manage.py send_reminders` (email avec `send_mail`, backend console en développement). Planification : **Celery Beat** (fonctionne aussi sous Windows) ; documenter aussi le Planificateur de tâches Windows en secours.

## 8. Charte graphique (style « carnet photo ») — accessible

- Fond `#FEFDFB`, cartes `#FFFFFF` avec ombre douce, texte `#2B2B33`, texte secondaire `#6E6E7A`
- Couleurs **vérifiées WCAG AA (≥ 4,5:1)** pour le texte et les boutons :
  - Actions principales : **`#C2412F`** (corail foncé). `#D94F3D` seulement pour la décoration ou le texte ≥ 24 px.
  - IA : **`#2563C9`** (bleu), blocs IA encadrés en pointillés bleus
  - Succès : **`#237A5C`** (menthe foncé). `#2E9E78` seulement pour la décoration.
  - Pastels de fond : `#FFF1C9`, `#DCEBFF`, `#FFE1DC`, `#D8F5EA` (toujours avec texte `#2B2B33`)
- Polices Google Fonts : **Poppins** (titres), **Nunito** (texte, 16 px minimum), **Caveat** (annotations décoratives seulement, jamais pour une information importante)
- Photos en style **polaroid** (cadre blanc, légère rotation)
- Variables CSS dans `static/css/memoria.css`, Bootstrap personnalisé par-dessus. **Responsive** (mobile, tablette, ordinateur).
- Une même mise en page sur toutes les pages : menu latéral, titre de page, fil d'Ariane, zone de messages.

## 9. Règles de travail

- Après chaque modification : `python manage.py check`, `makemigrations` / `migrate` si besoin, puis les tests de l'app.
- Ajouter chaque nouvelle bibliothèque à `requirements.txt`.
- Ne jamais commiter `.env`, `db.sqlite3`, `media/`, `env/`.
- Un module = une branche Git (`feature/journal`, `feature/voice`, `feature/face`, `feature/memory`, `feature/reminder`). **Chaque membre commite lui-même avec son nom** (la grille divise la note individuelle par 2 sans Git). Fusion dans `main` par pull request.
- Ne pas changer les noms des classes ni des champs de la section 6 sans demander (diagramme de classes présenté au jury).
- À la fin de chaque tâche : résumer ce qui a été fait, les commandes à lancer, et quels critères de la grille sont couverts.

## 10. Grille d'évaluation (20 points) — exigences à respecter

**Attention : pas de Git → note individuelle divisée par 2. Pas d'intégration et de déploiement → note d'équipe = 0.**

### 10.1 Fonctionnalités & CRUD (2 pts)
- CRUD complet et fluide sur toutes les classes, **aucun bug en démo**.
- Validation **côté serveur** (`clean()`, formulaires) **et côté client** (attributs HTML `required`, `maxlength`, `accept`, `type`), messages d'erreur clairs en français.

### 10.2 Tests (2 pts)
- **Tests unitaires** des modèles : chaque `clean()`, contraintes `unique_together`, `__str__`, méthodes métier.
- **Tests d'intégration** des vues avec `django.test.Client` : statuts HTTP (200, 302, 403, 404), données affichées, redirection vers la connexion si non connecté, **un utilisateur ne peut pas lire/modifier/supprimer les données d'un autre (404)**.
- Tests des services IA avec **mock** (succès et échec de l'API).
- **coverage.py** : `coverage run manage.py test` puis `coverage report` et `coverage html`. Objectif **≥ 80 %**. Garder le rapport dans `docs/`.

### 10.3 Besoins non fonctionnels (2 pts)
- **Sécurité OWASP** : CSRF actif sur tous les formulaires, ORM uniquement (aucune requête SQL brute), mots de passe hashés (système Django), `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` et clés API lus depuis `.env`, réglages de production (`SECURE_*`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` quand `DEBUG=False`), `python manage.py check --deploy` sans erreur grave.
- **Rôles et permissions** : `LoginRequiredMixin` sur toutes les vues privées, `PermissionRequiredMixin` pour l'administration, filtrage par `request.user` dans chaque `get_queryset()`, `user` rempli dans `form_valid()` (jamais par le formulaire). Impossible d'accéder à une page par URL directe sans droit. **Tests dédiés.**
- **Robustesse** : `try/except` pertinents, pages **403, 404, 500** personnalisées, validation de toutes les entrées (taille et type des fichiers).

### 10.4 Accessibilité Web (2 pts)
- Structure sémantique : `<header>`, `<nav aria-label="Menu principal">`, `<main id="contenu">`, `<footer>` ; **un seul `<h1>` par page**, puis `h2`, `h3` dans l'ordre.
- `alt` sur toutes les images (vide `alt=""` si décorative), `aria-label` sur les boutons-icônes, `aria-live` sur les zones de chargement IA, `<label>` lié à chaque champ, erreurs liées par `aria-describedby`.
- Contrastes WCAG AA (couleurs de la section 8), police ≥ 16 px.
- **Navigation au clavier** : lien « Aller au contenu » en premier, ordre de tabulation logique, **focus visible** (`:focus-visible` avec contour bien visible), menus et boîtes de confirmation utilisables au clavier, aucun piège au clavier.
- Attribut `lang="fr"`, titres de page (`<title>`) uniques.
- **Audit** Lighthouse et axe sur les pages principales, score accessibilité **≥ 90**, rapports dans `docs/accessibilite/`.

### 10.5 Intelligence artificielle (4 pts)
- Chaque IA répond à un vrai besoin (section 7) et est appelée proprement (service, tâche, gestion d'erreurs, clé dans `.env`).
- **Retour utilisateur** : indicateur de chargement pendant la tâche, résultat affiché sans recharger, message d'erreur explicite + Réessayer.
- **Métriques** : commande `python manage.py evaluate_<app>` par module, sur le jeu étiqueté de `<App>/evaluation/`. Elle calcule les métriques de la section 7 (`sklearn.metrics` : `classification_report`, `confusion_matrix`), enregistre la matrice de confusion en image (matplotlib) et un rapport Markdown commenté dans `docs/metrics/<app>.md`.
- **Performance** : temps de réponse mesuré (ActivityLog) et présenté (moyenne, maximum par service) ; appels longs traités par **Celery + Redis** pour ne jamais bloquer la requête HTTP ; résultats coûteux mis en cache (embeddings, modèles chargés une seule fois).

### 10.6 UX / UI (1 pt)
- Interface soignée, cohérente sur toutes les pages, responsive, parcours logique (tableau de bord → entrée → voix/photos → récit → souvenirs).
- États vides prévus (« Aucune entrée pour l'instant », avec bouton d'action), confirmations avant suppression, messages de succès.

### 10.7 Intégration & déploiement (obligatoire)
- **CI GitHub Actions** (`.github/workflows/ci.yml`) : à chaque push et pull request → installation, `migrate`, `check`, tests avec coverage (IA mockée, Celery en mode eager).
- **Docker** : `Dockerfile` (gunicorn) + `docker-compose.yml` (web, worker Celery, Celery Beat, redis) qui lance toute l'application avec `docker compose up`.
- **Fichiers statiques** servis avec `whitenoise`.
- **Mise en ligne** sur une plateforme d'hébergement (ex. Render, Railway ou PythonAnywhere — vérifier les offres gratuites actuelles), procédure complète dans `docs/DEPLOIEMENT.md`.

### 10.8 Questions / réponses (8 pts, individuel)
- Chaque membre doit comprendre et savoir justifier son code : architecture MVT, sécurité, choix et métriques de l'IA, déploiement. Le code doit rester simple et commenté pour être expliqué à l'oral.

## 11. Installer Redis et Celery sous Windows

- Redis n'existe pas nativement sous Windows : utiliser **Docker Desktop** (`docker run -d -p 6379:6379 --name redis redis:7`) ou WSL.
- Lancer le worker avec : `celery -A MemoriA worker -l info --pool=solo` (obligatoire sous Windows).
- Lancer les tâches planifiées avec : `celery -A MemoriA beat -l info`.
