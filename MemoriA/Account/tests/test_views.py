# Tests d'intégration des vues de Account (django.test.Client)
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from Account.models import ActivityLog, Profile
from Journal.models import JournalEntry
from Reminder.models import DailyPrompt, Reminder
from Voice.models import MoodRecord

PASSWORD = 'MotDePasse!2026'


class AccountViewsTestBase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', email='sarah@example.com', password=PASSWORD)
        self.user.groups.add(Group.objects.get(name='Utilisateur'))

    def login(self, user=None):
        self.client.login(username=(user or self.user).username, password=PASSWORD)


class SignupViewTest(AccountViewsTestBase):

    def test_page_inscription(self):
        response = self.client.get(reverse('account:signup'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Créer un compte')

    def test_inscription_cree_profil_groupe_et_connecte(self):
        response = self.client.post(reverse('account:signup'), {
            'username': 'amine', 'email': 'amine@example.com',
            'password1': PASSWORD, 'password2': PASSWORD,
        })
        self.assertRedirects(response, reverse('account:home'))
        user = User.objects.get(username='amine')
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertTrue(user.groups.filter(name='Utilisateur').exists())
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)

    def test_mot_de_passe_hashe(self):
        self.client.post(reverse('account:signup'), {
            'username': 'amine', 'email': 'amine@example.com',
            'password1': PASSWORD, 'password2': PASSWORD,
        })
        user = User.objects.get(username='amine')
        self.assertNotEqual(user.password, PASSWORD)
        self.assertTrue(user.check_password(PASSWORD))

    def test_mots_de_passe_differents(self):
        response = self.client.post(reverse('account:signup'), {
            'username': 'amine', 'email': 'amine@example.com',
            'password1': PASSWORD, 'password2': 'autre',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='amine').exists())

    def test_email_deja_utilise(self):
        response = self.client.post(reverse('account:signup'), {
            'username': 'amine', 'email': 'SARAH@example.com',
            'password1': PASSWORD, 'password2': PASSWORD,
        })
        self.assertContains(response, 'Cette adresse e-mail est déjà utilisée.')

    def test_utilisateur_connecte_redirige(self):
        self.login()
        response = self.client.get(reverse('account:signup'))
        self.assertRedirects(response, reverse('account:home'))


class LoginLogoutTest(AccountViewsTestBase):

    def test_page_connexion(self):
        response = self.client.get(reverse('account:login'))
        self.assertEqual(response.status_code, 200)

    def test_connexion_reussie(self):
        response = self.client.post(reverse('account:login'), {'username': 'sarah', 'password': PASSWORD})
        self.assertRedirects(response, reverse('account:home'))

    def test_connexion_echouee(self):
        response = self.client.post(reverse('account:login'), {'username': 'sarah', 'password': 'faux'})
        self.assertContains(response, "Nom d'utilisateur ou mot de passe incorrect.")

    def test_deconnexion(self):
        self.login()
        response = self.client.post(reverse('account:logout'))
        self.assertRedirects(response, reverse('account:login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_formulaires_proteges_par_csrf(self):
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(reverse('account:login'), {'username': 'sarah', 'password': PASSWORD})
        self.assertEqual(response.status_code, 403)


class HomeViewTest(AccountViewsTestBase):

    def test_redirection_si_non_connecte(self):
        response = self.client.get(reverse('account:home'))
        self.assertRedirects(response, f"{reverse('account:login')}?next=/")

    def test_tableau_de_bord_vide(self):
        self.login()
        response = self.client.get(reverse('account:home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucune entrée pour l'instant.")
        self.assertEqual(len(response.context['week']), 7)

    def test_affiche_seulement_mes_donnees(self):
        other = User.objects.create_user('amine', password=PASSWORD)
        JournalEntry.objects.create(user=self.user, title='Mon entrée', content='...')
        JournalEntry.objects.create(user=other, title='Entrée secrète', content='...')
        self.login()
        response = self.client.get(reverse('account:home'))
        self.assertContains(response, 'Mon entrée')
        self.assertNotContains(response, 'Entrée secrète')

    def test_humeur_et_question_du_jour(self):
        today = timezone.localdate()
        MoodRecord.objects.create(user=self.user, emotion='Joie', score=0.9, record_date=today)
        MoodRecord.objects.create(user=self.user, emotion='Peur', record_date=today - timedelta(days=10))
        reminder = Reminder.objects.create(user=self.user, reminder_time='21:00')
        DailyPrompt.objects.create(user=self.user, reminder=reminder, question='Comment va Sarah ?')
        self.login()
        response = self.client.get(reverse('account:home'))
        self.assertEqual(response.context['week'][-1]['mood'].emotion, 'Joie')
        self.assertContains(response, 'Comment va Sarah ?')


class ProfileViewsTest(AccountViewsTestBase):

    def test_redirection_si_non_connecte(self):
        for name in ('account:profile', 'account:profile_update', 'account:password_change'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('account:login'), response.url)

    def test_affiche_mon_profil(self):
        self.login()
        response = self.client.get(reverse('account:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['profile'].user, self.user)

    def test_profil_cree_si_absent(self):
        Profile.objects.filter(user=self.user).delete()
        self.login()
        response = self.client.get(reverse('account:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Profile.objects.filter(user=self.user).exists())

    def test_modification_profil(self):
        self.login()
        response = self.client.post(reverse('account:profile_update'), {
            'first_name': 'Sarah', 'last_name': 'Ben Ali', 'email': 'sarah@example.com',
            'bio': 'J’aime la mer.', 'birth_date': '2000-05-01', 'language': 'en',
        }, follow=True)
        self.assertContains(response, 'Votre profil a été mis à jour.')
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Sarah')
        self.assertEqual(self.user.profile.language, 'en')

    def test_date_de_naissance_future_refusee(self):
        self.login()
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        response = self.client.post(reverse('account:profile_update'), {
            'email': 'sarah@example.com', 'birth_date': future, 'language': 'fr',
        })
        self.assertContains(response, 'La date de naissance ne peut pas être dans le futur.')

    def test_avatar_mauvais_format_refuse(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.login()
        fichier = SimpleUploadedFile('virus.txt', b'pas une image', content_type='text/plain')
        response = self.client.post(reverse('account:profile_update'), {
            'email': 'sarah@example.com', 'language': 'fr', 'avatar': fichier,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('avatar'))

    def test_changement_mot_de_passe(self):
        self.login()
        response = self.client.post(reverse('account:password_change'), {
            'old_password': PASSWORD, 'new_password1': 'NouveauMdp!2026', 'new_password2': 'NouveauMdp!2026',
        })
        self.assertRedirects(response, reverse('account:profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NouveauMdp!2026'))


class StatisticsViewTest(AccountViewsTestBase):

    def test_redirection_si_non_connecte(self):
        response = self.client.get(reverse('account:statistics'))
        self.assertEqual(response.status_code, 302)

    def test_interdit_pour_un_utilisateur(self):
        self.login()
        response = self.client.get(reverse('account:statistics'))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, '403.html')

    def test_autorise_pour_un_administrateur(self):
        self.user.groups.add(Group.objects.get(name='Administrateur'))
        ActivityLog.objects.create(user=self.user, action='Transcription', ai_service='Whisper', duration_ms=1000)
        ActivityLog.objects.create(user=self.user, action='Transcription', ai_service='Whisper',
                                   duration_ms=3000, status='Echec')
        self.login()
        response = self.client.get(reverse('account:statistics'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['failure_rate'], 50.0)
        service = response.context['services'][0]
        self.assertEqual(service['avg_ms'], 2000)
        self.assertEqual(service['max_ms'], 3000)
        self.assertContains(response, 'Whisper')

    def test_menu_statistiques_visible_seulement_pour_admin(self):
        self.login()
        response = self.client.get(reverse('account:home'))
        self.assertNotContains(response, reverse('account:statistics'))


class TaskStatusViewTest(AccountViewsTestBase):

    def test_redirection_si_non_connecte(self):
        response = self.client.get(reverse('account:task_status', args=['abc']))
        self.assertEqual(response.status_code, 302)

    def test_tache_inconnue_en_attente(self):
        self.login()
        response = self.client.get(reverse('account:task_status', args=['abc']))
        self.assertEqual(response.json(), {'status': 'PENDING'})

    @patch('Account.views.AsyncResult')
    def test_tache_reussie(self, mock_result):
        mock_result.return_value.state = 'SUCCESS'
        mock_result.return_value.result = {'html': '<p>Bonjour</p>'}
        self.login()
        response = self.client.get(reverse('account:task_status', args=['abc']))
        self.assertEqual(response.json(), {'status': 'SUCCESS', 'result': {'html': '<p>Bonjour</p>'}})

    @patch('Account.views.AsyncResult')
    def test_tache_echouee(self, mock_result):
        mock_result.return_value.state = 'FAILURE'
        mock_result.return_value.result = ValueError('détail technique')
        self.login()
        response = self.client.get(reverse('account:task_status', args=['abc']))
        self.assertEqual(response.json(), {'status': 'FAILURE'})       # pas de détail technique

    @patch('Account.views.AsyncResult')
    def test_tache_echouee_message_clair_de_l_ia(self, mock_result):
        from Account.services import AIServiceError
        mock_result.return_value.state = 'FAILURE'
        mock_result.return_value.result = AIServiceError("Le service ne répond pas.")
        self.login()
        response = self.client.get(reverse('account:task_status', args=['abc']))
        self.assertEqual(response.json(), {'status': 'FAILURE', 'message': "Le service ne répond pas."})


class ErrorPagesTest(AccountViewsTestBase):

    def test_page_404_personnalisee(self):
        self.login()
        response = self.client.get('/page-qui-n-existe-pas/')
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, '404.html')
        self.assertContains(response, 'Page introuvable', status_code=404)


class AccessibilityTest(AccountViewsTestBase):
    """Vérifications simples de la structure accessible (section 10.4)."""

    def test_structure_de_la_page(self):
        self.login()
        html = self.client.get(reverse('account:home')).content.decode()
        self.assertIn('<html lang="fr">', html)
        self.assertIn('href="#contenu">Aller au contenu</a>', html)
        self.assertIn('<main id="contenu"', html)
        self.assertIn('aria-label="Menu principal"', html)
        self.assertEqual(html.count('<h1'), 1)

    def test_titres_uniques(self):
        self.login()
        titles = set()
        for name in ('account:home', 'account:profile', 'account:profile_update', 'account:password_change'):
            html = self.client.get(reverse(name)).content.decode()
            titles.add(html.split('<title>')[1].split('</title>')[0])
        self.assertEqual(len(titles), 4)

    def test_erreurs_reliees_au_champ(self):
        self.login()
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        html = self.client.post(reverse('account:profile_update'), {
            'email': 'sarah@example.com', 'birth_date': future, 'language': 'fr',
        }).content.decode()
        self.assertIn('aria-describedby="id_birth_date_errors"', html)
        self.assertIn('aria-invalid="true"', html)


@override_settings(DEBUG=False)
class ServerErrorTemplateTest(TestCase):

    def test_template_500_autonome(self):
        from django.template.loader import render_to_string
        html = render_to_string('500.html')
        self.assertIn('Une erreur est survenue', html)
