# Tests des modèles de Memory
from datetime import date

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from Journal.models import JournalEntry
from Memory.models import Story, GeneratedImage, Retrospective


class MemoryModelsTestBase(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.entry = JournalEntry.objects.create(user=self.user, title='Sortie en mer', content='...')


class StoryModelTest(MemoryModelsTestBase):

    def test_str(self):
        story = Story.objects.create(entry=self.entry, text='Il était une fois...')
        self.assertEqual(str(story), 'Récit : Sortie en mer')

    def test_valeurs_par_defaut(self):
        story = Story.objects.create(entry=self.entry, text='...')
        self.assertEqual(story.style, 'Simple')
        self.assertEqual(story.version, 1)

    def test_un_seul_recit_par_entree(self):
        Story.objects.create(entry=self.entry, text='Premier')
        with self.assertRaises(IntegrityError):
            Story.objects.create(entry=self.entry, text='Second')


class GeneratedImageModelTest(MemoryModelsTestBase):

    def setUp(self):
        super().setUp()
        self.story = Story.objects.create(entry=self.entry, text='...')

    def test_str(self):
        image = GeneratedImage.objects.create(story=self.story, image='generated/a.png',
                                              prompt='Un bateau', style='Aquarelle')
        self.assertEqual(str(image), f"Image {image.id} - Aquarelle")

    def test_valeurs_par_defaut(self):
        image = GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='Un bateau')
        self.assertEqual(image.style, 'Realiste')
        self.assertFalse(image.is_main)

    def test_plusieurs_images_par_recit(self):
        GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='1')
        GeneratedImage.objects.create(story=self.story, image='generated/b.png', prompt='2')
        self.assertEqual(self.story.images.count(), 2)

    def test_set_as_main_une_seule_principale(self):
        first = GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='1', is_main=True)
        second = GeneratedImage.objects.create(story=self.story, image='generated/b.png', prompt='2')
        second.set_as_main()
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_main)
        self.assertTrue(second.is_main)
        self.assertEqual(self.story.images.filter(is_main=True).count(), 1)

    def test_set_as_main_ne_touche_pas_les_autres_recits(self):
        other_entry = JournalEntry.objects.create(user=self.user, title='Autre', content='...')
        other_story = Story.objects.create(entry=other_entry, text='...')
        other_main = GeneratedImage.objects.create(story=other_story, image='generated/c.png', prompt='3', is_main=True)
        GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='1').set_as_main()
        other_main.refresh_from_db()
        self.assertTrue(other_main.is_main)

    def test_main_image(self):
        self.assertIsNone(self.story.main_image())
        first = GeneratedImage.objects.create(story=self.story, image='generated/a.png', prompt='1')
        self.assertEqual(self.story.main_image(), first)          # aucune principale : la première
        main = GeneratedImage.objects.create(story=self.story, image='generated/b.png', prompt='2', is_main=True)
        self.assertEqual(self.story.main_image(), main)


class RetrospectiveModelTest(MemoryModelsTestBase):

    def make_retro(self, start, end):
        return Retrospective(user=self.user, period_type='Semaine', title='Ma semaine',
                             start_date=start, end_date=end)

    def test_str(self):
        retro = self.make_retro(date(2026, 10, 1), date(2026, 10, 7))
        retro.save()
        self.assertEqual(str(retro), 'Ma semaine')

    def test_clean_fin_avant_debut_refusee(self):
        with self.assertRaises(ValidationError):
            self.make_retro(date(2026, 10, 7), date(2026, 10, 1)).clean()

    def test_clean_fin_egale_debut_refusee(self):
        with self.assertRaises(ValidationError):
            self.make_retro(date(2026, 10, 7), date(2026, 10, 7)).clean()

    def test_clean_dates_valides(self):
        self.make_retro(date(2026, 10, 1), date(2026, 10, 7)).clean()

    def test_mots_cles_par_defaut(self):
        retro = self.make_retro(date(2026, 10, 1), date(2026, 10, 7))
        retro.save()
        self.assertEqual(retro.keywords, [])
