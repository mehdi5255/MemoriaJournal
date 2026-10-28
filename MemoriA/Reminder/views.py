from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from Account.services import launch_task

from . import tasks
from .forms import AnswerForm, DailyPromptForm, ReminderForm
from .models import DailyPrompt, Reminder


# ---------- Filtrage par utilisateur ----------

class ReminderOwnerMixin(LoginRequiredMixin):
    model = Reminder
    context_object_name = 'reminder'

    def get_queryset(self):
        return Reminder.objects.filter(user=self.request.user)


class PromptOwnerMixin(LoginRequiredMixin):
    model = DailyPrompt
    context_object_name = 'prompt'

    def get_queryset(self):
        return DailyPrompt.objects.filter(user=self.request.user).select_related('reminder', 'answer_entry')


class DeleteMessageMixin:
    success_message = ''

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


# ---------- Reminder (page « Rappels ») ----------

class ReminderListView(ReminderOwnerMixin, ListView):
    """Page Rappels : mes rappels, la question du jour et les dernières questions."""
    template_name = 'Reminder/reminder_list.html'
    context_object_name = 'reminders'

    def get_queryset(self):
        return super().get_queryset().order_by('reminder_time')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        prompts = DailyPrompt.objects.filter(user=self.request.user)
        context['today_prompt'] = prompts.filter(prompt_date=today).first()
        context['recent_prompts'] = prompts.exclude(prompt_date=today).order_by('-prompt_date')[:5]
        context['answered'] = prompts.filter(status='Repondu').count()
        context['total_prompts'] = prompts.count()
        return context


class ReminderDetailView(ReminderOwnerMixin, DetailView):
    template_name = 'Reminder/reminder_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['prompts'] = self.object.prompts.order_by('-prompt_date')[:10]
        context['task_id'] = self.request.GET.get('task', '')           # tâche IA en cours
        context['task_type'] = self.request.GET.get('type', '')
        return context


class ReminderCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Reminder
    form_class = ReminderForm
    template_name = 'Reminder/reminder_form.html'
    success_message = "Le rappel a été créé."

    def form_valid(self, form):
        # L'utilisateur est toujours celui qui est connecté (jamais choisi dans le formulaire)
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('reminder:reminder_detail', args=[self.object.pk])


class ReminderUpdateView(ReminderOwnerMixin, SuccessMessageMixin, UpdateView):
    form_class = ReminderForm
    template_name = 'Reminder/reminder_form.html'
    success_message = "Le rappel a été modifié."

    def get_success_url(self):
        return reverse('reminder:reminder_detail', args=[self.object.pk])


