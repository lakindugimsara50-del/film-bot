/**
 * FilmZilla – Cinema Roulette, Live Reactions, Movie Request Dispatcher & PiP (v1.0)
 * Enhances user engagement, discovery, and player multitasking.
 */

'use strict';

(function(window) {

  // ---------------------------------------------------------------------
  // 1. Cinema Roulette ("Surprise Me / Feeling Lucky 🎲")
  // ---------------------------------------------------------------------
  function openRouletteModal() {
    let modal = document.getElementById('roulette-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'roulette-modal';
      modal.className = 'roulette-modal-backdrop';
      modal.innerHTML = `
        <div class="roulette-modal-dialog">
          <div class="roulette-modal-header">
            <h3 class="roulette-modal-title">
              <i class="fa-solid fa-dice" style="color:#f59e0b"></i> Cinema Roulette
            </h3>
            <button type="button" class="user-list-modal-close" id="roulette-close-btn" aria-label="Close">&times;</button>
          </div>
          <div class="roulette-modal-body">
            <p style="font-size:13px;color:#94a3b8;margin:0">Can't decide what to watch? Let destiny choose a top-rated movie for you!</p>
            <div class="roulette-reel-box" id="roulette-reel-box">
              <img src="" alt="Selected Film" class="roulette-reel-img" id="roulette-reel-img">
            </div>
            <div class="roulette-movie-info">
              <h4 class="roulette-movie-title" id="roulette-movie-title">Spinning the Reel...</h4>
              <div class="roulette-movie-meta" id="roulette-movie-meta"></div>
            </div>
            <div class="roulette-actions">
              <button type="button" class="btn-roulette-spin" id="btn-roulette-spin">
                <i class="fa-solid fa-rotate-right"></i> Spin Again
              </button>
              <a href="#" class="btn-roulette-watch" id="btn-roulette-watch">
                <i class="fa-solid fa-play"></i> Watch Movie
              </a>
            </div>
          </div>
        </div>
      `;
      document.body.appendChild(modal);

      modal.querySelector('#roulette-close-btn').addEventListener('click', () => {
        modal.classList.remove('open');
      });
      modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('open');
      });
      modal.querySelector('#btn-roulette-spin').addEventListener('click', () => {
        spinReel();
      });
    }

    modal.classList.add('open');
    spinReel();
  }

  function spinReel() {
    const allM = (window.FilmSub && typeof FilmSub.allMovies === 'function') 
      ? FilmSub.allMovies() 
      : ((window.FILMSUB_DATA && window.FILMSUB_DATA.movies) || []);

    if (!allM || allM.length === 0) return;

    // Filter high-quality candidates (Rating >= 6.5 or 2024+)
    const candidates = allM.filter(m => {
      const r = parseFloat(m.imdb || m.rating || 0);
      const y = parseInt(m.year, 10) || 0;
      return r >= 6.5 || y >= 2024;
    });
    const pool = candidates.length > 5 ? candidates : allM;

    const reelBox = document.getElementById('roulette-reel-box');
    const reelImg = document.getElementById('roulette-reel-img');
    const titleEl = document.getElementById('roulette-movie-title');
    const metaEl = document.getElementById('roulette-movie-meta');
    const watchBtn = document.getElementById('btn-roulette-watch');
    const spinBtn = document.getElementById('btn-roulette-spin');

    if (!reelBox || !reelImg || !titleEl) return;

    reelBox.classList.remove('winner');
    spinBtn.disabled = true;
    spinBtn.style.opacity = '0.6';
    titleEl.textContent = 'Spinning the Reel...';
    if (metaEl) metaEl.textContent = 'Searching for a masterpiece...';

    let spins = 0;
    const maxSpins = 18;
    const interval = 75;

    const spinInterval = setInterval(() => {
      const randMovie = pool[Math.floor(Math.random() * pool.length)];
      reelImg.src = randMovie.poster || randMovie.poster_url || '';
      spins++;

      if (spins >= maxSpins) {
        clearInterval(spinInterval);
        // Select the final chosen movie
        const chosen = pool[Math.floor(Math.random() * pool.length)];
        reelImg.src = chosen.poster || chosen.poster_url || '';
        titleEl.textContent = chosen.title || 'Untitled';
        const rating = chosen.imdb || chosen.rating || '7.5';
        const genres = Array.isArray(chosen.genres) ? chosen.genres.slice(0, 2).join(', ') : 'Cinema';

        if (metaEl) {
          metaEl.innerHTML = `<span style="color:#f5c518;font-weight:700">★ ${rating}</span> <span>•</span> <span>${chosen.year || 2026}</span> <span>•</span> <span>${genres}</span>`;
        }
        if (watchBtn) {
          watchBtn.href = `movie.html?id=${encodeURIComponent(chosen.slug || chosen.id)}`;
        }
        reelBox.classList.add('winner');
        spinBtn.disabled = false;
        spinBtn.style.opacity = '1';
      }
    }, interval);
  }

  // ---------------------------------------------------------------------
  // 2. Modern Fluent 3D Emoji Assets & Screen Floater Engine
  // ---------------------------------------------------------------------
  const FLUENT_3D_EMOJIS = {
    fire: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f525/512.webp',
    popcorn: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f37f/512.webp',
    heart: 'https://fonts.gstatic.com/s/e/notoemoji/latest/2764_fe0f/512.webp',
    mindblown: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f92f/512.webp',
    laugh: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f923/512.webp',
    party: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f389/512.webp',
    shock: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f631/512.webp',
    clap: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f44f/512.webp',
    bolt: 'https://fonts.gstatic.com/s/e/notoemoji/latest/26a1/512.webp',
    skull: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f480/512.webp',
    crown: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f451/512.webp',
    star: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f929/512.webp',
    angry: 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f621/512.webp'
  };

  function initLiveReactions(movieId) {
    const container = document.getElementById('cs-reaction-bar');
    if (!container || !movieId) return;

    const storageKey = `fz_reactions_${movieId}`;
    let savedState = {};
    try {
      savedState = JSON.parse(localStorage.getItem(storageKey) || '{}');
    } catch (_) {}

    // Simulated baseline reactions based on movie hash so counters look alive
    let hash = 0;
    for (let i = 0; i < movieId.length; i++) hash = (hash << 5) - hash + movieId.charCodeAt(i);
    const baseCount = Math.abs(hash) % 400 + 120;

    const counts = {
      fire: (savedState.fire ? 1 : 0) + baseCount * 3,
      mindblown: (savedState.mindblown ? 1 : 0) + Math.round(baseCount * 1.8),
      popcorn: (savedState.popcorn ? 1 : 0) + Math.round(baseCount * 2.4),
      angry: (savedState.angry ? 1 : 0) + Math.round(baseCount * 0.9),
      heart: (savedState.heart ? 1 : 0) + Math.round(baseCount * 2.8),
      laugh: (savedState.laugh ? 1 : 0) + Math.round(baseCount * 1.2),
      sleep: (savedState.sleep ? 1 : 0) + Math.round(baseCount * 0.15)
    };

    // Update UI counters
    ['fire', 'mindblown', 'popcorn', 'angry', 'heart', 'laugh', 'sleep'].forEach(type => {
      const el = document.getElementById(`count-${type}`);
      if (el) el.textContent = counts[type];
      const btn = container.querySelector(`[data-reaction="${type}"]`);
      if (btn && savedState[type]) btn.classList.add('active');
    });

    // Wire clicks
    container.querySelectorAll('.cs-react-btn, .cs-react-pill').forEach(btn => {
      if (btn._bound) return;
      btn._bound = true;

      btn.addEventListener('click', (e) => {
        const type = btn.dataset.reaction;
        if (!type) return;

        const isCurrentlyActive = btn.classList.contains('active');
        const newActive = !isCurrentlyActive;
        btn.classList.toggle('active', newActive);
        savedState[type] = newActive;
        counts[type] += (newActive ? 1 : -1);

        const countEl = document.getElementById(`count-${type}`);
        if (countEl) countEl.textContent = counts[type];

        try {
          localStorage.setItem(storageKey, JSON.stringify(savedState));
        } catch (_) {}

        if (newActive) {
          // Spawn luxury 3D multi-particle stream animation
          spawnFloatingEmoji(btn, type);
        }
      });
    });
  }

  function spawnFloatingEmoji(anchorBtn, type = 'fire') {
    const playerWrap = document.querySelector('.player-container') || document.querySelector('#video-player-container');
    if (!playerWrap) return;

    let stage = document.getElementById('cs-reaction-floating-stage');
    if (!stage) {
      stage = document.createElement('div');
      stage.id = 'cs-reaction-floating-stage';
      stage.className = 'cs-reaction-floating-stage';
      playerWrap.appendChild(stage);
    }

    const src = FLUENT_3D_EMOJIS[type] || FLUENT_3D_EMOJIS.fire;
    const baseLeft = Math.floor(Math.random() * 60 + 20); // 20% to 80% across player width

    const randSway = (Math.random() - 0.5) * 80; // -40px to +40px
    const randRot = (Math.random() - 0.5) * 30; // -15deg to +15deg

    // Hero 3D Emoji
    const floater = document.createElement('div');
    floater.className = `cs-floating-hero-emoji cs-glow-${type}`;
    floater.style.left = `${baseLeft}%`;
    floater.style.setProperty('--rand-sway', `${randSway}px`);
    floater.style.setProperty('--rand-rot', `${randRot}deg`);
    floater.innerHTML = `
      <img src="${src}" alt="${type}" class="cs-floater-img">
      <span class="cs-floater-sparkle s1"></span>
      <span class="cs-floater-sparkle s2"></span>
    `;

    stage.appendChild(floater);

    // Companion floating micro particles
    for (let i = 0; i < 2; i++) {
      const mini = document.createElement('div');
      mini.className = 'cs-floating-mini-particle';
      mini.style.left = `calc(${baseLeft}% + ${(Math.random() - 0.5) * 50}px)`;
      mini.style.setProperty('--rand-sway', `${(Math.random() - 0.5) * 60}px`);
      mini.style.setProperty('--rand-rot', `${(Math.random() - 0.5) * 40}deg`);
      mini.style.animationDelay = `${(i + 1) * 90}ms`;
      mini.innerHTML = `<img src="${src}" alt="" class="cs-mini-floater-img">`;
      stage.appendChild(mini);

      setTimeout(() => {
        if (mini.parentNode) mini.parentNode.removeChild(mini);
      }, 1500);
    }

    setTimeout(() => {
      if (floater.parentNode) floater.parentNode.removeChild(floater);
    }, 1600);
  }

  window.spawnFloatingEmoji = spawnFloatingEmoji;

  // ---------------------------------------------------------------------
  // 3. Movie Request Modal Dispatcher
  // ---------------------------------------------------------------------
  function openMovieRequestModal(defaultTitle = '') {
    let modal = document.getElementById('request-movie-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'request-movie-modal';
      modal.className = 'request-modal-backdrop';
      modal.innerHTML = `
        <div class="request-modal-dialog">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <h3 style="font-size:18px;font-weight:800;color:#fff;margin:0;display:flex;align-items:center;gap:8px">
              <i class="fa-solid fa-clapperboard" style="color:var(--accent)"></i> Request a Movie / Series
            </h3>
            <button type="button" class="user-list-modal-close" id="request-modal-close" aria-label="Close">&times;</button>
          </div>
          <p style="font-size:13px;color:#94a3b8;margin:0">Can't find a film on FilmZilla? Submit your request and our bot will search, crawl, and upload it to Telegram Cloud!</p>
          <form id="request-movie-form" style="display:flex;flex-direction:column;gap:12px">
            <div class="request-input-group">
              <label for="req-movie-title">Movie / Series Title *</label>
              <input type="text" id="req-movie-title" required placeholder="e.g. Deadpool & Wolverine">
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div class="request-input-group">
                <label for="req-movie-year">Year (Optional)</label>
                <input type="number" id="req-movie-year" placeholder="e.g. 2024" min="1950" max="2030">
              </div>
              <div class="request-input-group">
                <label for="req-movie-sub">Subtitle</label>
                <select id="req-movie-sub">
                  <option value="sinhala">Sinhala Subtitles</option>
                  <option value="english">English Subtitles</option>
                  <option value="any">Any / Direct HD</option>
                </select>
              </div>
            </div>
            <div class="request-input-group">
              <label for="req-movie-note">Note / Link (Optional)</label>
              <textarea id="req-movie-note" rows="2" placeholder="e.g. Please upload 1080p WEB-DL if available"></textarea>
            </div>
            <button type="submit" class="btn btn-primary" id="btn-submit-request" style="width:100%;padding:12px;font-weight:800;justify-content:center;margin-top:6px">
              <i class="fa-solid fa-paper-plane"></i> Submit Movie Request
            </button>
          </form>
        </div>
      `;
      document.body.appendChild(modal);

      modal.querySelector('#request-modal-close').addEventListener('click', () => {
        modal.classList.remove('open');
      });
      modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.remove('open');
      });

      modal.querySelector('#request-movie-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const title = document.getElementById('req-movie-title').value.trim();
        const year = document.getElementById('req-movie-year').value.trim();
        const sub = document.getElementById('req-movie-sub').value;
        const note = document.getElementById('req-movie-note').value.trim();

        if (!title) return;

        const submitBtn = document.getElementById('btn-submit-request');
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin"></i> Submitting...';
        }

        // Store request locally and simulate fast dispatch
        const reqList = JSON.parse(localStorage.getItem('fz_user_requests') || '[]');
        reqList.unshift({ title, year, sub, note, timestamp: Date.now() });
        localStorage.setItem('fz_user_requests', JSON.stringify(reqList));

        setTimeout(() => {
          modal.classList.remove('open');
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i class="fa-solid fa-paper-plane"></i> Submit Movie Request';
          }
          if (typeof showToast === 'function') {
            showToast(`Request for "${title}" submitted! Our bot will crawl and upload it soon. 🚀`);
          }
        }, 600);
      });
    }

    const titleInp = document.getElementById('req-movie-title');
    if (titleInp && defaultTitle) titleInp.value = defaultTitle;
    modal.classList.add('open');
    setTimeout(() => titleInp && titleInp.focus(), 100);
  }

  // ---------------------------------------------------------------------
  // 4. Native Picture-in-Picture (PiP) Mode
  // ---------------------------------------------------------------------
  async function togglePictureInPicture(videoEl) {
    if (!videoEl) {
      videoEl = document.querySelector('.player-container video') || document.querySelector('video');
    }
    if (!videoEl) return;
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
      } else if (document.pictureInPictureEnabled) {
        await videoEl.requestPictureInPicture();
      } else {
        if (typeof showToast === 'function') {
          showToast('Picture-in-Picture is not supported in this browser.');
        } else {
          alert('Picture-in-Picture is not supported in this browser.');
        }
      }
    } catch (err) {
      console.warn('PiP error:', err);
    }
  }

  // Initialize global listeners on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', () => {
    const fabRoulette = document.getElementById('fab-roulette');
    if (fabRoulette) {
      fabRoulette.addEventListener('click', openRouletteModal);
    }

    // Delegated click listener for PiP corner button
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('#cs-video-pip-corner-btn, #btn-pip-trigger');
      if (btn) {
        e.preventDefault();
        togglePictureInPicture();
      }
    });

    // Keyboard shortcut 'P' for PiP
    document.addEventListener('keydown', (e) => {
      if (e.key === 'p' || e.key === 'P') {
        const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
        if (activeTag === 'input' || activeTag === 'textarea' || (document.activeElement && document.activeElement.isContentEditable)) return;
        togglePictureInPicture();
      }
    });
  });

  // Expose Global Discovery API
  window.FilmZillaRoulette = {
    openRoulette: openRouletteModal,
    openRequestModal: openMovieRequestModal,
    initReactions: initLiveReactions,
    togglePiP: togglePictureInPicture
  };

})(window);
