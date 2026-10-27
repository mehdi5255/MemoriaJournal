/*
 * Suivi des tâches IA (Celery) — script commun à toutes les apps.
 *
 * Utilisation dans un template :
 *   {% include "includes/ai_status.html" with task_id=task_id label="Transcription en cours…" %}
 *
 * Toutes les 2 s, le script interroge la vue JSON ai/task/<task_id>/ :
 *   - PENDING : on continue d'attendre
 *   - SUCCESS : on affiche le résultat (result.html si fourni) et on émet l'événement "ai:success"
 *   - FAILURE : on affiche le message d'erreur et le bouton Réessayer
 */
(function () {
    'use strict';

    var INTERVAL_MS = 2000;
    var MAX_TRIES = 150;    // 5 minutes au maximum

    function show(el, visible) {
        if (el) { el.hidden = !visible; }
    }

    function watch(box) {
        var url = box.dataset.statusUrl;
        var tries = 0;
        var loading = box.querySelector('.ai-loading');
        var success = box.querySelector('.ai-success');
        var error = box.querySelector('.ai-error');

        function fail(message) {
            show(loading, false);
            var text = error && error.querySelector('p');
            if (text && typeof message === 'string' && message) { text.textContent = message; }
            show(error, true);
            box.setAttribute('aria-busy', 'false');
            var retry = error && error.querySelector('button, a');
            if (retry) { retry.focus(); }
            box.dispatchEvent(new CustomEvent('ai:failure', { bubbles: true }));
        }

        function done(result) {
            show(loading, false);
            box.setAttribute('aria-busy', 'false');
            var target = box.dataset.resultTarget && document.querySelector(box.dataset.resultTarget);
            if (target && result && result.html) {
                target.innerHTML = result.html;
            }
            if (success) {
                if (result && result.message) { success.textContent = result.message; }
                show(success, true);
            }
            box.dispatchEvent(new CustomEvent('ai:success', { bubbles: true, detail: result || {} }));
            if (box.dataset.reload === 'true') { window.location.reload(); }
        }

        function poll() {
            tries += 1;
            if (tries > MAX_TRIES) { fail(); return; }
            fetch(url, { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
                .then(function (response) {
                    if (!response.ok) { throw new Error('HTTP ' + response.status); }
                    return response.json();
                })
                .then(function (data) {
                    if (data.status === 'SUCCESS') { done(data.result); }
                    else if (data.status === 'FAILURE') { fail(data.message); }
                    else { setTimeout(poll, INTERVAL_MS); }
                })
                .catch(function () { fail(); });
        }

        box.setAttribute('aria-busy', 'true');
        show(loading, true);
        show(success, false);
        show(error, false);
        poll();
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-ai-task]').forEach(watch);
    });

    // Permet de suivre une tâche ajoutée plus tard dans la page
    window.MemoriaAI = { watch: watch };
})();
