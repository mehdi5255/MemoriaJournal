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
