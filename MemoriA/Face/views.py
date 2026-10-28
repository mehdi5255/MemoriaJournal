from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from celery.result import AsyncResult
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from Account.services import launch_task
from MemoriA.celery import app as celery_app

from . import tasks
from .forms import FaceTagForm, PersonForm, PhotoForm
from .models import FaceTag, Person, Photo


# ---------- Filtrage par utilisateur (un utilisateur ne voit que ses données) ----------

class PhotoOwnerMixin(LoginRequiredMixin):
    model = Photo
    context_object_name = 'photo'

    def get_queryset(self):
        return Photo.objects.filter(entry__user=self.request.user).select_related('entry')


class PersonOwnerMixin(LoginRequiredMixin):
    model = Person
    context_object_name = 'person'

    def get_queryset(self):
        return Person.objects.filter(user=self.request.user)


class FaceTagOwnerMixin(LoginRequiredMixin):
    model = FaceTag
    context_object_name = 'face'

    def get_queryset(self):
        return FaceTag.objects.filter(photo__entry__user=self.request.user).select_related('photo__entry', 'person')


class UserFormKwargsMixin:
    """Donne l'utilisateur au formulaire pour limiter les choix à ses propres données."""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs


class DeleteMessageMixin:
    success_message = ''

    def form_valid(self, form):
        messages.success(self.request, self.success_message)
        return super().form_valid(form)


def faces_with_boxes(photo):
    """Visages d'une photo avec la position de leur cadre (en %) pour l'affichage."""
    width, height = photo.image.width, photo.image.height
    return [{'tag': tag, 'style': tag.box_style(width, height)}
            for tag in photo.face_tags.select_related('person').order_by('x')]


# ---------- Photo ----------

class PhotoListView(PhotoOwnerMixin, ListView):
    template_name = 'Face/photo_list.html'
    context_object_name = 'photos'
    paginate_by = 12

    def get_queryset(self):
        queryset = super().get_queryset().annotate(faces=Count('face_tags')).order_by('-creation_date')
        if self.request.GET.get('visages') == 'non':
            queryset = queryset.filter(faces=0)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['without_faces'] = self.request.GET.get('visages') == 'non'
        return context


class PhotoDetailView(PhotoOwnerMixin, DetailView):
    template_name = 'Face/photo_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            context['faces'] = faces_with_boxes(self.object)
        except (OSError, ValueError):
            context['faces'] = []         # fichier image absent : la page s'affiche quand même
            context['image_missing'] = True
        context['task_id'] = self.request.GET.get('task', '')      # analyse IA en cours
        context['with_forms'] = True
        return context


class PhotoAnalyzeView(PhotoOwnerMixin, View):
    """Bouton « Analyser à nouveau » (et « Réessayer » après un échec)."""
    http_method_names = ['post']

    def post(self, request, pk):
        photo = get_object_or_404(self.get_queryset(), pk=pk)
        task_id = launch_task(request, tasks.analyze_photo_task, photo.pk, request.user.pk)
        url = reverse('face:photo_detail', args=[photo.pk])
        return redirect(f"{url}?task={task_id}" if task_id else url)


class PhotoCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = Photo
    form_class = PhotoForm
    template_name = 'Face/photo_form.html'
    success_message = "La photo a été ajoutée."

    def get_initial(self):
        # Lien « Ajouter une photo » depuis une entrée : ?entry=<id>
        initial = super().get_initial()
        if self.request.GET.get('entry', '').isdigit():
            initial['entry'] = self.request.GET['entry']
        return initial

    def form_valid(self, form):
        response = super().form_valid(form)
        # L'analyse (légende, objets, visages) démarre tout de suite, en arrière-plan
        self.task_id = launch_task(self.request, tasks.analyze_photo_task, self.object.pk, self.request.user.pk)
        return redirect(self.get_success_url()) if self.task_id else response

    def get_success_url(self):
        url = reverse('face:photo_detail', args=[self.object.pk])
        return f"{url}?task={self.task_id}" if getattr(self, 'task_id', None) else url


