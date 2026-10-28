# Tests des services IA de Reminder : KMeans tourne pour de vrai, la NER est simulée
from datetime import date, datetime, time, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from Account.models import ActivityLog
from Account.services import AIServiceError
from Journal.models import JournalEntry
from Reminder import services, tasks
from Reminder.models import DailyPrompt, Reminder

PASSWORD = 'MotDePasse!2026'
TODAY = date(2026, 10, 28)            # un mercredi


class BestTimeTest(SimpleTestCase):

    def test_cercle_des_heures(self):
        for minutes in (0, 90, 720, 1439):
            x, y = services.minutes_to_point(minutes)
            self.assertEqual(services.point_to_minutes(x, y), minutes)

    def test_habitude_du_soir(self):
        minutes = [21 * 60 + m for m in (0, 10, 20, 30, 5, 15)] + [8 * 60, 13 * 60]
        best, share = services.best_minutes(minutes)
        self.assertAlmostEqual(best, 21 * 60 + 13, delta=5)
        self.assertEqual(share, 6 / 8)

    def test_habitude_autour_de_minuit(self):
        # 23 h 50 et 0 h 10 : la moyenne simple donnerait midi, le cercle donne minuit
        minutes = [23 * 60 + 50, 23 * 60 + 55, 5, 10, 23 * 60 + 58, 2]
        best, _ = services.best_minutes(minutes, clusters=1)
        self.assertTrue(best >= 23 * 60 + 50 or best <= 10, best)

    def test_pas_assez_d_entrees(self):
        with self.assertRaisesMessage(AIServiceError, 'au moins 5 entrées'):
            services.best_minutes([600, 610])


class EntitiesAndQuestionTest(SimpleTestCase):

    def test_nettoyage_des_entites(self):
        raw = [{'type': 'PER', 'text': ' Sarah', 'score': 0.99}, {'type': 'PER', 'text': 'sarah', 'score': 0.95},
               {'type': 'LOC', 'text': 'Tunis.', 'score': 0.97}, {'type': 'ORG', 'text': 'X', 'score': 0.99},
               {'type': 'MISC', 'text': 'Aïd', 'score': 0.5}]
        self.assertEqual(services.clean_entities(raw), [{'type': 'PER', 'text': 'Sarah'}, {'type': 'LOC', 'text': 'Tunis'}])

    def test_question_avec_une_personne_en_priorite(self):
        entities = [{'type': 'LOC', 'text': 'Sousse'}, {'type': 'PER', 'text': 'Karim'}]
        question = services.build_question(entities, TODAY)
        self.assertIn('Karim', question)

    def test_question_avec_un_lieu(self):
        self.assertIn('Sousse', services.build_question([{'type': 'LOC', 'text': 'Sousse'}], TODAY))

    def test_question_generale_sans_entite(self):
        self.assertIn(services.build_question([], TODAY), services.GENERIC)

    def test_la_question_change_chaque_jour(self):
        entities = [{'type': 'PER', 'text': 'Karim'}]
        questions = {services.build_question(entities, TODAY + timedelta(days=d)) for d in range(3)}
        self.assertEqual(len(questions), 3)


@override_settings(HF_TOKEN='test-token')
class ReminderServicesTestBase(TestCase):

    def setUp(self):
        services.get_hf_client.cache_clear()
        services.get_local_ner.cache_clear()
        self.user = User.objects.create_user('emir', email='emir@example.com', password=PASSWORD)
        self.reminder = Reminder.objects.create(user=self.user, reminder_time=time(21, 0), days='0,1,2,3,4',
                                                channel='Email')

    def add_entries(self, hours):
        for i, (h, m) in enumerate(hours):
            entry = JournalEntry.objects.create(user=self.user, title=f'Entrée {i}', content='...')
            moment = timezone.make_aware(datetime(2026, 10, 1 + i, h, m))
            JournalEntry.objects.filter(pk=entry.pk).update(creation_date=moment)


