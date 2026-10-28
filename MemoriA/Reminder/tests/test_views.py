# Tests d'intégration des vues de Reminder (CRUD, réponse, permissions)
from datetime import time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Journal.models import JournalEntry
from Reminder.models import DailyPrompt, Reminder

PASSWORD = 'MotDePasse!2026'


class ReminderViewsTestBase(TestCase):

    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user('emir', password=PASSWORD)
        self.other = User.objects.create_user('amine', password=PASSWORD)
        self.reminder = Reminder.objects.create(user=self.user, reminder_time=time(21, 0), days='0,1,2,3,4')
        self.prompt = DailyPrompt.objects.create(user=self.user, reminder=self.reminder,
                                                 question='Comment s\'est passé ton match avec Karim ?')
        self.other_reminder = Reminder.objects.create(user=self.other, reminder_time=time(8, 0), message='Rappel secret')
        self.other_prompt = DailyPrompt.objects.create(user=self.other, reminder=self.other_reminder,
                                                       question='Question secrète ?')
        self.client.login(username='emir', password=PASSWORD)

    def reminder_data(self, **values):
        data = {'reminder_time': '20:30', 'week_days': ['0', '2', '4'], 'channel': 'Email',
                'message': 'Écris ta journée', 'is_active': 'on'}
        data.update(values)
        return data


class LoginRequiredTest(ReminderViewsTestBase):

    def test_toutes_les_pages_redirigent_vers_la_connexion(self):
        self.client.logout()
        urls = [reverse('reminder:reminder_list'), reverse('reminder:reminder_create'),
                reverse('reminder:reminder_detail', args=[self.reminder.pk]),
                reverse('reminder:reminder_update', args=[self.reminder.pk]),
                reverse('reminder:reminder_delete', args=[self.reminder.pk]),
                reverse('reminder:prompt_list'), reverse('reminder:prompt_create'),
                reverse('reminder:prompt_detail', args=[self.prompt.pk]),
                reverse('reminder:prompt_update', args=[self.prompt.pk]),
                reverse('reminder:prompt_delete', args=[self.prompt.pk]),
                reverse('reminder:prompt_answer', args=[self.prompt.pk])]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('account:login'), response.url)
        self.assertEqual(self.client.post(reverse('reminder:prompt_ignore', args=[self.prompt.pk])).status_code, 302)


