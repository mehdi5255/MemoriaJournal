# Tests d'intégration des vues de Voice (CRUD, permissions, validation des fichiers audio)
import io
import shutil
import tempfile
import wave
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from Journal.models import JournalEntry
from Voice.forms import audio_duration, detect_audio_format
from Voice.models import MoodRecord, VoiceNote

PASSWORD = 'MotDePasse!2026'
MEDIA_TEST = tempfile.mkdtemp()


def make_wav(seconds=2, name='note.wav'):
    """Vrai fichier WAV silencieux de la durée demandée (8 kHz, mono)."""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(8000)
        wav.writeframes(b'\x80' * 8000 * seconds)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='audio/wav')


def make_webm(name='enregistrement.webm'):
    """Début d'un fichier WebM (signature EBML) : sa durée n'est pas lisible côté serveur."""
    return SimpleUploadedFile(name, b'\x1a\x45\xdf\xa3' + b'\x00' * 200, content_type='audio/webm')


class AudioDetectionTest(SimpleTestCase):

    def test_formats_reconnus(self):
        self.assertEqual(detect_audio_format(b'RIFF\x00\x00\x00\x00WAVEfmt '), 'wav')
        self.assertEqual(detect_audio_format(b'OggS\x00\x02'), 'ogg')
        self.assertEqual(detect_audio_format(b'ID3\x04\x00'), 'mp3')
        self.assertEqual(detect_audio_format(b'\x1a\x45\xdf\xa3\x01'), 'webm')
        self.assertEqual(detect_audio_format(b'\x00\x00\x00\x20ftypM4A '), 'm4a')

    def test_formats_refuses(self):
        self.assertIsNone(detect_audio_format(b'Bonjour, ceci est un texte'))
        self.assertIsNone(detect_audio_format(b'RIFF\x00\x00\x00\x00AVI LIST'))      # RIFF mais pas WAVE
        self.assertIsNone(detect_audio_format(b'\x89PNG\r\n\x1a\n'))

    def test_duree_lue_dans_le_fichier(self):
        self.assertEqual(audio_duration(make_wav(3).read()), 3)
        self.assertIsNone(audio_duration(b'pas un fichier audio'))


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class VoiceViewsTestBase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user('seif', password=PASSWORD)
        self.other = User.objects.create_user('amine', password=PASSWORD)
        self.entry = JournalEntry.objects.create(user=self.user, title='Ma soirée', content='...')
        self.other_entry = JournalEntry.objects.create(user=self.other, title='Secret', content='...')
        self.note = VoiceNote.objects.create(entry=self.entry, title='Note du soir', audio='voice_notes/a.wav',
                                             duration=12, transcription='Bonne soirée.', status='Termine')
        self.other_note = VoiceNote.objects.create(entry=self.other_entry, title='Note secrète',
                                                   audio='voice_notes/b.wav')
        self.mood = MoodRecord.objects.create(user=self.user, entry=self.entry, emotion='Joie', score=0.8,
                                              record_date=self.today)
        self.other_mood = MoodRecord.objects.create(user=self.other, emotion='Peur', score=0.4, note='Humeur secrète')
        self.client.login(username='seif', password=PASSWORD)


class LoginRequiredTest(VoiceViewsTestBase):

    def test_toutes_les_pages_redirigent_vers_la_connexion(self):
        self.client.logout()
        urls = [reverse('voice:mood_list'), reverse('voice:mood_create'),
                reverse('voice:mood_detail', args=[self.mood.pk]),
                reverse('voice:mood_update', args=[self.mood.pk]),
                reverse('voice:mood_delete', args=[self.mood.pk]),
                reverse('voice:note_list'), reverse('voice:note_create'),
                reverse('voice:note_detail', args=[self.note.pk]),
                reverse('voice:note_update', args=[self.note.pk]),
                reverse('voice:note_delete', args=[self.note.pk])]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('account:login'), response.url)


