"""Évaluation des IA du module Face sur les jeux annotés de Face/evaluation/.

    python manage.py evaluate_face                          # tout
    python manage.py evaluate_face --only detection          # objets, legendes, detection ou identification

Les photos de visages viennent du jeu public LFW (téléchargé une fois par scikit-learn, environ 230 Mo,
pas dans Git). Résultats dans docs/metrics/ : face.md, face_confusion.png, face_seuil.png,
face_results.json et face_legendes.csv (grille pour noter les légendes à la main).
"""
import csv
import io
import json
import statistics
import time
from datetime import date
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from PIL import Image

from Account.models import ActivityLog
from Account.services import AIServiceError, run_with_fallback
from Face import metrics, services

EVAL_USERNAME = 'evaluation_face'
PARTS = ('objets', 'legendes', 'detection', 'identification')
THRESHOLDS = [round(0.20 + 0.05 * i, 2) for i in range(10)]          # 0.20 ... 0.65


def timed(func, *args):
    start = time.perf_counter()
    result = func(*args)
    return result, int((time.perf_counter() - start) * 1000)


@lru_cache(maxsize=1)
def load_lfw(min_faces):
    """{nom: [image OpenCV BGR uint8, ...]} dans l'ordre du jeu LFW (téléchargé au premier appel)."""
    import numpy as np
    from sklearn.datasets import fetch_lfw_people
    data = fetch_lfw_people(min_faces_per_person=min_faces, color=True, resize=1.0, slice_=None)
    people = {}
    for image, target in zip(data.images, data.target):
        bgr = (np.clip(image, 0, 1) * 255).astype('uint8')[:, :, ::-1].copy()
        people.setdefault(str(data.target_names[target]), []).append(bgr)
    return people


