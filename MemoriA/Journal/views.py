from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.db.models import Count, Q
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from Memory.models import Story

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


class DeleteMessageMixin:
    success_message = ''

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


# ---------- JournalEntry ----------

class EntryListView(EntryOwnerMixin, ListView):
    """Liste des entrées avec filtres : texte, période, étiquette, favoris."""
    template_name = 'Journal/entry_list.html'
    context_object_name = 'entries'
    paginate_by = 10

    def get_queryset(self):
        queryset = super().get_queryset()
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
        params = self.request.GET.copy()
        params.pop('page', None)
        context['filters'] = params.urlencode()                      # garde les filtres dans la pagination
        context['has_filters'] = any(params.get(k) for k in ('q', 'debut', 'fin', 'etiquette', 'favori'))
        context['tags'] = Tag.objects.filter(user=self.request.user).order_by('name')
        context['current'] = {k: self.request.GET.get(k, '') for k in ('q', 'debut', 'fin', 'etiquette', 'favori')}
        context['total'] = JournalEntry.objects.filter(user=self.request.user).count()
        return context


class EntryDetailView(EntryOwnerMixin, DetailView):
    """Une entrée avec tout ce qui y est rattaché : notes vocales, photos, humeurs, récit."""
    template_name = 'Journal/entry_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entry = self.object
        context['voice_notes'] = entry.voice_notes.order_by('creation_date')
        context['photos'] = entry.photos.order_by('creation_date')
        context['moods'] = entry.mood_records.order_by('-record_date')
        context['story'] = Story.objects.filter(entry=entry).first()
        return context


class EntryCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = JournalEntry
    form_class = JournalEntryForm
    template_name = 'Journal/entry_form.html'
    success_message = "L'entrée « %(title)s » a été enregistrée."

    def form_valid(self, form):
        # L'utilisateur est toujours celui qui est connecté (jamais choisi dans le formulaire)
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('journal:entry_detail', args=[self.object.pk])


class EntryUpdateView(EntryOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = JournalEntryForm
    template_name = 'Journal/entry_form.html'
    success_message = "L'entrée « %(title)s » a été modifiée."

    def get_success_url(self):
        return reverse('journal:entry_detail', args=[self.object.pk])


class EntryDeleteView(EntryOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Journal/confirm_delete.html'
    success_url = reverse_lazy('journal:entry_list')
    success_message = "L'entrée a été supprimée."


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
