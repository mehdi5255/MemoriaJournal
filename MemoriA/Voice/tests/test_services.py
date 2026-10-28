# Tests des services IA de Voice : tous les appels externes sont mockés
import io
import shutil
import tempfile
import wave
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from Account.models import ActivityLog
from Account.services import AIServiceError
from Journal.models import JournalEntry
from Voice import services, tasks
from Voice.models import MoodRecord, VoiceNote

MEDIA_TEST = tempfile.mkdtemp()
PASSWORD = 'MotDePasse!2026'


def wav_bytes(seconds=1, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b'\x00\x00' * rate * seconds)
    return buffer.getvalue()


class PureFunctionsTest(SimpleTestCase):

    def test_detect_language(self):
        self.assertEqual(services.detect_language("Je suis allé à la plage avec mes amis et c'est très bien"), 'fr')
        self.assertEqual(services.detect_language("I went to the beach with my friends and it was great"), 'en')
        self.assertEqual(services.detect_language('ذهبت إلى البحر'), 'ar')

    def test_to_emotion(self):
        self.assertEqual(services.to_emotion([{'label': 'joy', 'score': 0.92}, {'label': 'sadness', 'score': 0.05}]),
                         ('Joie', 0.92))
        self.assertEqual(services.to_emotion([{'label': 'anger', 'score': 0.7}]), ('Colere', 0.7))

    def test_to_emotion_neutre_si_le_modele_hesite(self):
        scores = [{'label': 'joy', 'score': 0.35}, {'label': 'sadness', 'score': 0.33}, {'label': 'fear', 'score': 0.32}]
        self.assertEqual(services.to_emotion(scores), ('Neutre', 0.35))

    def test_decode_audio_wav(self):
        audio = services.decode_audio(wav_bytes(1, rate=8000))       # rééchantillonné en 16 kHz
        self.assertAlmostEqual(len(audio) / 16000, 1, delta=0.05)

    def test_decode_audio_illisible(self):
        with self.assertRaises(Exception):
            services.decode_audio(b'pas du son')


class VoiceServicesTestBase(TestCase):

    def setUp(self):
        services.get_hf_client.cache_clear()
        services.get_local_whisper.cache_clear()
        services.get_local_emotion.cache_clear()
        self.user = User.objects.create_user('seif', password=PASSWORD)
        self.today = timezone.localdate()


@override_settings(HF_TOKEN='test-token')
class TranscriptionTest(VoiceServicesTestBase):

    @patch('Voice.services.get_hf_client')
    def test_hugging_face(self, mock_hf):
        mock_hf.return_value.automatic_speech_recognition.return_value = SimpleNamespace(text=' Bonjour à tous. ')
        self.assertEqual(services.transcribe(b'audio', self.user), ('Bonjour à tous.', 'fr'))
        self.assertEqual(ActivityLog.objects.get().ai_service, 'Whisper')

    @patch('Voice.services.get_hf_client')
    def test_echec_hugging_face(self, mock_hf):
        mock_hf.return_value.automatic_speech_recognition.side_effect = RuntimeError('402 Payment Required')
        with self.assertRaisesMessage(AIServiceError, 'Le service de transcription ne répond pas'):
            services.transcribe(b'audio', self.user)
        self.assertEqual(ActivityLog.objects.get().status, 'Echec')

    @override_settings(VOICE_BACKEND='auto')
    @patch('Voice.services.get_local_whisper')
    @patch('Voice.services.get_hf_client')
    def test_auto_bascule_sur_whisper_local(self, mock_hf, mock_local):
        mock_hf.return_value.automatic_speech_recognition.side_effect = RuntimeError('402')
        mock_local.return_value.return_value = {'text': 'Texte transcrit en local et très clair.'}
        text, _ = services.transcribe(wav_bytes(), self.user)
        self.assertEqual(text, 'Texte transcrit en local et très clair.')
        sent = mock_local.return_value.call_args.args[0]
        self.assertEqual(sent['sampling_rate'], 16000)                 # son décodé pour Whisper
        self.assertEqual(set(ActivityLog.objects.values_list('ai_service', 'status')),
                         {('Whisper', 'Echec'), ('Whisper (local)', 'Succes')})

    @override_settings(VOICE_BACKEND='local')
    @patch('Voice.services.get_local_whisper')
    def test_aucune_parole(self, mock_local):
        mock_local.return_value.return_value = {'text': '   '}
        with self.assertRaisesMessage(AIServiceError, "Aucune parole"):
            services.transcribe(wav_bytes(), self.user)

    @override_settings(HF_TOKEN='')
    def test_jeton_absent(self):
        with self.assertRaisesMessage(AIServiceError, 'HF_TOKEN'):
            services.transcribe(b'audio', self.user)


