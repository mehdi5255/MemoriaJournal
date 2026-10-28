# Tests des métriques et de la commande evaluate_reminder (aucun appel IA réel)
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
from Reminder import metrics


class TimeMetricsTest(SimpleTestCase):

    def test_erreur_autour_de_minuit(self):
        self.assertEqual(metrics.minutes_error(metrics.to_minutes('23:50'), metrics.to_minutes('00:10')), 20)
        self.assertEqual(metrics.minutes_error(600, 630), 30)

    def test_moyenne_simple_se_trompe_a_minuit(self):
        minutes = [metrics.to_minutes(h) for h in ('23:50', '00:10')]
        self.assertEqual(metrics.mean_baseline(minutes), 720)                 # midi !
        self.assertIn(metrics.circular_mean(minutes), (0, 1439))              # minuit

    def test_mediane_et_mae(self):
        self.assertEqual(metrics.median_baseline([60, 120, 600]), 120)
        self.assertEqual(metrics.mae([10, 20]), 15)
        self.assertEqual(metrics.mae([]), 0)


class EntityMetricsTest(SimpleTestCase):

    def test_normalisation(self):
        self.assertEqual(metrics.normalize("Leïla"), 'leila')
        self.assertEqual(metrics.normalize("Tunis-Carthage"), 'tunis carthage')

    def test_alignement_strict(self):
        expected = [('PER', 'Sarah'), ('LOC', 'Tunis'), ('ORG', 'STEG')]
        predicted = [('PER', 'Sarah'), ('PER', 'Tunis'), ('MISC', 'Ramadan')]
        y_true, y_pred = metrics.align(expected, predicted)
        # Sarah juste, Tunis mal typé, STEG oubliée, Ramadan inventé
        self.assertEqual(y_true, ['PER', 'LOC', 'ORG', 'Aucune'])
        self.assertEqual(y_pred, ['PER', 'PER', 'Aucune', 'MISC'])

    def test_alignement_partiel(self):
        expected = [('LOC', 'lac de Tunis')]
        self.assertEqual(metrics.align(expected, [('LOC', 'Tunis')]), (['LOC', 'Aucune'], ['Aucune', 'LOC']))
        self.assertEqual(metrics.align(expected, [('LOC', 'Tunis')], partial=True), (['LOC'], ['LOC']))


class EvaluateReminderCommandTest(TestCase):

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    @patch('Reminder.services.call_hf_ner')
    def test_rapport_complet(self, mock_ner):
        # Le modèle trouve toujours « Sarah » (personne) et « Tunis » (lieu)
        mock_ner.return_value = [{'type': 'PER', 'text': 'Sarah', 'score': 0.99},
                                 {'type': 'LOC', 'text': 'Tunis', 'score': 0.7}]
        call_command('evaluate_reminder', '--limit', '3', '--output', str(self.out), stdout=StringIO())

        results = json.loads((self.out / 'reminder_results.json').read_text(encoding='utf-8'))
        hours = results['heure']
        self.assertEqual(hours['n'], 3)
        self.assertLess(hours['mae']['kmeans'], 15)
        self.assertEqual(set(hours['mae']), {'kmeans', 'moyenne_circulaire', 'mediane', 'moyenne'})
        entities = results['entites']
        self.assertEqual(entities['reussis'], 3)
        # Phrase 1 : Sarah juste ; Tunis (0.7) retiré par le seuil 0.8
        self.assertEqual(entities['lignes'][0]['predites'], [['PER', 'Sarah']])
        self.assertEqual(entities['strict']['par_type']['PER']['precision'], round(1 / 3, 2))
        self.assertEqual(len(entities['strict']['matrice']), 5)
        self.assertEqual(len(entities['par_seuil']), len(metrics.THRESHOLDS))
        report = (self.out / 'reminder.md').read_text(encoding='utf-8')
        for title in ('MAE', 'Precision', 'Matrice de confusion', 'Influence du seuil'):
            self.assertIn(title, report)
        for name in ('reminder_heure.png', 'reminder_confusion.png', 'reminder_seuil.png'):
            self.assertTrue((self.out / name).exists(), name)
        self.assertFalse(User.objects.filter(username='evaluation_reminder').exists())

    @patch('Reminder.services.call_hf_ner', side_effect=AIServiceError('Le service ne répond pas.'))
    def test_echec_de_l_api_n_arrete_pas_l_evaluation(self, mock_ner):
        call_command('evaluate_reminder', '--only', 'entites', '--limit', '2', '--output', str(self.out),
                     stdout=StringIO())
        results = json.loads((self.out / 'reminder_results.json').read_text(encoding='utf-8'))
        self.assertEqual(results['entites']['reussis'], 0)
        self.assertEqual(len(results['entites']['erreurs']), 2)
        self.assertIn('Aucune phrase analysée', (self.out / 'reminder.md').read_text(encoding='utf-8'))
