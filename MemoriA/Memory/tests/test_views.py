# Tests d'intégration des vues de Memory (CRUD, permissions, validation)
import io
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from Journal.models import JournalEntry
from Memory.models import GeneratedImage, Retrospective, Story
from Memory.services import AIServiceError

PASSWORD = 'MotDePasse!2026'
MEDIA_TEST = tempfile.mkdtemp()
TEXT = "Nous sommes partis tôt le matin vers la plage, le soleil se levait à peine."


def make_png(name='image.png'):
    """Petite image PNG valide pour les uploads."""
    buffer = io.BytesIO()
    Image.new('RGB', (10, 10), color='orange').save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class MemoryViewsTestBase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    def setUp(self):
        self.today = timezone.localdate()
        self.user = User.objects.create_user('sarah', password=PASSWORD)
        self.other = User.objects.create_user('amine', password=PASSWORD)
        self.entry = JournalEntry.objects.create(user=self.user, title='Plage', content='...', entry_date=self.today)
        self.story = Story.objects.create(entry=self.entry, text=TEXT)
        # Données d'un autre utilisateur
        self.other_entry = JournalEntry.objects.create(user=self.other, title='Secret', content='...')
        self.other_story = Story.objects.create(entry=self.other_entry, text='Récit secret de Amine.')
        self.other_image = GeneratedImage.objects.create(story=self.other_story, image='generated/x.png', prompt='x')
        self.other_retro = Retrospective.objects.create(
            user=self.other, period_type='Semaine', title='Semaine secrète',
            start_date=self.today - timedelta(days=6), end_date=self.today)
        self.client.login(username='sarah', password=PASSWORD)