class SuggestTimeTest(ReminderServicesTestBase):

    def test_heure_conseillee_arrondie_et_mesuree(self):
        self.add_entries([(21, 3), (21, 12), (20, 58), (21, 7), (21, 9), (9, 0)])
        suggested, share = services.suggest_time(self.user)
        self.assertEqual(suggested, time(21, 5))
        self.assertEqual(share, 0.83)
        self.assertEqual(ActivityLog.objects.get().ai_service, 'KMeans')

    def test_tache_enregistre_l_heure(self):
        self.add_entries([(7, 30)] * 5)
        result = tasks.suggest_time_task.delay(self.reminder.pk, self.user.pk).get()
        self.reminder.refresh_from_db()
        self.assertEqual(self.reminder.suggested_time, time(7, 30))
        self.assertIn('07:30', result['html'])

    def test_pas_assez_d_entrees(self):
        self.add_entries([(21, 0)])
        with self.assertRaises(AIServiceError):
            tasks.suggest_time_task.delay(self.reminder.pk, self.user.pk)
        self.assertEqual(ActivityLog.objects.get().status, 'Echec')


class NerTest(ReminderServicesTestBase):

    @patch('Reminder.services.get_hf_client')
    def test_hugging_face(self, mock_hf):
        mock_hf.return_value.token_classification.return_value = [
            SimpleNamespace(entity_group='PER', word='Karim', score=0.99),
            SimpleNamespace(entity_group='LOC', word='Sousse', score=0.98)]
        self.assertEqual(services.extract_entities('Match avec Karim à Sousse', self.user),
                         [{'type': 'PER', 'text': 'Karim'}, {'type': 'LOC', 'text': 'Sousse'}])

    @override_settings(REMINDER_BACKEND='auto')
    @patch('Reminder.services.get_local_ner')
    @patch('Reminder.services.get_hf_client')
    def test_secours_local(self, mock_hf, mock_local):
        mock_hf.return_value.token_classification.side_effect = RuntimeError('402')
        mock_local.return_value.return_value = [{'entity_group': 'PER', 'word': 'Amira', 'score': 0.97}]
        self.assertEqual(services.extract_entities('Mariage d’Amira', self.user), [{'type': 'PER', 'text': 'Amira'}])
        self.assertEqual(set(ActivityLog.objects.values_list('ai_service', 'status')),
                         {('CamemBERT NER', 'Echec'), ('CamemBERT NER (local)', 'Succes')})

    @patch('Reminder.services.get_hf_client')
    def test_echec(self, mock_hf):
        mock_hf.return_value.token_classification.side_effect = RuntimeError('503')
        with self.assertRaisesMessage(AIServiceError, 'reconnaissance des noms ne répond pas'):
            services.extract_entities('Bonjour', self.user)

    @patch('Reminder.services.extract_entities', return_value=[{'type': 'PER', 'text': 'Karim'}])
    def test_question_du_jour_personnalisee(self, mock_ner):
        JournalEntry.objects.create(user=self.user, title='Match', content='Foot avec Karim')
        prompt, created = services.create_daily_prompt(self.reminder, TODAY)
        self.assertTrue(created)
        self.assertIn('Karim', prompt.question)
        self.assertEqual(prompt.detected_entities, ['personne : Karim'])
        self.assertIn('Foot avec Karim', mock_ner.call_args.args[0])

    @patch('Reminder.services.extract_entities', side_effect=AIServiceError('panne'))
    def test_question_generale_si_l_ia_echoue(self, mock_ner):
        JournalEntry.objects.create(user=self.user, title='Match', content='...')
        prompt, _ = services.create_daily_prompt(self.reminder, TODAY)
        self.assertIn(prompt.question, services.GENERIC)

    @patch('Reminder.services.extract_entities', return_value=[])
    def test_une_seule_question_par_jour(self, mock_ner):
        first, _ = services.create_daily_prompt(self.reminder, TODAY)
        second, created = services.create_daily_prompt(self.reminder, TODAY)
        self.assertEqual((first.pk, created), (second.pk, False))