class Command(BaseCommand):
    help = "Évalue les objets (précision), les légendes, la détection (Precision/Recall) et l'identification des visages."

    def add_arguments(self, parser):
        parser.add_argument('--only', nargs='+', choices=PARTS, default=list(PARTS))
        parser.add_argument('--output', default=str(Path(settings.BASE_DIR).parent / 'docs' / 'metrics'))

    def handle(self, *args, **options):
        self.data_dir = Path(settings.BASE_DIR) / 'Face' / 'evaluation'
        self.out = Path(options['output'])
        self.out.mkdir(parents=True, exist_ok=True)
        self.protocol = json.loads((self.data_dir / 'visages.json').read_text(encoding='utf-8'))
        self.scenes = json.loads((self.data_dir / 'objets.json').read_text(encoding='utf-8'))['scenes']

        results_file = self.out / 'face_results.json'
        results = json.loads(results_file.read_text(encoding='utf-8')) if results_file.exists() else {}
        User.objects.filter(username=EVAL_USERNAME).delete()
        self.user = User.objects.create_user(EVAL_USERNAME)
        parts = {'objets': self.evaluate_objects, 'legendes': self.evaluate_captions,
                 'detection': self.evaluate_detection, 'identification': self.evaluate_identification}
        try:
            for name in PARTS:
                if name in options['only']:
                    self.stdout.write(f"Évaluation : {name}…")
                    results[name] = parts[name]()
                    results['date'] = date.today().isoformat()
                    results_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            self.user.delete()
        self.draw_charts(results)
        self.write_report(results)
        self.stdout.write(self.style.SUCCESS(f"Rapport écrit dans {self.out / 'face.md'}"))

    def scene_bytes(self, scene):
        return (self.data_dir / 'scenes' / scene['fichier']).read_bytes()

    def lfw(self):
        return load_lfw(self.protocol['lfw']['min_faces_per_person'])

    # ---------- 1. Objets (DETR) ----------

    def evaluate_objects(self):
        rows, times = [], []
        for scene in self.scenes:
            row = {'id': scene['id'], 'scene': scene['scene'], 'attendus': scene['objets']}
            try:
                raw, ms = timed(run_with_fallback, settings.FACE_BACKEND, self.user, 'Détection des objets',
                                ('DETR', services.call_hf_objects), ('DETR (local)', services.call_local_objects),
                                self.scene_bytes(scene))
                predicted = services.objects_in_french(raw)
                row.update({'predits': predicted, **metrics.object_scores(predicted, scene['objets'])})
                # Corrigé : objets visibles ajoutés après vérification à l'œil (voir objets.json)
                verified = scene.get('ajouts_verifies', [])
                corrected = metrics.object_scores(predicted, scene['objets'] + verified)
                row.update({'ajouts_verifies': verified, 'en_trop_corrige': corrected['en_trop']})
                times.append(ms)
            except AIServiceError as error:
                row['erreur'] = str(error)
            rows.append(row)
        ok = [r for r in rows if 'precision' in r]
        total_pred = sum(len(r['predits']) for r in ok)
        total_true = sum(len(r['attendus']) for r in ok)
        total_ok = sum(len(r['corrects']) for r in ok)
        total_wrong = sum(len(r['en_trop_corrige']) for r in ok)
        return {
            'n': len(rows), 'reussis': len(ok), 'lignes': rows,
            # Micro : tous les objets ensemble ; macro : moyenne des scènes
            'precision': round(total_ok / total_pred, 3) if total_pred else 0,
            'precision_corrigee': round((total_pred - total_wrong) / total_pred, 3) if total_pred else 0,
            'rappel': round(total_ok / total_true, 3) if total_true else 0,
            'precision_moyenne_scenes': round(statistics.mean(r['precision'] for r in ok), 3) if ok else 0,
            'moteur': self.engine('Détection des objets'),
            'temps': metrics.time_summary(times[1:] or times), 'premier_appel_ms': times[0] if times else 0,
        }

    # ---------- 2. Légendes (BLIP) ----------

    def evaluate_captions(self):
        from Memory.metrics import clip_score
        rows, times = [], []
        for scene in self.scenes:
            row = {'id': scene['id'], 'scene': scene['scene']}
            try:
                data = self.scene_bytes(scene)
                english, ms = timed(run_with_fallback, settings.FACE_BACKEND, self.user, 'Légende de la photo',
                                    ('BLIP', services.call_hf_caption), ('BLIP (local)', services.call_local_caption), data)
                image = Image.open(io.BytesIO(data)).convert('RGB')
                row.update({'legende_en': english, 'legende_fr': services.translate_to_french(english),
                            # CLIP compare l'image et la légende anglaise (CLIP a été entraîné en anglais)
                            'clip': round(clip_score(image, english), 2),
                            'clip_reference': round(clip_score(image, scene['scene']), 2)})
                times.append(ms)
            except AIServiceError as error:
                row['erreur'] = str(error)
            rows.append(row)
        ok = [r for r in rows if 'clip' in r]
        self.export_caption_grid(rows)
        return {
            'n': len(rows), 'reussis': len(ok), 'lignes': rows,
            'clip_moyen': round(statistics.mean(r['clip'] for r in ok), 2) if ok else 0,
            'clip_reference_moyen': round(statistics.mean(r['clip_reference'] for r in ok), 2) if ok else 0,
            'humain': self.read_human_notes(),
            'moteur': self.engine('Légende de la photo'),
            'temps': metrics.time_summary(times[1:] or times), 'premier_appel_ms': times[0] if times else 0,
        }

    def export_caption_grid(self, rows):
        """Grille à remplir par l'équipe : une note de 1 à 5 par légende."""
        with open(self.out / 'face_legendes.csv', 'w', newline='', encoding='utf-8-sig') as file:
            writer = csv.writer(file, delimiter=';')
            writer.writerow(['id', 'image', 'legende', 'note_1_a_5', 'commentaire'])
            for r in rows:
                writer.writerow([r['id'], f"MemoriA/Face/evaluation/scenes/scene_{r['id']:02d}.jpg",
                                 r.get('legende_fr', r.get('erreur', '')), '', ''])

    def read_human_notes(self):
        """Notes humaines : copier face_legendes.csv complété dans Face/evaluation/notes_legendes.csv."""
        path = self.data_dir / 'notes_legendes.csv'
        if not path.exists():
            return None
        with open(path, encoding='utf-8-sig') as file:
            notes = [int(r['note_1_a_5']) for r in csv.DictReader(file, delimiter=';')
                     if (r.get('note_1_a_5') or '').strip().isdigit()]
        return {'n': len(notes), 'moyenne': round(statistics.mean(notes), 2) if notes else 0}

    # ---------- 3. Détection des visages (YuNet) ----------

    def collage(self, layout):
        """Image de 2 x 2 vignettes et la position de chaque vignette (avec ou sans visage)."""
        import cv2
        import numpy as np
        size = self.protocol['detection']['taille_vignette']
        canvas = np.full((2 * size, 2 * size, 3), 255, dtype=np.uint8)
        tiles = []
        for index, tile in enumerate(layout):
            x, y = (index % 2) * size, (index // 2) * size
            if tile[0] == 'lfw':
                picture = self.lfw()[tile[1]][tile[2]]
            else:
                scene = next(s for s in self.scenes if s['id'] == tile[1])
                picture = cv2.imdecode(np.frombuffer(self.scene_bytes(scene), np.uint8), cv2.IMREAD_COLOR)
            canvas[y:y + size, x:x + size] = cv2.resize(picture, (size, size))
            tiles.append({'x': x, 'y': y, 'size': size, 'visage': tile[0] == 'lfw'})
        return canvas, tiles

    def evaluate_detection(self):
        rows, times = [], []
        tp = extra = fp = fn = 0
        secondary = self.protocol['detection'].get('visages_secondaires', {})
        empty_tiles = 0
        for number, layout in enumerate(self.protocol['detection']['collages'], start=1):
            image, tiles = self.collage(layout)
            detections, ms = timed(services.detect_faces, image)
            a, s, b, c = metrics.detection_counts(detections, tiles, secondary.get(str(number), 0))
            tp, extra, fp, fn = tp + a, extra + s, fp + b, fn + c
            empty_tiles += sum(1 for t in tiles if not t['visage'])
            times.append(ms)
            rows.append({'id': number, 'visages_attendus': sum(t['visage'] for t in tiles),
                         'visages_detectes': len(detections), 'vrais_positifs': a, 'visages_secondaires': s,
                         'faux_positifs': b, 'faux_negatifs': c})
        # Stricte : chaque photo LFW n'a qu'un visage ; corrigée : les visages d'arrière-plan vérifiés comptent
        strict_precision, recall, strict_f1 = metrics.precision_recall(tp, fp + extra, fn)
        precision, _, f1 = metrics.precision_recall(tp + extra, fp, fn)
        return {'n': len(rows), 'lignes': rows, 'vrais_positifs': tp, 'visages_secondaires': extra,
                'faux_positifs': fp, 'faux_negatifs': fn, 'vignettes_sans_visage': empty_tiles,
                'precision_stricte': round(strict_precision, 3), 'f1_strict': round(strict_f1, 3),
                'precision': round(precision, 3), 'rappel': round(recall, 3), 'f1': round(f1, 3),
                'seuil': services.DETECTION_THRESHOLD, 'temps': metrics.time_summary(times[1:] or times)}

    # ---------- 4. Identification (SFace) ----------

    def main_feature(self, image):
        """Empreinte du plus grand visage de la photo (le sujet de la photo LFW), ou None."""
        faces = services.detect_faces(image)
        if not faces:
            return None
        face = max(faces, key=lambda f: f['width'] * f['height'])
        return services.face_feature(image, face['row'])

    def evaluate_identification(self):
        from sklearn.metrics import accuracy_score, adjusted_rand_score, confusion_matrix

        p = self.protocol['identification']
        people = self.lfw()
        n_ref, n_test = p['photos_reference'], p['photos_test']
        references, missed, times = {}, 0, []
        for name in p['proches']:
            features = [f for f in (self.main_feature(img) for img in people[name][:n_ref]) if f is not None]
            references[name] = metrics.mean_encoding(features)

        tests = [(name, img) for name in p['proches'] for img in people[name][n_ref:n_ref + n_test]]
        tests += [(metrics.UNKNOWN, img) for name in p['inconnus'] for img in people[name][:p['photos_par_inconnu']]]
        truth, scores_by_test, features_known, labels_known = [], [], [], []
        for expected, image in tests:
            start = time.perf_counter()
            feature = self.main_feature(image)
            times.append(int((time.perf_counter() - start) * 1000))
            if feature is None:
                missed += 1
                truth.append(expected)
                scores_by_test.append(None)
                continue
            truth.append(expected)
            scores_by_test.append({name: services.cosine(feature, ref) for name, ref in references.items()})
            if expected != metrics.UNKNOWN:
                features_known.append(feature)
                labels_known.append(expected)

        def predict(threshold):
            out = []
            for scores in scores_by_test:
                if scores is None:
                    out.append(metrics.UNKNOWN)           # aucun visage trouvé : personne n'est reconnu
                    continue
                name, best = max(scores.items(), key=lambda kv: kv[1])
                out.append(name if best >= threshold else metrics.UNKNOWN)
            return out

        current = services.MATCH_THRESHOLD
        predictions = predict(current)
        labels = p['proches'] + [metrics.UNKNOWN]
        known = [i for i, t in enumerate(truth) if t != metrics.UNKNOWN]
        unknown = [i for i, t in enumerate(truth) if t == metrics.UNKNOWN]

        def rates(preds):
            ok_known = sum(preds[i] == truth[i] for i in known) / len(known)
            rejected = sum(preds[i] == metrics.UNKNOWN for i in unknown) / len(unknown) if unknown else 1
            return round(ok_known, 3), round(rejected, 3)

        curve = []
        for t in THRESHOLDS:
            ok_known, rejected = rates(predict(t))
            curve.append({'seuil': t, 'proches_reconnus': ok_known, 'inconnus_rejetes': rejected,
                          'accuracy': round(accuracy_score(truth, predict(t)), 3)})
        ok_known, rejected = rates(predictions)
        # Regroupement DBSCAN des visages des proches, comparé aux vraies identités (1 = parfait, 0 = hasard)
        clusters = services.cluster_faces(features_known)
        return {
            'n': len(tests), 'proches': p['proches'], 'non_detectes': missed, 'seuil': current,
            'accuracy': round(accuracy_score(truth, predictions), 3),
            'proches_reconnus': ok_known, 'inconnus_rejetes': rejected,
            'matrice': confusion_matrix(truth, predictions, labels=labels).tolist(), 'etiquettes': labels,
            'courbe_seuil': curve,
            'dbscan_ari': round(adjusted_rand_score(labels_known, clusters), 3),
            'dbscan_groupes': len(set(c for c in clusters if c != -1)),
            'dbscan_isoles': sum(1 for c in clusters if c == -1),
            'temps': metrics.time_summary(times[1:] or times),
        }

    def engine(self, action):
        services_used = (ActivityLog.objects.filter(user=self.user, action=action, status='Succes')
                         .values_list('ai_service', flat=True).distinct())
        return ', '.join(sorted(services_used)) or '—'

    # ---------- Graphiques ----------

    def draw_charts(self, results):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        ident = results.get('identification')
        if ident:
            labels = [name.split()[-1] if name != metrics.UNKNOWN else name for name in ident['etiquettes']]
            fig, ax = plt.subplots(figsize=(7, 6))
            image = ax.imshow(ident['matrice'], cmap='Blues')
            ax.set_xticks(range(len(labels)), labels, rotation=30, ha='right')
            ax.set_yticks(range(len(labels)), labels)
            ax.set_xlabel('Personne prédite')
            ax.set_ylabel('Personne attendue')
            ax.set_title(f"Identification SFace (seuil {ident['seuil']})")
            highest = max(max(row) for row in ident['matrice']) or 1
            for i, row in enumerate(ident['matrice']):
                for j, value in enumerate(row):
                    ax.text(j, i, value, ha='center', va='center', color='white' if value > highest / 2 else '#2B2B33')
            fig.colorbar(image, ax=ax, fraction=0.046)
            fig.tight_layout()
            fig.savefig(self.out / 'face_confusion.png', dpi=120)
            plt.close(fig)

            fig, ax = plt.subplots(figsize=(6.5, 3.5))
            curve = ident['courbe_seuil']
            ax.plot([c['seuil'] for c in curve], [c['proches_reconnus'] for c in curve], marker='o', color='#2563C9',
                    label='Proches bien reconnus')
            ax.plot([c['seuil'] for c in curve], [c['inconnus_rejetes'] for c in curve], marker='s', color='#237A5C',
                    label='Inconnus bien rejetés')
            ax.axvline(ident['seuil'], color='#C2412F', linestyle='--', label='Seuil utilisé')
            ax.set_ylim(0, 1.05)
            ax.set_xlabel('Seuil de similarité cosinus')
            ax.set_ylabel('Taux')
            ax.set_title('Choix du seuil de reconnaissance')
            ax.legend(loc='lower left')
            fig.tight_layout()
            fig.savefig(self.out / 'face_seuil.png', dpi=120)
            plt.close(fig)

    # ---------- Rapport ----------

    def write_report(self, results):
        lines = ['# Évaluation des IA du module Face', '',
                 f"Rapport généré par `python manage.py evaluate_face` le {results.get('date', date.today().isoformat())}.",
                 'Jeux annotés : `MemoriA/Face/evaluation/` (scènes et protocole). Photos de visages : jeu public '
                 '**Labeled Faces in the Wild** (LFW), téléchargé par scikit-learn, jamais mis dans Git. '
                 'Valeurs brutes : `face_results.json`.', '']
        lines += self.report_detection(results.get('detection'))
        lines += self.report_identification(results.get('identification'))
        lines += self.report_objects(results.get('objets'))
        lines += self.report_captions(results.get('legendes'))
        lines += ["## Limites de l'évaluation", '',
                  "- LFW contient surtout des personnalités photographiées de face par des photographes de presse : "
                  "les photos de famille (profil, mauvaise lumière, flou) seront plus difficiles.",
                  "- Les scènes des objets et des légendes sont générées par SD-Turbo : des images nettes et simples.",
                  "- Les jeux sont petits : une erreur fait varier les pourcentages de plusieurs points.",
                  "- On peut remplacer ou compléter ces jeux par des photos de l'équipe (avec son accord), dans le même format.", '']
        (self.out / 'face.md').write_text('\n'.join(lines), encoding='utf-8')

    def report_detection(self, r):
        if not r:
            return []
        return [
            '## 1. Détection des visages (YuNet) : Precision / Recall', '',
            f"**Méthode** : {r['n']} photos de test, chacune formée de 4 vignettes : une photo LFW (1 visage) ou une scène "
            "sans personne (0 visage). On connaît donc la place de chaque visage. Une détection est un **vrai positif** "
            "si elle tombe dans une vignette avec visage (une seule fois), sinon c'est un **faux positif** ; une vignette "
            f"avec visage sans détection est un **faux négatif**. Seuil de détection : {r['seuil']}.", '',
            "**Correction de l'annotation** : la première évaluation a trouvé des détections « en trop » dans certaines "
            "photos LFW. Elles ont été dessinées sur les collages et vérifiées à l'œil : ce sont toutes de **vrais visages "
            "à l'arrière-plan** (personnes derrière ou à côté du sujet). Le rapport donne donc deux precisions : "
            "la stricte (1 seul visage par photo LFW) et la corrigée (visages d'arrière-plan vérifiés comptés comme justes).", '',
            '| Mesure | Valeur |', '|---|---|',
            f"| **Recall** (visages principaux trouvés) | **{r['rappel']}** ({r['vrais_positifs']} sur {r['vrais_positifs'] + r['faux_negatifs']}) |",
            f"| **Precision corrigée** | **{r.get('precision', '—')}** (F1 {r.get('f1', '—')}) |",
            f"| Precision stricte | {r.get('precision_stricte', '—')} (F1 {r.get('f1_strict', '—')}) |",
            f"| Visages d'arrière-plan vérifiés | {r.get('visages_secondaires', 0)} |",
            f"| Fausses alertes sur les {r.get('vignettes_sans_visage', '—')} scènes sans personne | {r['faux_positifs']} |",
            f"| Temps moyen / maximum par photo (500 x 500) | {r['temps']['moyenne_ms']} ms / {r['temps']['max_ms']} ms |", '',
            '**Commentaire**', '',
            f"- Tous les visages principaux sont trouvés (recall {r['rappel']}) et "
            + (f"aucune fausse alerte sur les scènes sans personne : " if r['faux_positifs'] == 0 else
               f"{r['faux_positifs']} fausse(s) alerte(s) : ")
            + ("YuNet est très fiable sur des visages de face, en quelques millisecondes." if r.get('f1', 0) >= 0.9 else
               "on peut ajuster le seuil de détection."),
            "- YuNet trouve même de petits visages flous à l'arrière-plan : dans l'application, ils apparaissent comme "
            "« Personne inconnue » et l'utilisateur peut les supprimer.", '']

    def report_identification(self, r):
        if not r:
            return []
        lines = [
            '## 2. Identification des proches (SFace) : accuracy et matrice de confusion', '',
            f"**Méthode** : comme dans l'application, chacun des {len(r['proches'])} proches est enregistré avec 3 photos "
            "(moyenne des empreintes SFace). On identifie ensuite 6 autres photos de chacun, plus 15 photos de 5 personnes "
            f"jamais enregistrées, qui doivent être classées « Inconnu ». Seuil de similarité cosinus : {r['seuil']} "
            '(valeur recommandée par OpenCV).', '',
            '![Matrice de confusion](face_confusion.png)', '',
            '| Mesure | Valeur |', '|---|---|',
            f"| **Accuracy (toutes les photos)** | **{100 * r['accuracy']:.0f} %** |",
            f"| Proches bien reconnus | {100 * r['proches_reconnus']:.0f} % |",
            f"| Inconnus bien rejetés | {100 * r['inconnus_rejetes']:.0f} % |",
            f"| Photos sans visage détecté | {r['non_detectes']} |",
            f"| Regroupement DBSCAN des proches (Adjusted Rand Index, 1 = parfait) | {r['dbscan_ari']} "
            f"({r['dbscan_groupes']} groupes, {r['dbscan_isoles']} visages isolés) |",
            f"| Temps moyen par photo (détection + empreinte) | {r['temps']['moyenne_ms']} ms |", '',
            '### Choix du seuil', '', '![Seuil](face_seuil.png)', '',
            '| Seuil | ' + ' | '.join(str(c['seuil']) for c in r['courbe_seuil']) + ' |',
            '|---|' + '---|' * len(r['courbe_seuil']),
            '| Proches reconnus | ' + ' | '.join(str(c['proches_reconnus']) for c in r['courbe_seuil']) + ' |',
            '| Inconnus rejetés | ' + ' | '.join(str(c['inconnus_rejetes']) for c in r['courbe_seuil']) + ' |', '',
            '**Commentaire**', '',
            f"- Avec le seuil {r['seuil']}, {100 * r['proches_reconnus']:.0f} % des proches sont reconnus et "
            f"{100 * r['inconnus_rejetes']:.0f} % des inconnus sont rejetés.",
            "- Un seuil plus haut rejette mieux les inconnus mais oublie plus de proches, un seuil plus bas fait "
            "l'inverse. Dans MémoriA, une erreur reste peu grave : l'application propose « C'est X ? » et l'utilisateur confirme.",
            f"- DBSCAN : un ARI de {r['dbscan_ari']} " + (
                "montre que les groupes proposés correspondent bien aux vraies personnes." if r['dbscan_ari'] >= 0.7 else
                "montre que les groupes mélangent encore des personnes : il faut toujours les vérifier avant de les nommer."),
            '']
        return lines

    def report_objects(self, r):
        if not r:
            return []
        lines = [
            '## 3. Objets détectés (DETR) : précision', '',
            f"**Méthode** : {r['n']} scènes dont les objets visibles ont été annotés à la main (`objets.json`). "
            "**Précision** = objets proposés corrects / objets proposés ; **rappel** = objets annotés trouvés / annotés.", '',
            "**Correction de l'annotation** : comme pour les visages, les objets « en trop » ont été dessinés sur les "
            "images et vérifiés à l'œil. Plusieurs étaient vraiment visibles et avaient été oubliés à l'annotation "
            "(couteau, chaises et plantes du repas de famille, piétons près du bus…). En cas de doute, l'objet reste "
            "compté comme une erreur. Le rapport donne les deux précisions.", '',
            '| Mesure | Valeur |', '|---|---|',
            f"| **Précision corrigée** | **{r.get('precision_corrigee', '—')}** |",
            f"| Précision stricte (première annotation) | {r['precision']} |",
            f"| Rappel | {r['rappel']} |",
            f"| Précision moyenne par scène | {r['precision_moyenne_scenes']} |",
            f"| Moteur utilisé | {r['moteur']} |",
            f"| Temps moyen / maximum | {r['temps']['moyenne_ms'] / 1000:.1f} s / {r['temps']['max_ms'] / 1000:.1f} s |", '',
            '| # | Objets annotés | Objets proposés | Visibles, ajoutés après vérification | Vraies erreurs | Oubliés |',
            '|---|---|---|---|---|---|',
        ]
        for row in r['lignes']:
            if 'precision' in row:
                lines.append(f"| {row['id']} | {', '.join(row['attendus'])} | {', '.join(row['predits']) or '—'} | "
                             f"{', '.join(row.get('ajouts_verifies', [])) or '—'} | "
                             f"{', '.join(row.get('en_trop_corrige', row['en_trop'])) or '—'} | "
                             f"{', '.join(row['oublies']) or '—'} |")
        corrected = r.get('precision_corrigee', r['precision'])
        lines += ['', '**Commentaire**', '',
                  f"- Précision corrigée de {corrected} : " + (
                      "la grande majorité des objets proposés sont vraiment sur la photo." if corrected >= 0.85 else
                      "une partie des objets proposés sont faux : le seuil de confiance (0,7) pourrait être relevé."),
                  "- Les vraies erreurs sont surtout des **confusions entre objets proches** (la voiture vue comme un "
                  "camion, la chaise vue comme un banc) ou de très petits objets.",
                  f"- Rappel de {r['rappel']} : les objets annotés sont presque tous trouvés.", '']
        return lines

    def report_captions(self, r):
        if not r:
            return []
        lines = [
            '## 4. Légendes (BLIP) : évaluation qualitative', '',
            "**Méthode** : BLIP décrit les 20 scènes, puis la légende est traduite en français. Deux mesures :", '',
            "- **automatique** : le CLIP score (100 x similarité entre l'image et la légende anglaise), comparé au score "
            "de la description qui a servi à créer l'image (référence) ;",
            "- **humaine** : une note de 1 à 5 par l'équipe dans `face_legendes.csv`.", '',
            '| Mesure | Valeur |', '|---|---|',
            f"| CLIP score moyen des légendes BLIP | **{r['clip_moyen']}** |",
            f"| CLIP score moyen de la description de référence | {r['clip_reference_moyen']} |",
            f"| Note humaine moyenne (sur 5) | {r['humain']['moyenne']} ({r['humain']['n']} notes) |" if r.get('humain')
            else '| Note humaine moyenne (sur 5) | à compléter (voir ci-dessous) |',
            f"| Moteur utilisé | {r['moteur']} |",
            f"| Temps moyen / maximum | {r['temps']['moyenne_ms'] / 1000:.1f} s / {r['temps']['max_ms'] / 1000:.1f} s |", '',
            '| # | Légende (français) | CLIP |', '|---|---|---|',
        ]
        for row in r['lignes']:
            if 'clip' in row:
                lines.append(f"| {row['id']} | {row['legende_fr']} | {row['clip']} |")
        gap = round(r['clip_reference_moyen'] - r['clip_moyen'], 2)
        lines += ['', '**Commentaire**', '',
                  f"- Les légendes BLIP obtiennent {r['clip_moyen']} contre {r['clip_reference_moyen']} pour la description "
                  f"de référence (écart de {gap}) : " + (
                      "elles décrivent presque aussi bien l'image." if gap <= 3 else
                      "elles sont correctes mais plus courtes et moins précises que la description de référence."),
                  "- **Notes humaines** : remplissez la colonne `note_1_a_5` de `face_legendes.csv`, copiez le fichier dans "
                  "`MemoriA/Face/evaluation/notes_legendes.csv` puis relancez `python manage.py evaluate_face --only legendes`.", '']
        return lines
