/* ============================================================
   FilmSub – player.js | CineSubz Movie Detail & Video Player
   ============================================================ */

'use strict';

let currentMovie = null;
let vjsPlayer = null;
let currentStreamIdx = 0;
let isTrailerActive = false;
let currentSeason = 1;
let currentEpisode = 1;

document.addEventListener('DOMContentLoaded', async () => {
  // Wait for app.js to be ready
  await waitForFilmSub();
  await FilmSub.loadMovies();

  const slug = getSlugFromURL();
  if (!slug) { showError('Movie not found. Please go back and try again.'); return; }

  currentMovie = FilmSub.findMovieBySlug(slug);
  if (!currentMovie) { showError('Movie not found. It may have been removed.'); return; }

  // Set initial season / episode
  currentSeason = currentMovie.season || 1;
  currentEpisode = currentMovie.episode || 1;

  FilmSub.trackView(slug);
  document.title = `${currentMovie.title || 'Movie'} (${currentMovie.year || ''}) Sinhala Subtitles – FilmSub`;

  // Render CineSubz-style components
  renderBreadcrumb(currentMovie);
  renderPageHeader(currentMovie);
  renderServerTabs(currentMovie);
  initAdaptiveQuality(currentMovie);
  initVideoPlayer(currentMovie);
  renderSeriesSection(currentMovie);
  renderMovieDetails(currentMovie);
  renderDownloadSection(currentMovie);
  renderRelatedMovies(currentMovie);
  initShareButton(currentMovie);
});

// ---- Wait for FilmSub global ----
function waitForFilmSub() {
  return new Promise(resolve => {
    const check = () => { if (window.FilmSub) resolve(); else setTimeout(check, 50); };
    check();
  });
}

// ---- Get slug from URL ----
function getSlugFromURL() {
  const params = new URLSearchParams(window.location.search);
  return params.get('id') || window.location.hash.replace('#', '') || null;
}

// ---- Error state ----
function showError(msg) {
  const mainEl = document.getElementById('movie-main');
  if (mainEl) {
    mainEl.innerHTML = `
      <div class="empty-state" style="margin-top:var(--header-h);padding-top:80px">
        <div class="empty-state-icon"><i class="fa-solid fa-triangle-exclamation"></i></div>
        <h3>Oops!</h3>
        <p>${FilmSub.escHtml(msg)}</p>
        <a href="index.html" class="btn btn-primary" style="margin-top:24px"><i class="fa-solid fa-house"></i> Go Home</a>
      </div>`;
  }
}

// ---- Data Fallback Helpers ----
/**
 * Returns ONLY Cloud CDN streams (Google Drive, OneDrive, R2, etc.)
 * Telegram URLs are NEVER included here — they are download-only.
 * If no cloud stream exists, returns empty array → player shows download UI.
 */
function getMovieStreams(movie) {
  if (!movie) return [];
  const list = [];

  // Helper: is a URL a valid Cloud CDN stream (not Telegram, not embed)?
  function isCloudStreamUrl(url) {
    if (!url) return false;
    if (url.includes('t.me/') || url.includes('api.telegram.org') ||
        url.includes('onrender.com/stream') || url.includes('telegram')) return false;
    if (url.includes('autoembed') || url.includes('vidsrc') ||
        url.includes('multiembed') || url.includes('2embed')) return false;
    return (
      url.includes('sharepoint.com') ||
      url.includes('1drv.ms') ||
      url.includes('google.com/uc') ||
      url.includes('drive.google.com') ||        // GDrive share, preview, file/d/
      url.includes('drive.google.com/file/d/') || // GDrive direct file links
      url.includes('r2.dev') ||
      url.includes('lnk.fyi') ||  // rclone share links
      url.includes('googleusercontent.com')
    );
  }


  // 1. Prefer explicit streams[] array from movie data (set by bot after upload)
  if (Array.isArray(movie.streams)) {
    movie.streams.forEach(s => {
      const sUrl = s.stream_url || '';
      // Skip download_only entries and non-cloud entries
      if (s.download_only) return;
      if (!isCloudStreamUrl(sUrl)) return;

      list.push({
        server: `Server ${list.length + 1}`,
        label: s.label || `⚡ Server ${list.length + 1} (Cloud Direct)`,
        type: s.type || 'video/mp4',
        stream_url: sUrl,
        file_id: s.file_id || '',
      });
    });
  }

  // 2. Fallback: use movie.stream_url if it is a cloud URL and not already in list
  if (movie.stream_url && isCloudStreamUrl(movie.stream_url)) {
    if (!list.some(item => item.stream_url === movie.stream_url)) {
      list.unshift({
        server: 'Server 1',
        label: '⚡ Server 1 (Cloud Direct Ultra HD)',
        type: 'video/mp4',
        stream_url: movie.stream_url,
        file_id: movie.file_id || '',
      });
    }
  }

  // NOTE: Telegram is intentionally EXCLUDED from streams.
  // Telegram URLs are in movie.downloads[] for download-only use.
  // If list is empty, the player will show a "Download Only" UI.
  return list;
}

