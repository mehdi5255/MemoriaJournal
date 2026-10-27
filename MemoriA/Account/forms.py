from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Profile

AVATAR_MAX_SIZE = 2 * 1024 * 1024          # 2 Mo
AVATAR_TYPES = ['image/jpeg', 'image/png', 'image/webp']


class SignupForm(UserCreationForm):
    email = forms.EmailField(label='Adresse e-mail', max_length=254)

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return email


class ProfileForm(forms.ModelForm):
    # Champs du User modifiables depuis la page profil
    first_name = forms.CharField(label='Prénom', max_length=150, required=False)
    last_name = forms.CharField(label='Nom', max_length=150, required=False)
    email = forms.EmailField(label='Adresse e-mail', max_length=254)

    class Meta:
        model = Profile
        fields = ['avatar', 'bio', 'birth_date', 'language']
        labels = {'avatar': 'Photo de profil', 'bio': 'Présentation',
                  'birth_date': 'Date de naissance', 'language': 'Langue'}
        widgets = {
            'avatar': forms.ClearableFileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
            'bio': forms.Textarea(attrs={'rows': 4, 'maxlength': 1000}),
            'birth_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        help_texts = {'avatar': 'JPEG, PNG ou WebP, 2 Mo maximum.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields['first_name'].initial = user.first_name
        self.fields['last_name'].initial = user.last_name
        self.fields['email'].initial = user.email
        self.fields['birth_date'].widget.attrs['max'] = timezone.localdate().isoformat()
        # Ordre d'affichage
        self.order_fields(['first_name', 'last_name', 'email', 'avatar', 'bio', 'birth_date', 'language'])

    def clean_avatar(self):
        avatar = self.cleaned_data.get('avatar')
        # Seulement si un nouveau fichier est envoyé
        if avatar and hasattr(avatar, 'content_type'):
            if avatar.content_type not in AVATAR_TYPES:
                raise forms.ValidationError("Format non accepté : choisissez une image JPEG, PNG ou WebP.")
            if avatar.size > AVATAR_MAX_SIZE:
                raise forms.ValidationError("L'image ne doit pas dépasser 2 Mo.")
        return avatar

    def clean_birth_date(self):
        birth_date = self.cleaned_data.get('birth_date')
        if birth_date and birth_date > timezone.localdate():
            raise forms.ValidationError("La date de naissance ne peut pas être dans le futur.")
        return birth_date

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.user.pk).exists():
            raise forms.ValidationError("Cette adresse e-mail est déjà utilisée.")
        return email

    def save(self, commit=True):
        profile = super().save(commit=commit)
        user = profile.user
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
        return profile
