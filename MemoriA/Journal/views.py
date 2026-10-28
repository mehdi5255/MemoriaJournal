from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from Account.services import AIServiceError, launch_task
from Memory.models import Story
from Voice.services import attach_dictations

from . import services, tasks
from .forms import JournalEntryForm, TagForm
from .models import JournalEntry, Tag


def parse_date(value):
    """Date du filtre (AAAA-MM-JJ) ou None si elle est vide ou invalide."""
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# ---------- Filtrage par utilisateur ----------

class EntryOwnerMixin(LoginRequiredMixin):
    model = JournalEntry
    context_object_name = 'entry'

    def get_queryset(self):
        return JournalEntry.objects.filter(user=self.request.user).prefetch_related('tags')


class TagOwnerMixin(LoginRequiredMixin):
    model = Tag
    context_object_name = 'tag'

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user)


class UserFormKwargsMixin:
    """Passe l'utilisateur connecté au formulaire (étiquettes proposées, nom unique)."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class EmbeddingMixin:
    """Après l'enregistrement d'une entrée, l'embedding est recalculé par Celery (la page ne l'attend pas)."""

    def form_valid(self, form):
        response = super().form_valid(form)
        # Les dictées du formulaire deviennent des notes vocales de l'entrée (avec leur transcription)
        notes = attach_dictations(self.object, self.request.user, form.cleaned_data.get('dictations', ''))
        if notes:
            messages.info(self.request, f"{len(notes)} dictée{'s' if len(notes) > 1 else ''} gardée"
                                        f"{'s' if len(notes) > 1 else ''} comme note vocale.")
        self.task_id = launch_task(self.request, tasks.compute_embedding_task, self.object.pk, self.request.user.pk)
        return redirect(self.get_success_url()) if self.task_id else response

    def get_success_url(self):
        url = reverse('journal:entry_detail', args=[self.object.pk])
        task_id = getattr(self, 'task_id', None)
        return f"{url}?task={task_id}" if task_id else url


class DeleteMessageMixin:
    success_message = ''

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


# ---------- JournalEntry ----------

class EntryListView(EntryOwnerMixin, ListView):
    """Liste des entrées avec filtres : texte (mots exacts ou par le sens), période, étiquette, favoris."""
    template_name = 'Journal/entry_list.html'
    context_object_name = 'entries'
    paginate_by = 10

    def semantic_mode(self):
        return self.request.GET.get('mode') == 'sens' and bool(self.request.GET.get('q', '').strip())

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.semantic_mode():
            return queryset.none()              # les résultats arrivent par la tâche Celery
        params = self.request.GET
        text = params.get('q', '').strip()
        if text:
            queryset = queryset.filter(Q(title__icontains=text) | Q(content__icontains=text) |
                                       Q(location__icontains=text))
        start, end = parse_date(params.get('debut')), parse_date(params.get('fin'))
        if start:
            queryset = queryset.filter(entry_date__gte=start)
        if end:
            queryset = queryset.filter(entry_date__lte=end)
        if params.get('etiquette', '').isdigit():
            queryset = queryset.filter(tags__id=params['etiquette'])
        if params.get('favori') == '1':
            queryset = queryset.filter(is_favorite=True)
        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.semantic_mode():
            # Recherche par le sens : tâche lancée ici (ou relancée par le bouton Réessayer)
            query = self.request.GET['q'].strip()[:200]
            context['semantic'] = True
            context['task_id'] = self.request.GET.get('task') or \
                launch_task(self.request, tasks.semantic_search_task, self.request.user.pk, query)
            context['retry_data'] = {'q': query}
        params = self.request.GET.copy()
        params.pop('page', None)
        context['filters'] = params.urlencode()                      # garde les filtres dans la pagination
        context['has_filters'] = any(params.get(k) for k in ('q', 'debut', 'fin', 'etiquette', 'favori'))
        context['tags'] = Tag.objects.filter(user=self.request.user).order_by('name')
        context['current'] = {k: self.request.GET.get(k, '')
                              for k in ('q', 'debut', 'fin', 'etiquette', 'favori', 'mode')}
        context['total'] = JournalEntry.objects.filter(user=self.request.user).count()
        return context


class EntryDetailView(EntryOwnerMixin, DetailView):
    """Une entrée avec tout ce qui y est rattaché : notes vocales, photos, humeurs, récit, souvenirs proches."""
    template_name = 'Journal/entry_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entry = self.object
        context['voice_notes'] = entry.voice_notes.order_by('creation_date')
        context['photos'] = entry.photos.order_by('creation_date')
        context['moods'] = entry.mood_records.order_by('-record_date')
        context['story'] = Story.objects.filter(entry=entry).first()
        context['similar'] = services.similar_entries(entry)         # embeddings déjà enregistrés : rapide
        context['task_id'] = self.request.GET.get('task', '')
        return context


class EntryCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, EmbeddingMixin, CreateView):
    model = JournalEntry
    form_class = JournalEntryForm
    template_name = 'Journal/entry_form.html'
    success_message = "L'entrée « %(title)s » a été enregistrée."

    def form_valid(self, form):
        # L'utilisateur est toujours celui qui est connecté (jamais choisi dans le formulaire)
        form.instance.user = self.request.user
        return super().form_valid(form)


class EntryUpdateView(EntryOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, EmbeddingMixin, UpdateView):
    form_class = JournalEntryForm
    template_name = 'Journal/entry_form.html'
    success_message = "L'entrée « %(title)s » a été modifiée."


class EntryDeleteView(EntryOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Journal/confirm_delete.html'
    success_url = reverse_lazy('journal:entry_list')
    success_message = "L'entrée a été supprimée."


# ---------- IA : recherche par le sens, souvenirs similaires, étiquettes ----------

class SemanticSearchView(LoginRequiredMixin, View):
    """Bouton Réessayer de la recherche par le sens : relance la tâche."""
    http_method_names = ['post']

    def post(self, request):
        query = request.POST.get('q', '').strip()[:200]
        params = {'mode': 'sens', 'q': query}
        task_id = launch_task(request, tasks.semantic_search_task, request.user.pk, query) if query else None
        if task_id:
            params['task'] = task_id
        return redirect(f"{reverse('journal:entry_list')}?{urlencode(params)}")


class EntryAnalyzeView(LoginRequiredMixin, View):
    """Relance le calcul de l'embedding d'une entrée (bouton « Chercher des souvenirs similaires »)."""
    http_method_names = ['post']

    def post(self, request, pk):
        entry = get_object_or_404(JournalEntry, pk=pk, user=request.user)
        task_id = launch_task(request, tasks.compute_embedding_task, entry.pk, request.user.pk)
        url = reverse('journal:entry_detail', args=[entry.pk])
        return redirect(f"{url}?task={task_id}" if task_id else url)


class TagSuggestView(LoginRequiredMixin, View):
    """Appelée en JavaScript depuis le formulaire d'entrée : lance la suggestion et renvoie l'adresse de suivi."""
    http_method_names = ['post']

    def post(self, request):
        text = f"{request.POST.get('title', '').strip()}. {request.POST.get('content', '').strip()}"[:3000]
        if len(text) < 12:
            return JsonResponse({'error': "Écrivez d'abord le titre et quelques phrases."}, status=400)
        if not request.user.tags.exists():
            return JsonResponse({'error': "Créez d'abord quelques étiquettes : l'IA choisit parmi elles."}, status=400)
        try:
            task_id = tasks.suggest_tags_task.delay(request.user.pk, text).id
        except AIServiceError as error:
            return JsonResponse({'error': str(error)}, status=503)
        except Exception:
            return JsonResponse({'error': "Le service de suggestion est indisponible pour le moment "
                                          "(Redis ou le worker Celery ne répond pas). Réessayez dans un instant."},
                                status=503)
        return JsonResponse({'task_id': task_id, 'status_url': reverse('account:task_status', args=[task_id])})


# ---------- Tag ----------

class TagListView(TagOwnerMixin, ListView):
    template_name = 'Journal/tag_list.html'
    context_object_name = 'tags'

    def get_queryset(self):
        return super().get_queryset().annotate(nb_entries=Count('entries')).order_by('name')


class TagDetailView(TagOwnerMixin, DetailView):
    template_name = 'Journal/tag_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['entries'] = self.object.entries.filter(user=self.request.user)[:20]
        return context


class TagCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = Tag
    form_class = TagForm
    template_name = 'Journal/tag_form.html'
    success_message = "L'étiquette « %(name)s » a été créée."
    success_url = reverse_lazy('journal:tag_list')

    def form_valid(self, form):
        form.instance.user = self.request.user
        return super().form_valid(form)


class TagUpdateView(TagOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = TagForm
    template_name = 'Journal/tag_form.html'
    success_message = "L'étiquette « %(name)s » a été modifiée."
    success_url = reverse_lazy('journal:tag_list')


class TagDeleteView(TagOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Journal/confirm_delete.html'
    success_url = reverse_lazy('journal:tag_list')
    success_message = "L'étiquette a été supprimée (les entrées sont conservées)."
