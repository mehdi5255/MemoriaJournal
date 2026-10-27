# Tests des métriques et de la commande evaluate_memory (aucun appel IA réel)
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from PIL import Image

from Memory import metrics
from Memory.services import AIServiceError


class NormalizeAndFactsTest(SimpleTestCase):

    def test_normalize(self):
        self.assertEqual(metrics.normalize('Grand-Mère à Sfax'), 'grand-mere a sfax')
        self.assertEqual(metrics.normalize('Sœur'), 'soeur')

    def test_fact_recall(self):
        story = "Avec ma grand-mère, nous avons mangé un couscous au poisson."
        recall, missing = metrics.fact_recall(story, [['grand-mere', 'mamie'], ['couscous'], ['sfax']])
        self.assertAlmostEqual(recall, 2 / 3)
        self.assertEqual(missing, ['sfax'])

    def test_inventions(self):
        self.assertEqual(metrics.inventions_found("Il pleuvait, mon chien aboyait.", ['chien', 'plage']), ['chien'])

    def test_word_count(self):
        self.assertEqual(metrics.word_count("Aujourd'hui, ma grand-mère est venue."), 5)


class ScoresTest(SimpleTestCase):

    def test_length_score(self):
        self.assertEqual(metrics.length_score(150), 5)
        self.assertEqual(metrics.length_score(250), 5)
        self.assertEqual(metrics.length_score(270), 4)        # 8 % de trop
        self.assertEqual(metrics.length_score(300), 3)        # 20 % de trop : 2e tranche
        self.assertEqual(metrics.length_score(330), 2)        # 32 % de trop : 3e tranche
        self.assertEqual(metrics.length_score(20), 1)

    def test_recall_to_score(self):
        self.assertEqual(metrics.recall_to_score(1.0), 5)
        self.assertEqual(metrics.recall_to_score(0.5), 3)
        self.assertEqual(metrics.recall_to_score(1.0, inventions=2), 3)
        self.assertEqual(metrics.recall_to_score(0.0, inventions=3), 1)

    @patch('Memory.metrics.call_gemini', return_value='Voici : {"style": 4, "commentaire": "Assez poétique."}')
    def test_judge_style(self, mock_gemini):
        self.assertEqual(metrics.judge_style('texte', 'récit', 'Poetique'), (4, 'Assez poétique.'))

    @patch('Memory.metrics.call_gemini', return_value='{"style": 9}')
    def test_judge_style_borne_a_5(self, mock_gemini):
        self.assertEqual(metrics.judge_style('t', 'r', 'Simple')[0], 5)

    @patch('Memory.metrics.call_gemini', return_value='Je ne sais pas.')
    def test_judge_sans_json(self, mock_gemini):
        with self.assertRaises(AIServiceError):
            metrics.judge_style('t', 'r', 'Simple')

    def test_time_summary(self):
        self.assertEqual(metrics.time_summary([100, 300]), {'moyenne_ms': 200, 'max_ms': 300, 'n': 2})
        self.assertEqual(metrics.time_summary([]), {'moyenne_ms': 0, 'max_ms': 0, 'n': 0})


class PrecisionAtKTest(SimpleTestCase):

    themes = [['plage', 'mer'], ['famille', 'cousin'], ['bateau']]

    def test_keyword_matches(self):
        self.assertTrue(metrics.keyword_matches('baignade mer', ['plage', 'mer']))
        self.assertTrue(metrics.keyword_matches('cousins', ['famille', 'cousin']))
        self.assertTrue(metrics.keyword_matches('Révisions', ['revision']))
        self.assertFalse(metrics.keyword_matches('coup de soleil', ['plage', 'mer']))

    def test_precision_souple(self):
        keywords = ['plage hammamet', 'cousins', 'coup soleil', 'glace', 'sable']
        precision, recall, hits = metrics.precision_recall_at_k(keywords, self.themes, k=5, strict=False)
        self.assertEqual(precision, 0.4)
        self.assertAlmostEqual(recall, 2 / 3)
        self.assertEqual(hits, ['plage hammamet', 'cousins'])

    def test_precision_stricte(self):
        # « plage hammamet » : « hammamet » n'est pas un thème annoté, donc le mot-clé est refusé
        keywords = ['plage hammamet', 'cousins', 'mer bateau', 'glace', 'sable']
        precision, recall, hits = metrics.precision_recall_at_k(keywords, self.themes, k=5)
        self.assertEqual(hits, ['cousins', 'mer bateau'])
        self.assertEqual(precision, 0.4)
        self.assertEqual(recall, 1.0)

    def test_mots_vides_ignores(self):
        self.assertEqual(metrics.content_tokens('Révisions toute la journée'), ['revisions'])
        self.assertTrue(metrics.keyword_correct('révisions toute', [['revision']]))


