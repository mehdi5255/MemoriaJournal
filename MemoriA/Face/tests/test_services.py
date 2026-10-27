# Tests des services IA de Face : BLIP, DETR, YuNet et SFace sont mockés (DBSCAN tourne pour de vrai)
import io
import shutil
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from Account.models import ActivityLog
from Account.services import AIServiceError
from Face import services, tasks
from Face.models import FaceTag, Person, Photo
from Journal.models import JournalEntry

MEDIA_TEST = tempfile.mkdtemp()
PASSWORD = 'MotDePasse!2026'


def png(size=(200, 100)):
    buffer = io.BytesIO()
    Image.new('RGB', size, color='orange').save(buffer, format='PNG')
    return buffer.getvalue()


def vector(*values):
    """Empreinte de visage de test (normalisée, comme celles de SFace)."""
    v = np.array(values, dtype=float)
    return v / np.linalg.norm(v)


def detection(x, y, w, h, score=0.95):
    return {'x': x, 'y': y, 'width': w, 'height': h, 'score': score, 'row': np.zeros(15)}


class PureFunctionsTest(SimpleTestCase):

    def test_objets_en_francais_uniques_et_tries(self):
        results = [{'label': 'cup', 'score': 0.8}, {'label': 'person', 'score': 0.99},
                   {'label': 'person', 'score': 0.97}, {'label': 'dog', 'score': 0.4}, {'label': 'spaceship', 'score': 0.9}]
        self.assertEqual(services.objects_in_french(results), ['personne', 'spaceship', 'tasse'])

    def test_recouvrement(self):
        a = {'x': 0, 'y': 0, 'width': 10, 'height': 10}
        self.assertEqual(services.overlap(a, a), 1)
        self.assertEqual(services.overlap(a, {'x': 20, 'y': 20, 'width': 5, 'height': 5}), 0)
        self.assertAlmostEqual(services.overlap(a, {'x': 5, 'y': 0, 'width': 10, 'height': 10}), 50 / 150)

    def test_cosinus(self):
        self.assertAlmostEqual(services.cosine(vector(1, 0), vector(1, 0)), 1)
        self.assertAlmostEqual(services.cosine(vector(1, 0), vector(0, 1)), 0)

    def test_meilleure_correspondance(self):
        sarah = SimpleNamespace(name='Sarah', face_encoding=list(vector(1, 0.1)))
        karim = SimpleNamespace(name='Karim', face_encoding=list(vector(0, 1)))
        self.assertIs(services.best_match(vector(1, 0), [sarah, karim])[0], sarah)
        person, score = services.best_match(vector(-1, -1), [sarah, karim])       # personne ne ressemble
        self.assertIsNone(person)
        self.assertLess(score, services.MATCH_THRESHOLD)

    def test_dbscan_regroupe_les_visages_proches(self):
        features = [vector(1, 0, 0), vector(0.98, 0.05, 0), vector(0, 1, 0), vector(0.02, 0.99, 0), vector(0, 0, 1)]
        labels = services.cluster_faces(features)
        self.assertEqual(labels[0], labels[1])
        self.assertEqual(labels[2], labels[3])
        self.assertNotEqual(labels[0], labels[2])
        self.assertEqual(labels[4], -1)                   # visage isolé

    def test_dbscan_un_seul_visage(self):
        self.assertEqual(services.cluster_faces([vector(1, 0)]), [-1])


@override_settings(MEDIA_ROOT=MEDIA_TEST, HF_TOKEN='test-token')
class FaceServicesTestBase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    def setUp(self):
        for loader in (services.get_hf_client, services.get_local_captioner, services.get_local_object_detector,
                       services.get_translator, services.get_face_detector, services.get_face_recognizer):
            loader.cache_clear()
        self.user = User.objects.create_user('sabri', password=PASSWORD)
        self.entry = JournalEntry.objects.create(user=self.user, title='Anniversaire', content='...')
        self.photo = self.make_photo()
        self.sarah = Person.objects.create(user=self.user, name='Sarah', face_encoding=list(vector(1, 0, 0)))

    def make_photo(self, **kwargs):
        photo = Photo(entry=self.entry, **kwargs)
        photo.image.save('photo.png', ContentFile(png()), save=True)
        return photo


