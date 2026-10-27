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
