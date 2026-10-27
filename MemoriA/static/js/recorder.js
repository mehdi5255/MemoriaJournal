/*
 * Enregistrement de la voix avec MediaRecorder — utilisable entièrement au clavier.
 *
 * Structure attendue dans la page (voir Voice/voicenote_form.html) :
 *   <section data-recorder data-input="#id_audio" data-duration="#id_client_duration" data-max-seconds="600">
 *     <button data-action="start">…</button> <button data-action="stop">…</button>
 *     <p class="recorder-timer"></p> <p class="recorder-status" role="status"></p> <audio controls hidden></audio>
 *   </section>
 *
 * À l'arrêt, l'enregistrement est placé dans le champ fichier du formulaire :
 * le formulaire est envoyé normalement (avec son jeton CSRF) et validé par le serveur.
 */
(function () {
    'use strict';

    function format(seconds) {
        var m = Math.floor(seconds / 60), s = seconds % 60;
        return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    function setup(box) {
        var startBtn = box.querySelector('[data-action="start"]');
        var stopBtn = box.querySelector('[data-action="stop"]');
        var timer = box.querySelector('.recorder-timer');
        var status = box.querySelector('.recorder-status');
        var preview = box.querySelector('audio');
        var input = document.querySelector(box.dataset.input);
        var durationField = document.querySelector(box.dataset.duration);
        var maxSeconds = parseInt(box.dataset.maxSeconds || '600', 10);
        var recorder = null, chunks = [], seconds = 0, tick = null, stream = null;

        function say(message) { status.textContent = message; }

        if (!navigator.mediaDevices || !window.MediaRecorder) {
            startBtn.disabled = true;
            say("L'enregistrement n'est pas possible avec ce navigateur : importez un fichier audio ci-dessous.");
            return;
        }

        function stop() {
            if (recorder && recorder.state === 'recording') { recorder.stop(); }
        }

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
                stopBtn.focus();                       // le focus suit l'action suivante possible
                preview.hidden = true;
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

        function finish() {
            clearInterval(tick);
            stream.getTracks().forEach(function (track) { track.stop(); });
            var blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
            var file = new File([blob], 'enregistrement.webm', { type: blob.type });

            // Place l'enregistrement dans le champ fichier du formulaire
            var transfer = new DataTransfer();
            transfer.items.add(file);
            input.files = transfer.files;
            if (durationField) { durationField.value = Math.max(1, seconds); }

            preview.src = URL.createObjectURL(blob);
            preview.hidden = false;
            startBtn.disabled = false;
            startBtn.textContent = 'Recommencer';
            stopBtn.disabled = true;
            startBtn.focus();
            say('Enregistrement terminé (' + format(seconds) + '). Écoutez-le, puis cliquez sur « Enregistrer ».');
        }

        // Fichier importé : le navigateur lit sa durée (le serveur la vérifie aussi)
        input.addEventListener('change', function () {
            if (!input.files.length || !durationField) { return; }
            var probe = document.createElement('audio');
            probe.preload = 'metadata';
            probe.addEventListener('loadedmetadata', function () {
                if (isFinite(probe.duration)) { durationField.value = Math.round(probe.duration); }
                if (probe.duration > maxSeconds) {
                    say('Ce fichier dure plus de 10 minutes : il sera refusé.');
                }
                URL.revokeObjectURL(probe.src);
            });
            probe.src = URL.createObjectURL(input.files[0]);
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-recorder]').forEach(setup);
    });
})();
