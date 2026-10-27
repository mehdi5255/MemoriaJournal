# Tests des services de Account (log_ai_call)
from unittest.mock import Mock

from django.contrib.auth.models import User
from django.test import TestCase

from Account.models import ActivityLog
from Account.services import log_ai_call


class LogAiCallTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')

    def test_succes_renvoie_le_resultat_et_cree_un_log(self):
        func = Mock(return_value='Bonjour')
        result = log_ai_call(self.user, 'Transcription', 'Whisper', func, 'audio.webm', langue='fr')

        self.assertEqual(result, 'Bonjour')
        func.assert_called_once_with('audio.webm', langue='fr')
        log = ActivityLog.objects.get()
        self.assertEqual(log.status, 'Succes')
        self.assertEqual(log.ai_service, 'Whisper')
        self.assertGreaterEqual(log.duration_ms, 0)

    def test_echec_cree_un_log_et_relance_l_erreur(self):
        func = Mock(side_effect=ConnectionError('API indisponible'))
        with self.assertRaises(ConnectionError):
            log_ai_call(self.user, 'Récit', 'Gemini', func)

        log = ActivityLog.objects.get()
        self.assertEqual(log.status, 'Echec')
        self.assertEqual(log.action, 'Récit')
