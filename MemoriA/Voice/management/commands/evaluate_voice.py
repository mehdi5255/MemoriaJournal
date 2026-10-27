"""Évaluation des 2 IA du module Voice sur les jeux annotés de Voice/evaluation/.

    python manage.py evaluate_voice                       # tout
    python manage.py evaluate_voice --only emotions       # une seule partie : transcription ou emotions
    python manage.py evaluate_voice --generer-audio       # (re)crée les 10 enregistrements avec les voix de Windows

Résultats dans docs/metrics/ : voice.md (rapport commenté), voice_confusion.png (matrice de confusion),
voice_seuil.png, voice_wer.png et voice_results.json (valeurs brutes).
"""
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from Account.models import ActivityLog
from Account.services import AIServiceError
from Voice import metrics, services

EVAL_USERNAME = 'evaluation_voice'
PARTS = ('transcription', 'emotions')
LABELS = dict([('Joie', 'Joie'), ('Tristesse', 'Tristesse'), ('Colere', 'Colère'), ('Peur', 'Peur'),
               ('Surprise', 'Surprise'), ('Neutre', 'Neutre')])

# Script PowerShell : synthèse vocale de Windows (texte et fichier passés par variables d'environnement)
TTS_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.SelectVoice($env:TTS_VOIX); "
    "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, "
    "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
    "$s.SetOutputToWaveFile($env:TTS_FICHIER, $f); $s.Speak($env:TTS_TEXTE); $s.Dispose()"
)


def on_windows():
    """Les voix de synthèse n'existent que sous Windows (fonction séparée pour pouvoir la simuler en test)."""
    return sys.platform == 'win32'


def timed(func, *args):
    start = time.perf_counter()
    result = func(*args)
    return result, int((time.perf_counter() - start) * 1000)


def last_service(user):
    log = ActivityLog.objects.filter(user=user, status='Succes').order_by('-id').first()
    return log.ai_service if log else ''


