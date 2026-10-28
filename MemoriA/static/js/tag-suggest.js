/*
 * Suggestion d'étiquettes dans le formulaire d'une entrée (module Journal).
 *
 * 1. Le bouton envoie le titre et le texte à la vue journal:tag_suggest (POST, jeton CSRF du formulaire).
 * 2. La vue lance la tâche Celery et renvoie l'adresse de suivi : ai-status.js interroge cette adresse.
 * 3. Quand la tâche réussit, les cases des étiquettes proposées sont cochées (rien n'est enregistré
 *    avant que l'utilisateur clique sur « Enregistrer »).
 */
(function () {
    'use strict';

    var button = document.getElementById('suggest-tags');
    if (!button) { return; }
    var form = document.getElementById('entry-form');
    var zone = document.getElementById('suggestion-zone');
    var result = document.getElementById('suggestion-resultat');
    var model = document.getElementById('suggestion-modele');

    function newBox() {
        // L'indicateur de chargement apparaît tout de suite, avant même la réponse du serveur
        zone.innerHTML = '';
        var box = model.content.firstElementChild.cloneNode(true);
        box.querySelector('.js-retry').addEventListener('click', suggest);
        zone.appendChild(box);
        return box;
    }

    function showError(box, message) {
        box.querySelector('.ai-loading').hidden = true;
        box.querySelector('.ai-error p').textContent = message;
        box.querySelector('.ai-error').hidden = false;
        box.setAttribute('aria-busy', 'false');
        box.querySelector('.js-retry').focus();
    }

    function checkTags(ids) {
        form.querySelectorAll('input[name="tags"]').forEach(function (input) {
            var suggested = ids.indexOf(parseInt(input.value, 10)) !== -1;
            input.classList.toggle('ai-suggested', suggested);
            if (suggested) { input.checked = true; }
        });
    }

    function suggest() {
        var data = new FormData();
        data.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]').value);
        data.append('title', form.querySelector('[name="title"]').value);
        data.append('content', form.querySelector('[name="content"]').value);
        result.innerHTML = '';
        button.disabled = true;
        var box = newBox();

        fetch(button.dataset.url, { method: 'POST', body: data, credentials: 'same-origin' })
            .then(function (response) { return response.json(); })
            .then(function (answer) {
                button.disabled = false;
                if (answer.error) { showError(box, answer.error); return; }
                box.dataset.statusUrl = answer.status_url;
                box.dataset.resultTarget = '#suggestion-resultat';
                box.addEventListener('ai:success', function (event) { checkTags(event.detail.tag_ids || []); });
                window.MemoriaAI.watch(box);
            })
            .catch(function () {
                button.disabled = false;
                showError(box, "Le service ne répond pas. Réessayez dans un instant.");
            });
    }

    button.addEventListener('click', suggest);
})();