class LoginRequiredTest(MemoryViewsTestBase):

    def test_toutes_les_pages_redirigent_vers_la_connexion(self):
        self.client.logout()
        image = GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='a')
        retro = Retrospective.objects.create(user=self.user, period_type='Semaine', title='S',
                                             start_date=self.today - timedelta(days=2), end_date=self.today)
        urls = [
            reverse('memory:souvenirs'),
            reverse('memory:story_list'), reverse('memory:story_create'),
            reverse('memory:story_detail', args=[self.story.pk]),
            reverse('memory:story_update', args=[self.story.pk]),
            reverse('memory:story_delete', args=[self.story.pk]),
            reverse('memory:image_list'), reverse('memory:image_create', args=[self.story.pk]),
            reverse('memory:image_detail', args=[image.pk]),
            reverse('memory:image_update', args=[image.pk]),
            reverse('memory:image_delete', args=[image.pk]),
            reverse('memory:retrospective_list'), reverse('memory:retrospective_create'),
            reverse('memory:retrospective_detail', args=[retro.pk]),
            reverse('memory:retrospective_update', args=[retro.pk]),
            reverse('memory:retrospective_delete', args=[retro.pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('account:login'), response.url)
        response = self.client.post(reverse('memory:image_set_main', args=[image.pk]))
        self.assertEqual(response.status_code, 302)


class OtherUserDataTest(MemoryViewsTestBase):
    """Un utilisateur ne peut pas lire, modifier ou supprimer les données d'un autre (404)."""

    def test_recit_d_un_autre_404(self):
        for name in ('story_detail', 'story_update', 'story_delete'):
            response = self.client.get(reverse(f'memory:{name}', args=[self.other_story.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.client.post(reverse('memory:story_delete', args=[self.other_story.pk]))
        self.assertTrue(Story.objects.filter(pk=self.other_story.pk).exists())

    def test_image_d_un_autre_404(self):
        for name in ('image_detail', 'image_update', 'image_delete'):
            response = self.client.get(reverse(f'memory:{name}', args=[self.other_image.pk]))
            self.assertEqual(response.status_code, 404, name)
        response = self.client.post(reverse('memory:image_set_main', args=[self.other_image.pk]))
        self.assertEqual(response.status_code, 404)
        response = self.client.get(reverse('memory:image_create', args=[self.other_story.pk]))
        self.assertEqual(response.status_code, 404)

    def test_retrospective_d_un_autre_404(self):
        for name in ('retrospective_detail', 'retrospective_update', 'retrospective_delete'):
            response = self.client.get(reverse(f'memory:{name}', args=[self.other_retro.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.client.post(reverse('memory:retrospective_delete', args=[self.other_retro.pk]))
        self.assertTrue(Retrospective.objects.filter(pk=self.other_retro.pk).exists())

    def test_listes_ne_montrent_que_mes_donnees(self):
        for name in ('souvenirs', 'story_list', 'image_list', 'retrospective_list'):
            response = self.client.get(reverse(f'memory:{name}'))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'Secret')
            self.assertNotContains(response, 'Semaine secrète')

    def test_impossible_de_creer_un_recit_sur_l_entree_d_un_autre(self):
        response = self.client.post(reverse('memory:story_create'), {
            'entry': self.other_entry.pk, 'style': 'Simple', 'text': TEXT})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('entry'))

    def test_impossible_de_lier_l_entree_d_un_autre_a_une_retrospective(self):
        response = self.client.post(reverse('memory:retrospective_create'), {
            'title': 'Ma semaine', 'period_type': 'Semaine',
            'start_date': self.today - timedelta(days=6), 'end_date': self.today,
            'entries': [self.other_entry.pk]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('entries'))


class SouvenirsViewTest(MemoryViewsTestBase):

    def test_page_souvenirs(self):
        response = self.client.get(reverse('memory:souvenirs'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['story_count'], 1)
        self.assertContains(response, 'Plage')

    def test_etats_vides(self):
        self.story.delete()
        response = self.client.get(reverse('memory:souvenirs'))
        self.assertContains(response, "Aucun récit pour l'instant.")
        self.assertContains(response, "Aucune rétrospective pour l'instant.")


class StoryViewsTest(MemoryViewsTestBase):

    def test_liste_et_filtre_par_style(self):
        response = self.client.get(reverse('memory:story_list'))
        self.assertContains(response, 'Plage')
        response = self.client.get(reverse('memory:story_list'), {'style': 'Poetique'})
        self.assertContains(response, 'Aucun récit dans ce style.')

    def test_detail(self):
        response = self.client.get(reverse('memory:story_detail', args=[self.story.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, TEXT)

    def test_creation(self):
        entry = JournalEntry.objects.create(user=self.user, title='Montagne', content='...')
        response = self.client.post(reverse('memory:story_create'), {
            'entry': entry.pk, 'style': 'Poetique', 'text': TEXT})
        story = Story.objects.get(entry=entry)
        self.assertRedirects(response, reverse('memory:story_detail', args=[story.pk]))
        self.assertEqual(story.style, 'Poetique')
        self.assertEqual(story.version, 1)

    def test_creation_pre_remplie_depuis_une_entree(self):
        entry = JournalEntry.objects.create(user=self.user, title='Montagne', content='...')
        response = self.client.get(reverse('memory:story_create'), {'entry': entry.pk})
        self.assertEqual(response.context['form'].initial['entry'], str(entry.pk))

    def test_formulaire_ne_propose_que_les_entrees_sans_recit(self):
        entry = JournalEntry.objects.create(user=self.user, title='Montagne', content='...')
        response = self.client.get(reverse('memory:story_create'))
        self.assertEqual(list(response.context['form'].fields['entry'].queryset), [entry])

    def test_aucune_entree_disponible(self):
        response = self.client.get(reverse('memory:story_create'))
        self.assertContains(response, 'Écrivez d\'abord une entrée')

    def test_texte_trop_court_refuse(self):
        entry = JournalEntry.objects.create(user=self.user, title='Montagne', content='...')
        response = self.client.post(reverse('memory:story_create'), {
            'entry': entry.pk, 'style': 'Simple', 'text': 'Trop court'})
        self.assertContains(response, 'Le récit doit contenir au moins 20 caractères.')
        self.assertFalse(Story.objects.filter(entry=entry).exists())

    def test_modification(self):
        response = self.client.post(reverse('memory:story_update', args=[self.story.pk]), {
            'style': 'Humoristique', 'text': TEXT + ' Et un crabe a volé mon sandwich !'}, follow=True)
        self.assertContains(response, 'Le récit a été modifié.')
        self.story.refresh_from_db()
        self.assertEqual(self.story.style, 'Humoristique')

    def test_suppression_avec_confirmation(self):
        response = self.client.get(reverse('memory:story_delete', args=[self.story.pk]))
        self.assertContains(response, 'Voulez-vous vraiment supprimer')
        response = self.client.post(reverse('memory:story_delete', args=[self.story.pk]), follow=True)
        self.assertRedirects(response, reverse('memory:story_list'))
        self.assertContains(response, 'Le récit a été supprimé.')
        self.assertFalse(Story.objects.filter(pk=self.story.pk).exists())
        self.assertTrue(JournalEntry.objects.filter(pk=self.entry.pk).exists())


class ImageViewsTest(MemoryViewsTestBase):

    def add_image(self, **data):
        values = {'image': make_png(), 'prompt': 'Un coucher de soleil sur la mer', 'style': 'Aquarelle'}
        values.update(data)
        return self.client.post(reverse('memory:image_create', args=[self.story.pk]), values)

    def test_ajout_premiere_image_devient_principale(self):
        response = self.add_image()
        self.assertRedirects(response, reverse('memory:story_detail', args=[self.story.pk]))
        image = self.story.images.get()
        self.assertTrue(image.is_main)
        self.assertEqual(image.style, 'Aquarelle')

    def test_ajout_image_principale_remplace_l_ancienne(self):
        self.add_image()
        self.add_image(is_main='on')
        self.assertEqual(self.story.images.filter(is_main=True).count(), 1)
        self.assertTrue(self.story.images.latest('creation_date').is_main)

    def test_mauvais_format_refuse(self):
        fichier = SimpleUploadedFile('notes.txt', b'pas une image', content_type='text/plain')
        response = self.add_image(image=fichier)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors.get('image'))
        self.assertFalse(self.story.images.exists())

    def test_image_trop_lourde_refusee(self):
        from Memory import forms
        original = forms.IMAGE_MAX_SIZE
        forms.IMAGE_MAX_SIZE = 10          # 10 octets pour le test
        try:
            response = self.add_image()
        finally:
            forms.IMAGE_MAX_SIZE = original
        self.assertIn("L'image ne doit pas dépasser 10 Mo.", response.context['form'].errors['image'])

    def test_choisir_image_principale(self):
        self.add_image()
        self.add_image()
        second = self.story.images.get(is_main=False)
        response = self.client.post(reverse('memory:image_set_main', args=[second.pk]))
        self.assertRedirects(response, reverse('memory:story_detail', args=[self.story.pk]))
        second.refresh_from_db()
        self.assertTrue(second.is_main)
        self.assertEqual(self.story.images.filter(is_main=True).count(), 1)

    def test_choisir_principale_refuse_en_get(self):
        self.add_image()
        image = self.story.images.get()
        response = self.client.get(reverse('memory:image_set_main', args=[image.pk]))
        self.assertEqual(response.status_code, 405)

    def test_liste_detail_modification_suppression(self):
        self.add_image()
        image = self.story.images.get()
        self.assertContains(self.client.get(reverse('memory:image_list')), 'Un coucher de soleil')
        self.assertEqual(self.client.get(reverse('memory:image_detail', args=[image.pk])).status_code, 200)

        response = self.client.post(reverse('memory:image_update', args=[image.pk]), {
            'prompt': 'Un phare au crépuscule', 'style': 'Dessin', 'is_main': 'on'})
        self.assertRedirects(response, reverse('memory:image_detail', args=[image.pk]))
        image.refresh_from_db()
        self.assertEqual(image.style, 'Dessin')

        response = self.client.post(reverse('memory:image_delete', args=[image.pk]))
        self.assertRedirects(response, reverse('memory:story_detail', args=[self.story.pk]))
        self.assertFalse(GeneratedImage.objects.filter(pk=image.pk).exists())

    def test_etat_vide(self):
        response = self.client.get(reverse('memory:image_list'))
        self.assertContains(response, "Aucune illustration pour l'instant.")


class RetrospectiveViewsTest(MemoryViewsTestBase):

    def data(self, **values):
        data = {'title': 'Ma semaine au soleil', 'period_type': 'Semaine',
                'start_date': self.today - timedelta(days=6), 'end_date': self.today,
                'summary': 'Une belle semaine.', 'keywords_text': 'plage, famille , , soleil',
                'entries': [self.entry.pk]}
        data.update(values)
        return data

    def test_creation(self):
        response = self.client.post(reverse('memory:retrospective_create'), self.data())
        retro = Retrospective.objects.get(user=self.user)
        self.assertRedirects(response, reverse('memory:retrospective_detail', args=[retro.pk]))
        self.assertEqual(retro.keywords, ['plage', 'famille', 'soleil'])
        self.assertEqual(list(retro.entries.all()), [self.entry])

    def test_utilisateur_rempli_par_la_vue(self):
        # Même si on essaie d'envoyer un autre user, c'est l'utilisateur connecté qui est enregistré
        self.client.post(reverse('memory:retrospective_create'), self.data(user=self.other.pk))
        self.assertEqual(Retrospective.objects.get(title='Ma semaine au soleil').user, self.user)

    def test_fin_avant_debut_refusee(self):
        response = self.client.post(reverse('memory:retrospective_create'), self.data(
            start_date=self.today, end_date=self.today - timedelta(days=3), entries=[]))
        self.assertContains(response, 'La date de fin doit être après la date de début.')

    def test_semaine_trop_longue_refusee(self):
        response = self.client.post(reverse('memory:retrospective_create'), self.data(
            start_date=self.today - timedelta(days=20), entries=[]))
        self.assertContains(response, 'ne peut pas dépasser 7 jours')

    def test_mois_accepte_31_jours(self):
        response = self.client.post(reverse('memory:retrospective_create'), self.data(
            period_type='Mois', start_date=self.today - timedelta(days=31)))
        self.assertEqual(response.status_code, 302)

    def test_debut_dans_le_futur_refuse(self):
        response = self.client.post(reverse('memory:retrospective_create'), self.data(
            start_date=self.today + timedelta(days=1), end_date=self.today + timedelta(days=5), entries=[]))
        self.assertContains(response, 'La période ne peut pas commencer dans le futur.')

    def test_entree_hors_periode_refusee(self):
        old = JournalEntry.objects.create(user=self.user, title='Vieille entrée', content='...',
                                          entry_date=self.today - timedelta(days=40))
        response = self.client.post(reverse('memory:retrospective_create'), self.data(entries=[old.pk]))
        self.assertContains(response, 'Ces entrées sont en dehors de la période : Vieille entrée.')

    def test_liste_filtre_detail(self):
        self.client.post(reverse('memory:retrospective_create'), self.data())
        retro = Retrospective.objects.get(user=self.user)
        self.assertContains(self.client.get(reverse('memory:retrospective_list')), 'Ma semaine au soleil')
        response = self.client.get(reverse('memory:retrospective_list'), {'period': 'Mois'})
        self.assertContains(response, 'Aucune rétrospective pour cette période.')
        response = self.client.get(reverse('memory:retrospective_detail', args=[retro.pk]))
        self.assertContains(response, 'famille')
        self.assertContains(response, 'Plage')

    def test_modification_et_suppression(self):
        self.client.post(reverse('memory:retrospective_create'), self.data())
        retro = Retrospective.objects.get(user=self.user)
        response = self.client.get(reverse('memory:retrospective_update', args=[retro.pk]))
        self.assertEqual(response.context['form'].fields['keywords_text'].initial, 'plage, famille, soleil')

        response = self.client.post(reverse('memory:retrospective_update', args=[retro.pk]),
                                    self.data(title='Semaine modifiée', keywords_text='mer'), follow=True)
        self.assertContains(response, 'La rétrospective a été modifiée.')
        retro.refresh_from_db()
        self.assertEqual(retro.keywords, ['mer'])

        response = self.client.post(reverse('memory:retrospective_delete', args=[retro.pk]))
        self.assertRedirects(response, reverse('memory:retrospective_list'))
        self.assertFalse(Retrospective.objects.filter(pk=retro.pk).exists())

    def test_image_de_couverture_limitee_a_mes_images(self):
        mine = GeneratedImage.objects.create(story=self.story, image='generated/m.png', prompt='m')
        response = self.client.get(reverse('memory:retrospective_create'))
        self.assertEqual(list(response.context['form'].fields['cover_image'].queryset), [mine])


class MemoryAccessibilityTest(MemoryViewsTestBase):

    def test_un_seul_h1_et_titres_uniques(self):
        GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='a')
        urls = [reverse('memory:souvenirs'), reverse('memory:story_list'), reverse('memory:story_create'),
                reverse('memory:story_detail', args=[self.story.pk]),
                reverse('memory:story_update', args=[self.story.pk]),
                reverse('memory:image_list'), reverse('memory:retrospective_list'),
                reverse('memory:retrospective_create')]
        titles = set()
        for url in urls:
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('<h1'), 1, url)
            titles.add(html.split('<title>')[1].split('</title>')[0])
        self.assertEqual(len(titles), len(urls))

    def test_images_avec_texte_alternatif(self):
        GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='Un phare breton')
        html = self.client.get(reverse('memory:story_detail', args=[self.story.pk])).content.decode()
        self.assertIn('alt="Un phare breton"', html)

    def test_cases_a_cocher_dans_un_fieldset(self):
        html = self.client.get(reverse('memory:retrospective_create')).content.decode()
        self.assertIn('<legend', html)
        self.assertIn('Entrées de la période', html)


class AIViewsTest(MemoryViewsTestBase):
    """Boutons IA : la vue lance la tâche puis affiche l'indicateur de chargement."""

    @patch('Memory.services.call_gemini', return_value=TEXT)
    def test_generer_un_recit(self, mock_gemini):
        entry = JournalEntry.objects.create(user=self.user, title='Montagne', content='Randonnée.')
        response = self.client.post(reverse('memory:story_generate'), {'ia-entry': entry.pk, 'ia-style': 'Poetique'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'Memory/story_generating.html')
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'data-ai-task')
        self.assertEqual(Story.objects.get(entry=entry).style, 'Poetique')     # mode eager : déjà fait

    def test_generer_refuse_l_entree_d_un_autre(self):
        response = self.client.post(reverse('memory:story_generate'),
                                    {'ia-entry': self.other_entry.pk, 'ia-style': 'Simple'})
        self.assertRedirects(response, reverse('memory:story_create'))
        self.assertEqual(Story.objects.filter(entry=self.other_entry).count(), 1)

    def test_generer_refuse_en_get(self):
        self.assertEqual(self.client.get(reverse('memory:story_generate')).status_code, 405)

    @patch('Memory.services.call_gemini', return_value=TEXT + ' Nouvelle version.')
    def test_regenerer(self, mock_gemini):
        response = self.client.post(reverse('memory:story_regenerate', args=[self.story.pk]), {'style': 'Humoristique'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('?task=', response.url)
        self.story.refresh_from_db()
        self.assertEqual(self.story.version, 2)
        page = self.client.get(response.url)
        self.assertContains(page, 'Nouvelle version du récit en cours')
        self.assertContains(page, 'name="style" value="Humoristique"')       # bouton Réessayer

    def test_regenerer_le_recit_d_un_autre_404(self):
        response = self.client.post(reverse('memory:story_regenerate', args=[self.other_story.pk]))
        self.assertEqual(response.status_code, 404)

    @patch('Memory.services.call_gemini', side_effect=Exception('boom'))
    def test_erreur_de_l_ia_affiche_un_message(self, mock_gemini):
        # call_gemini est appelé dans log_ai_call : l'erreur brute est transformée par launch_task
        response = self.client.post(reverse('memory:story_regenerate', args=[self.story.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Réessayez dans un instant')
        self.story.refresh_from_db()
        self.assertEqual(self.story.version, 1)

    @patch('Memory.services.call_gemini', side_effect=AIServiceError(
        'Le service de rédaction (Gemini) ne répond pas. Réessayez dans un instant.'))
    def test_message_clair_de_l_ia(self, mock_gemini):
        response = self.client.post(reverse('memory:story_regenerate', args=[self.story.pk]), follow=True)
        self.assertContains(response, 'Le service de rédaction (Gemini) ne répond pas.')

    @patch('Memory.tasks.generate_story_task.delay', side_effect=ConnectionError('Redis absent'))
    def test_redis_indisponible(self, mock_delay):
        response = self.client.post(reverse('memory:story_regenerate', args=[self.story.pk]), follow=True)
        self.assertContains(response, 'Redis ou le worker Celery ne répond pas')

    @patch('Memory.services.get_hf_client')
    @patch('Memory.services.call_gemini', return_value='A beach')
    def test_creer_une_illustration(self, mock_gemini, mock_hf):
        mock_hf.return_value.text_to_image.return_value = Image.new('RGB', (8, 8))
        response = self.client.post(reverse('memory:image_generate', args=[self.story.pk]), {'style': 'Dessin'})
        self.assertIn('type=image', response.url)
        self.assertEqual(self.story.images.get().style, 'Dessin')
        page = self.client.get(response.url)
        self.assertContains(page, "Création de l'illustration")
        self.assertContains(page, 'data-result-target="#nouvelle-image"')

    def test_illustration_avec_la_photo_d_une_autre_entree_refusee(self):
        from Face.models import Photo
        photo = Photo.objects.create(entry=self.other_entry, image='photos/x.jpg')
        response = self.client.post(reverse('memory:image_generate', args=[self.story.pk]),
                                    {'style': 'Dessin', 'source_photo': photo.pk}, follow=True)
        self.assertContains(response, 'Choisissez un style valide')
        self.assertFalse(self.story.images.exists())

    def test_illustrer_le_recit_d_un_autre_404(self):
        response = self.client.post(reverse('memory:image_generate', args=[self.other_story.pk]), {'style': 'Dessin'})
        self.assertEqual(response.status_code, 404)

    @patch('Memory.services.get_keybert_model')
    def test_analyser_une_retrospective(self, mock_model):
        mock_model.return_value.extract_keywords.return_value = [('plage', 0.9)]
        retro = Retrospective.objects.create(user=self.user, period_type='Semaine', title='S',
                                             start_date=self.today - timedelta(days=3), end_date=self.today)
        response = self.client.post(reverse('memory:retrospective_analyze', args=[retro.pk]))
        self.assertIn('?task=', response.url)
        retro.refresh_from_db()
        self.assertEqual(retro.keywords, ['plage'])
        page = self.client.get(response.url)
        self.assertContains(page, 'Analyse des thèmes')
        self.assertContains(page, 'Répartition des émotions')

    def test_analyser_la_retrospective_d_un_autre_404(self):
        response = self.client.post(reverse('memory:retrospective_analyze', args=[self.other_retro.pk]))
        self.assertEqual(response.status_code, 404)

    def test_boutons_ia_non_connecte(self):
        self.client.logout()
        for url in (reverse('memory:story_generate'), reverse('memory:story_regenerate', args=[self.story.pk]),
                    reverse('memory:image_generate', args=[self.story.pk]),
                    reverse('memory:retrospective_analyze', args=[self.other_retro.pk])):
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse('account:login'), response.url)