function getMovieSubtitles(movie) {
  if (!movie) return [];
  if (Array.isArray(movie.subtitles) && movie.subtitles.length > 0) {
    return movie.subtitles;
  }
  if (movie.subtitle_url) {
    return [
      {
        language: movie.lang || "Sinhala",
        label: "සිංහල උපසිරැසි",
        url: movie.subtitle_url,
        default: true
      }
    ];
  }
  return [];
}

function getMovieDownloads(movie) {
  if (!movie) return [];
  if (Array.isArray(movie.downloads) && movie.downloads.length > 0) {
    return movie.downloads;
  }
  const streamUrl = movie.stream_url || (Array.isArray(movie.files) && movie.files[0] && movie.files[0].stream_url);
  if (streamUrl) {
    let sizeStr = "HD";
    if (movie.file_size) {
      const mb = movie.file_size / (1024 * 1024);
      sizeStr = mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb.toFixed(1)} MB`;
    }
    return [
      {
        quality: movie.quality || "1080p",
        size: sizeStr,
        url: streamUrl,
        format: "MP4"
      }
    ];
  }
  return [];
}

// ---- 1. Breadcrumb ----
function renderBreadcrumb(movie) {
  const currentEl = document.getElementById('cs-breadcrumb-current');
  if (currentEl) {
    currentEl.textContent = `${movie.title} (${movie.year || ''}) Sinhala Subtitles`;
  }
}

// ---- 2. Page Header ----
function renderPageHeader(movie) {
  const esc = FilmSub.escHtml;
  const titleEl = document.getElementById('movie-detail-title') || document.getElementById('cs-main-title');
  if (titleEl) {
    titleEl.textContent = `${movie.title || 'Untitled'} (${movie.year || ''}) Sinhala Subtitles`;
  }

  const qualEl = document.getElementById('cs-header-quality');
  if (qualEl) qualEl.textContent = movie.quality ? `${movie.quality} WEB-DL` : 'HD WEB-DL';

  const yearEl = document.getElementById('cs-header-year');
  if (yearEl) yearEl.textContent = movie.year || '';

  const imdbEl = document.getElementById('cs-header-imdb');
  if (imdbEl && movie.imdb) {
    imdbEl.style.display = 'inline-flex';
    imdbEl.innerHTML = `<i class="fa-solid fa-star"></i> ${esc(movie.imdb)} / 10`;
  }
}

// ---- 3. Server Tabs (with Trailer support) ----
function renderServerTabs(movie) {
  const tabsEl = document.getElementById('server-tabs');
  if (!tabsEl) return;
  const streams = getMovieStreams(movie);
  const icons = ['fa-solid fa-circle-play', 'fa-solid fa-bolt', 'fa-solid fa-server', 'fa-solid fa-film', 'fa-brands fa-telegram'];

  let tabsHtml = streams.map((s, i) => `
    <button class="server-tab${i === currentStreamIdx ? ' active' : ''}" data-type="stream" data-index="${i}">
      <i class="${icons[i] || 'fa-solid fa-server'}"></i>
      ${FilmSub.escHtml(s.label || s.server || `Server ${i + 1}`)}
    </button>`).join('');

  // Add Official Trailer tab
  tabsHtml += `
    <button class="server-tab" data-type="trailer">
      <i class="fa-brands fa-youtube" style="color:#ff0000"></i>
      Official Trailer
    </button>`;

  tabsEl.innerHTML = tabsHtml;

  tabsEl.querySelectorAll('.server-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      tabsEl.querySelectorAll('.server-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const type = btn.dataset.type;
      if (type === 'trailer') {
        loadTrailer(movie);
      } else {
        const idx = parseInt(btn.dataset.index, 10);
        loadStream(movie, idx);
      }
    });
  });
}

// ---- 3b. Adaptive Quality & Network-Aware Streaming ----
let selectedQuality = 'auto';

function detectNetworkSpeed() {
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!conn) return { speed: 'fast', downlink: 10, effectiveType: '4g' };
  const downlink = conn.downlink || 5;
  const effectiveType = conn.effectiveType || '4g';
  let speed = 'fast';
  if (downlink < 1.0 || effectiveType === '2g' || effectiveType === 'slow-2g') speed = 'slow';
  else if (downlink < 2.5 || effectiveType === '3g') speed = 'medium';
  return { speed, downlink, effectiveType };
}

function initAdaptiveQuality(movie) {
  const pills = document.querySelectorAll('.q-pill');
  const speedBadge = document.getElementById('net-speed-text');
  const net = detectNetworkSpeed();

  if (speedBadge) {
    if (net.speed === 'slow') {
      speedBadge.innerHTML = `<i class="fa-solid fa-signal" style="color:#e50914"></i> Low Data (360p)`;
    } else if (net.speed === 'medium') {
      speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#f5c518"></i> Standard (720p)`;
    } else {
      speedBadge.innerHTML = `<i class="fa-solid fa-bolt" style="color:#46d369"></i> High Speed (1080p)`;
    }
  }

  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      selectedQuality = pill.dataset.quality;

      FilmSub.showToast(`Quality set to: ${selectedQuality.toUpperCase()}`, 'info');

      // Check if current stream or downloads have a matching quality
      const downloads = getMovieDownloads(movie);
      const matched = downloads.find(d => (d.quality || '').toLowerCase() === selectedQuality.toLowerCase());
      if (matched && matched.url && vjsPlayer) {
        vjsPlayer.src({ src: matched.url, type: 'video/mp4' });
        setTimeout(syncSubtitles, 300);
      }
    });
  });

  // Listen to network changes if browser supports Network Information API
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn && conn.addEventListener) {
    conn.addEventListener('change', () => {
      const updatedNet = detectNetworkSpeed();
      if (selectedQuality === 'auto' && speedBadge) {
        if (updatedNet.speed === 'slow') {
          speedBadge.innerHTML = `<i class="fa-solid fa-signal" style="color:#e50914"></i> Low Data (360p)`;
        } else if (updatedNet.speed === 'medium') {
          speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#f5c518"></i> Standard (720p)`;
        } else {
          speedBadge.innerHTML = `<i class="fa-solid fa-bolt" style="color:#46d369"></i> High Speed (1080p)`;
        }
      }
    });
  }
}

