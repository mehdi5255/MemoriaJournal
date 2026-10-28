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


class ReminderDaysTest(ReminderModelsTestBase):

    def test_day_numbers(self):
        self.reminder.days = '4,0, 2,9,x,0'
        self.assertEqual(self.reminder.day_numbers(), [0, 2, 4])        # valeurs invalides ignorées

    def test_days_label(self):
        self.assertEqual(self.reminder.days_label(), 'tous les jours')
        self.reminder.days = '0,1,2,3,4'
        self.assertEqual(self.reminder.days_label(), 'en semaine')
        self.reminder.days = '5,6'
        self.assertEqual(self.reminder.days_label(), 'samedi, dimanche')
        self.reminder.days = ''
        self.assertEqual(self.reminder.days_label(), 'aucun jour')

    def test_is_due_on(self):
        from datetime import date
        monday, sunday = date(2026, 10, 26), date(2026, 11, 1)
        self.reminder.days = '0'
        self.assertTrue(self.reminder.is_due_on(monday))
        self.assertFalse(self.reminder.is_due_on(sunday))
        self.reminder.is_active = False
        self.assertFalse(self.reminder.is_due_on(monday))