class Command(BaseCommand):
    help = "Évalue la transcription (WER) et la détection d'émotion (Precision/Recall/F1, matrice de confusion)."

    def add_arguments(self, parser):
        parser.add_argument('--only', nargs='+', choices=PARTS, default=list(PARTS))
        parser.add_argument('--limit', type=int, default=None, help="Nombre maximum d'éléments par partie")
        parser.add_argument('--generer-audio', action='store_true',
                            help="Crée les enregistrements de evaluation/audio/ avec les voix de Windows")
        parser.add_argument('--output', default=str(Path(settings.BASE_DIR).parent / 'docs' / 'metrics'))

    def handle(self, *args, **options):
        self.data_dir = Path(settings.BASE_DIR) / 'Voice' / 'evaluation'
        self.out = Path(options['output'])
        self.out.mkdir(parents=True, exist_ok=True)
        self.limit = options['limit']

        if options['generer_audio']:
            self.generate_audio()

        results_file = self.out / 'voice_results.json'
        results = json.loads(results_file.read_text(encoding='utf-8')) if results_file.exists() else {}

        # Utilisateur temporaire : ses ActivityLog sont supprimés à la fin
        User.objects.filter(username=EVAL_USERNAME).delete()
        self.user = User.objects.create_user(EVAL_USERNAME)
        parts = {'transcription': self.evaluate_transcription, 'emotions': self.evaluate_emotions}
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
        self.stdout.write(self.style.SUCCESS(f"Rapport écrit dans {self.out / 'voice.md'}"))

    def load(self, name, key):
        items = json.loads((self.data_dir / name).read_text(encoding='utf-8'))[key]
        return items[:self.limit] if self.limit else items

    # ---------- Enregistrements de test ----------

    def generate_audio(self):
        if not on_windows():
            raise CommandError("--generer-audio utilise les voix de Windows : lancez-le sous Windows.")
        audio_dir = self.data_dir / 'audio'
        audio_dir.mkdir(exist_ok=True)
        for item in self.load('transcriptions.json', 'enregistrements'):
            env = dict(os.environ, TTS_VOIX=item['voix'], TTS_TEXTE=item['reference'],
                       TTS_FICHIER=str(audio_dir / item['fichier']))
            subprocess.run(['powershell', '-NoProfile', '-Command', TTS_SCRIPT], env=env, check=True)
            self.stdout.write(f"Audio créé : {item['fichier']}")

    # ---------- 1. Transcription : WER ----------

    def evaluate_transcription(self):
        rows = []
        for item in self.load('transcriptions.json', 'enregistrements'):
            self.stdout.write(f"Transcription {item['id']} : {item['fichier']}…")
            row = {'id': item['id'], 'fichier': item['fichier'], 'langue': item['langue'],
                   'reference': item['reference']}
            path = self.data_dir / 'audio' / item['fichier']
            try:
                if not path.exists():
                    raise AIServiceError("Fichier absent : lancez la commande avec --generer-audio.")
                (text, language), row['duree_ms'] = timed(services.transcribe, path.read_bytes(), self.user)
                row.update({'transcription': text, 'langue_detectee': language, 'moteur': last_service(self.user),
                            'wer': round(metrics.word_error_rate(item['reference'], text), 3)})
                row.update(metrics.wer_details(item['reference'], text))
            except AIServiceError as error:
                row['erreur'] = str(error)
                self.stderr.write(f"  échec : {error}")
            rows.append(row)

        ok = [r for r in rows if 'wer' in r]
        times = [r['duree_ms'] for r in ok]
        total_words = sum(r['mots_reference'] for r in ok)
        total_errors = sum(r['substitutions'] + r['suppressions'] + r['insertions'] for r in ok)
        return {
            'n': len(rows), 'reussis': len(ok), 'lignes': rows,
            'wer_moyen': round(statistics.mean([r['wer'] for r in ok]), 3) if ok else 0,
            # WER global : toutes les erreurs / tous les mots (les longues phrases pèsent plus)
            'wer_global': round(total_errors / total_words, 3) if total_words else 0,
            'wer_fr': round(statistics.mean([r['wer'] for r in ok if r['langue'] == 'fr']), 3)
            if any(r['langue'] == 'fr' for r in ok) else None,
            'wer_en': round(statistics.mean([r['wer'] for r in ok if r['langue'] == 'en']), 3)
            if any(r['langue'] == 'en' for r in ok) else None,
            'langue_correcte': sum(r['langue_detectee'] == r['langue'] for r in ok),
            'moteurs': sorted({r['moteur'] for r in ok}),
            # Le premier appel inclut le chargement du modèle local : on le montre à part
            'temps': metrics.time_summary(times[1:] or times),
            'premier_appel_ms': times[0] if times else 0,
        }

    # ---------- 2. Émotions : Precision / Recall / F1 ----------

    def evaluate_emotions(self):
        from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

        items = self.load('emotions.json', 'phrases')
        y_true, all_scores, times, errors = [], [], [], []
        for item in items:
            try:
                scores, ms = timed(services.run_with_backend, self.user, "Détection d'émotion",
                                   ('Émotions', services.call_hf_emotion),
                                   ('Émotions (local)', services.call_local_emotion), item['texte'])
                y_true.append(item['emotion'])
                all_scores.append({'id': item['id'], 'texte': item['texte'], 'scores': scores})
                times.append(ms)
            except AIServiceError as error:
                errors.append({'id': item['id'], 'erreur': str(error)})
        if not all_scores:
            return {'n': len(items), 'reussis': 0, 'erreurs': errors}

        raw = [s['scores'] for s in all_scores]
        threshold, by_threshold = metrics.best_threshold(raw, y_true)
        current = services.NEUTRAL_THRESHOLD
        y_pred = [metrics.predict(s, current) for s in raw]
        report = classification_report(y_true, y_pred, labels=metrics.EMOTIONS, output_dict=True, zero_division=0)

        # Mesure « équitable » : seulement les 4 émotions que le modèle connaît
        known = [i for i, label in enumerate(y_true) if label not in ('Surprise', 'Neutre')]
        known_accuracy = accuracy_score([y_true[i] for i in known], [y_pred[i] for i in known]) if known else 0

        lines = [{'id': s['id'], 'texte': s['texte'], 'attendu': t, 'predit': p,
                  'confiance': round(max(x['score'] for x in s['scores']), 2)}
                 for s, t, p in zip(all_scores, y_true, y_pred)]
        return {
            'n': len(items), 'reussis': len(all_scores), 'erreurs': errors, 'lignes': lines,
            'seuil_utilise': current, 'seuil_optimal': threshold,
            'f1_par_seuil': [{'seuil': t, 'f1_macro': round(f, 3), 'accuracy_4_emotions': round(a, 3)}
                             for t, f, a in by_threshold],
            'accuracy': round(accuracy_score(y_true, y_pred), 3),
            'accuracy_4_emotions': round(known_accuracy, 3),
            'par_classe': {label: {k: round(report[label][k], 2) for k in ('precision', 'recall', 'f1-score', 'support')}
                           for label in metrics.EMOTIONS},
            'macro': {k: round(report['macro avg'][k], 3) for k in ('precision', 'recall', 'f1-score')},
            'matrice': confusion_matrix(y_true, y_pred, labels=metrics.EMOTIONS).tolist(),
            'moteurs': sorted(set(ActivityLog.objects.filter(user=self.user, status='Succes', action__contains='émotion')
                                  .values_list('ai_service', flat=True))),
            'temps': metrics.time_summary(times[1:] or times),
            'premier_appel_ms': times[0] if times else 0,
        }

    # ---------- Graphiques ----------

    def draw_charts(self, results):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        emotions = results.get('emotions')
        if emotions and emotions.get('reussis'):
            labels = [LABELS[e] for e in metrics.EMOTIONS]
            fig, ax = plt.subplots(figsize=(6.5, 5.5))
            image = ax.imshow(emotions['matrice'], cmap='Blues')
            ax.set_xticks(range(6), labels, rotation=30, ha='right')
            ax.set_yticks(range(6), labels)
            ax.set_xlabel('Émotion prédite')
            ax.set_ylabel('Émotion attendue')
            ax.set_title(f"Matrice de confusion ({emotions['reussis']} phrases, seuil {emotions['seuil_utilise']})")
            highest = max(max(row) for row in emotions['matrice']) or 1
            for i, row in enumerate(emotions['matrice']):
                for j, value in enumerate(row):
                    ax.text(j, i, value, ha='center', va='center',
                            color='white' if value > highest / 2 else '#2B2B33', fontsize=12)
            fig.colorbar(image, ax=ax, fraction=0.046)
            fig.tight_layout()
            fig.savefig(self.out / 'voice_confusion.png', dpi=120)
            plt.close(fig)

            fig, ax = plt.subplots(figsize=(6.5, 3.5))
            points = emotions['f1_par_seuil']
            ax.plot([p['seuil'] for p in points], [p['f1_macro'] for p in points], marker='o', color='#2563C9',
                    label='F1 macro (6 émotions)')
            ax.plot([p['seuil'] for p in points], [p.get('accuracy_4_emotions', 0) for p in points], marker='s',
                    color='#237A5C', label='Précision sur les 4 émotions connues')
            ax.axvline(emotions['seuil_utilise'], color='#C2412F', linestyle='--', label='Seuil utilisé')
            ax.set_ylim(0, 1.05)
            ax.set_xlabel('Seuil « Neutre » (confiance minimale)')
            ax.set_ylabel('Score')
            ax.set_title('Choix du seuil Neutre')
            ax.legend()
            fig.tight_layout()
            fig.savefig(self.out / 'voice_seuil.png', dpi=120)
            plt.close(fig)

        transcription = results.get('transcription')
        if transcription and transcription.get('reussis'):
            ok = [r for r in transcription['lignes'] if 'wer' in r]
            fig, ax = plt.subplots(figsize=(6.5, 3.5))
            ax.bar([str(r['id']) for r in ok], [100 * r['wer'] for r in ok],
                   color=['#2563C9' if r['langue'] == 'fr' else '#237A5C' for r in ok])
            ax.set_xlabel('Enregistrement (bleu : français, vert : anglais)')
            ax.set_ylabel('WER (%)')
            ax.set_title('Taux d\'erreur de mots par enregistrement')
            fig.tight_layout()
            fig.savefig(self.out / 'voice_wer.png', dpi=120)
            plt.close(fig)

    # ---------- Rapport ----------

    def write_report(self, results):
        lines = [
            '# Évaluation des IA du module Voice',
            '',
            f"Rapport généré par `python manage.py evaluate_voice` le {results.get('date', date.today().isoformat())}.",
            'Jeux de données annotés : `MemoriA/Voice/evaluation/`. Valeurs brutes : `voice_results.json`.',
            '',
        ]
        lines += self.report_transcription(results.get('transcription'))
        lines += self.report_emotions(results.get('emotions'))
        lines += [
            "## Limites de l'évaluation",
            '',
            '- Les enregistrements sont produits par les voix de synthèse de Windows : une voix nette, sans bruit '
            'ni accent. Le WER sur de vraies notes vocales (micro de téléphone, bruit, accent) sera plus élevé. '
            'Pour le mesurer, remplacez les fichiers de `evaluation/audio/` par de vrais enregistrements du même texte.',
            '- 60 phrases (10 par émotion) : les pourcentages par classe varient de 10 points pour une seule erreur.',
            '- Les phrases sont courtes et assez claires ; un vrai journal mélange souvent plusieurs émotions.',
            '',
        ]
        (self.out / 'voice.md').write_text('\n'.join(lines), encoding='utf-8')

    def report_transcription(self, r):
        if not r:
            return []
        lines = [
            '## 1. Transcription (Whisper) : taux d\'erreur de mots (WER)',
            '',
            f"**Méthode** : {r['n']} enregistrements dont on connaît le texte exact (7 en français, 3 en anglais) sont "
            'transcrits par le moteur configuré (`VOICE_BACKEND`). Le **WER** compte les mots remplacés, oubliés ou '
            'ajoutés, divisés par le nombre de mots du texte de référence (bibliothèque `jiwer`). Avant le calcul, '
            'les deux textes sont mis en minuscules et sans ponctuation. **Plus le WER est bas, mieux c\'est** '
            '(0 % = transcription parfaite).',
            '',
        ]
        if not r['reussis']:
            return lines + ['Aucune transcription réussie : vérifiez les fichiers audio et le moteur.', '']
        lines += [
            '![WER par enregistrement](voice_wer.png)',
            '',
            '| Mesure | Valeur |',
            '|---|---|',
            f"| **WER moyen** | **{100 * r['wer_moyen']:.1f} %** |",
            f"| WER global (toutes les erreurs / tous les mots) | {100 * r['wer_global']:.1f} % |",
            f"| WER en français / en anglais | {self.percent(r['wer_fr'])} / {self.percent(r['wer_en'])} |",
            f"| Langue détectée correctement | {r['langue_correcte']} / {r['reussis']} |",
            f"| Moteur utilisé | {', '.join(r['moteurs'])} |",
            f"| Temps moyen / maximum (modèle chargé) | {r['temps']['moyenne_ms'] / 1000:.1f} s / "
            f"{r['temps']['max_ms'] / 1000:.1f} s |",
            f"| Premier appel (chargement du modèle compris) | {r['premier_appel_ms'] / 1000:.1f} s |",
            '',
            '| # | Langue | Référence | Transcription | WER |',
            '|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if 'wer' in row:
                lines.append(f"| {row['id']} | {row['langue']} | {row['reference']} | {row['transcription']} | "
                             f"{100 * row['wer']:.0f} % |")
            else:
                lines.append(f"| {row['id']} | {row['langue']} | {row['reference']} | échec : {row['erreur']} | |")
        lines += ['', '**Commentaire**', '']
        wer = r['wer_moyen']
        if wer <= 0.10:
            lines.append(f"- WER moyen de {100 * wer:.1f} % : moins d'un mot faux sur dix, la transcription est "
                         'fiable pour un journal. Les erreurs sont visibles dans le tableau ci-dessus : surtout des '
                         "noms propres et des mots proches à l'oral (homophones).")
        elif wer <= 0.25:
            lines.append(f"- WER moyen de {100 * wer:.1f} % : le sens est conservé mais il faut parfois corriger "
                         'la transcription (elle est modifiable sur la page de la note).')
        else:
            lines.append(f"- WER moyen de {100 * wer:.1f} % : trop d'erreurs, un modèle Whisper plus grand serait nécessaire.")
        if any('local' in m for m in r['moteurs']):
            lines.append('- Le moteur utilisé est **Whisper base en local** (secours gratuit, 74 millions de paramètres). '
                         'Whisper large-v3 via Hugging Face (1,5 milliard de paramètres) ferait moins d\'erreurs.')
        lines.append(f"- La langue est reconnue dans {r['langue_correcte']} cas sur {r['reussis']}.")
        return lines + ['']

    def report_emotions(self, r):
        if not r:
            return []
        lines = [
            '## 2. Détection d\'émotion (xlm-emo-t) : Precision, Recall, F1',
            '',
            f"**Méthode** : {r['n']} phrases de journal étiquetées à la main, 10 par émotion de `MoodRecord`. "
            'Le modèle `MilaNLProc/xlm-emo-t` ne connaît que **4 émotions** (joie, tristesse, colère, peur). '
            f"Règle de l'application : si sa confiance est inférieure au **seuil Neutre ({r['seuil_utilise']})**, "
            "l'humeur est « Neutre ». Il ne peut jamais prédire « Surprise ».",
            '',
        ]
        if not r['reussis']:
            return lines + ['Aucune phrase analysée : vérifiez le moteur (`VOICE_BACKEND`).', '']
        lines += [
            '![Matrice de confusion](voice_confusion.png)',
            '',
            '| Émotion | Precision | Recall | F1 | Phrases |',
            '|---|---|---|---|---|',
        ]
        for emotion in metrics.EMOTIONS:
            c = r['par_classe'][emotion]
            lines.append(f"| {LABELS[emotion]} | {c['precision']} | {c['recall']} | {c['f1-score']} | {int(c['support'])} |")
        lines += [
            f"| **Moyenne macro** | **{r['macro']['precision']}** | **{r['macro']['recall']}** | "
            f"**{r['macro']['f1-score']}** | {r['reussis']} |",
            '',
            '| Mesure | Valeur |',
            '|---|---|',
            f"| Accuracy (6 émotions) | {100 * r['accuracy']:.0f} % |",
            f"| Accuracy sur les 4 émotions connues du modèle | {100 * r['accuracy_4_emotions']:.0f} % |",
            f"| Moteur utilisé | {', '.join(r['moteurs'])} |",
            f"| Temps moyen / maximum (modèle chargé) | {r['temps']['moyenne_ms']} ms / {r['temps']['max_ms']} ms |",
            f"| Premier appel (chargement du modèle compris) | {r['premier_appel_ms'] / 1000:.1f} s |",
            '',
            '### Choix du seuil « Neutre »',
            '',
            '![Choix du seuil](voice_seuil.png)',
            '',
            "Pour chaque seuil de 0,30 à 0,95, on calcule le F1 macro **et** la précision sur les 4 émotions connues. "
            "Un seuil très haut améliore un peu le F1 macro sur ces 60 phrases mais transforme de vraies émotions "
            "modérées en « Neutre ». Règle retenue, avec un objectif fixé à l'avance : **le meilleur F1 macro parmi les seuils "
            f"qui gardent au moins {round(100 * metrics.MIN_KNOWN_ACCURACY)} % de bonnes réponses sur les 4 émotions connues**. Seuil choisi : **{r['seuil_optimal']}** ; seuil utilisé par l'application : "
            f"**{r['seuil_utilise']}** (`NEUTRAL_THRESHOLD` dans `Voice/services.py`).",
            '',
            '| Seuil | ' + ' | '.join(str(p['seuil']) for p in r['f1_par_seuil']) + ' |',
            '|---|' + '---|' * len(r['f1_par_seuil']),
            '| F1 macro | ' + ' | '.join(str(p['f1_macro']) for p in r['f1_par_seuil']) + ' |',
            '| Précision 4 émotions | ' + ' | '.join(str(p.get('accuracy_4_emotions', '—'))
                                                     for p in r['f1_par_seuil']) + ' |',
            '',
            '### Erreurs du modèle',
            '',
            '| # | Phrase | Attendu | Prédit | Confiance |',
            '|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if row['attendu'] != row['predit']:
                lines.append(f"| {row['id']} | {row['texte']} | {LABELS[row['attendu']]} | {LABELS[row['predit']]} | "
                             f"{row['confiance']} |")
        lines += ['', '**Commentaire**', '']
        lines.append(f"- Sur les 4 émotions qu'il connaît, le modèle a raison dans {100 * r['accuracy_4_emotions']:.0f} % "
                     'des cas : il est fiable pour la joie, la tristesse, la colère et la peur.')
        lines.append(f"- Le F1 macro sur les 6 émotions ({r['macro']['f1-score']}) est tiré vers le bas par **Surprise** "
                     '(F1 = 0, le modèle n\'a pas cette classe) : une surprise est classée en joie, en peur ou en neutre.')
        weakest = min(('Joie', 'Tristesse', 'Colere', 'Peur'), key=lambda e: r['par_classe'][e]['precision'])
        lines.append(f"- La precision la plus basse est celle de **{LABELS[weakest]}** "
                     f"({r['par_classe'][weakest]['precision']}) : les phrases d'autres catégories (surprises, "
                     f"phrases banales) qui ne rentrent dans aucune des 4 classes finissent souvent en {LABELS[weakest].lower()}.")
        neutral = r['par_classe']['Neutre']
        lines.append(f"- **Neutre** : recall {neutral['recall']}, precision {neutral['precision']}. Le modèle reste "
                     'souvent très confiant même sur une phrase banale ; le seuil ne rattrape qu\'une partie de ces cas.')
        lines.append("- Piste d'amélioration : un modèle à 6 ou 7 émotions (avec surprise et neutre), ou laisser "
                     "l'utilisateur corriger l'émotion (déjà possible : l'humeur est modifiable).")
        return lines + ['']

    @staticmethod
    def percent(value):
        return '—' if value is None else f"{100 * value:.1f} %"