// ---- 4. Universal Video Player (Iframe Embeds & Video.js) ----
function initVideoPlayer(movie) {
  const streams = getMovieStreams(movie);
  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  if (streams.length === 0) {
    // No cloud stream — show download-only UI
    const downloads = getMovieDownloads(movie);
    const dlLinks = downloads.slice(0, 3).map(dl =>
      `<a href="${FilmSub.escHtml(dl.url || '#')}" target="_blank" rel="noopener"
          style="display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-size:14px;font-weight:600">
         <i class="fa-solid fa-cloud-arrow-down"></i>
         ${FilmSub.escHtml(dl.quality || '1080p')} — Download
       </a>`
    ).join('');

    playerEl.innerHTML = `
      <div style="aspect-ratio:16/9;display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(135deg,#0a0a0a,#141414);color:var(--text2);gap:16px;border-radius:8px;padding:32px;text-align:center">
        <i class="fa-solid fa-cloud-arrow-down" style="font-size:48px;color:var(--accent)"></i>
        <h3 style="color:#fff;margin:0;font-size:18px">Download to Watch</h3>
        <p style="font-size:13px;color:var(--text3);max-width:400px;margin:0">
          Cloud stream server is being set up. Download the file and watch it with any player — Sinhala subtitles are included!
        </p>
        <div style="display:flex;gap:12px;flex-wrap:wrap;justify-content:center;margin-top:8px">
          ${dlLinks || '<span style="color:var(--text3);font-size:13px">Download links loading...</span>'}
        </div>
      </div>`;
    return;
  }

  loadStream(movie, 0);
}