class EvaluateMemoryCommandTest(TestCase):
    """La commande complète avec toutes les IA mockées et un petit --limit."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    @patch('Memory.metrics.clip_score', side_effect=[31.0, 18.0, 29.0, 20.0])
    @patch('Memory.services.create_image')
    @patch('Memory.services.call_keybert', return_value=['plage', 'bateau dauphins', 'cousins', 'soleil', 'glace'])
    @patch('Memory.metrics.call_gemini', return_value='{"style": 4, "commentaire": "Bien."}')
    @patch('Memory.services.call_gemini')
    def test_rapport_complet(self, mock_story, mock_judge, mock_keybert, mock_image, mock_clip):
        mock_story.return_value = ("Journée à la plage de Hammamet avec Sarah et Karim. On a nagé puis mangé un "
                                   "sandwich sous le parasol. Coup de soleil en rentrant. " * 4)

        def fake_image(user, prompt):
            from Account.models import ActivityLog
            ActivityLog.objects.create(user=user, action='Illustration du récit', ai_service='SD-Turbo (local)')
            return Image.new('RGB', (64, 64), 'blue')
        mock_image.side_effect = fake_image

        out = StringIO()
        call_command('evaluate_memory', '--limit', '2', '--pause', '0', '--output', str(self.out), stdout=out)

        results = json.loads((self.out / 'memory_results.json').read_text(encoding='utf-8'))
        self.assertEqual(results['recits']['reussis'], 2)
        self.assertEqual(results['recits']['lignes'][0]['rappel_faits'], 1.0)
        self.assertEqual(results['images']['clip_moyen'], 30.0)
        self.assertEqual(results['images']['meilleur_que_hasard'], 2)
        self.assertEqual(results['retros']['lignes'][0]['precision_5'], 0.8)
        report = (self.out / 'memory.md').read_text(encoding='utf-8')
        for title in ('grille qualitative', 'CLIP score', 'Precision@5'):
            self.assertIn(title, report)
        for chart in ('memory_recits.png', 'memory_clip.png', 'memory_keybert.png', 'memory_recits.csv'):
            self.assertTrue((self.out / chart).exists(), chart)
        # L'utilisateur temporaire et ses données sont supprimés
        self.assertFalse(User.objects.filter(username='evaluation_memory').exists())

    @patch('Memory.services.call_gemini', side_effect=AIServiceError('Quota dépassé'))
    def test_echec_de_l_api_n_arrete_pas_l_evaluation(self, mock_gemini):
        call_command('evaluate_memory', '--only', 'recits', '--limit', '2', '--pause', '0',
                     '--output', str(self.out), stdout=StringIO(), stderr=StringIO())
        results = json.loads((self.out / 'memory_results.json').read_text(encoding='utf-8'))
        self.assertEqual(results['recits']['reussis'], 0)
        self.assertEqual(results['recits']['lignes'][0]['erreur'], 'Quota dépassé')
        self.assertIn('Aucun récit généré', (self.out / 'memory.md').read_text(encoding='utf-8'))

    @patch('Memory.services.call_keybert', return_value=['a', 'b', 'c', 'd', 'e'])
    def test_les_resultats_precedents_sont_conserves(self, mock_keybert):
        (self.out / 'memory_results.json').write_text(json.dumps({'images': {'n': 0, 'reussis': 0}}), encoding='utf-8')
        call_command('evaluate_memory', '--only', 'retros', '--limit', '1', '--output', str(self.out), stdout=StringIO())
        results = json.loads((self.out / 'memory_results.json').read_text(encoding='utf-8'))
        self.assertIn('images', results)
        self.assertIn('retros', results)
