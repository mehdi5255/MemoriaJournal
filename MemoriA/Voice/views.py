import calendar
from collections import Counter
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from .forms import MoodRecordForm, VoiceNoteForm, VoiceNoteUpdateForm
from .models import MoodRecord, VoiceNote

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

    def get_success_url(self):
        return reverse('voice:note_detail', args=[self.object.pk])


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
        })
        return context


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
