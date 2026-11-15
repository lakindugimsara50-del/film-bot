/* ============================================================
   FilmSub – Netflix UI Advanced JS v3.0
   Scroll animations · Card ribbons · Progress bar · Popup upgrade
   ============================================================ */

'use strict';

(function () {

  /* =============================================
     1. SCROLL PROGRESS BAR
     ============================================= */
  function initScrollProgressBar() {
    const bar = document.createElement('div');
    bar.id = 'scroll-progress-bar';
    document.body.prepend(bar);

    window.addEventListener('scroll', () => {
      const scrollTop = window.scrollY;
      const docHeight = document.documentElement.scrollHeight - window.innerHeight;
      const pct = docHeight > 0 ? Math.round((scrollTop / docHeight) * 100) : 0;
      bar.style.width = pct + '%';
    }, { passive: true });
  }

  /* =============================================
     2. SECTION SCROLL FADE-IN (Instant Zero-Lag)
     ============================================= */
  function initSectionAnimations() {
    const sections = document.querySelectorAll('.section');
    sections.forEach(s => s.classList.add('in-view'));
  }

  /* =============================================
     3. MOVIE CARD SCROLL ENTRANCE (Instant Zero-Lag)
     ============================================= */
  function initCardEntranceAnimation() {
    document.querySelectorAll('.movie-card').forEach(card => card.classList.add('visible'));
  }

  /* =============================================
     4. HERO – AUTO-ROTATE BILLBOARD & DOTS
     ============================================= */
  function initHeroBillboard() {
    // Hero billboard indicators handled natively by app.js (Apple TV+ clean layout)
    return;
  }

  /* =============================================
     5. CARD RIBBONS (NEW / HOT BADGES)
     ============================================= */
  function injectCardRibbons() {
    // Badges natively generated in app.js inside .card-badges-left
    return;
  }

  /* =============================================
     6. SECTION COUNT BADGES
     ============================================= */
  function addSectionCountBadges() {
    function updateCounts() {
      if (!window.FilmSub) return;
      const allM = window.FilmSub.allMovies();

      document.querySelectorAll('.section-header').forEach(header => {
        if (header.querySelector('.section-count-badge')) return;
        const track = header.parentElement && header.parentElement.querySelector('.carousel-track');
        if (!track) return;
        const count = track.querySelectorAll('.movie-card').length;
        if (count > 0) {
          const badge = document.createElement('span');
          badge.className = 'section-count-badge';
          badge.textContent = count;
          const titleEl = header.querySelector('.section-title');
          if (titleEl) titleEl.appendChild(badge);
        }
      });
    }

    setTimeout(updateCounts, 800);
  }

  /* =============================================
     7. CAROUSEL EDGE FADES (scroll state)
     ============================================= */
  function initCarouselEdgeFades() {
    document.querySelectorAll('.carousel-wrap').forEach(wrap => {
      const track = wrap.querySelector('.carousel-track');
      if (!track) return;

      function update() {
        wrap.classList.toggle('scrolled-left', track.scrollLeft > 10);
        const atEnd = track.scrollLeft + track.clientWidth >= track.scrollWidth - 10;
        wrap.classList.toggle('scrollable-right', !atEnd && track.scrollWidth > track.clientWidth + 10);
      }

      track.addEventListener('scroll', update, { passive: true });
      update();
      // Re-check after cards load
      setTimeout(update, 1000);
    });
  }

  /* =============================================
     8. ENHANCED HIGHLIGHT POPUP (Clean Netflix Style)
     ============================================= */
  function upgradeHighlightPopup() {
    // Popup creation and hover-intent handled natively in app.js
    return;
  }

  function getCurrentPopupMovie() {
    const popup = document.getElementById('cs-highlight-popup');
    if (!popup) return null;
    const titleEl = popup.querySelector('.cs-popup-title');
    if (!titleEl) return null;
    const title = titleEl.textContent.trim();
    if (!window.FilmSub) return null;
    return window.FilmSub.allMovies().find(m => m.title === title) || null;
  }

  /* =============================================
     9. BUTTON RIPPLE EFFECT
     ============================================= */
  function initButtonRipple() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn, .cs-popup-btn-watch, .cs-popup-btn-dl');
      if (!btn) return;
      const rect = btn.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width * 100).toFixed(1) + '%';
      const my = ((e.clientY - rect.top) / rect.height * 100).toFixed(1) + '%';
      btn.style.setProperty('--mx', mx);
      btn.style.setProperty('--my', my);
    });
  }

  /* =============================================
     10. KEYBOARD SHORTCUT HIGHLIGHTS
     ============================================= */
  function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      // Already handled: Ctrl+K for search (in app.js)
      // Add: Escape closes mobile menu too
      if (e.key === 'Escape') {
        const mobileNav = document.querySelector('.mobile-nav.open');
        if (mobileNav) {
          mobileNav.classList.remove('open');
          const hamburger = document.querySelector('.hamburger.open');
          if (hamburger) hamburger.classList.remove('open');
          document.body.classList.remove('no-scroll');
        }
      }

      // Arrow keys for carousel navigation (when focused)
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
        const focused = document.activeElement;
        const track = focused && focused.closest('.carousel-track');
        if (track) {
          e.preventDefault();
          track.scrollBy({ left: e.key === 'ArrowRight' ? 200 : -200, behavior: 'smooth' });
        }
      }
    });
  }

  /* =============================================
     11. LIVE-TIME DISPLAY (if hero has it)
     ============================================= */
  function initCurrentTime() {
    const timeEl = document.getElementById('hero-live-time');
    if (!timeEl) return;
    const update = () => {
      const now = new Date();
      timeEl.textContent = now.toLocaleTimeString('si-LK', { hour: '2-digit', minute: '2-digit' });
    };
    update();
    setInterval(update, 60000);
  }

  /* =============================================
     12. MOBILE BOTTOM BAR – ACTIVE STATE
     ============================================= */
  function initMobileBottomBarActive() {
    const bar = document.querySelector('.mobile-bottom-bar');
    if (!bar) return;

    const currentPath = window.location.pathname.split('/').pop() || 'index.html';
    bar.querySelectorAll('a').forEach(link => {
      const href = link.getAttribute('href') || '';
      const hrefFile = href.split('/').pop().split('?')[0];
      if (hrefFile === currentPath || (currentPath === '' && hrefFile === 'index.html')) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });
  }

  /* =============================================
     13. HERO MATURITY RATING BADGE
     ============================================= */
  function addHeroMaturityRating() {
    return;
  }

  /* =============================================
     14. SMOOTH IMAGE LOAD (Disabled for Zero-Lag Low-Data Performance)
     ============================================= */
  function initImageBlurUp() {
    // Disabled to prevent blurry images and eliminate heavy MutationObserver on document.body
    return;
  }

  /* =============================================
     15. INIT – RUN EVERYTHING
     ============================================= */
  function initAllAdvancedUI() {
    initScrollProgressBar();
    initSectionAnimations();
    initCardEntranceAnimation();
    initHeroBillboard();
    initCarouselEdgeFades();
    injectCardRibbons();
    addSectionCountBadges();
    upgradeHighlightPopup();
    initButtonRipple();
    initKeyboardShortcuts();
    initCurrentTime();
    initMobileBottomBarActive();
    addHeroMaturityRating();
    initImageBlurUp();
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAllAdvancedUI);
  } else {
    initAllAdvancedUI();
  }

  // Expose for debugging
  window.FilmSubUI = {
    injectCardRibbons,
    addSectionCountBadges,
    initCarouselEdgeFades,
  };

})();
