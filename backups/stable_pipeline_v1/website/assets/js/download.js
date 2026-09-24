/* ============================================================
   FilmSub – download.js | Download Countdown System
   ============================================================ */

'use strict';

(function () {
  // Constants
  const COUNTDOWN_SECONDS = 5;
  const CIRCUMFERENCE = 283; // 2 * pi * 45

  // State
  let countdownTimer = null;
  let pendingURL = '';
  let pendingQuality = '';
  let pendingTitle = '';

  // DOM refs (created lazily)
  let overlay = null;

  // ---- Init ----
  document.addEventListener('DOMContentLoaded', () => {
    buildOverlay();
  });

  // ---- Build Overlay DOM ----
  function buildOverlay() {
    if (document.getElementById('countdown-overlay')) {
      overlay = document.getElementById('countdown-overlay');
      return;
    }
    overlay = document.createElement('div');
    overlay.id = 'countdown-overlay';
    overlay.className = 'countdown-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.innerHTML = `
      <div class="countdown-box" role="document">
        <div class="countdown-icon">
          <i class="fa-solid fa-download"></i>
        </div>
        <div class="countdown-title">Preparing Your Download</div>
        <div class="countdown-sub" id="cd-quality-label">Please wait while we redirect you...</div>

        <div class="countdown-ring-wrap" id="cd-ring-shown">
          <svg class="countdown-ring" viewBox="0 0 100 100">
            <circle class="countdown-ring-bg" cx="50" cy="50" r="45"/>
            <circle class="countdown-ring-progress" id="cd-ring-progress" cx="50" cy="50" r="45"/>
          </svg>
          <div class="countdown-number" id="cd-number">${COUNTDOWN_SECONDS}</div>
        </div>

        <a id="cd-get-link" class="countdown-get-link btn" href="#" target="_blank" rel="noopener noreferrer">
          <i class="fa-solid fa-download"></i>
          Get Download Link
        </a>

        <button class="countdown-close" id="cd-close-btn" type="button">
          <i class="fa-solid fa-xmark"></i> Cancel
        </button>
      </div>`;

    document.body.appendChild(overlay);

    // Close on backdrop click
    overlay.addEventListener('click', e => {
      if (e.target === overlay) closeCountdown();
    });

    // Close button
    document.getElementById('cd-close-btn').addEventListener('click', closeCountdown);

    // Trap focus
    overlay.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeCountdown();
    });
  }

  // ---- Show Countdown ----
  function show(url, quality, movieTitle) {
    pendingURL = url || '#';
    pendingQuality = quality || '';
    pendingTitle = movieTitle || '';

    buildOverlay();

    // Reset state
    clearInterval(countdownTimer);
    let remaining = COUNTDOWN_SECONDS;

    const numberEl = document.getElementById('cd-number');
    const ringEl = document.getElementById('cd-ring-progress');
    const ringWrap = document.getElementById('cd-ring-shown');
    const getLinkEl = document.getElementById('cd-get-link');
    const qualityLabel = document.getElementById('cd-quality-label');

    if (qualityLabel) qualityLabel.textContent = quality
      ? `Preparing ${quality} download for "${movieTitle || 'this movie'}"...`
      : 'Please wait while we prepare your download...';

    if (numberEl) numberEl.textContent = remaining;
    if (ringEl) { ringEl.style.strokeDashoffset = '0'; ringEl.style.transition = 'none'; }
    if (ringWrap) ringWrap.style.display = 'block';
    if (getLinkEl) {
      getLinkEl.classList.remove('visible');
      getLinkEl.href = '#';
    }

    // Show overlay
    overlay.classList.add('active');
    document.body.classList.add('no-scroll');
    overlay.focus();

    // Animate ring immediately
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (ringEl) {
          ringEl.style.transition = `stroke-dashoffset ${COUNTDOWN_SECONDS}s linear`;
          ringEl.style.strokeDashoffset = String(CIRCUMFERENCE);
        }
      });
    });

    // Tick
    countdownTimer = setInterval(() => {
      remaining--;
      if (numberEl) numberEl.textContent = remaining;

      if (remaining <= 0) {
        clearInterval(countdownTimer);
        // Hide ring, show button
        if (ringWrap) ringWrap.style.display = 'none';
        if (getLinkEl) {
          getLinkEl.href = pendingURL;
          getLinkEl.classList.add('visible');

          // Auto-trigger download
          triggerDownload(pendingURL, pendingQuality);
        }
        if (qualityLabel) qualityLabel.textContent = 'Your download is ready!';
      }
    }, 1000);
  }

  // ---- Auto trigger download ----
  function triggerDownload(url, quality) {
    if (!url || url === '#') return;
    // Create a temp anchor and click it
    const a = document.createElement('a');
    a.href = url;
    a.download = quality ? `FilmSub-${quality}.mp4` : 'FilmSub-download.mp4';
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // ---- Close ----
  function closeCountdown() {
    clearInterval(countdownTimer);
    if (overlay) overlay.classList.remove('active');
    document.body.classList.remove('no-scroll');
    pendingURL = '';
  }

  // ---- Expose ----
  window.FilmSubDownload = { show, close: closeCountdown };
})();
