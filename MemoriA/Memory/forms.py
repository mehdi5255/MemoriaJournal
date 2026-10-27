from datetime import timedelta

from django import forms
from django.utils import timezone

from Face.models import Photo
from Journal.models import JournalEntry

from .models import Story, GeneratedImage, Retrospective

IMAGE_MAX_SIZE = 10 * 1024 * 1024          # 10 Mo
IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp']
IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp'

# Durée maximale d'une période (en jours) selon son type
PERIOD_MAX_DAYS = {'Semaine': 7, 'Mois': 31}


class StoryForm(forms.ModelForm):

    class Meta:
        model = Story
        fields = ['entry', 'style', 'text']
        labels = {'entry': 'Entrée du journal', 'style': 'Style du récit', 'text': 'Texte du récit'}
        widgets = {'text': forms.Textarea(attrs={'rows': 10, 'maxlength': 10000})}
        help_texts = {'entry': 'Une entrée ne peut avoir qu\'un seul récit.'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # L'entrée d'un récit existant ne change pas
            del self.fields['entry']
        else:
            # Seulement mes entrées qui n'ont pas encore de récit
            self.fields['entry'].queryset = JournalEntry.objects.filter(user=user, story__isnull=True)
            self.fields['entry'].empty_label = 'Choisissez une entrée'

    def clean_text(self):
        text = self.cleaned_data['text'].strip()
        if len(text) < 20:
            raise forms.ValidationError("Le récit doit contenir au moins 20 caractères.")
        return text


class GenerateStoryForm(forms.Form):
    """Choix de l'entrée et du style pour faire écrire le récit par l'IA."""
    prefix = 'ia'           # évite des id en double avec le formulaire manuel sur la même page
    entry =forms.ModelChoiceField(label='Entrée du journal', queryset=JournalEntry.objects.none(),
                                   empty_label='Choisissez une entrée')
    style = forms.ChoiceField(label='Style du récit', choices=Story.STYLE_CHOICES)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry'].queryset = JournalEntry.objects.filter(user=user, story__isnull=True)


class GenerateImageForm(forms.Form):
    """Choix du style (et d'une photo à transformer) pour l'illustration IA."""
    style = forms.ChoiceField(label="Style de l'illustration", choices=GeneratedImage.STYLE_CHOICES)
    source_photo = forms.ModelChoiceField(label='Transformer une photo (facultatif)', required=False,
                                          queryset=Photo.objects.none(), empty_label='Non, créer une image')

    def __init__(self, *args, story=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['source_photo'].queryset = Photo.objects.filter(entry=story.entry)


class GeneratedImageForm(forms.ModelForm):

    class Meta:
        model = GeneratedImage
        fields = ['image', 'prompt', 'style', 'source_photo', 'is_main']
        labels = {'image': 'Image', 'prompt': 'Description de l\'image', 'style': 'Style',
                  'source_photo': 'Photo d\'origine (facultatif)', 'is_main': 'Image principale du récit'}
        widgets = {
            'image': forms.ClearableFileInput(attrs={'accept': IMAGE_ACCEPT}),
            'prompt': forms.Textarea(attrs={'rows': 3, 'maxlength': 1000}),
        }
        help_texts = {'image': 'JPEG, PNG ou WebP, 10 Mo maximum.',
                      'prompt': 'Ce que représente l\'image (sert aussi de texte alternatif).'}

    def __init__(self, *args, story=None, **kwargs):
        super().__init__(*args, **kwargs)
        story = story or self.instance.story
        # Seulement les photos de l'entrée de ce récit
        self.fields['source_photo'].queryset = Photo.objects.filter(entry=story.entry)
        self.fields['source_photo'].empty_label = 'Aucune'

    def clean_image(self):
        image = self.cleaned_data.get('image')
        # Seulement si un nouveau fichier est envoyé
        if image and hasattr(image, 'content_type'):
            if image.content_type not in IMAGE_TYPES:
                raise forms.ValidationError("Format non accepté : choisissez une image JPEG, PNG ou WebP.")
            if image.size > IMAGE_MAX_SIZE:
                raise forms.ValidationError("L'image ne doit pas dépasser 10 Mo.")
        return image


class RetrospectiveForm(forms.ModelForm):
    # Les mots-clés sont saisis séparés par des virgules puis stockés en liste
    keywords_text = forms.CharField(label='Mots-clés', required=False, max_length=500,
                                    help_text='Séparés par des virgules, par exemple : plage, famille, examens.')

    class Meta:
        model = Retrospective
        fields = ['title', 'period_type', 'start_date', 'end_date', 'summary', 'entries', 'cover_image']
        labels = {'title': 'Titre', 'period_type': 'Période', 'start_date': 'Date de début',
                  'end_date': 'Date de fin', 'summary': 'Résumé', 'entries': 'Entrées de la période',
                  'cover_image': 'Image de couverture'}
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'end_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'summary': forms.Textarea(attrs={'rows': 5, 'maxlength': 5000}),
            'entries': forms.CheckboxSelectMultiple,
        }
        help_texts = {'entries': 'Seules les entrées comprises entre les deux dates sont acceptées.'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields['entries'].queryset = JournalEntry.objects.filter(user=user).order_by('-entry_date')
        self.fields['cover_image'].queryset = GeneratedImage.objects.filter(story__entry__user=user)
        self.fields['cover_image'].empty_label = 'Aucune'
        today = timezone.localdate().isoformat()
        self.fields['start_date'].widget.attrs['max'] = today
        self.fields['end_date'].widget.attrs['max'] = today
        if self.instance.pk:
            self.fields['keywords_text'].initial = ', '.join(self.instance.keywords)
        self.order_fields(['title', 'period_type', 'start_date', 'end_date', 'summary',
                           'keywords_text', 'entries', 'cover_image'])

    def clean_keywords_text(self):
        text = self.cleaned_data.get('keywords_text', '')
        return [word.strip() for word in text.split(',') if word.strip()]

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        period = cleaned.get('period_type')
        if start and end:
            if end <= start:
                self.add_error('end_date', "La date de fin doit être après la date de début.")
            elif period in PERIOD_MAX_DAYS and (end - start) > timedelta(days=PERIOD_MAX_DAYS[period]):
                self.add_error('end_date', f"Une période « {period} » ne peut pas dépasser "
                                           f"{PERIOD_MAX_DAYS[period]} jours.")
            if start > timezone.localdate():
                self.add_error('start_date', "La période ne peut pas commencer dans le futur.")
            entries = cleaned.get('entries')
            if entries:
                outside = [e for e in entries if not (start <= e.entry_date <= end)]
                if outside:
                    self.add_error('entries', "Ces entrées sont en dehors de la période : "
                                              + ', '.join(e.title for e in outside) + '.')
        return cleaned

    def save(self, commit=True):
        self.instance.keywords = self.cleaned_data['keywords_text']
        return super().save(commit=commit)
