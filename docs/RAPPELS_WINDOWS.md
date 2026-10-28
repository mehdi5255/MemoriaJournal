# Envoi des rappels : Celery Beat ou Planificateur de tâches Windows

Les rappels sont envoyés par la commande `python manage.py send_reminders` : pour chaque rappel actif
dont l'heure est passée aujourd'hui, elle crée la **question du jour** personnalisée et envoie un e-mail
si le canal est « Email » (en développement, l'e-mail s'affiche dans la console).

## 1. Solution normale : Celery Beat

Celery Beat lance la tâche `Reminder.tasks.send_reminders_task` **toutes les 5 minutes**
(réglage `CELERY_BEAT_SCHEDULE` dans `MemoriA/settings.py`). Il faut Redis, un worker et Beat :

```bash
docker start redis
```
```bash
.\worker.ps1
```
```bash
.\beat.ps1
```

Avec Docker, le service `beat` de `docker-compose.yml` le fait déjà : `docker compose up`.

## 2. Solution de secours : Planificateur de tâches Windows

Si Celery Beat ne peut pas tourner, Windows peut lancer la commande toutes les 5 minutes.
Dans un terminal **PowerShell**, depuis le dossier `Journal` :

```bash
schtasks /Create /TN "MemoriA rappels" /SC MINUTE /MO 5 /TR "C:\Users\mehdi\Desktop\Journal\env\Scripts\python.exe C:\Users\mehdi\Desktop\Journal\MemoriA\manage.py send_reminders" /F
```

- Vérifier : `schtasks /Query /TN "MemoriA rappels"`
- Lancer tout de suite : `schtasks /Run /TN "MemoriA rappels"`
- Supprimer : `schtasks /Delete /TN "MemoriA rappels" /F`

Adapter les chemins si le projet est ailleurs. La commande ne renvoie jamais deux fois le même rappel
le même jour (champ `last_sent`), on peut donc la lancer aussi souvent que l'on veut.

## 3. Tester sans attendre

```bash
python manage.py send_reminders --force
```
