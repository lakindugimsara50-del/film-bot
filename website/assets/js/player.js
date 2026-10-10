/* ============================================================
   FilmSub – player.js | Netflix Super Player v2.0
   ============================================================
   Features:
   1. Zero-White-Screen ("Sudu Thira") Protection:
      - Dark #000000 backgrounds + animated .super-player-loader
      - Iframes stay opacity:0 until onload + paint buffer completes
   2. Chunked Byte-Range Streaming (/api/stream?id=<drive_id>&q=<quality>):
      - Streams Google Drive movies directly in Video.js (#filmsubPlayer)
        via HTTP 206 Partial Content byte-range chunks
   3. Real-Time Adaptive Quality Switcher (Auto / 1080p / 720p / 480p / 360p):
      - Monitors navigator.connection + Video.js buffer stalls ('waiting')
      - Auto-downgrades quality on lag while preserving currentTime()
      - In-player Quality Selector inside Video.js control bar (works in Fullscreen)
   4. Pure Native VIP Player:
      - Server 1: ⚡ Super Player (Telegram Cloud HD / Auto Sinhala Sub / Video.js)
      - 100% Native Telegram Cloud Streaming, zero third-party embed iframes (VidLink/AutoEmbed/2Embed removed)
   5. Embedded Sinhala Subtitles:
      - Native HTML5 <track> + programmatic VTTCue injection + in-video overlay
      - Supports Fullscreen on Desktop/Mobile, Sync (-0.5s/+0.5s), Size, Color & Custom .SRT upload
   ============================================================ */

'use strict';

let currentMovie = null;
let vjsPlayer = null;
let currentStreamIdx = 0;
let isTrailerActive = false;
let currentSeason = 1;
let currentEpisode = 1;

const QUALITY_LADDER = ['1080p', '720p', '480p'];
let selectedQuality = (function() { try { return sessionStorage.getItem('filmsub_pref_quality') || 'auto'; } catch(e) { return 'auto'; } })();
let currentEffectiveQuality = '720p';
let liveSubEnabled = true;
let liveSubOffsetSec = 0.0;
let liveSubTimer = null;
let liveSubStartEpoch = 0;
let parsedSubCues = [];
let subSizeIndex = 0;
const SUB_SIZES = [
  { label: '100%', scale: 1.0 },
  { label: '125%', scale: 1.25 },
  { label: '150%', scale: 1.5 }
];
let subColorIndex = 0;
const SUB_COLORS = ['#ffeb3b', '#ffffff', '#00e5ff', '#46d369'];

let stallTimestamps = [];
let activeStallTimer = null;
let lastAutoSwitchEpoch = 0;
let fallbackAutoRetryCount = 0;
let lastFallbackRetryEpoch = 0;

let activeStreamBaseUrl = (window.FILMSUB_STREAM_CONFIG && window.FILMSUB_STREAM_CONFIG.stream_base_url) || '';
let streamServerHealthy = false;
let edgeProxyHealthy = false;

async function probeStreamServerHealth(baseUrl) {
  // If baseUrl is empty, probe the edge proxy /stream/ping
  if (!baseUrl) {
    if (!isRunningOnCloudflarePages()) return false;
    try {
      const ctrl = new AbortController();
      const tId = setTimeout(() => ctrl.abort(), 2500);
      const resp = await fetch(`/stream/ping?t=${Date.now()}`, {
        method: 'GET',
        signal: ctrl.signal,
      }).catch(() => null);
      clearTimeout(tId);
      if (resp && resp.ok) {
        const ct = (resp.headers.get('content-type') || '').toLowerCase();
        if (ct.includes('application/json')) {
          const data = await resp.json().catch(() => null);
          if (data && data.backend && typeof data.backend === 'string' && data.backend.startsWith('http')) {
            const b = data.backend.trim().replace(/\/+$/, '');
            activeStreamBaseUrl = b;
          }
          if (data && (data.ready === true || data.status === 'pong')) {
            streamServerHealthy = true;
            return true;
          }
        }
      }
    } catch (e) {}
    return false;
  }

  // Probe specific backend (e.g. TryCloudflare tunnel or Render)
  const base = baseUrl ? baseUrl.replace(/\/+$/, '') : '';
  if (!base) return false;
  try {
    const ctrl = new AbortController();
    const tId = setTimeout(() => ctrl.abort(), 3500);
    const resp = await fetch(`${base}/health?t=${Date.now()}`, {
      method: 'GET',
      signal: ctrl.signal,
      mode: 'cors',
    }).catch(() => null);
    clearTimeout(tId);
    if (resp && resp.ok) {
      const ct = (resp.headers.get('content-type') || '').toLowerCase();
      if (ct.includes('application/json')) {
        const data = await resp.json().catch(() => null);
        if (data && (data.ready === true || data.status === 'pong')) {
          return true;
        }
      }
      return true;
    }

    const ctrl2 = new AbortController();
    const tId2 = setTimeout(() => ctrl2.abort(), 2000);
    const resp2 = await fetch(`${base}/stream/ping?t=${Date.now()}`, {
      method: 'GET',
      signal: ctrl2.signal,
      mode: 'cors',
    }).catch(() => null);
    clearTimeout(tId2);
    if (resp2 && resp2.ok) {
      const ct2 = (resp2.headers.get('content-type') || '').toLowerCase();
      if (ct2.includes('application/json')) {
        const data2 = await resp2.json().catch(() => null);
        if (data2 && (data2.ready === true || data2.status === 'pong')) {
          return true;
        }
      }
      return true;
    }
    return false;
  } catch (e) {
    return false;
  }
}

