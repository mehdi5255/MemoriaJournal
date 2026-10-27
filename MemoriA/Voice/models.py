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
