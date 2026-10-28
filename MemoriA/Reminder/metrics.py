# Métriques d'évaluation du module Reminder (utilisées par la commande evaluate_reminder)
import math
import statistics
import unicodedata

ENTITY_TYPES = ['PER', 'LOC', 'ORG', 'MISC']
NONE = 'Aucune'                     # entité oubliée ou inventée
THRESHOLDS = [round(0.3 + 0.1 * i, 1) for i in range(7)]          # 0.3, 0.4 ... 0.9


# ---------- Meilleure heure : erreur en minutes ----------

def to_minutes(hhmm):
    hours, minutes = map(int, hhmm.split(':'))
    return hours * 60 + minutes


def minutes_error(predicted, expected):
    """Écart en minutes sur le cadran de 24 h : entre 23 h 50 et 0 h 10, il y a 20 minutes (pas 23 h 40)."""
    gap = abs(predicted - expected) % 1440
    return min(gap, 1440 - gap)


def mean_baseline(minutes_list):
    """Méthode naïve : la moyenne des heures. Elle se trompe autour de minuit (23 h et 1 h -> midi)."""
    return round(statistics.mean(minutes_list)) % 1440


def median_baseline(minutes_list):
    return round(statistics.median(minutes_list)) % 1440


def circular_mean(minutes_list):
    """Moyenne sur le cercle (sans regrouper) : règle minuit mais reste tirée par les entrées au hasard."""
    angles = [2 * math.pi * m / 1440 for m in minutes_list]
    x = sum(math.cos(a) for a in angles)
    y = sum(math.sin(a) for a in angles)
    return round((math.degrees(math.atan2(y, x)) % 360) / 360 * 1440) % 1440


def mae(errors):
    return round(statistics.mean(errors), 1) if errors else 0


# ---------- Entités nommées : alignement puis Precision / Recall / F1 ----------

def normalize(text):
    """Minuscules, sans accents ni ponctuation : « Leïla » = « leila »."""
    text = unicodedata.normalize('NFD', text.lower().replace('’', "'"))
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return ' '.join(text.replace('-', ' ').replace("'", ' ').split())


def same_text(a, b, partial):
    a, b = normalize(a), normalize(b)
    return a == b or (partial and (a in b or b in a))


def align(expected, predicted, partial=False):
    """Associe les entités attendues et prédites qui ont le même texte, puis renvoie deux listes de types
    (y_true, y_pred) pour sklearn : une entité oubliée donne (type, 'Aucune'), une entité inventée ('Aucune', type).
    expected / predicted : listes de (type, texte)."""
    y_true, y_pred = [], []
    remaining = list(predicted)
    for e_type, e_text in expected:
        match = next((p for p in remaining if p[0] == e_type and same_text(p[1], e_text, partial)), None) \
            or next((p for p in remaining if same_text(p[1], e_text, partial)), None)
        if match:
            remaining.remove(match)
        y_true.append(e_type)
        y_pred.append(match[0] if match else NONE)
    for p_type, _ in remaining:
        y_true.append(NONE)
        y_pred.append(p_type)
    return y_true, y_pred


def time_summary(durations_ms):
    if not durations_ms:
        return {'moyenne_ms': 0, 'max_ms': 0, 'n': 0}
    return {'moyenne_ms': round(statistics.mean(durations_ms)), 'max_ms': max(durations_ms), 'n': len(durations_ms)}
