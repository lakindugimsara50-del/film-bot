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
     2. SECTION SCROLL FADE-IN (Intersection Observer)
     ============================================= */
  function initSectionAnimations() {
    const sections = document.querySelectorAll('.section');
    if (!sections.length) return;

    // Skip if user prefers reduced motion
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      sections.forEach(s => s.classList.add('in-view'));
      return;
    }

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view');
          observer.unobserve(entry.target);
        }
      });
    }, {
      rootMargin: '0px 0px -60px 0px',
      threshold: 0.08
    });

    sections.forEach(s => observer.observe(s));
  }

  /* =============================================
     3. MOVIE CARD SCROLL ENTRANCE ANIMATION
     ============================================= */
  function initCardEntranceAnimation() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('visible');
          observer.unobserve(entry.target);
        }
      });
    }, {
      rootMargin: '0px 0px -20px 0px',
      threshold: 0.05
    });

    // Observe existing cards
    document.querySelectorAll('.movie-card').forEach(card => observer.observe(card));

    // Re-observe dynamically added cards
    const mutObs = new MutationObserver((mutations) => {
      mutations.forEach(mutation => {
        mutation.addedNodes.forEach(node => {
          if (node.nodeType === 1) {
            if (node.classList && node.classList.contains('movie-card')) {
              observer.observe(node);
            }
            node.querySelectorAll && node.querySelectorAll('.movie-card').forEach(c => observer.observe(c));
          }
        });
      });
    });
    mutObs.observe(document.body, { childList: true, subtree: true });
  }

  /* =============================================
     4. HERO – AUTO-ROTATE BILLBOARD & DOTS
     ============================================= */
  function initHeroBillboard() {
    const heroSection = document.getElementById('hero-section');
    if (!heroSection) return;

    // Add dots container
    const dotsContainer = document.createElement('div');
    dotsContainer.className = 'hero-dots';
    heroSection.appendChild(dotsContainer);

    // Add scroll hint
    const scrollHint = document.createElement('div');
    scrollHint.className = 'hero-scroll-hint';
    scrollHint.innerHTML = '<i class="fa-solid fa-chevron-down"></i><span>Scroll</span>';
    heroSection.appendChild(scrollHint);

    // Hide scroll hint after first scroll
    window.addEventListener('scroll', () => {
      if (window.scrollY > 50) {
        scrollHint.style.opacity = '0';
      }
    }, { passive: true, once: true });

    // Auto-rotate if multiple featured movies (dots UI)
    let movies = [];
    let currentIdx = 0;
    let rotateTimer = null;

    // Wait for FilmSub data to load then setup dots
    function setupDots() {
      if (!window.FilmSub) return;
      const allM = window.FilmSub.allMovies();
      if (!allM || allM.length < 2) return;

      // Pick top 5 featured/trending for carousel
      const featured = allM.filter(m => m.featured || m.trending).slice(0, 5);
      movies = featured.length >= 2 ? featured : allM.slice(0, 5);

      if (movies.length < 2) return;

      // Render dots
      dotsContainer.innerHTML = movies.map((_, i) =>
        `<div class="hero-dot${i === 0 ? ' active' : ''}" data-idx="${i}"></div>`
      ).join('');

      dotsContainer.querySelectorAll('.hero-dot').forEach(dot => {
        dot.addEventListener('click', () => {
          const idx = parseInt(dot.dataset.idx);
          rotateTo(idx);
        });
      });

      // Auto rotate every 8 seconds
      rotateTimer = setInterval(() => {
        rotateTo((currentIdx + 1) % movies.length);
      }, 8000);

      // Pause on hover
      heroSection.addEventListener('mouseenter', () => clearInterval(rotateTimer));
      heroSection.addEventListener('mouseleave', () => {
        rotateTimer = setInterval(() => rotateTo((currentIdx + 1) % movies.length), 8000);
      });
    }

    function rotateTo(idx) {
      currentIdx = idx;
      dotsContainer.querySelectorAll('.hero-dot').forEach((d, i) => {
        d.classList.toggle('active', i === idx);
      });

      const movie = movies[idx];
      if (!movie) return;

      if (window.FilmSub && typeof window.FilmSub.renderHero === 'function') {
        window.FilmSub.renderHero(movie);
      }
    }

    // Delay setup to allow data to load
    setTimeout(setupDots, 600);
  }

  /* =============================================
     5. CARD RIBBONS (NEW / HOT BADGES)
     ============================================= */
  function injectCardRibbons() {
    // Run once on load and again on DOM mutations
    function processCards() {
      document.querySelectorAll('.movie-card:not([data-ribbon-done])').forEach(card => {
        card.setAttribute('data-ribbon-done', '1');

        const slug = card.dataset.slug || '';
        const allM = window.FilmSub ? window.FilmSub.allMovies() : [];
        const movie = allM.find(m => (m.slug || m.id) === slug);
        if (!movie) return;

        const poster = card.querySelector('.card-poster');
        if (!poster) return;

        // "NEW" ribbon: if added within last 14 days
        const addedAt = movie.added_at || movie.added_date;
        if (addedAt) {
          const added = new Date(addedAt);
          const daysDiff = (Date.now() - added.getTime()) / (1000 * 60 * 60 * 24);
          if (daysDiff <= 14 && !poster.querySelector('.card-ribbon-new')) {
            const ribbon = document.createElement('div');
            ribbon.className = 'card-ribbon-new';
            ribbon.innerHTML = '<span>NEW</span>';
            poster.appendChild(ribbon);
          }
        }

        // "HOT" badge for trending cards
        if ((movie.trending || movie.hot) && card.classList.contains('trending-card')) {
          if (!poster.querySelector('.card-hot-badge')) {
            const hotBadge = document.createElement('div');
            hotBadge.className = 'card-hot-badge';
            hotBadge.innerHTML = '<i class="fa-solid fa-fire"></i> HOT';
            poster.appendChild(hotBadge);
          }
        }

        // Sinhala subtitle highlight stripe on hover
        const hasSub = (Array.isArray(movie.subtitles) && movie.subtitles.length > 0) || movie.subtitle_url || movie.has_sinhala_sub;
        if (hasSub && !poster.querySelector('.card-sub-highlight')) {
          const subStripe = document.createElement('div');
          subStripe.className = 'card-sub-highlight';
          subStripe.innerHTML = '<i class="fa-solid fa-closed-captioning"></i> සිංහල';
          poster.appendChild(subStripe);
        }
      });
    }

    // Wait for FilmSub data
    const checkReady = () => {
      if (window.FilmSub && window.FilmSub.allMovies().length > 0) {
        processCards();
        // Watch for new cards
        new MutationObserver(() => processCards())
          .observe(document.body, { childList: true, subtree: true });
      } else {
        setTimeout(checkReady, 400);
      }
    };
    checkReady();
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
     8. ENHANCED HIGHLIGHT POPUP – ADD MATCH SCORE
     ============================================= */
  function upgradeHighlightPopup() {
    // Intercept popup creation to add extra elements
    const originalInit = window._originalHighlightPopupInit;

    // Observe popup for content changes
    const popup = document.getElementById('cs-highlight-popup');
    if (!popup) {
      setTimeout(upgradeHighlightPopup, 500);
      return;
    }

    const mutObs = new MutationObserver(() => {
      if (!popup.classList.contains('active')) return;

      // Add match score if not already added
      const body = popup.querySelector('.cs-popup-body');
      if (!body || body.querySelector('.cs-popup-match')) return;

      const movie = getCurrentPopupMovie();
      if (!movie) return;

      // Calculate a "match score" based on IMDB
      const imdb = parseFloat(movie.imdb || movie.rating || '0');
      const matchPct = imdb > 0 ? Math.min(99, Math.round(imdb * 10 + 5)) : 0;

      if (matchPct > 0) {
        const matchEl = document.createElement('div');
        matchEl.className = 'cs-popup-match';
        matchEl.innerHTML = `<i class="fa-solid fa-thumbs-up"></i>${matchPct}% Match`;

        const metaEl = body.querySelector('.cs-popup-meta');
        if (metaEl) metaEl.prepend(matchEl);

        // Add match bar
        const barEl = document.createElement('div');
        barEl.className = 'cs-popup-match-bar';
        barEl.innerHTML = `<div class="cs-popup-match-bar-fill" style="width:${matchPct}%"></div>`;
        if (metaEl) metaEl.after(barEl);
      }

      // Add play overlay on banner
      const banner = popup.querySelector('.cs-popup-banner');
      if (banner && !banner.querySelector('.cs-popup-play-overlay')) {
        const overlay = document.createElement('div');
        overlay.className = 'cs-popup-play-overlay';
        overlay.innerHTML = '<div class="cs-popup-play-icon"><i class="fa-solid fa-play"></i></div>';
        banner.appendChild(overlay);

        // Click overlay navigates to movie
        const watchBtn = popup.querySelector('.cs-popup-btn-watch');
        overlay.addEventListener('click', () => { if (watchBtn) watchBtn.click(); });
      }

      // Add view count (simulated from localStorage)
      const actionsEl = body.querySelector('.cs-popup-actions');
      if (actionsEl && movie && !body.querySelector('.cs-popup-view-count')) {
        const slug = movie.slug || movie.id || '';
        const views = parseInt(localStorage.getItem(`view_${slug}`) || '0', 10);
        if (views > 0) {
          const viewEl = document.createElement('div');
          viewEl.className = 'cs-popup-view-count';
          viewEl.innerHTML = `<i class="fa-solid fa-eye"></i>${views.toLocaleString()} views`;
          actionsEl.before(viewEl);
        }
      }
    });

    mutObs.observe(popup, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
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
    function setup() {
      const heroMeta = document.getElementById('hero-meta');
      if (!heroMeta || heroMeta.querySelector('.hero-maturity')) return;

      const maturity = document.createElement('span');
      maturity.className = 'hero-maturity';
      maturity.textContent = '15+';
      heroMeta.appendChild(maturity);
    }
    setTimeout(setup, 700);
  }

  /* =============================================
     14. SMOOTH IMAGE LOAD (blur-up effect)
     ============================================= */
  function initImageBlurUp() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    document.querySelectorAll('.card-poster img, .hero-poster-mini').forEach(img => {
      if (img.complete) return;
      img.style.filter = 'blur(8px)';
      img.style.transition = 'filter 0.4s ease';
      img.addEventListener('load', () => {
        img.style.filter = 'blur(0)';
      }, { once: true });
    });

    // Also handle dynamically added images
    new MutationObserver(mutations => {
      mutations.forEach(mutation => {
        mutation.addedNodes.forEach(node => {
          if (node.nodeType !== 1) return;
          const imgs = node.tagName === 'IMG' ? [node] : [...(node.querySelectorAll?.('.card-poster img') || [])];
          imgs.forEach(img => {
            if (img.complete) return;
            img.style.filter = 'blur(8px)';
            img.style.transition = 'filter 0.4s ease';
            img.addEventListener('load', () => { img.style.filter = 'blur(0)'; }, { once: true });
          });
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
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
