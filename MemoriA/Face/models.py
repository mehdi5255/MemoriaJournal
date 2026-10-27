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
