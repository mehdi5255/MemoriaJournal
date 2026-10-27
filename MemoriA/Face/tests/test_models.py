# Tests des modèles de Face
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from Face.models import Photo, Person, FaceTag
from Journal.models import JournalEntry


class FaceModelsTestBase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.entry = JournalEntry.objects.create(user=self.user, title='Anniversaire', content='...')
        self.photo = Photo.objects.create(entry=self.entry, image='photos/a.jpg')


class PhotoModelTest(FaceModelsTestBase):

    def test_str_avec_legende(self):
        self.photo.caption = 'Un gâteau sur une table'
        self.assertEqual(str(self.photo), 'Un gâteau sur une table')

    def test_str_sans_legende(self):
        self.assertEqual(str(self.photo), f"Photo {self.photo.id}")

    def test_valeurs_par_defaut(self):
        self.assertEqual(self.photo.detected_objects, [])
        self.assertFalse(self.photo.faces_analyzed)


class PersonModelTest(FaceModelsTestBase):

    def test_str(self):
        person = Person.objects.create(user=self.user, name='Sarah')
        self.assertEqual(str(person), 'Sarah')

    def test_relation_par_defaut(self):
        person = Person.objects.create(user=self.user, name='Sarah')
        self.assertEqual(person.relation, 'Ami')

    def test_nom_unique_par_utilisateur(self):
        Person.objects.create(user=self.user, name='Sarah')
        with self.assertRaises(IntegrityError):
            Person.objects.create(user=self.user, name='Sarah')


class FaceTagModelTest(FaceModelsTestBase):

    def make_tag(self, **kwargs):
        return FaceTag(photo=self.photo, x=10, y=20, width=50, height=50, **kwargs)

    def test_str(self):
        tag = self.make_tag()
        tag.save()
        self.assertEqual(str(tag), f"Visage sur photo {self.photo.id}")

    def test_clean_confiance_negative_refusee(self):
        with self.assertRaises(ValidationError):
            self.make_tag(confidence=-0.5).clean()

    def test_clean_confiance_superieure_a_1_refusee(self):
        with self.assertRaises(ValidationError):
            self.make_tag(confidence=1.2).clean()

    def test_clean_confiance_valide(self):
        self.make_tag(confidence=0.87).clean()

    def test_suppression_personne_garde_visage(self):
        person = Person.objects.create(user=self.user, name='Sarah')
        tag = self.make_tag(person=person)
        tag.save()
        person.delete()
        tag.refresh_from_db()
        self.assertIsNone(tag.person)

    def test_suppression_photo_supprime_visages(self):
        self.make_tag().save()
        self.photo.delete()
        self.assertEqual(FaceTag.objects.count(), 0)


class FaceBusinessMethodsTest(FaceModelsTestBase):

    def test_alt_text_legende(self):
        self.photo.caption = 'Deux amis sur la plage'
        self.assertEqual(self.photo.alt_text(), 'Deux amis sur la plage')

    def test_alt_text_sans_legende(self):
        self.assertEqual(self.photo.alt_text(),
                         f"Photo de l'entrée « Anniversaire » du {self.entry.entry_date:%d/%m/%Y}")

    def test_box_style_en_pourcentages(self):
        tag = FaceTag(photo=self.photo, x=50, y=25, width=100, height=50)
        self.assertEqual(tag.box_style(200, 100), 'left:25.00%;top:25.00%;width:50.00%;height:50.00%')

    def test_box_style_sans_dimensions(self):
        self.assertEqual(FaceTag(photo=self.photo, x=1, y=1, width=1, height=1).box_style(0, 0), '')
