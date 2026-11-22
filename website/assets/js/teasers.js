/**
 * FilmZilla Bite-Sized Teasers / Reels Feed Engine
 * TikTok & Instagram Reels Style 30-Second Scene Discovery Experience
 */

(function () {
  'use strict';

  // State
  let allTeasers = [];
  let currentFilter = 'All';
  let activeIndex = 0;
  let isGlobalMuted = true;
  let observer = null;
  let currentSeekMovie = null;

  // DOM Elements
  let feedContainer;
  let soundToggleBtn;
  let categoryRail;
  let smartSeekModal;
  let infoSheetBackdrop;
  let infoSheet;

  // Audio preference storage key
  const MUTE_KEY = 'filmzilla_reels_muted_pref';
  const LIKES_KEY = 'filmzilla_reels_likes';

  function init() {
    feedContainer = document.getElementById('reels-feed-container');
    soundToggleBtn = document.getElementById('global-sound-toggle');
    categoryRail = document.getElementById('reels-category-rail');
    smartSeekModal = document.getElementById('smart-seek-modal');
    infoSheetBackdrop = document.getElementById('reels-info-sheet-backdrop');
    infoSheet = document.getElementById('reels-info-sheet');

    if (!feedContainer) return;

    // Load sound preference
    const savedMute = localStorage.getItem(MUTE_KEY);
    if (savedMute !== null) {
      isGlobalMuted = savedMute === 'true';
    }
    updateSoundButtonUI();

    // Load Teasers Data
    if (window.FILMSUB_TEASERS && Array.isArray(window.FILMSUB_TEASERS) && window.FILMSUB_TEASERS.length > 0) {
      allTeasers = window.FILMSUB_TEASERS;
      setupTeasers();
    } else {
      fetch('data/teasers.json')
        .then(res => res.json())
        .then(data => {
          allTeasers = data.teasers || [];
          setupTeasers();
        })
        .catch(err => {
          console.warn('Could not load teasers.json, fallback to catalog:', err);
          fallbackFromCatalog();
        });
    }

    bindGlobalEvents();
  }

  function fallbackFromCatalog() {
    if (window.FILMSUB_DATA && window.FILMSUB_DATA.movies) {
      allTeasers = window.FILMSUB_DATA.movies
        .filter(m => m.trailer_id || m.backdrop)
        .map((m, idx) => ({
          id: `teaser-${m.id || m.slug}`,
          movie_id: m.id || m.slug,
          title: m.title,
          title_si: m.title_si || '',
          year: m.year || 2026,
          imdb: m.imdb || m.rating || '7.5',
          rating: m.rating || m.imdb || '7.5',
          quality: m.quality || '1080p',
          genres: m.genres || ['Action'],
          poster: m.poster || m.poster_url || '',
          backdrop: m.backdrop || m.backdrop_url || '',
          trailer_id: m.trailer_id || '',
          clip_start: 30,
          clip_end: 60,
          duration: 30,
          intensity_score: 0.82,
          scene_hook: '🔥 Most Replayed Scene • Quick Discovery',
          scene_hook_si: '🔥 වැඩිම පිරිසක් නැවත නැවත නැරඹූ සුවිශේෂී ජවනිකාව',
          scene_timestamp_movie: 1800,
          formatted_seek_time: '30:00',
          video_url: '',
          youtube_embed_url: m.trailer_id
            ? `https://www.youtube.com/embed/${m.trailer_id}?start=30&end=60&autoplay=1&mute=1&controls=0&loop=1&playlist=${m.trailer_id}&playsinline=1&rel=0&modestbranding=1&enablejsapi=1`
            : '',
          has_sinhala_sub: true,
          likes_count: 150 + idx * 25
        }));
      setupTeasers();
    }
  }

  function setupTeasers() {
    if (!allTeasers || allTeasers.length === 0) {
      feedContainer.innerHTML = `
        <div style="height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;text-align:center;padding:20px;">
          <i class="fa-solid fa-film" style="font-size:48px;color:#e50914;margin-bottom:16px;"></i>
          <h2>No Teasers Available Yet</h2>
          <p style="color:#aaa;">Check back shortly as new 30-second clips are being generated.</p>
          <a href="index.html" class="reels-back-btn" style="margin-top:20px;">Return Home</a>
        </div>
      `;
      return;
    }

    // Check URL query param for requested movie: ?id=movie-slug
    const urlParams = new URLSearchParams(window.location.search);
    const requestedId = urlParams.get('id') || urlParams.get('movie');
    if (requestedId) {
      const matchIdx = allTeasers.findIndex(t => t.movie_id === requestedId || t.id === requestedId || t.id === `teaser-${requestedId}`);
      if (matchIdx > -1) {
        const item = allTeasers.splice(matchIdx, 1)[0];
        allTeasers.unshift(item); // Prioritize requested teaser
      } else if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
        // Dynamic fallback: movie exists in catalog but wasn't in static teasers.json
        const m = window.FILMSUB_DATA.movies.find(x => (x.id || x.slug) === requestedId);
        if (m) {
          const trailerId = m.trailer_id || '';
          const dynamicItem = {
            id: `teaser-${m.id || m.slug}`,
            movie_id: m.id || m.slug,
            title: m.title,
            title_si: m.title_si || '',
            year: m.year || 2026,
            imdb: m.imdb || m.rating || '7.5',
            rating: m.rating || m.imdb || '7.5',
            quality: m.quality || '1080p',
            genres: m.genres || ['Action'],
            poster: m.poster || m.poster_url || '',
            backdrop: m.backdrop || m.backdrop_url || '',
            trailer_id: trailerId,
            clip_start: 30,
            clip_end: 60,
            duration: 30,
            intensity_score: 0.85,
            scene_hook: '🔥 Most Replayed Scene • Quick Discovery',
            scene_hook_si: '🔥 වැඩිම පිරිසක් නැවත නැවත නැරඹූ සුවිශේෂී ජවනිකාව',
            scene_timestamp_movie: 1800,
            formatted_seek_time: '30:00',
            video_url: '',
            youtube_embed_url: trailerId
              ? `https://www.youtube.com/embed/${trailerId}?start=30&end=60&autoplay=1&mute=1&controls=0&loop=1&playlist=${trailerId}&playsinline=1&rel=0&modestbranding=1&enablejsapi=1`
              : '',
            has_sinhala_sub: true,
            likes_count: 240
          };
          allTeasers.unshift(dynamicItem);
        }
      }
    }

    renderFeed();
    setupIntersectionObserver();
  }

  function getLikedSet() {
    try {
      const raw = localStorage.getItem(LIKES_KEY);
      return raw ? new Set(JSON.parse(raw)) : new Set();
    } catch {
      return new Set();
    }
  }

  function toggleLike(teaserId) {
    const set = getLikedSet();
    const isLiked = set.has(teaserId);
    if (isLiked) {
      set.delete(teaserId);
    } else {
      set.add(teaserId);
    }
    localStorage.setItem(LIKES_KEY, JSON.stringify(Array.from(set)));
    return !isLiked;
  }

  function renderFeed() {
    const likedSet = getLikedSet();
    const filtered = allTeasers.filter(t => {
      if (currentFilter === 'All') return true;
      if (currentFilter === 'Sinhala Sub') return t.has_sinhala_sub;
      return t.genres && t.genres.some(g => g.toLowerCase().includes(currentFilter.toLowerCase()));
    });

    if (filtered.length === 0) {
      feedContainer.innerHTML = `
        <div style="height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;text-align:center;padding:20px;">
          <i class="fa-solid fa-filter" style="font-size:40px;color:#ff3366;margin-bottom:14px;"></i>
          <h3>No teasers found for "${currentFilter}"</h3>
          <p style="color:#aaa;font-size:14px;">Try selecting another category or view all teasers.</p>
          <button type="button" class="reels-pill active" onclick="window.FilmZillaReels.setFilter('All')" style="margin-top:10px;">Show All</button>
        </div>
      `;
      return;
    }

    feedContainer.innerHTML = filtered.map((t, idx) => {
      const isLiked = likedSet.has(t.id);
      const isBookmarked = (window.FilmSubUser && typeof window.FilmSubUser.WatchLater?.has === 'function')
        ? window.FilmSubUser.WatchLater.has(t.movie_id)
        : false;
      const isFirst = idx === 0;

      // Video engine: Native MP4 or YouTube embed
      let mediaHtml = '';
      if (t.video_url) {
        mediaHtml = `
          <video class="reels-video" playsinline webkit-playsinline loop preload="${isFirst ? 'auto' : 'metadata'}" ${isGlobalMuted ? 'muted' : ''} poster="${t.backdrop || t.poster}">
            <source src="${t.video_url}" type="video/mp4">
          </video>
        `;
      } else if (t.youtube_embed_url || t.trailer_id) {
        // Embed YouTube with JS API enabled
        const trailerId = t.trailer_id || (t.youtube_embed_url.match(/embed\/([^?&]+)/) || [])[1] || '';
        const clipStart = Math.floor(t.clip_start || 0);
        const clipEnd = Math.floor(t.clip_end || (clipStart + 30));
        const ytMuted = isGlobalMuted ? 1 : 0;
        
        let ytSrc = (t.youtube_embed_url || `https://www.youtube.com/embed/${trailerId}?start=${clipStart}&end=${clipEnd}&autoplay=1&mute=1&controls=0&loop=1&playlist=${trailerId}&playsinline=1&rel=0&modestbranding=1&enablejsapi=1`)
          .replace(/mute=[01]/, `mute=${ytMuted}`)
          .replace('https://www.youtube-nocookie.com/embed/', 'https://www.youtube.com/embed/');
          
        const origin = (window.location.origin && window.location.origin !== 'null') ? window.location.origin : '';
        if (origin && !ytSrc.includes('origin=')) {
          ytSrc += `&origin=${encodeURIComponent(origin)}`;
        }
        mediaHtml = `
          <iframe class="reels-yt-iframe" ${isFirst ? `src="${ytSrc}"` : ''} data-src="${ytSrc}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen></iframe>
        `;
      } else {
        mediaHtml = `
          <img src="${t.backdrop || t.poster}" alt="${t.title}" style="width:100%;height:100%;object-fit:cover;">
        `;
      }

      return `
        <article class="teaser-card" id="${t.id}" data-index="${idx}" data-movie-id="${t.movie_id}" data-seek="${t.scene_timestamp_movie || 120}" data-seek-time="${t.formatted_seek_time || '02:00'}" data-clip-start="${t.clip_start || 0}" data-clip-end="${t.clip_end || 30}" data-paused="${isFirst ? 'false' : 'true'}">
          <!-- Background Stage -->
          <div class="teaser-stage-wrap">
            <div class="teaser-backdrop-blur" style="background-image: url('${t.backdrop || t.poster}')"></div>
            <div class="teaser-media-wrap" data-card-idx="${idx}">
              ${mediaHtml}
            </div>
            <div class="teaser-vignette"></div>
            <div class="teaser-gesture-layer" data-card-idx="${idx}" title="Tap to Play/Pause, Double-tap to Like"></div>
          </div>

          <!-- Play/Pause Tap Feedback -->
          <div class="teaser-tap-ripple" id="ripple-${idx}">
            <i class="fa-solid fa-play"></i>
          </div>

          <!-- Sound Prompt (shown on first card if muted) -->
          ${isFirst && isGlobalMuted ? `
            <div class="teaser-sound-prompt" id="sound-prompt-${idx}" title="Click to enable sound">
              <i class="fa-solid fa-volume-xmark"></i>
              <span>Tap for sound 🔊</span>
            </div>
          ` : ''}

          <!-- Right Action Rail -->
          <div class="teaser-action-rail">
            <!-- Movie Poster Avatar -->
            <div class="action-poster-avatar" title="View details for ${t.title}" onclick="window.FilmZillaReels.openInfoSheet('${t.movie_id}')">
              <img src="${t.poster || t.backdrop}" alt="${t.title}">
            </div>

            <!-- Like Button -->
            <div class="action-rail-item">
              <button type="button" class="action-rail-btn ${isLiked ? 'btn-liked' : ''}" id="like-btn-${t.id}" aria-label="Like" onclick="window.FilmZillaReels.handleLike('${t.id}')">
                <i class="fa-solid fa-heart"></i>
              </button>
              <span class="action-rail-count" id="like-count-${t.id}">${t.likes_count + (isLiked ? 1 : 0)}</span>
            </div>

            <!-- Bookmark / Watch Later -->
            <div class="action-rail-item">
              <button type="button" class="action-rail-btn ${isBookmarked ? 'btn-bookmarked' : ''}" id="bookmark-btn-${t.movie_id}" aria-label="Watch Later" onclick="window.FilmZillaReels.handleBookmark('${t.movie_id}', '${escapeAttr(t.title)}')">
                <i class="fa-solid fa-bookmark"></i>
              </button>
              <span class="action-rail-count">Save</span>
            </div>

            <!-- Share Button -->
            <div class="action-rail-item">
              <button type="button" class="action-rail-btn" aria-label="Share" onclick="window.FilmZillaReels.handleShare('${t.movie_id}', '${escapeAttr(t.title)}')">
                <i class="fa-solid fa-share-nodes"></i>
              </button>
              <span class="action-rail-count">Share</span>
            </div>

            <!-- Info Sheet -->
            <div class="action-rail-item">
              <button type="button" class="action-rail-btn" aria-label="Movie Info" onclick="window.FilmZillaReels.openInfoSheet('${t.movie_id}')">
                <i class="fa-solid fa-circle-info"></i>
              </button>
              <span class="action-rail-count">Info</span>
            </div>
          </div>

          <!-- Bottom Card Content -->
          <div class="teaser-bottom-info">
            <div class="teaser-replay-tag">
              <i class="fa-solid fa-fire"></i>
              <span>YouTube Most Replayed Peak (${t.clip_start || 0}s - ${t.clip_end || 30}s)</span>
            </div>

            <h2 class="teaser-movie-title">
              ${escapeHtml(t.title)} <span class="teaser-year">(${t.year})</span>
            </h2>

            ${t.title_si ? `<div class="teaser-movie-title-si">${escapeHtml(t.title_si)}</div>` : ''}

            <div class="teaser-meta-row">
              <span class="teaser-badge badge-imdb"><i class="fa-solid fa-star"></i> ${t.imdb || '7.5'}</span>
              <span class="teaser-badge">${(t.genres || []).slice(0, 2).join(' • ')}</span>
              ${t.has_sinhala_sub ? `<span class="teaser-badge badge-sub">සිංහල උපසිරැසි</span>` : ''}
              <span class="teaser-badge" style="background:rgba(255,255,255,0.1)">⚡ 30s Cut</span>
            </div>

            <p class="teaser-hook-text">
              <i class="fa-solid fa-quote-left" style="opacity:0.6;margin-right:4px;"></i>
              ${escapeHtml(t.scene_hook_si || t.scene_hook || '🔥 Most Replayed Scene')}
            </p>

            <!-- Prominent CTA Button -->
            <div class="teaser-cta-container">
              <button type="button" class="btn-watch-full-movie" onclick="window.FilmZillaReels.promptWatchModal('${t.movie_id}', '${escapeAttr(t.title)}', '${t.formatted_seek_time}', ${t.scene_timestamp_movie || 120})">
                <div class="cta-left-content">
                  <div class="cta-play-circle"><i class="fa-solid fa-play"></i></div>
                  <div class="cta-text-group">
                    <span class="cta-main-title">Watch Full Movie</span>
                    <span class="cta-sub-title">සම්පූර්ණ චිත්‍රපටය මෙතනින් නරඹන්න</span>
                  </div>
                </div>
                <i class="fa-solid fa-chevron-right cta-arrow-icon"></i>
              </button>
            </div>
          </div>

          <!-- 30-Second Bottom Timeline Progress Bar -->
          <div class="teaser-bottom-progress">
            <div class="teaser-progress-fill" id="progress-${idx}"></div>
          </div>
        </article>
      `;
    }).join('');

    bindCardInteractions();
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function escapeAttr(str) {
    if (!str) return '';
    return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function bindCardInteractions() {
    const cards = feedContainer.querySelectorAll('.teaser-card');
    cards.forEach((card, idx) => {
      const gestureLayer = card.querySelector('.teaser-gesture-layer') || card.querySelector('.teaser-media-wrap');
      const soundPrompt = card.querySelector('.teaser-sound-prompt');

      if (soundPrompt) {
        soundPrompt.addEventListener('click', (e) => {
          e.stopPropagation();
          setGlobalMute(false);
          soundPrompt.classList.add('hidden');
        });
      }

      if (!gestureLayer) return;

      // Tap & Double Tap Logic
      let lastTapTime = 0;
      let tapTimeout = null;

      gestureLayer.addEventListener('click', (e) => {
        // Ignore clicks on buttons/links
        if (e.target.closest('button') || e.target.closest('a')) return;

        const now = Date.now();
        const delta = now - lastTapTime;

        if (delta < 300) {
          // Double Tap: Like!
          if (tapTimeout) clearTimeout(tapTimeout);
          triggerDoubleTapHeart(card, e);
          const teaserId = card.id;
          handleLike(teaserId, true);
        } else {
          // Single Tap: Toggle Pause / Play / Unmute
          tapTimeout = setTimeout(() => {
            if (isGlobalMuted) {
              setGlobalMute(false);
              if (soundPrompt) soundPrompt.classList.add('hidden');
            } else {
              toggleCardPlayback(card, idx);
            }
          }, 320);
        }
        lastTapTime = now;
      });
    });
  }

  function triggerDoubleTapHeart(card, event) {
    const heart = document.createElement('div');
    heart.className = 'teaser-floating-heart';
    heart.innerHTML = '<i class="fa-solid fa-heart"></i>';

    const rect = card.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    heart.style.left = `${x}px`;
    heart.style.top = `${y}px`;

    card.appendChild(heart);
    setTimeout(() => heart.remove(), 850);
  }

  function toggleCardPlayback(card, idx) {
    const video = card.querySelector('video');
    const iframe = card.querySelector('iframe');
    const ripple = document.getElementById(`ripple-${idx}`);
    const isPaused = card.dataset.paused === 'true';

    if (video) {
      if (video.paused) {
        video.play().catch(() => {});
        card.dataset.paused = 'false';
        showRipple(ripple, 'play');
      } else {
        video.pause();
        card.dataset.paused = 'true';
        showRipple(ripple, 'pause');
      }
    } else if (iframe && iframe.contentWindow) {
      if (isPaused) {
        iframe.contentWindow.postMessage('{"event":"command","func":"playVideo","args":""}', '*');
        card.dataset.paused = 'false';
        showRipple(ripple, 'play');
      } else {
        iframe.contentWindow.postMessage('{"event":"command","func":"pauseVideo","args":""}', '*');
        card.dataset.paused = 'true';
        showRipple(ripple, 'pause');
      }
    }
  }

  function showRipple(rippleEl, state) {
    if (!rippleEl) return;
    rippleEl.innerHTML = state === 'play' ? '<i class="fa-solid fa-play"></i>' : '<i class="fa-solid fa-pause"></i>';
    rippleEl.classList.add('active');
    setTimeout(() => {
      rippleEl.classList.remove('active');
    }, 400);
  }

  // IntersectionObserver for vertical scrolling autoplay
  function setupIntersectionObserver() {
    if (observer) observer.disconnect();

    const options = {
      root: feedContainer,
      threshold: 0.65
    };

    observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        const card = entry.target;
        const idx = parseInt(card.dataset.index, 10);

        if (entry.isIntersecting) {
          activeIndex = idx;
          playCard(card, idx);
          preloadAdjacent(idx);
        } else {
          pauseCard(card, idx);
        }
      });
    }, options);

    const cards = feedContainer.querySelectorAll('.teaser-card');
    cards.forEach(card => observer.observe(card));

    // Start playing the first card immediately
    if (cards.length > 0) {
      playCard(cards[0], 0);
    }
  }

  function playCard(card, idx) {
    const video = card.querySelector('video');
    const iframe = card.querySelector('iframe');
    const progressFill = document.getElementById(`progress-${idx}`);
    const clipStart = parseFloat(card.dataset.clipStart || 0);

    card.dataset.paused = 'false';

    // Update cinematic desktop ambient backdrop blur (always matches active card)
    const ambientBg = document.getElementById('reels-ambient-image');
    if (ambientBg) {
      let bgImg = '';
      if (card) {
        const bgBlurEl = card.querySelector('.teaser-backdrop-blur');
        if (bgBlurEl && bgBlurEl.style.backgroundImage) {
          bgImg = bgBlurEl.style.backgroundImage;
        } else if (card.id || (card.dataset && card.dataset.movieId)) {
          const matched = (typeof allTeasers !== 'undefined' && Array.isArray(allTeasers))
            ? allTeasers.find(t => t.id === card.id || t.movie_id === card.dataset.movieId)
            : null;
          if (matched && (matched.backdrop || matched.poster)) {
            bgImg = `url("${matched.backdrop || matched.poster}")`;
          }
        }
      }
      if (!bgImg && typeof allTeasers !== 'undefined' && allTeasers && allTeasers[idx]) {
        const fallback = allTeasers[idx].backdrop || allTeasers[idx].poster;
        if (fallback) bgImg = `url("${fallback}")`;
      }
      if (bgImg) {
        ambientBg.style.backgroundImage = bgImg.startsWith('url(') ? bgImg : `url("${bgImg}")`;
      }
    }

    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }

    if (video) {
      video.muted = isGlobalMuted;
      video.currentTime = 0;
      video.play().catch(e => console.log('Autoplay policy caught:', e));

      // Progress bar updater
      video.ontimeupdate = () => {
        if (video.duration && progressFill) {
          const pct = (video.currentTime / video.duration) * 100;
          progressFill.style.width = `${pct}%`;
        }
      };
    } else if (iframe) {
      const targetSrc = iframe.dataset.src || '';
      const ytMuted = isGlobalMuted ? 1 : 0;
      const currentSrc = iframe.getAttribute('src');

      if (!currentSrc || currentSrc === 'about:blank' || currentSrc.includes('/teasers')) {
        // First load of this iframe: inject src with current mute state
        const finalSrc = targetSrc.replace(/mute=[01]/, `mute=${ytMuted}`);
        iframe.setAttribute('src', finalSrc);
      } else if (iframe.contentWindow) {
        try {
          const ytMuteCmd = isGlobalMuted
            ? '{"event":"command","func":"mute","args":""}'
            : '{"event":"command","func":"unMute","args":""}';
          iframe.contentWindow.postMessage(ytMuteCmd, '*');
          if (!isGlobalMuted) {
            iframe.contentWindow.postMessage('{"event":"command","func":"setVolume","args":[100]}', '*');
          }
          iframe.contentWindow.postMessage('{"event":"command","func":"playVideo","args":""}', '*');
        } catch (_) {}
      }

      // Animate progress bar across 30 seconds
      animateTimelineProgress(progressFill, 30, clipStart);
    }
  }

  function pauseCard(card, idx) {
    const video = card.querySelector('video');
    const iframe = card.querySelector('iframe');
    const progressFill = document.getElementById(`progress-${idx}`);

    card.dataset.paused = 'true';

    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }

    if (video) {
      video.pause();
    } else if (iframe && iframe.contentWindow) {
      try {
        iframe.contentWindow.postMessage('{"event":"command","func":"pauseVideo","args":""}', '*');
        iframe.contentWindow.postMessage('{"event":"command","func":"mute","args":""}', '*');
      } catch (_) {}
    }
    if (progressFill) {
      progressFill.style.width = '0%';
    }
  }

  let progressInterval = null;
  function animateTimelineProgress(fillEl, durationSecs, clipStart = 0) {
    if (!fillEl) return;
    if (progressInterval) clearInterval(progressInterval);
    const start = Date.now();
    const totalMs = durationSecs * 1000;

    progressInterval = setInterval(() => {
      const elapsed = Date.now() - start;
      const pct = Math.min(100, (elapsed / totalMs) * 100);
      fillEl.style.width = `${pct}%`;
      if (pct >= 100) {
        clearInterval(progressInterval);
        progressInterval = null;

        // Continuous Loop for YouTube 30s clips
        const cards = feedContainer.querySelectorAll('.teaser-card');
        const curCard = cards[activeIndex];
        if (curCard) {
          const iframe = curCard.querySelector('iframe');
          if (iframe && iframe.contentWindow) {
            iframe.contentWindow.postMessage(JSON.stringify({
              event: 'command',
              func: 'seekTo',
              args: [clipStart, true]
            }), '*');
            iframe.contentWindow.postMessage('{"event":"command","func":"playVideo","args":""}', '*');
            animateTimelineProgress(fillEl, durationSecs, clipStart);
          }
        }
      }
    }, 150);
  }

  function preloadAdjacent(currentIdx) {
    const cards = feedContainer.querySelectorAll('.teaser-card');
    const nextIdx = currentIdx + 1;
    if (nextIdx < cards.length) {
      const nextCard = cards[nextIdx];
      const video = nextCard.querySelector('video');
      const iframe = nextCard.querySelector('iframe');
      if (video && video.preload !== 'auto') {
        video.preload = 'auto';
      }
      if (iframe && !iframe.getAttribute('src') && iframe.dataset.src) {
        // Preload next iframe so video buffers in advance, muted
        const nextSrc = iframe.dataset.src.replace('autoplay=1', 'autoplay=0').replace('mute=0', 'mute=1');
        iframe.setAttribute('src', nextSrc);
      }
    }
  }

  function setGlobalMute(mute) {
    isGlobalMuted = mute;
    localStorage.setItem(MUTE_KEY, String(mute));
    updateSoundButtonUI();

    // Apply to current active card
    const cards = feedContainer.querySelectorAll('.teaser-card');
    if (cards[activeIndex]) {
      const curCard = cards[activeIndex];
      const video = curCard.querySelector('video');
      const iframe = curCard.querySelector('iframe');
      if (video) {
        video.muted = isGlobalMuted;
      } else if (iframe && iframe.contentWindow) {
        const cmd = isGlobalMuted
          ? '{"event":"command","func":"mute","args":""}'
          : '{"event":"command","func":"unMute","args":""}';
        iframe.contentWindow.postMessage(cmd, '*');
        if (!isGlobalMuted) {
          iframe.contentWindow.postMessage('{"event":"command","func":"setVolume","args":[100]}', '*');
        }
      }
    }
  }

  function updateSoundButtonUI() {
    if (!soundToggleBtn) return;
    if (isGlobalMuted) {
      soundToggleBtn.innerHTML = '<i class="fa-solid fa-volume-xmark"></i>';
      soundToggleBtn.classList.remove('unmuted');
      soundToggleBtn.title = 'Sound Muted (Click to enable)';
    } else {
      soundToggleBtn.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
      soundToggleBtn.classList.add('unmuted');
      soundToggleBtn.title = 'Sound Active (Click to mute)';
    }
  }

  function handleLike(teaserId, forceLike = false) {
    const btn = document.getElementById(`like-btn-${teaserId}`);
    const countEl = document.getElementById(`like-count-${teaserId}`);
    const set = getLikedSet();

    let isLikedNow;
    if (forceLike) {
      if (!set.has(teaserId)) {
        set.add(teaserId);
        localStorage.setItem(LIKES_KEY, JSON.stringify(Array.from(set)));
      }
      isLikedNow = true;
    } else {
      isLikedNow = toggleLike(teaserId);
    }

    if (btn) {
      if (isLikedNow) {
        btn.classList.add('btn-liked');
      } else {
        btn.classList.remove('btn-liked');
      }
    }

    if (countEl) {
      const base = parseInt(countEl.textContent, 10) || 120;
      countEl.textContent = isLikedNow ? base + 1 : Math.max(0, base - 1);
    }

    showToast(isLikedNow ? '❤️ Added to Liked Scenes!' : 'Removed from Likes');
  }

  function handleBookmark(movieId, movieTitle) {
    const movie = (window.FILMSUB_DATA && window.FILMSUB_DATA.movies)
      ? window.FILMSUB_DATA.movies.find(m => (m.id || m.slug) === movieId)
      : allTeasers.find(t => t.movie_id === movieId);
    const movieObj = movie ? {
      id: movieId,
      slug: movieId,
      title: movie.title || movieTitle,
      title_si: movie.title_si || '',
      poster: movie.poster || movie.backdrop || '',
      backdrop: movie.backdrop || movie.poster || '',
      year: movie.year || 2026,
      rating: movie.imdb || movie.rating || '7.5',
      genres: movie.genres || []
    } : { id: movieId, slug: movieId, title: movieTitle };

    let isAdded = false;
    if (window.FilmSubUser && typeof window.FilmSubUser.WatchLater?.toggle === 'function') {
      isAdded = window.FilmSubUser.WatchLater.toggle(movieObj);
      showToast(isAdded ? `🔖 Saved "${movieTitle}" to Watch Later!` : `Removed "${movieTitle}"`);
    } else {
      showToast(`🔖 Saved "${movieTitle}" to Watch Later!`);
    }
    const btn = document.getElementById(`bookmark-btn-${movieId}`);
    if (btn) btn.classList.toggle('btn-bookmarked', isAdded);
  }

  function handleShare(movieId, movieTitle) {
    const url = `${window.location.origin}${window.location.pathname}?id=${movieId}`;
    if (navigator.share) {
      navigator.share({
        title: `${movieTitle} - 30-Sec Teaser on FilmZilla`,
        text: `Watch this insane 30-second scene teaser from ${movieTitle} on FilmZilla!`,
        url: url
      }).catch(() => {});
    } else {
      navigator.clipboard.writeText(url).then(() => {
        showToast('🔗 Teaser link copied to clipboard!');
      }).catch(() => {
        showToast('Link: ' + url);
      });
    }
  }

  function promptWatchModal(movieId, title, seekTimeStr, seekSecs) {
    currentSeekMovie = {
      movieId,
      title,
      seekTimeStr: seekTimeStr || '02:00',
      seekSecs: seekSecs || 120
    };

    if (!smartSeekModal) {
      // Direct navigate if modal missing
      window.location.href = `movie.html?id=${movieId}&t=${seekSecs}&from_teaser=1`;
      return;
    }

    document.getElementById('smart-seek-title-text').textContent = title;
    document.getElementById('smart-seek-scene-time').textContent = seekTimeStr;

    smartSeekModal.classList.add('open');
  }

  function executeWatch(mode) {
    if (!currentSeekMovie) return;
    const { movieId, seekSecs } = currentSeekMovie;
    if (mode === 'beginning') {
      window.location.href = `movie.html?id=${movieId}&from_teaser=1`;
    } else {
      window.location.href = `movie.html?id=${movieId}&t=${seekSecs}&from_teaser=1`;
    }
  }

  function closeSmartSeekModal() {
    if (smartSeekModal) smartSeekModal.classList.remove('open');
  }

  function openInfoSheet(movieId) {
    if (!infoSheetBackdrop || !infoSheet) return;
    const movie = (window.FILMSUB_DATA && window.FILMSUB_DATA.movies)
      ? window.FILMSUB_DATA.movies.find(m => (m.id || m.slug) === movieId)
      : allTeasers.find(t => t.movie_id === movieId);

    if (!movie) return;

    const teaser = allTeasers.find(t => t.movie_id === movieId || t.id === `teaser-${movieId}`);
    const seekTime = teaser?.formatted_seek_time || '02:00';
    const seekSecs = teaser?.scene_timestamp_movie || 120;

    infoSheet.innerHTML = `
      <div class="info-sheet-handle"></div>
      <div style="display:flex;gap:16px;margin-bottom:16px;">
        <img src="${movie.poster || movie.backdrop}" alt="${movie.title}" style="width:90px;height:130px;object-fit:cover;border-radius:10px;box-shadow:0 4px 15px rgba(0,0,0,0.6);">
        <div style="display:flex;flex-direction:column;gap:6px;">
          <h3 style="margin:0;font-size:18px;color:#fff;">${escapeHtml(movie.title)} (${movie.year || 2026})</h3>
          ${movie.title_si ? `<div style="font-size:13px;color:#f39c12;font-weight:600;">${escapeHtml(movie.title_si)}</div>` : ''}
          <div style="font-size:12px;color:#9ca3af;"><i class="fa-solid fa-star" style="color:#f5c518"></i> IMDb ${movie.imdb || movie.rating || '7.5'} • ${(movie.genres || []).join(', ')}</div>
          <div style="font-size:12px;color:#38bdf8;"><i class="fa-solid fa-closed-captioning"></i> ${movie.subtitle_language || 'Sinhala'} Subtitles Included</div>
        </div>
      </div>
      <p style="font-size:13px;line-height:1.6;color:#d1d5db;margin-bottom:20px;">
        ${escapeHtml(movie.description_si || movie.storyline_si || movie.description || 'Watch full movie in high definition with Sinhala subtitles.')}
      </p>
      <div style="display:flex;gap:12px;">
        <button type="button" class="btn-watch-full-movie" style="flex:1;" onclick="window.FilmZillaReels.promptWatchModal('${movie.id || movie.slug}', '${escapeAttr(movie.title)}', '${seekTime}', ${seekSecs})">
          <div class="cta-left-content">
            <div class="cta-play-circle"><i class="fa-solid fa-play"></i></div>
            <div class="cta-text-group">
              <span class="cta-main-title">Watch Full Movie</span>
              <span class="cta-sub-title">Stream Now HD</span>
            </div>
          </div>
          <i class="fa-solid fa-chevron-right cta-arrow-icon"></i>
        </button>
      </div>
    `;

    infoSheetBackdrop.classList.add('open');
  }

  function closeInfoSheet() {
    if (infoSheetBackdrop) infoSheetBackdrop.classList.remove('open');
  }

  function showToast(msg) {
    let toast = document.getElementById('reels-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'reels-toast';
      toast.style.cssText = `
        position: fixed;
        bottom: 90px;
        left: 50%;
        transform: translateX(-50%);
        background: rgba(20, 20, 24, 0.95);
        color: #fff;
        padding: 10px 22px;
        border-radius: 25px;
        font-size: 13px;
        font-weight: 600;
        border: 1px solid rgba(255, 255, 255, 0.15);
        backdrop-filter: blur(12px);
        box-shadow: 0 10px 30px rgba(0,0,0,0.8);
        z-index: 3000;
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.25s ease, transform 0.25s ease;
      `;
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.style.opacity = '1';
    toast.style.transform = 'translateX(-50%) translateY(0)';
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(10px)';
    }, 2200);
  }

  function bindGlobalEvents() {
    // Sound Button Click
    if (soundToggleBtn) {
      soundToggleBtn.addEventListener('click', () => {
        setGlobalMute(!isGlobalMuted);
      });
    }

    // Keyboard Shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

      if (e.key === 'ArrowDown' || e.key === 'j' || e.key === 'PageDown') {
        e.preventDefault();
        scrollByStep(1);
      } else if (e.key === 'ArrowUp' || e.key === 'k' || e.key === 'PageUp') {
        e.preventDefault();
        scrollByStep(-1);
      } else if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        const cards = feedContainer.querySelectorAll('.teaser-card');
        if (cards[activeIndex]) toggleCardPlayback(cards[activeIndex], activeIndex);
      } else if (e.key === 'm' || e.key === 'M') {
        e.preventDefault();
        setGlobalMute(!isGlobalMuted);
      } else if (e.key === 'l' || e.key === 'L') {
        e.preventDefault();
        const cards = feedContainer.querySelectorAll('.teaser-card');
        if (cards[activeIndex]) handleLike(cards[activeIndex].id);
      } else if (e.key === 'f' || e.key === 'F' || e.key === 'Enter') {
        const cards = feedContainer.querySelectorAll('.teaser-card');
        if (cards[activeIndex]) {
          const btn = cards[activeIndex].querySelector('.btn-watch-full-movie');
          if (btn) btn.click();
        }
      } else if (e.key === 'Escape') {
        closeSmartSeekModal();
        closeInfoSheet();
      }
    });

    // Desktop Side Arrow Buttons
    const prevBtn = document.getElementById('desktop-nav-prev');
    const nextBtn = document.getElementById('desktop-nav-next');
    if (prevBtn) prevBtn.addEventListener('click', () => scrollByStep(-1));
    if (nextBtn) nextBtn.addEventListener('click', () => scrollByStep(1));

    // Category rail pills
    if (categoryRail) {
      categoryRail.addEventListener('click', (e) => {
        const pill = e.target.closest('.reels-pill');
        if (!pill) return;
        applyFilter(pill.dataset.filter || 'All');
      });
    }

    // Modal backdrops
    if (smartSeekModal) {
      smartSeekModal.addEventListener('click', (e) => {
        if (e.target === smartSeekModal) closeSmartSeekModal();
      });
    }
    if (infoSheetBackdrop) {
      infoSheetBackdrop.addEventListener('click', (e) => {
        if (e.target === infoSheetBackdrop) closeInfoSheet();
      });
    }
  }

  function scrollByStep(dir) {
    const cards = feedContainer.querySelectorAll('.teaser-card');
    const targetIdx = Math.max(0, Math.min(cards.length - 1, activeIndex + dir));
    if (cards[targetIdx]) {
      cards[targetIdx].scrollIntoView({ behavior: 'smooth' });
    }
  }

  function applyFilter(filterName) {
    if (progressInterval) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
    currentFilter = filterName || 'All';
    activeIndex = 0;

    if (categoryRail) {
      categoryRail.querySelectorAll('.reels-pill').forEach(p => {
        if (p.dataset.filter === currentFilter) p.classList.add('active');
        else p.classList.remove('active');
      });
    }

    if (feedContainer) {
      feedContainer.scrollTop = 0;
    }

    renderFeed();
    setupIntersectionObserver();
  }

  // Public API
  window.FilmZillaReels = {
    setFilter: applyFilter,
    handleLike,
    handleBookmark,
    handleShare,
    promptWatchModal,
    executeWatch,
    closeSmartSeekModal,
    openInfoSheet,
    closeInfoSheet,
    scrollByStep
  };

  // Start on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
