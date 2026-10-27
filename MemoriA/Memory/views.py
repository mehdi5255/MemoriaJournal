from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, TemplateView, UpdateView

from .forms import GeneratedImageForm, RetrospectiveForm, StoryForm
from .models import GeneratedImage, Retrospective, Story


# ---------- Filtrage par utilisateur (un utilisateur ne voit que ses données) ----------

class StoryOwnerMixin(LoginRequiredMixin):
    model = Story

    def get_queryset(self):
        return (Story.objects.filter(entry__user=self.request.user)
                .select_related('entry').prefetch_related('images'))


class ImageOwnerMixin(LoginRequiredMixin):
    model = GeneratedImage
    context_object_name = 'image'

    def get_queryset(self):
        return GeneratedImage.objects.filter(story__entry__user=self.request.user).select_related('story__entry')


class RetrospectiveOwnerMixin(LoginRequiredMixin):
    model = Retrospective
    context_object_name = 'retrospective'

    def get_queryset(self):
        return Retrospective.objects.filter(user=self.request.user)


class DeleteMessageMixin:
    """Message de confirmation après une suppression."""
    success_message = ''

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


# ---------- Page Souvenirs ----------

class SouvenirsView(LoginRequiredMixin, TemplateView):
    template_name = 'Memory/souvenirs.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        stories = (Story.objects.filter(entry__user=user)
                   .select_related('entry').prefetch_related('images')
                   .order_by('-entry__entry_date'))
        context['latest_stories'] = stories[:6]
        context['story_count'] = stories.count()
        context['image_count'] = GeneratedImage.objects.filter(story__entry__user=user).count()
        context['retrospectives'] = Retrospective.objects.filter(user=user).order_by('-start_date')[:4]
        context['retrospective_count'] = Retrospective.objects.filter(user=user).count()
        return context


# ---------- Story (récit) ----------

class StoryListView(StoryOwnerMixin, ListView):
    template_name = 'Memory/story_list.html'
    context_object_name = 'stories'
    paginate_by = 12

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-entry__entry_date')
        style = self.request.GET.get('style')
        if style in dict(Story.STYLE_CHOICES):
            queryset = queryset.filter(style=style)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['styles'] = Story.STYLE_CHOICES
        context['current_style'] = self.request.GET.get('style', '')
        return context


class StoryDetailView(StoryOwnerMixin, DetailView):
    template_name = 'Memory/story_detail.html'
    context_object_name = 'story'


class StoryCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Story
    form_class = StoryForm
    template_name = 'Memory/story_form.html'
    success_message = "Le récit a été créé."

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_initial(self):
        # Lien « Créer le récit » depuis une entrée : ?entry=<id>
        initial = super().get_initial()
        if self.request.GET.get('entry', '').isdigit():
            initial['entry'] = self.request.GET['entry']
        return initial

    def get_success_url(self):
        return reverse('memory:story_detail', args=[self.object.pk])


class StoryUpdateView(StoryOwnerMixin, SuccessMessageMixin, UpdateView):
    form_class = StoryForm
    template_name = 'Memory/story_form.html'
    context_object_name = 'story'
    success_message = "Le récit a été modifié."

    def get_success_url(self):
        return reverse('memory:story_detail', args=[self.object.pk])


