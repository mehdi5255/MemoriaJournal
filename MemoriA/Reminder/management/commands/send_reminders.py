"""Envoie les rappels dont l'heure est passée aujourd'hui (question du jour + e-mail si canal Email).

    python manage.py send_reminders            # rappels prévus maintenant
    python manage.py send_reminders --force    # tous les rappels actifs (pour tester)

Planification : Celery Beat la lance toutes les 5 minutes (celery -A MemoriA beat -l info).
Secours sous Windows : Planificateur de tâches, voir docs/RAPPELS_WINDOWS.md.
"""
from django.core.management.base import BaseCommand

from Reminder.services import send_due_reminders


class Command(BaseCommand):
    help = "Envoie les rappels prévus maintenant (questions du jour et e-mails)."

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help="Envoie tous les rappels actifs, quelle que soit l'heure")

    def handle(self, *args, **options):
        sent = send_due_reminders(force=options['force'])
        self.stdout.write(self.style.SUCCESS(f"{sent} rappel(s) envoyé(s)."))
