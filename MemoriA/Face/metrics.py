# Métriques d'évaluation du module Face (utilisées par la commande evaluate_face)
import statistics
import unicodedata

UNKNOWN = 'Inconnu'


def normalize(text):
    text = unicodedata.normalize('NFKD', text.lower().strip())
    return ''.join(c for c in text if not unicodedata.combining(c))


# ---------- Objets (DETR) ----------

def object_scores(predicted, expected):
    """Précision = objets prédits corrects / objets prédits ; rappel = objets attendus trouvés / attendus.
    La comparaison ignore accents et majuscules ; les listes renvoyées gardent les noms d'origine."""
    pred = {normalize(p): p for p in predicted}
    true = {normalize(e): e for e in expected}
    correct = pred.keys() & true.keys()
    return {'corrects': sorted(true[k] for k in correct),
            'en_trop': sorted(pred[k] for k in pred.keys() - true.keys()),
            'oublies': sorted(true[k] for k in true.keys() - pred.keys()),
            'precision': len(correct) / len(pred) if pred else 1.0,
            'rappel': len(correct) / len(true) if true else 1.0}


# ---------- Détection des visages (YuNet) ----------

def detection_counts(detections, tiles, secondary=0):
    """Collage de vignettes : une vignette contient 1 visage principal (photo LFW) ou aucun (scène).
    Une détection compte dans la vignette où tombe son centre.
    - vrai positif : 1re détection d'une vignette avec visage ;
    - visage secondaire : détection en plus dans une vignette LFW, vérifiée à l'œil comme un vrai visage
      à l'arrière-plan (au plus `secondary` par collage, voir visages.json) ;
    - faux positif : toute autre détection (en trop, ou dans une scène sans personne) ;
    - faux négatif : vignette avec visage sans aucune détection.
    Renvoie (vrais positifs, visages secondaires, faux positifs, faux négatifs)."""
    tp = extra = fp = 0
    found = [0] * len(tiles)
    for d in detections:
        cx, cy = d['x'] + d['width'] / 2, d['y'] + d['height'] / 2
        index = next((i for i, t in enumerate(tiles)
                      if t['x'] <= cx < t['x'] + t['size'] and t['y'] <= cy < t['y'] + t['size']), None)
        if index is not None and tiles[index]['visage'] and found[index] == 0:
            tp += 1
        elif index is not None and tiles[index]['visage'] and extra < secondary:
            extra += 1
        else:
            fp += 1
        if index is not None:
            found[index] += 1
    fn = sum(1 for i, t in enumerate(tiles) if t['visage'] and found[i] == 0)
    return tp, extra, fp, fn


def precision_recall(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


# ---------- Identification (SFace) ----------

def mean_encoding(features):
    """Même règle que l'application : moyenne des empreintes, puis normalisation."""
    import numpy as np
    mean = np.mean(features, axis=0)
    return mean / (np.linalg.norm(mean) or 1)


def identify(feature, references, threshold):
    """Nom de la personne la plus ressemblante, ou « Inconnu » sous le seuil."""
    from .services import cosine
    best, best_score = UNKNOWN, -1.0
    for name, encoding in references.items():
        score = cosine(feature, encoding)
        if score > best_score:
            best, best_score = name, score
    return (best if best_score >= threshold else UNKNOWN), best_score


def time_summary(durations_ms):
    if not durations_ms:
        return {'moyenne_ms': 0, 'max_ms': 0, 'n': 0}
    return {'moyenne_ms': round(statistics.mean(durations_ms)), 'max_ms': max(durations_ms), 'n': len(durations_ms)}
