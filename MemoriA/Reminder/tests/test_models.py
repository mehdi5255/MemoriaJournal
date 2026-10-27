# Tests des modèles de Reminder
from datetime import time

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from Reminder.models import Reminder, DailyPrompt


class ReminderModelsTestBase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.reminder = Reminder.objects.create(user=self.user, reminder_time=time(21, 0))


class ReminderModelTest(ReminderModelsTestBase):

    def test_str(self):
        self.assertEqual(str(self.reminder), 'sarah - 21:00:00')

    def test_valeurs_par_defaut(self):
        self.assertEqual(self.reminder.days, '0,1,2,3,4,5,6')
        self.assertEqual(self.reminder.channel, 'Application')
        self.assertTrue(self.reminder.is_active)


class DailyPromptModelTest(ReminderModelsTestBase):

    def test_str(self):
        prompt = DailyPrompt.objects.create(user=self.user, reminder=self.reminder, question='Comment va Sarah ?')
        self.assertEqual(str(prompt), f"Question du {timezone.localdate()}")

    def test_statut_par_defaut(self):
        prompt = DailyPrompt.objects.create(user=self.user, reminder=self.reminder, question='...')
        self.assertEqual(prompt.status, 'Envoye')

    def test_une_question_par_jour_et_par_rappel(self):
        DailyPrompt.objects.create(user=self.user, reminder=self.reminder, question='Première')
        with self.assertRaises(IntegrityError):
            DailyPrompt.objects.create(user=self.user, reminder=self.reminder, question='Seconde')
