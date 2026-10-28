import calendar
from collections import Counter
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from Account.services import AIServiceError, launch_task

from . import tasks
from .forms import (AUDIO_MAX_SECONDS, AUDIO_MAX_SIZE, DetectMoodForm, MoodRecordForm, VoiceNoteForm,
                    VoiceNoteUpdateForm, audio_duration, detect_audio_format)
from .models import MoodRecord, VoiceNote
from .services import mood_alert, save_dictation

# Couleur de chaque émotion dans le graphique (même code que les pastilles du tableau de bord)
EMOTION_COLORS = {'Joie': '#F2C94C', 'Surprise': '#2E9E78', 'Neutre': '#8A8A96',
                  'Tristesse': '#2563C9', 'Peur': '#7B5EA7', 'Colere': '#C2412F'}


# ---------- Filtrage par utilisateur ----------

class VoiceNoteOwnerMixin(LoginRequiredMixin):
    model = VoiceNote
    context_object_name = 'note'

    def get_queryset(self):
        return VoiceNote.objects.filter(entry__user=self.request.user).select_related('entry')


class MoodOwnerMixin(LoginRequiredMixin):
    model = MoodRecord
    context_object_name = 'mood'

    def get_queryset(self):
        return MoodRecord.objects.filter(user=self.request.user).select_related('entry')


