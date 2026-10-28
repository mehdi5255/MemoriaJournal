from datetime import timedelta

from celery.result import AsyncResult
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.auth.models import Group, User
from django.contrib.auth.views import PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Avg, Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, TemplateView, UpdateView

from Journal.models import JournalEntry
from MemoriA.celery import app as celery_app
from Reminder.models import DailyPrompt
from Voice.models import MoodRecord
from Voice.services import mood_alert

from .forms import ProfileForm, SignupForm
from .models import ActivityLog, Profile
from .services import AIServiceError

# Couleur de la pastille d'humeur (pastels de la charte, toujours accompagnés du texte)
MOOD_CLASSES = {'Joie': 'mood-joie', 'Tristesse': 'mood-tristesse', 'Colere': 'mood-colere',
                'Peur': 'mood-peur', 'Surprise': 'mood-surprise', 'Neutre': 'mood-neutre'}


class HomeView(LoginRequiredMixin, TemplateView):
    """Tableau de bord : 7 derniers jours, dernières entrées, question du jour."""
    template_name = 'home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()
        start = today - timedelta(days=6)

        # Dernière humeur de chaque jour
        moods = {}
        for mood in MoodRecord.objects.filter(user=user, record_date__gte=start).order_by('creation_date'):
            moods[mood.record_date] = mood
        entry_days = set(JournalEntry.objects.filter(user=user, entry_date__gte=start)
                         .values_list('entry_date', flat=True))

        week = []
        for i in range(7):
            day = start + timedelta(days=i)
            mood = moods.get(day)
            week.append({
                'date': day,
                'mood': mood,
                'mood_class': MOOD_CLASSES.get(mood.emotion, '') if mood else 'mood-vide',
                'has_entry': day in entry_days,
                'is_today': day == today,
            })

        context['week'] = week
        context['latest_entries'] = JournalEntry.objects.filter(user=user)[:5]
        context['daily_prompt'] = DailyPrompt.objects.filter(user=user, prompt_date=today).first()
        context['alert'] = mood_alert(user)          # 3 jours négatifs de suite (module Voice)
        return context


class SignupView(CreateView):
    form_class = SignupForm
    template_name = 'registration/signup.html'
    success_url = reverse_lazy('account:home')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('account:home')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        # Le profil est créé par le signal ; on ajoute le rôle Utilisateur
        group, _ = Group.objects.get_or_create(name='Utilisateur')
        self.object.groups.add(group)
        login(self.request, self.object)
        messages.success(self.request, f"Bienvenue {self.object.username} ! Votre compte a été créé.")
        return response


class ProfileMixin(LoginRequiredMixin):
    """Le profil affiché est toujours celui de l'utilisateur connecté."""
    model = Profile

    def get_object(self, queryset=None):
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        return profile


class ProfileDetailView(ProfileMixin, DetailView):
    template_name = 'Account/profile_detail.html'
    context_object_name = 'profile'


class ProfileUpdateView(ProfileMixin, SuccessMessageMixin, UpdateView):
    form_class = ProfileForm
    template_name = 'Account/profile_form.html'
    success_url = reverse_lazy('account:profile')
    success_message = "Votre profil a été mis à jour."


class MemoriaPasswordChangeView(SuccessMessageMixin, PasswordChangeView):
    template_name = 'registration/password_change.html'
    success_url = reverse_lazy('account:profile')
    success_message = "Votre mot de passe a été modifié."


class StatisticsView(PermissionRequiredMixin, TemplateView):
    """Statistiques réservées au groupe Administrateur."""
    template_name = 'Account/statistics.html'
    permission_required = 'Account.view_activitylog'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logs = ActivityLog.objects.all()
        total_calls = logs.count()
        failures = logs.filter(status='Echec').count()

        context['user_count'] = User.objects.count()
        context['entry_count'] = JournalEntry.objects.count()
        context['total_calls'] = total_calls
        context['failure_rate'] = round(100 * failures / total_calls, 1) if total_calls else 0
        # Temps de réponse par service IA
        context['services'] = (logs.exclude(ai_service='')
                               .values('ai_service')
                               .annotate(calls=Count('id'),
                                         failures=Count('id', filter=Q(status='Echec')),
                                         avg_ms=Avg('duration_ms'),
                                         max_ms=Max('duration_ms'))
                               .order_by('ai_service'))
        return context


class TaskStatusView(LoginRequiredMixin, View):
    """Vue JSON interrogée par ai-status.js : PENDING, SUCCESS ou FAILURE."""

    def get(self, request, task_id):
        result = AsyncResult(task_id, app=celery_app)
        if result.state == 'SUCCESS':
            data = {'status': 'SUCCESS', 'result': result.result if isinstance(result.result, dict) else {}}
        elif result.state in ('FAILURE', 'REVOKED'):
            data = {'status': 'FAILURE'}
            # Message clair seulement pour les erreurs IA prévues (jamais les détails techniques)
            if isinstance(result.result, AIServiceError):
                data['message'] = str(result.result)
        else:
            # PENDING, STARTED, RETRY : la tâche n'est pas terminée
            data = {'status': 'PENDING'}
        return JsonResponse(data)
