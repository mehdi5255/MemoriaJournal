# Tests des métriques et de la commande evaluate_face (LFW et modèles IA simulés)
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import numpy as np
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from Face import metrics


def face(x, y, w=40, h=40):
    return {'x': x, 'y': y, 'width': w, 'height': h, 'score': 0.9, 'row': np.zeros(15)}


TILES = [{'x': 0, 'y': 0, 'size': 100, 'visage': True}, {'x': 100, 'y': 0, 'size': 100, 'visage': False}]


class ObjectScoresTest(SimpleTestCase):

    def test_precision_rappel(self):
        r = metrics.object_scores(['chat', 'Canapé', 'chien'], ['chat', 'canape', 'plante'])
        self.assertEqual(r['corrects'], ['canape', 'chat'])
        self.assertAlmostEqual(r['precision'], 2 / 3)
        self.assertAlmostEqual(r['rappel'], 2 / 3)
        self.assertEqual((r['en_trop'], r['oublies']), (['chien'], ['plante']))

    def test_rien_propose(self):
        self.assertEqual(metrics.object_scores([], ['chat'])['precision'], 1.0)


class DetectionCountsTest(SimpleTestCase):

    def test_un_visage_trouve(self):
        self.assertEqual(metrics.detection_counts([face(30, 30)], TILES), (1, 0, 0, 0))

    def test_visage_oublie(self):
        self.assertEqual(metrics.detection_counts([], TILES), (0, 0, 0, 1))

    def test_fausse_alerte_dans_une_scene(self):
        self.assertEqual(metrics.detection_counts([face(30, 30), face(130, 30)], TILES), (1, 0, 1, 0))

    def test_visage_d_arriere_plan_verifie(self):
        detections = [face(10, 10), face(50, 50, 20, 20)]
        self.assertEqual(metrics.detection_counts(detections, TILES), (1, 0, 1, 0))                # strict
        self.assertEqual(metrics.detection_counts(detections, TILES, secondary=1), (1, 1, 0, 0))  # corrigé

    def test_precision_recall(self):
        self.assertEqual(metrics.precision_recall(8, 2, 0), (0.8, 1.0, 2 * 0.8 / 1.8))
        self.assertEqual(metrics.precision_recall(0, 0, 0)[:2], (1.0, 1.0))


class IdentificationTest(SimpleTestCase):

    def test_moyenne_normalisee(self):
        np.testing.assert_allclose(metrics.mean_encoding([np.array([1.0, 0]), np.array([0, 1.0])]),
                                   np.array([1, 1]) / np.sqrt(2))

    def test_identifier(self):
        refs = {'Sarah': np.array([1.0, 0]), 'Karim': np.array([0, 1.0])}
        self.assertEqual(metrics.identify(np.array([0.9, 0.1]), refs, 0.363)[0], 'Sarah')
        self.assertEqual(metrics.identify(np.array([-1.0, -1.0]), refs, 0.363)[0], metrics.UNKNOWN)


class EvaluateFaceCommandTest(TestCase):
    """La commande complète avec un faux LFW (vecteurs simples) et des modèles simulés."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, True)
        names = ['Colin Powell', 'Tony Blair', 'Donald Rumsfeld', 'Gerhard Schroeder', 'Ariel Sharon',
                 'Hugo Chavez', 'Junichiro Koizumi', 'Jean Chretien', 'John Ashcroft', 'Jacques Chirac',
                 'George W Bush', 'Serena Williams', 'Vladimir Putin', 'Luiz Inacio Lula da Silva']
        # Chaque « photo » est une petite image dont le pixel [0, 0] dit à qui elle appartient
        self.people = {}
        for k, name in enumerate(names):
            self.people[name] = [np.full((250, 250, 3), k, dtype=np.uint8) for _ in range(12)]

        def fake_feature(image):
            k = int(image[0, 0, 0])
            v = np.zeros(len(names))
            v[k] = 1.0
            return v

        patches = [
            patch('Face.management.commands.evaluate_face.load_lfw', return_value=self.people),
            patch('Face.management.commands.evaluate_face.Command.main_feature', side_effect=lambda self_, img: fake_feature(img), autospec=True),
            patch('Face.services.detect_faces', return_value=[face(60, 60, 100, 100)]),
            patch('Face.services.call_hf_objects', return_value=[{'label': 'cat', 'score': 0.95}]),
            patch('Face.services.call_hf_caption', return_value='a cat on a sofa'),
            patch('Face.services.translate_to_french', return_value='un chat sur un canapé'),
            patch('Memory.metrics.clip_score', return_value=30.0),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_rapport_complet(self):
        call_command('evaluate_face', '--output', str(self.out), stdout=StringIO())
        results = json.loads((self.out / 'face_results.json').read_text(encoding='utf-8'))

        ident = results['identification']
        self.assertEqual(ident['accuracy'], 1.0)                 # vecteurs parfaitement séparés
        self.assertEqual(ident['inconnus_rejetes'], 1.0)
        self.assertEqual(len(ident['matrice']), 6)
        self.assertEqual(results['detection']['n'], 20)
        self.assertEqual(results['objets']['lignes'][0]['corrects'], ['chat'])
        self.assertEqual(results['legendes']['clip_moyen'], 30.0)
        report = (self.out / 'face.md').read_text(encoding='utf-8')
        for title in ('Precision', 'Recall', 'matrice de confusion', 'Objets détectés', 'Légendes'):
            self.assertIn(title, report)
        for name in ('face_confusion.png', 'face_seuil.png', 'face_legendes.csv'):
            self.assertTrue((self.out / name).exists(), name)
        self.assertFalse(User.objects.filter(username='evaluation_face').exists())

    def test_une_seule_partie(self):
        call_command('evaluate_face', '--only', 'objets', '--output', str(self.out), stdout=StringIO())
        results = json.loads((self.out / 'face_results.json').read_text(encoding='utf-8'))
        self.assertEqual(set(results) - {'date'}, {'objets'})
