# Tests des modèles de Voice
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from Journal.models import JournalEntry
from Voice.models import VoiceNote, MoodRecord


class VoiceModelsTestBase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.entry = JournalEntry.objects.create(user=self.user, title='Ma journée', content='...')


class VoiceNoteModelTest(VoiceModelsTestBase):

    def test_str(self):
        note = VoiceNote.objects.create(entry=self.entry, title='Note du soir', audio='voice_notes/a.webm')
        self.assertEqual(str(note), 'Note du soir')

    def test_statut_par_defaut(self):
        note = VoiceNote.objects.create(entry=self.entry, title='Note', audio='voice_notes/a.webm')
        self.assertEqual(note.status, 'EnAttente')

    def test_clean_plus_de_10_minutes_refuse(self):
        note = VoiceNote(entry=self.entry, title='Trop longue', audio='voice_notes/a.webm', duration=601)
        with self.assertRaises(ValidationError):
            note.clean()

    def test_clean_10_minutes_accepte(self):
        note = VoiceNote(entry=self.entry, title='Limite', audio='voice_notes/a.webm', duration=600)
        note.clean()

    def test_suppression_entree_supprime_notes(self):
        VoiceNote.objects.create(entry=self.entry, title='Note', audio='voice_notes/a.webm')
        self.entry.delete()
        self.assertEqual(VoiceNote.objects.count(), 0)


class MoodRecordModelTest(VoiceModelsTestBase):

    def test_str(self):
        mood = MoodRecord.objects.create(user=self.user, emotion='Joie', score=0.9)
        self.assertEqual(str(mood), f"Joie - {timezone.localdate()}")

    def test_source_par_defaut(self):
        mood = MoodRecord.objects.create(user=self.user, emotion='Neutre')
        self.assertEqual(mood.source, 'Manuel')

    def test_clean_score_negatif_refuse(self):
        with self.assertRaises(ValidationError):
            MoodRecord(user=self.user, emotion='Joie', score=-0.1).clean()

    def test_clean_score_superieur_a_1_refuse(self):
        with self.assertRaises(ValidationError):
            MoodRecord(user=self.user, emotion='Joie', score=1.5).clean()

    def test_clean_score_limites_acceptees(self):
        MoodRecord(user=self.user, emotion='Joie', score=0).clean()
        MoodRecord(user=self.user, emotion='Joie', score=1).clean()

    def test_une_humeur_par_note_vocale(self):
        note = VoiceNote.objects.create(entry=self.entry, title='Note', audio='voice_notes/a.webm')
        MoodRecord.objects.create(user=self.user, voice_note=note, emotion='Joie')
        with self.assertRaises(IntegrityError):
            MoodRecord.objects.create(user=self.user, voice_note=note, emotion='Peur')

    def test_suppression_entree_garde_humeur(self):
        mood = MoodRecord.objects.create(user=self.user, entry=self.entry, emotion='Joie')
        self.entry.delete()
        mood.refresh_from_db()
        self.assertIsNone(mood.entry)
