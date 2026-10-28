# Tests des services IA de Memory : tous les appels externes sont mockés
import io
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from Account.models import ActivityLog
from Face.models import FaceTag, Person, Photo
from Journal.models import JournalEntry
from Memory import services, tasks
from Memory.models import GeneratedImage, Retrospective, Story
from Voice.models import MoodRecord, VoiceNote

MEDIA_TEST = tempfile.mkdtemp()
STORY = "Ce matin, nous avons marché jusqu'à la plage. La mer était calme et Sarah riait aux éclats."


def png_bytes(color='blue'):
    buffer = io.BytesIO()
    Image.new('RGB', (16, 16), color=color).save(buffer, format='PNG')
    return buffer.getvalue()


def gemini_response(text):
    response = MagicMock()
    response.text = text
    return response


@override_settings(MEDIA_ROOT=MEDIA_TEST, GEMINI_API_KEY='test-key', HF_TOKEN='test-token')
class MemoryServicesTestBase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    def setUp(self):
        # Les clients sont mis en cache : on repart de zéro à chaque test
        services.get_gemini_client.cache_clear()
        services.get_hf_client.cache_clear()
        services.get_keybert_model.cache_clear()
        self.today = timezone.localdate()
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.entry = JournalEntry.objects.create(user=self.user, title='Plage', content='Journée à la mer.',
                                                 location='Hammamet', entry_date=self.today)


class StoryPromptTest(MemoryServicesTestBase):

    def test_le_prompt_rassemble_texte_voix_humeur_personnes_et_photos(self):
        VoiceNote.objects.create(entry=self.entry, title='Soir', audio='voice_notes/a.webm',
                                 transcription='On a mangé une glace.', status='Termine')
        VoiceNote.objects.create(entry=self.entry, title='Ratée', audio='voice_notes/b.webm',
                                 transcription='Texte non validé', status='Echec')
        MoodRecord.objects.create(user=self.user, entry=self.entry, emotion='Joie', score=0.9)
        photo = Photo.objects.create(entry=self.entry, image='photos/a.jpg', caption='Deux amis sur le sable')
        sarah = Person.objects.create(user=self.user, name='Sarah')
        FaceTag.objects.create(photo=photo, person=sarah, x=1, y=1, width=5, height=5)

        prompt = services.build_story_prompt(self.entry, 'Poetique')
        self.assertIn('Journée à la mer.', prompt)
        self.assertIn('Hammamet', prompt)
        self.assertIn('On a mangé une glace.', prompt)
        self.assertNotIn('Texte non validé', prompt)
        self.assertIn('Joie', prompt)
        self.assertIn('Sarah', prompt)
        self.assertIn('Deux amis sur le sable', prompt)
        self.assertIn('poétique', prompt)


class GenerateStoryTextTest(MemoryServicesTestBase):

    @patch('Memory.services.get_gemini_client')
    def test_succes_avec_photo_jointe(self, mock_client):
        mock_client.return_value.models.generate_content.return_value = gemini_response(STORY)
        Photo.objects.create(entry=self.entry, image=SimpleUploadedFile('a.png', png_bytes(), 'image/png'))

        text, prompt = services.generate_story_text(self.entry, 'Simple', self.user)

        self.assertEqual(text, STORY)
        contents = mock_client.return_value.models.generate_content.call_args.kwargs['contents']
        self.assertEqual(contents[0], prompt)
        self.assertEqual(len(contents), 2)                  # le prompt + la photo
        log = ActivityLog.objects.get()
        self.assertEqual((log.ai_service, log.status), ('Gemini', 'Succes'))

    @patch('Memory.services.get_gemini_client')
    def test_echec_de_l_api(self, mock_client):
        mock_client.return_value.models.generate_content.side_effect = ConnectionError('timeout')
        with self.assertRaisesMessage(services.AIServiceError, 'ne répond pas'):
            services.generate_story_text(self.entry, 'Simple', self.user)
        self.assertEqual(ActivityLog.objects.get().status, 'Echec')

    @patch('Memory.services.get_gemini_client')
    def test_reponse_vide(self, mock_client):
        mock_client.return_value.models.generate_content.return_value = gemini_response('')
        with self.assertRaisesMessage(services.AIServiceError, "aucun texte"):
            services.generate_story_text(self.entry, 'Simple', self.user)

    @override_settings(GEMINI_API_KEY='')
    def test_cle_absente(self):
        with self.assertRaisesMessage(services.AIServiceError, 'GEMINI_API_KEY'):
            services.generate_story_text(self.entry, 'Simple', self.user)
        self.assertEqual(ActivityLog.objects.get().status, 'Echec')


