# Tests d'intégration des vues de Face (CRUD, permissions, validation des images)
import io
import shutil
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from Face.models import FaceTag, Person, Photo
from Journal.models import JournalEntry

PASSWORD = 'MotDePasse!2026'
MEDIA_TEST = tempfile.mkdtemp()


def image_bytes(fmt='PNG', size=(200, 100)):
    buffer = io.BytesIO()
    Image.new('RGB', size, color='orange').save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name='photo.png', fmt='PNG', content_type='image/png', size=(200, 100)):
    return SimpleUploadedFile(name, image_bytes(fmt, size), content_type=content_type)


@override_settings(MEDIA_ROOT=MEDIA_TEST)
class FaceViewsTestBase(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TEST, ignore_errors=True)

    def make_photo(self, entry, **kwargs):
        photo = Photo(entry=entry, **kwargs)
        photo.image.save('photo.png', ContentFile(image_bytes()), save=True)      # 200 x 100 pixels
        return photo

    def setUp(self):
        # Tests du CRUD : l'analyse IA lancée après un upload est simulée (testée dans test_services.py)
        patcher = patch('Face.tasks.analyze_photo_task.delay', return_value=MagicMock(id='tache-test'))
        self.mock_analyze = patcher.start()
        self.addCleanup(patcher.stop)
        self.today = timezone.localdate()
        self.user = User.objects.create_user('sabri', password=PASSWORD)
        self.other = User.objects.create_user('amine', password=PASSWORD)
        self.entry = JournalEntry.objects.create(user=self.user, title='Anniversaire', content='...')
        self.other_entry = JournalEntry.objects.create(user=self.other, title='Secret', content='...')
        self.photo = self.make_photo(self.entry, caption='Un gâteau sur la table')
        self.other_photo = self.make_photo(self.other_entry, caption='Photo secrète')
        self.sarah = Person.objects.create(user=self.user, name='Sarah', relation='Famille')
        self.other_person = Person.objects.create(user=self.other, name='Personne secrète')
        self.tag = FaceTag.objects.create(photo=self.photo, person=self.sarah, x=10, y=10, width=50, height=50,
                                          confidence=0.9)
        self.other_tag = FaceTag.objects.create(photo=self.other_photo, x=1, y=1, width=5, height=5)
        self.client.login(username='sabri', password=PASSWORD)


class LoginRequiredTest(FaceViewsTestBase):

    def test_toutes_les_pages_redirigent_vers_la_connexion(self):
        self.client.logout()
        urls = [reverse('face:person_list'), reverse('face:person_create'),
                reverse('face:person_detail', args=[self.sarah.pk]), reverse('face:person_update', args=[self.sarah.pk]),
                reverse('face:person_delete', args=[self.sarah.pk]),
                reverse('face:photo_list'), reverse('face:photo_create'),
                reverse('face:photo_detail', args=[self.photo.pk]), reverse('face:photo_update', args=[self.photo.pk]),
                reverse('face:photo_delete', args=[self.photo.pk]),
                reverse('face:face_list'), reverse('face:face_create', args=[self.photo.pk]),
                reverse('face:face_detail', args=[self.tag.pk]), reverse('face:face_update', args=[self.tag.pk]),
                reverse('face:face_delete', args=[self.tag.pk])]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn(reverse('account:login'), response.url)


