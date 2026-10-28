/*
 * Dictée dans le formulaire d'une entrée (Journal + Voice).
 *
 * 1. MediaRecorder enregistre la voix (boutons utilisables au clavier), ou l'utilisateur importe un fichier.
 * 2. L'audio est envoyé à voice:dictation (POST + jeton CSRF), qui lance la transcription Whisper (Celery).
 * 3. ai-status.js suit la tâche ; à la fin, le texte est ajouté au champ « Ma journée »
 *    et le jeton de la dictée est gardé dans un champ caché : à l'enregistrement de l'entrée,
 *    l'audio devient une note vocale de cette entrée.
 */
(function () {
    'use strict';

    var box = document.getElementById('dictee');
    if (!box) { return; }
    var form = box.closest('form');
    var target = document.querySelector(box.dataset.target);
    var tokens = document.querySelector(box.dataset.tokens);
    var startBtn = box.querySelector('[data-action="start"]');
    var stopBtn = box.querySelector('[data-action="stop"]');
    var timer = box.querySelector('.dictation-timer');
    var status = box.querySelector('.dictation-status');
    var fileInput = document.getElementById('dictee-fichier');
    var zone = box.querySelector('.dictation-zone');
    var list = box.querySelector('.dictation-list');
    var model = box.querySelector('.dictation-modele');
    var maxSeconds = parseInt(box.dataset.maxSeconds || '600', 10);
    var recorder = null, chunks = [], seconds = 0, tick = null, stream = null;

    function format(s) {
        var m = Math.floor(s / 60), r = s % 60;
        return (m < 10 ? '0' : '') + m + ':' + (r < 10 ? '0' : '') + r;
    }

    function say(message) { status.textContent = message; }

    function addToken(token) {
        var values = tokens.value ? tokens.value.split(',') : [];
        if (values.indexOf(token) === -1) { values.push(token); }
        tokens.value = values.join(',');
    }

    function insertText(text) {
        // Le texte dicté s'ajoute à la suite de ce qui est déjà écrit
        target.value = target.value.trim() ? target.value.trim() + '\n\n' + text : text;
        target.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function showError(statusBox, message, retry) {
        statusBox.querySelector('.ai-loading').hidden = true;
        statusBox.querySelector('.ai-error p').textContent = message;
        statusBox.querySelector('.ai-error').hidden = false;
        statusBox.setAttribute('aria-busy', 'false');
        var button = statusBox.querySelector('.js-retry');
        button.onclick = retry;
        button.focus();
    }

    function send(file, duration) {
        zone.innerHTML = '';
        var statusBox = model.content.firstElementChild.cloneNode(true);
        zone.appendChild(statusBox);                       // chargement visible tout de suite
        var data = new FormData();
        data.append('csrfmiddlewaretoken', form.querySelector('[name="csrfmiddlewaretoken"]').value);
        data.append('audio', file);
        data.append('duration', String(duration || ''));
        var retry = function () { send(file, duration); };

        fetch(box.dataset.url, { method: 'POST', body: data, credentials: 'same-origin' })
            .then(function (response) { return response.json(); })
            .then(function (answer) {
                if (answer.error) { showError(statusBox, answer.error, retry); return; }
                statusBox.dataset.statusUrl = answer.status_url;
                statusBox.addEventListener('ai:success', function (event) {
                    insertText(event.detail.text || '');
                    addToken(answer.token);
                    var item = document.createElement('li');
                    item.textContent = '✓ Dictée de ' + format(duration || 0) +
                        ' : elle sera gardée comme note vocale à l\'enregistrement de l\'entrée.';
                    list.appendChild(item);
                    target.focus();
                });
                statusBox.addEventListener('ai:failure', function () {
                    statusBox.querySelector('.js-retry').onclick = retry;
                });
                window.MemoriaAI.watch(statusBox);
            })
            .catch(function () { showError(statusBox, "Le service ne répond pas. Réessayez dans un instant.", retry); });
    }

    function stop() {
        if (recorder && recorder.state === 'recording') { recorder.stop(); }
    }

    function finish() {
        clearInterval(tick);
        stream.getTracks().forEach(function (track) { track.stop(); });
        var blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        startBtn.disabled = false;
        stopBtn.disabled = true;
        startBtn.focus();
        say('Enregistrement terminé (' + format(seconds) + ') : transcription en cours.');
        send(new File([blob], 'dictee.webm', { type: blob.type }), Math.max(1, seconds));
    }

    if (!navigator.mediaDevices || !window.MediaRecorder) {
        startBtn.disabled = true;
        say("L'enregistrement n'est pas possible avec ce navigateur : importez un fichier audio.");
    } else {
        startBtn.addEventListener('click', function () {
            navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
                stream = s;
                chunks = [];
                seconds = 0;
                var type = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : '';
                recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream);
                recorder.addEventListener('dataavailable', function (e) { if (e.data.size) { chunks.push(e.data); } });
                recorder.addEventListener('stop', finish);
                recorder.start();
                startBtn.disabled = true;
                stopBtn.disabled = false;
                stopBtn.focus();
                timer.textContent = format(0) + ' / ' + format(maxSeconds);
                say('Enregistrement en cours. Appuyez sur « Arrêter » pour terminer.');
                tick = setInterval(function () {
                    seconds += 1;
                    timer.textContent = format(seconds) + ' / ' + format(maxSeconds);
                    if (seconds >= maxSeconds) {
                        say('Durée maximale de 10 minutes atteinte : enregistrement arrêté.');
                        stop();
                    }
                }, 1000);
            }).catch(function () {
                say("Le micro n'est pas accessible (autorisation refusée ou absent) : importez un fichier audio.");
            });
        });
        stopBtn.addEventListener('click', stop);
    }

    // Fichier importé : le navigateur lit sa durée (le serveur la vérifie aussi)
    fileInput.addEventListener('change', function () {
        if (!fileInput.files.length) { return; }
        var file = fileInput.files[0];
        var probe = document.createElement('audio');
        probe.preload = 'metadata';
        probe.addEventListener('loadedmetadata', function () {
            URL.revokeObjectURL(probe.src);
            send(file, isFinite(probe.duration) ? Math.round(probe.duration) : '');
        });
        probe.addEventListener('error', function () { send(file, ''); });
        probe.src = URL.createObjectURL(file);
        fileInput.value = '';
    });
})();
