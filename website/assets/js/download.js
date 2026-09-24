/* ============================================================
   FilmSub – download.js | One-Click Instant Direct Download Engine
   ============================================================ */

'use strict';

(function () {
  // Fast 1-second preparation for instant user-friendly UX (no annoying long waits, no Drive OK screen)
  const COUNTDOWN_SECONDS = 1;
  const CIRCUMFERENCE = 283; // 2 * pi * 45

  // State
  let countdownTimer = null;
  let pendingURL = '';
  let pendingQuality = '';
  let pendingTitle = '';

  // DOM refs (created lazily)
  let overlay = null;
  let downloadIframe = null;

  // ---- Convert any Google Drive link into our Edge /api/download endpoint ----
  function toDirectDownloadUrl(rawUrl, quality, movieTitle) {
    if (!rawUrl || rawUrl === '#') return '#';
    const str = String(rawUrl).trim();
    const q = String(quality || '1080p').replace(/[^a-zA-Z0-9]/g, '') || '1080p';
    const t = String(movieTitle || 'Movie').trim() || 'Movie';

    // Already pointing to /api/download
    if (str.startsWith('/api/download') || str.includes('/api/download?')) {
      try {
        const u = new URL(str, window.location.origin);
        if (!u.searchParams.get('q') && q) u.searchParams.set('q', q);
        if (!u.searchParams.get('title') && t) u.searchParams.set('title', t);
        return u.pathname + u.search;
      } catch (e) {
        return str;
      }
    }

    // Extract Google Drive file ID if present
    let driveId = '';
    if (/^[a-zA-Z0-9_-]{15,60}$/.test(str)) {
      driveId = str;
    } else if (str.includes('drive.google.com') || str.includes('drive.usercontent.google.com') || str.includes('/api/stream')) {
      const m1 = str.match(/\/d\/([a-zA-Z0-9_-]{15,60})/);
      const m2 = str.match(/[?&]id=([a-zA-Z0-9_-]{15,60})/);
      driveId = (m1 && m1[1]) || (m2 && m2[1]) || '';
    }

    if (driveId) {
      return `/api/download?id=${encodeURIComponent(driveId)}&q=${encodeURIComponent(q)}&title=${encodeURIComponent(t)}`;
    }

    return str;
  }

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
          <i class="fa-solid fa-cloud-arrow-down"></i>
        </div>
        <div class="countdown-title" id="cd-main-title">Starting Direct Download...</div>
        <div class="countdown-sub" id="cd-quality-label">Connecting to High-Speed Edge CDN...</div>

        <div class="countdown-ring-wrap" id="cd-ring-shown">
          <svg class="countdown-ring" viewBox="0 0 100 100">
            <circle class="countdown-ring-bg" cx="50" cy="50" r="45"/>
            <circle class="countdown-ring-progress" id="cd-ring-progress" cx="50" cy="50" r="45"/>
          </svg>
          <div class="countdown-number" id="cd-number">${COUNTDOWN_SECONDS}</div>
        </div>

        <a id="cd-get-link" class="countdown-get-link btn" href="#" download>
          <i class="fa-solid fa-download"></i>
          Click Here if Download Didn't Start
        </a>

        <button class="countdown-close" id="cd-close-btn" type="button">
          <i class="fa-solid fa-xmark"></i> Close
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

  // ---- Show & Trigger Instant Download ----
  function show(url, quality, movieTitle) {
    pendingQuality = quality || '1080p';
    pendingTitle = movieTitle || 'Movie';
    pendingURL = toDirectDownloadUrl(url, pendingQuality, pendingTitle);

    buildOverlay();

    clearInterval(countdownTimer);
    let remaining = COUNTDOWN_SECONDS;

    const mainTitleEl = document.getElementById('cd-main-title');
    const numberEl = document.getElementById('cd-number');
    const ringEl = document.getElementById('cd-ring-progress');
    const ringWrap = document.getElementById('cd-ring-shown');
    const getLinkEl = document.getElementById('cd-get-link');
    const qualityLabel = document.getElementById('cd-quality-label');

    if (mainTitleEl) mainTitleEl.textContent = `Downloading ${pendingQuality} MP4`;
    if (qualityLabel) {
      qualityLabel.textContent = `Starting "${pendingTitle}" (${pendingQuality} • Sinhala Sub Merged)...`;
    }

    if (numberEl) numberEl.textContent = remaining;
    if (ringEl) { ringEl.style.strokeDashoffset = '0'; ringEl.style.transition = 'none'; }
    if (ringWrap) ringWrap.style.display = 'block';
    if (getLinkEl) {
      getLinkEl.classList.remove('visible');
      getLinkEl.href = pendingURL;
    }

    // Show overlay
    overlay.classList.add('active');
    document.body.classList.add('no-scroll');
    overlay.focus();

    // Trigger browser download immediately via hidden iframe / anchor (zero Drive warning page!)
    triggerDownload(pendingURL, pendingQuality, pendingTitle);

    // Animate ring
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (ringEl) {
          ringEl.style.transition = `stroke-dashoffset ${COUNTDOWN_SECONDS}s linear`;
          ringEl.style.strokeDashoffset = String(CIRCUMFERENCE);
        }
      });
    });

    countdownTimer = setInterval(() => {
      remaining--;
      if (numberEl) numberEl.textContent = Math.max(0, remaining);

      if (remaining <= 0) {
        clearInterval(countdownTimer);
        if (ringWrap) ringWrap.style.display = 'none';
        if (getLinkEl) {
          getLinkEl.href = pendingURL;
          getLinkEl.classList.add('visible');
        }
        if (mainTitleEl) mainTitleEl.textContent = 'Download Started! ✅';
        if (qualityLabel) {
          qualityLabel.textContent = `Your ${pendingQuality} download has started in your browser. Enjoy watching!`;
        }
        // Auto-close modal after 3.5s once download is underway
        setTimeout(() => {
          if (overlay && overlay.classList.contains('active')) {
            closeCountdown();
          }
        }, 3500);
      }
    }, 800);
  }

  // ---- Trigger Native Browser Download without New-Tab Popup or Drive Warning ----
  function triggerDownload(url, quality, title) {
    if (!url || url === '#') return;
    const directUrl = toDirectDownloadUrl(url, quality, title);
    const cleanTitle = String(title || 'Movie').replace(/[<>:"/\\|?*\x00-\x1F]/g, '').trim() || 'Movie';
    const cleanQ = String(quality || '1080p').replace(/[^a-zA-Z0-9]/g, '') || '1080p';
    const fileName = `${cleanTitle} [${cleanQ}] - FilmSub.mp4`;

    // For same-origin /api/download endpoints, trigger an anchor with download attribute
    // and fallback iframe so mobile & desktop browsers start saving immediately.
    const a = document.createElement('a');
    a.href = directUrl;
    a.setAttribute('download', fileName);
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      if (a.parentNode) a.parentNode.removeChild(a);
    }, 1000);
  }

  // ---- Close ----
  function closeCountdown() {
    clearInterval(countdownTimer);
    if (overlay) overlay.classList.remove('active');
    document.body.classList.remove('no-scroll');
    pendingURL = '';
  }

  // ---- Expose ----
  window.FilmSubDownload = {
    show,
    close: closeCountdown,
    toDirectDownloadUrl,
  };
})();