class StoryDeleteView(StoryOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Memory/confirm_delete.html'
    context_object_name = 'story'
    success_url = reverse_lazy('memory:story_list')
    success_message = "Le récit a été supprimé."

    def form_valid(self, form):
        # Supprime aussi les fichiers des images du récit
        for image in self.object.images.all():
            image.image.delete(save=False)
        return super().form_valid(form)


# ---------- GeneratedImage (illustration) ----------

class ImageListView(ImageOwnerMixin, ListView):
    template_name = 'Memory/generatedimage_list.html'
    context_object_name = 'images'
    paginate_by = 12

    def get_queryset(self):
        return super().get_queryset().order_by('-creation_date')


class ImageDetailView(ImageOwnerMixin, DetailView):
    template_name = 'Memory/generatedimage_detail.html'


class ImageCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = GeneratedImage
    form_class = GeneratedImageForm
    template_name = 'Memory/generatedimage_form.html'
    success_message = "L'image a été ajoutée."

    def dispatch(self, request, *args, **kwargs):
        # Le récit doit appartenir à l'utilisateur, sinon 404
        if request.user.is_authenticated:
            self.story = get_object_or_404(Story, pk=kwargs['story_pk'], entry__user=request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['story'] = self.story
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['story'] = self.story
        return context

    def form_valid(self, form):
        form.instance.story = self.story
        # La première image d'un récit devient automatiquement l'image principale
        if not self.story.images.exists():
            form.instance.is_main = True
        response = super().form_valid(form)
        if self.object.is_main:
            self.object.set_as_main()
        return response

    def get_success_url(self):
        return reverse('memory:story_detail', args=[self.story.pk])


class ImageUpdateView(ImageOwnerMixin, SuccessMessageMixin, UpdateView):
    form_class = GeneratedImageForm
    template_name = 'Memory/generatedimage_form.html'
    success_message = "L'image a été modifiée."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['story'] = self.object.story
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.is_main:
            self.object.set_as_main()
        return response

    def get_success_url(self):
        return reverse('memory:image_detail', args=[self.object.pk])


class ImageDeleteView(ImageOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Memory/confirm_delete.html'
    success_message = "L'image a été supprimée."

    def form_valid(self, form):
        self.object.image.delete(save=False)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('memory:story_detail', args=[self.object.story_id])


class ImageSetMainView(ImageOwnerMixin, View):
    """Bouton « Choisir comme image principale » (POST uniquement)."""
    http_method_names = ['post']

    def post(self, request, pk):
        image = get_object_or_404(self.get_queryset(), pk=pk)
        image.set_as_main()
        messages.success(request, "L'image principale a été changée.")
        return redirect('memory:story_detail', pk=image.story_id)


# ---------- Retrospective (bilan) ----------

class RetrospectiveListView(RetrospectiveOwnerMixin, ListView):
    template_name = 'Memory/retrospective_list.html'
    context_object_name = 'retrospectives'
    paginate_by = 12

    def get_queryset(self):
        queryset = super().get_queryset().select_related('cover_image').order_by('-start_date')
        period = self.request.GET.get('period')
        if period in dict(Retrospective.TYPE_CHOICES):
            queryset = queryset.filter(period_type=period)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['periods'] = Retrospective.TYPE_CHOICES
        context['current_period'] = self.request.GET.get('period', '')
        return context


class RetrospectiveDetailView(RetrospectiveOwnerMixin, DetailView):
    template_name = 'Memory/retrospective_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['entries'] = self.object.entries.order_by('entry_date')
        return context


class RetrospectiveFormMixin:
    form_class = RetrospectiveForm
    template_name = 'Memory/retrospective_form.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        return reverse('memory:retrospective_detail', args=[self.object.pk])


class RetrospectiveCreateView(LoginRequiredMixin, RetrospectiveFormMixin, SuccessMessageMixin, CreateView):
    model = Retrospective
    success_message = "La rétrospective a été créée."

    def form_valid(self, form):
        # L'utilisateur est toujours celui qui est connecté (jamais choisi dans le formulaire)
        form.instance.user = self.request.user
        return super().form_valid(form)


class RetrospectiveUpdateView(RetrospectiveOwnerMixin, RetrospectiveFormMixin, SuccessMessageMixin, UpdateView):
    success_message = "La rétrospective a été modifiée."


class RetrospectiveDeleteView(RetrospectiveOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Memory/confirm_delete.html'
    success_url = reverse_lazy('memory:retrospective_list')
    success_message = "La rétrospective a été supprimée."