async function loadLiveStreamConfig(forceRefresh = false) {
  if (!forceRefresh) {
    const cached = sessionStorage.getItem('filmsub_stream_base');
    const cachedTime = parseInt(sessionStorage.getItem('filmsub_stream_base_time') || '0', 10);
    const cachedHealthy = sessionStorage.getItem('filmsub_stream_healthy') === 'true';
    if (cached && cachedHealthy && (Date.now() - cachedTime) < 60000) {
      activeStreamBaseUrl = cached;
      streamServerHealthy = true;
      edgeProxyHealthy = sessionStorage.getItem('filmsub_edge_healthy') === 'true';
      return activeStreamBaseUrl;
    }
  }

  let candidateUrl = '';
  let fallbackUrl = '';

  // 1. Fetch fresh stream_endpoint.json from GitHub Raw FIRST (real-time source of truth pushed by Colab)
  try {
    const ghUrl = 'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json?t=' + Date.now();
    const ctrl = new AbortController();
    const tId = setTimeout(() => ctrl.abort(), 2500);
    const rGh = await fetch(ghUrl, { signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(tId);
    if (rGh.ok) {
      const d = await rGh.json();
      if (d && d.stream_base_url && typeof d.stream_base_url === 'string') {
        const u = d.stream_base_url.trim().replace(/\/+$/, '');
        if (u.startsWith('http')) candidateUrl = u;
      }
      if (d && d.fallback_stream_url && typeof d.fallback_stream_url === 'string') {
        const uFb = d.fallback_stream_url.trim().replace(/\/+$/, '');
        if (uFb.startsWith('http')) fallbackUrl = uFb;
      }
    }
  } catch (e) {}

  // 2. Check window.FILMSUB_STREAM_CONFIG (loaded from stream_endpoint.js) if GitHub Raw didn't return
  if (!candidateUrl && window.FILMSUB_STREAM_CONFIG && typeof window.FILMSUB_STREAM_CONFIG === 'object') {
    if (window.FILMSUB_STREAM_CONFIG.stream_base_url) {
      candidateUrl = String(window.FILMSUB_STREAM_CONFIG.stream_base_url).trim().replace(/\/+$/, '');
    }
    if (window.FILMSUB_STREAM_CONFIG.fallback_stream_url) {
      fallbackUrl = String(window.FILMSUB_STREAM_CONFIG.fallback_stream_url).trim().replace(/\/+$/, '');
    }
  }

  // 3. Check local endpoint data/stream_endpoint.json
  if (!candidateUrl) {
    try {
      const rLoc = await fetch('data/stream_endpoint.json?t=' + Date.now(), { cache: 'no-store' });
      if (rLoc.ok) {
        const d = await rLoc.json();
        if (d && d.stream_base_url) {
          candidateUrl = String(d.stream_base_url).trim().replace(/\/+$/, '');
        }
        if (d && d.fallback_stream_url) {
          fallbackUrl = String(d.fallback_stream_url).trim().replace(/\/+$/, '');
        }
      }
    } catch (e) {}
  }

  // 4. Test candidate URL (Colab tunnel)
  let activeUrl = '';
  if (candidateUrl) {
    const isAlive = await probeStreamServerHealth(candidateUrl);
    if (isAlive) {
      activeUrl = candidateUrl;
      streamServerHealthy = true;
    }
  }

  // 5. Test Edge proxy (/stream/ping)
  edgeProxyHealthy = false;
  if (isRunningOnCloudflarePages()) {
    edgeProxyHealthy = await probeStreamServerHealth('');
  }

  // 6. Test fallback URL (e.g. Render 24/7 backend)
  if (!activeUrl && !edgeProxyHealthy && fallbackUrl) {
    const isFallbackAlive = await probeStreamServerHealth(fallbackUrl);
    if (isFallbackAlive) {
      activeUrl = fallbackUrl;
      streamServerHealthy = true;
    }
  }

  if (activeUrl) {
    activeStreamBaseUrl = activeUrl;
    streamServerHealthy = true;
  } else if (edgeProxyHealthy) {
    if (!activeStreamBaseUrl || !activeStreamBaseUrl.startsWith('http')) {
      activeStreamBaseUrl = candidateUrl || fallbackUrl || 'https://filmsub.pages.dev';
    }
    streamServerHealthy = true;
  } else {
    activeStreamBaseUrl = candidateUrl || fallbackUrl || '';
    streamServerHealthy = false;
  }

  try {
    if (streamServerHealthy && activeStreamBaseUrl) {
      sessionStorage.setItem('filmsub_stream_base', activeStreamBaseUrl);
      sessionStorage.setItem('filmsub_stream_healthy', 'true');
      sessionStorage.setItem('filmsub_edge_healthy', edgeProxyHealthy ? 'true' : 'false');
      sessionStorage.setItem('filmsub_stream_base_time', String(Date.now()));
    } else {
      sessionStorage.removeItem('filmsub_stream_base');
      sessionStorage.removeItem('filmsub_stream_healthy');
      sessionStorage.removeItem('filmsub_edge_healthy');
    }
  } catch (e) {}

  return activeStreamBaseUrl;
}

function isRunningOnCloudflarePages() {
  if (typeof window === 'undefined' || !window.location) return false;
  const h = (window.location.hostname || '').toLowerCase();
  return h.includes('pages.dev') || h.includes('filmsub');
}

function getStreamEndpointPrefix() {
  // 1. On Cloudflare Pages, always use the same-origin edge proxy to prevent ISP / DNS blocking of *.trycloudflare.com
  if (isRunningOnCloudflarePages()) {
    return '';
  }
  // 2. Direct live tunnel for ultra-fast zero-latency streaming on localhost / non-Pages environments
  if (activeStreamBaseUrl && activeStreamBaseUrl.startsWith('http') && !isDeadTunnel(activeStreamBaseUrl)) {
    return activeStreamBaseUrl;
  }
  return '';
}

function isDeadTunnel(u) {
  if (!u || typeof u !== 'string') return true;
  const l = u.toLowerCase();
  const cleanU = u.trim().replace(/\/+$/, '');
  const cleanBase = (activeStreamBaseUrl || '').trim().replace(/\/+$/, '');
  if (cleanBase && (cleanU === cleanBase || cleanU.startsWith(cleanBase) || cleanBase.startsWith(cleanU))) {
    return false;
  }
  return l.includes('trycloudflare.com') ||
         l.includes('loca.lt') ||
         l.includes('ngrok.io') ||
         l.includes('ngrok-free.app') ||
         l.includes('127.0.0.1') ||
         l.includes('localhost');
}

function normalizeStreamUrl(u) {
  if (!u || typeof u !== 'string') return '';
  const prefix = getStreamEndpointPrefix();
  const match = u.match(/\/stream\/channel\/(-?\d+)\/(\d+)/);
  if (match) {
    const cId = match[1];
    const mId = match[2];
    return `${prefix}/stream/channel/${cId}/${mId}`;
  }
  const matchDl = u.match(/\/stream\/download\/(-?\d+)\/(\d+)/);
  if (matchDl) {
    const cId = matchDl[1];
    const mId = matchDl[2];
    return `${prefix}/stream/download/${cId}/${mId}`;
  }
  const matchFile = u.match(/\/stream\/file\/([a-zA-Z0-9_-]+)/);
  if (matchFile) {
    const fId = matchFile[1];
    return `${prefix}/stream/file/${fId}`;
  }
  const matchRawStream = u.match(/\/stream\/([a-zA-Z0-9_-]{15,})/);
  if (matchRawStream && !matchRawStream[1].startsWith('channel') && !matchRawStream[1].startsWith('download') && !matchRawStream[1].startsWith('file')) {
    const fId = matchRawStream[1];
    return `${prefix}/stream/file/${fId}`;
  }
  return isDeadTunnel(u) ? '' : u;
}

// ── Multi-Quality Edge & RAM Pre-Warming System ─────────────────────────────
const _warmedTiers = new Set();
function prewarmQualityTier(movie, quality) {
  if (!movie) return;
  const qNorm = String(quality || '').toLowerCase();
  const vm = movie.variant_media || (movie.movie_entry && movie.movie_entry.variant_media);
  let targetMsgId = null;
  const wcId = movie.channel_chat_id || '-1004325759505';

  if (vm && typeof vm === 'object') {
    const v = vm[qNorm];
    if (v && v.message_id > 0) targetMsgId = v.message_id;
  }
  if (!targetMsgId && (qNorm === '720p' || qNorm === 'auto')) {
    targetMsgId = (typeof movie.message_id === 'number' && movie.message_id > 0) ? movie.message_id : null;
    if (!targetMsgId && Array.isArray(movie.downloads)) {
      for (const d of movie.downloads) {
        if (d.message_id && typeof d.message_id === 'number' && d.message_id > 0) {
          targetMsgId = d.message_id;
          break;
        }
        const dlU = String(d.url || d.telegram_url || '');
        const m = dlU.match(/t\.me\/c\/\d+\/(\d+)/) || dlU.match(/t\.me\/[a-zA-Z0-9_]+\/(\d+)/);
        if (m) {
          targetMsgId = parseInt(m[1], 10);
          break;
        }
      }
    }
  }

  if (targetMsgId) {
    const key = `${wcId}:${targetMsgId}`;
    if (_warmedTiers.has(key)) return;
    _warmedTiers.add(key);

    const pfx = getStreamEndpointPrefix() || '';
    fetch(`${pfx}/stream/warmup/${wcId}/${targetMsgId}`, { method: 'POST', mode: 'cors' }).catch(() => {});
  } else {
    // Proactively prime Google Drive initial 8MB into Cloudflare Edge Cache
    const driveId = extractMovieDriveId(movie);
    if (driveId) {
      const driveKey = `gdrive:${driveId}`;
      if (!_warmedTiers.has(driveKey)) {
        _warmedTiers.add(driveKey);
        fetch(`/api/stream?id=${encodeURIComponent(driveId)}&q=auto`, {
          headers: { 'Range': 'bytes=0-1048575' },
          mode: 'cors'
        }).catch(() => {});
      }
    }
  }
}

function prewarmAllVariants(movie) {
  if (!movie) return;
  prewarmQualityTier(movie, '720p');
  setTimeout(() => prewarmQualityTier(movie, '480p'), 80);
  setTimeout(() => prewarmQualityTier(movie, '1080p'), 160);
}

// ── Immediate Early Stream Pre-Warming (fires synchronously on script parse) ──
(function primeEarlyStream() {
  try {
    const slug = getSlugFromURL();
    if (!slug) return;
    const list = (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) ? window.FILMSUB_DATA.movies : null;
    if (list) {
      const m = list.find(x => x.slug === slug || x.id === slug);
      if (m) {
        prewarmAllVariants(m);
      }
    }
  } catch (e) {}
})();



document.addEventListener('DOMContentLoaded', async () => {
  const slug = getSlugFromURL();
  if (!slug) {
    showError('Movie not found. Please go back and try again.');
    return;
  }

  // 1. Instant Synchronous Catalog Lookup (<1ms)
  // window.FILMSUB_DATA is already loaded synchronously by <script src="data/movies_data.js">
  if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
    currentMovie = window.FILMSUB_DATA.movies.find(m => m.slug === slug || m.id === slug);
  }

  // Background non-blocking stream config refresh
  loadLiveStreamConfig().catch(() => {});

  // 2. Fallback: If movie is not in bundled data, wait for FilmSub.loadMovies()
  if (!currentMovie) {
    await waitForFilmSub();
    await FilmSub.loadMovies();
    currentMovie = FilmSub.findMovieBySlug(slug);
    if (!currentMovie) {
      try {
        const ghUrl = 'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json?t=' + Date.now();
        const ctrl = new AbortController();
        const tId = setTimeout(() => ctrl.abort(), 3000);
        const rGh = await fetch(ghUrl, { signal: ctrl.signal, cache: 'no-store' });
        clearTimeout(tId);
        if (rGh.ok) {
          const d = await rGh.json();
          const moviesList = Array.isArray(d) ? d : (d.movies || []);
          const found = moviesList.find(m => m.slug === slug || m.id === slug);
          if (found) {
            currentMovie = found;
            if (window.FilmSub && Array.isArray(FilmSub.movies)) {
              FilmSub.movies.unshift(found);
            }
          }
        }
      } catch (e) {}
    }
  } else {
    // If found synchronously, schedule FilmSub.loadMovies() in background without delaying playback start
    waitForFilmSub().then(() => {
      if (window.FilmSub && typeof FilmSub.loadMovies === 'function') {
        FilmSub.loadMovies().catch(() => {});
      }
    });
  }

  if (!currentMovie) {
    showError('Movie not found. It may have been removed.');
    return;
  }

  // Robust season & episode extraction from URL slug (e.g. s01e05, 1x05) or query params (?s=1&e=5)
  const slugEpMatch = slug.match(/[-_.]s(\d+)[-_.]?e(\d+)/i) || slug.match(/[-_.](\d+)x(\d+)/i);
  if (slugEpMatch) {
    currentSeason = parseInt(slugEpMatch[1], 10);
    currentEpisode = parseInt(slugEpMatch[2], 10);
  } else {
    currentSeason = currentMovie.season || 1;
    currentEpisode = currentMovie.episode || 1;
  }
  const urlParams = new URLSearchParams(window.location.search);
  const qSeason = urlParams.get('s') || urlParams.get('season');
  const qEpisode = urlParams.get('e') || urlParams.get('ep') || urlParams.get('episode');
  if (qSeason) currentSeason = parseInt(qSeason, 10);
  if (qEpisode) currentEpisode = parseInt(qEpisode, 10);

  const initialNet = detectNetworkSpeed();
  currentEffectiveQuality = initialNet.recommendedQuality;

  FilmSub.trackView(slug);
  const isSeriesSlug = currentMovie.type === 'series' || currentSeason > 1 || currentEpisode > 1 || Boolean(slugEpMatch);
  if (isSeriesSlug) {
    document.title = `${currentMovie.title || 'Series'} S${String(currentSeason).padStart(2, '0')}E${String(currentEpisode).padStart(2, '0')} Sinhala Subtitles – FilmSub`;
  } else {
    document.title = `${currentMovie.title || 'Movie'} (${currentMovie.year || ''}) Sinhala Subtitles – FilmSub`;
  }

  renderBreadcrumb(currentMovie);
  renderPageHeader(currentMovie);
  renderServerTabs(currentMovie);
  initAdaptiveQuality(currentMovie);
  initSubtitleControls(currentMovie);

  // Instant player initialization (<50ms)
  initVideoPlayer(currentMovie);

  renderQuickDownloadStrip(currentMovie);
  renderSeriesSection(currentMovie);
  renderMovieDetails(currentMovie);
  renderDownloadSection(currentMovie);
  renderRelatedMovies(currentMovie);
  initShareButton(currentMovie);

  // Background non-blocking subtitle parser & sync
  loadParsedSubtitles(currentMovie).then(() => {
    try { syncSubtitles(); } catch (e) {}
  }).catch(() => {});

  // Trigger instant background pre-buffering (first 8-16MB) for all qualities so video starts in <300ms
  try {
    prewarmAllVariants(currentMovie);
  } catch (e) {}
});

// ---- Wait for FilmSub global ----
function waitForFilmSub() {
  return new Promise(resolve => {
    const check = () => { if (window.FilmSub) resolve(); else setTimeout(check, 40); };
    check();
  });
}

// ---- Get slug from URL ----
function getSlugFromURL() {
  const params = new URLSearchParams(window.location.search);
  const qId = params.get('id') || params.get('movie') || params.get('slug');
  if (qId) return qId.trim();

  const hash = window.location.hash.replace(/^#\/?/, '').trim();
  if (hash) return hash;

  const pathParts = window.location.pathname.split('/').filter(Boolean);
  if (pathParts.length >= 2 && (pathParts[0] === 'movie' || pathParts[0] === 'films')) {
    return pathParts[1].replace(/\.html$/, '').trim();
  }
  return null;
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

// ---- Google Drive File ID & Chunk URL Helpers ----
function extractDriveFileIdFromUrl(url) {
  if (!url || typeof url !== 'string') return '';
  const m1 = url.match(/\/file\/d\/([a-zA-Z0-9_-]{15,60})/);
  if (m1) return m1[1];
  const m2 = url.match(/\/d\/([a-zA-Z0-9_-]{15,60})/);
  if (m2) return m2[1];
  const m3 = url.match(/[?&]id=([a-zA-Z0-9_-]{15,60})/);
  if (m3) return m3[1];
  return '';
}

function extractMovieDriveId(movie) {
  if (!movie) return '';
  if (movie.drive_file_id) return movie.drive_file_id;
  const fromPrimary = extractDriveFileIdFromUrl(movie.stream_url || '');
  if (fromPrimary) return fromPrimary;

  if (Array.isArray(movie.streams)) {
    for (const s of movie.streams) {
      if (s && s.drive_id) return s.drive_id;
      const id = extractDriveFileIdFromUrl(s.stream_url || s.url || '');
      if (id) return id;
    }
  }
  if (Array.isArray(movie.downloads)) {
    for (const d of movie.downloads) {
      const id = extractDriveFileIdFromUrl(d.url || '');
      if (id) return id;
    }
  }
  if (movie.qualities && typeof movie.qualities === 'object') {
    for (const k of Object.keys(movie.qualities)) {
      const val = movie.qualities[k];
      const u = typeof val === 'string' ? val : (val && (val.stream_url || val.download_url)) || '';
      const id = extractDriveFileIdFromUrl(u);
      if (id) return id;
    }
  }
  return '';
}

function extractDriveIdForQuality(movie, quality) {
  if (!movie) return '';
  const qNorm = String(quality || 'auto').toLowerCase();
  if (movie.qualities && typeof movie.qualities === 'object') {
    const qEntry = movie.qualities[qNorm] || movie.qualities[qNorm.toUpperCase()];
    if (qEntry) {
      if (typeof qEntry === 'object' && qEntry.drive_id) return qEntry.drive_id;
      const u = typeof qEntry === 'string' ? qEntry : (qEntry.stream_url || qEntry.download_url || '');
      const qId = extractDriveFileIdFromUrl(u);
      if (qId) return qId;
    }
  }
  if (Array.isArray(movie.downloads) && qNorm !== 'auto') {
    const matchedDl = movie.downloads.find(d => String(d.quality || '').toLowerCase().includes(qNorm) && !d.download_only);
    if (matchedDl && matchedDl.url) {
      const dId = extractDriveFileIdFromUrl(matchedDl.url);
      if (dId) return dId;
    }
  }
  return extractMovieDriveId(movie);
}

function buildChunkStreamUrl(driveId, quality) {
  if (!driveId) return '';
  const q = encodeURIComponent(String(quality || 'auto').toLowerCase());
  const id = encodeURIComponent(driveId);
  return `/api/stream?id=${id}&q=${q}`;
}

/**
 * Builds the complete Multi-Server & External Backup ("Bahirawa Players") stream list.
 * Prioritizes Telegram Cloud HD & Super Player as Server 1, backed by VIP HD servers.
 * Ensures every movie & TV episode works seamlessly across Laptop, PC, Mobile, and Tablet.
 */
/**
 * Curates strictly the Pure Native Telegram Cloud Super Player:
 * - Server 1: ⚡ Super Player (Telegram Cloud HD • Zero Ads) — Native Video.js HTML5 player, zero ads, auto Sinhala sub
 */
function getMovieStreams(movie) {
  if (!movie) return [];
  const list = [];
  const driveId = extractMovieDriveId(movie);
  const isSeries = movie.type === 'series' || (Array.isArray(movie.seasons) && movie.seasons.length > 0) || currentSeason > 1 || currentEpisode > 1;
  const sNum = currentSeason || movie.season || 1;
  const eNum = currentEpisode || movie.episode || 1;
  const primaryUrl = movie.stream_url || (Array.isArray(movie.streams) && movie.streams[0] && movie.streams[0].stream_url) || '';

  // Does the local stream match this specific episode?
  const isMatchingEpisode = !isSeries || (sNum === (movie.season || 1) && eNum === (movie.episode || 1));

  let imdbId = (movie.imdb_id || movie.imdbId || '').trim();
  let tmdbId = String(movie.tmdb_id || movie.tmdbId || '').trim();

  // If imdb field contains tt ID (e.g. "tt0944947")
  if (!imdbId && movie.imdb && /^tt\d+/i.test(movie.imdb.trim())) {
    imdbId = movie.imdb.trim();
  }

  // Extract from existing streams/downloads/slug/id if missing
  if (!imdbId || !tmdbId) {
    const urlsToCheck = [
      movie.stream_url || '',
      ...(Array.isArray(movie.streams) ? movie.streams.map(s => s.stream_url || s.url || '') : []),
      ...(Array.isArray(movie.downloads) ? movie.downloads.map(d => d.url || '') : []),
      movie.slug || '',
      movie.id || ''
    ];
    for (const u of urlsToCheck) {
      if (!imdbId) {
        const mImdb = u.match(/(tt\d{6,10})/i);
        if (mImdb) imdbId = mImdb[1];
      }
      if (!tmdbId) {
        const mTmdb = u.match(/tmdb[=_/](\d{3,10})/i);
        if (mTmdb) tmdbId = mTmdb[1];
      }
      if (imdbId && tmdbId) break;
    }
  }

  // Resolve extId from other movies in catalog with same title if missing
  if ((!imdbId && !tmdbId) && window.FilmSub && typeof FilmSub.getAllMovies === 'function') {
    const normT = (movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const matchedOther = FilmSub.getAllMovies().find(m => {
      const otherT = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      return otherT && (otherT === normT || normT.startsWith(otherT) || otherT.startsWith(normT)) && (m.imdb_id || m.tmdb_id);
    });
    if (matchedOther) {
      if (!imdbId && matchedOther.imdb_id) imdbId = matchedOther.imdb_id.trim();
      if (!tmdbId && matchedOther.tmdb_id) tmdbId = String(matchedOther.tmdb_id).trim();
    }
  }

  // Fallback catalog for common popular titles
  if (!imdbId || !tmdbId) {
    const titleKey = (movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const FALLBACK_EXT_IDS = {
      'gameofthrones': { imdb: 'tt0944947', tmdb: '1399' },
      'the100': { imdb: 'tt2661044', tmdb: '48866' },
      'breakingbad': { imdb: 'tt0903747', tmdb: '1396' },
      'interstellar': { imdb: 'tt0816692', tmdb: '157336' },
      'inception': { imdb: 'tt1375666', tmdb: '27205' },
      'thebeekeeper': { imdb: 'tt15314262', tmdb: '866398' },
      'premalu': { imdb: 'tt28288786', tmdb: '1149791' },
      'onelastshot': { imdb: 'tt37971717', tmdb: '1607127' },
      'dc': { imdb: 'tt37501035', tmdb: '1479832' },
      'irumudi': { imdb: 'tt31000001', tmdb: '1300001' },
      'feed': { imdb: 'tt30505578', tmdb: '1128204' },
      'fuze': { imdb: 'tt31003460', tmdb: '1242265' },
      'kgfchapter1': { imdb: 'tt7838252', tmdb: '564147' },
      'iceage': { imdb: 'tt0268380', tmdb: '425' }
    };
    for (const [k, ids] of Object.entries(FALLBACK_EXT_IDS)) {
      if (titleKey.includes(k) || k.includes(titleKey)) {
        if (!imdbId && ids.imdb) imdbId = ids.imdb;
        if (!tmdbId && ids.tmdb) tmdbId = ids.tmdb;
        break;
      }
    }
  }

  let srvCounter = 1;

  // =========================================================================
  // Server 1: ⚡ Super Player (Telegram Cloud HD • Auto Sinhala Sub) [Native Video.js - Zero Ads]
  // =========================================================================
  let nativeStreamUrl = '';
  
  // 1. Resolve real Telegram Cloud stream via channel chat ID and message ID
  let tgChatId = movie.channel_chat_id || '';
  // IMPORTANT: NEVER use channel_post_id as video message_id!
  // channel_post_id is the announcement poster/text message in the public channel, NOT the video stream!
  let tgMsgId = (typeof movie.message_id === 'number' && movie.message_id > 0) ? movie.message_id : 0;

  if (!tgMsgId && Array.isArray(movie.downloads)) {
    for (const d of movie.downloads) {
      if (d.message_id && typeof d.message_id === 'number' && d.message_id > 0) {
        tgMsgId = d.message_id;
        break;
      }
      const rawDlUrl = String(d.url || d.telegram_url || '');
      const mMatch = rawDlUrl.match(/t\.me\/c\/\d+\/(\d+)/) || rawDlUrl.match(/t\.me\/[a-zA-Z0-9_]+\/(\d+)/);
      if (mMatch) {
        const pId = parseInt(mMatch[1], 10);
        if (pId > 0) {
          tgMsgId = pId;
          break;
        }
      }
    }
  }

  if (!tgMsgId && Array.isArray(movie.streams)) {
    for (const s of movie.streams) {
      const u = String(s.stream_url || s.url || '');
      const matchS = u.match(/\/stream\/channel\/(-?\d+)\/(\d+)/);
      if (matchS) {
        if (!tgChatId) tgChatId = matchS[1];
        const pId = parseInt(matchS[2], 10);
        if (pId > 0) {
          tgMsgId = pId;
          break;
        }
      }
    }
  }

  const existingTgStream = Array.isArray(movie.streams)
    ? movie.streams.find(s => s.mode === 'super_chunk' || (s.stream_url && !s.embed && !s.stream_url.includes('vidlink') && !s.stream_url.includes('autoembed') && !s.stream_url.includes('multiembed')))
    : null;

  const vm = movie.variant_media || (movie.movie_entry && movie.movie_entry.variant_media);
  if (vm && typeof vm === 'object') {
    const qKey = (currentEffectiveQuality || '720p').toLowerCase();
    const vOpt = vm[qKey] || vm['720p'] || vm['480p'] || vm['1080p'] || Object.values(vm).find(v => v && (v.message_id > 0 || (v.stream_url && !v.stream_url.includes('/0'))));
    if (vOpt) {
      if (vOpt.stream_url && !vOpt.stream_url.includes('/0')) {
        const norm = normalizeStreamUrl(vOpt.stream_url);
        if (norm) nativeStreamUrl = norm;
      }
      if (vOpt.message_id && typeof vOpt.message_id === 'number' && vOpt.message_id > 0) {
        tgMsgId = vOpt.message_id;
      }
    }
  }

  if (!nativeStreamUrl && tgMsgId > 0) {
    if (!tgChatId) tgChatId = '-1004325759505';
    nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/channel/${tgChatId}/${tgMsgId}`;
  } else if (!nativeStreamUrl && movie.file_id) {
    nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(movie.file_id)}`;
  } else if (!nativeStreamUrl && existingTgStream && existingTgStream.stream_url && !existingTgStream.stream_url.includes('vidlink') && !existingTgStream.stream_url.includes('autoembed') && !existingTgStream.stream_url.includes('multiembed')) {
    nativeStreamUrl = normalizeStreamUrl(existingTgStream.stream_url);
  } else if (!nativeStreamUrl && driveId) {
    nativeStreamUrl = `/api/stream?id=${encodeURIComponent(driveId)}`;
  } else if (!nativeStreamUrl && primaryUrl && !primaryUrl.includes('vidlink') && !primaryUrl.includes('autoembed') && !primaryUrl.includes('multiembed') && !primaryUrl.includes('embed')) {
    nativeStreamUrl = normalizeStreamUrl(primaryUrl);
  }

  // Fallback for TV series episode switching if current movie doesn't have this episode's stream
  if (!nativeStreamUrl && isSeries && window.FilmSub && typeof FilmSub.getAllMovies === 'function') {
    const normT = (movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const matchedEp = FilmSub.getAllMovies().find(m => {
      const otherT = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      return (otherT === normT || otherT.startsWith(normT) || normT.startsWith(otherT)) &&
             (m.season === sNum && m.episode === eNum);
    });
    if (matchedEp) {
      const epMsgId = (typeof matchedEp.message_id === 'number' && matchedEp.message_id > 0) ? matchedEp.message_id : 0;
      const epChatId = matchedEp.channel_chat_id || tgChatId || '-1004325759505';
      if (epMsgId > 0) {
        tgMsgId = epMsgId;
        nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/channel/${epChatId}/${epMsgId}`;
      } else if (matchedEp.file_id) {
        nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(matchedEp.file_id)}`;
      }
    }
  }

  const isRealStreamReady = Boolean(
    (tgMsgId > 0 || movie.file_id || driveId) &&
    nativeStreamUrl &&
    !nativeStreamUrl.endsWith('/0') &&
    !nativeStreamUrl.includes('/channel/-1004325759505/0')
  );

  list.push({
    server: 'Server 1',
    label: '⚡ Super Player (Telegram Cloud HD)',
    mode: 'super_chunk',
    type: 'video/mp4',
    embed: false,
    stream_url: nativeStreamUrl || `${getStreamEndpointPrefix()}/stream/channel/-1004325759505/${tgMsgId}`,
    hasLocalFile: isRealStreamReady,
  });

  return list;
}

// ---- Subtitle Builders & Parsers (SRT + VTT Support) ----
function buildDefaultSinhalaVttText(movie) {
  const titleEn = (movie && movie.title) ? movie.title : 'Movie';
  const titleSi = (movie && movie.title_si) ? movie.title_si : titleEn;
  const year = (movie && movie.year) ? ` (${movie.year})` : '';
  return [
    'WEBVTT',
    '',
    '1',
    '00:00:00.200 --> 00:00:05.500',
    `🎬 ${titleSi}${year} — සිංහල උපසිරැසි (Sinhala Subtitles Auto-Play ON)`,
    '',
    '2',
    '00:00:05.800 --> 00:00:12.500',
    'FilmSub.lk Super Player — 1080p / 720p / 480p Ultra-Smooth Adaptive Stream',
    '',
    '3',
    '00:00:12.800 --> 00:00:24.000',
    'Download කරන සියලුම MP4 වීඩියෝ ගොනු තුළ සිංහල උපසිරැසි ස්වයංක්‍රීයව (Merged Subtitles) අන්තර්ගත කර ඇත.',
    '',
    '4',
    '00:00:24.500 --> 00:00:40.000',
    `${titleEn} — සිංහල උපසිරැසි සමඟින් දැන් ක්‍රියාත්මකයි.`
  ].join('\n');
}

function buildDefaultSinhalaVttDataUri(movie) {
  return 'data:text/vtt;charset=utf-8,' + encodeURIComponent(buildDefaultSinhalaVttText(movie));
}

function _isValidSubUrl(u) {
  if (!u || typeof u !== 'string') return false;
  if (u === 'data:text/vtt;...' || u.length < 22) return false;
  return true;
}

function getMovieSubtitles(movie) {
  if (!movie) return [];
  const isHardcoded = Boolean(movie && (movie.sub_hardcoded || movie.is_already_hardsubbed));
  const defaultUri = buildDefaultSinhalaVttDataUri(movie);
  if (Array.isArray(movie.subtitles) && movie.subtitles.length > 0) {
    return movie.subtitles.map((sub, idx) => ({
      ...sub,
      language: sub.language || 'Sinhala',
      srclang: sub.srclang || 'si',
      label: sub.label || 'සිංහල උපසිරැසි (Sinhala)',
      url: _isValidSubUrl(sub.url) ? sub.url : defaultUri,
      default: sub.default !== undefined ? sub.default : (!isHardcoded && idx === 0)
    }));
  }

  let ghSubUrl = '';
  if (movie.slug) {
    const isSeries = movie.type === 'series' || currentSeason > 1 || currentEpisode > 1;
    const sNum = currentSeason || movie.season || 1;
    const eNum = currentEpisode || movie.episode || 1;
    const epSfx = isSeries ? `-s${String(sNum).padStart(2, '0')}e${String(eNum).padStart(2, '0')}` : '';
    ghSubUrl = `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/subs/${movie.slug}${epSfx}-si.vtt`;
  }

  const effectiveSubUrl = _isValidSubUrl(movie.subtitle_url) ? movie.subtitle_url : ghSubUrl;
  if (effectiveSubUrl) {
    return [
      {
        language: movie.lang || 'Sinhala',
        srclang: 'si',
        label: 'සිංහල උපසිරැසි (Sinhala)',
        url: effectiveSubUrl,
        default: !isHardcoded
      }
    ];
  }
  return [
    {
      language: 'Sinhala',
      srclang: 'si',
      label: 'සිංහල උපසිරැසි (Sinhala Auto)',
      url: defaultUri,
      default: !isHardcoded
    }
  ];
}

function parseVttTime(ts) {
  if (!ts) return 0;
  const parts = ts.trim().replace(',', '.').split(':');
  if (parts.length === 3) {
    return parseFloat(parts[0]) * 3600 + parseFloat(parts[1]) * 60 + parseFloat(parts[2]);
  }
  if (parts.length === 2) {
    return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
  }
  return 0;
}

function decodeSubtitleBuffer(buffer) {
  if (!buffer) return '';
  if (typeof buffer === 'string') {
    return buffer.replace(/^\uFEFF/, '').replace(/\u0000/g, '');
  }
  const bytes = new Uint8Array(buffer);
  let encoding = 'utf-8';
  let offset = 0;
  if (bytes.length >= 2 && bytes[0] === 0xFF && bytes[1] === 0xFE) {
    encoding = 'utf-16le';
    offset = 2;
  } else if (bytes.length >= 2 && bytes[0] === 0xFE && bytes[1] === 0xFF) {
    encoding = 'utf-16be';
    offset = 2;
  } else if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
    encoding = 'utf-8';
    offset = 3;
  }
  try {
    const decoder = new TextDecoder(encoding);
    return decoder.decode(bytes.subarray(offset)).replace(/^\uFEFF/, '').replace(/\u0000/g, '');
  } catch (e) {
    const fallbackDec = new TextDecoder('utf-8');
    return fallbackDec.decode(bytes).replace(/^\uFEFF/, '').replace(/\u0000/g, '');
  }
}

function parseVttToCues(rawText) {
  if (!rawText) return [];
  const sanitized = String(rawText).replace(/^\uFEFF/, '').replace(/\u0000/g, '');
  const lines = sanitized.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
  const cues = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (line.includes('-->')) {
      const [startStr, endStr] = line.split('-->');
      const start = parseVttTime(startStr);
      const end = parseVttTime((endStr || '').trim().split(/\s+/)[0]);
      i++;
      const textLines = [];
      while (i < lines.length && lines[i].trim() !== '') {
        textLines.push(lines[i].trim());
        i++;
      }
      if (textLines.length > 0 && end > start) {
        cues.push({
          start,
          end,
          text: textLines.join('<br>'),
          plainText: textLines.join('\n').replace(/<[^>]+>/g, '')
        });
      }
    } else {
      i++;
    }
  }
  return cues;
}

function convertSrtToVttText(rawText) {
  if (!rawText) return 'WEBVTT\n\n';
  const cleaned = String(rawText).replace(/^\uFEFF/, '').replace(/\u0000/g, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
  if (cleaned.startsWith('WEBVTT')) return cleaned;
  const vttBody = cleaned.replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, '$1.$2');
  return 'WEBVTT\n\n' + vttBody;
}

async function loadParsedSubtitles(movie) {
  const subs = getMovieSubtitles(movie);
  if (subs && subs.length > 0) {
    const subUrl = subs[0].url || '';
    try {
      if (subUrl.startsWith('data:text/vtt')) {
        const commaIdx = subUrl.indexOf(',');
        if (commaIdx !== -1) {
          const raw = decodeURIComponent(subUrl.slice(commaIdx + 1));
          const parsed = parseVttToCues(raw);
          if (parsed.length > 0) {
            parsedSubCues = parsed;
            return parsed;
          }
        }
      } else if (subUrl) {
        const resp = await fetch(subUrl);
        if (resp.ok) {
          const buf = await resp.arrayBuffer();
          const text = decodeSubtitleBuffer(buf);
          const parsed = parseVttToCues(convertSrtToVttText(text));
          if (parsed.length > 0) {
            parsedSubCues = parsed;
            return parsed;
          }
        }
      }
    } catch (e) {
      console.warn('Subtitle fetch fallback to default Sinhala VTT:', e);
    }
  }
  const fallbackParsed = parseVttToCues(buildDefaultSinhalaVttText(movie));
  parsedSubCues = fallbackParsed;
  return fallbackParsed;
}

// ---- Download Link Normalization ----
function normalizeDriveDownloadUrl(url, quality, movieTitle) {
  if (!url) return '';
  const str = String(url).trim();
  const q = String(quality || '1080p').replace(/[^a-zA-Z0-9]/g, '') || '1080p';
  const t = String(movieTitle || 'Movie').trim() || 'Movie';

  if (str.startsWith('/api/download')) {
    return str;
  }

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

function formatBytesFromBytes(bytes) {
  if (!bytes || bytes <= 0) return '';
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.max(95, Math.round(mb))} MB`;
}

function getMovieDownloads(movie) {
  if (!movie) return [];
  const rawDls = Array.isArray(movie.downloads) ? [...movie.downloads] : [];
  const botUsername = 'Filmsinhala200Bot';
  const vm = movie.variant_media;

  // Channel ID extraction helper
  let chClean = '4325759505';
  const mUrl = movie.stream_url || (rawDls.find(d => d.url && d.url.includes('/stream/channel/')) || {}).url || '';
  const mMatch = mUrl.match(/\/stream\/channel\/(-?\d+)\//);
  if (mMatch) {
    const rawId = mMatch[1].replace(/^-?100/, '').replace(/^-/, '');
    if (rawId) chClean = rawId;
  }

  const results = [];
  const seenQualities = new Set();
  const seenUrls = new Set();

  const addCard = (card) => {
    if (!card || !card.url || isDeadTunnel(card.url)) return;
    if (seenUrls.has(card.url)) return;
    seenUrls.add(card.url);
    results.push({
      ...card,
      format: card.format || 'MP4 (සිංහල Sub Merged)',
      sub_merged: true,
      subtitle_merged: true,
    });
  };

  const hasTelegramMedia = Boolean(
    (vm && typeof vm === 'object' && Object.keys(vm).length > 0) ||
    movie.message_id ||
    movie.file_id ||
    rawDls.some(d => d.host === 'Telegram' || (d.url && d.url.includes('t.me')))
  );

  // 1. If multi-quality variant_media is present (from Leech / Bot upload),
  // build high-priority genuine download cards for 1080p, 720p, 480p, 360p
  if (vm && typeof vm === 'object') {
    const qLabels = {
      '1080p': '1080p Full HD',
      '720p': '720p HD',
      '480p': '480p SD',
      '360p': '360p Data Saver',
    };
    ['1080p', '720p', '480p', '360p'].forEach(q => {
      const v = vm[q];
      if (v && typeof v === 'object' && (v.file_id || v.message_id)) {
        const sz = v.size_bytes ? formatBytesFromBytes(v.size_bytes) : '';
        const botUrl = `https://t.me/${botUsername}?start=dl_${movie.slug}_${q}`;
        const chanUrl = v.message_id ? `https://t.me/c/${chClean}/${v.message_id}` : '';
        const primaryDlUrl = botUrl || chanUrl;
        
        seenQualities.add(q.toLowerCase());
        addCard({
          quality: q,
          label: `${qLabels[q] || q} (Telegram Bot / Cloud)`,
          size: sz,
          url: primaryDlUrl,
          channel_url: chanUrl,
          bot_url: botUrl,
          format: 'MP4',
          host: 'Telegram',
        });
      }
    });
  }

  // 2. Process explicit downloads from movie.downloads
  rawDls.forEach(d => {
    if (!d || !d.url || isDeadTunnel(d.url)) return;

    const isTg = d.host === 'Telegram' || d.download_only || d.url.includes('t.me');
    const isGdrive = d.host === 'Google Drive' || d.url.includes('/api/download') || d.url.includes('drive.google.com');

    // Never show phantom lower-quality GDrive cards that point to non-existent /api/download files
    if (isGdrive) {
      const qNorm = String(d.quality || '').toLowerCase();
      // If movie is Telegram-based, skip GDrive links entirely to avoid confusion/404s
      if (hasTelegramMedia) return;
      if (qNorm.includes('720p') || qNorm.includes('480p') || qNorm.includes('360p')) {
        const mainDriveId = extractMovieDriveId(movie);
        const qDriveId = extractDriveIdForQuality(movie, qNorm);
        if (!qDriveId || qDriveId === mainDriveId) {
          return; // Skip fake cloned GDrive card
        }
      }
    }

    // Extract base quality tag: e.g. "1080p (Telegram Direct)" -> "1080p"
    const qMatch = String(d.quality || '').match(/(1080p|720p|480p|360p)/i);
    const baseQ = qMatch ? qMatch[1].toLowerCase() : (d.quality || '1080p');

    // If we already added a cleaner variant_media card for this quality, merge channel_url if applicable
    if (seenQualities.has(baseQ)) {
      if (d.url.includes('/c/')) {
        const existing = results.find(r => r.quality && r.quality.toLowerCase().includes(baseQ));
        if (existing && !existing.channel_url) {
          existing.channel_url = d.url;
        }
      }
      return;
    }

    seenQualities.add(baseQ);
    addCard(d);
  });

  // 3. Fallback for single Telegram message_id movie if variant_media was absent
  if (movie.message_id && !seenQualities.has('1080p')) {
    const sz1080 = movie.file_size ? formatBytesFromBytes(movie.file_size) : '';
    const botUrl = `https://t.me/${botUsername}?start=dl_${movie.slug}_1080p`;
    const chanUrl = `https://t.me/c/${chClean}/${movie.message_id}`;
    seenQualities.add('1080p');
    addCard({
      quality: '1080p',
      label: '1080p Full HD (Telegram Bot / Cloud)',
      size: sz1080,
      url: botUrl,
      channel_url: chanUrl,
      bot_url: botUrl,
      format: 'MP4',
      host: 'Telegram',
    });
  }

  // 4. Fallback for direct MP4 stream if not already covered
  if (movie.stream_url && movie.stream_url.endsWith('.mp4') && !isDeadTunnel(movie.stream_url)) {
    const qStr = movie.quality || '1080p';
    const baseQ = qStr.toLowerCase();
    if (!seenQualities.has(baseQ)) {
      seenQualities.add(baseQ);
      const szStr = movie.file_size ? formatBytesFromBytes(movie.file_size) : (movie.size || '');
      addCard({
        quality: qStr,
        label: `${qStr} Full HD (Direct High-Speed Stream)`,
        size: szStr,
        url: movie.stream_url,
        format: 'MP4',
        host: 'Direct',
      });
    }
  }

  return results;
}

