# Métriques d'évaluation du module Voice (utilisées par la commande evaluate_voice)
import re
import statistics

from .services import EMOTION_LABELS

EMOTIONS = ['Joie', 'Tristesse', 'Colere', 'Peur', 'Surprise', 'Neutre']
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(14)]         # 0.30, 0.35 ... 0.95


# ---------- Transcription : WER ----------

def normalize_for_wer(text):
    """Normalisation habituelle avant le WER : minuscules, sans ponctuation, apostrophes -> espace.
    Ainsi « J'ai » et « j ai » comptent comme les mêmes mots ; les accents restent (erreur réelle)."""
    text = text.lower().replace('’', "'").replace("'", ' ').replace('-', ' ')
    text = re.sub(r"[^\w\s]", ' ', text)
    return ' '.join(text.split())


def word_error_rate(reference, hypothesis):
    """WER = (substitutions + suppressions + insertions) / nombre de mots de la référence."""
    import jiwer
    return jiwer.wer(normalize_for_wer(reference), normalize_for_wer(hypothesis))


def wer_details(reference, hypothesis):
    """Nombre d'erreurs de chaque type, pour le rapport."""
    import jiwer
    out = jiwer.process_words(normalize_for_wer(reference), normalize_for_wer(hypothesis))
    return {'substitutions': out.substitutions, 'suppressions': out.deletions, 'insertions': out.insertions,
            'mots_reference': out.substitutions + out.deletions + out.hits}


# ---------- Émotions : seuil Neutre ----------

def predict(scores, threshold):
    """Même règle que services.to_emotion, avec un seuil donné."""
    best = max(scores, key=lambda r: r['score'])
    if best['score'] < threshold or best['label'] not in EMOTION_LABELS:
        return 'Neutre'
    return EMOTION_LABELS[best['label']]


def macro_f1(y_true, y_pred):
    from sklearn.metrics import f1_score
    return f1_score(y_true, y_pred, labels=EMOTIONS, average='macro', zero_division=0)


def known_accuracy(y_true, y_pred):
    """Part de bonnes réponses sur les phrases des 4 émotions que le modèle connaît."""
    pairs = [(t, p) for t, p in zip(y_true, y_pred) if t not in ('Surprise', 'Neutre')]
    return sum(t == p for t, p in pairs) / len(pairs) if pairs else 0


# Objectif fixé à l'avance : les 4 émotions connues du modèle restent bien reconnues
MIN_KNOWN_ACCURACY = 0.90


def best_threshold(all_scores, y_true):
    """Essaie chaque seuil. Règle : le meilleur F1 macro PARMI les seuils qui gardent au moins 90 % de bonnes
    réponses sur les 4 émotions connues (un seuil trop haut transforme de vraies émotions en « Neutre »).
    Renvoie (seuil choisi, [(seuil, F1 macro, précision sur les 4 émotions), ...])."""
    results = []
    for t in THRESHOLDS:
        y_pred = [predict(s, t) for s in all_scores]
        results.append((t, macro_f1(y_true, y_pred), known_accuracy(y_true, y_pred)))
    allowed = [r for r in results if r[2] >= MIN_KNOWN_ACCURACY - 1e-9] or results
    best = max(allowed, key=lambda r: (round(r[1], 4), -r[0]))      # le plus bas en cas d'égalité
    return best[0], results


def time_summary(durations_ms):
    if not durations_ms:
        return {'moyenne_ms': 0, 'max_ms': 0, 'n': 0}
    return {'moyenne_ms': round(statistics.mean(durations_ms)), 'max_ms': max(durations_ms), 'n': len(durations_ms)}