class ReminderDeleteView(ReminderOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Reminder/confirm_delete.html'
    success_url = reverse_lazy('reminder:reminder_list')
    success_message = "Le rappel et ses questions ont été supprimés."


# ---------- DailyPrompt (question du jour) ----------

class PromptListView(PromptOwnerMixin, ListView):
    template_name = 'Reminder/dailyprompt_list.html'
    context_object_name = 'prompts'
    paginate_by = 15

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-prompt_date')
        status = self.request.GET.get('statut')
        if status in dict(DailyPrompt.STATUS_CHOICES):
            queryset = queryset.filter(status=status)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['statuses'] = DailyPrompt.STATUS_CHOICES
        context['current_status'] = self.request.GET.get('statut', '')
        return context


class PromptDetailView(PromptOwnerMixin, DetailView):
    template_name = 'Reminder/dailyprompt_detail.html'


class UserFormKwargsMixin:
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class PromptCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = DailyPrompt
    form_class = DailyPromptForm
    template_name = 'Reminder/dailyprompt_form.html'
    success_message = "La question a été ajoutée."

    def get_initial(self):
        # Lien « + Ajouter » depuis un rappel : ?reminder=<id>
        initial = super().get_initial()
        if self.request.GET.get('reminder', '').isdigit():
            initial['reminder'] = self.request.GET['reminder']
        return initial

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('reminder:prompt_detail', args=[self.object.pk])


class PromptUpdateView(PromptOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = DailyPromptForm
    template_name = 'Reminder/dailyprompt_form.html'
    success_message = "La question a été modifiée."

    def get_success_url(self):
        return reverse('reminder:prompt_detail', args=[self.object.pk])


class PromptDeleteView(PromptOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Reminder/confirm_delete.html'
    success_url = reverse_lazy('reminder:prompt_list')
    success_message = "La question a été supprimée. L'entrée écrite en réponse est conservée."


class PromptAnswerView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    """« Répondre » : ouvre une nouvelle entrée du journal, pré-remplie avec la question."""
    form_class = AnswerForm
    template_name = 'Reminder/answer_form.html'
    success_message = "Merci ! Votre réponse a été ajoutée à votre journal."

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            # La question doit appartenir à l'utilisateur, sinon 404
            self.prompt = get_object_or_404(DailyPrompt, pk=kwargs['pk'], user=request.user)
            if self.prompt.answer_entry_id:
                messages.info(request, "Vous avez déjà répondu à cette question.")
                return redirect('reminder:prompt_detail', pk=self.prompt.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {'title': f"Réponse : {self.prompt.question[:120]}", 'entry_date': self.prompt.prompt_date,
                'content': ''}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['prompt'] = self.prompt
        return context

    def form_valid(self, form):
        form.instance.user = self.request.user
        response = super().form_valid(form)
        self.prompt.answer_entry = self.object
        self.prompt.status = 'Repondu'
        self.prompt.save(update_fields=['answer_entry', 'status'])
        return response

    def get_success_url(self):
        return reverse('reminder:prompt_detail', args=[self.prompt.pk])


class PromptIgnoreView(PromptOwnerMixin, View):
    """« Pas aujourd'hui » : la question passe au statut Ignoré."""
    http_method_names = ['post']

    def post(self, request, pk):
        prompt = get_object_or_404(self.get_queryset(), pk=pk, answer_entry__isnull=True)
        prompt.status = 'Ignore'
        prompt.save(update_fields=['status'])
        messages.info(request, "D'accord, à demain !")
        # Retour sur la page d'où vient le clic (jamais une adresse libre venant du formulaire)
        return redirect('account:home' if request.POST.get('depuis') == 'accueil' else 'reminder:reminder_list')


# ---------- Fonctionnalités IA ----------

class ReminderSuggestView(ReminderOwnerMixin, View):
    """« Calculer l'heure conseillée » : KMeans sur les heures d'écriture (tâche Celery)."""
    http_method_names = ['post']

    def post(self, request, pk):
        reminder = get_object_or_404(self.get_queryset(), pk=pk)
        task_id = launch_task(request, tasks.suggest_time_task, reminder.pk, request.user.pk)
        url = reverse('reminder:reminder_detail', args=[reminder.pk])
        return redirect(f"{url}?task={task_id}&type=heure" if task_id else url)


class ReminderApplyView(ReminderOwnerMixin, View):
    """« Appliquer » : l'heure conseillée devient l'heure du rappel."""
    http_method_names = ['post']

    def post(self, request, pk):
        reminder = get_object_or_404(self.get_queryset(), pk=pk, suggested_time__isnull=False)
        reminder.reminder_time = reminder.suggested_time
        reminder.save(update_fields=['reminder_time'])
        messages.success(request, f"Le rappel sonnera désormais à {reminder.reminder_time:%H:%M}.")
        return redirect('reminder:reminder_detail', pk=reminder.pk)


class PromptGenerateView(ReminderOwnerMixin, View):
    """« Générer la question du jour » : NER sur les 3 dernières entrées (tâche Celery)."""
    http_method_names = ['post']

    def post(self, request, pk):
        reminder = get_object_or_404(self.get_queryset(), pk=pk)
        task_id = launch_task(request, tasks.generate_prompt_task, reminder.pk, request.user.pk)
        url = reverse('reminder:reminder_detail', args=[reminder.pk])
        return redirect(f"{url}?task={task_id}&type=question" if task_id else url)