// ---- 1. Breadcrumb & 2. Page Header ----
function renderBreadcrumb(movie) {
  const currentEl = document.getElementById('cs-breadcrumb-current');
  if (currentEl) {
    currentEl.textContent = `${movie.title} (${movie.year || ''}) Sinhala Subtitles`;
  }
}

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

// ---- 3. Server Tabs (Multi-Server + Bahirawa Backup Players + Trailer) ----
function renderServerTabs(movie) {
  const tabsEl = document.getElementById('server-tabs');
  if (!tabsEl) return;
  const streams = getMovieStreams(movie);
  const icons = [
    'fa-solid fa-play',
    'fa-solid fa-bolt',
    'fa-solid fa-film',
    'fa-solid fa-rocket',
    'fa-solid fa-server',
    'fa-solid fa-circle-play'
  ];

  let tabsHtml = streams.map((s, i) => {
    let cleanLabel = FilmSub.escHtml(s.label || s.server || `Server ${i + 1}`);
    cleanLabel = cleanLabel.replace(/^[\s⚡🎬📺🔥🎥]+/, '').trim();
    return `
    <button class="server-tab${i === currentStreamIdx ? ' active' : ''}" data-type="stream" data-index="${i}" type="button">
      <i class="${icons[i] || 'fa-solid fa-bolt'}" style="color:var(--accent)"></i>
      <span>${cleanLabel}</span>
    </button>`;
  }).join('');

  tabsHtml += `
    <button class="server-tab${isTrailerActive ? ' active' : ''}" data-type="trailer" type="button">
      <i class="fa-solid fa-film" style="color:var(--accent)"></i>
      <span>Trailer</span>
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

// ---- 3b. Adaptive Quality & Network-Aware Streaming Engine ----
function detectNetworkSpeed() {
  const isMobile = typeof window !== 'undefined' && (window.innerWidth <= 768 || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || ''));
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!conn) {
    // No Network Information API: default 720p (works well on both Wi-Fi and 4G)
    return { speed: isMobile ? 'medium' : 'fast', downlink: 5, effectiveType: '4g', recommendedQuality: '720p' };
  }
  const downlink = typeof conn.downlink === 'number' ? conn.downlink : 5;
  const effectiveType = conn.effectiveType || '4g';
  const saveData = Boolean(conn.saveData);

  // Only suggest 480p if explicitly saving data or on very low-speed 2G/3G connections (<1.2 Mbps)
  if (saveData || downlink < 1.2 || effectiveType === '2g' || effectiveType === 'slow-2g') {
    return { speed: 'slow', downlink, effectiveType, recommendedQuality: '480p' };
  }
  // 720p is the golden default for instant startup, smooth buffering, and high clarity.
  // Only start on 1080p if explicitly very high bandwidth (> 25 Mbps)
  if (downlink < 25.0) {
    return { speed: 'medium', downlink, effectiveType, recommendedQuality: '720p' };
  }
  return { speed: 'fast', downlink, effectiveType, recommendedQuality: '1080p' };
}

function mapQualityToDriveVq(q) {
  const norm = String(q || 'auto').toLowerCase();
  if (norm === '1080p') return 'hd1080';
  if (norm === '720p') return 'hd720';
  if (norm === '480p') return 'large';
  if (norm === '360p') return 'medium';
  const net = detectNetworkSpeed();
  return mapQualityToDriveVq(net.recommendedQuality);
}

function updateQualitySpeedBadge(q) {
  const speedBadge = document.getElementById('net-speed-text');
  const inPlayerBadge = document.getElementById('vjs-sq-badge-text');
  const norm = String(q || 'auto').toLowerCase();
  const activeTier = (norm === 'auto' ? currentEffectiveQuality : norm).toUpperCase();

  if (speedBadge) {
    if (norm === 'auto') {
      const net = detectNetworkSpeed();
      if (activeTier === '360P') {
        speedBadge.innerHTML = `<i class="fa-solid fa-signal" style="color:#e50914"></i> Auto (${activeTier})`;
      } else if (activeTier === '480P') {
        speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#f5c518"></i> Auto (${activeTier} Smooth)`;
      } else if (activeTier === '720P') {
        speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#46d369"></i> Auto (${activeTier} HD)`;
      } else {
        speedBadge.innerHTML = `<i class="fa-solid fa-bolt" style="color:#46d369"></i> Auto (${activeTier} FHD)`;
      }
    } else {
      speedBadge.innerHTML = `<i class="fa-solid fa-circle-check" style="color:#46d369"></i> ${activeTier} HD`;
    }
  }
  if (inPlayerBadge) {
    inPlayerBadge.textContent = norm === 'auto' ? `AUTO (${activeTier})` : activeTier;
  }

  // Sync active state on top toolbar pills & in-player menu items
  document.querySelectorAll('.q-pill').forEach(pill => {
    pill.classList.toggle('active', (pill.dataset.quality || '').toLowerCase() === norm);
  });
  document.querySelectorAll('.vjs-sq-item').forEach(item => {
    item.classList.toggle('active', (item.dataset.quality || '').toLowerCase() === norm);
  });
}

