# Tests de la dictée dans le formulaire d'une entrée (Whisper toujours simulé)
import io
import shutil
import tempfile
import wave
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from Account.services import AIServiceError
from Journal.models import JournalEntry
from Voice import services
from Voice.models import VoiceNote

MEDIA = tempfile.mkdtemp()


def wav_bytes(seconds=1):
    """Un vrai petit fichier WAV silencieux (mutagen sait lire sa durée)."""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(8000)
        file.writeframes(b'\x00\x00' * 8000 * seconds)
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=MEDIA)
class DictationTest(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.client.login(username='sarah', password='motdepasse123')
        # Le calcul d'embedding de l'entrée est lancé à l'enregistrement : simulé
        patcher = patch('Journal.services.encode', side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
        patcher.start()
        self.addCleanup(patcher.stop)

    def post_audio(self, data, name='dictee.wav', **extra):
        return self.client.post(reverse('voice:dictation'),
                                {'audio': SimpleUploadedFile(name, data, content_type='audio/wav'), **extra})

    @patch('Voice.services.call_hf_whisper', return_value="Aujourd'hui je suis allé à la plage avec Karim.")
    def test_dictee_transcrite(self, mock_whisper):
        response = self.post_audio(wav_bytes(2))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('/ai/task/', data['status_url'])
        # En test, la tâche s'est exécutée tout de suite : le texte est gardé avec l'audio
        info = services.read_dictation(self.user, data['token'])
        self.assertEqual(info['text'], "Aujourd'hui je suis allé à la plage avec Karim.")
        self.assertEqual((info['format'], info['duration'], info['language']), ('wav', 2, 'fr'))

    @patch('Voice.services.call_hf_whisper', return_value="Bonne journée au travail.")
    def test_tache_renvoie_le_texte(self, mock_whisper):
        from Voice.tasks import transcribe_dictation_task
        token = services.save_dictation(self.user, wav_bytes(), 'wav', 1)
        result = transcribe_dictation_task(self.user.pk, token)
        self.assertEqual(result['text'], "Bonne journée au travail.")
        self.assertEqual(result['token'], token)

    def test_aucun_fichier(self):
        response = self.client.post(reverse('voice:dictation'))
        self.assertEqual(response.status_code, 400)

    def test_format_refuse(self):
        response = self.post_audio(b'pas un fichier audio du tout', name='texte.wav')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Format non reconnu', response.json()['error'])

    def test_plus_de_10_minutes_refuse(self):
        webm = b'\x1a\x45\xdf\xa3' + b'\x00' * 100              # WebM : durée donnée par le navigateur
        response = self.post_audio(webm, name='dictee.webm', duration='601')
        self.assertEqual(response.status_code, 400)
        self.assertIn('10 minutes', response.json()['error'])

    def test_duree_inconnue_refusee(self):
        response = self.post_audio(b'\x1a\x45\xdf\xa3' + b'\x00' * 100, name='dictee.webm')
        self.assertEqual(response.status_code, 400)

    @patch('Voice.tasks.transcribe_dictation_task.delay', side_effect=ConnectionError('Redis'))
    def test_redis_absent(self, mock_delay):
        response = self.post_audio(wav_bytes())
        self.assertEqual(response.status_code, 503)
        self.assertIn('Réessayez', response.json()['error'])

    @patch('Voice.services.call_hf_whisper', side_effect=AIServiceError('Le service de transcription ne répond pas.'))
    def test_echec_de_whisper(self, mock_whisper):
        token = services.save_dictation(self.user, wav_bytes(), 'wav', 1)
        with self.assertRaisesMessage(AIServiceError, 'ne répond pas'):
            services.transcribe_dictation(self.user, token)

    def test_jeton_invalide(self):
        self.assertIsNone(services.read_dictation(self.user, '../../secret'))
        with self.assertRaises(AIServiceError):
            services.transcribe_dictation(self.user, 'a' * 32)

    def test_connexion_obligatoire(self):
        self.client.logout()
        self.assertEqual(self.post_audio(wav_bytes()).status_code, 302)

    @patch('Voice.services.call_hf_whisper', return_value="Soirée cinéma avec Yassine.")
    def test_dictee_devient_une_note_vocale_de_l_entree(self, mock_whisper):
        token = self.post_audio(wav_bytes(3)).json()['token']
        response = self.client.post(reverse('journal:entry_create'), {
            'title': 'Cinéma', 'entry_date': '2026-01-10', 'content': 'Soirée cinéma avec Yassine.',
            'dictations': token}, follow=True)
        entry = JournalEntry.objects.get(title='Cinéma')
        note = VoiceNote.objects.get(entry=entry)
        self.assertEqual((note.transcription, note.status, note.duration), ('Soirée cinéma avec Yassine.', 'Termine', 3))
        self.assertTrue(note.audio.name.startswith('voice_notes/'))
        self.assertContains(response, 'comme note vocale')
        # Les fichiers temporaires de la dictée sont supprimés
        self.assertIsNone(services.read_dictation(self.user, token))

    def test_dictee_d_un_autre_utilisateur_ignoree(self):
        other = User.objects.create_user('amine', password='motdepasse123')
        token = services.save_dictation(other, wav_bytes(), 'wav', 1)
        self.client.post(reverse('journal:entry_create'), {
            'title': 'Ma journée', 'entry_date': '2026-01-10', 'content': 'Rien de spécial.', 'dictations': token})
        self.assertFalse(VoiceNote.objects.exists())
        self.assertTrue(default_storage.exists(services.dictation_path(other, token, 'json')))

    def test_formulaire_affiche_la_dictee(self):
        response = self.client.get(reverse('journal:entry_create'))
        self.assertContains(response, 'Dicter ma journée')
        self.assertContains(response, 'dictation.js')
        self.assertContains(response, 'name="dictations"')
        self.assertContains(response, '<h1', count=1)
