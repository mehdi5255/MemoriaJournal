/*
 * Graphique du mois sur la page Humeur (Chart.js).
 * Les mêmes données sont aussi dans un tableau HTML juste en dessous (accessibilité).
 */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        var canvas = document.getElementById('graphique-humeur');
        var source = document.getElementById('donnees-humeur');
        if (!canvas || !source || !window.Chart) { return; }

        var data = JSON.parse(source.textContent);
        var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        new window.Chart(canvas, {
            type: 'bar',
            data: {
                labels: data.labels,
                datasets: [{
                    label: 'Intensité moyenne',
                    data: data.scores,
                    backgroundColor: data.colors,
                    borderRadius: 6
                }]
            },
            options: {
                animation: reduceMotion ? false : undefined,
                maintainAspectRatio: false,
                scales: {
                    y: { min: 0, max: 1, title: { display: true, text: 'Intensité (0 à 1)' } },
                    x: { title: { display: true, text: 'Jour du mois' } }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (item) {
                                return data.emotions[item.dataIndex] + ' : ' + item.formattedValue;
                            }
                        }
                    }
                }
            }
        });
    });
})();