function renderStreamEmbed(playerEl, stream, movie) {
  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }
  isTrailerActive = false;

  // For Google Drive /preview URLs, ensure clean embed format (no extra params)
  let embedUrl = stream.stream_url || '';
  if (embedUrl.includes('drive.google.com/file/d/')) {
    // Extract file ID and build clean preview URL
    const match = embedUrl.match(/\/d\/([a-zA-Z0-9_-]+)/);
    if (match) {
      embedUrl = `https://drive.google.com/file/d/${match[1]}/preview`;
    }
  }

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden">
      <iframe src="${FilmSub.escHtml(embedUrl)}"
              title="${FilmSub.escHtml(movie.title || 'Movie')} Streaming Player"
              frameborder="0"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
              allowfullscreen="true"
              webkitallowfullscreen="true"
              mozallowfullscreen="true"
              playsinline="true"
              style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px">
      </iframe>
    </div>`;
}

function createVjsPlayer(playerEl, stream, movie) {

  const subtitles = getMovieSubtitles(movie);
  const tracksHTML = subtitles.map((sub, i) => `
    <track kind="subtitles" src="${FilmSub.escHtml(sub.url || '')}"
           srclang="${FilmSub.escHtml(sub.language || 'si')}"
           label="${FilmSub.escHtml(sub.label || 'Sinhala')}"
           ${sub.default || i === 0 ? 'default' : ''}>`).join('');

  playerEl.innerHTML = `
    <div style="position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden">
      <video id="filmsubPlayer" class="video-js vjs-big-play-centered vjs-theme-fantasy"
             controls preload="auto" playsinline webkit-playsinline
             style="position:absolute;top:0;left:0;width:100%;height:100%"
             data-setup='{"fluid": true, "responsive": true}'>
        <source src="${FilmSub.escHtml(stream.stream_url)}" type="${FilmSub.escHtml(stream.type || 'video/mp4')}">
        ${tracksHTML}
        <p class="vjs-no-js">Enable JavaScript or use a modern browser to watch videos.</p>
      </video>
    </div>`;

  if (typeof videojs !== 'undefined') {
    vjsPlayer = videojs('filmsubPlayer', {
      fluid: true,
      responsive: true,
      preload: 'auto',
      autoplay: false,
      playbackRates: [0.5, 0.75, 1, 1.25, 1.5, 2],
      techOrder: ['html5'],
      html5: {
        vhs: {
          overrideNative: false,
          enableLowInitialPlaylist: true,
          limitRenditionByPlayerDimensions: false,
          useNetworkInformationApi: true,
          bandwidth: 5000000,       // start assuming 5 Mbps
          bufferBasedABR: true
        },
        nativeVideoTracks: true,
        nativeAudioTracks: true,
        nativeTextTracks: true
      },
      liveui: false,
      controlBar: {
        children: [
          'playToggle', 'volumePanel', 'currentTimeDisplay', 'timeDivider',
          'durationDisplay', 'progressControl', 'playbackRateMenuButton',
          'subsCapsButton', 'fullscreenToggle'
        ]
      }
    });

    vjsPlayer.ready(() => {
      // Aggressively pre-buffer: set bandwidth hint high so browser
      // sends a large initial Range request (matches server's 4 MiB pre-buffer)
      try {
        if (vjsPlayer.tech_ && vjsPlayer.tech_.vhs) {
          vjsPlayer.tech_.vhs.bandwidth = 8000000; // 8 Mbps hint → fast initial fetch
        }
        if (vjsPlayer.tech_ && vjsPlayer.tech_.el_) {
          vjsPlayer.tech_.el_.setAttribute('preload', 'auto');
        }
      } catch (e) {}
      syncSubtitles();
      try { vjsPlayer.play(); } catch (e) {}
    });

    vjsPlayer.on('loadedmetadata', () => {
      syncSubtitles();
    });

    try {
      if (vjsPlayer.textTracks && vjsPlayer.textTracks()) {
        vjsPlayer.textTracks().on('addtrack', (e) => {
          if (e && e.track && (e.track.kind === 'subtitles' || e.track.kind === 'captions')) {
            e.track.mode = 'showing';
          }
        });
      }
    } catch (e) {}

    // Intercept Video.js errors cleanly - never show raw black error screen
    vjsPlayer.on('error', () => {
      console.warn('Video.js direct stream error on server index', currentStreamIdx);
      const errDisplay = playerEl.querySelector('.vjs-error-display');
      if (errDisplay) errDisplay.style.display = 'none';

      const streams = getMovieStreams(movie);
      // Auto-fallback to next stream if available (e.g. Server 1 -> Server 2)
      if (streams.length > 1 && currentStreamIdx < streams.length - 1) {
        const nextIdx = currentStreamIdx + 1;
        FilmSub.showToast('Switching to mirror stream...', 'info');
        const tabsEl = document.getElementById('server-tabs');
        if (tabsEl) {
          tabsEl.querySelectorAll('.server-tab').forEach(b => b.classList.remove('active'));
          const btn = tabsEl.querySelector(`button[data-index="${nextIdx}"]`);
          if (btn) btn.classList.add('active');
        }
        loadStream(movie, nextIdx);
      } else {
        renderPlayerFallback(playerEl, movie);
      }
    });
  }
}


function renderPlayerFallback(playerEl, movie) {
  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }
  const streams = getMovieStreams(movie);
  playerEl.innerHTML = `
    <div style="width:100%;aspect-ratio:16/9;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#0d0d0d;color:#fff;padding:24px;text-align:center;gap:14px;border-radius:8px">
      <i class="fa-solid fa-triangle-exclamation" style="font-size:42px;color:var(--accent)"></i>
      <h3 style="font-size:18px;margin:0">Stream Server Issue</h3>
      <p style="font-size:13px;color:var(--text2);max-width:440px;margin:0">මෙම සේවාදායකයේ (Direct Server) වීඩියෝව වාදනය කිරීමට නොහැකි විය. කරුණාකර පහත ඇති වෙනත් Server එකක් තෝරන්න:</p>
      <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:6px">
        ${streams.map((s, i) => `
          <button class="server-tab fallback-btn" data-index="${i}" type="button" style="background:#242424;padding:9px 18px;border-radius:6px;border:1px solid rgba(255,255,255,0.15);color:#fff;font-size:13px;cursor:pointer">
            <i class="fa-solid fa-play"></i> ${FilmSub.escHtml(s.label || s.server || `Server ${i + 1}`)}
          </button>
        `).join('')}
      </div>
    </div>`;

  playerEl.querySelectorAll('.fallback-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.index, 10);
      const tabsEl = document.getElementById('server-tabs');
      if (tabsEl) {
        const tabBtn = tabsEl.querySelector(`button[data-index="${idx}"]`);
        if (tabBtn) { tabBtn.click(); return; }
      }
      loadStream(movie, idx);
    });
  });
}

function syncSubtitles() {
  if (!vjsPlayer) return;
  try {
    const textTracks = vjsPlayer.textTracks();
    if (!textTracks) return;
    for (let i = 0; i < textTracks.length; i++) {
      const track = textTracks[i];
      if (track && (track.kind === 'subtitles' || track.kind === 'captions')) {
        track.mode = 'showing';
        break;
      }
    }
  } catch (e) {}
}

function loadStream(movie, idx) {
  const streams = getMovieStreams(movie);
  if (!streams[idx]) return;
  const stream = streams[idx];
  currentStreamIdx = idx;

  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  // Google Drive /preview URLs MUST be shown as iframes (not Video.js).
  // They are Google's native video player and support Range requests internally.
  const url = stream.stream_url || '';
  const isGDrivePreview = url.includes('drive.google.com/file/d/') && url.includes('/preview');
  const isGDriveEmbed = url.includes('drive.google.com') && (url.includes('/preview') || url.includes('/view'));

  // Handle iframe embed stream (type=embed, or GDrive /preview)
  if (stream.type === 'embed' || stream.embed === true || isGDrivePreview || isGDriveEmbed) {
    renderStreamEmbed(playerEl, stream, movie);
    return;
  }


  // If trailer was active, or vjsPlayer is not initialized, create Video.js player
  if (isTrailerActive || !vjsPlayer) {
    if (vjsPlayer) {
      try { vjsPlayer.dispose(); } catch (e) {}
      vjsPlayer = null;
    }
    isTrailerActive = false;
    createVjsPlayer(playerEl, stream, movie);
    return;
  }

  if (stream.stream_url && !stream.stream_url.includes('t.me/')) {
    vjsPlayer.src({ src: stream.stream_url, type: stream.type || 'video/mp4' });
    setTimeout(syncSubtitles, 250);
    try { vjsPlayer.play(); } catch (e) {}
    return;
  }

  if (vjsPlayer && stream.stream_url) {
    vjsPlayer.src({ src: stream.stream_url, type: stream.type || 'video/mp4' });
    setTimeout(syncSubtitles, 250);
    try { vjsPlayer.play(); } catch (e) {}
  }
}

function loadTrailer(movie) {
  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  if (vjsPlayer) {
    try { vjsPlayer.pause(); } catch (e) {}
  }

  isTrailerActive = true;
  const query = encodeURIComponent(`${movie.title} ${movie.year || ''} official trailer`);
  const trailerSrc = movie.trailer_url || `https://www.youtube-nocookie.com/embed?listType=search&list=${query}&autoplay=1`;

  playerEl.innerHTML = `
    <div style="position:relative;aspect-ratio:16/9;width:100%;background:#000;border-radius:8px;overflow:hidden">
      <iframe src="${trailerSrc}"
              title="${FilmSub.escHtml(movie.title)} Official Trailer"
              frameborder="0"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
              allowfullscreen="true"
              webkitallowfullscreen="true"
              mozallowfullscreen="true"
              playsinline="true"
              style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px">
      </iframe>
    </div>`;
}