class OtherUserDataTest(FaceViewsTestBase):
    """Un utilisateur ne peut pas lire, modifier ou supprimer les données d'un autre (404)."""

    def test_photo_d_un_autre_404(self):
        for name in ('photo_detail', 'photo_update', 'photo_delete', 'face_create'):
            response = self.client.get(reverse(f'face:{name}', args=[self.other_photo.pk]))
            self.assertEqual(response.status_code, 404, name)
        self.client.post(reverse('face:photo_delete', args=[self.other_photo.pk]))
        self.assertTrue(Photo.objects.filter(pk=self.other_photo.pk).exists())

    def test_personne_d_un_autre_404(self):
        for name in ('person_detail', 'person_update', 'person_delete'):
            response = self.client.get(reverse(f'face:{name}', args=[self.other_person.pk]))
            self.assertEqual(response.status_code, 404, name)

    def test_visage_d_un_autre_404(self):
        for name in ('face_detail', 'face_update', 'face_delete'):
            response = self.client.get(reverse(f'face:{name}', args=[self.other_tag.pk]))
            self.assertEqual(response.status_code, 404, name)

    def test_listes_ne_montrent_que_mes_donnees(self):
        for name in ('person_list', 'photo_list', 'face_list'):
            response = self.client.get(reverse(f'face:{name}'))
            self.assertNotContains(response, 'secrète')
            self.assertNotContains(response, 'Secret')

    def test_impossible_de_lier_la_personne_d_un_autre(self):
        response = self.client.post(reverse('face:face_update', args=[self.tag.pk]), {
            'person': self.other_person.pk, 'x': 10, 'y': 10, 'width': 50, 'height': 50})
        self.assertTrue(response.context['form'].errors.get('person'))

    def test_impossible_d_ajouter_une_photo_a_l_entree_d_un_autre(self):
        response = self.client.post(reverse('face:photo_create'), {'entry': self.other_entry.pk, 'image': upload()})
        self.assertTrue(response.context['form'].errors.get('entry'))


class PersonViewsTest(FaceViewsTestBase):

    def test_page_mes_proches(self):
        response = self.client.get(reverse('face:person_list'))
        self.assertContains(response, 'Sarah')
        self.assertEqual(response.context['persons'][0].photo_count, 1)

    def test_filtre_par_relation(self):
        response = self.client.get(reverse('face:person_list'), {'relation': 'Collegue'})
        self.assertContains(response, 'Aucun proche dans cette catégorie.')

    def test_compteur_de_visages_inconnus(self):
        FaceTag.objects.create(photo=self.photo, x=100, y=10, width=40, height=40)
        response = self.client.get(reverse('face:person_list'))
        self.assertEqual(response.context['unknown_faces'], 1)
        self.assertContains(response, '1 visage sans nom')

    def test_album_contient_seulement_ses_photos(self):
        other_photo = self.make_photo(self.entry, caption='Photo sans Sarah')
        response = self.client.get(reverse('face:person_detail', args=[self.sarah.pk]))
        self.assertEqual(list(response.context['photos']), [self.photo])
        self.assertContains(response, 'alt="Un gâteau sur la table"')
        self.assertNotContains(response, 'Photo sans Sarah')
        self.assertTrue(other_photo.pk)

    def test_creation(self):
        response = self.client.post(reverse('face:person_create'), {
            'name': 'Karim', 'relation': 'Ami', 'cover_photo': upload('karim.jpg', 'JPEG', 'image/jpeg')})
        karim = Person.objects.get(name='Karim')
        self.assertRedirects(response, reverse('face:person_detail', args=[karim.pk]))
        self.assertEqual(karim.user, self.user)

    def test_nom_en_double_refuse(self):
        response = self.client.post(reverse('face:person_create'), {'name': 'sarah', 'relation': 'Ami'})
        self.assertContains(response, 'Vous avez déjà un proche avec ce nom.')

    def test_meme_nom_qu_un_proche_d_un_autre_utilisateur(self):
        response = self.client.post(reverse('face:person_create'), {'name': 'Personne secrète', 'relation': 'Ami'})
        self.assertEqual(response.status_code, 302)

    def test_date_de_naissance_future_refusee(self):
        response = self.client.post(reverse('face:person_create'), {
            'name': 'Bébé', 'relation': 'Famille', 'birth_date': self.today + timedelta(days=3)})
        self.assertContains(response, 'La date ne peut pas être dans le futur.')

    def test_modification(self):
        response = self.client.post(reverse('face:person_update', args=[self.sarah.pk]), {
            'name': 'Sarah', 'relation': 'Ami', 'notes': 'Ma cousine'}, follow=True)
        self.assertContains(response, 'La fiche de Sarah a été modifiée.')

    def test_suppression_garde_les_visages(self):
        response = self.client.post(reverse('face:person_delete', args=[self.sarah.pk]))
        self.assertRedirects(response, reverse('face:person_list'))
        self.tag.refresh_from_db()
        self.assertIsNone(self.tag.person)

    def test_etat_vide(self):
        Person.objects.filter(user=self.user).delete()
        self.assertContains(self.client.get(reverse('face:person_list')), "Aucun proche pour l'instant.")