class PhotoUpdateView(PhotoOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = PhotoForm
    template_name = 'Face/photo_form.html'
    success_message = "La photo a été modifiée."

    def get_success_url(self):
        return reverse('face:photo_detail', args=[self.object.pk])


class PhotoDeleteView(PhotoOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Face/confirm_delete.html'
    success_url = reverse_lazy('face:photo_list')
    success_message = "La photo a été supprimée."

    def form_valid(self, form):
        self.object.image.delete(save=False)          # supprime aussi le fichier
        return super().form_valid(form)


# ---------- Person (page « Mes proches ») ----------

class PersonListView(PersonOwnerMixin, ListView):
    template_name = 'Face/person_list.html'
    context_object_name = 'persons'

    def get_queryset(self):
        queryset = (super().get_queryset()
                    .annotate(photo_count=Count('face_tags__photo', distinct=True))
                    .order_by('name'))
        relation = self.request.GET.get('relation')
        if relation in dict(Person.RELATION_CHOICES):
            queryset = queryset.filter(relation=relation)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['relations'] = Person.RELATION_CHOICES
        context['current_relation'] = self.request.GET.get('relation', '')
        context['unknown_faces'] = FaceTag.objects.filter(photo__entry__user=self.request.user,
                                                          person__isnull=True).count()
        return context


class PersonDetailView(PersonOwnerMixin, DetailView):
    """Album de la personne : toutes les photos où elle a été reconnue."""
    template_name = 'Face/person_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['photos'] = (Photo.objects.filter(entry__user=self.request.user, face_tags__person=self.object)
                             .select_related('entry').distinct().order_by('-entry__entry_date'))
        return context


class PersonCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    model = Person
    form_class = PersonForm
    template_name = 'Face/person_form.html'
    success_message = "%(name)s a été ajouté(e) à vos proches."

    def form_valid(self, form):
        # L'utilisateur est toujours celui qui est connecté (jamais choisi dans le formulaire)
        form.instance.user = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('face:person_detail', args=[self.object.pk])


class PersonUpdateView(PersonOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = PersonForm
    template_name = 'Face/person_form.html'
    success_message = "La fiche de %(name)s a été modifiée."

    def get_success_url(self):
        return reverse('face:person_detail', args=[self.object.pk])


class PersonDeleteView(PersonOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Face/confirm_delete.html'
    success_url = reverse_lazy('face:person_list')
    success_message = "La personne a été supprimée. Ses visages restent sur les photos, sans nom."

    def form_valid(self, form):
        if self.object.cover_photo:
            self.object.cover_photo.delete(save=False)
        return super().form_valid(form)


# ---------- FaceTag (visage sur une photo) ----------

class FaceTagListView(FaceTagOwnerMixin, ListView):
    """Tous les visages, avec un filtre « à identifier »."""
    template_name = 'Face/facetag_list.html'
    context_object_name = 'faces'
    paginate_by = 24

    def get_queryset(self):
        queryset = super().get_queryset().order_by('-creation_date')
        state = self.request.GET.get('etat')
        if state == 'inconnus':
            queryset = queryset.filter(person__isnull=True)
        elif state == 'a-confirmer':
            queryset = queryset.filter(person__isnull=False, is_confirmed=False)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['current_state'] = self.request.GET.get('etat', '')
        return context


class FaceTagDetailView(FaceTagOwnerMixin, DetailView):
    template_name = 'Face/facetag_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        photo = self.object.photo
        try:
            context['box_style'] = self.object.box_style(photo.image.width, photo.image.height)
        except (OSError, ValueError):
            context['box_style'] = ''
        return context


class FaceTagCreateView(LoginRequiredMixin, UserFormKwargsMixin, SuccessMessageMixin, CreateView):
    """Ajout manuel d'un visage sur une photo (la détection automatique arrive avec l'IA)."""
    model = FaceTag
    form_class = FaceTagForm
    template_name = 'Face/facetag_form.html'
    success_message = "Le visage a été ajouté."

    def dispatch(self, request, *args, **kwargs):
        # La photo doit appartenir à l'utilisateur, sinon 404
        if request.user.is_authenticated:
            self.photo = get_object_or_404(Photo, pk=kwargs['photo_pk'], entry__user=request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['photo'] = self.photo
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['photo'] = self.photo
        return context

    def form_valid(self, form):
        form.instance.photo = self.photo
        form.instance.confidence = 1.0      # cadre tracé par l'utilisateur : confiance totale
        response = super().form_valid(form)
        new_person_message(self.request, form)
        refresh_encodings(self.request, {self.object.person})
        return response

    def get_success_url(self):
        return reverse('face:photo_detail', args=[self.photo.pk])


class FaceTagUpdateView(FaceTagOwnerMixin, UserFormKwargsMixin, SuccessMessageMixin, UpdateView):
    form_class = FaceTagForm
    template_name = 'Face/facetag_form.html'
    success_message = "Le visage a été modifié."

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['photo'] = self.object.photo
        return context

    def form_valid(self, form):
        previous = FaceTag.objects.get(pk=self.object.pk).person
        response = super().form_valid(form)
        new_person_message(self.request, form)
        refresh_encodings(self.request, {previous, self.object.person})
        return response

    def get_success_url(self):
        return reverse('face:photo_detail', args=[self.object.photo_id])


def new_person_message(request, form):
    """Confirme la création du proche faite depuis le formulaire du visage."""
    person = getattr(form, 'created_person', None)
    if person:
        messages.success(request, f"« {person.name} » a été ajouté à vos proches, avec ce visage comme photo de profil.")


def refresh_encodings(request, persons):
    """Après une identification, l'empreinte des personnes concernées est recalculée en arrière-plan."""
    for person in persons:
        if person is not None:
            launch_task(request, tasks.update_person_encoding_task, person.pk, request.user.pk)


class FaceTagAnswerView(FaceTagOwnerMixin, View):
    """Réponse à « C'est Sarah ? » : oui confirme, non retire le nom proposé."""
    http_method_names = ['post']

    def post(self, request, pk):
        face = get_object_or_404(self.get_queryset(), pk=pk, person__isnull=False)
        person = face.person
        if request.POST.get('reponse') == 'oui':
            face.is_confirmed = True
            messages.success(request, f"C'est bien {person.name} : visage confirmé.")
        else:
            face.person = None
            face.is_confirmed = False
            messages.info(request, f"D'accord, ce n'est pas {person.name}.")
        face.save(update_fields=['person', 'is_confirmed'])
        refresh_encodings(request, {person})
        return redirect('face:photo_detail', pk=face.photo_id)


class FaceTagDeleteView(FaceTagOwnerMixin, DeleteMessageMixin, DeleteView):
    template_name = 'Face/confirm_delete.html'
    success_message = "Le visage a été supprimé."

    def get_success_url(self):
        return reverse('face:photo_detail', args=[self.object.photo_id])


# ---------- Regroupement des visages inconnus (DBSCAN) ----------

def crop_style(tag):
    """Style CSS qui montre seulement le visage (la photo en fond, agrandie et décalée sur le cadre)."""
    width, height = tag.photo.image.width, tag.photo.image.height
    position_x = 100 * tag.x / (width - tag.width) if width > tag.width else 0
    position_y = 100 * tag.y / (height - tag.height) if height > tag.height else 0
    return (f"background-image:url('{tag.photo.image.url}');"
            f"background-size:{100 * width / tag.width:.1f}% {100 * height / tag.height:.1f}%;"
            f"background-position:{position_x:.1f}% {position_y:.1f}%")


class FaceGroupsView(LoginRequiredMixin, View):
    """Page « Regrouper les visages inconnus » : lance DBSCAN puis propose de nommer chaque groupe."""

    def get(self, request):
        task_id = request.GET.get('task', '')
        groups = None
        if task_id:
            result = AsyncResult(task_id, app=celery_app)
            if result.state == 'SUCCESS' and isinstance(result.result, dict):
                groups = self.load_groups(request.user, result.result.get('groups', []))
        unknown = FaceTag.objects.filter(photo__entry__user=request.user, person__isnull=True).count()
        return render(request, 'Face/face_groups.html', {
            'task_id': task_id if groups is None else '', 'groups': groups, 'unknown_count': unknown,
            'persons': Person.objects.filter(user=request.user).order_by('name'),
        })

    def post(self, request):
        task_id = launch_task(request, tasks.group_unknown_faces_task, request.user.pk)
        url = reverse('face:face_groups')
        return redirect(f"{url}?task={task_id}" if task_id else url)

    @staticmethod
    def load_groups(user, groups):
        """Recharge les visages depuis la base : seulement ceux de l'utilisateur, encore sans nom."""
        loaded = []
        for ids in groups:
            faces = list(FaceTag.objects.filter(pk__in=ids, photo__entry__user=user, person__isnull=True)
                         .select_related('photo__entry'))
            if len(faces) >= 2:
                loaded.append([{'tag': face, 'style': crop_style(face)} for face in faces])
        return loaded


class FaceGroupNameView(LoginRequiredMixin, View):
    """« Nommer ce groupe » : tous les visages du groupe reçoivent la même personne (existante ou nouvelle)."""
    http_method_names = ['post']

    def post(self, request):
        faces = FaceTag.objects.filter(pk__in=request.POST.getlist('faces'), photo__entry__user=request.user,
                                       person__isnull=True)
        name = request.POST.get('nouveau_nom', '').strip()[:100]
        person_id = request.POST.get('personne', '')
        if name:
            person, _ = Person.objects.get_or_create(user=request.user, name=name)
        elif person_id.isdigit():
            person = get_object_or_404(Person, pk=person_id, user=request.user)
        else:
            messages.error(request, "Choisissez une personne ou écrivez un nouveau nom.")
            return redirect('face:face_groups')
        count = faces.update(person=person, is_confirmed=True)
        refresh_encodings(request, {person})
        messages.success(request, f"{count} visage(s) attribué(s) à {person.name}.")
        return redirect('face:person_detail', pk=person.pk)
