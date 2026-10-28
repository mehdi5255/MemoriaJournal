# Tests des vues de Journal (CRUD des entrées et des étiquettes, filtres, permissions)
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from Journal.models import JournalEntry, Tag


class JournalViewsTestBase(TestCase):

    def setUp(self):
        # Le calcul d'embedding est lancé à chaque enregistrement : le modèle est simulé
        patcher = patch('Journal.services.encode', side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
        self.mock_encode = patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.other = User.objects.create_user('amine', password='motdepasse123')
        self.client.login(username='sarah', password='motdepasse123')
        self.today = timezone.localdate()
        self.tag = Tag.objects.create(user=self.user, name='Voyage', color='#2563C9')
        self.entry = JournalEntry.objects.create(user=self.user, title='Journée à Djerba', location='Djerba',
                                                 content='Baignade et couscous au poisson.', entry_date=self.today)
        self.entry.tags.add(self.tag)
        self.other_tag = Tag.objects.create(user=self.other, name='Secret')
        self.other_entry = JournalEntry.objects.create(user=self.other, title='Entrée privée', content='Personne ne doit lire ça.')

    def entry_data(self, **changes):
        data = {'title': 'Soirée cinéma', 'entry_date': self.today.isoformat(), 'content': 'Un très bon film avec Karim.',
                'location': 'Tunis', 'tags': [self.tag.pk]}
        data.update(changes)
        return data


class LoginRequiredTest(JournalViewsTestBase):

    def test_redirection_vers_la_connexion(self):
        self.client.logout()
        urls = [reverse('journal:entry_list'), reverse('journal:entry_create'),
                reverse('journal:entry_detail', args=[self.entry.pk]), reverse('journal:entry_update', args=[self.entry.pk]),
                reverse('journal:entry_delete', args=[self.entry.pk]), reverse('journal:tag_list'),
                reverse('journal:tag_create'), reverse('journal:tag_detail', args=[self.tag.pk])]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('account:login'), response.url)