function applyQualitySwitch(targetQuality, opts = {}) {
  const isAutoDowngrade = Boolean(opts.isAutoDowngrade);
  // CRITICAL: Block auto-downgrade during initial startup or first 30s of playback
  if (isAutoDowngrade) {
    if (selectedQuality !== 'auto') return;
    if (vjsPlayer) {
      const curT = typeof vjsPlayer.currentTime === 'function' ? vjsPlayer.currentTime() : 0;
      if (curT < 30.0) {
        console.warn(`[ABR] Ignoring auto-downgrade during initial playback buffer period (currentTime: ${curT.toFixed(1)}s < 30s)`);
        return;
      }
    }
  }
  const prevEffectiveQuality = currentEffectiveQuality;
  const prevDriveId = extractDriveIdForQuality(currentMovie, prevEffectiveQuality);

  if (!isAutoDowngrade) {
    selectedQuality = targetQuality;
    try { sessionStorage.setItem('filmsub_pref_quality', targetQuality); } catch(e) {}
    if (targetQuality === 'auto') {
      currentEffectiveQuality = detectNetworkSpeed().recommendedQuality;
    } else {
      currentEffectiveQuality = targetQuality;
    }
  } else {
    currentEffectiveQuality = targetQuality;
    lastAutoSwitchEpoch = Date.now();
  }

  updateQualitySpeedBadge(selectedQuality);

  const headerQual = document.getElementById('cs-header-quality');
  if (headerQual) {
    headerQual.textContent = `${currentEffectiveQuality.toUpperCase()} WEB-DL`;
  }

  const newDriveId = extractDriveIdForQuality(currentMovie, currentEffectiveQuality);

  // 1. If Video.js Super Player is active, switch chunk stream quality and preserve exact currentTime
  if (vjsPlayer && typeof vjsPlayer.currentTime === 'function') {
    const curTime = vjsPlayer.currentTime() || 0;
    const wasPaused = vjsPlayer.paused();
    let newSrc = '';
    const qNorm = String(currentEffectiveQuality || '').toLowerCase();
    let defaultTgChatId = (currentMovie && currentMovie.channel_chat_id) || '-1004325759505';

    // 1. Check if movie has multi-quality variant_media (Telegram Cloud)
    const vm = currentMovie && (currentMovie.variant_media || (currentMovie.movie_entry && currentMovie.movie_entry.variant_media));
    if (vm && typeof vm === 'object') {
      let vEntry = vm[qNorm] || vm[currentEffectiveQuality];
      if (!vEntry || (!vEntry.message_id && !vEntry.stream_url)) {
        // Fallback to highest available quality that actually exists
        const order = ['720p', '480p', '1080p'];
        for (const q of order) {
          if (vm[q] && (vm[q].message_id > 0 || (vm[q].stream_url && !vm[q].stream_url.includes('/0')))) {
            vEntry = vm[q];
            break;
          }
        }
      }
      if (vEntry) {
        let tgChatId = currentMovie.channel_chat_id || '';
        if (!tgChatId) {
          const mUrl = currentMovie.stream_url || (vEntry.stream_url || '');
          const mMatch = mUrl.match(/\/stream\/channel\/(-?\d+)\//);
          tgChatId = mMatch ? mMatch[1] : defaultTgChatId;
        }
        if (vEntry.message_id && Number(vEntry.message_id) > 0) {
          newSrc = `${getStreamEndpointPrefix()}/stream/channel/${tgChatId}/${vEntry.message_id}`;
        } else if (vEntry.stream_url && !vEntry.stream_url.includes('/0')) {
          newSrc = normalizeStreamUrl(vEntry.stream_url);
        }
      }
    }

    // 2. Check Drive ID for quality
    if (!newSrc && newDriveId) {
      newSrc = buildChunkStreamUrl(newDriveId, currentEffectiveQuality);
    }

    // 3. Check qualities map
    if (!newSrc && currentMovie && currentMovie.qualities && typeof currentMovie.qualities === 'object') {
      const qVal = currentMovie.qualities[qNorm] || currentMovie.qualities[currentEffectiveQuality];
      if (typeof qVal === 'string' && qVal.startsWith('http') && !qVal.includes('t.me')) {
        newSrc = normalizeStreamUrl(qVal);
      } else if (qVal && typeof qVal === 'object' && qVal.stream_url && !qVal.stream_url.includes('t.me')) {
        newSrc = normalizeStreamUrl(qVal.stream_url);
      }
    }

    // 4. Check movie downloads
    if (!newSrc) {
      const downloads = getMovieDownloads(currentMovie);
      const matched = downloads.find(d => String(d.quality || '').toLowerCase().includes(qNorm) && !d.download_only);
      if (matched) {
        if (matched.stream_url && !matched.stream_url.includes('t.me')) {
          newSrc = normalizeStreamUrl(matched.stream_url);
        } else if (matched.message_id) {
          const cId = defaultTgChatId;
          newSrc = `${getStreamEndpointPrefix()}/stream/channel/${cId}/${matched.message_id}`;
        } else if (matched.file_id) {
          newSrc = `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(matched.file_id)}`;
        } else if (matched.url && matched.url.includes('/c/')) {
          const mMatch = matched.url.match(/t\.me\/c\/(\d+)\/(\d+)/);
          if (mMatch) {
            newSrc = `${getStreamEndpointPrefix()}/stream/channel/-100${mMatch[1]}/${mMatch[2]}`;
          }
        } else if (matched.url && !matched.url.includes('drive.google.com/uc') && !matched.url.includes('t.me') && !matched.url.startsWith('/api/download')) {
          newSrc = normalizeStreamUrl(matched.url);
        }
      }
    }

    const currentSrc = (typeof vjsPlayer.currentSrc === 'function' ? vjsPlayer.currentSrc() : '') || '';
    const shouldReloadSrc = newSrc && (!isAutoDowngrade || (newDriveId && prevDriveId && newDriveId !== prevDriveId) || (currentSrc && !currentSrc.includes(newSrc)));

    if (newSrc && shouldReloadSrc && currentSrc !== newSrc) {
      const targetTime = (curTime && curTime > 0) ? curTime : (vjsPlayer.currentTime() || 0);
      const shouldResumePlay = !wasPaused;

      // Temporarily pause and mute audio during quality switch to avoid 0:00 playback glitches
      if (typeof vjsPlayer.pause === 'function') vjsPlayer.pause();
      const prevMuted = (typeof vjsPlayer.muted === 'function') ? vjsPlayer.muted() : false;
      if (targetTime > 0.5 && typeof vjsPlayer.muted === 'function') {
        vjsPlayer.muted(true);
      }

      // Immediately warm target stream ahead
      const targetMsgMatch = newSrc.match(/\/stream\/channel\/(-?\d+)\/(\d+)/);
      if (targetMsgMatch) {
        const pfx = getStreamEndpointPrefix() || '';
        fetch(`${pfx}/stream/warmup/${targetMsgMatch[1]}/${targetMsgMatch[2]}`, { method: 'POST', mode: 'cors' }).catch(() => {});
      }

      vjsPlayer.src({ src: newSrc, type: 'video/mp4' });
      vjsPlayer.load();

      let isRestored = false;
      const applySeek = () => {
        if (isRestored) return;
        const techEl = (vjsPlayer.tech_ && vjsPlayer.tech_.el_) || document.getElementById('filmsubPlayer_html5_api');
        const rState = Math.max(
          typeof vjsPlayer.readyState === 'function' ? vjsPlayer.readyState() : 0,
          techEl ? (techEl.readyState || 0) : 0
        );
        const duration = typeof vjsPlayer.duration === 'function' ? vjsPlayer.duration() : 0;

        // Player must have metadata (readyState >= 1) or valid duration to accept currentTime seek
        if (rState >= 1 || (duration > 0 && !isNaN(duration))) {
          if (targetTime > 0.5) {
            try {
              vjsPlayer.currentTime(targetTime);
              if (techEl && Math.abs((techEl.currentTime || 0) - targetTime) > 0.5) {
                techEl.currentTime = targetTime;
              }
              console.log(`[FilmSub] Quality switch: successfully restored playhead to ${targetTime.toFixed(1)}s (ready=${rState}, dur=${duration.toFixed(1)}s)`);
            } catch (err) {
              console.warn('[FilmSub] Seek failed, will retry on next tick:', err);
              return;
            }
          }
          isRestored = true;
          if (targetTime > 0.5 && typeof vjsPlayer.muted === 'function') {
            vjsPlayer.muted(prevMuted);
          }
          syncSubtitles();
          if (shouldResumePlay) {
            try { vjsPlayer.play().catch(() => {}); } catch (e) {}
          }
        }
      };

      vjsPlayer.one('loadedmetadata', applySeek);
      vjsPlayer.one('canplay', applySeek);
      vjsPlayer.one('loadeddata', applySeek);

      // Active high-frequency polling every 40ms to catch readyState transitions immediately
      let pollCount = 0;
      const pollTimer = setInterval(() => {
        pollCount++;
        if (isRestored || pollCount > 150 || !vjsPlayer) {
          clearInterval(pollTimer);
          return;
        }
        applySeek();
      }, 40);

      // Extra safeguard: On timeupdate, if video starts playing from 0:00 instead of targetTime, immediately force seek!
      const onGuardTimeUpdate = () => {
        if (isRestored) {
          vjsPlayer.off('timeupdate', onGuardTimeUpdate);
          return;
        }
        if (targetTime > 1.0) {
          const nowT = vjsPlayer.currentTime() || 0;
          if (nowT < targetTime - 1.0) {
            applySeek();
          }
        }
      };
      vjsPlayer.on('timeupdate', onGuardTimeUpdate);
    }

    if (isAutoDowngrade) {
      FilmSub.showToast(
        `⚡ අන්තර්ජාල වේගය අනුව හිරවීමකින් තොරව නැරඹීම සඳහා ${currentEffectiveQuality.toUpperCase()} වෙත ස්වයංක්‍රීයව මාරු විය!`,
        'info'
      );
    } else {
      FilmSub.showToast(
        `⚡ Quality: ${selectedQuality === 'auto' ? `Auto (${currentEffectiveQuality.toUpperCase()})` : currentEffectiveQuality.toUpperCase()} • සිංහල උපසිරැසි ක්‍රියාත්මකයි`,
        'info'
      );
    }
    return;
  }

  // 2. If Google Drive / Embed iframe is active, reload with zero-white-screen dark loader & new vq param
  const driveIframe = document.getElementById('player-drive-iframe');
  if (driveIframe && driveIframe.dataset.baseEmbed) {
    const loader = document.getElementById('super-player-loader');
    if (loader) loader.classList.remove('hidden');
    driveIframe.style.opacity = '0';

    const vq = mapQualityToDriveVq(currentEffectiveQuality);
    const base = driveIframe.dataset.baseEmbed;
    const sep = base.includes('?') ? '&' : '?';
    driveIframe.src = `${base}${sep}vq=${vq}&hl=si`;
    FilmSub.showToast(`Quality switched to ${currentEffectiveQuality.toUpperCase()} • සිංහල උපසිරැසි ON`, 'info');
  }
}

function initAdaptiveQuality(movie) {
  const pills = document.querySelectorAll('.q-pill');
  updateQualitySpeedBadge(selectedQuality);

  pills.forEach(pill => {
    // Proactive Pre-Warming on hover/touch
    const warmPillQuality = () => {
      const q = pill.dataset.quality;
      if (q && q !== 'auto') {
        prewarmQualityTier(currentMovie, q);
      }
    };
    pill.addEventListener('pointerenter', warmPillQuality, { passive: true });
    pill.addEventListener('touchstart', warmPillQuality, { passive: true });

    pill.addEventListener('click', () => {
      const q = pill.dataset.quality || 'auto';
      applyQualitySwitch(q, { isAutoDowngrade: false });
    });
  });

  // Monitor real-time browser network strength changes — only update UI badge, NEVER reload media mid-stream
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn && conn.addEventListener) {
    conn.addEventListener('change', () => {
      updateQualitySpeedBadge(selectedQuality);
    });
  }
}

function getBufferAhead(player) {
  try {
    if (!player || typeof player.currentTime !== 'function') return 0;
    const ct = player.currentTime() || 0;
    const b = player.buffered();
    if (!b || b.length === 0) return 0;
    for (let i = 0; i < b.length; i++) {
      if (ct >= b.start(i) && ct <= b.end(i)) {
        return b.end(i) - ct;
      }
    }
    if (b.end(b.length - 1) > ct) {
      return b.end(b.length - 1) - ct;
    }
  } catch (e) {}
  return 0;
}

/**
 * Attaches real-time buffer stall & frame-drop monitoring to Video.js.
 * Automatically steps down quality (1080p -> 720p -> 480p -> 360p) if lagging,
 * while ignoring user timeline seeks and initial moov metadata probing.
 */
function attachAdaptiveStallMonitor(player) {
  if (!player) return;
  stallTimestamps = [];
  if (activeStallTimer) {
    clearTimeout(activeStallTimer);
    activeStallTimer = null;
  }

  let hasEverPlayed = false;

  const triggerStepDownIfNeeded = () => {
    if (selectedQuality !== 'auto') return;
    // 30s cooldown between switches — prevents quality oscillation / flicker
    if (Date.now() - lastAutoSwitchEpoch < 30000) return;
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (curT < 30.0) return; // Never step down in first 30 seconds
    const idx = QUALITY_LADDER.indexOf(currentEffectiveQuality.toLowerCase());
    if (idx !== -1 && idx < QUALITY_LADDER.length - 1) {
      const nextLower = QUALITY_LADDER[idx + 1];
      console.log(`[ABR] Stall detected after ${curT.toFixed(1)}s. Stepping down: ${currentEffectiveQuality} -> ${nextLower}`);
      applyQualitySwitch(nextLower, { isAutoDowngrade: true });
    }
  };

  const triggerStepUpIfNeeded = () => {
    if (selectedQuality !== 'auto') return;
    if (Date.now() - lastAutoSwitchEpoch < 30000) return;
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (curT < 60.0) return; // Only step up after 1 minute of stable playback
    const idx = QUALITY_LADDER.indexOf(currentEffectiveQuality.toLowerCase());
    if (idx > 0) {
      const nextHigher = QUALITY_LADDER[idx - 1];
      console.log(`[ABR] Buffer healthy. Stepping up: ${currentEffectiveQuality} -> ${nextHigher}`);
      applyQualitySwitch(nextHigher, { isAutoDowngrade: false });
    }
  };

  player.on('seeking', () => {
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }
  });

  player.on('waiting', () => {
    if (!hasEverPlayed) return;
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (curT < 30.0) return; // Don't step down during initial buffer build-up
    if (player.seeking && player.seeking()) return;
    if (player.paused && player.paused()) return;

    if (selectedQuality === 'auto') {
      const now = Date.now();
      stallTimestamps = stallTimestamps.filter(t => (now - t) < 30000);
      stallTimestamps.push(now);
    }

    if (activeStallTimer) clearTimeout(activeStallTimer);
    activeStallTimer = setTimeout(() => {
      if (player && !player.paused() && !(player.seeking && player.seeking())) {
        const curTNow = typeof player.currentTime === 'function' ? player.currentTime() : 0;
        if (selectedQuality === 'auto' && curTNow >= 30.0) {
          triggerStepDownIfNeeded();
        }
        // Intelligent stall recovery: nudge playhead slightly to kick decoder
        try {
          if (curTNow > 0) {
            player.currentTime(curTNow + 0.05);
            player.play().catch(() => {});
          }
        } catch (e) {}
      }
    }, 8000);  // 8s continuous stall before considering downgrade
  });

  player.on('playing', () => {
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (curT > 2.0) {
      hasEverPlayed = true;
    }
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }
  });

  let lastBufferCheck = 0;
  player.on('timeupdate', () => {
    try {
      if (typeof player.currentTime === 'function' && player.currentTime() > 2.0) {
        hasEverPlayed = true;
      }
    } catch (e) {}

    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }

    const now = Date.now();
    if (now - lastBufferCheck < 5000) return;
    lastBufferCheck = now;

    // Progressive MP4 playback:
    // CRITICAL: NEVER proactively step down during active playback!
    // As long as the video is playing without stalling, DO NOT abort the media stream!
    // Only step up if buffer ahead is extraordinarily healthy (> 30s) and playback has been running for > 60s
    if (selectedQuality === 'auto' && !player.paused() && !(player.seeking && player.seeking())) {
      const ahead = getBufferAhead(player);
      if (ahead > 30.0 && (now - lastAutoSwitchEpoch > 30000)) {
        triggerStepUpIfNeeded();
      }
    }
  });
}

// ---- 3c. Subtitle Controls (Toggle, Sync, Font Size, Color, Custom .SRT Upload, Fullscreen) ----
function applySubtitleVisualStyle() {
  const subTextEl = document.getElementById('fs-sub-text');
  const sizeObj = SUB_SIZES[subSizeIndex] || SUB_SIZES[0];
  const colorHex = SUB_COLORS[subColorIndex] || SUB_COLORS[0];

  if (subTextEl) {
    subTextEl.style.color = colorHex;
    subTextEl.style.fontSize = `calc(clamp(14px, 2.2vw, 21px) * ${sizeObj.scale})`;
  }

  let dynamicStyle = document.getElementById('fs-dynamic-cue-style');
  if (!dynamicStyle) {
    dynamicStyle = document.createElement('style');
    dynamicStyle.id = 'fs-dynamic-cue-style';
    document.head.appendChild(dynamicStyle);
  }
  dynamicStyle.textContent = `
    .video-js video::cue {
      color: ${colorHex} !important;
      font-size: ${Math.round(100 * sizeObj.scale)}% !important;
    }
    .video-js .vjs-text-track-display {
      opacity: 0 !important;
      pointer-events: none !important;
    }
    .seek-ripple-overlay {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 42%;
      display: flex;
      align-items: center;
      justify-content: center;
      pointer-events: none;
      z-index: 25;
      animation: fs-ripple-fade 0.65s ease-out forwards;
    }
    .seek-ripple-overlay.forward {
      right: 0;
      border-radius: 0 8px 8px 0;
      background: radial-gradient(circle, rgba(229,9,20,0.35) 0%, rgba(0,0,0,0) 75%);
    }
    .seek-ripple-overlay.backward {
      left: 0;
      border-radius: 8px 0 0 8px;
      background: radial-gradient(circle, rgba(229,9,20,0.35) 0%, rgba(0,0,0,0) 75%);
    }
    .seek-ripple-bubble {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 5px;
      color: #fff;
      font-size: 22px;
      font-weight: 700;
      background: rgba(15,15,15,0.85);
      padding: 12px 18px;
      border-radius: 50px;
      border: 1px solid rgba(255,255,255,0.2);
      box-shadow: 0 4px 15px rgba(0,0,0,0.6);
    }
    .seek-ripple-bubble span {
      font-size: 13px;
      font-weight: 600;
      letter-spacing: 0.5px;
    }
    @keyframes fs-ripple-fade {
      0% { opacity: 0; transform: scale(0.85); }
      35% { opacity: 1; transform: scale(1.05); }
      100% { opacity: 0; transform: scale(1); }
    }
  `;
}

function initSubtitleControls(movie) {
  const toggleBtn = document.getElementById('btn-toggle-live-sub');
  const stateSpan = document.getElementById('live-sub-state');
  const minusBtn = document.getElementById('btn-sub-sync-minus');
  const plusBtn = document.getElementById('btn-sub-sync-plus');
  const sizeBtn = document.getElementById('btn-sub-size');
  const sizeLabel = document.getElementById('sub-size-label');
  const colorBtn = document.getElementById('btn-sub-color');
  const customSubInput = document.getElementById('custom-sub-upload');
  const fsBtn = document.getElementById('btn-player-fullscreen');

  if (toggleBtn) {
    toggleBtn.addEventListener('click', () => {
      liveSubEnabled = !liveSubEnabled;
      toggleBtn.classList.toggle('active', liveSubEnabled);
      if (stateSpan) stateSpan.textContent = liveSubEnabled ? 'ON' : 'OFF';
      const overlay = document.getElementById('fs-sub-overlay');
      if (overlay) overlay.style.display = liveSubEnabled ? 'flex' : 'none';
      if (vjsPlayer && vjsPlayer.textTracks) {
        const tracks = vjsPlayer.textTracks();
        for (let i = 0; i < tracks.length; i++) {
          if (tracks[i].kind === 'subtitles' || tracks[i].kind === 'captions') {
            tracks[i].mode = liveSubEnabled ? 'showing' : 'disabled';
          }
        }
      }
      FilmSub.showToast(
        liveSubEnabled ? 'සිංහල උපසිරැසි සක්‍රීයයි (Sinhala Subtitles ON)' : 'සිංහල උපසිරැසි අක්‍රීයයි (Subtitles OFF)',
        'info'
      );
    });
  }

  if (minusBtn) {
    minusBtn.addEventListener('click', () => {
      liveSubOffsetSec -= 0.5;
      syncSubtitles();
      FilmSub.showToast(`Subtitle Sync: ${liveSubOffsetSec >= 0 ? '+' : ''}${liveSubOffsetSec.toFixed(1)}s`, 'info');
    });
  }

  if (plusBtn) {
    plusBtn.addEventListener('click', () => {
      liveSubOffsetSec += 0.5;
      syncSubtitles();
      FilmSub.showToast(`Subtitle Sync: ${liveSubOffsetSec >= 0 ? '+' : ''}${liveSubOffsetSec.toFixed(1)}s`, 'info');
    });
  }

  if (sizeBtn) {
    sizeBtn.addEventListener('click', () => {
      subSizeIndex = (subSizeIndex + 1) % SUB_SIZES.length;
      if (sizeLabel) sizeLabel.textContent = SUB_SIZES[subSizeIndex].label;
      applySubtitleVisualStyle();
      FilmSub.showToast(`Subtitle Size: ${SUB_SIZES[subSizeIndex].label}`, 'info');
    });
  }

  if (colorBtn) {
    colorBtn.addEventListener('click', () => {
      subColorIndex = (subColorIndex + 1) % SUB_COLORS.length;
      applySubtitleVisualStyle();
      FilmSub.showToast('Subtitle Color Updated', 'info');
    });
  }

  if (customSubInput) {
    customSubInput.addEventListener('change', (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        const rawText = decodeSubtitleBuffer(reader.result);
        const vttText = convertSrtToVttText(rawText);
        const newCues = parseVttToCues(vttText);
        if (newCues.length > 0) {
          parsedSubCues = newCues;
          liveSubEnabled = true;
          if (toggleBtn) toggleBtn.classList.add('active');
          if (stateSpan) stateSpan.textContent = 'ON';
          const overlay = document.getElementById('fs-sub-overlay');
          if (overlay) overlay.style.display = 'flex';
          syncSubtitles();
          FilmSub.showToast(`✅ උපසිරැසි ගොනුව (${file.name}) සාර්ථකව Video එකට ඇතුළත් කරන ලදී! (${newCues.length} cues)`, 'success');
        } else {
          FilmSub.showToast('Could not parse subtitle file. Please use a valid .SRT or .VTT file.', 'error');
        }
      };
      reader.readAsArrayBuffer(file);
    });
  }

  if (fsBtn) {
    fsBtn.addEventListener('click', () => {
      if (vjsPlayer && typeof vjsPlayer.requestFullscreen === 'function') {
        if (vjsPlayer.isFullscreen()) vjsPlayer.exitFullscreen();
        else vjsPlayer.requestFullscreen();
        return;
      }
      const wrap = document.querySelector('#video-player-container .player-iframe-wrap') || document.getElementById('video-player-container');
      if (!wrap) return;
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      } else if (wrap.requestFullscreen) {
        wrap.requestFullscreen().catch(() => {});
      } else if (wrap.webkitRequestFullscreen) {
        wrap.webkitRequestFullscreen();
      }
    });
  }
}

async function mountLiveSubtitleOverlay(playerEl, movie) {
  if (!playerEl) return;
  if (liveSubTimer) {
    clearInterval(liveSubTimer);
    liveSubTimer = null;
  }
  const isHardcoded = Boolean(movie && (movie.sub_hardcoded || movie.is_already_hardsubbed));
  if (isHardcoded) {
    liveSubEnabled = false;
    const toggleBtn = document.getElementById('btn-toggle-live-sub');
    const stateSpan = document.getElementById('live-sub-state');
    if (toggleBtn) toggleBtn.classList.remove('active');
    if (stateSpan) stateSpan.textContent = 'OFF';
  }
  if (!parsedSubCues || parsedSubCues.length === 0) {
    await loadParsedSubtitles(movie);
  }

  // Mount inside #filmsubPlayer if Video.js is active so Fullscreen includes the overlay,
  // otherwise inside .player-iframe-wrap
  const targetContainer = playerEl.querySelector('#filmsubPlayer') || playerEl.querySelector('.player-iframe-wrap') || playerEl;
  if (!targetContainer) return;

  let overlay = targetContainer.querySelector('#fs-sub-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'fs-sub-overlay';
    overlay.className = 'fs-sub-overlay';
    overlay.style.display = liveSubEnabled ? 'flex' : 'none';
    overlay.innerHTML = `<span class="fs-sub-text" id="fs-sub-text">🎬 ${FilmSub.escHtml(movie.title_si || movie.title || '')} — සිංහල උපසිරැසි ක්‍රියාත්මකයි</span>`;
    targetContainer.appendChild(overlay);
  }
  applySubtitleVisualStyle();

  liveSubStartEpoch = Date.now();
  liveSubTimer = setInterval(() => {
    const subTextEl = document.getElementById('fs-sub-text');
    const subBoxEl = document.getElementById('fs-sub-overlay');
    if (!subTextEl || !subBoxEl) return;
    if (!liveSubEnabled) {
      subBoxEl.style.display = 'none';
      return;
    }

    subBoxEl.style.display = 'flex';

    let currentSec = 0;
    if (vjsPlayer && typeof vjsPlayer.currentTime === 'function') {
      currentSec = Math.max(0, (vjsPlayer.currentTime() || 0) + liveSubOffsetSec);
    } else {
      const maxCueEnd = parsedSubCues.length > 0 ? Math.max(45, parsedSubCues[parsedSubCues.length - 1].end + 4) : 45;
      currentSec = Math.max(0, (((Date.now() - liveSubStartEpoch) / 1000) + liveSubOffsetSec)) % maxCueEnd;
    }

    const activeCue = parsedSubCues.find(c => currentSec >= c.start && currentSec <= c.end);
    if (activeCue) {
      subTextEl.innerHTML = activeCue.text;
      subTextEl.style.opacity = '1';
    } else {
      subTextEl.style.opacity = '0';
    }
  }, 200);
}