class CaptionAndObjectsTest(FaceServicesTestBase):

    @patch('Face.services.translate_to_french', return_value='un gâteau sur une table')
    @patch('Face.services.get_hf_client')
    def test_legende_hugging_face_traduite(self, mock_hf, mock_translate):
        mock_hf.return_value.image_to_text.return_value = SimpleNamespace(generated_text='a cake on a table')
        self.assertEqual(services.caption_photo(self.photo, self.user), 'Un gâteau sur une table')
        mock_translate.assert_called_once_with('a cake on a table')
        self.assertTrue(ActivityLog.objects.filter(ai_service='BLIP', status='Succes').exists())

    @override_settings(FACE_BACKEND='auto')
    @patch('Face.services.translate_to_french', side_effect=lambda text: text)
    @patch('Face.services.call_local_caption', return_value='two friends at a cafe')
    @patch('Face.services.get_hf_client')
    def test_legende_secours_local(self, mock_hf, mock_local, mock_translate):
        mock_hf.return_value.image_to_text.side_effect = RuntimeError('402 Payment Required')
        self.assertEqual(services.caption_photo(self.photo, self.user), 'Two friends at a cafe')
        self.assertEqual(set(ActivityLog.objects.filter(action='Légende de la photo')
                             .values_list('ai_service', 'status')), {('BLIP', 'Echec'), ('BLIP (local)', 'Succes')})

    @patch('Face.services.get_hf_client')
    def test_legende_echec(self, mock_hf):
        mock_hf.return_value.image_to_text.side_effect = RuntimeError('503')
        with self.assertRaisesMessage(AIServiceError, 'Le service de légende ne répond pas'):
            services.caption_photo(self.photo, self.user)

    @patch('Face.services.get_translator', side_effect=OSError('modèle absent'))
    def test_traduction_en_echec_garde_l_anglais(self, mock_translator):
        self.assertEqual(services.translate_to_french('a dog'), 'a dog')

    @patch('Face.services.get_hf_client')
    def test_objets_hugging_face(self, mock_hf):
        mock_hf.return_value.object_detection.return_value = [SimpleNamespace(label='cake', score=0.92),
                                                               SimpleNamespace(label='person', score=0.88)]
        self.assertEqual(services.detect_objects(self.photo, self.user), ['gâteau', 'personne'])

    @override_settings(FACE_BACKEND='local')
    @patch('Face.services.get_local_object_detector')
    def test_objets_local(self, mock_local):
        mock_local.return_value.return_value = [{'label': 'cup', 'score': 0.9}]
        self.assertEqual(services.detect_objects(self.photo, self.user), ['tasse'])

    @override_settings(HF_TOKEN='')
    def test_jeton_absent(self):
        with self.assertRaisesMessage(AIServiceError, 'HF_TOKEN'):
            services.detect_objects(self.photo, self.user)