// ---- 4b. CineSubz / Netflix TV Series Seasons & Episodes Picker ----
function renderSeriesSection(movie) {
  const section = document.getElementById('series-section');
  if (!section) return;

  const isSeries = movie.type === 'series' || (Array.isArray(movie.seasons) && movie.seasons.length > 0);
  if (!isSeries) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';

  const seasons = Array.isArray(movie.seasons) && movie.seasons.length > 0
    ? movie.seasons
    : [{ season_number: 1, name: 'Season 1', episode_count: movie.number_of_episodes || 10 }];

  const totalSeasonsEl = document.getElementById('total-seasons-badge');
  const totalEpisodesEl = document.getElementById('total-episodes-badge');
  if (totalSeasonsEl) totalSeasonsEl.textContent = `${seasons.length} Season${seasons.length > 1 ? 's' : ''}`;
  if (totalEpisodesEl) {
    const totalEps = seasons.reduce((acc, s) => acc + (s.episode_count || 10), 0);
    totalEpisodesEl.textContent = `${totalEps} Episodes`;
  }

  const tabsEl = document.getElementById('season-tabs');
  const gridEl = document.getElementById('episodes-grid');
  if (!tabsEl || !gridEl) return;

  // Render season tabs
  tabsEl.innerHTML = seasons.map((s, idx) => `
    <button class="season-tab${idx === 0 ? ' active' : ''}" data-index="${idx}" data-season="${s.season_number || (idx + 1)}" type="button">
      <i class="fa-solid fa-layer-group"></i> ${FilmSub.escHtml(s.name || `Season ${s.season_number || (idx + 1)}`)}
    </button>
  `).join('');

  const renderEpisodesForSeason = (seasonObj) => {
    const count = seasonObj.episode_count || 10;
    const sNum = seasonObj.season_number || 1;
    let epHtml = '';
    for (let ep = 1; ep <= count; ep++) {
      const isActive = (sNum === currentSeason && ep === currentEpisode);
      epHtml += `
        <div class="episode-card${isActive ? ' active' : ''}" data-season="${sNum}" data-episode="${ep}">
          <div class="ep-info">
            <span class="ep-num">S${String(sNum).padStart(2, '0')} E${String(ep).padStart(2, '0')}</span>
            <span class="ep-title">Episode ${ep}</span>
            <span class="ep-duration"><i class="fa-regular fa-clock"></i> ~50 min</span>
          </div>
          <button class="ep-play-btn" title="Watch S${sNum} E${ep}" type="button">
            <i class="fa-solid fa-play"></i>
          </button>
        </div>
      `;
    }
    gridEl.innerHTML = epHtml;

    gridEl.querySelectorAll('.episode-card').forEach(card => {
      card.addEventListener('click', () => {
        gridEl.querySelectorAll('.episode-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        const ep = parseInt(card.dataset.episode, 10);
        const s = parseInt(card.dataset.season, 10);
        currentSeason = s;
        currentEpisode = ep;

        FilmSub.showToast(`Loading Season ${s} Episode ${ep}...`, 'info');

        const qualEl = document.getElementById('cs-header-quality');
        if (qualEl) {
          qualEl.textContent = `S${String(s).padStart(2, '0')} E${String(ep).padStart(2, '0')} HD`;
        }

        // Re-generate tabs & load stream for this episode
        renderServerTabs(movie);
        loadStream(movie, 0);

        // Scroll smoothly to player
        const playerSec = document.getElementById('player-section');
        if (playerSec) {
          playerSec.scrollIntoView({ behavior: 'smooth' });
        }
      });
    });
  };

  // Render initial season
  renderEpisodesForSeason(seasons[0]);

  // Tab click listeners
  tabsEl.querySelectorAll('.season-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      tabsEl.querySelectorAll('.season-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const idx = parseInt(btn.dataset.index, 10);
      renderEpisodesForSeason(seasons[idx]);
    });
  });
}