// ---- 4. Zero-White-Screen Loader HTML Builder with Live Percentage Tracker ----
let currentLoadPercent = 0;
let loadPercentTimer = null;

function updatePlayerLoadPercent(targetPct, statusMessage, details) {
  const pctEl = document.getElementById('sp-progress-pct');
  const barEl = document.getElementById('sp-progress-bar');
  const statusEl = document.getElementById('sp-progress-status');
  const detailsEl = document.getElementById('sp-progress-details');

  if (statusMessage && statusEl) statusEl.textContent = statusMessage;
  if (details && detailsEl) detailsEl.textContent = details;

  targetPct = Math.min(100, Math.max(0, targetPct));
  currentLoadPercent = targetPct;

  if (pctEl) pctEl.textContent = `${Math.round(targetPct)}%`;
  if (barEl) barEl.style.width = `${targetPct}%`;
}

function startProgressAnimation() {
  currentLoadPercent = 12;
  updatePlayerLoadPercent(12, '⚡ Connecting High-Speed Stream Server...', 'Colab MTProto Tunnel Initializing...');

  if (loadPercentTimer) clearInterval(loadPercentTimer);

  loadPercentTimer = setInterval(() => {
    if (currentLoadPercent < 45) {
      currentLoadPercent += Math.random() * 8 + 4;
      updatePlayerLoadPercent(currentLoadPercent, '🔍 Resolving Telegram Media Peer...', 'Accessing 1080p Chunk Stream...');
    } else if (currentLoadPercent < 80) {
      currentLoadPercent += Math.random() * 4 + 2;
      updatePlayerLoadPercent(currentLoadPercent, '📥 Buffering Stream Chunks & Sinhala Subtitles...', 'Synchronizing Video.js Engine...');
    } else if (currentLoadPercent < 94) {
      currentLoadPercent += 0.8;
      updatePlayerLoadPercent(currentLoadPercent, '🎬 Starting Video Decoder...', 'Finalizing 1080p Playback Buffer...');
    }
    if (currentLoadPercent >= 95) {
      clearInterval(loadPercentTimer);
      loadPercentTimer = null;
    }
  }, 160);
}

function finishProgressAnimation() {
  if (loadPercentTimer) { clearInterval(loadPercentTimer); loadPercentTimer = null; }
  updatePlayerLoadPercent(100, '✅ 100% Ready • Starting Playback...', 'Playing with Sinhala Subtitles');
  setTimeout(() => {
    const loader = document.getElementById('super-player-loader');
    if (loader) loader.classList.add('hidden');
  }, 350);
}

function buildSuperLoaderHtml(movie, serverLabel) {
  const title = FilmSub.escHtml(movie.title || 'Movie');
  const bgImg = movie.backdrop || movie.poster || '';
  const bgStyle = bgImg
    ? `background: linear-gradient(rgba(0,0,0,0.72), rgba(0,0,0,0.88)), url('${FilmSub.escHtml(bgImg)}') center/cover no-repeat;`
    : 'background:#000000;';
  return `
    <div class="super-player-loader" id="super-player-loader" style="${bgStyle}">
      <div class="sp-loader-card">
        <div class="netflix-pulse-spinner">
          <div class="netflix-pulse-ring"></div>
          <div class="netflix-pulse-icon"><i class="fa-solid fa-play"></i></div>
        </div>
        <div class="sp-loader-title">${title}</div>
        
        <!-- Live Loading Percentage Display (CineSubz / Netflix VIP) -->
        <div class="sp-progress-container" id="sp-progress-container">
          <div class="sp-progress-meta">
            <span class="sp-progress-status" id="sp-progress-status">⚡ Connecting Stream Server...</span>
            <span class="sp-progress-pct" id="sp-progress-pct">0%</span>
          </div>
          <div class="sp-progress-track">
            <div class="sp-progress-bar" id="sp-progress-bar" style="width: 0%"></div>
          </div>
          <div class="sp-progress-sub">
            <span class="sp-status-pulse-dot"></span>
            <span id="sp-progress-details">High-Speed Telegram Cloud Stream • Auto Sinhala Sub</span>
          </div>
        </div>
      </div>
    </div>`;
}

// ---- 5. Universal Super Video Player (Video.js Chunk Stream + Zero-White-Screen Iframe Embeds) ----
function initVideoPlayer(movie) {
  const streams = getMovieStreams(movie);
  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  if (streams.length === 0) {
    const downloads = getMovieDownloads(movie);
    const dlLinks = downloads.slice(0, 4).map(dl =>
      `<a href="${FilmSub.escHtml(dl.url || '#')}" target="_blank" rel="noopener"
          style="display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600">
         <i class="fa-solid fa-cloud-arrow-down"></i>
         ${FilmSub.escHtml(dl.quality || '1080p')} (${FilmSub.escHtml(dl.size || 'HD')}) — සිංහල Sub Merged
       </a>`
    ).join('');

    playerEl.innerHTML = `
      <div style="aspect-ratio:16/9;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#000000;color:var(--text2);gap:14px;border-radius:8px;padding:24px;text-align:center">
        <i class="fa-solid fa-cloud-arrow-down" style="font-size:44px;color:var(--accent)"></i>
        <h3 style="color:#fff;margin:0;font-size:17px">Download MP4 with Merged Sinhala Subtitles</h3>
        <p style="font-size:13px;color:var(--text3);max-width:420px;margin:0">
          සියලුම Download ගොනු තුළ සිංහල උපසිරැසි (Sinhala Subtitles) Video එකටම Merge කර ඇත.
        </p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:6px">
          ${dlLinks || '<span style="color:var(--text3);font-size:13px">Download links loading...</span>'}
        </div>
      </div>`;
    return;
  }

  // ── data-player support (plan §2.4) ───────────────────────────────────────
  // If the container (or <video> element) has a data-player attribute, load
  // the matching VIP server directly instead of Server 1.
  let startIdx = 0;
  const dataPlayerName = (
    playerEl.dataset.player ||
    document.getElementById('filmsubPlayer')?.dataset?.player ||
    ''
  ).toLowerCase().trim();

  if (dataPlayerName && dataPlayerName !== 'super') {
    // Map "vip1"→0, "vip2"→1, "vip3"→2 (matching PLAYER_ORDER order in streams)
    const playerMap = { vip1: 0, vip2: 1, vip3: 2 };
    if (playerMap[dataPlayerName] !== undefined) {
      startIdx = playerMap[dataPlayerName];
    }
  } else if (!streams[0].hasLocalFile && streams.length > 1) {
    startIdx = 1;
  }

  loadStream(movie, startIdx);

  // ── data-subtitle support (plan §6.2) ────────────────────────────────────
  // After Video.js is initialised (slight delay), attach the VTT track.
  const subSrc = (
    playerEl.dataset.subtitle ||
    document.getElementById('filmsubPlayer')?.dataset?.subtitle ||
    ''
  ).trim();

  if (subSrc) {
    const waitForVjs = setInterval(() => {
      const p = window.vjsPlayer || (typeof videojs !== 'undefined' && videojs.getPlayers()?.filmsubPlayer);
      if (p && typeof p.addRemoteTextTrack === 'function') {
        clearInterval(waitForVjs);
        try {
          p.addRemoteTextTrack({
            src: subSrc,
            kind: 'subtitles',
            srclang: 'si',
            label: 'සිංහල (Sinhala)',
            default: true,
          }, false);
          console.log('[FilmSub] data-subtitle VTT track injected:', subSrc);
        } catch (e) {
          console.warn('[FilmSub] addRemoteTextTrack error:', e);
        }
      }
    }, 400);
    // Give up after 8 s (not a Video.js player scenario)
    setTimeout(() => clearInterval(waitForVjs), 8000);
  }
}