class FaceDetectionTest(FaceServicesTestBase):

    @patch('Face.services.get_face_detector')
    def test_detect_faces_remet_a_l_echelle(self, mock_detector):
        # Image 2560 x 1280 : réduite de moitié pour YuNet, cadres ramenés à la taille d'origine
        row = np.zeros(15, dtype=np.float32)
        row[:4] = [100, 50, 40, 40]
        row[14] = 0.93
        mock_detector.return_value.detect.return_value = (1, np.array([row]))
        faces = services.detect_faces(np.zeros((1280, 2560, 3), dtype=np.uint8))
        mock_detector.return_value.setInputSize.assert_called_once_with((1280, 640))
        self.assertEqual((faces[0]['x'], faces[0]['y'], faces[0]['width'], faces[0]['height']), (200, 100, 80, 80))
        self.assertAlmostEqual(faces[0]['score'], 0.93, places=2)

    @patch('Face.services.get_face_detector')
    def test_aucun_visage(self, mock_detector):
        mock_detector.return_value.detect.return_value = (1, None)
        self.assertEqual(services.detect_faces(np.zeros((100, 100, 3), dtype=np.uint8)), [])

    def test_modeles_absents(self):
        with patch('Face.services.MODELS_DIR', services.Path(MEDIA_TEST) / 'vide'):
            with self.assertRaisesMessage(AIServiceError, 'download_face_models'):
                services.get_face_detector()

    @patch('Face.services.face_feature', side_effect=[vector(0.99, 0.1, 0), vector(0, 0, 1)])
    @patch('Face.services.detect_faces', return_value=[detection(10, 10, 40, 40, 0.97), detection(120, 20, 40, 40, 0.88)])
    def test_analyse_cree_les_visages_et_propose_sarah(self, mock_detect, mock_feature):
        created = services.analyze_faces(self.photo, self.user)
        self.assertEqual(len(created), 2)
        self.assertEqual(created[0].person, self.sarah)          # « C'est Sarah ? »
        self.assertFalse(created[0].is_confirmed)
        self.assertIsNone(created[1].person)                      # inconnu
        self.assertEqual(created[1].confidence, 0.88)
        self.photo.refresh_from_db()
        self.assertTrue(self.photo.faces_analyzed)
        self.assertEqual(set(ActivityLog.objects.values_list('ai_service', flat=True)), {'YuNet', 'SFace'})

    @patch('Face.services.face_feature', return_value=vector(1, 0, 0))
    @patch('Face.services.detect_faces', return_value=[detection(10, 10, 40, 40), detection(120, 20, 40, 40)])
    def test_nouvelle_analyse_garde_les_visages_confirmes(self, mock_detect, mock_feature):
        confirmed = FaceTag.objects.create(photo=self.photo, person=self.sarah, x=12, y=12, width=40, height=40,
                                           confidence=0.9, is_confirmed=True)
        old_suggestion = FaceTag.objects.create(photo=self.photo, x=150, y=50, width=20, height=20, confidence=0.8)
        services.analyze_faces(self.photo, self.user)
        self.assertTrue(FaceTag.objects.filter(pk=confirmed.pk).exists())
        self.assertFalse(FaceTag.objects.filter(pk=old_suggestion.pk).exists())   # remplacée par la nouvelle analyse
        self.assertEqual(self.photo.face_tags.count(), 2)                         # le confirmé + le visage à x=120

    @patch('Face.services.detect_faces', return_value=[detection(10, 10, 40, 40)])
    def test_sans_proche_connu_pas_de_reconnaissance(self, mock_detect):
        Person.objects.update(face_encoding=None)
        created = services.analyze_faces(self.photo, self.user)
        self.assertIsNone(created[0].person)
        self.assertFalse(ActivityLog.objects.filter(ai_service='SFace').exists())

    @patch('Face.services.feature_for_tag', side_effect=[vector(1, 0), vector(0.8, 0.6)])
    @patch('Face.services.detect_faces', return_value=[])
    def test_empreinte_de_la_personne_moyenne_des_visages_confirmes(self, mock_detect, mock_feature):
        for x in (10, 100):
            FaceTag.objects.create(photo=self.photo, person=self.sarah, x=x, y=10, width=40, height=40, is_confirmed=True)
        FaceTag.objects.create(photo=self.photo, person=self.sarah, x=150, y=10, width=40, height=40)   # non confirmé
        self.assertEqual(services.update_person_encoding(self.sarah, self.user), 2)
        self.sarah.refresh_from_db()
        expected = vector(0.9, 0.3)
        np.testing.assert_allclose(self.sarah.face_encoding, expected, atol=1e-5)

    def test_empreinte_sans_visage_confirme(self):
        services.update_person_encoding(self.sarah, self.user)
        self.sarah.refresh_from_db()
        self.assertIsNone(self.sarah.face_encoding)

    @patch('Face.services.feature_for_tag', side_effect=[vector(1, 0), vector(0.99, 0.1), vector(0, 1)])
    @patch('Face.services.detect_faces', return_value=[])
    def test_groupes_de_visages_inconnus(self, mock_detect, mock_feature):
        tags = [FaceTag.objects.create(photo=self.photo, x=x, y=10, width=30, height=30) for x in (10, 60, 120)]
        FaceTag.objects.create(photo=self.photo, person=self.sarah, x=160, y=10, width=30, height=30)   # déjà nommé
        groups = services.group_unknown_faces(self.user)
        self.assertEqual(groups, [[tags[0].pk, tags[1].pk]])
        self.assertTrue(ActivityLog.objects.filter(ai_service='DBSCAN').exists())


