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

    def main_image(self):
        """Image principale du récit, sinon la première image (utilise prefetch_related si présent)."""
        images = list(self.images.all())
        for image in images:
            if image.is_main:
                return image
        return images[0] if images else None

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

    def set_as_main(self):
        """Une seule image principale par récit : les autres perdent ce statut."""
        GeneratedImage.objects.filter(story=self.story, is_main=True).exclude(pk=self.pk).update(is_main=False)
        if not self.is_main:
            self.is_main = True
            self.save(update_fields=['is_main'])

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