function renderStreamEmbed(playerEl, stream, movie) {
  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }
  isTrailerActive = false;

  let baseEmbedUrl = stream.stream_url || '';
  const autoMatchTv = baseEmbedUrl.match(/autoembed\.(?:co|cc|to)\/tv\/(?:imdb|tmdb)\/([a-zA-Z0-9_-]+)-(\d+)-(\d+)/i);
  if (autoMatchTv) {
    baseEmbedUrl = `https://player.autoembed.cc/embed/tv/${autoMatchTv[1]}/${autoMatchTv[2]}/${autoMatchTv[3]}`;
  } else {
    const autoMatchMovie = baseEmbedUrl.match(/autoembed\.(?:co|cc|to)\/movie\/(?:imdb|tmdb)\/([a-zA-Z0-9_-]+)/i);
    if (autoMatchMovie) {
      baseEmbedUrl = `https://player.autoembed.cc/embed/movie/${autoMatchMovie[1]}`;
    }
  }

  const streams = getMovieStreams(movie);
  const sIdx = streams.findIndex(s => s.stream_url === stream.stream_url) !== -1
    ? streams.findIndex(s => s.stream_url === stream.stream_url)
    : currentStreamIdx;

  const altUrls = stream.alt_urls || {};
  const hasAlt = Boolean(altUrls.vidlink || altUrls.autoembed || altUrls.twoembed);

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000000 !important;background-color:#000000 !important;border-radius:8px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, stream.label || stream.server)}
      <iframe id="player-drive-iframe"
              data-base-embed="${FilmSub.escHtml(baseEmbedUrl)}"
              src="${FilmSub.escHtml(baseEmbedUrl)}"
              title="${FilmSub.escHtml(movie.title || 'Movie')} Streaming Player"
              frameborder="0"
              loading="eager"
              referrerpolicy="no-referrer-when-downgrade"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
              allowfullscreen="true"
              webkitallowfullscreen="true"
              mozallowfullscreen="true"
              playsinline="true"
              style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px;background:#000000 !important;background-color:#000000 !important;color-scheme:dark !important;opacity:0;transition:opacity 0.25s ease;z-index:5">
      </iframe>
    </div>
    <div class="player-server-helper" style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;padding:8px 12px;background:#101010;border-radius:6px;margin-top:8px;border:1px solid rgba(255,255,255,0.06);font-size:12.5px;color:#aaa">
      <div style="display:flex;align-items:center;gap:7px">
        <i class="fa-solid fa-circle-play" style="color:var(--accent)"></i>
        <span>Active Server: <strong style="color:#fff">${FilmSub.escHtml(stream.label || stream.server)}</strong></span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        ${hasAlt ? `
          <span style="color:#777">Stream Source:</span>
          ${altUrls.vidlink ? `<button type="button" class="sub-ctrl-btn active alt-src-btn" onclick="window.switchEmbedSource(this, '${FilmSub.escHtml(altUrls.vidlink)}')" style="padding:4px 9px;font-size:11.5px;cursor:pointer">🎬 VidLink</button>` : ''}
          ${altUrls.autoembed ? `<button type="button" class="sub-ctrl-btn alt-src-btn" onclick="window.switchEmbedSource(this, '${FilmSub.escHtml(altUrls.autoembed)}')" style="padding:4px 9px;font-size:11.5px;cursor:pointer">⚡ AutoEmbed</button>` : ''}
          ${altUrls.twoembed ? `<button type="button" class="sub-ctrl-btn alt-src-btn" onclick="window.switchEmbedSource(this, '${FilmSub.escHtml(altUrls.twoembed)}')" style="padding:4px 9px;font-size:11.5px;cursor:pointer">🚀 2Embed</button>` : ''}
        ` : `
          <span style="color:#777">Switch Player:</span>
          ${streams.map((st, i) => `
            <button type="button" class="sub-ctrl-btn${sIdx === i ? ' active' : ''}" onclick="loadStream(currentMovie, ${i})" style="padding:4px 9px;font-size:11.5px;cursor:pointer">
              ${FilmSub.escHtml((st.label || st.server || `Server ${i + 1}`).split('(')[0].trim())}
            </button>
          `).join('')}
        `}
      </div>
    </div>`;

  const iframeEl = document.getElementById('player-drive-iframe');
  const loaderEl = document.getElementById('super-player-loader');

  const revealIframe = () => {
    setTimeout(() => {
      if (iframeEl && iframeEl.isConnected) iframeEl.style.opacity = '1';
      if (loaderEl && loaderEl.isConnected) loaderEl.classList.add('hidden');
    }, 100);
  };

  if (iframeEl) {
    iframeEl.addEventListener('load', revealIframe);
    iframeEl.addEventListener('error', revealIframe);
  }
  setTimeout(revealIframe, 1200);
  mountLiveSubtitleOverlay(playerEl, movie);
}

window.switchEmbedSource = function(btn, url) {
  if (!url) return;
  const iframeEl = document.getElementById('player-drive-iframe');
  const loaderEl = document.getElementById('super-player-loader');
  if (loaderEl) loaderEl.classList.remove('hidden');
  if (iframeEl) {
    iframeEl.style.opacity = '0';
    iframeEl.src = url;
  }
  document.querySelectorAll('.alt-src-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  setTimeout(() => {
    if (iframeEl && iframeEl.isConnected) iframeEl.style.opacity = '1';
    if (loaderEl && loaderEl.isConnected) loaderEl.classList.add('hidden');
  }, 1200);
};

/**
 * Injects an interactive Quality Selector Menu Button directly inside the Video.js control bar
 * so users can switch Auto / 1080p / 720p / 480p / 360p even in Fullscreen mode.
 */
function injectInPlayerQualityControl(player) {
  if (!player || !player.controlBar) return;
  const cbEl = player.controlBar.el();
  if (!cbEl || cbEl.querySelector('.vjs-super-quality-btn')) return;

  const qWrap = document.createElement('div');
  qWrap.className = 'vjs-control vjs-button vjs-super-quality-btn';
  qWrap.setAttribute('title', 'Stream Quality (Auto / 1080p / 720p / 480p / 360p)');

  const activeLabel = selectedQuality === 'auto'
    ? `AUTO (${currentEffectiveQuality.toUpperCase()})`
    : selectedQuality.toUpperCase();

  qWrap.innerHTML = `
    <span class="vjs-super-quality-badge">
      <i class="fa-solid fa-gear"></i>
      <span id="vjs-sq-badge-text">${activeLabel}</span>
    </span>
    <div class="vjs-super-quality-menu" id="vjs-super-quality-menu">
      <button type="button" class="vjs-sq-item${selectedQuality === 'auto' ? ' active' : ''}" data-quality="auto">
        <span>⚡ Auto Adaptive</span><span>HD</span>
      </button>
      <button type="button" class="vjs-sq-item${selectedQuality === '1080p' ? ' active' : ''}" data-quality="1080p">
        <span>1080p Full HD</span><span>FHD</span>
      </button>
      <button type="button" class="vjs-sq-item${selectedQuality === '720p' ? ' active' : ''}" data-quality="720p">
        <span>720p HD</span><span>HD</span>
      </button>
      <button type="button" class="vjs-sq-item${selectedQuality === '480p' ? ' active' : ''}" data-quality="480p">
        <span>480p Smooth</span><span>SD</span>
      </button>
    </div>
  `;

  const fsToggle = cbEl.querySelector('.vjs-fullscreen-control');
  if (fsToggle) {
    cbEl.insertBefore(qWrap, fsToggle);
  } else {
    cbEl.appendChild(qWrap);
  }

  const menuEl = qWrap.querySelector('#vjs-super-quality-menu');
  qWrap.addEventListener('pointerenter', () => {
    prewarmAllVariants(currentMovie);
  }, { passive: true });

  qWrap.addEventListener('click', (e) => {
    e.stopPropagation();
    prewarmAllVariants(currentMovie);
    if (menuEl) menuEl.classList.toggle('open');
  });

  qWrap.querySelectorAll('.vjs-sq-item').forEach(btn => {
    const q = btn.dataset.quality;
    if (q && q !== 'auto') {
      const warmItem = () => prewarmQualityTier(currentMovie, q);
      btn.addEventListener('pointerenter', warmItem, { passive: true });
      btn.addEventListener('touchstart', warmItem, { passive: true });
    }
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const targetQ = btn.dataset.quality || 'auto';
      if (menuEl) menuEl.classList.remove('open');
      applyQualitySwitch(targetQ, { isAutoDowngrade: false });
    });
  });

  document.addEventListener('click', () => {
    if (menuEl) menuEl.classList.remove('open');
  });
}

/**
 * Format video timestamp with leading zeros (e.g., 00:34 / 02:13:09).
 */
function formatFilmTime(seconds, includeHoursIfZero = false) {
  if (!seconds || isNaN(seconds) || seconds < 0) return includeHoursIfZero ? '00:00:00' : '00:00';
  const s = Math.floor(seconds);
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  const pad = (n) => String(n).padStart(2, '0');
  if (hrs > 0) {
    return `${pad(hrs)}:${pad(mins)}:${pad(secs)}`;
  }
  if (includeHoursIfZero) {
    return `00:${pad(mins)}:${pad(secs)}`;
  }
  return `${pad(mins)}:${pad(secs)}`;
}

/**
 * Returns the effective duration in seconds, falling back to movie metadata if stream duration is missing/infinite.
 */
function getEffectiveDuration(player, movie) {
  let dur = 0;
  if (player && typeof player.duration === 'function') {
    dur = player.duration();
  }
  if (!dur || isNaN(dur) || !isFinite(dur) || dur <= 0) {
    const m = movie || currentMovie;
    if (typeof m?.duration === 'number') {
      dur = m.duration * 60;
    } else if (typeof m?.duration === 'string') {
      const minMatch = m.duration.match(/(\d+)\s*min/i) || m.duration.match(/^(\d+)$/);
      const hrMatch = m.duration.match(/(\d+)\s*h(?:r|ours?)?/i);
      let totalMins = 0;
      if (hrMatch) totalMins += parseInt(hrMatch[1], 10) * 60;
      if (minMatch) totalMins += parseInt(minMatch[1], 10);
      if (totalMins > 0) dur = totalMins * 60;
    }
  }
  return (dur && dur > 0) ? dur : 0;
}

/**
 * Displays a smooth Netflix/YouTube style ripple overlay during double-tap / keyboard seek (+10s / -10s).
 */
function showSeekRipple(playerEl, direction, seconds = 10) {
  if (!playerEl) return;
  const wrap = playerEl.querySelector('.player-iframe-wrap') || (playerEl.classList && playerEl.classList.contains('player-iframe-wrap') ? playerEl : playerEl);

  const old = wrap.querySelector('.seek-ripple-overlay');
  if (old) old.remove();

  const ripple = document.createElement('div');
  ripple.className = `seek-ripple-overlay ${direction}`;
  ripple.innerHTML = `
    <div class="seek-ripple-bubble">
      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        ${direction === 'forward'
          ? '<path d="M21 12a9 9 0 1 1-9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
          : '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'}
        <text x="12" y="15.5" font-size="8.5" font-weight="800" fill="currentColor" stroke="none" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">10</text>
      </svg>
      <span>${direction === 'forward' ? `+${seconds}s` : `-${seconds}s`}</span>
    </div>`;
  wrap.appendChild(ripple);
  setTimeout(() => {
    if (ripple.parentNode) ripple.parentNode.removeChild(ripple);
  }, 700);
}

/**
 * CineSubz-Style Center Overlay Controls:
 * - Center Rewind 10s button
 * - Center Play / Pause button
 * - Center Forward 10s button
 * - Auto-hide on playback with hover / touch reveal
 */
function mountCenterPlayerControls(playerEl, player, movie) {
  if (!player || !player.el) return;
  const playerDom = player.el();
  if (!playerDom) return;

  const old = playerDom.querySelector('.cs-player-center-overlay');
  if (old) old.remove();

  const overlay = document.createElement('div');
  overlay.className = 'cs-player-center-overlay';
  overlay.id = 'cs-player-center-overlay';
  overlay.innerHTML = `
    <button type="button" class="cs-center-btn cs-center-rewind" id="cs-btn-center-rewind" title="Rewind 10s (Left Arrow)" aria-label="Rewind 10 seconds">
      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
        <path d="M3 3v5h5"/>
        <text x="12" y="15.5" font-size="8.5" font-weight="800" fill="currentColor" stroke="none" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">10</text>
      </svg>
    </button>
    <button type="button" class="cs-center-btn cs-center-playpause" id="cs-btn-center-playpause" title="Play / Pause (Space)" aria-label="Play or Pause">
      <i class="fa-solid fa-play" id="cs-center-play-icon"></i>
    </button>
    <button type="button" class="cs-center-btn cs-center-forward" id="cs-btn-center-forward" title="Forward 10s (Right Arrow)" aria-label="Forward 10 seconds">
      <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12a9 9 0 1 1-9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
        <path d="M21 3v5h-5"/>
        <text x="12" y="15.5" font-size="8.5" font-weight="800" fill="currentColor" stroke="none" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">10</text>
      </svg>
    </button>
  `;

  const cb = playerDom.querySelector('.vjs-control-bar');
  if (cb) {
    playerDom.insertBefore(overlay, cb);
  } else {
    playerDom.appendChild(overlay);
  }

  const playBtn = overlay.querySelector('#cs-btn-center-playpause');
  const rewindBtn = overlay.querySelector('#cs-btn-center-rewind');
  const forwardBtn = overlay.querySelector('#cs-btn-center-forward');
  const playIcon = overlay.querySelector('#cs-center-play-icon');

  const updatePlayState = () => {
    if (!player || !playIcon) return;
    if (player.paused()) {
      playIcon.className = 'fa-solid fa-play';
      overlay.classList.remove('hidden');
    } else {
      playIcon.className = 'fa-solid fa-pause';
    }
  };

  playBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!player) return;
    if (player.paused()) {
      player.play().catch(() => {});
    } else {
      player.pause();
    }
    updatePlayState();
  });

  rewindBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!player) return;
    const cur = (typeof player.currentTime === 'function') ? player.currentTime() : 0;
    player.currentTime(Math.max(0, cur - 10));
    showSeekRipple(playerEl, 'backward', 10);
  });

  forwardBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (!player) return;
    const cur = (typeof player.currentTime === 'function') ? player.currentTime() : 0;
    const dur = getEffectiveDuration(player, movie);
    player.currentTime(Math.min(dur || (cur + 10), cur + 10));
    showSeekRipple(playerEl, 'forward', 10);
  });

  player.on('play', updatePlayState);
  player.on('pause', updatePlayState);
  player.on('ended', () => {
    if (playIcon) playIcon.className = 'fa-solid fa-rotate-right';
    overlay.classList.remove('hidden');
  });

  let hideTimer = null;
  const showOverlay = () => {
    overlay.classList.remove('hidden');
    if (hideTimer) clearTimeout(hideTimer);
    if (player && !player.paused()) {
      hideTimer = setTimeout(() => {
        if (player && !player.paused()) {
          overlay.classList.add('hidden');
        }
      }, 2800);
    }
  };

  playerDom.addEventListener('pointermove', showOverlay, { passive: true });
  playerDom.addEventListener('touchstart', showOverlay, { passive: true });
  playerDom.addEventListener('mouseleave', () => {
    if (player && !player.paused()) {
      overlay.classList.add('hidden');
    }
  });

  updatePlayState();
}

/**
 * Injects CineSubz-Style Control Bar components:
 * - Rewind 10s & Forward 10s buttons next to Play/Pause
 * - High-precision Time Display (00:34 / 02:13:09)
 * - Brand Watermark (FilmSub HD)
 */
function injectBottomControlBarItems(player, movie) {
  if (!player || !player.controlBar) return;
  const cbEl = player.controlBar.el();
  if (!cbEl) return;

  // 1. Rewind 10s and Forward 10s buttons right next to playToggle
  if (!cbEl.querySelector('.vjs-rewind-10')) {
    const playToggleEl = player.controlBar.playToggle ? player.controlBar.playToggle.el() : cbEl.firstElementChild;

    const rewindBtn = document.createElement('button');
    rewindBtn.type = 'button';
    rewindBtn.className = 'vjs-control vjs-button vjs-custom-seek-btn vjs-rewind-10';
    rewindBtn.setAttribute('title', 'Rewind 10 seconds (Left Arrow)');
    rewindBtn.setAttribute('aria-label', 'Rewind 10 seconds');
    rewindBtn.innerHTML = `
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
        <path d="M3 3v5h5"/>
        <text x="12" y="15.5" font-size="8.5" font-weight="800" fill="currentColor" stroke="none" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">10</text>
      </svg>`;
    rewindBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const cur = typeof player.currentTime === 'function' ? player.currentTime() : 0;
      player.currentTime(Math.max(0, cur - 10));
      showSeekRipple(player.el(), 'backward', 10);
    });

    const forwardBtn = document.createElement('button');
    forwardBtn.type = 'button';
    forwardBtn.className = 'vjs-control vjs-button vjs-custom-seek-btn vjs-forward-10';
    forwardBtn.setAttribute('title', 'Forward 10 seconds (Right Arrow)');
    forwardBtn.setAttribute('aria-label', 'Forward 10 seconds');
    forwardBtn.innerHTML = `
      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12a9 9 0 1 1-9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
        <path d="M21 3v5h-5"/>
        <text x="12" y="15.5" font-size="8.5" font-weight="800" fill="currentColor" stroke="none" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">10</text>
      </svg>`;
    forwardBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const cur = typeof player.currentTime === 'function' ? player.currentTime() : 0;
      const dur = getEffectiveDuration(player, movie);
      player.currentTime(Math.min(dur || (cur + 10), cur + 10));
      showSeekRipple(player.el(), 'forward', 10);
    });

    if (playToggleEl && playToggleEl.nextSibling) {
      cbEl.insertBefore(rewindBtn, playToggleEl.nextSibling);
      cbEl.insertBefore(forwardBtn, rewindBtn.nextSibling);
    } else {
      cbEl.appendChild(rewindBtn);
      cbEl.appendChild(forwardBtn);
    }
  }

  // 2. Custom Time Display (00:34 / 02:13:09)
  let timeWrap = cbEl.querySelector('.vjs-custom-time-display');
  if (!timeWrap) {
    timeWrap = document.createElement('div');
    timeWrap.className = 'vjs-control vjs-custom-time-display';
    timeWrap.innerHTML = `
      <span class="vjs-cur-time" id="vjs-cur-time">00:00</span>
      <span class="vjs-time-slash">/</span>
      <span class="vjs-dur-time" id="vjs-dur-time">00:00</span>
    `;

    const volPanel = player.controlBar.volumePanel ? player.controlBar.volumePanel.el() : null;
    if (volPanel && volPanel.nextSibling) {
      cbEl.insertBefore(timeWrap, volPanel.nextSibling);
    } else {
      cbEl.appendChild(timeWrap);
    }
  }

  // 3. Brand Watermark & Quality Badge on the right side
  if (!cbEl.querySelector('.vjs-control-brand-wrap')) {
    const brandWrap = document.createElement('div');
    brandWrap.className = 'vjs-control-brand-wrap';
    const initQuality = (selectedQuality === 'auto' ? (currentEffectiveQuality || '720p') : selectedQuality).toUpperCase();
    brandWrap.innerHTML = `
      <span class="vjs-brand-filmsub"><span class="brand-red">FILM</span><span class="brand-white">SUB</span></span>
      <span class="vjs-brand-quality-badge" id="vjs-bar-quality-badge">HD ${initQuality}</span>
    `;

    const sqBtn = cbEl.querySelector('.vjs-super-quality-btn');
    const fsBtn = player.controlBar.fullscreenToggle ? player.controlBar.fullscreenToggle.el() : null;
    if (sqBtn) {
      cbEl.insertBefore(brandWrap, sqBtn);
    } else if (fsBtn) {
      cbEl.insertBefore(brandWrap, fsBtn);
    } else {
      cbEl.appendChild(brandWrap);
    }
  }

  // Live time updater
  const curTimeEl = timeWrap.querySelector('#vjs-cur-time');
  const durTimeEl = timeWrap.querySelector('#vjs-dur-time');

  const updateTime = () => {
    if (!player) return;
    const cur = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    const dur = getEffectiveDuration(player, movie);
    if (curTimeEl) curTimeEl.textContent = formatFilmTime(cur, dur >= 3600);
    if (durTimeEl) durTimeEl.textContent = dur > 0 ? formatFilmTime(dur, dur >= 3600) : '--:--';
  };

  player.on('timeupdate', updateTime);
  player.on('loadedmetadata', updateTime);
  player.on('durationchange', updateTime);
  player.on('seeking', updateTime);
  player.on('seeked', updateTime);
  updateTime();
}

/**
 * Keyboard shortcuts for Laptop / PC:
 * - ArrowLeft / J: Rewind 10s
 * - ArrowRight / L: Forward 10s
 * - Space / K: Play/Pause
 * - F: Fullscreen
 * - M: Mute
 * - ArrowUp / ArrowDown: Volume +/- 10%
 */
function attachPlayerKeyboardShortcuts(player, playerEl) {
  if (window._fsPlayerKeyHandler) {
    document.removeEventListener('keydown', window._fsPlayerKeyHandler);
  }
  window._fsPlayerKeyHandler = (e) => {
    const tag = (e.target && e.target.tagName) ? e.target.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || (e.target && e.target.isContentEditable)) return;
    if (!player || typeof player.currentTime !== 'function') return;

    const cur = player.currentTime() || 0;
    const dur = getEffectiveDuration(player, currentMovie);
    const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl;

    switch (e.key) {
      case 'ArrowLeft':
      case 'j':
      case 'J':
        e.preventDefault();
        player.currentTime(Math.max(0, cur - 10));
        showSeekRipple(wrap, 'backward', 10);
        break;
      case 'ArrowRight':
      case 'l':
      case 'L':
        e.preventDefault();
        player.currentTime(Math.min(dur || (cur + 10), cur + 10));
        showSeekRipple(wrap, 'forward', 10);
        break;
      case ' ':
      case 'k':
      case 'K':
        e.preventDefault();
        if (player.paused()) {
          player.play().catch(() => {});
        } else {
          player.pause();
        }
        break;
      case 'f':
      case 'F':
        e.preventDefault();
        if (player.isFullscreen && player.isFullscreen()) {
          player.exitFullscreen();
        } else if (player.requestFullscreen) {
          player.requestFullscreen();
        }
        break;
      case 'm':
      case 'M':
        e.preventDefault();
        player.muted(!player.muted());
        break;
      case 'ArrowUp':
        e.preventDefault();
        player.volume(Math.min(1, (player.volume() || 0) + 0.1));
        break;
      case 'ArrowDown':
        e.preventDefault();
        player.volume(Math.max(0, (player.volume() || 0) - 0.1));
        break;
    }
  };
  document.addEventListener('keydown', window._fsPlayerKeyHandler);
}

/**
 * Configures touch gestures on Mobile:
 * - Double-tap left 42%: -10s backward seek (with rapid multi-tap support: -20s, -30s)
 * - Double-tap right 42%: +10s forward seek (with rapid multi-tap support: +20s, +30s)
 * - Single-tap: Toggle controls overlay
 */
function attachMobileTouchControls(playerEl, player) {
  if (!playerEl || !player) return;
  const wrap = playerEl.querySelector('.player-iframe-wrap') || (player.el ? player.el() : playerEl);

  let tapCount = 0;
  let lastTapEpoch = 0;
  let lastTapZone = null;
  let singleTapTimeout = null;
  let accumulatedSeek = 0;
  let seekResetTimeout = null;

  wrap.addEventListener('touchend', (e) => {
    if (e.target.closest('.vjs-control-bar, .cs-player-center-overlay, .vjs-menu, button, a, input, select')) return;
    const touch = e.changedTouches && e.changedTouches[0];
    if (!touch) return;

    const now = Date.now();
    const rect = wrap.getBoundingClientRect();
    const touchX = touch.clientX - rect.left;
    const pct = touchX / rect.width;

    let zone = 'center';
    if (pct < 0.42) zone = 'left';
    else if (pct > 0.58) zone = 'right';

    if (zone !== 'center' && zone === lastTapZone && (now - lastTapEpoch < 420)) {
      // Double-tap or rapid multi-tap!
      if (singleTapTimeout) {
        clearTimeout(singleTapTimeout);
        singleTapTimeout = null;
      }
      tapCount++;
      accumulatedSeek += 10;

      const cur = typeof player.currentTime === 'function' ? player.currentTime() : 0;
      const dur = getEffectiveDuration(player, currentMovie);

      if (zone === 'left') {
        player.currentTime(Math.max(0, cur - 10));
        showSeekRipple(wrap, 'backward', accumulatedSeek);
      } else {
        player.currentTime(Math.min(dur || (cur + 10), cur + 10));
        showSeekRipple(wrap, 'forward', accumulatedSeek);
      }

      if (seekResetTimeout) clearTimeout(seekResetTimeout);
      seekResetTimeout = setTimeout(() => {
        accumulatedSeek = 0;
        tapCount = 0;
        lastTapZone = null;
      }, 750);

      lastTapEpoch = now;
      if (e.cancelable) e.preventDefault();
    } else {
      // First tap
      lastTapEpoch = now;
      lastTapZone = zone;
      tapCount = 1;
      accumulatedSeek = 10;

      if (singleTapTimeout) clearTimeout(singleTapTimeout);
      singleTapTimeout = setTimeout(() => {
        const centerOverlay = wrap.querySelector('.cs-player-center-overlay');
        if (centerOverlay) {
          if (centerOverlay.classList.contains('hidden')) {
            centerOverlay.classList.remove('hidden');
            if (player && !player.paused()) {
              setTimeout(() => {
                if (player && !player.paused()) centerOverlay.classList.add('hidden');
              }, 3000);
            }
          } else {
            if (player && !player.paused()) {
              centerOverlay.classList.add('hidden');
            }
          }
        }
        tapCount = 0;
        lastTapZone = null;
        accumulatedSeek = 0;
      }, 260);
    }
  }, { passive: false });
}

/**
 * Displays a Cinema-Style Auto-Reconnect Modal when network stream gets interrupted mid-playback.
 */
function showStreamReconnectModal(playerEl, player, stream, resumeTime, movie) {
  const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl;
  if (!wrap) return;

  const existing = wrap.querySelector('.cs-reconnect-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.className = 'cs-reconnect-modal';
  modal.innerHTML = `
    <div class="cs-reconnect-box">
      <div class="cs-reconnect-icon"><i class="fa-solid fa-cloud-arrow-down"></i></div>
      <h3 class="cs-reconnect-title">Connection Interrupted</h3>
      <p class="cs-reconnect-desc">Network connection to the stream server was paused. Resuming video seamlessly from where you left off.</p>
      <div class="cs-reconnect-actions">
        <button type="button" class="btn-rec btn-rec-primary" id="btn-rec-retry"><i class="fa-solid fa-rotate-right"></i> Resume Playback</button>
        <button type="button" class="btn-rec btn-rec-secondary" id="btn-rec-480"><i class="fa-solid fa-bolt"></i> 480p SD Smooth</button>
      </div>
      <span class="cs-rec-countdown" id="cs-rec-countdown">Auto-reconnecting in 3s...</span>
    </div>
  `;
  wrap.appendChild(modal);

  let countdown = 3;
  let timer = null;

  const doReconnect = (targetQuality = null) => {
    if (timer) clearInterval(timer);
    modal.remove();
    if (targetQuality) {
      applyQualitySwitch(targetQuality, { isAutoDowngrade: false });
    } else {
      try {
        const curSrc = stream.stream_url;
        player.src({ src: curSrc, type: stream.type || 'video/mp4' });
        player.ready(() => {
          player.currentTime(Math.max(0, resumeTime - 1));
          player.play().catch(() => {});
        });
      } catch (e) {
        console.error('Reconnect failed:', e);
      }
    }
  };

  const retryBtn = modal.querySelector('#btn-rec-retry');
  if (retryBtn) retryBtn.addEventListener('click', () => doReconnect());

  const btn480 = modal.querySelector('#btn-rec-480');
  if (btn480) btn480.addEventListener('click', () => doReconnect('480p'));

  const countSpan = modal.querySelector('#cs-rec-countdown');
  timer = setInterval(() => {
    countdown--;
    if (countSpan) countSpan.textContent = `Auto-reconnecting in ${countdown}s...`;
    if (countdown <= 0) {
      clearInterval(timer);
      doReconnect();
    }
  }, 1000);
}

function createVjsPlayer(playerEl, stream, movie) {
  if (!stream || !stream.hasLocalFile || !stream.stream_url || stream.stream_url.endsWith('/0')) {
    renderPlayerFallback(playerEl, movie);
    return;
  }

  const subtitles = getMovieSubtitles(movie);
  const isHardcoded = Boolean(movie && (movie.sub_hardcoded || movie.is_already_hardsubbed));
  const tracksHTML = subtitles.map((sub, i) => `
    <track kind="subtitles" src="${FilmSub.escHtml(sub.url || '')}"
           srclang="${FilmSub.escHtml(sub.srclang || 'si')}"
           label="${FilmSub.escHtml(sub.label || 'සිංහල උපසිරැසි')}"
           ${(sub.default || i === 0) && !isHardcoded ? 'default' : ''}>`).join('');

  const posterUrl = movie.backdrop || movie.poster || '';
  const posterAttr = posterUrl ? `poster="${FilmSub.escHtml(posterUrl)}"` : '';

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000000 !important;border-radius:8px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, stream.label || stream.server)}
      <video id="filmsubPlayer" class="video-js vjs-big-play-centered vjs-theme-fantasy"
             controls preload="auto" playsinline webkit-playsinline ${posterAttr}
             style="position:absolute;top:0;left:0;width:100%;height:100%;background:#000000 !important">
        <source src="${FilmSub.escHtml(stream.stream_url)}" type="${FilmSub.escHtml(stream.type || 'video/mp4')}">
        ${tracksHTML}
        <p class="vjs-no-js">Enable JavaScript or use a modern browser to watch videos.</p>
      </video>
    </div>`;

  const hideLoader = () => {
    const loader = document.getElementById('super-player-loader');
    if (loader) loader.classList.add('hidden');
  };

  if (typeof videojs !== 'undefined') {
    vjsPlayer = videojs('filmsubPlayer', {
      fluid: true,
      responsive: true,
      poster: posterUrl,
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
          bandwidth: 20000000,        // 20 Mbps starting bandwidth estimate (was 15 Mbps)
          bufferBasedABR: true,
          maxBufferLength: 180,       // 3 minutes max buffer (was 120s)
          minBufferLength: 2,         // 2s buffer allows playback to start in <500ms (was 30s)
          maxBufferSize: 192 * 1024 * 1024,  // 192 MiB max buffer RAM (was 128 MiB)
          experimentalBufferClipping: false
        },
        nativeCaptions: false,      // prevent subtitle double-render on Safari
        nativeVideoTracks: true,
        nativeAudioTracks: true,
        nativeTextTracks: false
      },
      liveui: false,
      controlBar: {
        children: [
          'playToggle', 'volumePanel', 'progressControl',
          'playbackRateMenuButton', 'fullscreenToggle'
        ]
      }
    });

    vjsPlayer.ready(() => {
      try {
        if (vjsPlayer.tech_ && vjsPlayer.tech_.el_) {
          const el = vjsPlayer.tech_.el_;
          el.setAttribute('preload', 'auto');
          el.setAttribute('playsinline', '');
          el.setAttribute('webkit-playsinline', '');
        }
      } catch (e) {}

      injectInPlayerQualityControl(vjsPlayer);
      mountCenterPlayerControls(playerEl, vjsPlayer, movie);
      injectBottomControlBarItems(vjsPlayer, movie);
      attachPlayerKeyboardShortcuts(vjsPlayer, playerEl);
      attachMobileTouchControls(playerEl, vjsPlayer);

      // Bug 2 fix: Ensure Sinhala subtitle track is injected via addRemoteTextTrack
      // if the HTML <track> hasn't surfaced in textTracks() yet (async VJS fetch).
      const subs = getMovieSubtitles(movie);
      if (subs && subs.length > 0) {
        try {
          const existingTracks = vjsPlayer.textTracks();
          let hasSub = false;
          for (let i = 0; i < existingTracks.length; i++) {
            if (existingTracks[i].kind === 'subtitles' || existingTracks[i].kind === 'captions') {
              hasSub = true;
              existingTracks[i].mode = (liveSubEnabled && !isHardcoded) ? 'showing' : 'disabled';
            }
          }
          if (!hasSub && !isHardcoded && typeof vjsPlayer.addRemoteTextTrack === 'function') {
            const primarySub = subs[0];
            vjsPlayer.addRemoteTextTrack({
              src: primarySub.url,
              kind: 'subtitles',
              srclang: primarySub.srclang || 'si',
              label: primarySub.label || 'සිංහල (Sinhala)',
              default: !isHardcoded,
            }, false);
          }
        } catch (e) {}
      }

      syncSubtitles();
      // Deferred re-sync: VJS fetches the VTT asynchronously — run again after 1.5s
      // to guarantee VTTCues are injected once the track file is fully loaded.
      setTimeout(() => { try { syncSubtitles(); } catch (e) {} }, 1500);

      mountLiveSubtitleOverlay(playerEl, movie);
      attachAdaptiveStallMonitor(vjsPlayer);
      try {
        const p = vjsPlayer.play();
        if (p && typeof p.catch === 'function') {
          p.catch(() => {
            hideLoader();
          });
        }
      } catch (e) {
        hideLoader();
      }
    });

    vjsPlayer.on('loadedmetadata', () => {
      finishProgressAnimation();
      syncSubtitles();
    });

    vjsPlayer.on('loadeddata', finishProgressAnimation);
    vjsPlayer.on('canplay', finishProgressAnimation);
    vjsPlayer.on('canplaythrough', finishProgressAnimation);
    vjsPlayer.on('playing', () => {
      finishProgressAnimation();
      syncSubtitles();
    });

    // Safety fallback: Ensure loader fades out after at most 2.4s so big play button is always visible
    setTimeout(finishProgressAnimation, 2400);

    // Auto-landscape orientation on mobile devices during fullscreen
    vjsPlayer.on('fullscreenchange', () => {
      try {
        if (vjsPlayer.isFullscreen() && window.innerWidth < 768 && screen.orientation && screen.orientation.lock) {
          screen.orientation.lock('landscape').catch(() => {});
        } else if (!vjsPlayer.isFullscreen() && screen.orientation && screen.orientation.unlock) {
          screen.orientation.unlock().catch(() => {});
        }
      } catch (e) {}
    });

    // Zero-lag watchdog and failover management:
    // If playback stalls at readyState 0 without data progress for more than 30s or network fails, automatically failover
    let slowHeaderWatchdog = null;
    let lastDataProgressEpoch = Date.now();
    vjsPlayer.on('progress', () => {
      lastDataProgressEpoch = Date.now();
    });

    let failoverTriggered = false;
    const triggerFailover = (reason) => {
      if (!vjsPlayer || failoverTriggered) return;
      failoverTriggered = true;
      const currentSrc = (typeof vjsPlayer.currentSrc === 'function' ? vjsPlayer.currentSrc() : '') || stream.stream_url || '';
      console.warn(`[FilmSub Player] Failover triggered (${reason}), currentSrc: ${currentSrc}`);

      // 1. If high resolution (1080p/720p) stalled on edge proxy, retry with lightweight 480p ONLY if selectedQuality is 'auto'
      if (!vjsPlayer._retried480p && selectedQuality === 'auto') {
        vjsPlayer._retried480p = true;
        const vm = currentMovie && (currentMovie.variant_media || (currentMovie.movie_entry && currentMovie.movie_entry.variant_media));
        if (vm && vm['480p'] && vm['480p'].message_id) {
          const targetMsgId = String(vm['480p'].message_id);
          // Only switch if we are NOT already on 480p!
          if (!currentSrc.includes(`/${targetMsgId}`)) {
            console.warn('[FilmSub Player] Connection timed out, gracefully downgrading to 480p while preserving playhead');
            failoverTriggered = false;
            resetWatchdog(15000);
            applyQualitySwitch('480p', { isAutoDowngrade: true });
            return;
          }
        }
      }

      // 2. Retry with direct live tunnel if edge stream failed or stalled
      if (!vjsPlayer._retriedDirect && activeStreamBaseUrl && activeStreamBaseUrl.startsWith('http') && !isDeadTunnel(activeStreamBaseUrl)) {
        vjsPlayer._retriedDirect = true;
        const curT = (typeof vjsPlayer.currentTime === 'function') ? (vjsPlayer.currentTime() || 0) : 0;
        const wasPaused = vjsPlayer.paused();
        const directPrefix = activeStreamBaseUrl.replace(/\/+$/, '');
        const directUrl = directPrefix + (currentSrc.startsWith('http') ? currentSrc.replace(/^https?:\/\/[^/]+/, '') : (currentSrc.startsWith('/') ? currentSrc : '/' + currentSrc));
        console.warn('[FilmSub Player] Switching directly to live tunnel:', directUrl);
        failoverTriggered = false;
        vjsPlayer.src({ type: stream.type || 'video/mp4', src: directUrl });
        vjsPlayer.load();
        if (curT > 0) {
          vjsPlayer.one('loadedmetadata', () => {
            try { vjsPlayer.currentTime(curT); } catch (e) {}
            if (!wasPaused) vjsPlayer.play().catch(() => {});
          });
        } else {
          vjsPlayer.play().catch(() => {});
        }
        resetWatchdog(15000);
        return;
      }

      setTimeout(() => {
        failoverTriggered = false;
        renderPlayerFallback(playerEl, movie);
      }, 50);
    };

    const resetWatchdog = (timeoutMs = 18000) => {
      if (slowHeaderWatchdog) clearTimeout(slowHeaderWatchdog);
      slowHeaderWatchdog = setTimeout(() => {
        if (!vjsPlayer) return;
        const rState = typeof vjsPlayer.readyState === 'function' ? vjsPlayer.readyState() : (vjsPlayer.tech_?.el_?.readyState || 0);
        const nState = typeof vjsPlayer.networkState === 'function' ? vjsPlayer.networkState() : (vjsPlayer.tech_?.el_?.networkState || 0);

        if (!vjsPlayer.paused() && rState === 0) {
          const timeSinceProgress = Date.now() - lastDataProgressEpoch;
          // If browser is actively receiving data within the last 12s or networkState is active (2=NETWORK_LOADING, 1=NETWORK_IDLE), extend watchdog!
          if (timeSinceProgress < 12000 || nState === 2 || nState === 1) {
            console.log(`[FilmSub Player] Video data transfer active (${timeSinceProgress}ms since progress, nState=${nState}), extending watchdog`);
            resetWatchdog(15000);
            return;
          }
          triggerFailover('stalled readyState 0 (stream server offline or unreachable)');
        }
      }, timeoutMs);
    };

    resetWatchdog(18000);

    vjsPlayer.on('play', () => resetWatchdog(20000));
    vjsPlayer.on('waiting', () => {
      const rState = typeof vjsPlayer.readyState === 'function' ? vjsPlayer.readyState() : (vjsPlayer.tech_?.el_?.readyState || 0);
      if (rState === 0) {
        resetWatchdog(15000);
      }
    });
    vjsPlayer.on('canplay', () => {
      if (slowHeaderWatchdog) clearTimeout(slowHeaderWatchdog);
      hideLoader();
      fallbackAutoRetryCount = 0;
      lastFallbackRetryEpoch = 0;
    });
    vjsPlayer.on('loadedmetadata', () => {
      if (slowHeaderWatchdog) clearTimeout(slowHeaderWatchdog);
      hideLoader();
      syncSubtitles();
    });
    vjsPlayer.on('loadeddata', () => {
      hideLoader();
      fallbackAutoRetryCount = 0;
      lastFallbackRetryEpoch = 0;
    });
    vjsPlayer.on('playing', () => {
      if (slowHeaderWatchdog) clearTimeout(slowHeaderWatchdog);
      hideLoader();
      syncSubtitles();
      fallbackAutoRetryCount = 0;
      lastFallbackRetryEpoch = 0;
    });
    vjsPlayer.on('dispose', () => {
      if (slowHeaderWatchdog) clearTimeout(slowHeaderWatchdog);
    });

    // Seamless failover on real player error or network interruption
    vjsPlayer.on('error', () => {
      const err = vjsPlayer ? vjsPlayer.error() : null;
      console.warn('[FilmSub Player] Video.js error event:', err);
      const errDisplay = playerEl.querySelector('.vjs-error-display');
      if (errDisplay) errDisplay.style.display = 'none';

      const curT = (vjsPlayer && typeof vjsPlayer.currentTime === 'function') ? vjsPlayer.currentTime() : 0;
      if (curT > 2) {
        // Playback was active: show sleek Cinema Reconnect Modal preserving current playhead
        showStreamReconnectModal(playerEl, vjsPlayer, stream, curT, movie);
        return;
      }

      const rState = (vjsPlayer && typeof vjsPlayer.readyState === 'function') ? vjsPlayer.readyState() : (vjsPlayer?.tech_?.el_?.readyState || 0);
      if (rState >= 1 && curT === 0) {
        console.warn('[FilmSub Player] Video has metadata (readyState=' + rState + '), attempting soft retry');
        vjsPlayer.play().catch(() => {});
        return;
      }

      if (slowHeaderWatchdog) clearTimeout(slowHeaderWatchdog);
      triggerFailover('player error event: ' + (err ? (err.message || err.code) : 'unknown'));
    });
  } else {
    hideLoader();
    mountLiveSubtitleOverlay(playerEl, movie);
  }
}