class FaceTasksTest(FaceServicesTestBase):

    @patch('Face.services.analyze_faces', return_value=[])
    @patch('Face.services.detect_objects', return_value=['gâteau'])
    @patch('Face.services.caption_photo', return_value='Un gâteau sur une table')
    def test_analyse_complete(self, mock_caption, mock_objects, mock_faces):
        result = tasks.analyze_photo_task.delay(self.photo.pk, self.user.pk).get()
        self.photo.refresh_from_db()
        self.assertEqual((self.photo.caption, self.photo.detected_objects), ('Un gâteau sur une table', ['gâteau']))
        self.assertIn('alt="Un gâteau sur une table"', result['html'])
        self.assertIn('gâteau', result['html'])
        self.assertNotIn('csrfmiddlewaretoken', result['html'])     # pas de formulaire hors requête

    @patch('Face.services.analyze_faces', return_value=[])
    @patch('Face.services.detect_objects', return_value=[])
    @patch('Face.services.caption_photo')
    def test_legende_de_l_utilisateur_conservee(self, mock_caption, mock_objects, mock_faces):
        self.photo.caption = 'Ma légende'
        self.photo.save()
        tasks.analyze_photo_task.delay(self.photo.pk, self.user.pk).get()
        mock_caption.assert_not_called()

    @patch('Face.services.analyze_faces', return_value=[])
    @patch('Face.services.detect_objects', side_effect=AIServiceError('DETR indisponible'))
    @patch('Face.services.caption_photo', side_effect=AIServiceError('BLIP indisponible'))
    def test_echec_partiel_garde_les_visages(self, mock_caption, mock_objects, mock_faces):
        result = tasks.analyze_photo_task.delay(self.photo.pk, self.user.pk).get()
        self.assertIn('BLIP indisponible', result['html'])
        mock_faces.assert_called_once()

    @patch('Face.services.analyze_faces', side_effect=AIServiceError('Modèles absents'))
    @patch('Face.services.detect_objects', side_effect=AIServiceError('DETR indisponible'))
    @patch('Face.services.caption_photo', side_effect=AIServiceError('BLIP indisponible'))
    def test_echec_total(self, mock_caption, mock_objects, mock_faces):
        with self.assertRaisesMessage(AIServiceError, 'BLIP indisponible'):
            tasks.analyze_photo_task.delay(self.photo.pk, self.user.pk)

    def test_photo_d_un_autre_utilisateur(self):
        other = User.objects.create_user('amine', password=PASSWORD)
        with self.assertRaises(Photo.DoesNotExist):
            tasks.analyze_photo_task.delay(self.photo.pk, other.pk)

    @patch('Face.services.update_person_encoding', return_value=3)
    def test_mise_a_jour_empreinte(self, mock_update):
        result = tasks.update_person_encoding_task.delay(self.sarah.pk, self.user.pk).get()
        self.assertIn('3 visage(s)', result['message'])

    @patch('Face.services.group_unknown_faces', return_value=[[1, 2]])
    def test_regroupement(self, mock_group):
        self.assertEqual(tasks.group_unknown_faces_task.delay(self.user.pk).get()['groups'], [[1, 2]])