@override_settings(HF_TOKEN='test-token')
class EmotionTest(VoiceServicesTestBase):

    @patch('Voice.services.get_hf_client')
    def test_hugging_face(self, mock_hf):
        mock_hf.return_value.text_classification.return_value = [
            SimpleNamespace(label='sadness', score=0.81), SimpleNamespace(label='joy', score=0.1)]
        self.assertEqual(services.detect_emotion('Journée triste.', self.user), ('Tristesse', 0.81))
        self.assertEqual(ActivityLog.objects.get().ai_service, 'Émotions')

    @override_settings(VOICE_BACKEND='local')
    @patch('Voice.services.get_local_emotion')
    def test_local(self, mock_local):
        mock_local.return_value.return_value = [[{'label': 'fear', 'score': 0.66}, {'label': 'joy', 'score': 0.2}]]
        self.assertEqual(services.detect_emotion("J'ai peur de l'examen.", self.user), ('Peur', 0.66))

    @patch('Voice.services.get_hf_client')
    def test_echec(self, mock_hf):
        mock_hf.return_value.text_classification.side_effect = RuntimeError('503')
        with self.assertRaisesMessage(AIServiceError, "détection d'émotion ne répond pas"):
            services.detect_emotion('Bonjour', self.user)

    def test_texte_vide(self):
        with self.assertRaisesMessage(AIServiceError, 'Le texte est vide'):
            services.detect_emotion('   ', self.user)


