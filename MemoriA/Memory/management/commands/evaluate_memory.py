"""Évaluation des 3 IA du module Memory sur les jeux annotés de Memory/evaluation/.

    python manage.py evaluate_memory                    # tout (environ 10 minutes)
    python manage.py evaluate_memory --only retros      # une seule partie : recits, images ou retros
    python manage.py evaluate_memory --limit 3          # 3 éléments par partie (essai rapide)

Résultats : docs/metrics/memory.md (rapport commenté), memory_*.png (graphiques),
memory_results.json (valeurs brutes), memory_recits.csv (grille à compléter à la main).
"""
import csv
import json
import statistics
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from Account.models import ActivityLog
from Journal.models import JournalEntry
from Memory import metrics, services
from Voice.models import MoodRecord, VoiceNote

EVAL_USERNAME = 'evaluation_memory'
PARTS = ('recits', 'images', 'retros')


def timed(func, *args, **kwargs):
    """Renvoie (résultat, durée en ms)."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    return result, int((time.perf_counter() - start) * 1000)


def mean(values):
    return round(statistics.mean(values), 2) if values else 0


class Command(BaseCommand):
    help = "Évalue le récit (grille), l'illustration (CLIP score) et la rétrospective (Precision@5)."

    def add_arguments(self, parser):
        parser.add_argument('--only', nargs='+', choices=PARTS, default=list(PARTS))
        parser.add_argument('--limit', type=int, default=None, help="Nombre maximum d'éléments par partie")
        parser.add_argument('--pause', type=float, default=6.0,
                            help="Pause entre deux appels Gemini (quota gratuit : 10 requêtes/minute)")
        parser.add_argument('--output', default=str(Path(settings.BASE_DIR).parent / 'docs' / 'metrics'))

    def handle(self, *args, **options):
        self.data_dir = Path(settings.BASE_DIR) / 'Memory' / 'evaluation'
        self.out = Path(options['output'])
        self.out.mkdir(parents=True, exist_ok=True)
        self.limit, self.pause = options['limit'], options['pause']

        results_file = self.out / 'memory_results.json'
        results = json.loads(results_file.read_text(encoding='utf-8')) if results_file.exists() else {}

        # Utilisateur temporaire : ses entrées et ses ActivityLog sont supprimés à la fin
        User.objects.filter(username=EVAL_USERNAME).delete()
        self.user = User.objects.create_user(EVAL_USERNAME)
        parts = {'recits': self.evaluate_stories, 'images': self.evaluate_images,
                 'retros': self.evaluate_retrospectives}
        try:
            for name in PARTS:
                if name in options['only']:
                    results[name] = parts[name]()
                    # Sauvegarde après chaque partie : une erreur plus loin ne fait rien perdre
                    results['date'] = date.today().isoformat()
                    results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            self.user.delete()

        self.draw_charts(results)
        self.write_report(results)
        self.stdout.write(self.style.SUCCESS(f"Rapport écrit dans {self.out / 'memory.md'}"))

    def load(self, name, key):
        items = json.loads((self.data_dir / name).read_text(encoding='utf-8'))[key]
        return items[:self.limit] if self.limit else items

    # ---------- 1. Récits : grille qualitative ----------

    def evaluate_stories(self):
        rows = []
        for item in self.load('recits.json', 'entrees'):
            self.stdout.write(f"Récit {item['id']} : {item['titre']}…")
            entry = JournalEntry.objects.create(user=self.user, title=item['titre'], content=item['texte'],
                                                location=item.get('lieu', ''),
                                                entry_date=date.fromisoformat(item['date']))
            if item.get('transcription'):
                VoiceNote.objects.create(entry=entry, title='Note', audio='voice_notes/evaluation.webm',
                                         transcription=item['transcription'], status='Termine')
            if item.get('humeur'):
                MoodRecord.objects.create(user=self.user, entry=entry, emotion=item['humeur'])
            row = {'id': item['id'], 'titre': item['titre'], 'style': item['style']}
            try:
                (story, _), row['duree_ms'] = timed(services.generate_story_text, entry, item['style'], self.user)
                time.sleep(self.pause)
                recall, missing = metrics.fact_recall(story, item['faits'])
                inventions = metrics.inventions_found(story, item.get('inventions', []))
                words = metrics.word_count(story)
                (style_note, comment), _ = timed(metrics.judge_style, item['texte'], story, item['style'])
                time.sleep(self.pause)
                row.update({
                    'texte': story, 'mots': words, 'rappel_faits': round(recall, 2), 'faits_manquants': missing,
                    'inventions': inventions,
                    'fidelite': metrics.recall_to_score(recall, len(inventions)),
                    'longueur': metrics.length_score(words),
                    'style_note': style_note, 'commentaire_juge': comment,
                })
                row['note'] = round((row['fidelite'] + row['longueur'] + row['style_note']) / 3, 2)
            except services.AIServiceError as error:
                row['erreur'] = str(error)
                self.stderr.write(f"  échec : {error}")
            rows.append(row)

        ok = [r for r in rows if 'note' in r]
        self.export_story_grid(rows)
        return {
            'n': len(rows), 'reussis': len(ok), 'lignes': rows,
            'fidelite': mean([r['fidelite'] for r in ok]), 'longueur': mean([r['longueur'] for r in ok]),
            'style': mean([r['style_note'] for r in ok]), 'note': mean([r['note'] for r in ok]),
            'rappel_faits': mean([r['rappel_faits'] for r in ok]),
            'mots_moyen': round(mean([r['mots'] for r in ok])),
            'dans_la_fourchette': sum(metrics.LENGTH_MIN <= r['mots'] <= metrics.LENGTH_MAX for r in ok),
            'inventions_total': sum(len(r['inventions']) for r in ok),
            'temps': metrics.time_summary([r['duree_ms'] for r in ok]),
            'humain': self.read_human_notes(),
        }

    def export_story_grid(self, rows):
        """Grille CSV avec les récits générés : colonnes vides pour les notes humaines (1 à 5)."""
        with open(self.out / 'memory_recits.csv', 'w', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file, delimiter=';')
            writer.writerow(['id', 'titre', 'style', 'recit', 'fidelite_auto', 'longueur_auto', 'style_juge',
                             'fidelite_humain', 'style_humain', 'longueur_humain', 'commentaire_humain'])
            for r in rows:
                writer.writerow([r['id'], r['titre'], r['style'], r.get('texte', r.get('erreur', '')),
                                 r.get('fidelite', ''), r.get('longueur', ''), r.get('style_note', ''), '', '', '', ''])

    def read_human_notes(self):
        """Notes humaines facultatives : copier memory_recits.csv complété dans Memory/evaluation/notes_humaines.csv."""
        path = self.data_dir / 'notes_humaines.csv'
        if not path.exists():
            return None
        notes = {'fidelite': [], 'style': [], 'longueur': []}
        with open(path, encoding='utf-8-sig') as file:
            for row in csv.DictReader(file, delimiter=';'):
                for key in notes:
                    value = (row.get(f'{key}_humain') or '').strip()
                    if value.isdigit():
                        notes[key].append(int(value))
        return {key: mean(values) for key, values in notes.items()} | {'n': len(notes['fidelite'])}

    # ---------- 2. Illustrations : CLIP score ----------

    def evaluate_images(self):
        scenes = self.load('illustrations.json', 'scenes')
        images_dir = self.out / 'memory_images'
        images_dir.mkdir(exist_ok=True)
        rows, generated = [], []
        for item in scenes:
            self.stdout.write(f"Illustration {item['id']} : {item['scene'][:50]}…")
            prompt = f"{item['scene']}, {services.IMAGE_STYLES[item['style']]}"
            row = {'id': item['id'], 'scene': item['scene'], 'style': item['style']}
            try:
                image, row['duree_ms'] = timed(services.create_image, self.user, prompt)
                last = ActivityLog.objects.filter(user=self.user, status='Succes').latest('id')
                row['moteur'] = last.ai_service
                thumb = image.copy()
                thumb.thumbnail((256, 256))
                thumb.convert('RGB').save(images_dir / f"image_{item['id']}.jpg", quality=85)
                generated.append((item, image))
            except services.AIServiceError as error:
                row['erreur'] = str(error)
                self.stderr.write(f"  échec : {error}")
            rows.append(row)

        # CLIP : bonne description vs description d'une autre image (référence « hasard »)
        by_id = {r['id']: r for r in rows}
        for index, (item, image) in enumerate(generated):
            other = generated[(index + 1) % len(generated)][0] if len(generated) > 1 else item
            by_id[item['id']]['clip'] = round(metrics.clip_score(image, item['scene']), 2)
            by_id[item['id']]['clip_mauvaise_description'] = round(metrics.clip_score(image, other['scene']), 2)

        ok = [r for r in rows if 'clip' in r]
        better = sum(r['clip'] > r['clip_mauvaise_description'] for r in ok)
        return {
            'n': len(rows), 'reussis': len(ok), 'lignes': rows,
            'clip_moyen': mean([r['clip'] for r in ok]),
            'clip_hasard': mean([r['clip_mauvaise_description'] for r in ok]),
            'meilleur_que_hasard': better,
            'moteurs': sorted({r['moteur'] for r in ok}),
            'temps': metrics.time_summary([r['duree_ms'] for r in ok]),
        }

    # ---------- 3. Rétrospectives : Precision@5 ----------

    def evaluate_retrospectives(self):
        rows = []
        for item in self.load('retrospectives.json', 'periodes'):
            self.stdout.write(f"Rétrospective {item['id']} : {item['titre']}…")
            text = '\n'.join(item['entrees'])
            row = {'id': item['id'], 'titre': item['titre']}
            try:
                keywords, row['duree_ms'] = timed(services.call_keybert, text, 5)
                precision, recall, hits = metrics.precision_recall_at_k(keywords, item['themes'], k=5)
                lenient, _, _ = metrics.precision_recall_at_k(keywords, item['themes'], k=5, strict=False)
                row.update({'mots_cles': keywords, 'corrects': hits, 'precision_5': round(precision, 2),
                            'rappel_5': round(recall, 2), 'precision_5_souple': round(lenient, 2)})
            except services.AIServiceError as error:
                row['erreur'] = str(error)
            rows.append(row)
        ok = [r for r in rows if 'precision_5' in r]
        times = [r['duree_ms'] for r in ok]
        return {
            'n': len(rows), 'reussis': len(ok), 'lignes': rows,
            'precision_5': mean([r['precision_5'] for r in ok]), 'rappel_5': mean([r['rappel_5'] for r in ok]),
            'precision_5_souple': mean([r['precision_5_souple'] for r in ok]),
            # Le premier appel inclut le chargement du modèle : on le montre à part
            'temps': metrics.time_summary(times[1:] or times),
            'premier_appel_ms': times[0] if times else 0,
        }

    # ---------- Graphiques ----------

    def draw_charts(self, results):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        colors = {'bleu': '#2563C9', 'corail': '#C2412F', 'vert': '#237A5C', 'gris': '#8A8A96'}
        recits = results.get('recits')
        if recits and recits['reussis']:
            fig, ax = plt.subplots(figsize=(6, 3.5))
            labels = ['Fidélité', 'Longueur', 'Style', 'Note globale']
            values = [recits['fidelite'], recits['longueur'], recits['style'], recits['note']]
            bars = ax.bar(labels, values, color=[colors['bleu'], colors['vert'], colors['corail'], '#2B2B33'])
            ax.bar_label(bars, fmt='%.2f')
            ax.set_ylim(0, 5.5)
            ax.set_ylabel('Note moyenne (sur 5)')
            ax.set_title(f"Grille des récits ({recits['reussis']} récits)")
            fig.tight_layout()
            fig.savefig(self.out / 'memory_recits.png', dpi=120)
            plt.close(fig)

        images = results.get('images')
        if images and images['reussis']:
            ok = [r for r in images['lignes'] if 'clip' in r]
            fig, ax = plt.subplots(figsize=(7, 3.5))
            x = range(len(ok))
            ax.bar([i - 0.2 for i in x], [r['clip'] for r in ok], 0.4, label='Bonne description', color=colors['bleu'])
            ax.bar([i + 0.2 for i in x], [r['clip_mauvaise_description'] for r in ok], 0.4,
                   label="Description d'une autre image", color=colors['gris'])
            ax.set_xticks(list(x), [str(r['id']) for r in ok])
            ax.set_xlabel('Image')
            ax.set_ylabel('CLIP score')
            ax.set_ylim(0, 48)                      # place pour la légende au-dessus des barres
            ax.set_title('CLIP score par illustration')
            ax.legend(loc='upper center', ncol=2)
            fig.tight_layout()
            fig.savefig(self.out / 'memory_clip.png', dpi=120)
            plt.close(fig)

        retros = results.get('retros')
        if retros and retros['reussis']:
            ok = [r for r in retros['lignes'] if 'precision_5' in r]
            fig, ax = plt.subplots(figsize=(7, 3.5))
            x = range(len(ok))
            ax.bar([i - 0.2 for i in x], [r['precision_5'] for r in ok], 0.4, label='Precision@5', color=colors['corail'])
            ax.bar([i + 0.2 for i in x], [r['rappel_5'] for r in ok], 0.4, label='Recall@5', color=colors['vert'])
            ax.set_xticks(list(x), [str(r['id']) for r in ok])
            ax.set_ylim(0, 1.3)
            ax.set_xlabel('Période')
            ax.set_title('Mots-clés KeyBERT par période')
            ax.legend(loc='upper center', ncol=2)
            fig.tight_layout()
            fig.savefig(self.out / 'memory_keybert.png', dpi=120)
            plt.close(fig)

    # ---------- Rapport ----------

    def write_report(self, results):
        lines = [
            '# Évaluation des IA du module Memory',
            '',
            f"Rapport généré par `python manage.py evaluate_memory` le {results['date']}.",
            'Jeux de données annotés : `MemoriA/Memory/evaluation/`. Valeurs brutes : `memory_results.json`.',
            '',
        ]
        lines += self.report_stories(results.get('recits'))
        lines += self.report_images(results.get('images'))
        lines += self.report_retros(results.get('retros'))
        lines += [
            '## Limites de l\'évaluation',
            '',
            '- Les jeux sont petits (15 récits, 10 images, 10 périodes) : les moyennes donnent une tendance, pas une mesure précise.',
            '- La fidélité est mesurée par la présence de mots attendus : un fait reformulé autrement peut être compté absent.',
            '- La note de style vient d\'un modèle (Gemini juge Gemini) : elle doit être confirmée par les notes humaines.',
            '- Le CLIP score mesure la correspondance texte-image, pas la beauté de l\'image.',
            '',
        ]
        (self.out / 'memory.md').write_text('\n'.join(lines), encoding='utf-8')

    def report_stories(self, r):
        if not r:
            return []
        lines = [
            '## 1. Récit de la journée (Gemini) : grille qualitative',
            '',
            f"**Méthode** : Gemini écrit le récit de {r['n']} entrées annotées (texte, note vocale, humeur), "
            'dans le style demandé. Chaque récit reçoit 3 notes sur 5 :',
            '',
            '- **Fidélité** (automatique) : 1 + 4 x part des faits clés retrouvés, moins 1 point par élément inventé '
            '(liste d\'éléments absents de l\'entrée).',
            f"- **Longueur** (automatique) : 5 si le récit fait entre {metrics.LENGTH_MIN} et {metrics.LENGTH_MAX} mots "
            '(consigne du prompt), puis -1 par tranche de 15 % d\'écart.',
            f"- **Style** : note donnée par un autre modèle, `{settings.GEMINI_JUDGE_MODEL}`, qui reçoit la même définition "
            'du style que le rédacteur (méthode « LLM-as-a-judge » ; un modèle différent évite qu\'il juge son propre texte).',
            '',
        ]
        failures = [row for row in r['lignes'] if 'erreur' in row]
        if failures:
            lines += [f"> **Résultats partiels** : {len(failures)} récit(s) sur {r['n']} n'ont pas pu être générés "
                      f"(« {failures[0]['erreur']} »). Relancez `python manage.py evaluate_memory --only recits` "
                      'quand le quota est renouvelé.', '']
        if not r['reussis']:
            return lines + ['Aucun récit généré : vérifiez `GEMINI_API_KEY` et le quota.', '']
        lines += [
            '![Grille des récits](memory_recits.png)',
            '',
            '| Critère | Note moyenne |',
            '|---|---|',
            f"| Fidélité | {r['fidelite']} / 5 (faits retrouvés : {round(100 * r['rappel_faits'])} %) |",
            f"| Longueur | {r['longueur']} / 5 ({r['dans_la_fourchette']} récits sur {r['reussis']} dans la fourchette, "
            f"{r['mots_moyen']} mots en moyenne) |",
            f"| Style | {r['style']} / 5 |",
            f"| **Note globale** | **{r['note']} / 5** |",
            '',
            f"Temps de génération : **{r['temps']['moyenne_ms'] / 1000:.1f} s** en moyenne, "
            f"{r['temps']['max_ms'] / 1000:.1f} s au maximum.",
            '',
            '| # | Entrée | Style | Mots | Faits | Inventions | Fidélité | Longueur | Style | Note |',
            '|---|---|---|---|---|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if 'note' in row:
                lines.append(f"| {row['id']} | {row['titre']} | {row['style']} | {row['mots']} | "
                             f"{round(100 * row['rappel_faits'])} % | {', '.join(row['inventions']) or '—'} | "
                             f"{row['fidelite']} | {row['longueur']} | {row['style_note']} | {row['note']} |")
            else:
                lines.append(f"| {row['id']} | {row['titre']} | {row['style']} | échec : {row['erreur']} ||||||| ")
        lines += ['', '**Commentaire**', '']
        lines.append(self.comment_level(r['fidelite'], "La fidélité est très bonne : les faits importants de la journée "
                                        "sont repris.", "La fidélité est correcte mais certains faits sont oubliés : "
                                        "le prompt pourrait demander de citer chaque personne et lieu.",
                                        "La fidélité est faible : le récit s'éloigne trop de l'entrée."))
        if r['inventions_total']:
            lines.append(f"- {r['inventions_total']} élément(s) inventé(s) détecté(s) : la consigne « n'invente rien » "
                         'n\'est pas toujours respectée.')
        else:
            lines.append('- Aucun des éléments interdits n\'apparaît : la consigne « n\'invente rien » est respectée.')
        lines.append(self.comment_level(r['longueur'], 'La longueur demandée est bien respectée.',
                                        'La longueur dépasse parfois la consigne : on peut limiter le nombre de '
                                        'mots dans la configuration de Gemini.',
                                        'La longueur est souvent hors consigne : il faut la contrôler après la génération.'))
        lines.append(self.comment_level(r['style'], 'Le style demandé est bien reconnaissable.',
                                        'Le style est présent mais parfois peu marqué.',
                                        'Le style demandé est mal respecté.'))
        if r.get('humain'):
            h = r['humain']
            lines += ['', f"**Notes humaines** ({h['n']} récits notés à la main) : fidélité {h['fidelite']}, "
                      f"style {h['style']}, longueur {h['longueur']} (sur 5)."]
        else:
            lines += ['', '**Notes humaines** : complétez les colonnes « humain » de `memory_recits.csv`, copiez le '
                      'fichier dans `MemoriA/Memory/evaluation/notes_humaines.csv` puis relancez '
                      '`python manage.py evaluate_memory --only recits` pour comparer avec les notes automatiques.']
        return lines + ['']

    def report_images(self, r):
        if not r:
            return []
        lines = [
            '## 2. Illustration (Stable Diffusion) : CLIP score',
            '',
            f"**Méthode** : {r['n']} scènes sont illustrées par le moteur configuré (`IMAGE_BACKEND`). Le modèle CLIP "
            '(`openai/clip-vit-base-patch32`) mesure la correspondance entre l\'image et sa description : '
            '**CLIP score = 100 x similarité cosinus**. Pour savoir si le score a un sens, on le compare au score de la '
            'même image avec la description d\'une **autre** image (référence « hasard »).',
            '',
        ]
        if not r['reussis']:
            return lines + ['Aucune image générée : vérifiez le crédit Hugging Face ou `IMAGE_BACKEND=local`.', '']
        lines += [
            '![CLIP score](memory_clip.png)',
            '',
            '| Mesure | Valeur |',
            '|---|---|',
            f"| CLIP score moyen (bonne description) | **{r['clip_moyen']}** |",
            f"| CLIP score moyen (description d'une autre image) | {r['clip_hasard']} |",
            f"| Images mieux notées avec leur propre description | {r['meilleur_que_hasard']} / {r['reussis']} |",
            f"| Moteur utilisé | {', '.join(r['moteurs'])} |",
            f"| Temps moyen / maximum | {r['temps']['moyenne_ms'] / 1000:.1f} s / {r['temps']['max_ms'] / 1000:.1f} s |",
            '',
            '| # | Scène | Style | CLIP | CLIP (autre description) | Image |',
            '|---|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if 'clip' in row:
                lines.append(f"| {row['id']} | {row['scene']} | {row['style']} | {row['clip']} | "
                             f"{row['clip_mauvaise_description']} | ![image {row['id']}](memory_images/image_{row['id']}.jpg) |")
            else:
                lines.append(f"| {row['id']} | {row['scene']} | {row['style']} | échec : {row.get('erreur', '')} | | |")
        gap = round(r['clip_moyen'] - r['clip_hasard'], 2)
        lines += ['', '**Commentaire**', '']
        if r['clip_moyen'] >= 28:
            lines.append(f"- Score moyen de {r['clip_moyen']} : les images correspondent bien à leur description "
                         '(au-dessus de 28, la correspondance est nette pour CLIP ViT-B/32).')
        elif r['clip_moyen'] >= 24:
            lines.append(f"- Score moyen de {r['clip_moyen']} : correspondance correcte, les éléments principaux sont présents.")
        else:
            lines.append(f"- Score moyen de {r['clip_moyen']} : correspondance faible, les images s'éloignent du texte.")
        lines.append(f"- L'écart avec la référence « hasard » est de {gap} points, et {r['meilleur_que_hasard']} images "
                     f"sur {r['reussis']} sont mieux notées avec leur propre description : le score mesure bien "
                     'le lien texte-image.' if gap > 0 else
                     f"- L'écart avec la référence « hasard » est de {gap} points : le score ne distingue pas les images.")
        if any('SD-Turbo' in m for m in r['moteurs']):
            lines.append('- Les images viennent du modèle local SD-Turbo (secours gratuit sur processeur, 512 x 512, '
                         '1 étape) : Stable Diffusion XL via Hugging Face donnerait des images plus détaillées.')
        return lines + ['']

    def report_retros(self, r):
        if not r:
            return []
        lines = [
            '## 3. Rétrospective (KeyBERT) : Precision@5 des thèmes',
            '',
            f"**Méthode** : pour {r['n']} périodes annotées à la main (4 entrées et 5 thèmes attendus chacune), "
            'KeyBERT (modèle multilingue `paraphrase-multilingual-MiniLM-L12-v2`, mots vides français) extrait 5 mots-clés.',
            '',
            '- **Precision@5 (stricte)** = part des 5 mots-clés corrects. Un mot-clé est correct si **tous** ses mots '
            'porteurs de sens appartiennent aux thèmes annotés (même racine, sans accents) : « robe mariée » est '
            'correct, « famille pleuré » ne l\'est pas.',
            '- **Precision@5 souple** (pour comparaison) : il suffit qu\'un des mots touche un thème.',
            '- **Recall@5** = part des thèmes attendus retrouvés parmi les mots-clés corrects.',
            '',
        ]
        if not r['reussis']:
            return lines + ['Aucune période analysée.', '']
        lines += [
            '![KeyBERT](memory_keybert.png)',
            '',
            '| Mesure | Valeur |',
            '|---|---|',
            f"| **Precision@5 moyenne (stricte)** | **{r['precision_5']}** |",
            f"| Precision@5 souple | {r.get('precision_5_souple', '—')} |",
            f"| Recall@5 moyen | {r['rappel_5']} |",
            f"| Temps moyen / maximum (modèle déjà chargé) | {r['temps']['moyenne_ms']} ms / {r['temps']['max_ms']} ms |",
            f"| Premier appel (chargement du modèle compris) | {r['premier_appel_ms'] / 1000:.1f} s |",
            '',
            '| # | Période | Mots-clés extraits | Corrects | P@5 | R@5 |',
            '|---|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if 'precision_5' in row:
                lines.append(f"| {row['id']} | {row['titre']} | {', '.join(row['mots_cles'])} | "
                             f"{', '.join(row['corrects']) or '—'} | {row['precision_5']} | {row['rappel_5']} |")
            else:
                lines.append(f"| {row['id']} | {row['titre']} | échec : {row['erreur']} | | | |")
        lines += ['', '**Commentaire**', '']
        if r['precision_5'] >= 0.7:
            lines.append(f"- Precision@5 de {r['precision_5']} : la grande majorité des thèmes proposés sont pertinents.")
        elif r['precision_5'] >= 0.5:
            lines.append(f"- Precision@5 de {r['precision_5']} : plus de la moitié des thèmes sont pertinents.")
        else:
            lines.append(f"- Precision@5 de {r['precision_5']} : les thèmes proposés sont souvent hors sujet.")
        if r.get('precision_5_souple', 0) > r['precision_5']:
            lines.append(f"- L'écart avec la mesure souple ({r['precision_5_souple']}) vient des expressions de deux mots "
                         'qui mélangent un thème et un mot sans rapport (ex. « famille pleuré ») : ce sont les erreurs '
                         'typiques de KeyBERT avec des bigrammes.')
        lines.append(f"- Recall@5 de {r['rappel_5']} : en moyenne, {round(100 * r['rappel_5'])} % des thèmes attendus "
                     'sont retrouvés avec seulement 5 mots-clés.')
        lines.append('- KeyBERT tourne en local (gratuit, sans quota) et répond en moins d\'une seconde une fois le '
                     'modèle chargé ; le modèle est préchargé au démarrage du worker Celery.')
        return lines + ['']

    @staticmethod
    def comment_level(score, good, medium, bad):
        text = good if score >= 4 else medium if score >= 3 else bad
        return f"- {text} (moyenne {score} / 5)"
