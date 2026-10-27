# Tests des modèles de Journal
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from Journal.models import Tag, JournalEntry


class TagModelTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')

    def test_str(self):
        tag = Tag.objects.create(user=self.user, name='Voyage')
        self.assertEqual(str(tag), 'Voyage')

    def test_couleur_par_defaut(self):
        tag = Tag.objects.create(user=self.user, name='Voyage')
        self.assertEqual(tag.color, '#2F6FD6')

    def test_nom_unique_par_utilisateur(self):
        Tag.objects.create(user=self.user, name='Voyage')
        with self.assertRaises(IntegrityError):
            Tag.objects.create(user=self.user, name='Voyage')

    def test_meme_nom_pour_deux_utilisateurs(self):
        other = User.objects.create_user('amine', password='motdepasse123')
        Tag.objects.create(user=self.user, name='Voyage')
        Tag.objects.create(user=other, name='Voyage')
        self.assertEqual(Tag.objects.filter(name='Voyage').count(), 2)


class JournalEntryModelTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')

    def test_str(self):
        entry = JournalEntry.objects.create(user=self.user, title='Journée à la plage', content='Il faisait beau.')
        self.assertEqual(str(entry), 'Journée à la plage')

    def test_date_par_defaut_aujourdhui(self):
        entry = JournalEntry.objects.create(user=self.user, title='Aujourd’hui', content='...')
        self.assertEqual(entry.entry_date, timezone.localdate())

    def test_clean_date_future_refusee(self):
        entry = JournalEntry(user=self.user, title='Demain', content='...',
                             entry_date=timezone.localdate() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            entry.clean()

    def test_clean_date_passee_acceptee(self):
        entry = JournalEntry(user=self.user, title='Hier', content='...',
                             entry_date=timezone.localdate() - timedelta(days=1))
        entry.clean()

    def test_ordre_plus_recent_en_premier(self):
        today = timezone.localdate()
        JournalEntry.objects.create(user=self.user, title='Ancienne', content='...', entry_date=today - timedelta(days=5))
        JournalEntry.objects.create(user=self.user, title='Récente', content='...', entry_date=today)
        self.assertEqual(JournalEntry.objects.first().title, 'Récente')

    def test_etiquettes(self):
        entry = JournalEntry.objects.create(user=self.user, title='Plage', content='...')
        tag = Tag.objects.create(user=self.user, name='Vacances')
        entry.tags.add(tag)
        self.assertIn(entry, tag.entries.all())