class GenerateStoryTaskTest(MemoryServicesTestBase):

    @patch('Memory.services.call_gemini', return_value=STORY)
    def test_creation_puis_regeneration_incremente_la_version(self, mock_gemini):
        result = tasks.generate_story_task.delay(self.entry.pk, 'Simple', self.user.pk).get()
        story = Story.objects.get(entry=self.entry)
        self.assertEqual(story.version, 1)
        self.assertIn('Sarah riait', result['html'])

        mock_gemini.return_value = "Une autre version, plus drôle, de cette belle journée à la mer."
        tasks.generate_story_task.delay(self.entry.pk, 'Humoristique', self.user.pk).get()
        story.refresh_from_db()
        self.assertEqual(story.version, 2)
        self.assertEqual(story.style, 'Humoristique')
        self.assertIn('plus drôle', story.text)
        self.assertTrue(story.prompt)

    @patch('Memory.services.call_gemini', side_effect=services.AIServiceError('Gemini indisponible'))
    def test_echec_ne_cree_pas_de_recit(self, mock_gemini):
        with self.assertRaises(services.AIServiceError):
            tasks.generate_story_task.delay(self.entry.pk, 'Simple', self.user.pk)
        self.assertFalse(Story.objects.exists())

    def test_entree_d_un_autre_utilisateur_refusee(self):
        other = User.objects.create_user('amine', password='motdepasse123')
        with self.assertRaises(JournalEntry.DoesNotExist):
            tasks.generate_story_task.delay(self.entry.pk, 'Simple', other.pk)


class IllustrationTest(MemoryServicesTestBase):

    def setUp(self):
        super().setUp()
        self.story = Story.objects.create(entry=self.entry, text=STORY)

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.call_gemini', return_value='A calm beach at sunrise')
    def test_texte_vers_image(self, mock_gemini, mock_hf):
        mock_hf.return_value.text_to_image.return_value = Image.new('RGB', (8, 8), 'yellow')

        result = tasks.generate_image_task.delay(self.story.pk, 'Aquarelle', self.user.pk).get()

        image = GeneratedImage.objects.get()
        self.assertTrue(image.is_main)                       # première image = principale
        self.assertEqual(image.style, 'Aquarelle')
        self.assertIn('A calm beach at sunrise', image.prompt)
        self.assertIn('watercolor', image.prompt)
        self.assertIn('<img', result['html'])
        services_logged = set(ActivityLog.objects.values_list('ai_service', flat=True))
        self.assertEqual(services_logged, {'Gemini', 'Stable Diffusion'})

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_deuxieme_image_pas_principale(self, mock_gemini, mock_hf):
        GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='a', is_main=True)
        mock_hf.return_value.text_to_image.return_value = Image.new('RGB', (8, 8))
        tasks.generate_image_task.delay(self.story.pk, 'Dessin', self.user.pk).get()
        self.assertEqual(self.story.images.filter(is_main=True).count(), 1)

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_transformation_d_une_photo(self, mock_gemini, mock_hf):
        photo = Photo.objects.create(entry=self.entry, image=SimpleUploadedFile('p.png', png_bytes(), 'image/png'))
        mock_hf.return_value.image_to_image.return_value = Image.new('RGB', (8, 8))
        tasks.generate_image_task.delay(self.story.pk, 'Dessin', self.user.pk, photo.pk).get()
        self.assertEqual(GeneratedImage.objects.get().source_photo, photo)
        mock_hf.return_value.image_to_image.assert_called_once()
        mock_hf.return_value.text_to_image.assert_not_called()

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.call_gemini', side_effect=services.AIServiceError('panne'))
    def test_description_de_secours_si_gemini_echoue(self, mock_gemini, mock_hf):
        mock_hf.return_value.text_to_image.return_value = Image.new('RGB', (8, 8))
        tasks.generate_image_task.delay(self.story.pk, 'Realiste', self.user.pk).get()
        self.assertIn('Ce matin', GeneratedImage.objects.get().prompt)

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_echec_de_stable_diffusion(self, mock_gemini, mock_hf):
        mock_hf.return_value.text_to_image.side_effect = RuntimeError('503 Service Unavailable')
        with self.assertRaisesMessage(services.AIServiceError, "Le service d'illustration ne répond pas"):
            tasks.generate_image_task.delay(self.story.pk, 'Realiste', self.user.pk)
        self.assertFalse(GeneratedImage.objects.exists())
        self.assertTrue(ActivityLog.objects.filter(ai_service='Stable Diffusion', status='Echec').exists())

    @override_settings(HF_TOKEN='')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_jeton_hf_absent(self, mock_gemini):
        with self.assertRaisesMessage(services.AIServiceError, 'HF_TOKEN'):
            services.generate_illustration(self.story, 'Realiste', self.user)