class UserFormKwargsMixin:
    """Donne l'utilisateur au formulaire pour limiter les choix à ses propres entrées."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class DeleteMessageMixin:
    success_message = ''

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


# ---------- VoiceNote (note vocale) ----------

class VoiceNoteListView(VoiceNoteOwnerMixin, ListView):
    template_name = 'Voice/voicenote_list.html'
    context_object_name = 'notes'
    paginate_by = 12

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-creation_date')
        status = self.request.GET.get('statut')
        if status in dict(VoiceNote.STATUS_CHOICES):
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['statuses'] = VoiceNote.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('statut', '')
        return context


class VoiceNoteDetailView(VoiceNoteOwnerMixin, DetailView):
    template_name = 'Voice/voicenote_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['task_id'] = self.request.GET.get('task', '')      # transcription en cours
        context['mood'] = getattr(self.object, 'mood_record', None)
        return context


class VoiceNoteCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = VoiceNote
    form_class = VoiceNoteForm
    template_name = 'Voice/voicenote_form.html'
    success_message = "La note vocale a été enregistrée."

    def get_initial(self):
        # Lien « Ajouter une note vocale » depuis une entrée : ?entry=<id>
        initial = super().get_initial()
        if self.request.GET.get('entry', '').isdigit():
            initial['entry'] = self.request.GET['entry']
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        # La transcription démarre tout de suite, en arrière-plan
        self.task_id = launch_task(self.request, tasks.transcribe_voice_note_task, self.object.pk, self.request.user.pk)
        return redirect(self.get_success_url()) if self.task_id else response

    def get_success_url(self):
        url = reverse('voice:note_detail', args=[self.object.pk])
        return f"{url}?task={self.task_id}" if getattr(self, 'task_id', None) else url


class VoiceNoteTranscribeView(VoiceNoteOwnerMixin, View):
    """Bouton « Transcrire à nouveau » (et « Réessayer » après un échec)."""
    http_method_names = ['post']

    def post(self, request, pk):
        note = get_object_or_404(self.get_queryset(), pk=pk)
        task_id = launch_task(request, tasks.transcribe_voice_note_task, note.pk, request.user.pk)
        url = reverse('voice:note_detail', args=[note.pk])
        return redirect(f"{url}?task={task_id}" if task_id else url)


class VoiceNoteUpdateView(VoiceNoteOwnerMixin, SuccessMessageMixin, UpdateView):
    form_class = VoiceNoteUpdateForm
    template_name = 'Voice/voicenote_form.html'
    success_message = "La note vocale a été modifiée."

    def get_success_url(self):
        return reverse('voice:note_detail', args=[self.object.pk])


class VoiceNoteDeleteView(VoiceNoteOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Voice/confirm_delete.html'
    success_url = reverse_lazy('voice:note_list')
    success_message = "La note vocale a été supprimée."

    def form_valid(self, form):
        self.object.audio.delete(save=False)          # supprime aussi le fichier
        return super().form_valid(form)


# ---------- MoodRecord (humeur) ----------

def parse_month(value):
    """« 2026-10 » -> date(2026, 10, 1) ; mois courant si la valeur est absente ou invalide."""
    try:
        year, month = (int(part) for part in value.split('-'))
        return date(year, month, 1)
    except (AttributeError, ValueError):
        today = timezone.localdate()
        return today.replace(day=1)


class MoodListView(MoodOwnerMixin, ListView):
    """Page Humeur : graphique du mois, tableau de données accessible et liste des humeurs."""
    template_name = 'Voice/moodrecord_list.html'
    context_object_name = 'moods'

    def get_queryset(self):
        self.month = parse_month(self.request.GET.get('mois'))
        last_day = calendar.monthrange(self.month.year, self.month.month)[1]
        self.month_end = self.month.replace(day=last_day)
        return (super().get_queryset()
                .filter(record_date__range=(self.month, self.month_end))
                .order_by('record_date', 'creation_date'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        moods = list(context['moods'])
        labels = dict(MoodRecord.EMOTION_CHOICES)

        # Une ligne par jour qui a au moins une humeur : émotion dominante et intensité moyenne
        by_day = {}
        for mood in moods:
            by_day.setdefault(mood.record_date, []).append(mood)
        days = []
        for day, records in sorted(by_day.items()):
            emotion = Counter(r.emotion for r in records).most_common(1)[0][0]
            days.append({'date': day, 'emotion': emotion, 'label': labels[emotion],
                         'score': round(sum(r.score for r in records) / len(records), 2),
                         'count': len(records), 'color': EMOTION_COLORS[emotion]})

        counts = Counter(m.emotion for m in moods)
        previous = (self.month.replace(day=1) - timedelta(days=1)).replace(day=1)
        following = (self.month_end + timedelta(days=1))
        today = timezone.localdate()

        context.update({
            'month': self.month,
            'previous_month': previous.strftime('%Y-%m'),
            'next_month': following.strftime('%Y-%m') if following <= today else None,
            'days': days,
            'distribution': [{'emotion': e, 'label': labels[e], 'count': counts[e], 'color': EMOTION_COLORS[e]}
                             for e, _ in MoodRecord.EMOTION_CHOICES if counts[e]],
            # Données du graphique (lues par Chart.js via json_script)
            'chart_data': {
                'labels': [d['date'].day for d in days],
                'scores': [d['score'] for d in days],
                'colors': [d['color'] for d in days],
                'emotions': [d['label'] for d in days],
            },
            'latest': sorted(moods, key=lambda m: (m.record_date, m.creation_date), reverse=True),
            'alert': mood_alert(self.request.user),
            'detect_form': DetectMoodForm(user=self.request.user),
            'task_id': self.request.GET.get('task', ''),
            'retry_data': {'entry': self.request.GET.get('entry', '')},
        })
        return context


class MoodDetectView(LoginRequiredMixin, View):
    """« Détecter mon humeur » : l'IA analyse le texte d'une entrée choisie."""
    http_method_names = ['post']

    def post(self, request):
        form = DetectMoodForm(request.POST, user=request.user)
        url = reverse('voice:mood_list')
        if not form.is_valid():
            messages.error(request, "Choisissez une de vos entrées.")
            return redirect(url)
        entry = form.cleaned_data['entry']
        task_id = launch_task(request, tasks.detect_entry_mood_task, entry.pk, request.user.pk)
        return redirect(f"{url}?task={task_id}&entry={entry.pk}" if task_id else url)


class MoodDetailView(MoodOwnerMixin, DetailView):
    template_name = 'Voice/moodrecord_detail.html'


class MoodCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = MoodRecord
    form_class = MoodRecordForm
    template_name = 'Voice/moodrecord_form.html'
    success_message = "L'humeur a été enregistrée."

    def get_initial(self):
        initial = super().get_initial()
        if self.request.GET.get('entry', '').isdigit():
            initial['entry'] = self.request.GET['entry']
        return initial

    def form_valid(self, form):
        # L'utilisateur et la source sont fixés par la vue, jamais par le formulaire
        form.instance.user = self.request.user
        form.instance.source = 'Manuel'
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('voice:mood_detail', args=[self.object.pk])


class MoodUpdateView(MoodOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = MoodRecordForm
    template_name = 'Voice/moodrecord_form.html'
    success_message = "L'humeur a été modifiée."

    def get_success_url(self):
        return reverse('voice:mood_detail', args=[self.object.pk])


class MoodDeleteView(MoodOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Voice/confirm_delete.html'
    success_url = reverse_lazy('voice:mood_list')
    success_message = "L'humeur a été supprimée."


# ---------- Dictée dans le formulaire d'une entrée (appelée en JavaScript) ----------

class DictationView(LoginRequiredMixin, View):
    """Reçoit l'enregistrement, vérifie format et durée, puis lance la transcription (Celery)."""
    http_method_names = ['post']

    def post(self, request):
        audio = request.FILES.get('audio')
        if not audio:
            return JsonResponse({'error': "Aucun enregistrement reçu."}, status=400)
        if audio.size > AUDIO_MAX_SIZE:
            return JsonResponse({'error': "L'enregistrement ne doit pas dépasser 20 Mo."}, status=400)
        data = audio.read()
        audio_format = detect_audio_format(data[:16])
        if not audio_format:
            return JsonResponse({'error': "Format non reconnu : WebM, OGG, MP3, WAV ou M4A."}, status=400)
        seconds = audio_duration(data)
        if seconds is None:
            # WebM : le serveur ne lit pas la durée, on prend celle mesurée par le navigateur
            seconds = int(request.POST['duration']) if request.POST.get('duration', '').isdigit() else None
        if seconds is None:
            return JsonResponse({'error': "Impossible de lire la durée de l'enregistrement."}, status=400)
        if seconds > AUDIO_MAX_SECONDS:
            return JsonResponse({'error': "Une note vocale ne doit pas dépasser 10 minutes."}, status=400)

        token = save_dictation(request.user, data, audio_format, seconds)
        try:
            task_id = tasks.transcribe_dictation_task.delay(request.user.pk, token).id
        except AIServiceError as error:
            return JsonResponse({'error': str(error), 'token': token}, status=503)
        except Exception:
            return JsonResponse({'error': "Le service de transcription est indisponible pour le moment "
                                          "(Redis ou le worker Celery ne répond pas). Réessayez dans un instant.",
                                 'token': token}, status=503)
        return JsonResponse({'task_id': task_id, 'token': token,
                             'status_url': reverse('account:task_status', args=[task_id])})