class FaceAIViewsTest(FaceServicesTestBase):

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('amine', password=PASSWORD)
        other_entry = JournalEntry.objects.create(user=self.other, title='Secret', content='...')
        self.other_photo = Photo.objects.create(entry=other_entry, image='photos/x.png')
        self.client.login(username='sabri', password=PASSWORD)

    @patch('Face.services.analyze_faces', return_value=[])
    @patch('Face.services.detect_objects', return_value=['table'])
    @patch('Face.services.caption_photo', return_value='Une table')
    def test_upload_lance_l_analyse(self, mock_caption, mock_objects, mock_faces):
        image = ContentFile(png(), name='nouvelle.png')
        image.content_type = 'image/png'
        response = self.client.post(reverse('face:photo_create'), {'entry': self.entry.pk, 'image': image})
        photo = Photo.objects.latest('id')
        self.assertIn(f'/face/photos/{photo.pk}/?task=', response.url)
        photo.refresh_from_db()
        self.assertEqual(photo.caption, 'Une table')
        page = self.client.get(response.url)
        self.assertContains(page, 'data-result-target="#analyse-photo"')

    @patch('Face.services.analyze_faces', return_value=[])
    @patch('Face.services.detect_objects', return_value=[])
    @patch('Face.services.caption_photo', return_value='Légende')
    def test_analyser_a_nouveau(self, mock_caption, mock_objects, mock_faces):
        response = self.client.post(reverse('face:photo_analyze', args=[self.photo.pk]))
        self.assertIn('?task=', response.url)

    def test_analyser_la_photo_d_un_autre_404(self):
        self.assertEqual(self.client.post(reverse('face:photo_analyze', args=[self.other_photo.pk])).status_code, 404)

    @patch('Face.tasks.update_person_encoding_task.delay')
    def test_c_est_sarah_oui(self, mock_encoding):
        face = FaceTag.objects.create(photo=self.photo, person=self.sarah, x=1, y=1, width=10, height=10)
        page = self.client.get(reverse('face:photo_detail', args=[self.photo.pk]))
        self.assertContains(page, "C'est Sarah ?")
        self.client.post(reverse('face:face_answer', args=[face.pk]), {'reponse': 'oui'})
        face.refresh_from_db()
        self.assertTrue(face.is_confirmed)
        mock_encoding.assert_called_once_with(self.sarah.pk, self.user.pk)

    @patch('Face.tasks.update_person_encoding_task.delay')
    def test_c_est_sarah_non(self, mock_encoding):
        face = FaceTag.objects.create(photo=self.photo, person=self.sarah, x=1, y=1, width=10, height=10)
        self.client.post(reverse('face:face_answer', args=[face.pk]), {'reponse': 'non'})
        face.refresh_from_db()
        self.assertIsNone(face.person)

    def test_repondre_pour_le_visage_d_un_autre_404(self):
        other_person = Person.objects.create(user=self.other, name='Autre')
        face = FaceTag.objects.create(photo=self.other_photo, person=other_person, x=1, y=1, width=5, height=5)
        self.assertEqual(self.client.post(reverse('face:face_answer', args=[face.pk]), {'reponse': 'oui'}).status_code, 404)

    @patch('Face.tasks.update_person_encoding_task.delay')
    def test_identifier_met_a_jour_les_empreintes(self, mock_encoding):
        karim = Person.objects.create(user=self.user, name='Karim')
        face = FaceTag.objects.create(photo=self.photo, person=self.sarah, x=1, y=1, width=10, height=10)
        self.client.post(reverse('face:face_update', args=[face.pk]),
                         {'person': karim.pk, 'x': 1, 'y': 1, 'width': 10, 'height': 10, 'is_confirmed': 'on'})
        updated = {call.args[0] for call in mock_encoding.call_args_list}
        self.assertEqual(updated, {self.sarah.pk, karim.pk})       # l'ancienne et la nouvelle personne

    def test_page_groupes(self):
        response = self.client.get(reverse('face:face_groups'))
        self.assertContains(response, 'Regrouper les visages inconnus')

    @patch('Face.services.group_unknown_faces')
    def test_lancer_le_regroupement(self, mock_group):
        mock_group.return_value = []
        response = self.client.post(reverse('face:face_groups'))
        self.assertIn('?task=', response.url)

    @patch('Face.views.AsyncResult')
    def test_afficher_les_groupes(self, mock_result):
        tags = [FaceTag.objects.create(photo=self.photo, x=x, y=10, width=30, height=30) for x in (10, 60)]
        other_tag = FaceTag.objects.create(photo=self.other_photo, x=1, y=1, width=5, height=5)
        mock_result.return_value.state = 'SUCCESS'
        mock_result.return_value.result = {'groups': [[tags[0].pk, tags[1].pk], [other_tag.pk, tags[0].pk]]}
        response = self.client.get(reverse('face:face_groups'), {'task': 'abc'})
        self.assertEqual(len(response.context['groups']), 1)       # le groupe avec le visage d'un autre est ignoré
        self.assertContains(response, 'Nommer ce groupe')
        self.assertContains(response, 'background-image')

    @patch('Face.tasks.update_person_encoding_task.delay')
    def test_nommer_un_groupe_nouveau_nom(self, mock_encoding):
        tags = [FaceTag.objects.create(photo=self.photo, x=x, y=10, width=30, height=30) for x in (10, 60)]
        other_tag = FaceTag.objects.create(photo=self.other_photo, x=1, y=1, width=5, height=5)
        response = self.client.post(reverse('face:face_group_name'),
                                    {'faces': [tags[0].pk, tags[1].pk, other_tag.pk], 'nouveau_nom': 'Karim'})
        karim = Person.objects.get(user=self.user, name='Karim')
        self.assertRedirects(response, reverse('face:person_detail', args=[karim.pk]))
        self.assertEqual(FaceTag.objects.filter(person=karim, is_confirmed=True).count(), 2)
        other_tag.refresh_from_db()
        self.assertIsNone(other_tag.person)                         # le visage d'un autre n'est pas touché

    @patch('Face.tasks.update_person_encoding_task.delay')
    def test_nommer_un_groupe_proche_existant(self, mock_encoding):
        tag = FaceTag.objects.create(photo=self.photo, x=10, y=10, width=30, height=30)
        self.client.post(reverse('face:face_group_name'), {'faces': [tag.pk], 'personne': self.sarah.pk})
        tag.refresh_from_db()
        self.assertEqual(tag.person, self.sarah)

    def test_nommer_sans_choix(self):
        response = self.client.post(reverse('face:face_group_name'), {'faces': []}, follow=True)
        self.assertContains(response, 'Choisissez une personne')

    def test_nommer_avec_le_proche_d_un_autre_404(self):
        other_person = Person.objects.create(user=self.other, name='Autre')
        tag = FaceTag.objects.create(photo=self.photo, x=10, y=10, width=30, height=30)
        response = self.client.post(reverse('face:face_group_name'), {'faces': [tag.pk], 'personne': other_person.pk})
        self.assertEqual(response.status_code, 404)


class DownloadFaceModelsTest(TestCase):

    def setUp(self):
        self.folder = services.Path(tempfile.mkdtemp())
        patcher = patch('Face.management.commands.download_face_models.models_dir', return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.folder, True)

    @patch('Face.management.commands.download_face_models.urllib.request.urlopen')
    def test_telechargement(self, mock_open):
        mock_open.return_value.__enter__.return_value.read.return_value = b'0' * 200_000
        from django.core.management import call_command
        call_command('download_face_models', stdout=io.StringIO())
        self.assertEqual(len(list(self.folder.glob('*.onnx'))), 2)

    @patch('Face.management.commands.download_face_models.urllib.request.urlopen')
    def test_fichier_trop_petit_refuse(self, mock_open):
        mock_open.return_value.__enter__.return_value.read.return_value = b'<html>erreur</html>'
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, 'trop petit'):
            call_command('download_face_models', stdout=io.StringIO())

    @patch('Face.management.commands.download_face_models.urllib.request.urlopen', side_effect=OSError('réseau'))
    def test_reseau_coupe(self, mock_open):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaisesMessage(CommandError, 'Relancez la commande'):
            call_command('download_face_models', stdout=io.StringIO())
