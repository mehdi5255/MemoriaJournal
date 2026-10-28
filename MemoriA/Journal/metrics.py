# Métriques d'évaluation du module Journal (utilisées par la commande evaluate_journal)
import re
import statistics
import unicodedata

THRESHOLDS = [round(0.10 + 0.05 * i, 2) for i in range(18)]         # 0.10, 0.15 ... 0.95
STOP_WORDS = {'avec', 'dans', 'pour', 'sans', 'mais', 'une', 'des', 'les', 'mes', 'mon', 'est', 'était', 'suis',
              'avais', 'chez', 'moi', 'loin', 'faire', 'trouver', 'arrivais', 'pas', 'qui', 'que', 'sur'}


# ---------- Recherche : Precision@k, MRR ----------

def precision_at_k(ranked, relevant, k=3):
    """Part des k premiers résultats qui sont pertinents."""
    return sum(1 for r in ranked[:k] if r in relevant) / k


def best_precision_at_k(relevant, k=3):
    """Maximum atteignable : avec 1 seule entrée pertinente, Precision@3 ne peut pas dépasser 1/3."""
    return min(len(relevant), k) / k


def hit_at_k(ranked, relevant, k=3):
    return 1 if any(r in relevant for r in ranked[:k]) else 0


def reciprocal_rank(ranked, relevant):
    """1 si la première entrée pertinente est en tête, 1/2 si elle est 2e… 0 si elle n'est pas trouvée."""
    for position, r in enumerate(ranked, start=1):
        if r in relevant:
            return 1 / position
    return 0


def words(text):
    text = unicodedata.normalize('NFD', text.lower())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return {w for w in re.findall(r"[a-z]+", text) if len(w) > 2 and w not in STOP_WORDS}


def keyword_ranking(query, documents):
    """Méthode de comparaison : recherche par mots (nombre de mots de la requête présents dans l'entrée).
    documents = {id: texte}. Les entrées sans aucun mot commun ne sont pas renvoyées."""
    query_words = words(query)
    scores = {doc_id: len(query_words & words(text)) for doc_id, text in documents.items()}
    return [doc_id for doc_id, score in sorted(scores.items(), key=lambda p: (-p[1], p[0])) if score > 0]


# ---------- Étiquettes : seuil ----------

def predict_tags(scores, threshold):
    return sorted(label for label, score in scores.items() if score >= threshold)


def micro_scores(y_true_sets, y_pred_sets):
    """Precision / Recall / F1 micro sur toutes les paires (texte, étiquette)."""
    tp = sum(len(t & p) for t, p in zip(y_true_sets, y_pred_sets))
    fp = sum(len(p - t) for t, p in zip(y_true_sets, y_pred_sets))
    fn = sum(len(t - p) for t, p in zip(y_true_sets, y_pred_sets))
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    return precision, recall, f1


def best_threshold(all_scores, y_true_sets):
    """Règle fixée avant l'évaluation : le seuil qui donne le meilleur F1 micro (le plus haut en cas d'égalité,
    pour proposer moins d'étiquettes fausses). Renvoie (seuil, [(seuil, precision, recall, f1), ...])."""
    results = []
    for t in THRESHOLDS:
        y_pred = [set(predict_tags(s, t)) for s in all_scores]
        results.append((t, *micro_scores(y_true_sets, y_pred)))
    best = max(results, key=lambda r: (round(r[3], 4), r[0]))
    return best[0], results


def time_summary(durations_ms):
    if not durations_ms:
        return {'moyenne_ms': 0, 'max_ms': 0, 'n': 0}
    return {'moyenne_ms': round(statistics.mean(durations_ms)), 'max_ms': max(durations_ms), 'n': len(durations_ms)}
