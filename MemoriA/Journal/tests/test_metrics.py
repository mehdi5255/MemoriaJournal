# Tests des métriques et de la commande evaluate_journal (aucun modèle réel)
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from Account.services import AIServiceError
from Journal import metrics
from Journal.models import JournalEntry


class SearchMetricsTest(SimpleTestCase):

    def test_precision_at_3(self):
        self.assertAlmostEqual(metrics.precision_at_k([5, 6, 7], {5, 7}), 2 / 3)
        self.assertEqual(metrics.precision_at_k([], {5}), 0)

    def test_maximum_atteignable(self):
        self.assertAlmostEqual(metrics.best_precision_at_k({5}), 1 / 3)
        self.assertEqual(metrics.best_precision_at_k({1, 2, 3, 4}), 1)

    def test_mrr_et_hit(self):
        self.assertEqual(metrics.reciprocal_rank([9, 5], {5}), 0.5)
        self.assertEqual(metrics.reciprocal_rank([9, 8], {5}), 0)
        self.assertEqual(metrics.hit_at_k([9, 8, 5, 1], {5}), 1)
        self.assertEqual(metrics.hit_at_k([9, 8, 7, 5], {5}), 0)

    def test_recherche_par_mots(self):
        documents = {1: 'Journée à la plage', 2: 'Examen de maths', 3: 'Plage et soleil à Djerba'}
        self.assertEqual(metrics.keyword_ranking('la plage au soleil', documents), [3, 1])
        self.assertEqual(metrics.keyword_ranking('montagne', documents), [])
        self.assertEqual(metrics.words("J'étais à l'Université"), {'etais', 'universite'})


class TagMetricsTest(SimpleTestCase):

    def test_prediction_et_scores_micro(self):
        self.assertEqual(metrics.predict_tags({'Voyage': 0.7, 'Sport': 0.2}, 0.3), ['Voyage'])
        precision, recall, f1 = metrics.micro_scores([{'Voyage'}, {'Sport', 'Amis'}], [{'Voyage', 'Amis'}, {'Sport'}])
        self.assertAlmostEqual(precision, 2 / 3)
        self.assertAlmostEqual(recall, 2 / 3)
        self.assertAlmostEqual(f1, 2 / 3)

    def test_meilleur_seuil(self):
        scores = [{'Voyage': 0.8, 'Sport': 0.42}, {'Sport': 0.9, 'Voyage': 0.41}]
        best, results = metrics.best_threshold(scores, [{'Voyage'}, {'Sport'}])
        self.assertEqual(best, 0.8)                     # le plus haut seuil qui garde un F1 parfait
        self.assertEqual(len(results), len(metrics.THRESHOLDS))

    def test_time_summary(self):
        self.assertEqual(metrics.time_summary([10, 30]), {'moyenne_ms': 20, 'max_ms': 30, 'n': 2})
        self.assertEqual(metrics.time_summary([])['n'], 0)


def fake_encode(texts):
    """« plage », « sable » et « mer » se ressemblent ; le reste va dans une autre direction."""
    return [[1.0, 0.0] if any(w in t.lower() for w in ('plage', 'sable', 'mer', 'eau')) else [0.0, 1.0]
            for t in texts]


class EvaluateJournalCommandTest(TestCase):

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    @patch('Journal.services.call_hf_zero_shot')
    @patch('Journal.services.encode', side_effect=fake_encode)
    def test_rapport_complet(self, mock_encode, mock_zero_shot):
        # Le modèle met toujours « Voyage » en tête
        mock_zero_shot.side_effect = lambda text, labels, multi_label: {
            label: (0.9 if label == 'Voyage' else 0.01) for label in labels}
        call_command('evaluate_journal', '--limit', '2', '--output', str(self.out), stdout=StringIO())

        results = json.loads((self.out / 'journal_results.json').read_text(encoding='utf-8'))
        search = results['recherche']
        self.assertEqual(search['reussis'], 2)
        self.assertEqual(search['lignes'][0]['rr'], 1.0)            # « une journée à la plage » -> entrée 1
        self.assertIn('mots_mrr', search)
        tags = results['etiquettes']
        self.assertEqual(tags['reussis'], 2)                         # 2 textes « Voyage »
        self.assertEqual(tags['par_etiquette']['Voyage']['recall'], 1.0)
        self.assertEqual(len(tags['matrice']), 8)
        self.assertIn('autre_mode', tags)
        report = (self.out / 'journal.md').read_text(encoding='utf-8')
        for title in ('Precision@3', 'MRR', 'Matrice de confusion', 'Choix du seuil'):
            self.assertIn(title, report)
        for name in ('journal_recherche.png', 'journal_confusion.png', 'journal_seuil.png'):
            self.assertTrue((self.out / name).exists(), name)
        # L'utilisateur temporaire et ses entrées sont supprimés
        self.assertFalse(User.objects.filter(username='evaluation_journal').exists())
        self.assertEqual(JournalEntry.objects.count(), 0)

    @patch('Journal.services.call_hf_zero_shot', side_effect=AIServiceError('Le service ne répond pas.'))
    @patch('Journal.services.encode', side_effect=AIServiceError('Modèle absent.'))
    def test_echec_n_arrete_pas_l_evaluation(self, mock_encode, mock_zero_shot):
        call_command('evaluate_journal', '--limit', '2', '--output', str(self.out), stdout=StringIO())
        results = json.loads((self.out / 'journal_results.json').read_text(encoding='utf-8'))
        self.assertEqual(results['recherche']['reussis'], 0)
        self.assertEqual(results['etiquettes']['reussis'], 0)
        report = (self.out / 'journal.md').read_text(encoding='utf-8')
        self.assertIn('Aucune requête évaluée', report)
        self.assertIn('Aucun texte analysé', report)