class OtherUserDataTest(VoiceViewsTestBase):
    """Un utilisateur ne peut pas lire, modifier ou supprimer les données d'un autre (404)."""

    def test_note_d_un_autre_404(self):
        for name in ('note_detail', 'note_update', 'note_delete'):
            response = self.client.get(reverse(f'voice:{name}', args=[self.other_note.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.client.post(reverse('voice:note_delete', args=[self.other_note.pk]))
        self.assertTrue(VoiceNote.objects.filter(pk=self.other_note.pk).exists())

    def test_humeur_d_un_autre_404(self):
        for name in ('mood_detail', 'mood_update', 'mood_delete'):
            response = self.client.get(reverse(f'voice:{name}', args=[self.other_mood.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.client.post(reverse('voice:mood_update', args=[self.other_mood.pk]),
                         {'emotion': 'Joie', 'score': 1, 'record_date': self.today})
        self.other_mood.refresh_from_db()
        self.assertEqual(self.other_mood.emotion, 'Peur')

    def test_listes_ne_montrent_que_mes_donnees(self):
        self.assertNotContains(self.client.get(reverse('voice:note_list')), 'Note secrète')
        self.assertNotContains(self.client.get(reverse('voice:mood_list')), 'Humeur secrète')

    def test_impossible_d_ajouter_une_note_a_l_entree_d_un_autre(self):
        response = self.client.post(reverse('voice:note_create'), {
            'entry': self.other_entry.pk, 'title': 'Intrus', 'audio': make_wav()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('entry'))

    def test_humeur_toujours_au_nom_de_l_utilisateur_connecte(self):
        self.client.post(reverse('voice:mood_create'), {
            'emotion': 'Surprise', 'score': 0.6, 'record_date': self.today, 'user': self.other.pk})
        self.assertEqual(MoodRecord.objects.get(emotion='Surprise').user, self.user)


class VoiceNoteViewsTest(VoiceViewsTestBase):

    def test_liste_et_filtre(self):
        self.assertContains(self.client.get(reverse('voice:note_list')), 'Note du soir')
        response = self.client.get(reverse('voice:note_list'), {'statut': 'Echec'})
        self.assertContains(response, 'Aucune note vocale avec ce statut.')

    def test_detail_avec_lecteur_audio(self):
        response = self.client.get(reverse('voice:note_detail', args=[self.note.pk]))
        self.assertContains(response, '<audio controls')
        self.assertContains(response, 'Bonne soirée.')

    @patch('Voice.services.detect_emotion', return_value=('Joie', 0.91))
    @patch('Voice.services.transcribe', return_value=('Quelle belle matinée.', 'fr'))
    def test_creation_wav_duree_lue_par_le_serveur_puis_transcription(self, mock_transcribe, mock_emotion):
        response = self.client.post(reverse('voice:note_create'), {
            'entry': self.entry.pk, 'title': 'Matin', 'audio': make_wav(3), 'client_duration': 999})
        note = VoiceNote.objects.get(title='Matin')
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/voice/notes/{note.pk}/?task=", response.url)      # page avec l'indicateur IA
        self.assertEqual(note.duration, 3)          # la valeur du navigateur est ignorée
        self.assertEqual(note.status, 'Termine')    # mode eager : la tâche est déjà faite
        self.assertEqual(note.mood_record.emotion, 'Joie')

    def test_creation_sans_ia_disponible_garde_la_note(self):
        # Pas de clé en test : la transcription échoue mais la note est bien enregistrée
        response = self.client.post(reverse('voice:note_create'), {
            'entry': self.entry.pk, 'title': 'Hors ligne', 'audio': make_wav(1)}, follow=True)
        note = VoiceNote.objects.get(title='Hors ligne')
        self.assertEqual(note.status, 'Echec')
        self.assertContains(response, 'HF_TOKEN')
        self.assertContains(response, 'Transcrire à nouveau')

    def test_creation_webm_duree_du_navigateur(self):
        self.client.post(reverse('voice:note_create'), {
            'entry': self.entry.pk, 'title': 'Enregistrée', 'audio': make_webm(), 'client_duration': 42})
        self.assertEqual(VoiceNote.objects.get(title='Enregistrée').duration, 42)

    def test_webm_sans_duree_refuse(self):
        response = self.client.post(reverse('voice:note_create'), {
            'entry': self.entry.pk, 'title': 'Sans durée', 'audio': make_webm()})
        self.assertIn("Impossible de lire la durée", response.context['form'].errors['audio'][0])

    def test_plus_de_10_minutes_refuse(self):
        response = self.client.post(reverse('voice:note_create'), {
            'entry': self.entry.pk, 'title': 'Trop longue', 'audio': make_webm(), 'client_duration': 601})
        self.assertIn('Une note vocale ne doit pas dépasser 10 minutes.', response.context['form'].errors['audio'])
        self.assertFalse(VoiceNote.objects.filter(title='Trop longue').exists())

    def test_faux_fichier_audio_refuse(self):
        # Un texte renommé en .mp3 : refusé grâce à la signature du fichier
        fichier = SimpleUploadedFile('piege.mp3', b'Ceci n est pas du son', content_type='audio/mpeg')
        response = self.client.post(reverse('voice:note_create'), {
            'entry': self.entry.pk, 'title': 'Piège', 'audio': fichier, 'client_duration': 5})
        self.assertIn('Format non reconnu', response.context['form'].errors['audio'][0])

    def test_fichier_trop_lourd_refuse(self):
        from Voice import forms
        original = forms.AUDIO_MAX_SIZE
        forms.AUDIO_MAX_SIZE = 100
        try:
            response = self.client.post(reverse('voice:note_create'), {
                'entry': self.entry.pk, 'title': 'Lourde', 'audio': make_wav(1)})
        finally:
            forms.AUDIO_MAX_SIZE = original
        self.assertIn('Le fichier audio ne doit pas dépasser 20 Mo.', response.context['form'].errors['audio'])

    def test_creation_pre_remplie_depuis_une_entree(self):
        response = self.client.get(reverse('voice:note_create'), {'entry': self.entry.pk})
        self.assertEqual(response.context['form'].initial['entry'], str(self.entry.pk))
        self.assertContains(response, 'data-recorder')
        self.assertContains(response, 'accept="audio/webm')

    def test_sans_entree_message_clair(self):
        JournalEntry.objects.filter(user=self.user).delete()
        response = self.client.get(reverse('voice:note_create'))
        self.assertContains(response, "écrivez d'abord une entrée")

    def test_modification_titre_et_transcription(self):
        response = self.client.post(reverse('voice:note_update', args=[self.note.pk]), {
            'title': 'Note corrigée', 'transcription': 'Très bonne soirée.', 'language': 'fr'}, follow=True)
        self.assertContains(response, 'La note vocale a été modifiée.')
        self.note.refresh_from_db()
        self.assertEqual(self.note.transcription, 'Très bonne soirée.')

    def test_suppression_avec_confirmation(self):
        response = self.client.get(reverse('voice:note_delete', args=[self.note.pk]))
        self.assertContains(response, 'Voulez-vous vraiment supprimer')
        response = self.client.post(reverse('voice:note_delete', args=[self.note.pk]), follow=True)
        self.assertRedirects(response, reverse('voice:note_list'))
        self.assertFalse(VoiceNote.objects.filter(pk=self.note.pk).exists())
        self.assertTrue(JournalEntry.objects.filter(pk=self.entry.pk).exists())

    def test_etat_vide(self):
        VoiceNote.objects.filter(entry__user=self.user).delete()
        self.assertContains(self.client.get(reverse('voice:note_list')), "Aucune note vocale pour l'instant.")


class MoodViewsTest(VoiceViewsTestBase):

    def test_page_humeur_avec_graphique_et_tableau(self):
        MoodRecord.objects.create(user=self.user, emotion='Tristesse', score=0.4, record_date=self.today)
        MoodRecord.objects.create(user=self.user, emotion='Joie', score=0.6, record_date=self.today)
        response = self.client.get(reverse('voice:mood_list'))
        day = response.context['days'][-1]
        self.assertEqual(day['emotion'], 'Joie')                # 2 « Joie » contre 1 « Tristesse »
        self.assertEqual(day['score'], 0.6)                     # moyenne de 0.8, 0.4 et 0.6
        self.assertContains(response, 'id="graphique-humeur"')
        self.assertContains(response, 'role="img"')
        self.assertContains(response, 'id="donnees-humeur"')    # données JSON pour Chart.js
        self.assertContains(response, '<caption')               # tableau de données accessible

    def test_navigation_entre_les_mois(self):
        old = self.today.replace(day=1) - timedelta(days=40)
        MoodRecord.objects.create(user=self.user, emotion='Peur', score=0.3, record_date=old)
        response = self.client.get(reverse('voice:mood_list'), {'mois': old.strftime('%Y-%m')})
        self.assertEqual([d['emotion'] for d in response.context['days']], ['Peur'])
        self.assertIsNotNone(response.context['next_month'])
        current = self.client.get(reverse('voice:mood_list'))
        self.assertIsNone(current.context['next_month'])        # pas de lien vers le futur

    def test_mois_invalide_affiche_le_mois_courant(self):
        response = self.client.get(reverse('voice:mood_list'), {'mois': 'n-importe-quoi'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['month'], self.today.replace(day=1))

    def test_mois_sans_humeur(self):
        response = self.client.get(reverse('voice:mood_list'), {'mois': '2020-01'})
        self.assertContains(response, 'Aucune humeur notée en')

    def test_creation_manuelle(self):
        response = self.client.post(reverse('voice:mood_create'), {
            'emotion': 'Surprise', 'score': 0.7, 'record_date': self.today, 'entry': self.entry.pk, 'note': 'Cadeau'})
        mood = MoodRecord.objects.get(emotion='Surprise')
        self.assertRedirects(response, reverse('voice:mood_detail', args=[mood.pk]))
        self.assertEqual(mood.source, 'Manuel')

    def test_score_hors_limites_refuse(self):
        response = self.client.post(reverse('voice:mood_create'), {
            'emotion': 'Joie', 'score': 1.5, 'record_date': self.today})
        self.assertContains(response, 'Le score doit être compris entre 0 et 1.')

    def test_date_future_refusee(self):
        response = self.client.post(reverse('voice:mood_create'), {
            'emotion': 'Joie', 'score': 0.5, 'record_date': self.today + timedelta(days=2)})
        self.assertContains(response, 'La date ne peut pas être dans le futur.')

    def test_entree_d_un_autre_refusee(self):
        response = self.client.post(reverse('voice:mood_create'), {
            'emotion': 'Joie', 'score': 0.5, 'record_date': self.today, 'entry': self.other_entry.pk})
        self.assertTrue(response.context['form'].errors.get('entry'))

    def test_detail_modification_suppression(self):
        self.assertContains(self.client.get(reverse('voice:mood_detail', args=[self.mood.pk])), 'Joie')
        response = self.client.post(reverse('voice:mood_update', args=[self.mood.pk]), {
            'emotion': 'Neutre', 'score': 0.5, 'record_date': self.today}, follow=True)
        self.assertContains(response, 'humeur a été modifiée.')
        response = self.client.post(reverse('voice:mood_delete', args=[self.mood.pk]))
        self.assertRedirects(response, reverse('voice:mood_list'))
        self.assertFalse(MoodRecord.objects.filter(pk=self.mood.pk).exists())


class VoiceAccessibilityTest(VoiceViewsTestBase):

    def test_un_seul_h1_et_titres_uniques(self):
        urls = [reverse('voice:mood_list'), reverse('voice:mood_create'),
                reverse('voice:mood_detail', args=[self.mood.pk]), reverse('voice:mood_update', args=[self.mood.pk]),
                reverse('voice:note_list'), reverse('voice:note_create'),
                reverse('voice:note_detail', args=[self.note.pk]), reverse('voice:note_update', args=[self.note.pk])]
        titles = set()
        for url in urls:
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('<h1'), 1, url)
            titles.add(html.split('<title>')[1].split('</title>')[0])
        self.assertEqual(len(titles), len(urls))

    def test_enregistreur_utilisable_au_clavier(self):
        html = self.client.get(reverse('voice:note_create')).content.decode()
        self.assertIn('<button type="button" class="btn btn-primary" data-action="start">', html)
        self.assertIn('data-action="stop" disabled', html)
        self.assertIn('role="status" aria-live="polite"', html)
