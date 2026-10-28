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
