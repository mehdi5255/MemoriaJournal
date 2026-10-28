import re

from django import forms
from django.utils import timezone

from .models import JournalEntry, Tag


class TagForm(forms.ModelForm):

    class Meta:
        model = Tag
        fields = ['name', 'color']
        labels = {'name': "Nom de l'étiquette", 'color': 'Couleur'}
        widgets = {
            'name': forms.TextInput(attrs={'maxlength': 50, 'placeholder': 'Ex. Voyage, Famille, Études'}),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color'}),
        }
        help_texts = {'name': "L'IA s'appuie sur ce nom pour proposer l'étiquette : choisissez un mot clair."}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if len(name) < 2:
            raise forms.ValidationError("Le nom doit contenir au moins 2 caractères.")
        # unique_together (user, name) : l'utilisateur n'est pas dans le formulaire, on vérifie ici
        if Tag.objects.filter(user=self.user, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Vous avez déjà une étiquette avec ce nom.")
        return name

    def clean_color(self):
        color = self.cleaned_data['color']
        if not re.fullmatch(r'#[0-9A-Fa-f]{6}', color):
            raise forms.ValidationError("La couleur doit être au format #RRGGBB.")
        return color.upper()


class JournalEntryForm(forms.ModelForm):
    # Jetons des dictées (rempli par dictation.js) : chaque dictée devient une note vocale de l'entrée
    dictations = forms.CharField(required=False, widget=forms.HiddenInput, max_length=1000)

    class Meta:
        model = JournalEntry
        fields = ['title', 'entry_date', 'content', 'location', 'tags', 'is_favorite']
        labels = {'title': 'Titre', 'entry_date': 'Date', 'content': 'Ma journée', 'location': 'Lieu',
                  'tags': 'Étiquettes', 'is_favorite': 'Ajouter aux favoris'}
        widgets = {
            'title': forms.TextInput(attrs={'maxlength': 150, 'placeholder': 'Ex. Une journée à la plage'}),
            'entry_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'content': forms.Textarea(attrs={'rows': 10, 'placeholder': "Racontez votre journée…"}),
            'location': forms.TextInput(attrs={'maxlength': 100, 'placeholder': 'Ex. Tunis'}),
            'tags': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Seulement les étiquettes de l'utilisateur connecté
        self.fields['tags'].queryset = Tag.objects.filter(user=user).order_by('name')
        self.fields['tags'].help_text = "Cochez une ou plusieurs étiquettes, ou demandez une suggestion à l'IA."
        self.fields['entry_date'].widget.attrs['max'] = timezone.localdate().isoformat()

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if len(title) < 2:
            raise forms.ValidationError("Le titre doit contenir au moins 2 caractères.")
        return title

    def clean_content(self):
        content = self.cleaned_data['content'].strip()
        if len(content) < 3:
            raise forms.ValidationError("Écrivez au moins quelques mots sur votre journée.")
        return content

    def clean_entry_date(self):
        entry_date = self.cleaned_data.get('entry_date')
        if entry_date and entry_date > timezone.localdate():
            raise forms.ValidationError("La date de l'entrée ne peut pas être dans le futur.")
        return entry_date