class PhotoViewsTest(FaceViewsTestBase):

    def test_galerie_polaroid_avec_alt(self):
        response = self.client.get(reverse('face:photo_list'))
        self.assertContains(response, 'class="polaroid')
        self.assertContains(response, 'alt="Un gâteau sur la table"')

    def test_filtre_sans_visage(self):
        sans = self.make_photo(self.entry, caption='Paysage')
        response = self.client.get(reverse('face:photo_list'), {'visages': 'non'})
        self.assertEqual(list(response.context['photos']), [sans])

    def test_detail_avec_cadres(self):
        response = self.client.get(reverse('face:photo_detail', args=[self.photo.pk]))
        self.assertContains(response, 'class="face-box"')
        self.assertContains(response, 'left:5.00%;top:10.00%;width:25.00%;height:50.00%')   # 10/200, 10/100...
        self.assertContains(response, 'Sarah')

    def test_detail_si_le_fichier_a_disparu(self):
        self.photo.image.delete(save=False)
        response = self.client.get(reverse('face:photo_detail', args=[self.photo.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Image introuvable')

    def test_ajout_jpeg_png_webp(self):
        for name, fmt, content_type in (('a.jpg', 'JPEG', 'image/jpeg'), ('b.png', 'PNG', 'image/png'),
                                        ('c.webp', 'WEBP', 'image/webp')):
            response = self.client.post(reverse('face:photo_create'), {
                'entry': self.entry.pk, 'image': upload(name, fmt, content_type), 'caption': name})
            self.assertEqual(response.status_code, 302, name)
        self.assertEqual(Photo.objects.filter(caption__in=['a.jpg', 'b.png', 'c.webp']).count(), 3)

    def test_faux_fichier_image_refuse(self):
        fichier = SimpleUploadedFile('virus.jpg', b'Ceci n est pas une image', content_type='image/jpeg')
        response = self.client.post(reverse('face:photo_create'), {'entry': self.entry.pk, 'image': fichier})
        self.assertTrue(response.context['form'].errors.get('image'))

    def test_format_gif_refuse(self):
        response = self.client.post(reverse('face:photo_create'), {
            'entry': self.entry.pk, 'image': upload('anim.gif', 'GIF', 'image/gif')})
        self.assertIn('Format non accepté', ' '.join(response.context['form'].errors['image']))

    def test_image_trop_lourde_refusee(self):
        from Face import forms
        original = forms.IMAGE_MAX_SIZE
        forms.IMAGE_MAX_SIZE = 100
        try:
            response = self.client.post(reverse('face:photo_create'), {'entry': self.entry.pk, 'image': upload()})
        finally:
            forms.IMAGE_MAX_SIZE = original
        self.assertIn("L'image ne doit pas dépasser 10 Mo.", response.context['form'].errors['image'])

    def test_date_future_refusee(self):
        response = self.client.post(reverse('face:photo_create'), {
            'entry': self.entry.pk, 'image': upload(), 'taken_date': self.today + timedelta(days=1)})
        self.assertContains(response, 'La date ne peut pas être dans le futur.')

    def test_creation_pre_remplie_et_accept(self):
        response = self.client.get(reverse('face:photo_create'), {'entry': self.entry.pk})
        self.assertEqual(response.context['form'].initial['entry'], str(self.entry.pk))
        self.assertContains(response, 'accept="image/jpeg,image/png,image/webp"')

    def test_modification_sans_changer_l_image(self):
        response = self.client.post(reverse('face:photo_update', args=[self.photo.pk]), {
            'entry': self.entry.pk, 'caption': 'Le gâteau au chocolat', 'location': 'Tunis'}, follow=True)
        self.assertContains(response, 'La photo a été modifiée.')
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.location, 'Tunis')

    def test_suppression_supprime_les_visages(self):
        response = self.client.post(reverse('face:photo_delete', args=[self.photo.pk]))
        self.assertRedirects(response, reverse('face:photo_list'))
        self.assertFalse(FaceTag.objects.filter(pk=self.tag.pk).exists())

    def test_sans_entree_message_clair(self):
        JournalEntry.objects.filter(user=self.user).delete()
        self.assertContains(self.client.get(reverse('face:photo_create')), "écrivez d'abord une entrée")


class FaceTagViewsTest(FaceViewsTestBase):

    def data(self, **values):
        data = {'person': self.sarah.pk, 'x': 100, 'y': 20, 'width': 60, 'height': 60}
        data.update(values)
        return data

    def test_liste_et_filtres(self):
        unknown = FaceTag.objects.create(photo=self.photo, x=120, y=5, width=30, height=30)
        response = self.client.get(reverse('face:face_list'), {'etat': 'inconnus'})
        self.assertEqual(list(response.context['faces']), [unknown])
        response = self.client.get(reverse('face:face_list'), {'etat': 'a-confirmer'})
        self.assertEqual(list(response.context['faces']), [self.tag])

    def test_detail(self):
        response = self.client.get(reverse('face:face_detail', args=[self.tag.pk]))
        self.assertContains(response, 'Visage de Sarah')
        self.assertContains(response, 'class="face-box"')

    def test_ajout_manuel_confiance_totale(self):
        response = self.client.post(reverse('face:face_create', args=[self.photo.pk]), self.data())
        self.assertRedirects(response, reverse('face:photo_detail', args=[self.photo.pk]))
        tag = FaceTag.objects.latest('id')
        self.assertEqual((tag.photo, tag.person, tag.confidence), (self.photo, self.sarah, 1.0))

    def test_cadre_qui_depasse_refuse(self):
        response = self.client.post(reverse('face:face_create', args=[self.photo.pk]), self.data(x=180, width=50))
        self.assertContains(response, 'Le cadre dépasse de la photo (200 x 100 pixels).')

    def test_cadre_vide_refuse(self):
        response = self.client.post(reverse('face:face_create', args=[self.photo.pk]), self.data(width=0))
        self.assertContains(response, 'au moins 1 pixel')

    def test_limites_dans_le_formulaire(self):
        response = self.client.get(reverse('face:face_create', args=[self.photo.pk]))
        self.assertContains(response, 'max="200"')
        self.assertContains(response, 'max="100"')

    def test_identifier_et_confirmer(self):
        karim = Person.objects.create(user=self.user, name='Karim')
        response = self.client.post(reverse('face:face_update', args=[self.tag.pk]),
                                    self.data(person=karim.pk, x=10, y=10, width=50, height=50, is_confirmed='on'))
        self.assertRedirects(response, reverse('face:photo_detail', args=[self.photo.pk]))
        self.tag.refresh_from_db()
        self.assertEqual((self.tag.person, self.tag.is_confirmed), (karim, True))

    def test_suppression(self):
        response = self.client.post(reverse('face:face_delete', args=[self.tag.pk]))
        self.assertRedirects(response, reverse('face:photo_detail', args=[self.photo.pk]))
        self.assertFalse(FaceTag.objects.filter(pk=self.tag.pk).exists())


class FaceAccessibilityTest(FaceViewsTestBase):

    def test_un_seul_h1_et_titres_uniques(self):
        urls = [reverse('face:person_list'), reverse('face:person_create'),
                reverse('face:person_detail', args=[self.sarah.pk]), reverse('face:person_update', args=[self.sarah.pk]),
                reverse('face:photo_list'), reverse('face:photo_create'),
                reverse('face:photo_detail', args=[self.photo.pk]), reverse('face:photo_update', args=[self.photo.pk]),
                reverse('face:face_list'), reverse('face:face_create', args=[self.photo.pk]),
                reverse('face:face_detail', args=[self.tag.pk]), reverse('face:face_update', args=[self.tag.pk])]
        titles = set()
        for url in urls:
            html = self.client.get(url).content.decode()
            self.assertEqual(html.count('<h1'), 1, url)
            titles.add(html.split('<title>')[1].split('</title>')[0])
        self.assertEqual(len(titles), len(urls))

    def test_toutes_les_images_ont_un_alt(self):
        import re
        for url in (reverse('face:photo_list'), reverse('face:photo_detail', args=[self.photo.pk]),
                    reverse('face:person_detail', args=[self.sarah.pk])):
            html = self.client.get(url).content.decode()
            for img in re.findall(r'<img[^>]*>', html):
                self.assertIn('alt=', img, url)


@patch('Face.views.refresh_encodings')
class NewPersonFromFaceTest(FaceViewsTestBase):
    """Créer un proche directement depuis le formulaire « Identifier le visage »."""

    def data(self, **values):
        data = {'person': '', 'x': 20, 'y': 10, 'width': 40, 'height': 30}
        data.update(values)
        return data

    def test_formulaire_propose_une_nouvelle_personne(self, mock_refresh):
        response = self.client.get(reverse('face:face_update', args=[self.tag.pk]))
        self.assertContains(response, 'Ou ajouter une nouvelle personne')
        self.assertContains(response, 'name="new_person_name"')
        self.assertContains(response, '<h1', count=1)

    def test_identifier_avec_une_nouvelle_personne(self, mock_refresh):
        response = self.client.post(reverse('face:face_update', args=[self.tag.pk]),
                                    self.data(new_person_name='  Karim  ', new_person_relation='Collegue'), follow=True)
        karim = Person.objects.get(user=self.user, name='Karim')
        self.assertEqual(karim.relation, 'Collegue')
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.person, karim)
        self.assertTrue(self.tag.is_confirmed)
        # Le visage découpé (40 x 30 pixels) devient la photo de profil
        with karim.cover_photo.open('rb') as file:
            self.assertEqual(Image.open(file).size, (40, 30))
        self.assertContains(response, 'a été ajouté à vos proches')
        self.assertIn(karim, mock_refresh.call_args.args[1])        # empreinte du visage recalculée

    def test_ajout_d_un_visage_avec_une_nouvelle_personne(self, mock_refresh):
        self.client.post(reverse('face:face_create', args=[self.photo.pk]), self.data(new_person_name='Leïla'))
        tag = FaceTag.objects.latest('id')
        self.assertEqual(tag.person.name, 'Leïla')
        self.assertEqual(tag.person.user, self.user)

    def test_nom_deja_utilise(self, mock_refresh):
        response = self.client.post(reverse('face:face_update', args=[self.tag.pk]), self.data(new_person_name='sarah'))
        self.assertContains(response, 'Ce proche existe déjà')
        self.assertEqual(Person.objects.filter(user=self.user).count(), 1)

    def test_meme_nom_qu_un_proche_d_un_autre_utilisateur(self, mock_refresh):
        self.client.post(reverse('face:face_update', args=[self.tag.pk]),
                         self.data(new_person_name='Personne secrète'))
        self.assertTrue(Person.objects.filter(user=self.user, name='Personne secrète').exists())

    def test_proche_choisi_et_nouveau_nom_refuse(self, mock_refresh):
        response = self.client.post(reverse('face:face_update', args=[self.tag.pk]),
                                    self.data(person=self.sarah.pk, new_person_name='Karim'))
        self.assertContains(response, 'pas les deux')
        self.assertFalse(Person.objects.filter(name='Karim').exists())

    def test_nom_trop_court(self, mock_refresh):
        response = self.client.post(reverse('face:face_update', args=[self.tag.pk]), self.data(new_person_name='K'))
        self.assertContains(response, 'au moins 2 caractères')

    def test_sans_nouveau_nom_rien_n_est_cree(self, mock_refresh):
        self.client.post(reverse('face:face_update', args=[self.tag.pk]), self.data())
        self.assertEqual(Person.objects.filter(user=self.user).count(), 1)
        self.tag.refresh_from_db()
        self.assertIsNone(self.tag.person)

    def test_cadre_invalide_aucun_proche_cree(self, mock_refresh):
        self.client.post(reverse('face:face_update', args=[self.tag.pk]), self.data(x=190, new_person_name='Karim'))
        self.assertFalse(Person.objects.filter(name='Karim').exists())
