"""Évaluation des 2 IA du module Journal sur les jeux annotés de Journal/evaluation/.

    python manage.py evaluate_journal                       # tout
    python manage.py evaluate_journal --only etiquettes     # une seule partie : recherche ou etiquettes

Résultats dans docs/metrics/ : journal.md (rapport commenté), journal_recherche.png, journal_confusion.png,
journal_seuil.png et journal_results.json (valeurs brutes).
"""
import json
import statistics
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from Account.models import ActivityLog
from Account.services import AIServiceError
from Journal import metrics, services
from Journal.models import JournalEntry, Tag

EVAL_USERNAME = 'evaluation_journal'
PARTS = ('recherche', 'etiquettes')
NONE = 'Aucune'


def timed(func, *args):
    start = time.perf_counter()
    result = func(*args)
    return result, int((time.perf_counter() - start) * 1000)


class Command(BaseCommand):
    help = "Évalue la recherche par le sens (Precision@3, MRR) et la suggestion d'étiquettes (Precision/Recall/F1)."

    def add_arguments(self, parser):
        parser.add_argument('--only', nargs='+', choices=PARTS, default=list(PARTS))
        parser.add_argument('--limit', type=int, default=None, help="Nombre maximum de requêtes ou de textes")
        parser.add_argument('--output', default=str(Path(settings.BASE_DIR).parent / 'docs' / 'metrics'))

    def handle(self, *args, **options):
        self.data_dir = Path(settings.BASE_DIR) / 'Journal' / 'evaluation'
        self.out = Path(options['output'])
        self.out.mkdir(parents=True, exist_ok=True)
        self.limit = options['limit']

        results_file = self.out / 'journal_results.json'
        results = json.loads(results_file.read_text(encoding='utf-8')) if results_file.exists() else {}

        # Utilisateur temporaire : ses entrées, étiquettes et ActivityLog sont supprimés à la fin
        User.objects.filter(username=EVAL_USERNAME).delete()
        self.user = User.objects.create_user(EVAL_USERNAME)
        parts = {'recherche': self.evaluate_search, 'etiquettes': self.evaluate_tags}
        try:
            for name in PARTS:
                if name in options['only']:
                    results[name] = parts[name]()
                    results['date'] = date.today().isoformat()
                    results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            self.user.delete()

        self.draw_charts(results)
        self.write_report(results)
        self.stdout.write(self.style.SUCCESS(f"Rapport écrit dans {self.out / 'journal.md'}"))

    def load(self, name):
        return json.loads((self.data_dir / name).read_text(encoding='utf-8'))

    def services_used(self, action):
        return sorted(set(ActivityLog.objects.filter(user=self.user, status='Succes', action=action)
                          .values_list('ai_service', flat=True)))

    # ---------- 1. Recherche par le sens : Precision@3 et MRR ----------

    def evaluate_search(self):
        data = self.load('recherche.json')
        queries = data['requetes'][:self.limit] if self.limit else data['requetes']
        entries = {}
        for item in data['entrees']:
            entries[item['id']] = JournalEntry.objects.create(user=self.user, title=item['titre'], content=item['texte'])
        ids = {entry.pk: number for number, entry in entries.items()}
        documents = {item['id']: f"{item['titre']}. {item['texte']}" for item in data['entrees']}
        try:
            _, embedding_ms = timed(services.fill_missing_embeddings, self.user)
        except AIServiceError as error:
            return {'n': len(queries), 'reussis': 0, 'erreur': str(error)}

        rows, times = [], []
        for query in queries:
            relevant = set(query['pertinentes'])
            try:
                found, ms = timed(services.semantic_search, self.user, query['texte'], 10)
            except AIServiceError as error:
                rows.append({'id': query['id'], 'requete': query['texte'], 'erreur': str(error)})
                continue
            times.append(ms)
            ranked = [ids[entry.pk] for entry, _ in found]
            keywords = metrics.keyword_ranking(query['texte'], documents)
            rows.append({
                'id': query['id'], 'requete': query['texte'], 'pertinentes': sorted(relevant),
                'top3': ranked[:3], 'scores_top3': [round(score, 3) for _, score in found[:3]],
                'p3': round(metrics.precision_at_k(ranked, relevant), 3),
                'p3_max': round(metrics.best_precision_at_k(relevant), 3),
                'rr': round(metrics.reciprocal_rank(ranked, relevant), 3),
                'hit3': metrics.hit_at_k(ranked, relevant),
                'mots_top3': keywords[:3],
                'mots_p3': round(metrics.precision_at_k(keywords, relevant), 3),
                'mots_rr': round(metrics.reciprocal_rank(keywords, relevant), 3),
            })
        ok = [r for r in rows if 'p3' in r]
        if not ok:
            return {'n': len(queries), 'reussis': 0, 'lignes': rows}

        def mean(key):
            return round(statistics.mean(r[key] for r in ok), 3)

        return {
            'n': len(queries), 'reussis': len(ok), 'entrees': len(entries), 'lignes': rows,
            'precision_at_3': mean('p3'), 'precision_at_3_max': mean('p3_max'),
            'precision_at_3_relative': round(mean('p3') / mean('p3_max'), 3),
            'mrr': mean('rr'), 'hit_at_3': mean('hit3'),
            'mots_precision_at_3': mean('mots_p3'), 'mots_mrr': mean('mots_rr'),
            'moteurs': self.services_used('Recherche par le sens'),
            'embeddings_ms': embedding_ms, 'temps': metrics.time_summary(times),
        }

    # ---------- 2. Étiquettes : Precision / Recall / F1 + matrice de confusion ----------

    def evaluate_tags(self):
        from sklearn.metrics import classification_report, confusion_matrix
        from sklearn.preprocessing import MultiLabelBinarizer

        data = self.load('etiquettes.json')
        labels = data['etiquettes']
        texts = data['textes'][:self.limit] if self.limit else data['textes']
        for name in labels:
            Tag.objects.create(user=self.user, name=name)

        rows, times, errors = [], [], []
        for item in texts:
            self.stdout.write(f"Texte {item['id']}…")
            try:
                # Mode de l'application (scores partagés) puis l'autre mode, pour comparer
                scores, ms = timed(services.tag_scores, item['texte'], labels, self.user, services.MULTI_LABEL)
                other = services.tag_scores(item['texte'], labels, self.user, not services.MULTI_LABEL)
            except AIServiceError as error:
                errors.append({'id': item['id'], 'erreur': str(error)})
                continue
            times.append(ms)
            rows.append({'id': item['id'], 'texte': item['texte'], 'principale': item['principale'],
                         'attendues': sorted(item['etiquettes']),
                         'scores': {k: round(v, 3) for k, v in scores.items()},
                         'scores_autre_mode': {k: round(v, 3) for k, v in other.items()}})
        if not rows:
            return {'n': len(texts), 'reussis': 0, 'erreurs': errors, 'seuil_utilise': services.TAG_THRESHOLD}

        threshold = services.TAG_THRESHOLD
        y_true_sets = [set(r['attendues']) for r in rows]
        best, by_threshold = metrics.best_threshold([r['scores'] for r in rows], y_true_sets)
        # Comparaison : chaque étiquette jugée seule (multi_label=True), première version avec le seuil 0.5
        other_best, other_by_threshold = metrics.best_threshold([r['scores_autre_mode'] for r in rows], y_true_sets)
        other_at = {round(t, 2): (p, rc, f) for t, p, rc, f in other_by_threshold}
        for row in rows:
            row['predites'] = metrics.predict_tags(row['scores'], threshold)
            row['top1'] = max(row['scores'], key=row['scores'].get)

        binarizer = MultiLabelBinarizer(classes=labels)
        y_true = binarizer.fit_transform([r['attendues'] for r in rows])
        y_pred = binarizer.transform([r['predites'] for r in rows])
        report = classification_report(y_true, y_pred, target_names=labels, output_dict=True, zero_division=0)

        # Matrice de confusion : étiquette principale attendue / étiquette la plus probable selon l'IA
        principal = [r['principale'] for r in rows]
        top1 = [r['top1'] for r in rows]
        return {
            'n': len(texts), 'reussis': len(rows), 'erreurs': errors, 'lignes': rows, 'etiquettes': labels,
            'seuil_utilise': threshold, 'seuil_optimal': best, 'multi_label': services.MULTI_LABEL,
            'autre_mode': {'multi_label': not services.MULTI_LABEL, 'seuil_optimal': other_best,
                           'a_0_5': [round(v, 3) for v in other_at[0.5]],
                           'au_meilleur_seuil': [round(v, 3) for v in other_at[other_best]]},
            'par_seuil': [{'seuil': t, 'precision': round(p, 3), 'recall': round(r, 3), 'f1': round(f, 3)}
                          for t, p, r, f in by_threshold],
            'par_etiquette': {label: {k: round(report[label][k], 2) for k in ('precision', 'recall', 'f1-score', 'support')}
                              for label in labels},
            'micro': {k: round(report['micro avg'][k], 3) for k in ('precision', 'recall', 'f1-score')},
            'macro': {k: round(report['macro avg'][k], 3) for k in ('precision', 'recall', 'f1-score')},
            'exactes': sum(set(r['predites']) == set(r['attendues']) for r in rows),
            'top1_juste': sum(r['top1'] in r['attendues'] for r in rows),
            'matrice': confusion_matrix(principal, top1, labels=labels).tolist(),
            'moteurs': self.services_used("Suggestion d'étiquettes"),
            'temps': metrics.time_summary(times[1:] or times),
            'premier_appel_ms': times[0] if times else 0,
        }

    # ---------- Graphiques ----------

    def draw_charts(self, results):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        search = results.get('recherche')
        if search and search.get('reussis'):
            names = ['Precision@3', 'Precision@3\n(sur le maximum)', 'MRR', 'Hit@3']
            semantic = [search['precision_at_3'], search['precision_at_3_relative'], search['mrr'], search['hit_at_3']]
            ok = [r for r in search['lignes'] if 'p3' in r]
            kw_hit = statistics.mean(1 if r['mots_p3'] > 0 else 0 for r in ok)
            kw_rel = statistics.mean(r['mots_p3'] / r['p3_max'] for r in ok)
            keyword = [search['mots_precision_at_3'], round(kw_rel, 3), search['mots_mrr'], round(kw_hit, 3)]
            fig, ax = plt.subplots(figsize=(7, 3.8))
            x = range(len(names))
            ax.bar([i - 0.2 for i in x], semantic, 0.4, label='Par le sens (MiniLM)', color='#2563C9')
            ax.bar([i + 0.2 for i in x], keyword, 0.4, label='Par mots exacts', color='#6E6E7A')
            for i, (a, b) in enumerate(zip(semantic, keyword)):
                ax.text(i - 0.2, a + 0.02, f"{a:.2f}", ha='center', fontsize=9)
                ax.text(i + 0.2, b + 0.02, f"{b:.2f}", ha='center', fontsize=9)
            ax.set_xticks(list(x), names)
            ax.set_ylim(0, 1.1)
            ax.set_title(f"Recherche sur {search['reussis']} requêtes annotées")
            ax.legend()
            fig.tight_layout()
            fig.savefig(self.out / 'journal_recherche.png', dpi=120)
            plt.close(fig)

        tags = results.get('etiquettes')
        if tags and tags.get('reussis'):
            labels = tags['etiquettes']
            fig, ax = plt.subplots(figsize=(6.5, 5.5))
            image = ax.imshow(tags['matrice'], cmap='Blues')
            ax.set_xticks(range(len(labels)), labels, rotation=30, ha='right')
            ax.set_yticks(range(len(labels)), labels)
            ax.set_xlabel("Étiquette la plus probable selon l'IA")
            ax.set_ylabel('Étiquette principale attendue')
            ax.set_title(f"Matrice de confusion ({tags['reussis']} textes)")
            highest = max(max(row) for row in tags['matrice']) or 1
            for i, row in enumerate(tags['matrice']):
                for j, value in enumerate(row):
                    ax.text(j, i, value, ha='center', va='center',
                            color='white' if value > highest / 2 else '#2B2B33', fontsize=11)
            fig.colorbar(image, ax=ax, fraction=0.046)
            fig.tight_layout()
            fig.savefig(self.out / 'journal_confusion.png', dpi=120)
            plt.close(fig)

            points = tags['par_seuil']
            fig, ax = plt.subplots(figsize=(6.5, 3.5))
            for key, label, color in (('precision', 'Precision', '#237A5C'), ('recall', 'Recall', '#6E6E7A'),
                                      ('f1', 'F1', '#2563C9')):
                ax.plot([p['seuil'] for p in points], [p[key] for p in points], marker='o', color=color, label=label)
            ax.axvline(tags['seuil_utilise'], color='#C2412F', linestyle='--', label='Seuil utilisé')
            ax.set_ylim(0, 1.05)
            ax.set_xlabel('Score minimal pour proposer une étiquette')
            ax.set_ylabel('Score (micro)')
            ax.set_title("Choix du seuil des étiquettes")
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(self.out / 'journal_seuil.png', dpi=120)
            plt.close(fig)

    # ---------- Rapport ----------

    def write_report(self, results):
        lines = [
            '# Évaluation des IA du module Journal',
            '',
            f"Rapport généré par `python manage.py evaluate_journal` le {results.get('date', date.today().isoformat())}.",
            'Jeux de données annotés : `MemoriA/Journal/evaluation/`. Valeurs brutes : `journal_results.json`.',
            '',
        ]
        lines += self.report_search(results.get('recherche'))
        lines += self.report_tags(results.get('etiquettes'))
        lines += [
            "## Limites de l'évaluation",
            '',
            '- 30 entrées et 20 requêtes, 40 textes : chaque erreur change les moyennes de plusieurs points.',
            '- Les annotations sont faites par une seule personne ; certaines entrées pourraient répondre à plus de '
            'requêtes (une soirée triste après une dispute est aussi « angoissée »).',
            '- Les textes sont courts et assez typiques ; un vrai journal mélange plusieurs sujets dans une même entrée.',
            '',
        ]
        (self.out / 'journal.md').write_text('\n'.join(lines), encoding='utf-8')

    def report_search(self, r):
        if not r:
            return []
        lines = [
            '## 1. Recherche par le sens (sentence-transformers) : Precision@3 et MRR',
            '',
            f"**Méthode** : {r.get('entrees', 30)} entrées de journal sont enregistrées pour un utilisateur de test et leurs "
            '**embeddings** sont calculés avec `paraphrase-multilingual-MiniLM-L12-v2` (modèle local, 384 dimensions). '
            f"{r['n']} requêtes sont écrites **avec d'autres mots** que les entrées (« une journée à la plage » pour une "
            "entrée qui parle de « sable » et de « nager »). Pour chaque requête, on a noté à la main les entrées "
            'pertinentes (1 à 3). La recherche classe les entrées par **similarité cosinus**.',
            '',
            '- **Precision@3** : part des 3 premiers résultats qui sont pertinents.',
            '- **MRR** (Mean Reciprocal Rank) : moyenne de 1 / rang de la première bonne entrée (1 = toujours en tête).',
            '- **Hit@3** : part des requêtes avec au moins une bonne entrée dans les 3 premiers résultats.',
            '',
        ]
        if not r['reussis']:
            return lines + [f"Aucune requête évaluée : {r.get('erreur', 'vérifiez le modèle')}.", '']
        lines += [
            '![Recherche par le sens](journal_recherche.png)',
            '',
            '| Mesure | Par le sens (IA) | Par mots exacts |',
            '|---|---|---|',
            f"| **Precision@3** | **{r['precision_at_3']}** | {r['mots_precision_at_3']} |",
            f"| Precision@3 maximale possible | {r['precision_at_3_max']} | {r['precision_at_3_max']} |",
            f"| **MRR** | **{r['mrr']}** | {r['mots_mrr']} |",
            f"| Hit@3 | {r['hit_at_3']} | |",
            f"| Moteur | {', '.join(r['moteurs'])} | recherche `icontains` |",
            f"| Temps moyen / maximum d'une recherche | {r['temps']['moyenne_ms']} ms / {r['temps']['max_ms']} ms | |",
            f"| Calcul des {r.get('entrees', 30)} embeddings (chargement compris) | {r['embeddings_ms'] / 1000:.1f} s | |",
            '',
            '| # | Requête | Pertinentes | 3 premiers (IA) | P@3 | 1/rang | 3 premiers (mots) |',
            '|---|---|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if 'p3' in row:
                lines.append(f"| {row['id']} | {row['requete']} | {row['pertinentes']} | {row['top3']} | {row['p3']} | "
                             f"{row['rr']} | {row['mots_top3'] or '—'} |")
            else:
                lines.append(f"| {row['id']} | {row['requete']} | | échec : {row['erreur']} | | | |")
        misses = [row for row in r['lignes'] if 'rr' in row and row['rr'] < 1]
        lines += [
            '',
            '**Commentaire**',
            '',
            f"- La bonne entrée arrive en tête pour {sum(1 for row in r['lignes'] if row.get('rr') == 1)} requêtes sur "
            f"{r['reussis']} (MRR {r['mrr']}) et dans les 3 premières pour {round(100 * r['hit_at_3'])} % des requêtes.",
            f"- La Precision@3 ({r['precision_at_3']}) est limitée par le jeu lui-même : beaucoup de requêtes n'ont qu'une "
            f"entrée pertinente, le maximum possible est donc {r['precision_at_3_max']}. Rapportée à ce maximum, elle vaut "
            f"**{r['precision_at_3_relative']}**.",
            f"- La recherche par mots exacts fait bien moins bien (MRR {r['mots_mrr']}) : elle ne trouve rien quand la "
            "requête n'utilise pas les mêmes mots que l'entrée. C'est tout l'intérêt des embeddings.",
        ]
        if misses:
            lines.append("- Requêtes les plus difficiles : " + ', '.join(f"« {row['requete']} »" for row in misses[:4]) +
                         " (sens proche de plusieurs entrées, ou idée exprimée très différemment).")
        lines += [
            '- Les embeddings sont calculés une seule fois (à l\'enregistrement de l\'entrée) et gardés dans '
            '`JournalEntry.embedding` : une recherche ne calcule que le vecteur de la requête, d\'où un temps très court.',
            '',
        ]
        return lines

    def report_tags(self, r):
        if not r:
            return []
        lines = [
            "## 2. Suggestion d'étiquettes (zero-shot mDeBERTa) : Precision, Recall, F1",
            '',
            f"**Méthode** : un utilisateur de test possède 8 étiquettes ({', '.join(r.get('etiquettes', []))}). "
            f"{r['n']} textes de journal sont étiquetés à la main (5 par étiquette, certains en ont deux). Le modèle "
            '`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` donne un score à chaque étiquette (« Ce texte parle de {}. ») ; '
            f"l'application propose les étiquettes dont le score dépasse **{r['seuil_utilise']}**. Un texte pouvant "
            "avoir plusieurs étiquettes, les mesures sont calculées étiquette par étiquette (`MultiLabelBinarizer` + "
            '`classification_report`).',
            '',
        ]
        other = r.get('autre_mode')
        if other:
            lines += [
                '### Deux façons de calculer les scores',
                '',
                '| Mode | Seuil | Precision | Recall | F1 micro |',
                '|---|---|---|---|---|',
                f"| Chaque étiquette jugée seule (`multi_label=True`, première version) | 0.5 | {other['a_0_5'][0]} | "
                f"{other['a_0_5'][1]} | {other['a_0_5'][2]} |",
                f"| Chaque étiquette jugée seule, meilleur seuil | {other['seuil_optimal']} | "
                f"{other['au_meilleur_seuil'][0]} | {other['au_meilleur_seuil'][1]} | {other['au_meilleur_seuil'][2]} |",
                f"| **Scores partagés (`multi_label=False`, application)** | **{r['seuil_utilise']}** | "
                f"**{r['micro']['precision']}** | **{r['micro']['recall']}** | **{r['micro']['f1-score']}** |",
                '',
                "La première version jugeait chaque étiquette **seule** : le modèle répond alors « oui » à presque tout "
                "(« Voyage », « Loisirs » et « Sport » à 1,00 pour le même texte) et l'application cochait trop "
                "d'étiquettes (precision faible). En mode **scores partagés**, les étiquettes sont mises en concurrence "
                "(la somme des scores vaut 1) : seules celles qui se détachent dépassent le seuil. **Attention** : ce "
                "changement et le seuil ont été choisis grâce à ce jeu de 40 textes, le résultat est donc un peu "
                "optimiste ; il faudrait le confirmer sur de nouveaux textes.",
                '',
            ]
        if not r['reussis']:
            return lines + ['Aucun texte analysé : vérifiez le moteur (`JOURNAL_BACKEND`).', '']
        lines += [
            '| Étiquette | Precision | Recall | F1 | Textes |',
            '|---|---|---|---|---|',
        ]
        for label in r['etiquettes']:
            c = r['par_etiquette'][label]
            lines.append(f"| {label} | {c['precision']} | {c['recall']} | {c['f1-score']} | {int(c['support'])} |")
        lines += [
            f"| **Micro (toutes les paires texte-étiquette)** | **{r['micro']['precision']}** | **{r['micro']['recall']}** | "
            f"**{r['micro']['f1-score']}** | |",
            f"| Macro (moyenne des 8 étiquettes) | {r['macro']['precision']} | {r['macro']['recall']} | "
            f"{r['macro']['f1-score']} | |",
            '',
            '| Mesure | Valeur |',
            '|---|---|',
            f"| Textes avec exactement les bonnes étiquettes | {r['exactes']} / {r['reussis']} |",
            f"| Étiquette la plus probable juste | {r['top1_juste']} / {r['reussis']} |",
            f"| Moteur utilisé | {', '.join(r['moteurs'])} |",
            f"| Temps moyen / maximum (modèle chargé) | {r['temps']['moyenne_ms']} ms / {r['temps']['max_ms']} ms |",
            f"| Premier appel (chargement du modèle compris) | {r['premier_appel_ms'] / 1000:.1f} s |",
            '',
            '### Matrice de confusion',
            '',
            '![Matrice de confusion](journal_confusion.png)',
            '',
            "Lignes : l'étiquette principale annotée ; colonnes : l'étiquette qui a reçu le plus haut score. "
            'La diagonale = l\'IA a mis la bonne étiquette en premier.',
            '',
            '### Choix du seuil',
            '',
            '![Choix du seuil](journal_seuil.png)',
            '',
            '| Seuil | ' + ' | '.join(str(p['seuil']) for p in r['par_seuil']) + ' |',
            '|---|' + '---|' * len(r['par_seuil']),
            '| Precision | ' + ' | '.join(str(p['precision']) for p in r['par_seuil']) + ' |',
            '| Recall | ' + ' | '.join(str(p['recall']) for p in r['par_seuil']) + ' |',
            '| F1 | ' + ' | '.join(str(p['f1']) for p in r['par_seuil']) + ' |',
            '',
            "Règle fixée avant l'évaluation : le seuil au **meilleur F1 micro** (le plus haut en cas d'égalité). "
            f"Seuil trouvé : **{r['seuil_optimal']}** ; seuil utilisé par l'application : **{r['seuil_utilise']}** "
            '(`TAG_THRESHOLD` dans `Journal/services.py`).',
            '',
            '### Erreurs',
            '',
            '| # | Texte | Attendues | Proposées |',
            '|---|---|---|---|',
        ]
        for row in r['lignes']:
            if set(row['predites']) != set(row['attendues']):
                lines.append(f"| {row['id']} | {row['texte']} | {', '.join(row['attendues'])} | "
                             f"{', '.join(row['predites']) or '—'} |")
        per = r['par_etiquette']
        worst = min(r['etiquettes'], key=lambda t: per[t]['f1-score'])
        best = max(r['etiquettes'], key=lambda t: per[t]['f1-score'])
        lines += [
            '',
            '**Commentaire**',
            '',
            f"- F1 micro de **{r['micro']['f1-score']}** (precision {r['micro']['precision']}, recall "
            f"{r['micro']['recall']}). L'étiquette la plus probable est juste pour {r['top1_juste']} textes sur "
            f"{r['reussis']} : le modèle comprend bien le sujet principal, sans aucun entraînement sur nos étiquettes.",
            f"- Étiquette la mieux reconnue : **{best}** (F1 {per[best]['f1-score']}) ; la plus difficile : **{worst}** "
            f"(F1 {per[worst]['f1-score']}).",
            '- Les erreurs viennent surtout des étiquettes larges (« Loisirs » touche aussi le sport, les sorties entre '
            'amis…) : le zero-shot donne un score élevé à plusieurs étiquettes proches.',
            "- Les étiquettes ne sont que **proposées** (cases pré-cochées) : l'utilisateur décoche une erreur en un clic.",
            '',
        ]
        return lines