class EntryViewsTest(JournalViewsTestBase):

    def test_liste_affiche_seulement_mes_entrees(self):
        response = self.client.get(reverse('journal:entry_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Journée à Djerba')
        self.assertNotContains(response, 'Entrée privée')
        self.assertContains(response, '<h1', count=1)

    def test_liste_vide(self):
        JournalEntry.objects.filter(user=self.user).delete()
        response = self.client.get(reverse('journal:entry_list'))
        self.assertContains(response, "Aucune entrée pour l'instant")
        self.assertContains(response, 'Écrire ma première entrée')

    def test_filtre_texte(self):
        JournalEntry.objects.create(user=self.user, title='Révisions', content='Examen de statistiques.')
        response = self.client.get(reverse('journal:entry_list'), {'q': 'couscous'})
        self.assertEqual([e.title for e in response.context['entries']], ['Journée à Djerba'])

    def test_filtre_dates(self):
        JournalEntry.objects.create(user=self.user, title='Ancienne', content='...', entry_date=self.today - timedelta(days=30))
        response = self.client.get(reverse('journal:entry_list'),
                                   {'debut': (self.today - timedelta(days=40)).isoformat(),
                                    'fin': (self.today - timedelta(days=20)).isoformat()})
        self.assertEqual([e.title for e in response.context['entries']], ['Ancienne'])

    def test_date_invalide_ignoree(self):
        response = self.client.get(reverse('journal:entry_list'), {'debut': 'pas-une-date'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['entries']), 1)

    def test_filtre_etiquette_et_favori(self):
        favorite = JournalEntry.objects.create(user=self.user, title='Favori', content='...', is_favorite=True)
        response = self.client.get(reverse('journal:entry_list'), {'etiquette': self.tag.pk})
        self.assertEqual(list(response.context['entries']), [self.entry])
        response = self.client.get(reverse('journal:entry_list'), {'favori': '1'})
        self.assertEqual(list(response.context['entries']), [favorite])

    def test_aucun_resultat_avec_filtres(self):
        response = self.client.get(reverse('journal:entry_list'), {'q': 'introuvable'})
        self.assertContains(response, 'Aucune entrée ne correspond à ces filtres')

    def test_pagination_garde_les_filtres(self):
        for i in range(12):
            JournalEntry.objects.create(user=self.user, title=f'Plage {i}', content='plage')
        response = self.client.get(reverse('journal:entry_list'), {'q': 'plage'})
        self.assertTrue(response.context['is_paginated'])
        self.assertContains(response, '?q=plage&amp;page=2')

    def test_detail(self):
        response = self.client.get(reverse('journal:entry_detail', args=[self.entry.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Baignade et couscous')
        self.assertContains(response, 'Voyage')
        self.assertContains(response, f'?entry={self.entry.pk}')          # liens photo, voix, récit
        self.assertContains(response, '<h1', count=1)

    def test_formulaire_de_creation(self):
        response = self.client.get(reverse('journal:entry_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'maxlength="150"')
        self.assertContains(response, f'max="{self.today.isoformat()}"')
        self.assertContains(response, 'Voyage')
        self.assertNotContains(response, 'Secret')                          # étiquette d'un autre utilisateur

    def test_creation(self):
        response = self.client.post(reverse('journal:entry_create'), self.entry_data())
        entry = JournalEntry.objects.get(title='Soirée cinéma')
        self.assertTrue(response.url.startswith(reverse('journal:entry_detail', args=[entry.pk]) + '?task='))
        self.assertEqual(entry.embedding, [1.0, 0.0])                     # calculé par la tâche
        self.assertEqual(entry.user, self.user)
        self.assertEqual(list(entry.tags.all()), [self.tag])
        response = self.client.post(reverse('journal:entry_create'), self.entry_data(title='Deuxième'), follow=True)
        self.assertContains(response, 'a été enregistrée')

    def test_creation_date_future_refusee(self):
        response = self.client.post(reverse('journal:entry_create'),
                                    self.entry_data(entry_date=(self.today + timedelta(days=1)).isoformat()))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'ne peut pas être dans le futur')
        self.assertContains(response, 'aria-describedby')
        self.assertFalse(JournalEntry.objects.filter(title='Soirée cinéma').exists())

    def test_creation_champs_vides_refusee(self):
        response = self.client.post(reverse('journal:entry_create'), self.entry_data(title=' ', content='  '))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JournalEntry.objects.filter(user=self.user).count(), 1)

    def test_etiquette_d_un_autre_utilisateur_refusee(self):
        response = self.client.post(reverse('journal:entry_create'), self.entry_data(tags=[self.other_tag.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(JournalEntry.objects.filter(title='Soirée cinéma').exists())

    def test_modification(self):
        response = self.client.post(reverse('journal:entry_update', args=[self.entry.pk]),
                                    self.entry_data(title='Djerba (modifié)', is_favorite='on'))
        self.assertTrue(response.url.startswith(reverse('journal:entry_detail', args=[self.entry.pk])))
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.title, 'Djerba (modifié)')
        self.assertTrue(self.entry.is_favorite)

    def test_suppression_avec_confirmation(self):
        url = reverse('journal:entry_delete', args=[self.entry.pk])
        response = self.client.get(url)
        self.assertContains(response, 'Voulez-vous vraiment supprimer')
        response = self.client.post(url)
        self.assertRedirects(response, reverse('journal:entry_list'))
        self.assertFalse(JournalEntry.objects.filter(pk=self.entry.pk).exists())

    def test_entree_d_un_autre_utilisateur_404(self):
        pk = self.other_entry.pk
        for name in ('entry_detail', 'entry_update', 'entry_delete'):
            self.assertEqual(self.client.get(reverse(f'journal:{name}', args=[pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse('journal:entry_update', args=[pk]), self.entry_data()).status_code, 404)
        self.assertEqual(self.client.post(reverse('journal:entry_delete', args=[pk])).status_code, 404)
        self.assertTrue(JournalEntry.objects.filter(pk=pk, title='Entrée privée').exists())


class TagViewsTest(JournalViewsTestBase):

    def test_liste(self):
        response = self.client.get(reverse('journal:tag_list'))
        self.assertContains(response, 'Voyage')
        self.assertNotContains(response, 'Secret')
        self.assertEqual(response.context['tags'][0].nb_entries, 1)

    def test_liste_vide(self):
        Tag.objects.filter(user=self.user).delete()
        self.assertContains(self.client.get(reverse('journal:tag_list')), 'Créer ma première étiquette')

    def test_detail(self):
        response = self.client.get(reverse('journal:tag_detail', args=[self.tag.pk]))
        self.assertContains(response, 'Journée à Djerba')

    def test_creation(self):
        response = self.client.post(reverse('journal:tag_create'), {'name': '  Famille  ', 'color': '#237a5c'})
        self.assertRedirects(response, reverse('journal:tag_list'))
        tag = Tag.objects.get(user=self.user, name='Famille')
        self.assertEqual(tag.color, '#237A5C')

    def test_nom_deja_utilise(self):
        response = self.client.post(reverse('journal:tag_create'), {'name': 'voyage', 'color': '#237A5C'})
        self.assertContains(response, 'déjà une étiquette avec ce nom')

    def test_meme_nom_qu_un_autre_utilisateur_accepte(self):
        self.client.post(reverse('journal:tag_create'), {'name': 'Secret', 'color': '#237A5C'})
        self.assertTrue(Tag.objects.filter(user=self.user, name='Secret').exists())

    def test_couleur_invalide(self):
        response = self.client.post(reverse('journal:tag_create'), {'name': 'Sport', 'color': 'rouge'})
        self.assertContains(response, 'format #RRGGBB')

    def test_modification_garde_son_nom(self):
        response = self.client.post(reverse('journal:tag_update', args=[self.tag.pk]), {'name': 'Voyage', 'color': '#C2412F'})
        self.assertRedirects(response, reverse('journal:tag_list'))
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.color, '#C2412F')

    def test_suppression_garde_les_entrees(self):
        url = reverse('journal:tag_delete', args=[self.tag.pk])
        self.assertContains(self.client.get(url), 'les entrées sont conservées')
        self.client.post(url)
        self.assertFalse(Tag.objects.filter(pk=self.tag.pk).exists())
        self.assertTrue(JournalEntry.objects.filter(pk=self.entry.pk).exists())

    def test_etiquette_d_un_autre_utilisateur_404(self):
        pk = self.other_tag.pk
        for name in ('tag_detail', 'tag_update', 'tag_delete'):
            self.assertEqual(self.client.get(reverse(f'journal:{name}', args=[pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse('journal:tag_delete', args=[pk])).status_code, 404)


class HomeLinksTest(JournalViewsTestBase):

    def test_tableau_de_bord_lie_les_entrees(self):
        response = self.client.get(reverse('account:home'))
        self.assertContains(response, reverse('journal:entry_create'))
        self.assertContains(response, reverse('journal:entry_detail', args=[self.entry.pk]))
