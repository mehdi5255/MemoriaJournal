from django import forms
from django.utils import timezone

from Journal.models import JournalEntry

from .models import DailyPrompt, Reminder

DAY_CHOICES = [(str(i), name.capitalize()) for i, name in enumerate(Reminder.DAY_NAMES)]


class ReminderForm(forms.ModelForm):
    # Les jours sont choisis avec des cases à cocher puis stockés « 0,1,2 » dans le champ days
    week_days = forms.MultipleChoiceField(label='Jours', choices=DAY_CHOICES, widget=forms.CheckboxSelectMultiple,
                                          help_text='Cochez au moins un jour.')

    class Meta:
        model = Reminder
        fields = ['reminder_time', 'channel', 'message', 'is_active']
        labels = {'reminder_time': 'Heure du rappel', 'channel': 'Canal', 'message': 'Message personnel',
                  'is_active': 'Rappel actif'}
        widgets = {
            'reminder_time': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'message': forms.Textarea(attrs={'rows': 3, 'maxlength': 500,
                                             'placeholder': 'Ex. Prends cinq minutes pour raconter ta journée.'}),
        }
        help_texts = {'channel': 'Email : un message est envoyé. Application : la question apparaît sur le tableau de bord.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['week_days'].initial = [str(d) for d in self.instance.day_numbers()] if self.instance.pk \
            else [str(d) for d in range(7)]
        self.order_fields(['reminder_time', 'week_days', 'channel', 'message', 'is_active'])

    def save(self, commit=True):
        self.instance.days = ','.join(sorted(self.cleaned_data['week_days']))
        return super().save(commit=commit)


class DailyPromptForm(forms.ModelForm):

    class Meta:
        model = DailyPrompt
        fields = ['reminder', 'prompt_date', 'question', 'status']
        labels = {'reminder': 'Rappel', 'prompt_date': 'Date', 'question': 'Question', 'status': 'Statut'}
        widgets = {
            'prompt_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'question': forms.Textarea(attrs={'rows': 3, 'maxlength': 500}),
        }
        help_texts = {'question': "Remplie automatiquement par l'IA chaque jour, vous pouvez aussi l'écrire."}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['reminder'].queryset = Reminder.objects.filter(user=user)
        self.fields['reminder'].empty_label = 'Choisissez un rappel'
        self.fields['prompt_date'].widget.attrs['max'] = timezone.localdate().isoformat()

    def clean_prompt_date(self):
        prompt_date = self.cleaned_data.get('prompt_date')
        if prompt_date and prompt_date > timezone.localdate():
            raise forms.ValidationError("La date ne peut pas être dans le futur.")
        return prompt_date

    def clean_question(self):
        question = self.cleaned_data['question'].strip()
        if len(question) < 5:
            raise forms.ValidationError("La question doit contenir au moins 5 caractères.")
        return question

    def validate_unique(self):
        # unique_together (reminder, prompt_date) avec un message clair en français
        reminder, day = self.cleaned_data.get('reminder'), self.cleaned_data.get('prompt_date')
        if reminder and day and DailyPrompt.objects.filter(reminder=reminder, prompt_date=day) \
                .exclude(pk=self.instance.pk).exists():
            self.add_error('prompt_date', "Ce rappel a déjà une question pour cette date.")


class AnswerForm(forms.ModelForm):
    """Réponse à la question du jour : une nouvelle entrée du journal, pré-remplie."""

    class Meta:
        model = JournalEntry
        fields = ['title', 'content', 'entry_date', 'location']
        labels = {'title': 'Titre', 'content': 'Ma réponse', 'entry_date': 'Date', 'location': 'Lieu'}
        widgets = {
            'title': forms.TextInput(attrs={'maxlength': 150}),
            'content': forms.Textarea(attrs={'rows': 8}),
            'entry_date': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'location': forms.TextInput(attrs={'maxlength': 100}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['entry_date'].widget.attrs['max'] = timezone.localdate().isoformat()

    def clean_content(self):
        content = self.cleaned_data['content'].strip()
        if len(content) < 3:
            raise forms.ValidationError("Écrivez quelques mots pour répondre.")
        return content