// ---- 5. Movie Details & Synopsis ----
function renderMovieDetails(movie) {
  const esc = FilmSub.escHtml;
  const poster = movie.poster || movie.poster_url || FilmSub.SITE_CONFIG.defaultPoster;
  const posterEl = document.getElementById('movie-poster-img');
  if (posterEl) {
    posterEl.src = poster;
    posterEl.alt = movie.title || '';
  }

  // Poster badges
  const badgesEl = document.getElementById('cs-poster-badges');
  if (badgesEl) {
    let bHtml = '';
    if (movie.imdb) bHtml += `<span class="badge badge-imdb"><i class="fa-solid fa-star"></i>${esc(movie.imdb)}</span>`;
    if (movie.quality) bHtml += `<span class="badge badge-quality">${esc(movie.quality)}</span>`;
    if (movie.year) bHtml += `<span class="badge badge-year">${esc(movie.year)}</span>`;
    badgesEl.innerHTML = bHtml;
  }

  // Cast list extraction
  let castStr = 'N/A';
  if (Array.isArray(movie.cast) && movie.cast.length > 0) {
    castStr = movie.cast.slice(0, 5).map(c => {
      if (typeof c === 'string') return esc(c);
      if (c && typeof c === 'object' && c.name) return esc(c.name);
      return '';
    }).filter(Boolean).join(', ');
  }

  // Genre chips
  const genres = Array.isArray(movie.genres) ? movie.genres : [];
  const genreChips = genres.map(g => `<a href="search.html?genre=${encodeURIComponent(g)}" class="genre-chip">${esc(g)}</a>`).join('');

  // Metadata Grid
  const metaGridEl = document.getElementById('cs-meta-grid');
  if (metaGridEl) {
    metaGridEl.innerHTML = `
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-film"></i> චිත්‍රපටයේ නම:</div>
        <div class="cs-meta-val"><strong>${esc(movie.title || 'Untitled')}</strong></div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-language"></i> සිංහල නම:</div>
        <div class="cs-meta-val" style="color:var(--gold);font-weight:600">${esc(movie.title_si || movie.title || '')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-regular fa-calendar"></i> නිකුත් වූ වර්ෂය:</div>
        <div class="cs-meta-val">${esc(movie.year || 'N/A')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-star"></i> IMDb අගය:</div>
        <div class="cs-meta-val"><span style="color:var(--gold);font-weight:700"><i class="fa-solid fa-star"></i> ${esc(movie.imdb || 'N/A')}</span> / 10</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-regular fa-clock"></i> ධාවන කාලය:</div>
        <div class="cs-meta-val">${esc(typeof movie.duration === 'number' ? `${movie.duration} min` : (movie.duration || 'N/A'))}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-user-tie"></i> අධ්‍යක්ෂණය:</div>
        <div class="cs-meta-val">${esc(movie.director || 'James Cameron')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-users"></i> ප්‍රධාන නළු නිළියන්:</div>
        <div class="cs-meta-val">${castStr}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-tags"></i> කාණ්ඩ (Genres):</div>
        <div class="cs-meta-val">${genreChips || 'Action'}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-volume-high"></i> ශ්‍රව්‍ය භාෂාව:</div>
        <div class="cs-meta-val">${esc(movie.language || 'English')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-closed-captioning"></i> උපසිරැසි:</div>
        <div class="cs-meta-val" style="color:var(--accent);font-weight:600">සිංහල (Sinhala Subtitles)</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-pen-nib"></i> උපසිරැසිකරු:</div>
        <div class="cs-meta-val">FilmSub.lk / CineSubz Team</div>
      </div>`;
  }

  // Synopsis
  const siDescEl = document.getElementById('movie-desc-si');
  if (siDescEl) {
    siDescEl.textContent = movie.description_si || movie.description || 'මෙම චිත්‍රපටය සඳහා සිංහල විස්තරය ළඟදීම එක් කෙරේ.';
  }

  const enDescEl = document.getElementById('movie-desc-en');
  if (enDescEl) {
    enDescEl.textContent = movie.description || 'No English synopsis available.';
  }
}

