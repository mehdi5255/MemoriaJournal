"""Évaluation des 2 IA du module Reminder sur les jeux annotés de Reminder/evaluation/.

    python manage.py evaluate_reminder                    # tout
    python manage.py evaluate_reminder --only entites     # une seule partie : heure ou entites

Résultats dans docs/metrics/ : reminder.md (rapport commenté), reminder_heure.png (erreur par profil),
reminder_confusion.png (matrice de confusion des entités) et reminder_results.json (valeurs brutes).
"""
import json
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from Account.models import ActivityLog
from Account.services import AIServiceError, log_ai_call
from Reminder import metrics, services

EVAL_USERNAME = 'evaluation_reminder'
PARTS = ('heure', 'entites')
METHODS = [('kmeans', 'KMeans (application)'), ('moyenne_circulaire', 'Moyenne sur le cercle'),
           ('mediane', 'Médiane'), ('moyenne', 'Moyenne simple')]
TYPE_LABELS = {'PER': 'Personne', 'LOC': 'Lieu', 'ORG': 'Organisation', 'MISC': 'Divers', 'Aucune': 'Aucune'}


def timed(func, *args):
    start = time.perf_counter()
    result = func(*args)
    return result, int((time.perf_counter() - start) * 1000)


def fmt(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class Command(BaseCommand):
    help = "Évalue la meilleure heure (MAE en minutes) et les entités nommées (Precision/Recall/F1 par type)."

    def add_arguments(self, parser):
        parser.add_argument('--only', nargs='+', choices=PARTS, default=list(PARTS))
        parser.add_argument('--limit', type=int, default=None, help="Nombre maximum d'éléments par partie")
        parser.add_argument('--output', default=str(Path(settings.BASE_DIR).parent / 'docs' / 'metrics'))

    def handle(self, *args, **options):
        self.data_dir = Path(settings.BASE_DIR) / 'Reminder' / 'evaluation'
        self.out = Path(options['output'])
        self.out.mkdir(parents=True, exist_ok=True)
        self.limit = options['limit']

        results_file = self.out / 'reminder_results.json'
        results = json.loads(results_file.read_text(encoding='utf-8')) if results_file.exists() else {}

        # Utilisateur temporaire : ses ActivityLog sont supprimés à la fin
        User.objects.filter(username=EVAL_USERNAME).delete()
        self.user = User.objects.create_user(EVAL_USERNAME)
        parts = {'heure': self.evaluate_time, 'entites': self.evaluate_entities}
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
        self.stdout.write(self.style.SUCCESS(f"Rapport écrit dans {self.out / 'reminder.md'}"))

    def load(self, name, key):
        items = json.loads((self.data_dir / name).read_text(encoding='utf-8'))[key]
        return items[:self.limit] if self.limit else items

    # ---------- 1. Meilleure heure : MAE ----------

    def evaluate_time(self):
        rows, times = [], []
        for profile in self.load('heures.json', 'profils'):
            minutes = [metrics.to_minutes(h) for h in profile['heures_ecriture']]
            expected = metrics.to_minutes(profile['habitude'])
            (kmeans, share), ms = timed(log_ai_call, self.user, "Meilleure heure de rappel", 'KMeans',
                                        services.best_minutes, minutes)
            times.append(ms)
            predictions = {
                'kmeans': (5 * round(kmeans / 5)) % 1440,          # arrondi à 5 minutes comme dans l'application
                'moyenne_circulaire': metrics.circular_mean(minutes),
                'mediane': metrics.median_baseline(minutes),
                'moyenne': metrics.mean_baseline(minutes),
            }
            rows.append({'id': profile['id'], 'profil': profile['profil'], 'habitude': profile['habitude'],
                         'part_kmeans': round(share, 2),
                         'predictions': {k: fmt(v) for k, v in predictions.items()},
                         'erreurs': {k: metrics.minutes_error(v, expected) for k, v in predictions.items()}})
        return {
            'n': len(rows), 'lignes': rows,
            'mae': {key: metrics.mae([r['erreurs'][key] for r in rows]) for key, _ in METHODS},
            'max': {key: max(r['erreurs'][key] for r in rows) for key, _ in METHODS},
            'moins_15_min': sum(r['erreurs']['kmeans'] <= 15 for r in rows),
            'temps': metrics.time_summary(times),
        }

    # ---------- 2. Entités nommées : Precision / Recall / F1 ----------

    def evaluate_entities(self):
        from sklearn.metrics import classification_report, confusion_matrix

        items = self.load('ner.json', 'phrases')
        lines, times, errors = [], [], []
        for item in items:
            try:
                raw, ms = timed(services.raw_entities, item['texte'], self.user)
            except AIServiceError as error:
                errors.append({'id': item['id'], 'erreur': str(error)})
                continue
            times.append(ms)
            lines.append({'id': item['id'], 'texte': item['texte'],
                          'attendues': [list(e) for e in item['entites']],
                          'brutes': [[r['type'], r['text'], round(r['score'], 3)] for r in raw],
                          'predites': self.keep(raw, services.ENTITY_THRESHOLD)})
        if not lines:
            return {'n': len(items), 'reussis': 0, 'erreurs': errors, 'seuil': services.ENTITY_THRESHOLD}

        # Influence du seuil : F1 micro pour chaque seuil (le seuil de l'application est fixé, on le compare)
        by_threshold = []
        for threshold in metrics.THRESHOLDS:
            y_true, y_pred = [], []
            for line in lines:
                raw = [{'type': t, 'text': x, 'score': sc} for t, x, sc in line['brutes']]
                t, p = metrics.align([tuple(e) for e in line['attendues']], [tuple(e) for e in self.keep(raw, threshold)])
                y_true += t
                y_pred += p
            report = classification_report(y_true, y_pred, labels=metrics.ENTITY_TYPES, output_dict=True,
                                           zero_division=0)['micro avg']
            by_threshold.append({'seuil': threshold, 'precision': round(report['precision'], 3),
                                 'recall': round(report['recall'], 3), 'f1': round(report['f1-score'], 3)})

        scores = {}
        for mode, partial in (('strict', False), ('partiel', True)):
            y_true, y_pred = [], []
            for line in lines:
                t, p = metrics.align([tuple(e) for e in line['attendues']], [tuple(e) for e in line['predites']], partial)
                y_true += t
                y_pred += p
            report = classification_report(y_true, y_pred, labels=metrics.ENTITY_TYPES, output_dict=True,
                                           zero_division=0)
            scores[mode] = {
                'par_type': {label: {k: round(report[label][k], 2) for k in ('precision', 'recall', 'f1-score', 'support')}
                             for label in metrics.ENTITY_TYPES},
                'micro': {k: round(report['micro avg'][k], 3) for k in ('precision', 'recall', 'f1-score')},
                'macro': {k: round(report['macro avg'][k], 3) for k in ('precision', 'recall', 'f1-score')},
                'matrice': confusion_matrix(y_true, y_pred, labels=metrics.ENTITY_TYPES + [metrics.NONE]).tolist(),
            }
        for line in lines:
            t, p = metrics.align([tuple(e) for e in line['attendues']], [tuple(e) for e in line['predites']])
            line['correct'] = t == p
        return {
            'n': len(items), 'reussis': len(lines), 'erreurs': errors, 'lignes': lines,
            'entites_attendues': sum(len(line['attendues']) for line in lines),
            'seuil': services.ENTITY_THRESHOLD, 'par_seuil': by_threshold, **scores,
            'moteurs': sorted(set(ActivityLog.objects.filter(user=self.user, status='Succes', action='Entités nommées')
                                  .values_list('ai_service', flat=True))),
            'temps': metrics.time_summary(times[1:] or times),
            'premier_appel_ms': times[0] if times else 0,
        }

    @staticmethod
    def keep(raw, threshold):
        """Même nettoyage que l'application (services.clean_entities), avec un seuil donné."""
        return [[e['type'], e['text']] for e in services.clean_entities(raw, threshold)]

    # ---------- Graphiques ----------

    def draw_charts(self, results):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        hours = results.get('heure')
        if hours and hours.get('n'):
            rows = hours['lignes']
            fig, ax = plt.subplots(figsize=(8, 4))
            width = 0.2
            colors = ['#2563C9', '#237A5C', '#6E6E7A', '#C2412F']
            for i, (key, label) in enumerate(METHODS):
                ax.bar([r['id'] + (i - 1.5) * width for r in rows], [r['erreurs'][key] for r in rows], width,
                       label=f"{label} (MAE {hours['mae'][key]} min)", color=colors[i])
            ax.set_xticks([r['id'] for r in rows])
            ax.set_xlabel('Profil')
            ax.set_ylabel('Erreur (minutes)')
            ax.set_title("Écart entre l'heure proposée et l'habitude réelle")
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(self.out / 'reminder_heure.png', dpi=120)
            plt.close(fig)

        entities = results.get('entites')
        if entities and entities.get('reussis'):
            labels = [TYPE_LABELS[t] for t in metrics.ENTITY_TYPES + [metrics.NONE]]
            matrix = entities['strict']['matrice']
            fig, ax = plt.subplots(figsize=(6, 5))
            image = ax.imshow(matrix, cmap='Blues')
            ax.set_xticks(range(len(labels)), labels, rotation=30, ha='right')
            ax.set_yticks(range(len(labels)), labels)
            ax.set_xlabel('Type prédit')
            ax.set_ylabel('Type attendu')
            ax.set_title(f"Entités nommées ({entities['reussis']} phrases, correspondance stricte)")
            highest = max(max(row) for row in matrix) or 1
            for i, row in enumerate(matrix):
                for j, value in enumerate(row):
                    ax.text(j, i, value, ha='center', va='center',
                            color='white' if value > highest / 2 else '#2B2B33', fontsize=12)
            fig.colorbar(image, ax=ax, fraction=0.046)
            fig.tight_layout()
            fig.savefig(self.out / 'reminder_confusion.png', dpi=120)
            plt.close(fig)

            points = entities.get('par_seuil', [])
            if points:
                fig, ax = plt.subplots(figsize=(6.5, 3.5))
                for key, label, color in (('precision', 'Precision', '#237A5C'), ('recall', 'Recall', '#6E6E7A'),
                                          ('f1', 'F1', '#2563C9')):
                    ax.plot([p['seuil'] for p in points], [p[key] for p in points], marker='o', color=color, label=label)
                ax.axvline(entities['seuil'], color='#C2412F', linestyle='--', label='Seuil utilisé')
                ax.set_ylim(0, 1.05)
                ax.set_xlabel('Score minimal pour garder une entité')
                ax.set_ylabel('Score (micro)')
                ax.set_title('Influence du seuil sur les entités')
                ax.legend(fontsize=8)
                fig.tight_layout()
                fig.savefig(self.out / 'reminder_seuil.png', dpi=120)
                plt.close(fig)

    # ---------- Rapport ----------

    def write_report(self, results):
        lines = [
            '# Évaluation des IA du module Reminder',
            '',
            f"Rapport généré par `python manage.py evaluate_reminder` le {results.get('date', date.today().isoformat())}.",
            'Jeux de données annotés : `MemoriA/Reminder/evaluation/`. Valeurs brutes : `reminder_results.json`.',
            '',
        ]
        lines += self.report_time(results.get('heure'))
        lines += self.report_entities(results.get('entites'))
        lines += [
            "## Limites de l'évaluation",
            '',
            "- Les heures d'écriture sont **simulées** (graine fixe) : de vrais utilisateurs ont des habitudes "
            "qui changent selon les jours (semaine / week-end), ce que le modèle ne distingue pas encore.",
            '- 30 phrases annotées par une seule personne : une entité de plus ou de moins change le F1 '
            "d'un type de plusieurs points, surtout pour MISC (peu d'exemples).",
            '- La frontière entre types est parfois discutable (un festival est-il un lieu ou un événement ?) : '
            "c'est pourquoi la correspondance partielle est donnée à côté de la correspondance stricte.",
            '',
        ]
        (self.out / 'reminder.md').write_text('\n'.join(lines), encoding='utf-8')

    def report_time(self, r):
        if not r:
            return []
        lines = [
            "## 1. Meilleure heure de rappel (KMeans) : erreur moyenne en minutes (MAE)",
            '',
            f"**Méthode** : {r['n']} profils d'écriture simulés de 30 entrées (`heures.json`). Pour chacun, on connaît "
            "l'heure habituelle ; les heures réelles varient autour et une partie des entrées est écrite à n'importe "
            "quelle heure (bruit). L'application place chaque heure sur un cercle (23 h 55 et 0 h 05 sont voisines), "
            "regroupe les heures avec **KMeans** (1 à 3 groupes, choisis par le score de silhouette) et propose le "
            "centre du groupe le plus nombreux. On compare avec trois méthodes simples. **Plus la MAE est basse, mieux c'est.**",
            '',
            '![Erreur par profil](reminder_heure.png)',
            '',
            '| Méthode | MAE (minutes) | Erreur maximale |',
            '|---|---|---|',
        ]
        for key, label in METHODS:
            bold = '**' if key == 'kmeans' else ''
            lines.append(f"| {bold}{label}{bold} | {bold}{r['mae'][key]}{bold} | {r['max'][key]} min |")
        lines += [
            '',
            '| # | Profil | Habitude | KMeans | Erreur | Moyenne simple | Erreur |',
            '|---|---|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            lines.append(f"| {row['id']} | {row['profil']} | {row['habitude']} | {row['predictions']['kmeans']} | "
                         f"{row['erreurs']['kmeans']} min | {row['predictions']['moyenne']} | {row['erreurs']['moyenne']} min |")
        lines += [
            '',
            '**Commentaire**',
            '',
            f"- KMeans se trompe en moyenne de **{r['mae']['kmeans']} minutes** et reste à moins de 15 minutes de "
            f"l'habitude pour {r['moins_15_min']} profils sur {r['n']}. L'heure proposée est arrondie à 5 minutes, "
            "ce qui ajoute jusqu'à 2 minutes d'erreur.",
            f"- La **moyenne simple** ({r['mae']['moyenne']} min) échoue complètement autour de minuit : 23 h 50 et "
            "0 h 10 donnent une moyenne vers midi. Le passage sur le cercle corrige ce problème.",
            f"- La **moyenne sur le cercle** ({r['mae']['moyenne_circulaire']} min) est tirée par les entrées écrites au "
            "hasard et par une deuxième habitude (profil 8 : soir et matin). KMeans isole l'habitude principale.",
            f"- Calcul très rapide : {r['temps']['moyenne_ms']} ms en moyenne, {r['temps']['max_ms']} ms au maximum.",
            '',
        ]
        return lines

    def report_entities(self, r):
        if not r:
            return []
        lines = [
            '## 2. Question personnalisée : entités nommées (CamemBERT NER)',
            '',
            f"**Méthode** : {r['n']} phrases de journal annotées à la main ({r.get('entites_attendues', 0)} entités : "
            "personnes PER, lieux LOC, organisations ORG, divers MISC). Le modèle `Jean-Baptiste/camembert-ner` "
            f"analyse chaque phrase ; comme dans l'application, seules les entités de score ≥ {r['seuil']} sont gardées. "
            "Une entité est **juste** si le texte (sans accents ni majuscules) **et** le type correspondent. "
            "En correspondance **partielle**, « lac de Tunis » trouvé comme « Tunis » compte aussi.",
            '',
        ]
        if not r['reussis']:
            return lines + ['Aucune phrase analysée : vérifiez le moteur (`REMINDER_BACKEND`).', '']
        strict, partial = r['strict'], r['partiel']
        lines += [
            '![Matrice de confusion](reminder_confusion.png)',
            '',
            '| Type | Precision | Recall | F1 | Entités | F1 partiel |',
            '|---|---|---|---|---|---|',
        ]
        for label in metrics.ENTITY_TYPES:
            c = strict['par_type'][label]
            lines.append(f"| {label} ({TYPE_LABELS[label]}) | {c['precision']} | {c['recall']} | {c['f1-score']} | "
                         f"{int(c['support'])} | {partial['par_type'][label]['f1-score']} |")
        lines += [
            f"| **Micro (toutes les entités)** | **{strict['micro']['precision']}** | **{strict['micro']['recall']}** | "
            f"**{strict['micro']['f1-score']}** | {r.get('entites_attendues', '')} | {partial['micro']['f1-score']} |",
            f"| Macro (moyenne des 4 types) | {strict['macro']['precision']} | {strict['macro']['recall']} | "
            f"{strict['macro']['f1-score']} | | {partial['macro']['f1-score']} |",
            '',
            '| Mesure | Valeur |',
            '|---|---|',
            f"| Phrases entièrement justes | {sum(line['correct'] for line in r['lignes'])} / {r['reussis']} |",
            f"| Moteur utilisé | {', '.join(r['moteurs'])} |",
            f"| Temps moyen / maximum (modèle chargé) | {r['temps']['moyenne_ms']} ms / {r['temps']['max_ms']} ms |",
            f"| Premier appel (chargement du modèle compris) | {r['premier_appel_ms'] / 1000:.1f} s |",
            '',
            '### Phrases avec au moins une erreur',
            '',
            '| # | Phrase | Attendu | Trouvé |',
            '|---|---|---|---|',
        ]
        for line in r['lignes']:
            if not line['correct']:
                expected = ', '.join(f"{t} {x}" for t, x in line['attendues']) or '—'
                found = ', '.join(f"{t} {x}" for t, x in line['predites']) or '—'
                lines.append(f"| {line['id']} | {line['texte']} | {expected} | {found} |")
        if r.get('par_seuil'):
            best_t = max(r['par_seuil'], key=lambda p: (p['f1'], p['seuil']))
            current = next(p for p in r['par_seuil'] if abs(p['seuil'] - r['seuil']) < 1e-9)
            lines += [
                '',
                '### Influence du seuil',
                '',
                '![Influence du seuil](reminder_seuil.png)',
                '',
                '| Seuil | ' + ' | '.join(str(p['seuil']) for p in r['par_seuil']) + ' |',
                '|---|' + '---|' * len(r['par_seuil']),
                '| Precision | ' + ' | '.join(str(p['precision']) for p in r['par_seuil']) + ' |',
                '| Recall | ' + ' | '.join(str(p['recall']) for p in r['par_seuil']) + ' |',
                '| F1 | ' + ' | '.join(str(p['f1']) for p in r['par_seuil']) + ' |',
                '',
                f"Le meilleur F1 sur ces phrases est obtenu avec un seuil de **{best_t['seuil']}** (F1 {best_t['f1']}) ; "
                f"le seuil de l'application ({r['seuil']}) donne F1 {current['f1']} et precision {current['precision']}. "
                "Le seuil **n'a pas été ajusté sur ce jeu de test** (le résultat serait alors trop optimiste) : il a été "
                "fixé avant l'évaluation pour privilégier la **precision**, car une question qui cite un mot qui n'est "
                "pas un nom (un prénom rangé dans « divers », par exemple) gêne plus qu'une question générale. En dessous de 0,6, "
                "le modèle ajoute des entités fausses ; au-dessus de 0,8, il oublie des prénoms.",
            ]
        per = strict['par_type']
        best = max(metrics.ENTITY_TYPES, key=lambda t: per[t]['f1-score'])
        worst = min(metrics.ENTITY_TYPES, key=lambda t: per[t]['f1-score'])
        lines += [
            '',
            '**Commentaire**',
            '',
            f"- F1 global (micro) de **{strict['micro']['f1-score']}** en correspondance stricte et "
            f"{partial['micro']['f1-score']} en correspondance partielle.",
            f"- Le type le mieux reconnu est **{best}** (F1 {per[best]['f1-score']}) ; le plus difficile est "
            f"**{worst}** (F1 {per[worst]['f1-score']}). Pour la question du jour, ce sont surtout les **personnes** "
            "et les **lieux** qui comptent (ils passent en premier dans `build_question`).",
            '- Les erreurs viennent surtout des frontières (« lac de Tunis » réduit à « Tunis ») et des types '
            'ambigus (un club, un festival, un animal) : la question posée reste naturelle même dans ces cas.',
            "- Si l'IA échoue ou ne trouve rien, l'application pose une question générale : l'utilisateur a "
            'toujours une question du jour.',
            '',
        ]
        return lines