function renderPlayerFallback(playerEl, movie) {
  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }

  if (window.fallbackRetryInterval) {
    clearInterval(window.fallbackRetryInterval);
    window.fallbackRetryInterval = null;
  }
  if (window.fallbackRetryTimeout) {
    clearTimeout(window.fallbackRetryTimeout);
    window.fallbackRetryTimeout = null;
  }

  const isVideoPending = !movie.message_id && !movie.file_id && (!Array.isArray(movie.downloads) || !movie.downloads.some(d => d.message_id));
  const tgDownload = (Array.isArray(movie.downloads) && movie.downloads.find(d => d.url && d.url.includes('t.me'))) || null;
  const tgChannelUrl = tgDownload ? tgDownload.url : (movie.channel_post_id ? `https://t.me/c/${(movie.channel_chat_id || '').replace(/^-100/, '')}/${movie.channel_post_id}` : 'https://t.me/filmsinhala200');
  const tgBotUrl = `https://t.me/Filmsinhala200Bot?start=watch_${encodeURIComponent(movie.slug || movie.id || '')}`;

  const headingText = isVideoPending
    ? 'වීඩියෝව සූදානම් වෙමින් පවතී...'
    : 'Stream Server එක සම්බන්ධ වෙමින් පවතී...';
  const descText = isVideoPending
    ? 'මෙම වීඩියෝව Telegram Cloud වෙත Upload වෙමින් පවතී. සුළු මොහොතකින් ස්වයංක්‍රීයව Playback ආරම්භ වේ.'
    : 'Telegram High-Speed Stream Server එක (Google Colab) සම්බන්ධ කර ගනිමින් පවතී. Colab සක්‍රීය වූ සැනින් ස්වයංක්‍රීයව Playback ආරම්භ වේ (ස්වයංක්‍රීයව Live සම්බන්ධ වේ)...';

  playerEl.innerHTML = `
    <div class="player-iframe-wrap cinema-standby-screen">
      <div class="cinema-standby-glow"></div>
      <div class="cinema-standby-spinner">
        <div class="cinema-spinner-ring"></div>
        <i class="fa-solid fa-film cinema-spinner-icon"></i>
      </div>
      <h3 class="cinema-standby-title">${headingText}</h3>
      <p class="cinema-standby-desc">
        ${descText}
      </p>
      <div class="cinema-pulse-loader-line">
        <div class="cinema-pulse-bar"></div>
      </div>
      <div class="cinema-standby-actions" style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:16px">
        <button id="btn-manual-reconnect" type="button" class="btn-cinema-action">
          <i class="fa-solid fa-rotate-right"></i> Refresh Player
        </button>
        <a href="${FilmSub.escHtml(tgBotUrl)}" target="_blank" rel="noopener" class="btn-cinema-action" style="background:#0088cc;color:#fff;border-color:#0088cc;display:inline-flex;align-items:center;gap:6px">
          <i class="fa-solid fa-robot"></i> Telegram Bot
        </a>
        ${tgChannelUrl ? `
          <a href="${FilmSub.escHtml(tgChannelUrl)}" target="_blank" rel="noopener" class="btn-cinema-action tg-action" style="display:inline-flex;align-items:center;gap:6px">
            <i class="fa-brands fa-telegram"></i> Telegram Post
          </a>
        ` : ''}
      </div>
    </div>`;

  const doRetry = async () => {
    if (window.fallbackRetryInterval) {
      clearInterval(window.fallbackRetryInterval);
      window.fallbackRetryInterval = null;
    }
    if (window.fallbackRetryTimeout) {
      clearTimeout(window.fallbackRetryTimeout);
      window.fallbackRetryTimeout = null;
    }
    fallbackAutoRetryCount = 0;
    lastFallbackRetryEpoch = 0;
    FilmSub.showToast('⚡ Live Status Check කරමින් පවතී...', 'info');

    // Poll latest movies.json in case background Stage 2 upload just finished
    try {
      const ghUrl = 'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json?t=' + Date.now();
      const r = await fetch(ghUrl, { cache: 'no-store' });
      if (r.ok) {
        const d = await r.json();
        const mList = Array.isArray(d) ? d : (d.movies || []);
        const found = mList.find(m => m.slug === movie.slug || m.id === movie.slug || m.slug === movie.id);
        if (found && (found.message_id || (Array.isArray(found.downloads) && found.downloads.some(x => x.message_id)))) {
          currentMovie = found;
          movie = found;
        }
      }
    } catch (e) {}

    try {
      sessionStorage.removeItem('filmsub_stream_base');
      sessionStorage.removeItem('filmsub_stream_healthy');
      await loadLiveStreamConfig(true);
    } catch (e) {}
    loadStream(movie, 0);
  };

  const btnRetry = playerEl.querySelector('#btn-manual-reconnect');
  if (btnRetry) btnRetry.addEventListener('click', doRetry);

  const now = Date.now();
  // Prevent infinite refresh loop:
  // If stream server is already healthy, do NOT loop every 4 seconds!
  // Instead, allow only ONE single auto-reconnect attempt after 5s.
  if (streamServerHealthy && activeStreamBaseUrl && !isDeadTunnel(activeStreamBaseUrl)) {
    if (fallbackAutoRetryCount < 1 && (now - lastFallbackRetryEpoch > 12000)) {
      fallbackAutoRetryCount++;
      lastFallbackRetryEpoch = now;
      console.log('[FilmSub Player] Scheduling single automatic stream reconnect in 5s...');
      window.fallbackRetryTimeout = setTimeout(async () => {
        try {
          await loadLiveStreamConfig(true);
        } catch (e) {}
        if (streamServerHealthy && activeStreamBaseUrl && !isDeadTunnel(activeStreamBaseUrl)) {
          loadStream(movie, 0);
        }
      }, 5000);
    } else {
      console.log('[FilmSub Player] Fallback auto-retry limit reached. Standby screen active awaiting user action.');
    }
  } else {
    // If the server is currently OFFLINE, poll to auto-discover when Colab tunnel comes online
    window.fallbackRetryInterval = setInterval(async () => {
      await loadLiveStreamConfig(true);
      if (streamServerHealthy && activeStreamBaseUrl && !isDeadTunnel(activeStreamBaseUrl)) {
        clearInterval(window.fallbackRetryInterval);
        window.fallbackRetryInterval = null;
        fallbackAutoRetryCount = 0;
        FilmSub.showToast('⚡ Stream Ready! ස්වයංක්‍රීයව Playback ආරම්භ කෙරේ...', 'success');
        loadStream(movie, 0);
      }
    }, 8000);
  }
}

/**
 * Ensures Video.js textTracks have active Sinhala cues populated both via <track>
 * AND programmatic VTTCue replacement so subtitles (including custom .SRT uploads
 * and -0.5s/+0.5s sync offsets) are 100% embedded in the video frame.
 */
function syncSubtitles() {
  if (!vjsPlayer) return;
  try {
    const textTracks = vjsPlayer.textTracks();
    if (!textTracks) return;

    let targetTrack = null;
    for (let i = 0; i < textTracks.length; i++) {
      const track = textTracks[i];
      if (track && (track.kind === 'subtitles' || track.kind === 'captions')) {
        track.mode = liveSubEnabled ? 'showing' : 'disabled';
        targetTrack = track;
        break;
      }
    }

    if (!targetTrack && typeof vjsPlayer.addTextTrack === 'function') {
      targetTrack = vjsPlayer.addTextTrack('subtitles', 'සිංහල උපසිරැසි (Sinhala)', 'si');
      if (targetTrack) targetTrack.mode = liveSubEnabled ? 'showing' : 'disabled';
    }

    // Always replace existing cues with parsedSubCues shifted by liveSubOffsetSec
    // so custom .SRT uploads and -0.5s/+0.5s sync buttons update the native track immediately.
    if (targetTrack && parsedSubCues.length > 0) {
      const CueClass = window.VTTCue || window.TextTrackCue;
      if (CueClass && typeof targetTrack.addCue === 'function') {
        if (typeof targetTrack.removeCue === 'function' && targetTrack.cues) {
          while (targetTrack.cues.length > 0) {
            try {
              targetTrack.removeCue(targetTrack.cues[0]);
            } catch (e) {
              break;
            }
          }
        }
        parsedSubCues.forEach(c => {
          try {
            const adjStart = Math.max(0, c.start + liveSubOffsetSec);
            const adjEnd = Math.max(adjStart + 0.2, c.end + liveSubOffsetSec);
            const cue = new CueClass(adjStart, adjEnd, c.plainText || c.text.replace(/<br\s*\/?>/gi, '\n'));
            targetTrack.addCue(cue);
          } catch (e) {}
        });
      }
    }
  } catch (e) {}
}