class RetrospectiveAnalysisTest(MemoryServicesTestBase):

    def setUp(self):
        super().setUp()
        self.entry.is_favorite = True
        self.entry.save()
        self.second = JournalEntry.objects.create(user=self.user, title='Examens', content='Révisions toute la journée.',
                                                  entry_date=self.today - timedelta(days=2))
        self.outside = JournalEntry.objects.create(user=self.user, title='Ancienne', content='...',
                                                   entry_date=self.today - timedelta(days=30))
        MoodRecord.objects.create(user=self.user, entry=self.entry, emotion='Joie', record_date=self.today)
        MoodRecord.objects.create(user=self.user, entry=self.second, emotion='Joie',
                                  record_date=self.today - timedelta(days=2))
        MoodRecord.objects.create(user=self.user, emotion='Tristesse', record_date=self.today - timedelta(days=1))
        self.retro = Retrospective.objects.create(user=self.user, period_type='Semaine', title='Ma semaine',
                                                  start_date=self.today - timedelta(days=6), end_date=self.today)

    def test_statistiques(self):
        self.retro.entries.set([self.entry, self.second])
        stats = services.retrospective_stats(self.retro)
        self.assertEqual(stats['entry_count'], 2)
        self.assertEqual(stats['dominant'], 'Joie')
        self.assertEqual(stats['emotions'][0], {'emotion': 'Joie', 'label': 'Joie', 'count': 2, 'percent': 67})
        self.assertIn(self.entry, stats['highlights'])

    @patch('Memory.services.get_keybert_model')
    def test_analyse_complete(self, mock_model):
        mock_model.return_value.extract_keywords.return_value = [('plage', 0.8), ('révisions', 0.6)]

        result = tasks.analyze_retrospective_task.delay(self.retro.pk, self.user.pk).get()

        self.retro.refresh_from_db()
        # Sans entrées choisies, toutes celles de la période sont ajoutées
        self.assertEqual(set(self.retro.entries.all()), {self.entry, self.second})
        self.assertEqual(self.retro.keywords, ['plage', 'révisions'])
        self.assertIn('Humeur dominante : joie', self.retro.summary)
        self.assertIn('plage', result['html'])
        text = mock_model.return_value.extract_keywords.call_args.args[0]
        self.assertIn('Révisions toute la journée.', text)
        self.assertNotIn('Ancienne', text)
        self.assertEqual(ActivityLog.objects.get().ai_service, 'KeyBERT')

    @patch('Memory.services.get_keybert_model')
    def test_resume_ecrit_par_l_utilisateur_conserve(self, mock_model):
        mock_model.return_value.extract_keywords.return_value = [('plage', 0.8)]
        self.retro.summary = 'Mon propre résumé.'
        self.retro.save()
        tasks.analyze_retrospective_task.delay(self.retro.pk, self.user.pk).get()
        self.retro.refresh_from_db()
        self.assertEqual(self.retro.summary, 'Mon propre résumé.')

    @patch('Memory.services.get_keybert_model')
    def test_echec_keybert(self, mock_model):
        mock_model.return_value.extract_keywords.side_effect = RuntimeError('modèle introuvable')
        with self.assertRaisesMessage(services.AIServiceError, "L'extraction des thèmes a échoué"):
            tasks.analyze_retrospective_task.delay(self.retro.pk, self.user.pk)
        self.assertEqual(ActivityLog.objects.get().status, 'Echec')

    def test_periode_sans_texte(self):
        empty = Retrospective.objects.create(user=self.user, period_type='Semaine', title='Vide',
                                             start_date=self.today - timedelta(days=60),
                                             end_date=self.today - timedelta(days=55))
        with self.assertRaisesMessage(services.AIServiceError, 'Aucun texte'):
            services.extract_keywords(empty, self.user)