// ---- 6. Download Section ----
function renderDownloadSection(movie) {
  const grid = document.getElementById('download-grid');
  if (!grid) return;
  const downloads = getMovieDownloads(movie);

  if (downloads.length === 0) {
    grid.innerHTML = `<p class="text-muted" style="padding:20px">No download links available yet.</p>`;
  } else {
    // Deduplicate by quality label + host
    const seen = new Set();
    const uniqueDls = downloads.filter(dl => {
      const key = `${dl.quality}|${dl.host}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    grid.innerHTML = uniqueDls.map(dl => {
      const q = dl.quality || '720p';
      const sz = dl.size || '';
      const fmt = dl.format || 'MP4';
      const dlUrl = dl.url || '#';
      const host = dl.host || 'Direct';
      const isTelegram = dl.download_only === true || host === 'Telegram';
      const isCloud = host === 'Cloud CDN' || (!isTelegram && dlUrl.includes('drive.google'));

      // Host badge color
      const hostColor = isCloud ? 'var(--accent)' : (isTelegram ? '#229ED9' : 'var(--text2)');
      const hostIcon = isCloud
        ? '<i class="fa-brands fa-google-drive"></i>'
        : (isTelegram ? '<i class="fa-brands fa-telegram"></i>' : '<i class="fa-solid fa-server"></i>');

      const actionBtn = isTelegram
        ? `<a href="${FilmSub.escHtml(dlUrl)}" target="_blank" rel="noopener" class="btn-tg-dl"
              style="display:inline-flex;align-items:center;gap:7px">
             <i class="fa-brands fa-telegram"></i> Telegram Download
           </a>`
        : `<button class="btn-direct-dl" data-url="${FilmSub.escHtml(dlUrl)}" data-quality="${FilmSub.escHtml(q)}">
             <i class="fa-solid fa-cloud-arrow-down"></i> Direct Download
           </button>`;

      return `
        <div class="cs-dl-card download-card">
          <div class="cs-dl-top">
            <div class="cs-dl-quality">
              <i class="fa-solid fa-file-video" style="color:var(--accent)"></i>
              <span>${FilmSub.escHtml(q)} WEB-DL</span>
            </div>
            <div class="cs-dl-size-badge">${FilmSub.escHtml(sz)}</div>
          </div>
          <div class="cs-dl-specs">
            <span>${hostIcon} <span style="color:${hostColor}">${FilmSub.escHtml(host)}</span></span>
            <span>•</span>
            <span><i class="fa-solid fa-video"></i> ${FilmSub.escHtml(fmt)}</span>
            <span>•</span>
            <span style="color:var(--accent)"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි</span>
          </div>
          <div class="cs-dl-actions">
            ${actionBtn}
          </div>
        </div>`;
    }).join('');

    // Attach countdown trigger for direct download buttons
    grid.querySelectorAll('.btn-direct-dl').forEach(btn => {
      btn.addEventListener('click', () => {
        const url = btn.dataset.url;
        const quality = btn.dataset.quality;
        if (url && url !== '#') {
          window.FilmSubDownload && window.FilmSubDownload.show(url, quality, movie.title);
        }
      });
    });
  }

  // Subtitle Download Card Setup
  const subs = getMovieSubtitles(movie);
  const subDlBtn = document.getElementById('btn-sub-dl');
  const subDlMeta = document.getElementById('cs-sub-dl-meta');
  if (subDlBtn) {
    if (subs.length > 0 && subs[0].url) {
      subDlBtn.href = subs[0].url;
      subDlBtn.setAttribute('download', `${movie.slug || 'movie'}-sinhala-sub.vtt`);
    } else {
      subDlBtn.style.display = 'none';
      if (subDlMeta) subDlMeta.textContent = 'Hardcoded Sinhala Subtitles included with the video file.';
    }
  }
}

// ---- 7. Related Movies ----
function renderRelatedMovies(movie) {
  const grid = document.getElementById('related-grid');
  if (!grid) return;
  const related = FilmSub.getRelated(movie);
  if (related.length === 0) {
    const section = document.getElementById('related-section');
    if (section) section.style.display = 'none';
    return;
  }
  grid.innerHTML = related.map(m => FilmSub.generateMovieCard(m)).join('');
}

// ---- 8. Share Button ----
function initShareButton(movie) {
  const shareBtn = document.getElementById('movie-share-btn');
  if (shareBtn) {
    shareBtn.addEventListener('click', () => {
      if (navigator.share) {
        navigator.share({
          title: `${movie.title} Sinhala Subtitles`,
          text: `Watch ${movie.title} with Sinhala Subtitles on FilmSub`,
          url: window.location.href
        }).catch(() => {});
      } else {
        navigator.clipboard.writeText(window.location.href)
          .then(() => FilmSub.showToast('Link copied to clipboard!'))
          .catch(() => FilmSub.showToast('Could not copy link.'));
      }
    });
  }
}