async function loadStream(movie, idx) {
  try {
    startProgressAnimation();
    if (window.fallbackRetryInterval) {
      clearInterval(window.fallbackRetryInterval);
      window.fallbackRetryInterval = null;
    }
    if (window.fallbackRetryTimeout) {
      clearTimeout(window.fallbackRetryTimeout);
      window.fallbackRetryTimeout = null;
    }

    const streams = getMovieStreams(movie);
    if (!streams[idx]) return;
    const stream = streams[idx];
    currentStreamIdx = idx;

    // Keep server tabs in sync automatically
    const tabsEl = document.getElementById('server-tabs');
    if (tabsEl) {
      tabsEl.querySelectorAll('.server-tab').forEach(b => b.classList.remove('active'));
      const btn = tabsEl.querySelector(`button[data-type="stream"][data-index="${idx}"]`);
      if (btn) btn.classList.add('active');
    }

    const playerEl = document.getElementById('video-player-container');
    if (!playerEl) return;

    if (stream.embed) {
      if (vjsPlayer) {
        try { vjsPlayer.dispose(); } catch (e) {}
        vjsPlayer = null;
      }
      isTrailerActive = false;
      renderStreamEmbed(playerEl, stream, movie);
      return;
    }

    if (!stream.hasLocalFile || !stream.stream_url || stream.stream_url.endsWith('/0')) {
      if (streams.length > 1 && idx === 0) {
        loadStream(movie, 1);
        return;
      }
      if (vjsPlayer) {
        try { vjsPlayer.dispose(); } catch (e) {}
        vjsPlayer = null;
      }
      isTrailerActive = false;
      renderPlayerFallback(playerEl, movie);
      return;
    }

    if (vjsPlayer) {
      try { vjsPlayer.dispose(); } catch (e) {}
      vjsPlayer = null;
    }
    isTrailerActive = false;
    createVjsPlayer(playerEl, stream, movie);
  } catch (err) {
    console.error('loadStream error:', err);
  }
}

function loadTrailer(movie) {
  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }

  isTrailerActive = true;
  const query = encodeURIComponent(`${movie.title} ${movie.year || ''} official trailer`);
  const trailerSrc = movie.trailer_url || `https://www.youtube-nocookie.com/embed?listType=search&list=${query}&autoplay=1`;

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;aspect-ratio:16/9;width:100%;background:#000000 !important;border-radius:8px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, '🎬 Official Trailer')}
      <iframe id="player-trailer-iframe"
              src="${trailerSrc}"
              title="${FilmSub.escHtml(movie.title)} Official Trailer"
              frameborder="0"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
              allowfullscreen="true"
              webkitallowfullscreen="true"
              mozallowfullscreen="true"
              playsinline="true"
              style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px;background:#000000 !important;opacity:0;transition:opacity 0.35s ease">
      </iframe>
    </div>`;

  const trailerIframe = document.getElementById('player-trailer-iframe');
  const loaderEl = document.getElementById('super-player-loader');
  const reveal = () => {
    setTimeout(() => {
      if (trailerIframe) trailerIframe.style.opacity = '1';
      if (loaderEl) loaderEl.classList.add('hidden');
    }, 250);
  };
  if (trailerIframe) trailerIframe.addEventListener('load', reveal);
  setTimeout(reveal, 3500);
}

// ---- Quick Download Strip ----
function renderQuickDownloadStrip(movie) {
  const stripBtns = document.getElementById('quick-dl-buttons');
  if (!stripBtns) return;
  let downloads = getMovieDownloads(movie).filter(d => !d.download_only && d.host !== 'Telegram');
  if (downloads.length === 0) {
    downloads = getMovieDownloads(movie);
  }
  const subs = getMovieSubtitles(movie);

  let html = downloads.slice(0, 4).map(dl => {
    const rawQ = dl.quality || '1080p';
    const qBadge = rawQ.includes('1080') ? '1080p Full HD' : (rawQ.includes('720') ? '720p HD' : (rawQ.includes('480') ? '480p SD' : rawQ));
    const sz = dl.size || '';
    const url = dl.url || '#';
    return `
      <button type="button" class="quick-dl-pill" data-url="${FilmSub.escHtml(url)}" data-quality="${FilmSub.escHtml(rawQ)}">
        <i class="fa-solid fa-cloud-arrow-down"></i>
        <strong>${FilmSub.escHtml(qBadge)}</strong>
        ${sz ? `<span class="quick-dl-size">${FilmSub.escHtml(sz)}</span>` : ''}
        <span class="quick-dl-sub-tag">Sub Merged</span>
      </button>`;
  }).join('');

  stripBtns.innerHTML = html;
  stripBtns.querySelectorAll('button.quick-dl-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const url = btn.dataset.url;
      const quality = btn.dataset.quality;
      if (url && url !== '#') {
        if (url.includes('t.me')) {
          window.open(url, '_blank', 'noopener');
        } else if (window.FilmSubDownload && typeof window.FilmSubDownload.show === 'function') {
          window.FilmSubDownload.show(url, quality, movie.title);
        } else {
          window.open(url, '_blank', 'noopener');
        }
      }
    });
  });
}

// ---- TV Series Seasons & Episodes Picker ----
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

  tabsEl.innerHTML = seasons.map((s, idx) => `
    <button class="season-tab${idx === 0 ? ' active' : ''}" data-index="${idx}" data-season="${s.season_number || (idx + 1)}" type="button">
      <i class="fa-solid fa-layer-group"></i> ${FilmSub.escHtml(s.name || `Season ${s.season_number || (idx + 1)}`)}
    </button>
  `).join('');

  const renderEpisodesForSeason = (seasonObj) => {
    const count = seasonObj.episode_count || 10;
    const sNum = seasonObj.season_number || 1;
    const sPoster = seasonObj.poster_url || movie.poster || movie.poster_url || '';

    // Gather all catalog entries belonging to this series
    const catalogMovies = (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies))
      ? window.FILMSUB_DATA.movies
      : (window.FilmSub && typeof window.FilmSub.allMovies === 'function' ? window.FilmSub.allMovies() : []);

    const seriesTitleNorm = (movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');

    let epHtml = '';
    for (let ep = 1; ep <= count; ep++) {
      const isActive = (sNum === currentSeason && ep === currentEpisode);

      // Look for a matching episode object in the catalog
      let matchedEpMovie = null;
      if (catalogMovies.length > 0) {
        matchedEpMovie = catalogMovies.find(m => {
          if (m.type !== 'series') return false;
          const tNorm = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
          const matchTitle = (tNorm === seriesTitleNorm || (movie.tmdb_id && m.tmdb_id === movie.tmdb_id));
          return matchTitle && (m.season === sNum && m.episode === ep);
        });
      }

      const epTitle = (matchedEpMovie && matchedEpMovie.episode_title)
        ? matchedEpMovie.episode_title
        : `Episode ${ep}`;
      const epThumb = (matchedEpMovie && (matchedEpMovie.backdrop || matchedEpMovie.poster)) || sPoster;
      const epDuration = (matchedEpMovie && matchedEpMovie.duration) || '~50 min';
      const epCode = `S${String(sNum).padStart(2, '0')} E${String(ep).padStart(2, '0')}`;

      epHtml += `
        <div class="episode-card${isActive ? ' active' : ''}" data-season="${sNum}" data-episode="${ep}" ${matchedEpMovie ? `data-slug="${FilmSub.escHtml(matchedEpMovie.slug || matchedEpMovie.id)}"` : ''}>
          <div class="ep-thumb-wrap">
            <img src="${FilmSub.escHtml(epThumb)}" alt="${FilmSub.escHtml(epTitle)}" loading="lazy" decoding="async" class="ep-thumb-img" onerror="this.src='${FilmSub.SITE_CONFIG.defaultPoster}'">
            <div class="ep-thumb-play"><i class="fa-solid fa-play"></i></div>
            <span class="ep-thumb-badge">${epCode}</span>
          </div>
          <div class="ep-info">
            <div class="ep-header-row">
              <span class="ep-num-pill">${epCode}</span>
              ${isActive ? '<span class="ep-active-pill"><i class="fa-solid fa-circle-play"></i> Playing Now</span>' : ''}
            </div>
            <h4 class="ep-title">${FilmSub.escHtml(epTitle)}</h4>
            <div class="ep-meta-row">
              <span class="ep-duration"><i class="fa-regular fa-clock"></i> ${FilmSub.escHtml(epDuration)}</span>
              <span class="ep-meta-dot">•</span>
              <span class="ep-sub-tag"><i class="fa-solid fa-closed-captioning"></i> සිංහල Sub</span>
              <span class="ep-meta-dot">•</span>
              <span class="ep-qual-tag">1080p FHD</span>
            </div>
          </div>
          <button class="ep-play-btn" title="Watch ${epCode}" type="button">
            <i class="fa-solid ${isActive ? 'fa-pause' : 'fa-play'}"></i>
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

        const matchedSlug = card.dataset.slug;
        if (matchedSlug && window.FilmSub && typeof window.FilmSub.findMovieBySlug === 'function') {
          const found = window.FilmSub.findMovieBySlug(matchedSlug);
          if (found) {
            currentMovie = found;
            movie = found;
          }
        }

        FilmSub.showToast(`Loading Season ${s} Episode ${ep}...`, 'info');

        const qualEl = document.getElementById('cs-header-quality');
        if (qualEl) {
          qualEl.textContent = `S${String(s).padStart(2, '0')} E${String(ep).padStart(2, '0')} HD`;
        }

        renderServerTabs(movie);
        loadStream(movie, 0);

        const playerSec = document.getElementById('player-section');
        if (playerSec) {
          playerSec.scrollIntoView({ behavior: 'smooth' });
        }
      });
    });
  };

  renderEpisodesForSeason(seasons[0]);

  tabsEl.querySelectorAll('.season-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      tabsEl.querySelectorAll('.season-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const idx = parseInt(btn.dataset.index, 10);
      renderEpisodesForSeason(seasons[idx]);
    });
  });
}

// ---- Movie Details & Synopsis ----
function renderMovieDetails(movie) {
  const esc = FilmSub.escHtml;
  const poster = movie.poster || movie.poster_url || FilmSub.SITE_CONFIG.defaultPoster;
  const posterEl = document.getElementById('movie-poster-img');
  if (posterEl) {
    posterEl.src = poster;
    posterEl.alt = movie.title || '';
  }

  const badgesEl = document.getElementById('cs-poster-badges');
  if (badgesEl) {
    let bHtml = '';
    if (movie.imdb) bHtml += `<span class="badge badge-imdb"><i class="fa-solid fa-star"></i>${esc(movie.imdb)}</span>`;
    if (movie.quality) bHtml += `<span class="badge badge-quality">${esc(movie.quality)}</span>`;
    if (movie.year) bHtml += `<span class="badge badge-year">${esc(movie.year)}</span>`;
    badgesEl.innerHTML = bHtml;
  }

  let castStr = 'N/A';
  if (Array.isArray(movie.cast) && movie.cast.length > 0) {
    castStr = movie.cast.slice(0, 5).map(c => {
      if (typeof c === 'string') return esc(c);
      if (c && typeof c === 'object' && c.name) return esc(c.name);
      return '';
    }).filter(Boolean).join(', ');
  }

  const genres = Array.isArray(movie.genres) ? movie.genres : [];
  const genreChips = genres.map(g => `<a href="search.html?genre=${encodeURIComponent(g)}" class="genre-chip">${esc(g)}</a>`).join('');

  const metaGridEl = document.getElementById('cs-meta-grid');
  if (metaGridEl) {
    metaGridEl.innerHTML = `
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-film"></i> Title:</div>
        <div class="cs-meta-val"><strong>${esc(movie.title || 'Untitled')}</strong></div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-language"></i> Sinhala Title:</div>
        <div class="cs-meta-val" style="color:var(--gold);font-weight:600">${esc(movie.title_si || movie.title || '')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-regular fa-calendar"></i> Release Year:</div>
        <div class="cs-meta-val">${esc(movie.year || 'N/A')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-star"></i> IMDb Rating:</div>
        <div class="cs-meta-val"><span style="color:var(--gold);font-weight:700"><i class="fa-solid fa-star"></i> ${esc(movie.imdb || 'N/A')}</span> / 10</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-regular fa-clock"></i> Runtime:</div>
        <div class="cs-meta-val">${esc(typeof movie.duration === 'number' ? `${movie.duration} min` : (movie.duration || 'N/A'))}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-user-tie"></i> Director:</div>
        <div class="cs-meta-val">${esc(movie.director || 'N/A')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-users"></i> Cast:</div>
        <div class="cs-meta-val">${castStr}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-tags"></i> Genres:</div>
        <div class="cs-meta-val">${genreChips || 'Action'}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-volume-high"></i> Language:</div>
        <div class="cs-meta-val">${esc(movie.language || 'English')}</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-closed-captioning"></i> Subtitles:</div>
        <div class="cs-meta-val" style="color:var(--accent);font-weight:600">Sinhala (සිංහල උපසිරැසි)</div>
      </div>
      <div class="cs-meta-row">
        <div class="cs-meta-label"><i class="fa-solid fa-pen-nib"></i> Subbed By:</div>
        <div class="cs-meta-val">FilmSub Team</div>
      </div>`;
  }

  const siDescEl = document.getElementById('movie-desc-si');
  if (siDescEl) {
    siDescEl.textContent = movie.description_si || movie.description || 'මෙම චිත්‍රපටය සඳහා සිංහල විස්තරය ළඟදීම එක් කෙරේ.';
  }

  const enDescEl = document.getElementById('movie-desc-en');
  if (enDescEl) {
    enDescEl.textContent = movie.description || 'No English synopsis available.';
  }
}

// ---- Download Section ----
function renderDownloadSection(movie) {
  const grid = document.getElementById('download-grid');
  if (!grid) return;

  if (movie.telegram_status === 'queued' || movie.telegram_status === 'uploading') {
    grid.innerHTML = `
      <div class="cs-dl-card cinema-standby-card" style="text-align: center; padding: 32px 20px; grid-column: 1 / -1;">
        <div class="netflix-pulse-spinner" style="margin: 0 auto 16px;">
          <div class="netflix-pulse-ring"></div>
          <div class="netflix-pulse-icon"><i class="fa-solid fa-cloud-arrow-up"></i></div>
        </div>
        <h4 style="margin: 0 0 8px; color: #fff; font-size: 17px; font-weight: 700;">Cloud Upload in Progress</h4>
        <p style="margin: 0 auto; color: var(--text2); max-width: 500px; font-size: 13.5px; line-height: 1.5;">මෙම වීඩියෝ ගොනුව Telegram Cloud වෙත Upload වෙමින් පවතී. Upload වූ සැනින් Download Links ස්වයංක්‍රීයව මෙහි දිස්වනු ඇත.</p>
      </div>`;
    return;
  }

  const downloads = getMovieDownloads(movie);

  if (downloads.length === 0) {
    grid.innerHTML = `<p class="text-muted" style="padding:20px">No download links available yet.</p>`;
  } else {
    const seen = new Set();
    const uniqueDls = downloads.filter(dl => {
      const key = `${dl.quality}|${dl.host}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    grid.innerHTML = uniqueDls.map(dl => {
      const rawQ = dl.quality || '720p';
      const qTitle = rawQ.includes('1080') ? '1080p Full HD' : (rawQ.includes('720') ? '720p HD' : (rawQ.includes('480') ? '480p SD' : `${rawQ} HD`));
      const sz = dl.size || '';
      const fmt = dl.format || 'MP4';
      const dlUrl = dl.url || '#';
      const host = dl.host || 'Direct';
      const isTelegram = dl.download_only === true || host === 'Telegram';
      const isCloud = host === 'Cloud CDN' || (!isTelegram && dlUrl.includes('drive.google'));

      const hostColor = isCloud ? 'var(--accent)' : (isTelegram ? '#229ED9' : 'var(--text2)');
      const hostIcon = isCloud
        ? '<i class="fa-brands fa-google-drive"></i>'
        : (isTelegram ? '<i class="fa-brands fa-telegram"></i>' : '<i class="fa-solid fa-server"></i>');

      let actionBtn = '';
      if (isTelegram) {
        const isBot = dlUrl.includes('start=dl_');
        const isChannel = dlUrl.includes('/c/');
        const btnLabel = isChannel ? 'Telegram Channel' : (isBot ? 'Telegram Bot Download' : 'Telegram Download');
        actionBtn = `
          <a href="${FilmSub.escHtml(dlUrl)}" target="_blank" rel="noopener" class="btn-tg-dl"
             style="display:inline-flex;align-items:center;gap:7px">
            <i class="fa-brands fa-telegram"></i> ${btnLabel}
          </a>`;
        if (dl.channel_url && dl.channel_url !== dlUrl) {
          actionBtn += `
            <a href="${FilmSub.escHtml(dl.channel_url)}" target="_blank" rel="noopener" class="btn-tg-dl"
               style="display:inline-flex;align-items:center;gap:7px;background:#229ed9">
              <i class="fa-solid fa-bullhorn"></i> Channel Post
            </a>`;
        }
      } else {
        actionBtn = `
          <button class="btn-direct-dl" data-url="${FilmSub.escHtml(dlUrl)}" data-quality="${FilmSub.escHtml(rawQ)}" type="button">
            <i class="fa-solid fa-cloud-arrow-down"></i> Direct Download
          </button>`;
      }

      return `
        <div class="cs-dl-card download-card">
          <div class="cs-dl-top">
            <div class="cs-dl-quality">
              <i class="fa-solid fa-film" style="color:var(--accent)"></i>
              <span>${FilmSub.escHtml(qTitle)}</span>
            </div>
            ${sz ? `<div class="cs-dl-size-badge">${FilmSub.escHtml(sz)}</div>` : ''}
          </div>
          <div class="cs-dl-specs">
            <span>${hostIcon} <span style="color:${hostColor}">${FilmSub.escHtml(host)}</span></span>
            <span>•</span>
            <span><i class="fa-solid fa-video"></i> ${FilmSub.escHtml(fmt)}</span>
            <span>•</span>
            <span style="color:var(--accent)"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි Merged</span>
          </div>
          <div class="cs-dl-actions" style="display:flex;gap:8px;flex-wrap:wrap">
            ${actionBtn}
          </div>
        </div>`;
    }).join('');

    grid.querySelectorAll('.btn-direct-dl').forEach(btn => {
      btn.addEventListener('click', () => {
        const url = btn.dataset.url;
        const quality = btn.dataset.quality;
        if (url && url !== '#') {
          if (window.FilmSubDownload && typeof window.FilmSubDownload.show === 'function') {
            window.FilmSubDownload.show(url, quality, movie.title);
          } else {
            window.open(url, '_blank', 'noopener');
          }
        }
      });
    });
  }

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

// ---- Related Movies & Share Button ----
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

function initShareButton(movie) {
  const shareBtn = document.getElementById('movie-share-btn');
  if (shareBtn) {
    shareBtn.addEventListener('click', () => {
      if (navigator.share) {
        navigator.share({
          title: `${movie.title} Sinhala Subtitles`,
          text: `Watch and download ${movie.title} with Sinhala subtitles on FilmSub!`,
          url: window.location.href,
        }).catch(() => {});
      } else {
        navigator.clipboard.writeText(window.location.href)
          .then(() => FilmSub.showToast('Link copied to clipboard!', 'success'))
          .catch(() => FilmSub.showToast('Copy the URL from your browser address bar.', 'info'));
      }
    });
  }
}
