from django import forms
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from Journal.models import JournalEntry

from .models import FaceTag, Person, Photo

IMAGE_MAX_SIZE = 10 * 1024 * 1024          # 10 Mo
IMAGE_FORMATS = {'JPEG', 'PNG', 'WEBP'}
IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp'


def validate_image(image):
    """Vérifie la taille puis le contenu réel du fichier avec Pillow (le nom du fichier ne suffit pas)."""
    if not image or not hasattr(image, 'content_type'):
        return image                      # pas de nouveau fichier envoyé
    if image.size > IMAGE_MAX_SIZE:
        raise forms.ValidationError("L'image ne doit pas dépasser 10 Mo.")
    try:
        with Image.open(image) as picture:
            image_format = picture.format
            picture.verify()              # détecte un fichier tronqué ou qui n'est pas une image
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise forms.ValidationError("Ce fichier n'est pas une image valide.")
    finally:
        image.seek(0)
    if image_format not in IMAGE_FORMATS:
        raise forms.ValidationError("Format non accepté : choisissez une image JPEG, PNG ou WebP.")
    return image


def validate_past_date(value):
    if value and value > timezone.localdate():
        raise forms.ValidationError("La date ne peut pas être dans le futur.")
    return value


class PhotoForm(forms.ModelForm):

    class Meta:
        model = Photo
        fields = ['entry', 'image', 'caption', 'taken_date', 'location']
        labels = {'entry': 'Entrée du journal', 'image': 'Photo', 'caption': 'Légende (texte alternatif)',
                  'taken_date': 'Date de la photo', 'location': 'Lieu'}
        widgets = {
            'image': forms.ClearableFileInput(attrs={'accept': IMAGE_ACCEPT}),
            'caption': forms.TextInput(attrs={'maxlength': 255}),
            'taken_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'location': forms.TextInput(attrs={'maxlength': 100}),
        }
        help_texts = {
            'image': 'JPEG, PNG ou WebP, 10 Mo maximum.',
            'caption': "Décrit la photo pour les personnes qui ne la voient pas. "
                       "Laissée vide, l'IA proposera une légende.",
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry'].queryset = JournalEntry.objects.filter(user=user)
        self.fields['entry'].empty_label = 'Choisissez une entrée'
        self.fields['taken_date'].widget.attrs['max'] = timezone.localdate().isoformat()

    def clean_image(self):
        return validate_image(self.cleaned_data.get('image'))

    def clean_taken_date(self):
        return validate_past_date(self.cleaned_data.get('taken_date'))


class PersonForm(forms.ModelForm):

    class Meta:
        model = Person
        fields = ['name', 'relation', 'birth_date', 'cover_photo', 'notes']
        labels = {'name': 'Nom', 'relation': 'Relation', 'birth_date': 'Date de naissance',
                  'cover_photo': 'Photo de profil', 'notes': 'Notes'}
        widgets = {
            'name': forms.TextInput(attrs={'maxlength': 100}),
            'birth_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'cover_photo': forms.ClearableFileInput(attrs={'accept': IMAGE_ACCEPT}),
            'notes': forms.Textarea(attrs={'rows': 3, 'maxlength': 1000}),
        }
        help_texts = {'cover_photo': 'JPEG, PNG ou WebP, 10 Mo maximum.'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['birth_date'].widget.attrs['max'] = timezone.localdate().isoformat()

    def clean_name(self):
        # unique_together (user, name) : l'utilisateur n'est pas dans le formulaire, on vérifie ici
        name = self.cleaned_data['name'].strip()
        others = Person.objects.filter(user=self.user, name__iexact=name).exclude(pk=self.instance.pk)
        if others.exists():
            raise forms.ValidationError("Vous avez déjà un proche avec ce nom.")
        return name

    def clean_cover_photo(self):
        return validate_image(self.cleaned_data.get('cover_photo'))

    def clean_birth_date(self):
        return validate_past_date(self.cleaned_data.get('birth_date'))


class FaceTagForm(forms.ModelForm):
    # Créer un nouveau proche sans quitter le formulaire (le visage devient sa photo de profil)
    new_person_name = forms.CharField(label='Nom de la nouvelle personne', required=False, max_length=100,
                                      widget=forms.TextInput(attrs={'maxlength': 100, 'placeholder': 'Ex. Sarah'}))
    new_person_relation = forms.ChoiceField(label='Relation', choices=Person.RELATION_CHOICES, initial='Ami',
                                            required=False)

    class Meta:
        model = FaceTag
        fields = ['person', 'x', 'y', 'width', 'height', 'is_confirmed']
        labels = {'person': 'Personne', 'x': 'Position horizontale (pixels)', 'y': 'Position verticale (pixels)',
                  'width': 'Largeur (pixels)', 'height': 'Hauteur (pixels)', 'is_confirmed': 'Identification confirmée'}
        help_texts = {'person': 'Choisissez un proche, ou ajoutez une nouvelle personne juste en dessous. '
                                'Laissez « Inconnue » si vous ne savez pas encore qui c\'est.'}

    def __init__(self, *args, user=None, photo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.photo = photo or self.instance.photo
        self.order_fields(['person', 'new_person_name', 'new_person_relation'])
        self.fields['person'].queryset = Person.objects.filter(user=user).order_by('name')
        self.fields['person'].empty_label = 'Inconnue'
        self.image_width, self.image_height = self.photo.image.width, self.photo.image.height
        for name, limit in (('x', self.image_width), ('width', self.image_width),
                            ('y', self.image_height), ('height', self.image_height)):
            self.fields[name].widget.attrs.update({'min': 0, 'max': limit})
        self.fields['width'].widget.attrs['min'] = 1
        self.fields['height'].widget.attrs['min'] = 1

    def clean(self):
        cleaned = super().clean()
        x, y = cleaned.get('x'), cleaned.get('y')
        width, height = cleaned.get('width'), cleaned.get('height')
        if None in (x, y, width, height):
            return cleaned
        if width < 1 or height < 1:
            raise forms.ValidationError("Le cadre doit faire au moins 1 pixel de côté.")
        if x + width > self.image_width or y + height > self.image_height:
            raise forms.ValidationError(f"Le cadre dépasse de la photo ({self.image_width} x {self.image_height} pixels).")
        return cleaned

    def clean_new_person_name(self):
        name = ' '.join(self.cleaned_data.get('new_person_name', '').split())
        if not name:
            return ''
        if self.data.get(self.add_prefix('person')):
            raise forms.ValidationError("Choisissez un proche existant OU écrivez un nouveau nom, pas les deux.")
        if len(name) < 2:
            raise forms.ValidationError("Le nom doit contenir au moins 2 caractères.")
        # unique_together (user, name) : message clair au lieu d'une erreur de la base
        if Person.objects.filter(user=self.user, name__iexact=name).exists():
            raise forms.ValidationError("Ce proche existe déjà : choisissez-le dans la liste « Personne ».")
        return name

    def save(self, commit=True):
        name = self.cleaned_data.get('new_person_name')
        if name and commit:
            person = Person.objects.create(user=self.user, name=name,
                                           relation=self.cleaned_data.get('new_person_relation') or 'Ami')
            self.save_face_as_cover(person)
            self.instance.person = person
            self.instance.is_confirmed = True        # l'utilisateur vient de dire qui c'est
            self.created_person = person
        return super().save(commit=commit)

    def save_face_as_cover(self, person):
        """Le visage découpé dans la photo devient la photo de profil du nouveau proche."""
        import io

        from django.core.files.base import ContentFile
        d = self.cleaned_data
        try:
            with self.photo.image.open('rb') as file:
                face = Image.open(file).convert('RGB').crop((d['x'], d['y'], d['x'] + d['width'], d['y'] + d['height']))
            buffer = io.BytesIO()
            face.save(buffer, format='JPEG', quality=90)
            person.cover_photo.save(f'visage_{person.pk}.jpg', ContentFile(buffer.getvalue()), save=True)
        except (OSError, KeyError):
            pass                                     # la photo de profil est un bonus : le proche est créé quand même