class OtherUserDataTest(ReminderViewsTestBase):
    """Un utilisateur ne peut pas lire, modifier ou supprimer les données d'un autre (404)."""

    def test_rappel_d_un_autre_404(self):
        for name in ('reminder_detail', 'reminder_update', 'reminder_delete'):
            response = self.client.get(reverse(f'reminder:{name}', args=[self.other_reminder.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.client.post(reverse('reminder:reminder_delete', args=[self.other_reminder.pk]))
        self.assertTrue(Reminder.objects.filter(pk=self.other_reminder.pk).exists())

    def test_question_d_un_autre_404(self):
        for name in ('prompt_detail', 'prompt_update', 'prompt_delete', 'prompt_answer'):
            response = self.client.get(reverse(f'reminder:{name}', args=[self.other_prompt.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.assertEqual(self.client.post(reverse('reminder:prompt_ignore', args=[self.other_prompt.pk])).status_code, 404)

    def test_repondre_a_la_question_d_un_autre_ne_cree_rien(self):
        response = self.client.post(reverse('reminder:prompt_answer', args=[self.other_prompt.pk]),
                                    {'title': 'Intrus', 'content': 'Bonjour', 'entry_date': self.today})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(JournalEntry.objects.filter(title='Intrus').exists())

    def test_listes_ne_montrent_que_mes_donnees(self):
        for name in ('reminder_list', 'prompt_list'):
            response = self.client.get(reverse(f'reminder:{name}'))
            self.assertNotContains(response, 'secrète')
            self.assertNotContains(response, '08:00')

    def test_question_sur_le_rappel_d_un_autre_refusee(self):
        response = self.client.post(reverse('reminder:prompt_create'), {
            'reminder': self.other_reminder.pk, 'prompt_date': self.today, 'question': 'Une question ?',
            'status': 'Envoye'})
        self.assertTrue(response.context['form'].errors.get('reminder'))

    def test_rappel_toujours_au_nom_de_l_utilisateur_connecte(self):
        self.client.post(reverse('reminder:reminder_create'), self.reminder_data(user=self.other.pk))
        self.assertEqual(Reminder.objects.get(reminder_time=time(20, 30)).user, self.user)


class ReminderViewsTest(ReminderViewsTestBase):

    def test_page_rappels(self):
        response = self.client.get(reverse('reminder:reminder_list'))
        self.assertContains(response, '21:00')
        self.assertContains(response, 'En semaine')
        self.assertEqual(response.context['today_prompt'], self.prompt)
        self.assertContains(response, 'Répondre')

    def test_creation_jours_coches(self):
        response = self.client.post(reverse('reminder:reminder_create'), self.reminder_data())
        reminder = Reminder.objects.get(reminder_time=time(20, 30))
        self.assertRedirects(response, reverse('reminder:reminder_detail', args=[reminder.pk]))
        self.assertEqual(reminder.days, '0,2,4')
        self.assertEqual(reminder.channel, 'Email')

    def test_aucun_jour_refuse(self):
        response = self.client.post(reverse('reminder:reminder_create'), self.reminder_data(week_days=[]))
        self.assertTrue(response.context['form'].errors.get('week_days'))

    def test_jour_invalide_refuse(self):
        response = self.client.post(reverse('reminder:reminder_create'), self.reminder_data(week_days=['9']))
        self.assertTrue(response.context['form'].errors.get('week_days'))

    def test_heure_obligatoire(self):
        response = self.client.post(reverse('reminder:reminder_create'), self.reminder_data(reminder_time=''))
        self.assertTrue(response.context['form'].errors.get('reminder_time'))

    def test_formulaire_cases_et_heure(self):
        response = self.client.get(reverse('reminder:reminder_update', args=[self.reminder.pk]))
        self.assertEqual(response.context['form'].fields['week_days'].initial, ['0', '1', '2', '3', '4'])
        self.assertContains(response, 'type="time"')
        self.assertContains(response, '<legend')

    def test_modification(self):
        response = self.client.post(reverse('reminder:reminder_update', args=[self.reminder.pk]),
                                    self.reminder_data(week_days=['5', '6'], is_active=''), follow=True)
        self.assertContains(response, 'Le rappel a été modifié.')
        self.reminder.refresh_from_db()
        self.assertEqual((self.reminder.days, self.reminder.is_active), ('5,6', False))

    def test_suppression_supprime_les_questions(self):
        response = self.client.get(reverse('reminder:reminder_delete', args=[self.reminder.pk]))
        self.assertContains(response, 'Voulez-vous vraiment supprimer')
        response = self.client.post(reverse('reminder:reminder_delete', args=[self.reminder.pk]))
        self.assertRedirects(response, reverse('reminder:reminder_list'))
        self.assertFalse(DailyPrompt.objects.filter(pk=self.prompt.pk).exists())

    def test_etat_vide(self):
        Reminder.objects.filter(user=self.user).delete()
        self.assertContains(self.client.get(reverse('reminder:reminder_list')), "Aucun rappel pour l'instant.")


class PromptViewsTest(ReminderViewsTestBase):

    def prompt_data(self, **values):
        data = {'reminder': self.reminder.pk, 'prompt_date': self.today - timedelta(days=1),
                'question': 'Qu\'as-tu appris hier ?', 'status': 'Envoye'}
        data.update(values)
        return data

    def test_liste_et_filtre(self):
        self.assertContains(self.client.get(reverse('reminder:prompt_list')), 'ton match avec Karim')
        response = self.client.get(reverse('reminder:prompt_list'), {'statut': 'Repondu'})
        self.assertContains(response, 'Aucune question avec ce statut.')

    def test_creation_pre_remplie_depuis_un_rappel(self):
        response = self.client.get(reverse('reminder:prompt_create'), {'reminder': self.reminder.pk})
        self.assertEqual(response.context['form'].initial['reminder'], str(self.reminder.pk))

    def test_creation(self):
        response = self.client.post(reverse('reminder:prompt_create'), self.prompt_data())
        prompt = DailyPrompt.objects.get(user=self.user, question__startswith='Qu')
        self.assertRedirects(response, reverse('reminder:prompt_detail', args=[prompt.pk]))
        self.assertEqual(prompt.user, self.user)

    def test_une_question_par_jour_et_par_rappel(self):
        response = self.client.post(reverse('reminder:prompt_create'), self.prompt_data(prompt_date=self.today))
        self.assertContains(response, 'Ce rappel a déjà une question pour cette date.')

    def test_date_future_refusee(self):
        response = self.client.post(reverse('reminder:prompt_create'),
                                    self.prompt_data(prompt_date=self.today + timedelta(days=1)))
        self.assertContains(response, 'La date ne peut pas être dans le futur.')

    def test_question_trop_courte(self):
        response = self.client.post(reverse('reminder:prompt_create'), self.prompt_data(question='Hé'))
        self.assertContains(response, 'au moins 5 caractères')

    def test_sans_rappel_message_clair(self):
        Reminder.objects.filter(user=self.user).delete()
        self.assertContains(self.client.get(reverse('reminder:prompt_create')), "créez d'abord un rappel")

    def test_modification_et_suppression(self):
        response = self.client.post(reverse('reminder:prompt_update', args=[self.prompt.pk]),
                                    self.prompt_data(prompt_date=self.today, question='Comment va Karim ?'))
        self.assertRedirects(response, reverse('reminder:prompt_detail', args=[self.prompt.pk]))
        self.prompt.refresh_from_db()
        self.assertEqual(self.prompt.question, 'Comment va Karim ?')
        response = self.client.post(reverse('reminder:prompt_delete', args=[self.prompt.pk]))
        self.assertRedirects(response, reverse('reminder:prompt_list'))


class AnswerTest(ReminderViewsTestBase):

    def test_formulaire_pre_rempli(self):
        response = self.client.get(reverse('reminder:prompt_answer', args=[self.prompt.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].initial['title'].startswith('Réponse : Comment'))
        self.assertEqual(response.context['form'].initial['entry_date'], self.today)
        self.assertContains(response, 'ton match avec Karim')

    def test_repondre_cree_une_entree_et_marque_repondu(self):
        response = self.client.post(reverse('reminder:prompt_answer', args=[self.prompt.pk]), {
            'title': 'Le match', 'content': 'On a gagné 3 à 1 !', 'entry_date': self.today, 'location': 'Tunis'},
            follow=True)
        self.assertRedirects(response, reverse('reminder:prompt_detail', args=[self.prompt.pk]))
        self.assertContains(response, 'Votre réponse a été ajoutée à votre journal.')
        self.prompt.refresh_from_db()
        entry = self.prompt.answer_entry
        self.assertEqual((self.prompt.status, entry.user, entry.title), ('Repondu', self.user, 'Le match'))

    def test_impossible_de_repondre_deux_fois(self):
        entry = JournalEntry.objects.create(user=self.user, title='Déjà', content='...')
        self.prompt.answer_entry = entry
        self.prompt.save()
        response = self.client.get(reverse('reminder:prompt_answer', args=[self.prompt.pk]), follow=True)
        self.assertContains(response, 'Vous avez déjà répondu à cette question.')

    def test_reponse_vide_refusee(self):
        response = self.client.post(reverse('reminder:prompt_answer', args=[self.prompt.pk]),
                                    {'title': 'Vide', 'content': '  ', 'entry_date': self.today})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(JournalEntry.objects.filter(title='Vide').exists())

    def test_date_de_reponse_future_refusee(self):
        response = self.client.post(reverse('reminder:prompt_answer', args=[self.prompt.pk]), {
            'title': 'Futur', 'content': 'Demain', 'entry_date': self.today + timedelta(days=2)})
        self.assertContains(response, 'ne peut pas être dans le futur')

    def test_ignorer(self):
        response = self.client.post(reverse('reminder:prompt_ignore', args=[self.prompt.pk]))
        self.assertRedirects(response, reverse('reminder:reminder_list'))
        self.prompt.refresh_from_db()
        self.assertEqual(self.prompt.status, 'Ignore')

    def test_ignorer_depuis_l_accueil(self):
        response = self.client.post(reverse('reminder:prompt_ignore', args=[self.prompt.pk]), {'depuis': 'accueil'})
        self.assertRedirects(response, reverse('account:home'))

    def test_ignorer_une_question_deja_repondue_404(self):
        entry = JournalEntry.objects.create(user=self.user, title='Déjà', content='...')
        self.prompt.answer_entry = entry
        self.prompt.save()
        self.assertEqual(self.client.post(reverse('reminder:prompt_ignore', args=[self.prompt.pk])).status_code, 404)

    def test_ignorer_refuse_en_get(self):
        self.assertEqual(self.client.get(reverse('reminder:prompt_ignore', args=[self.prompt.pk])).status_code, 405)

    def test_question_du_jour_sur_le_tableau_de_bord(self):
        response = self.client.get(reverse('account:home'))
        self.assertContains(response, reverse('reminder:prompt_answer', args=[self.prompt.pk]))
        self.assertContains(response, 'name="depuis" value="accueil"')


class ReminderAccessibilityTest(ReminderViewsTestBase):

    def test_un_seul_h1_et_titres_uniques(self):
        urls = [reverse('reminder:reminder_list'), reverse('reminder:reminder_create'),
                reverse('reminder:reminder_detail', args=[self.reminder.pk]),
                reverse('reminder:reminder_update', args=[self.reminder.pk]),
                reverse('reminder:prompt_list'), reverse('reminder:prompt_create'),
                reverse('reminder:prompt_detail', args=[self.prompt.pk]),
                reverse('reminder:prompt_update', args=[self.prompt.pk]),
                reverse('reminder:prompt_answer', args=[self.prompt.pk])]
        titles = set()
        for url in urls:
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('<h1'), 1, url)
            titles.add(html.split('<title>')[1].split('</title>')[0])
        self.assertEqual(len(titles), len(urls))
