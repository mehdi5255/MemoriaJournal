import io

from django import forms
from django.utils import timezone

from Journal.models import JournalEntry

from .models import MoodRecord, VoiceNote

AUDIO_MAX_SIZE = 20 * 1024 * 1024          # 20 Mo
AUDIO_MAX_SECONDS = 600                    # 10 minutes
AUDIO_ACCEPT = 'audio/webm,audio/ogg,audio/mpeg,audio/wav,audio/x-wav,audio/mp4,audio/x-m4a,.webm,.ogg,.mp3,.wav,.m4a'

# Début du fichier (« signature ») de chaque format accepté
AUDIO_SIGNATURES = {
    'webm': [b'\x1a\x45\xdf\xa3'],
    'ogg': [b'OggS'],
    'mp3': [b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'],
    'wav': [b'RIFF'],
    'm4a': [],                             # signature « ftyp » à l'octet 4, vérifiée à part
}


def detect_audio_format(head):
    """Reconnaît le format à partir des premiers octets (le nom du fichier ne suffit pas)."""
    if len(head) >= 12 and head[4:8] == b'ftyp':
        return 'm4a'
    for fmt, signatures in AUDIO_SIGNATURES.items():
        if any(head.startswith(sig) for sig in signatures):
            if fmt == 'wav' and head[8:12] != b'WAVE':
                continue
            return fmt
    return None


def audio_duration(data):
    """Durée en secondes lue dans le fichier (mutagen), ou None si le format ne la donne pas (ex. WebM)."""
    import mutagen
    try:
        info = mutagen.File(io.BytesIO(data))
    except Exception:
        return None
    if info is None or not getattr(info, 'info', None) or not info.info.length:
        return None
    return int(round(info.info.length))


class VoiceNoteForm(forms.ModelForm):
    # Durée mesurée par le navigateur : utilisée seulement si le serveur ne peut pas la lire (WebM)
    client_duration = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    class Meta:
        model = VoiceNote
        fields = ['entry', 'title', 'audio']
        labels = {'entry': 'Entrée du journal', 'title': 'Titre de la note', 'audio': 'Fichier audio'}
        widgets = {
            'title': forms.TextInput(attrs={'maxlength': 100, 'placeholder': 'Ex. Ma soirée'}),
            'audio': forms.ClearableFileInput(attrs={'accept': AUDIO_ACCEPT}),
        }
        help_texts = {'audio': 'Enregistrez-vous ci-dessus ou importez un fichier WebM, OGG, MP3, WAV ou M4A '
                               '(10 minutes et 20 Mo maximum).'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry'].queryset = JournalEntry.objects.filter(user=user)
        self.fields['entry'].empty_label = 'Choisissez une entrée'

    def clean_audio(self):
        audio = self.cleaned_data.get('audio')
        if not audio or not hasattr(audio, 'content_type'):
            return audio
        if audio.size > AUDIO_MAX_SIZE:
            raise forms.ValidationError("Le fichier audio ne doit pas dépasser 20 Mo.")
        data = audio.read()
        audio.seek(0)
        fmt = detect_audio_format(data[:16])
        if not fmt:
            raise forms.ValidationError("Format non reconnu : choisissez un fichier WebM, OGG, MP3, WAV ou M4A.")
        self.audio_format = fmt
        self.audio_seconds = audio_duration(data)
        return audio

    def clean(self):
        cleaned = super().clean()
        if self.errors.get('audio') or not hasattr(self, 'audio_format'):
            return cleaned
        seconds = self.audio_seconds
        if seconds is None:
            seconds = cleaned.get('client_duration')
        if seconds is None:
            self.add_error('audio', "Impossible de lire la durée de ce fichier : réessayez l'enregistrement.")
        elif seconds > AUDIO_MAX_SECONDS:
            self.add_error('audio', "Une note vocale ne doit pas dépasser 10 minutes.")
        else:
            self.instance.duration = seconds
        return cleaned


class VoiceNoteUpdateForm(forms.ModelForm):
    """Après l'envoi, on peut corriger le titre et la transcription (pas le fichier)."""

    class Meta:
        model = VoiceNote
        fields = ['title', 'transcription', 'language']
        labels = {'title': 'Titre de la note', 'transcription': 'Transcription', 'language': 'Langue'}
        widgets = {
            'title': forms.TextInput(attrs={'maxlength': 100}),
            'transcription': forms.Textarea(attrs={'rows': 8, 'maxlength': 20000}),
            'language': forms.TextInput(attrs={'maxlength': 10, 'placeholder': 'fr'}),
        }
        help_texts = {'transcription': "Remplie automatiquement par l'IA, vous pouvez la corriger."}


class DetectMoodForm(forms.Form):
    """Choix de l'entrée dont l'IA analyse le texte pour détecter l'humeur."""
    entry = forms.ModelChoiceField(label='Entrée à analyser', queryset=JournalEntry.objects.none(),
                                   empty_label='Choisissez une entrée')

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry'].queryset = JournalEntry.objects.filter(user=user)


class MoodRecordForm(forms.ModelForm):

    class Meta:
        model = MoodRecord
        fields = ['emotion', 'score', 'record_date', 'entry', 'note']
        labels = {'emotion': 'Émotion', 'score': 'Intensité', 'record_date': 'Date',
                  'entry': 'Entrée liée (facultatif)', 'note': 'Note'}
        widgets = {
            'score': forms.NumberInput(attrs={'min': 0, 'max': 1, 'step': 0.05}),
            'record_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'note': forms.Textarea(attrs={'rows': 3, 'maxlength': 1000}),
        }
        help_texts = {'score': 'De 0 (très faible) à 1 (très forte).'}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry'].queryset = JournalEntry.objects.filter(user=user)
        self.fields['entry'].empty_label = 'Aucune'
        self.fields['record_date'].widget.attrs['max'] = timezone.localdate().isoformat()
        if not self.instance.pk:
            self.fields['score'].initial = 0.5

    def clean_record_date(self):
        record_date = self.cleaned_data.get('record_date')
        if record_date and record_date > timezone.localdate():
            raise forms.ValidationError("La date ne peut pas être dans le futur.")
        return record_date
