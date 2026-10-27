# Tests des modèles de Account
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase

from Account.models import Profile, ActivityLog


class ProfileModelTest(TestCase):

    def setUp(self):
        # Le profil est créé par le signal post_save
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.profile = self.user.profile

    def test_str(self):
        self.assertEqual(str(self.profile), 'sarah')

    def test_langue_par_defaut(self):
        self.assertEqual(self.profile.language, 'fr')

    def test_un_seul_profil_par_utilisateur(self):
        with self.assertRaises(IntegrityError):
            Profile.objects.create(user=self.user)

    def test_profil_cree_automatiquement(self):
        user = User.objects.create_user('amine', password='motdepasse123')
        self.assertTrue(Profile.objects.filter(user=user).exists())

    def test_suppression_utilisateur_supprime_profil(self):
        self.user.delete()
        self.assertEqual(Profile.objects.count(), 0)


class ActivityLogModelTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')

    def test_str(self):
        log = ActivityLog.objects.create(user=self.user, action='Transcription', ai_service='Whisper')
        self.assertEqual(str(log), 'sarah - Transcription')

    def test_valeurs_par_defaut(self):
        log = ActivityLog.objects.create(user=self.user, action='Récit')
        self.assertEqual(log.status, 'Succes')
        self.assertEqual(log.duration_ms, 0)

    def test_related_name(self):
        ActivityLog.objects.create(user=self.user, action='Récit')
        self.assertEqual(self.user.activity_logs.count(), 1)


class GroupsMigrationTest(TestCase):
    """Les groupes sont créés par la migration 0002_create_groups."""

    def test_groupes_existent(self):
        from django.contrib.auth.models import Group
        self.assertTrue(Group.objects.filter(name='Utilisateur').exists())
        admin_group = Group.objects.get(name='Administrateur')
        self.assertTrue(admin_group.permissions.filter(codename='view_activitylog').exists())