class NegativeStreakTest(VoiceServicesTestBase):

    def add(self, days_ago, *emotions):
        for emotion in emotions:
            MoodRecord.objects.create(user=self.user, emotion=emotion, record_date=self.today - timedelta(days=days_ago))

    def test_trois_jours_negatifs_declenchent_l_alerte(self):
        self.add(0, 'Tristesse')
        self.add(1, 'Colere')
        self.add(2, 'Peur')
        self.add(3, 'Joie')
        self.assertEqual(services.negative_streak(self.user), 3)
        self.assertIn('3 jours de suite', services.mood_alert(self.user)['message'])

    def test_deux_jours_pas_d_alerte(self):
        self.add(0, 'Tristesse')
        self.add(1, 'Tristesse')
        self.assertEqual(services.negative_streak(self.user), 2)
        self.assertIsNone(services.mood_alert(self.user))

    def test_un_jour_sans_humeur_coupe_la_serie(self):
        self.add(0, 'Tristesse')
        self.add(1, 'Tristesse')
        self.add(3, 'Tristesse')
        self.assertEqual(services.negative_streak(self.user), 2)

    def test_aujourd_hui_pas_encore_note(self):
        self.add(1, 'Peur')
        self.add(2, 'Peur')
        self.add(3, 'Colere')
        self.assertEqual(services.negative_streak(self.user), 3)

    def test_emotion_dominante_du_jour(self):
        # Le même jour : 2 joies et 1 tristesse -> jour positif
        self.add(0, 'Tristesse', 'Joie', 'Joie')
        self.add(1, 'Tristesse')
        self.add(2, 'Tristesse')
        self.assertEqual(services.negative_streak(self.user), 0)

    def test_humeurs_d_un_autre_utilisateur_ignorees(self):
        other = User.objects.create_user('amine', password=PASSWORD)
        for days_ago in range(3):
            MoodRecord.objects.create(user=other, emotion='Peur', record_date=self.today - timedelta(days=days_ago))
        self.assertEqual(services.negative_streak(self.user), 0)


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class VoiceTasksTest(VoiceServicesTestBase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    def setUp(self):
        super().setUp()
        self.entry = JournalEntry.objects.create(user=self.user, title='Soirée', content='Une belle soirée.')
        self.note = VoiceNote(entry=self.entry, title='Note', duration=1)
        self.note.audio.save('note.wav', ContentFile(wav_bytes()), save=True)

    @patch('Voice.services.detect_emotion', return_value=('Joie', 0.88))
    @patch('Voice.services.transcribe', return_value=("Quelle belle soirée avec les amis.", 'fr'))
    def test_transcription_puis_humeur(self, mock_transcribe, mock_emotion):
        result = tasks.transcribe_voice_note_task.delay(self.note.pk, self.user.pk).get()
        self.note.refresh_from_db()
        self.assertEqual((self.note.status, self.note.language), ('Termine', 'fr'))
        mood = self.note.mood_record
        self.assertEqual((mood.source, mood.emotion, mood.score, mood.entry), ('Voix', 'Joie', 0.88, self.entry))
        self.assertIn('Quelle belle soirée', result['html'])
        self.assertIn('Joie', result['html'])

    @patch('Voice.services.detect_emotion', return_value=('Tristesse', 0.7))
    @patch('Voice.services.transcribe', return_value=('Texte', 'fr'))
    def test_retranscrire_met_a_jour_la_meme_humeur(self, mock_transcribe, mock_emotion):
        tasks.transcribe_voice_note_task.delay(self.note.pk, self.user.pk).get()
        mock_emotion.return_value = ('Joie', 0.9)
        tasks.transcribe_voice_note_task.delay(self.note.pk, self.user.pk).get()
        self.assertEqual(MoodRecord.objects.filter(voice_note=self.note).count(), 1)
        self.assertEqual(MoodRecord.objects.get(voice_note=self.note).emotion, 'Joie')

    @patch('Voice.services.detect_emotion', side_effect=AIServiceError('Émotion indisponible'))
    @patch('Voice.services.transcribe', return_value=('Texte gardé', 'fr'))
    def test_echec_de_l_emotion_garde_la_transcription(self, mock_transcribe, mock_emotion):
        tasks.transcribe_voice_note_task.delay(self.note.pk, self.user.pk).get()
        self.note.refresh_from_db()
        self.assertEqual((self.note.status, self.note.transcription), ('Termine', 'Texte gardé'))
        self.assertFalse(MoodRecord.objects.filter(voice_note=self.note).exists())

    @patch('Voice.services.transcribe', side_effect=AIServiceError('Whisper indisponible'))
    def test_echec_de_la_transcription(self, mock_transcribe):
        with self.assertRaisesMessage(AIServiceError, 'Whisper indisponible'):
            tasks.transcribe_voice_note_task.delay(self.note.pk, self.user.pk)
        self.note.refresh_from_db()
        self.assertEqual(self.note.status, 'Echec')

    def test_fichier_audio_introuvable(self):
        self.note.audio.delete(save=False)
        with self.assertRaisesMessage(AIServiceError, 'introuvable'):
            tasks.transcribe_voice_note_task.delay(self.note.pk, self.user.pk)

    def test_note_d_un_autre_utilisateur(self):
        other = User.objects.create_user('amine', password=PASSWORD)
        with self.assertRaises(VoiceNote.DoesNotExist):
            tasks.transcribe_voice_note_task.delay(self.note.pk, other.pk)

    @patch('Voice.services.detect_emotion', return_value=('Colere', 0.75))
    def test_humeur_depuis_le_texte_d_une_entree(self, mock_emotion):
        result = tasks.detect_entry_mood_task.delay(self.entry.pk, self.user.pk).get()
        mood = MoodRecord.objects.get(entry=self.entry, source='Texte')
        self.assertEqual((mood.emotion, mood.record_date), ('Colere', self.entry.entry_date))
        self.assertIn('Soirée. Une belle soirée.', mock_emotion.call_args.args[0])
        self.assertIn('Colère', result['html'])

    @patch('Voice.services.detect_emotion', side_effect=[('Peur', 0.6), ('Joie', 0.8)])
    def test_reanalyser_une_entree_ne_duplique_pas(self, mock_emotion):
        tasks.detect_entry_mood_task.delay(self.entry.pk, self.user.pk).get()
        tasks.detect_entry_mood_task.delay(self.entry.pk, self.user.pk).get()
        self.assertEqual(MoodRecord.objects.filter(entry=self.entry, source='Texte').count(), 1)


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class VoiceAIViewsTest(VoiceServicesTestBase):

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('amine', password=PASSWORD)
        self.entry = JournalEntry.objects.create(user=self.user, title='Soirée', content='Belle soirée.')
        self.other_entry = JournalEntry.objects.create(user=self.other, title='Secret', content='...')
        self.note = VoiceNote(entry=self.entry, title='Note', duration=1)
        self.note.audio.save('note.wav', ContentFile(wav_bytes()), save=True)
        self.client.login(username='seif', password=PASSWORD)

    @patch('Voice.services.detect_emotion', return_value=('Joie', 0.9))
    @patch('Voice.services.transcribe', return_value=('Bonjour.', 'fr'))
    def test_transcrire_a_nouveau(self, mock_transcribe, mock_emotion):
        response = self.client.post(reverse('voice:note_transcribe', args=[self.note.pk]))
        self.assertIn('?task=', response.url)
        page = self.client.get(response.url)
        self.assertContains(page, 'Transcription en cours')
        self.assertContains(page, 'data-result-target="#transcription"')

    def test_transcrire_la_note_d_un_autre_404(self):
        other_note = VoiceNote.objects.create(entry=self.other_entry, title='X', audio='voice_notes/x.wav')
        self.assertEqual(self.client.post(reverse('voice:note_transcribe', args=[other_note.pk])).status_code, 404)

    def test_transcrire_refuse_en_get(self):
        self.assertEqual(self.client.get(reverse('voice:note_transcribe', args=[self.note.pk])).status_code, 405)

    @patch('Voice.tasks.transcribe_voice_note_task.delay', side_effect=ConnectionError('Redis absent'))
    def test_redis_indisponible(self, mock_delay):
        response = self.client.post(reverse('voice:note_transcribe', args=[self.note.pk]), follow=True)
        self.assertContains(response, 'Redis ou le worker Celery ne répond pas')

    @patch('Voice.services.detect_emotion', return_value=('Surprise', 0.7))
    def test_detecter_l_humeur_d_une_entree(self, mock_emotion):
        response = self.client.post(reverse('voice:mood_detect'), {'entry': self.entry.pk})
        self.assertIn('?task=', response.url)
        self.assertTrue(MoodRecord.objects.filter(entry=self.entry, source='Texte').exists())
        page = self.client.get(response.url)
        self.assertContains(page, "Analyse de l'humeur de l'entrée")
        self.assertContains(page, f'name="entry" value="{self.entry.pk}"')        # bouton Réessayer

    def test_detecter_sur_l_entree_d_un_autre_refuse(self):
        response = self.client.post(reverse('voice:mood_detect'), {'entry': self.other_entry.pk}, follow=True)
        self.assertContains(response, 'Choisissez une de vos entrées.')
        self.assertFalse(MoodRecord.objects.filter(entry=self.other_entry).exists())

    def test_alerte_sur_la_page_humeur_et_le_tableau_de_bord(self):
        for days_ago in range(3):
            MoodRecord.objects.create(user=self.user, emotion='Tristesse', record_date=self.today - timedelta(days=days_ago))
        for url in (reverse('voice:mood_list'), reverse('account:home')):
            response = self.client.get(url)
            self.assertContains(response, 'Prenez soin de vous')
            self.assertContains(response, 'role="alert"')

    def test_pas_d_alerte_sans_serie_negative(self):
        MoodRecord.objects.create(user=self.user, emotion='Joie', record_date=self.today)
        self.assertNotContains(self.client.get(reverse('account:home')), 'Prenez soin de vous')

    def test_boutons_ia_non_connecte(self):
        self.client.logout()
        for url in (reverse('voice:note_transcribe', args=[self.note.pk]), reverse('voice:mood_detect')):
            self.assertIn(reverse('account:login'), self.client.post(url).url)
