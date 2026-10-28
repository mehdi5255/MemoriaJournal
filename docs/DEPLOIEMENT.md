# Déploiement de MémoriA

MémoriA se déploie de deux façons :

1. **En ligne, gratuitement, sur [Render](https://render.com)** : c'est la version présentée au jury.
2. **Sur n'importe quelle machine avec Docker** (`docker compose up`) : toute l'application, IA locales comprises.

L'intégration continue (GitHub Actions, `.github/workflows/ci.yml`) lance les tests à chaque push : on ne
déploie que du code dont la CI est verte.

---

## 1. Mise en ligne gratuite sur Render

### Pourquoi Render ?

Offres gratuites comparées en octobre 2026 :

| Hébergeur | Gratuit | Limite pour MémoriA |
|---|---|---|
| **Render** | Oui, sans carte bancaire : 1 site Docker + 1 Redis | 512 Mo de mémoire, pas de worker séparé gratuit, mise en veille après 15 min |
| PythonAnywhere | Oui | 100 s de calcul par jour, pas de Redis ni de Celery |
| Hugging Face Spaces | Non (Spaces Docker réservés à l'offre PRO) | — |
| Oracle Cloud Always Free | Oui, mais carte bancaire demandée | Plus long à installer |

### Architecture sur Render

```
Internet ──HTTPS──> Render (offre Free, 1 conteneur Docker)
                      ├─ gunicorn (site Django, fichiers statiques servis par whitenoise)
                      └─ worker Celery + Celery Beat (tâches IA et rappels)
                                │
                      Render Key Value (Redis gratuit) : file des tâches Celery et résultats
```

Render n'a pas de « Background Worker » gratuit : `render-start.sh` lance donc le worker Celery **dans le même
conteneur** que le site, en arrière-plan, puis gunicorn.

Fichiers utilisés :

| Fichier | Rôle |
|---|---|
| `render.yaml` | Blueprint : crée le site et le Redis, déclare les variables d'environnement |
| `Dockerfile` | Image de l'application (dépendances, fichiers statiques, modèles ONNX des visages) |
| `render-start.sh` | Démarrage : `migrate`, superutilisateur, worker Celery, gunicorn |

### Étapes

1. **Vérifier que la CI est verte** sur la branche `main` (onglet *Actions* du dépôt GitHub).
2. Se connecter sur [dashboard.render.com](https://dashboard.render.com) avec son compte GitHub et autoriser
   l'accès au dépôt `MemoriaJournal`.
3. **New → Blueprint**, choisir le dépôt, branche `main`. Render lit `render.yaml` et affiche les 2 ressources
   à créer : `memoria` (Web Service, Free) et `memoria-redis` (Key Value, Free).
4. Remplir les variables marquées « à saisir » :
   - `GEMINI_API_KEY` : clé Google AI Studio ;
   - `HF_TOKEN` : jeton Hugging Face ;
   - `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` : compte administrateur
     créé à chaque démarrage.

   `SECRET_KEY` est **générée par Render** (longue et aléatoire) : elle n'est jamais écrite dans le dépôt.
5. Cliquer sur **Deploy Blueprint**. La première construction dure 10 à 20 minutes (PyTorch est volumineux).
6. Ouvrir l'adresse donnée par Render, par exemple `https://memoria-xxxx.onrender.com`, et se connecter avec le
   superutilisateur.
7. Chaque `git push` sur `main` redéploie automatiquement le site.

### Réglages de production

| Réglage | Valeur | Pourquoi |
|---|---|---|
| `DEBUG` | `False` | Pas de détails techniques affichés, pages 403/404/500 personnalisées |
| `SECRET_KEY` | générée par Render | Signature des sessions et des jetons CSRF |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | adresse Render (`RENDER_EXTERNAL_HOSTNAME`) | Ajoutée automatiquement par `settings.py` |
| `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, HSTS | activés quand `DEBUG=False` | HTTPS obligatoire |
| `*_BACKEND` | `huggingface` | Les IA passent par les API : pas de modèle local dans 512 Mo |
| `PRELOAD_AI_MODELS` | `False` | Le worker ne charge aucun modèle au démarrage |
| `SERVE_MEDIA` | `True` | Django sert les photos et notes vocales envoyées (pas de serveur de fichiers séparé) |

Vérification : `DEBUG=False python manage.py check --deploy` ne signale aucun problème avec la clé générée.

### Limites de l'offre gratuite (à dire au jury)

- **Mise en veille** après 15 minutes sans visite : la première ouverture prend environ une minute.
- **Disque non persistant** : la base SQLite et les fichiers envoyés sont effacés à chaque redémarrage ou
  déploiement. Le superutilisateur est recréé automatiquement. Pour garder les données, il faudrait une base
  PostgreSQL et un stockage de fichiers payants.
- **512 Mo de mémoire** : les IA qui ont besoin d'un modèle local (recherche par le sens, Whisper local,
  SD-Turbo…) ne tournent pas en ligne. Elles passent par l'API Hugging Face (crédit nécessaire) ; sans crédit,
  l'application affiche un message clair avec un bouton Réessayer, sans planter. Les IA légères fonctionnent :
  KMeans (heure conseillée), détection et reconnaissance des visages (OpenCV ONNX), récits Gemini.
- La démonstration complète des IA locales se fait avec Docker (partie 2).

---

## 2. Déploiement avec Docker (toute l'application)

Sur une machine avec Docker (Docker Desktop sous Windows) :

```bash
cp .env.example .env          # puis remplir SECRET_KEY, GEMINI_API_KEY, HF_TOKEN…
docker compose up --build
```

Puis ouvrir http://localhost:8000. `docker-compose.yml` lance 4 services :

| Service | Rôle |
|---|---|
| `web` | `migrate` puis gunicorn |
| `worker` | worker Celery (tâches IA) |
| `beat` | Celery Beat (envoi des rappels toutes les 5 minutes) |
| `redis` | file des tâches |

La base et les fichiers envoyés sont gardés dans des volumes Docker (`db_data`, `media_data`).
Créer l'administrateur : `docker compose exec web python manage.py createsuperuser`.

---

## 3. En cas de problème

| Symptôme | Cause et solution |
|---|---|
| « Bad Request (400) » | Adresse absente de `ALLOWED_HOSTS` : vérifier que le service s'appelle bien sur Render (variable `RENDER_EXTERNAL_HOSTNAME`) |
| Erreur CSRF à la connexion | `CSRF_TRUSTED_ORIGINS` doit contenir l'adresse en `https://` (ajoutée automatiquement) |
| Les tâches IA restent « en cours » | Le worker n'a pas démarré : lire les *Logs* du service sur Render (ligne `celery@… ready.`) |
| « Le service … ne répond pas » | Clé API absente ou crédit Hugging Face épuisé : vérifier `HF_TOKEN` / `GEMINI_API_KEY` |
| Le service redémarre en boucle | Mémoire dépassée (512 Mo) : vérifier `PRELOAD_AI_MODELS=False` et les `*_BACKEND=huggingface` |
