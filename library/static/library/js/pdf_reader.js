'use strict';
const pdfContainer = document.getElementById('pdf-reader-container');
const fullscreenButton = document.getElementById('pdf-fullscreen');
if (pdfContainer && fullscreenButton && document.fullscreenEnabled && pdfContainer.requestFullscreen) {
    fullscreenButton.hidden = false;
    fullscreenButton.addEventListener('click', async () => {
        try {
            if (document.fullscreenElement) await document.exitFullscreen();
            else await pdfContainer.requestFullscreen();
        } catch (_) {
            fullscreenButton.hidden = true;
        }
    });
    document.addEventListener('fullscreenchange', () => {
        fullscreenButton.textContent = document.fullscreenElement ? fullscreenButton.dataset.exit : fullscreenButton.dataset.enter;
    });
}
