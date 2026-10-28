# Tests des services IA de Journal (modèles et API toujours simulés)
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from Account.models import ActivityLog
from Account.services import AIServiceError
from Journal import services
from Journal.models import JournalEntry, Tag

# Vecteurs normalisés très simples : « plage » et « mer » se ressemblent, « examen » non
VECTORS = {'plage': [1.0, 0.0], 'mer': [0.8, 0.6], 'examen': [-0.6, 0.8]}


def fake_encode(texts):
    vectors = []
    for text in texts:
        key = next((k for k in VECTORS if k in text.lower()), 'examen')
        vectors.append(VECTORS[key])
    return vectors


class EmbeddingServiceTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.beach = JournalEntry.objects.create(user=self.user, title='Plage', content='Journée à la plage.')
        self.sea = JournalEntry.objects.create(user=self.user, title='Mer', content='Balade au bord de la mer.')
        self.exam = JournalEntry.objects.create(user=self.user, title='Examen', content='Examen de maths.')

    def test_cosinus(self):
        self.assertAlmostEqual(services.cosine([1, 0], [0.8, 0.6]), 0.8)
        self.assertEqual(services.cosine([1, 0], [0, 1]), 0)

    def test_texte_de_l_entree(self):
        self.beach.location = 'Hammamet'
        self.assertEqual(services.entry_text(self.beach), 'Plage. Journée à la plage. Hammamet')

    @patch('Journal.services.get_embedder')
    def test_encode_normalise_et_arrondi(self, mock_model):
        mock_model.return_value.encode.return_value = [[0.123456789, 0.5]]
        self.assertEqual(services.encode(['texte']), [[0.12346, 0.5]])
        self.assertTrue(mock_model.return_value.encode.call_args.kwargs['normalize_embeddings'])

    @patch('Journal.services.get_embedder', side_effect=OSError('modèle absent'))
    def test_encode_erreur_claire(self, mock_model):
        with self.assertRaisesMessage(AIServiceError, 'recherche par le sens'):
            services.encode(['texte'])

    @patch('Journal.services.encode', side_effect=fake_encode)
    def test_compute_embedding_enregistre_et_mesure(self, mock_encode):
        services.compute_embedding(self.beach, self.user)
        self.beach.refresh_from_db()
        self.assertEqual(self.beach.embedding, [1.0, 0.0])
        log = ActivityLog.objects.get(user=self.user)
        self.assertEqual((log.ai_service, log.status), ('MiniLM (local)', 'Succes'))

    @patch('Journal.services.encode', side_effect=fake_encode)
    def test_recherche_par_le_sens(self, mock_encode):
        results = services.semantic_search(self.user, 'la mer')
        # Les embeddings manquants sont calculés d'abord (en un seul appel), puis la requête
        self.assertEqual(mock_encode.call_count, 2)
        self.assertEqual([entry for entry, _ in results], [self.sea, self.beach])     # examen trop éloigné
        self.assertAlmostEqual(results[0][1], 1.0)

    def test_recherche_trop_courte(self):
        with self.assertRaises(AIServiceError):
            services.semantic_search(self.user, ' a ')

    @patch('Journal.services.encode', side_effect=fake_encode)
    def test_recherche_seulement_dans_mes_entrees(self, mock_encode):
        other = User.objects.create_user('amine', password='motdepasse123')
        JournalEntry.objects.create(user=other, title='Plage privée', content='plage', embedding=[1.0, 0.0])
        titles = [entry.title for entry, _ in services.semantic_search(self.user, 'plage')]
        self.assertNotIn('Plage privée', titles)

    @patch('Journal.services.encode', side_effect=fake_encode)
    def test_souvenirs_similaires_sans_appel_au_modele(self, mock_encode):
        services.fill_missing_embeddings(self.user)
        mock_encode.reset_mock()
        self.beach.refresh_from_db()
        similar = services.similar_entries(self.beach)
        self.assertEqual([entry for entry, _ in similar], [self.sea])
        mock_encode.assert_not_called()

    def test_souvenirs_similaires_sans_embedding(self):
        self.assertEqual(services.similar_entries(self.beach), [])


class TagSuggestionServiceTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.travel = Tag.objects.create(user=self.user, name='Voyage')
        self.school = Tag.objects.create(user=self.user, name='Études')
        self.text = "Nous avons pris le train pour Tozeur, quel beau voyage."

    @patch('Journal.services.call_hf_zero_shot', return_value={'Voyage': 0.95, 'Études': 0.1})
    def test_suggestion(self, mock_api):
        self.assertEqual(services.suggest_tags(self.user, self.text), [(self.travel, 0.95)])
        self.assertEqual(sorted(mock_api.call_args.args[1]), ['Voyage', 'Études'])

    @patch('Journal.services.call_hf_zero_shot', return_value={'Voyage': 0.6, 'Études': 0.9})
    def test_tri_du_plus_sur(self, mock_api):
        self.assertEqual([t for t, _ in services.suggest_tags(self.user, self.text)], [self.school, self.travel])

    def test_sans_etiquette(self):
        Tag.objects.all().delete()
        with self.assertRaisesMessage(AIServiceError, "Créez d'abord"):
            services.suggest_tags(self.user, self.text)

    def test_texte_trop_court(self):
        with self.assertRaises(AIServiceError):
            services.suggest_tags(self.user, 'Bof.')

    @override_settings(JOURNAL_BACKEND='huggingface')
    @patch('Journal.services.call_hf_zero_shot', side_effect=AIServiceError('Le service ne répond pas.'))
    def test_echec_de_l_api(self, mock_api):
        with self.assertRaisesMessage(AIServiceError, 'ne répond pas'):
            services.suggest_tags(self.user, self.text)
        self.assertEqual(ActivityLog.objects.get().status, 'Echec')

    @override_settings(JOURNAL_BACKEND='auto')
    @patch('Journal.services.call_local_zero_shot', return_value={'Voyage': 0.8, 'Études': 0.2})
    @patch('Journal.services.call_hf_zero_shot', side_effect=AIServiceError('402'))
    def test_secours_local(self, mock_api, mock_local):
        self.assertEqual(services.suggest_tags(self.user, self.text), [(self.travel, 0.8)])
        services_used = list(ActivityLog.objects.order_by('id').values_list('ai_service', 'status'))
        self.assertEqual(services_used, [('mDeBERTa zero-shot', 'Echec'), ('mDeBERTa zero-shot (local)', 'Succes')])

    @override_settings(HF_TOKEN='jeton-de-test')
    @patch('Journal.services.get_hf_client')
    def test_appel_hugging_face(self, mock_client):
        mock_client.return_value.zero_shot_classification.return_value = [
            SimpleNamespace(label='Voyage', score=0.9), SimpleNamespace(label='Études', score=0.2)]
        self.assertEqual(services.call_hf_zero_shot(self.text, ['Voyage', 'Études']), {'Voyage': 0.9, 'Études': 0.2})
        self.assertFalse(mock_client.return_value.zero_shot_classification.call_args.kwargs['multi_label'])

    @patch('Journal.services.get_hf_client')
    def test_appel_hugging_face_en_erreur(self, mock_client):
        mock_client.return_value.zero_shot_classification.side_effect = ConnectionError()
        with self.assertRaisesMessage(AIServiceError, 'ne répond pas'):
            services.call_hf_zero_shot(self.text, ['Voyage'])

    def test_cle_absente(self):
        services.get_hf_client.cache_clear()
        with self.assertRaisesMessage(AIServiceError, 'HF_TOKEN'):
            services.get_hf_client()

    @patch('Journal.services.get_local_zero_shot')
    def test_modele_local(self, mock_pipeline):
        mock_pipeline.return_value = MagicMock(return_value={'labels': ['Voyage', 'Études'], 'scores': [0.9, 0.1]})
        self.assertEqual(services.call_local_zero_shot(self.text, ['Voyage', 'Études']), {'Voyage': 0.9, 'Études': 0.1})

    @patch('Journal.services.get_local_zero_shot', side_effect=OSError())
    def test_modele_local_en_erreur(self, mock_pipeline):
        with self.assertRaises(AIServiceError):
            services.call_local_zero_shot(self.text, ['Voyage'])


