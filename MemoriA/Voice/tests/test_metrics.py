# Tests des métriques et de la commande evaluate_voice (aucun appel IA réel)
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from Account.services import AIServiceError
from Voice import metrics


def scores(label, value):
    """Sortie du modèle : la classe donnée avec la confiance donnée, le reste partagé."""
    others = [l for l in ('joy', 'sadness', 'anger', 'fear') if l != label]
    rest = round((1 - value) / 3, 3)
    return [{'label': label, 'score': value}] + [{'label': l, 'score': rest} for l in others]


class WerTest(SimpleTestCase):

    def test_normalisation(self):
        self.assertEqual(metrics.normalize_for_wer("J'ai vu Sarah, à la plage !"), 'j ai vu sarah à la plage')

    def test_transcription_parfaite(self):
        self.assertEqual(metrics.word_error_rate("Il fait beau.", "il fait beau"), 0)

    def test_une_erreur_sur_quatre_mots(self):
        self.assertEqual(metrics.word_error_rate("nous avons beaucoup ri", "nous avons beaucoup riz"), 0.25)

    def test_details(self):
        details = metrics.wer_details("le chat dort ici", "le chien dort")
        self.assertEqual(details, {'substitutions': 1, 'suppressions': 1, 'insertions': 0, 'mots_reference': 4})


class ThresholdTest(SimpleTestCase):

    def test_predict(self):
        self.assertEqual(metrics.predict(scores('joy', 0.9), 0.5), 'Joie')
        self.assertEqual(metrics.predict(scores('anger', 0.45), 0.5), 'Neutre')
        self.assertEqual(metrics.predict(scores('anger', 0.45), 0.4), 'Colere')

    def test_meilleur_seuil(self):
        # Les phrases neutres ont une confiance de 0.6 : il faut un seuil au-dessus pour les reconnaître
        data = [scores('joy', 0.95), scores('sadness', 0.9), scores('joy', 0.6), scores('fear', 0.6)]
        truth = ['Joie', 'Tristesse', 'Neutre', 'Neutre']
        best, results = metrics.best_threshold(data, truth)
        self.assertEqual(best, 0.65)                      # le plus petit seuil qui sépare les deux groupes
        self.assertEqual(len(results), len(metrics.THRESHOLDS))

    def test_seuil_qui_casse_les_vraies_emotions_refuse(self):
        # 10 vraies émotions à 0.62 et 1 phrase neutre à 0.9 : un seuil de 0.95 trouverait la neutre
        # mais rendrait « Neutre » toutes les vraies émotions -> refusé par l'objectif de 90 %
        data = [scores('joy', 0.62)] * 10 + [scores('joy', 0.9)]
        truth = ['Joie'] * 10 + ['Neutre']
        best, results = metrics.best_threshold(data, truth)
        self.assertLessEqual(best, 0.6)
        accuracy = dict((t, a) for t, f, a in results)
        self.assertEqual(accuracy[0.95], 0)

    def test_time_summary(self):
        self.assertEqual(metrics.time_summary([10, 30]), {'moyenne_ms': 20, 'max_ms': 30, 'n': 2})
        self.assertEqual(metrics.time_summary([])['n'], 0)


class EvaluateVoiceCommandTest(TestCase):

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.out, ignore_errors=True)

    @patch('Voice.services.call_hf_emotion')
    @patch('Voice.services.call_hf_whisper')
    def test_rapport_complet(self, mock_whisper, mock_emotion):
        mock_whisper.return_value = "Aujourd'hui je suis allé à la plage avec mes amis. Il faisait très beau."
        mock_emotion.return_value = scores('joy', 0.97)
        call_command('evaluate_voice', '--limit', '2', '--output', str(self.out), stdout=StringIO())

        results = json.loads((self.out / 'voice_results.json').read_text(encoding='utf-8'))
        transcription = results['transcription']
        self.assertEqual(transcription['reussis'], 2)
        self.assertGreater(transcription['lignes'][0]['wer'], 0)            # 6 mots manquants
        self.assertEqual(transcription['langue_correcte'], 2)
        emotions = results['emotions']
        self.assertEqual(emotions['reussis'], 2)                            # 2 phrases « Joie »
        self.assertEqual(emotions['par_classe']['Joie']['recall'], 1.0)
        self.assertEqual(len(emotions['matrice']), 6)
        report = (self.out / 'voice.md').read_text(encoding='utf-8')
        for title in ('WER', 'Precision', 'Matrice de confusion', 'seuil'):
            self.assertIn(title, report)
        for name in ('voice_confusion.png', 'voice_seuil.png', 'voice_wer.png'):
            self.assertTrue((self.out / name).exists(), name)
        self.assertFalse(User.objects.filter(username='evaluation_voice').exists())

    @patch('Voice.services.call_hf_whisper', side_effect=AIServiceError('Le service de transcription ne répond pas.'))
    def test_echec_de_l_api_n_arrete_pas_l_evaluation(self, mock_whisper):
        call_command('evaluate_voice', '--only', 'transcription', '--limit', '2', '--output', str(self.out),
                     stdout=StringIO(), stderr=StringIO())
        results = json.loads((self.out / 'voice_results.json').read_text(encoding='utf-8'))
        self.assertEqual(results['transcription']['reussis'], 0)
        self.assertIn('ne répond pas', results['transcription']['lignes'][0]['erreur'])
        self.assertIn('Aucune transcription réussie', (self.out / 'voice.md').read_text(encoding='utf-8'))

    @patch('Voice.management.commands.evaluate_voice.on_windows', return_value=False)
    def test_generer_audio_hors_windows(self, mock_windows):
        with self.assertRaisesMessage(CommandError, 'voix de Windows'):
            call_command('evaluate_voice', '--generer-audio', '--output', str(self.out), stdout=StringIO())

    @patch('Voice.management.commands.evaluate_voice.subprocess.run')
    @patch('Voice.management.commands.evaluate_voice.on_windows', return_value=True)
    @patch('Voice.services.call_hf_emotion', return_value=scores('fear', 0.8))
    def test_generer_audio_appelle_powershell(self, mock_emotion, mock_windows, mock_run):
        call_command('evaluate_voice', '--generer-audio', '--only', 'emotions', '--limit', '1',
                     '--output', str(self.out), stdout=StringIO())
        self.assertEqual(mock_run.call_count, 1)
        env = mock_run.call_args.kwargs['env']
        self.assertEqual(env['TTS_VOIX'], 'Microsoft Hortense Desktop')
        self.assertTrue(env['TTS_FICHIER'].endswith('note_01.wav'))