class SendRemindersTest(ReminderServicesTestBase):

    def at(self, hour, minute=0, day=TODAY):
        return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))

    @patch('Reminder.services.extract_entities', return_value=[])
    def test_envoi_a_l_heure_prevue(self, mock_ner):
        self.assertEqual(services.send_due_reminders(self.at(21, 2)), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/reminder/questions/', mail.outbox[0].body)
        self.assertTrue(DailyPrompt.objects.filter(reminder=self.reminder, prompt_date=TODAY).exists())

    @patch('Reminder.services.extract_entities', return_value=[])
    def test_pas_avant_l_heure_ni_deux_fois(self, mock_ner):
        self.assertEqual(services.send_due_reminders(self.at(20, 0)), 0)
        services.send_due_reminders(self.at(21, 5))
        self.assertEqual(services.send_due_reminders(self.at(22, 0)), 0)
        self.assertEqual(len(mail.outbox), 1)

    @patch('Reminder.services.extract_entities', return_value=[])
    def test_pas_le_week_end_ni_en_pause(self, mock_ner):
        saturday = date(2026, 10, 31)
        self.assertEqual(services.send_due_reminders(self.at(21, 30, saturday)), 0)
        self.reminder.is_active = False
        self.reminder.save()
        self.assertEqual(services.send_due_reminders(self.at(21, 30)), 0)

    @patch('Reminder.services.extract_entities', return_value=[])
    def test_canal_application_sans_email(self, mock_ner):
        self.reminder.channel = 'Application'
        self.reminder.save()
        self.assertEqual(services.send_due_reminders(self.at(21, 30)), 1)
        self.assertEqual(len(mail.outbox), 0)

    @patch('Reminder.services.extract_entities', return_value=[])
    def test_commande_send_reminders(self, mock_ner):
        out = StringIO()
        call_command('send_reminders', '--force', stdout=out)
        self.assertIn('1 rappel(s) envoyé(s)', out.getvalue())

    @patch('Reminder.services.send_due_reminders', return_value=2)
    def test_tache_celery_beat(self, mock_send):
        self.assertEqual(tasks.send_reminders_task.delay().get(), {'envoyes': 2})

    def test_planification_celery_beat(self):
        from django.conf import settings
        self.assertEqual(settings.CELERY_BEAT_SCHEDULE['envoyer-les-rappels']['task'],
                         'Reminder.tasks.send_reminders_task')


class ReminderAIViewsTest(ReminderServicesTestBase):

    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user('amine', password=PASSWORD)
        self.other_reminder = Reminder.objects.create(user=self.other, reminder_time=time(8, 0))
        self.client.login(username='emir', password=PASSWORD)

    def test_calculer_l_heure(self):
        self.add_entries([(22, 0)] * 5)
        response = self.client.post(reverse('reminder:reminder_suggest', args=[self.reminder.pk]))
        self.assertIn('type=heure', response.url)
        page = self.client.get(response.url)
        self.assertContains(page, "Analyse de vos heures")
        self.assertContains(page, 'Appliquer 22:00')

    def test_appliquer(self):
        self.reminder.suggested_time = time(22, 15)
        self.reminder.save()
        response = self.client.post(reverse('reminder:reminder_apply', args=[self.reminder.pk]), follow=True)
        self.assertContains(response, 'sonnera désormais à 22:15')
        self.reminder.refresh_from_db()
        self.assertEqual(self.reminder.reminder_time, time(22, 15))

    def test_appliquer_sans_heure_conseillee_404(self):
        self.assertEqual(self.client.post(reverse('reminder:reminder_apply', args=[self.reminder.pk])).status_code, 404)

    @patch('Reminder.services.extract_entities', return_value=[{'type': 'LOC', 'text': 'Djerba'}])
    def test_generer_la_question(self, mock_ner):
        JournalEntry.objects.create(user=self.user, title='Vacances', content='Plage à Djerba')
        response = self.client.post(reverse('reminder:prompt_generate', args=[self.reminder.pk]))
        self.assertIn('type=question', response.url)
        self.assertIn('Djerba', DailyPrompt.objects.get(reminder=self.reminder).question)

    def test_boutons_ia_sur_le_rappel_d_un_autre_404(self):
        for name in ('reminder_suggest', 'reminder_apply', 'prompt_generate'):
            response = self.client.post(reverse(f'reminder:{name}', args=[self.other_reminder.pk]))
            self.assertEqual(response.status_code, 404, name)

    def test_boutons_ia_refuses_en_get(self):
        self.assertEqual(self.client.get(reverse('reminder:reminder_suggest', args=[self.reminder.pk])).status_code, 405)
