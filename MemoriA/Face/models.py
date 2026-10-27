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

    def alt_text(self):
        """Texte alternatif : la légende (écrite ou proposée par l'IA), sinon une description simple."""
        if self.caption:
            return self.caption
        day = self.taken_date or self.entry.entry_date
        return f"Photo de l'entrée « {self.entry.title} » du {day:%d/%m/%Y}"

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

    def box_style(self, image_width, image_height):
        """Position du cadre sur la photo en pourcentages (le cadre suit la taille de l'image à l'écran)."""
        if not image_width or not image_height:
            return ''
        return (f"left:{100 * self.x / image_width:.2f}%;top:{100 * self.y / image_height:.2f}%;"
                f"width:{100 * self.width / image_width:.2f}%;height:{100 * self.height / image_height:.2f}%")

    def __str__(self):
        return f"Visage sur photo {self.photo_id}"