class ImageBackendTest(MemoryServicesTestBase):
    """Choix du moteur d'illustration : huggingface (défaut), local ou auto (secours)."""

    def setUp(self):
        super().setUp()
        services.get_local_text_to_image.cache_clear()
        services.get_local_image_to_image.cache_clear()
        self.story = Story.objects.create(entry=self.entry, text=STORY)

    def local_pipe(self, mock_get):
        mock_get.return_value.return_value.images = [Image.new('RGB', (8, 8), 'green')]
        return mock_get.return_value

    @override_settings(IMAGE_BACKEND='local')
    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.get_local_text_to_image')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_moteur_local(self, mock_gemini, mock_local, mock_hf):
        pipe = self.local_pipe(mock_local)
        png, prompt = services.generate_illustration(self.story, 'Aquarelle', self.user)
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertEqual(pipe.call_args.kwargs['num_inference_steps'], 1)      # SD-Turbo : 1 étape
        mock_hf.assert_not_called()
        self.assertTrue(ActivityLog.objects.filter(ai_service='SD-Turbo (local)', status='Succes').exists())

    @override_settings(IMAGE_BACKEND='auto')
    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.get_local_text_to_image')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_auto_bascule_sur_le_local_si_hugging_face_echoue(self, mock_gemini, mock_local, mock_hf):
        mock_hf.return_value.text_to_image.side_effect = RuntimeError('402 Payment Required')
        self.local_pipe(mock_local)
        tasks.generate_image_task.delay(self.story.pk, 'Dessin', self.user.pk).get()
        self.assertEqual(GeneratedImage.objects.count(), 1)
        self.assertTrue(ActivityLog.objects.filter(ai_service='Stable Diffusion', status='Echec').exists())
        self.assertTrue(ActivityLog.objects.filter(ai_service='SD-Turbo (local)', status='Succes').exists())

    @override_settings(IMAGE_BACKEND='auto')
    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.get_local_text_to_image')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_auto_garde_hugging_face_s_il_repond(self, mock_gemini, mock_local, mock_hf):
        mock_hf.return_value.text_to_image.return_value = Image.new('RGB', (8, 8))
        services.generate_illustration(self.story, 'Dessin', self.user)
        mock_local.assert_not_called()

    @override_settings(IMAGE_BACKEND='auto')
    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.get_local_text_to_image', side_effect=OSError('modèle absent'))
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_auto_les_deux_moteurs_echouent(self, mock_gemini, mock_local, mock_hf):
        mock_hf.return_value.text_to_image.side_effect = RuntimeError('402')
        with self.assertRaisesMessage(services.AIServiceError, 'modèle d\'illustration local'):
            services.generate_illustration(self.story, 'Dessin', self.user)

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.get_local_text_to_image')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_par_defaut_pas_de_secours_local(self, mock_gemini, mock_local, mock_hf):
        mock_hf.return_value.text_to_image.side_effect = RuntimeError('402')
        with self.assertRaises(services.AIServiceError):
            services.generate_illustration(self.story, 'Dessin', self.user)
        mock_local.assert_not_called()

    @override_settings(IMAGE_BACKEND='local')
    @patch('Memory.services.get_local_image_to_image')
    @patch('Memory.services.call_gemini', return_value='A calm beach')
    def test_transformation_de_photo_en_local(self, mock_gemini, mock_local):
        pipe = self.local_pipe(mock_local)
        photo = Photo.objects.create(entry=self.entry, image=SimpleUploadedFile('p.png', png_bytes(), 'image/png'))
        services.generate_illustration(self.story, 'Dessin', self.user, source_photo=photo)
        kwargs = pipe.call_args.kwargs
        self.assertEqual(kwargs['image'].size, (512, 512))
        self.assertGreaterEqual(kwargs['num_inference_steps'] * kwargs['strength'], 1)


class DownloadImageModelCommandTest(TestCase):

    @patch('huggingface_hub.snapshot_download', return_value='C:/cache/sd-turbo')
    def test_telechargement(self, mock_download):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('download_image_model', stdout=out)
        self.assertIn('Modèle prêt', out.getvalue())
        self.assertIn('unet/*.fp16.safetensors', mock_download.call_args.kwargs['allow_patterns'])

    @patch('huggingface_hub.snapshot_download', side_effect=OSError('réseau coupé'))
    def test_echec_message_clair(self, mock_download):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, 'Relancez la commande'):
            call_command('download_image_model')


class GeminiQuotaMessageTest(MemoryServicesTestBase):

    @patch('Memory.services.get_gemini_client')
    def test_quota_atteint_message_clair(self, mock_client):
        mock_client.return_value.models.generate_content.side_effect = RuntimeError('429 RESOURCE_EXHAUSTED')
        with self.assertRaisesMessage(services.AIServiceError, 'quota gratuit de Gemini est atteint'):
            services.call_gemini(['Bonjour'])

    @override_settings(GEMINI_JUDGE_MODEL='modele-juge')
    @patch('Memory.services.get_gemini_client')
    def test_le_juge_utilise_un_autre_modele(self, mock_client):
        from Memory import metrics
        mock_client.return_value.models.generate_content.return_value = gemini_response('{"style": 5}')
        metrics.judge_style('texte', 'récit', 'Poetique')
        call = mock_client.return_value.models.generate_content.call_args.kwargs
        self.assertEqual(call['model'], 'modele-juge')
        self.assertIn('métaphores', call['contents'][0])      # définition du style transmise au juge