@patch('Journal.services.encode', side_effect=fake_encode)
class JournalAIViewsTest(TestCase):
    """Vues IA : en test, Celery exécute les tâches tout de suite (mode eager)."""

    def setUp(self):
        self.user = User.objects.create_user('sarah', password='motdepasse123')
        self.client.login(username='sarah', password='motdepasse123')
        self.beach = JournalEntry.objects.create(user=self.user, title='Plage', content='Journée à la plage.')
        self.sea = JournalEntry.objects.create(user=self.user, title='Mer', content='Balade au bord de la mer.')
        self.tag = Tag.objects.create(user=self.user, name='Voyage')

    def test_recherche_par_le_sens_lance_la_tache(self, mock_encode):
        response = self.client.get(reverse('journal:entry_list'), {'q': 'mer', 'mode': 'sens'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['semantic'])
        self.assertTrue(response.context['task_id'])
        self.assertContains(response, 'aria-live="polite"')
        self.assertContains(response, 'id="resultats-sens"')

    def test_tache_de_recherche(self, mock_encode):
        from Journal.tasks import semantic_search_task
        result = semantic_search_task(self.user.pk, 'mer')
        self.assertIn('Mer', result['html'])
        self.assertIn('par le sens', result['message'])

    def test_bouton_reessayer_de_la_recherche(self, mock_encode):
        response = self.client.post(reverse('journal:semantic_search'), {'q': 'mer'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('mode=sens', response.url)
        self.assertIn('task=', response.url)

    def test_detail_affiche_les_souvenirs_similaires(self, mock_encode):
        services.fill_missing_embeddings(self.user)
        response = self.client.get(reverse('journal:entry_detail', args=[self.beach.pk]))
        self.assertContains(response, '3 souvenirs similaires')
        self.assertEqual([e for e, _ in response.context['similar']], [self.sea])

    def test_analyser_une_entree(self, mock_encode):
        response = self.client.post(reverse('journal:entry_analyze', args=[self.beach.pk]))
        self.assertIn('?task=', response.url)
        self.beach.refresh_from_db()
        self.assertIsNotNone(self.beach.embedding)

    def test_analyser_l_entree_d_un_autre_utilisateur_404(self, mock_encode):
        other = User.objects.create_user('amine', password='motdepasse123')
        entry = JournalEntry.objects.create(user=other, title='Privée', content='...')
        self.assertEqual(self.client.post(reverse('journal:entry_analyze', args=[entry.pk])).status_code, 404)

    def test_echec_du_modele_n_empeche_pas_l_enregistrement(self, mock_encode):
        mock_encode.side_effect = AIServiceError("Le modèle de recherche par le sens n'a pas pu lire le texte.")
        response = self.client.post(reverse('journal:entry_create'),
                                    {'title': 'Soirée', 'content': 'Un bon film.', 'entry_date': '2026-01-10'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(JournalEntry.objects.filter(title='Soirée').exists())
        self.assertContains(response, 'pas pu lire le texte')

    @patch('Journal.services.call_hf_zero_shot', return_value={'Voyage': 0.93})
    def test_suggestion_d_etiquettes(self, mock_api, mock_encode):
        response = self.client.post(reverse('journal:tag_suggest'),
                                    {'title': 'Tozeur', 'content': 'Un long voyage en train dans le désert.'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('/ai/task/', data['status_url'])
        from Journal.tasks import suggest_tags_task
        result = suggest_tags_task(self.user.pk, 'Un long voyage en train dans le désert.')
        self.assertEqual(result['tag_ids'], [self.tag.pk])
        self.assertIn('Voyage', result['message'])

    def test_suggestion_texte_trop_court(self, mock_encode):
        response = self.client.post(reverse('journal:tag_suggest'), {'title': '', 'content': 'Bof'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('quelques phrases', response.json()['error'])

    def test_suggestion_sans_etiquette(self, mock_encode):
        Tag.objects.all().delete()
        response = self.client.post(reverse('journal:tag_suggest'), {'title': 'Tozeur', 'content': 'Un long voyage.'})
        self.assertEqual(response.status_code, 400)

    @patch('Journal.tasks.suggest_tags_task.delay', side_effect=ConnectionError('Redis'))
    def test_suggestion_redis_absent(self, mock_delay, mock_encode):
        response = self.client.post(reverse('journal:tag_suggest'), {'title': 'Tozeur', 'content': 'Un long voyage.'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('Réessayez', response.json()['error'])

    @patch('Journal.tasks.suggest_tags_task.delay', side_effect=AIServiceError('Le service ne répond pas.'))
    def test_suggestion_erreur_ia(self, mock_delay, mock_encode):
        response = self.client.post(reverse('journal:tag_suggest'), {'title': 'Tozeur', 'content': 'Un long voyage.'})
        self.assertEqual(response.json()['error'], 'Le service ne répond pas.')

    def test_formulaire_contient_le_bouton_ia(self, mock_encode):
        response = self.client.get(reverse('journal:entry_create'))
        self.assertContains(response, 'Suggérer des étiquettes')
        self.assertContains(response, 'tag-suggest.js')

    def test_vues_ia_demandent_la_connexion(self, mock_encode):
        self.client.logout()
        for url in (reverse('journal:semantic_search'), reverse('journal:tag_suggest'),
                    reverse('journal:entry_analyze', args=[self.beach.pk])):
            self.assertEqual(self.client.post(url).status_code, 302)
