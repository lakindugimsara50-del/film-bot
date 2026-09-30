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
   4. Multi-Server Clean VIP Players:
      - Server 1: ⚡ Super Player (Telegram Cloud HD / Auto Sinhala Sub)
      - Server 2: 🎬 VIP Player 1 (VidLink Pro Ultra HD)
      - Server 3: ⚡ VIP Player 2 (AutoEmbed HD)
      - Server 4: 🚀 VIP Player 3 (MultiEmbed Fast)
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

const QUALITY_LADDER = ['1080p', '720p', '480p', '360p'];
let selectedQuality = 'auto';
let currentEffectiveQuality = '1080p';
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

let activeStreamBaseUrl = (window.FILMSUB_STREAM_CONFIG && window.FILMSUB_STREAM_CONFIG.stream_base_url) || '';
let streamServerHealthy = false;

async function probeStreamServerHealth(baseUrl) {
  if (!baseUrl || !baseUrl.startsWith('http')) return false;
  try {
    const ctrl = new AbortController();
    const tId = setTimeout(() => ctrl.abort(), 2500);
    const resp = await fetch(`${baseUrl.replace(/\/+$/, '')}/health?t=${Date.now()}`, {
      method: 'GET',
      signal: ctrl.signal,
      mode: 'cors',
    });
    clearTimeout(tId);
    return resp.ok;
  } catch (e) {
    return false;
  }
}

async function loadLiveStreamConfig() {
  const cached = sessionStorage.getItem('filmsub_stream_base');
  const cachedHealthy = sessionStorage.getItem('filmsub_stream_healthy');
  const cachedTime = parseInt(sessionStorage.getItem('filmsub_stream_base_time') || '0', 10);
  if (cached !== null && (Date.now() - cachedTime) < 30000) {
    activeStreamBaseUrl = cached;
    streamServerHealthy = cachedHealthy === '1';
    return activeStreamBaseUrl;
  }

  let candidateUrl = '';

  // 1. Check fresh GitHub raw config FIRST (always has the latest Colab/Cloudflare tunnel URL)
  try {
    const ghUrl = 'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json?t=' + Date.now();
    const ctrl = new AbortController();
    const tId = setTimeout(() => ctrl.abort(), 2500);
    const rGh = await fetch(ghUrl, { signal: ctrl.signal, cache: 'no-store' });
    clearTimeout(tId);
    if (rGh.ok) {
      const d = await rGh.json();
      if (d && d.stream_base_url) {
        candidateUrl = String(d.stream_base_url).replace(/\/+$/, '');
      }
    }
  } catch (e) {}

  // 2. Fallback to local endpoint only if GitHub raw was unreachable
  if (!candidateUrl) {
    try {
      const rLoc = await fetch('data/stream_endpoint.json?t=' + Date.now(), { cache: 'no-store' });
      if (rLoc.ok) {
        const d = await rLoc.json();
        if (d && d.stream_base_url) {
          candidateUrl = String(d.stream_base_url).replace(/\/+$/, '');
        }
      }
    } catch (e) {}
  }

  // 3. Verify direct browser reachability to candidateUrl (/health);
  //    if blocked by ISP or offline, fall back to '' so requests route via
  //    Cloudflare Pages Edge Proxy (/stream/channel/:chat_id/:msg_id)
  if (candidateUrl) {
    const directOk = await probeStreamServerHealth(candidateUrl);
    if (directOk) {
      activeStreamBaseUrl = candidateUrl;
      streamServerHealthy = true;
    } else {
      // Try Edge Proxy health or allow Edge Proxy path `/stream/channel/...`
      activeStreamBaseUrl = '';
      streamServerHealthy = false;
    }
  } else {
    activeStreamBaseUrl = '';
    streamServerHealthy = false;
  }

  try {
    sessionStorage.setItem('filmsub_stream_base', activeStreamBaseUrl);
    sessionStorage.setItem('filmsub_stream_healthy', streamServerHealthy ? '1' : '0');
    sessionStorage.setItem('filmsub_stream_base_time', String(Date.now()));
  } catch (e) {}

  return activeStreamBaseUrl;
}

function isDeadTunnel(u) {
  if (!u || typeof u !== 'string') return true;
  const l = u.toLowerCase();
  if (activeStreamBaseUrl && u.startsWith(activeStreamBaseUrl)) return false;
  return l.includes('trycloudflare.com') ||
         l.includes('loca.lt') ||
         l.includes('ngrok.io') ||
         l.includes('ngrok-free.app') ||
         l.includes('127.0.0.1') ||
         l.includes('localhost');
}

function normalizeStreamUrl(u) {
  if (!u || typeof u !== 'string') return '';
  const match = u.match(/\/stream\/channel\/(-?\d+)\/(\d+)/);
  if (match) {
    const cId = match[1];
    const mId = match[2];
    if (activeStreamBaseUrl && activeStreamBaseUrl.startsWith('http')) {
      return `${activeStreamBaseUrl}/stream/channel/${cId}/${mId}`;
    }
    return `/stream/channel/${cId}/${mId}`;
  }
  const autoMatchTv = u.match(/autoembed\.(?:co|cc|to)\/tv\/(?:imdb|tmdb)\/([a-zA-Z0-9_-]+)-(\d+)-(\d+)/i);
  if (autoMatchTv) {
    return `https://player.autoembed.cc/embed/tv/${autoMatchTv[1]}/${autoMatchTv[2]}/${autoMatchTv[3]}`;
  }
  const autoMatchMovie = u.match(/autoembed\.(?:co|cc|to)\/movie\/(?:imdb|tmdb)\/([a-zA-Z0-9_-]+)/i);
  if (autoMatchMovie) {
    return `https://player.autoembed.cc/embed/movie/${autoMatchMovie[1]}`;
  }
  return isDeadTunnel(u) ? '' : u;
}

document.addEventListener('DOMContentLoaded', async () => {
  await waitForFilmSub();
  await FilmSub.loadMovies();
  await loadLiveStreamConfig();

  const slug = getSlugFromURL();
  if (!slug) {
    showError('Movie not found. Please go back and try again.');
    return;
  }

  currentMovie = FilmSub.findMovieBySlug(slug);
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
  await loadParsedSubtitles(currentMovie);
  renderServerTabs(currentMovie);
  initAdaptiveQuality(currentMovie);
  initSubtitleControls(currentMovie);
  initVideoPlayer(currentMovie);
  renderQuickDownloadStrip(currentMovie);
  renderSeriesSection(currentMovie);
  renderMovieDetails(currentMovie);
  renderDownloadSection(currentMovie);
  renderRelatedMovies(currentMovie);
  initShareButton(currentMovie);
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
 * Curates exactly 2 clean zero-ads players:
 * - Server 1: ⚡ Super Player (Telegram Cloud HD • Zero Ads) — Native Video.js HTML5 player, zero ads, auto Sinhala sub
 * - Server 2: 🎬 VIP Player (VidLink Ultra HD • Zero Ads) — Fast HD Embed with auto Sinhala sub
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
  let tgMsgId = movie.channel_post_id || movie.message_id || 0;

  if (!tgMsgId && Array.isArray(movie.downloads)) {
    for (const d of movie.downloads) {
      if (d.message_id) {
        tgMsgId = d.message_id;
      }
      const matchC = (d.url || '').match(/t\.me\/c\/(\d+)\/(\d+)/);
      if (matchC) {
        tgChatId = tgChatId || `-100${matchC[1]}`;
        tgMsgId = tgMsgId || parseInt(matchC[2], 10);
      }
      const matchU = (d.url || '').match(/t\.me\/([^/]+)\/(\d+)/);
      if (matchU) {
        tgChatId = tgChatId || matchU[1];
        tgMsgId = tgMsgId || parseInt(matchU[2], 10);
      }
      if (tgMsgId) break;
    }
  }

  const existingTgStream = Array.isArray(movie.streams)
    ? movie.streams.find(s => s.mode === 'super_chunk' || (s.stream_url && !s.embed && !s.stream_url.includes('vidlink') && !s.stream_url.includes('autoembed') && !s.stream_url.includes('multiembed')))
    : null;

  const vm = movie.variant_media || (movie.movie_entry && movie.movie_entry.variant_media);
  if (vm && typeof vm === 'object') {
    const qKey = (currentEffectiveQuality || '1080p').toLowerCase();
    const vOpt = vm[qKey] || vm['1080p'] || vm['720p'] || vm['480p'] || vm['360p'] || Object.values(vm)[0];
    if (vOpt) {
      if (vOpt.message_id) {
        tgMsgId = vOpt.message_id;
      } else if (vOpt.stream_url) {
        const norm = normalizeStreamUrl(vOpt.stream_url);
        if (norm) nativeStreamUrl = norm;
      }
    }
  }

  if (tgMsgId) {
    if (!tgChatId) tgChatId = '-1004325759505';
    if (activeStreamBaseUrl && activeStreamBaseUrl.startsWith('http')) {
      nativeStreamUrl = `${activeStreamBaseUrl}/stream/channel/${tgChatId}/${tgMsgId}`;
    } else {
      nativeStreamUrl = `/stream/channel/${tgChatId}/${tgMsgId}`;
    }
  } else if (!nativeStreamUrl && existingTgStream && existingTgStream.stream_url && !existingTgStream.stream_url.includes('vidlink') && !existingTgStream.stream_url.includes('autoembed') && !existingTgStream.stream_url.includes('multiembed')) {
    nativeStreamUrl = normalizeStreamUrl(existingTgStream.stream_url);
  } else if (!nativeStreamUrl && driveId) {
    nativeStreamUrl = `/api/stream?id=${encodeURIComponent(driveId)}`;
  } else if (!nativeStreamUrl && primaryUrl && !primaryUrl.includes('vidlink') && !primaryUrl.includes('autoembed') && !primaryUrl.includes('multiembed') && !primaryUrl.includes('embed')) {
    nativeStreamUrl = normalizeStreamUrl(primaryUrl);
  }

  if (isMatchingEpisode && nativeStreamUrl && !nativeStreamUrl.includes('vidlink') && !nativeStreamUrl.includes('autoembed') && !nativeStreamUrl.includes('multiembed')) {
    list.push({
      server: `Server ${srvCounter}`,
      label: `⚡ Super Player (Telegram Cloud HD • Auto Sinhala Sub)`,
      mode: 'super_chunk',
      type: 'video/mp4',
      embed: false,
      stream_url: nativeStreamUrl,
      hasLocalFile: true,
    });
    srvCounter++;
  } else if (nativeStreamUrl && !nativeStreamUrl.includes('vidlink') && !nativeStreamUrl.includes('autoembed') && !nativeStreamUrl.includes('multiembed')) {
    list.push({
      server: `Server ${srvCounter}`,
      label: `⚡ Super Player (Telegram Cloud HD • Auto Sinhala Sub)`,
      mode: 'super_chunk',
      type: 'video/mp4',
      embed: false,
      stream_url: nativeStreamUrl,
      hasLocalFile: true,
    });
    srvCounter++;
  }

  // =========================================================================
  // Server 2: 🎬 VIP Player 1 (VidLink Pro Ultra HD • Zero Ads • Auto Sinhala Sub)
  // [Universal Embed Player containing every movie & series in the world]
  // =========================================================================
  let vidlinkUrl = '';
  let autoEmbedUrl = '';
  let twoEmbedUrl = '';

  if (isSeries) {
    if (tmdbId) {
      vidlinkUrl = `https://vidlink.pro/tv/${tmdbId}/${sNum}/${eNum}`;
      autoEmbedUrl = `https://player.autoembed.cc/embed/tv/${imdbId || tmdbId}/${sNum}/${eNum}`;
      twoEmbedUrl = `https://www.2embed.cc/embedtv/${tmdbId}&s=${sNum}&e=${eNum}`;
    } else if (imdbId) {
      vidlinkUrl = `https://vidlink.pro/tv/${imdbId}/${sNum}/${eNum}`;
      autoEmbedUrl = `https://player.autoembed.cc/embed/tv/${imdbId}/${sNum}/${eNum}`;
      twoEmbedUrl = `https://www.2embed.cc/embedtv/${imdbId}&s=${sNum}&e=${eNum}`;
    } else {
      vidlinkUrl = `https://vidlink.pro/tv/1399/${sNum}/${eNum}`;
      autoEmbedUrl = `https://player.autoembed.cc/embed/tv/tt0944947/${sNum}/${eNum}`;
    }
  } else {
    if (tmdbId) {
      vidlinkUrl = `https://vidlink.pro/movie/${tmdbId}`;
      autoEmbedUrl = `https://player.autoembed.cc/embed/movie/${imdbId || tmdbId}`;
      twoEmbedUrl = `https://www.2embed.cc/embed/${tmdbId}`;
    } else if (imdbId) {
      vidlinkUrl = `https://vidlink.pro/movie/${imdbId}`;
      autoEmbedUrl = `https://player.autoembed.cc/embed/movie/${imdbId}`;
      twoEmbedUrl = `https://www.2embed.cc/embed/${imdbId}`;
    } else {
      vidlinkUrl = `https://vidlink.pro/movie/550`;
      autoEmbedUrl = `https://player.autoembed.cc/embed/movie/tt0137523`;
    }
  }

  // Inject Sinhala subtitle URL into VidLink if available
  const subList = getMovieSubtitles(movie);
  const primarySub = subList && subList[0] && subList[0].url && !subList[0].url.startsWith('data:') ? subList[0].url : '';
  if (primarySub && vidlinkUrl && !vidlinkUrl.includes('sub.Sinhala')) {
    const sep = vidlinkUrl.includes('?') ? '&' : '?';
    vidlinkUrl = `${vidlinkUrl}${sep}sub.Sinhala=${encodeURIComponent(primarySub)}`;
  }

  const s2Url = vidlinkUrl || autoEmbedUrl || twoEmbedUrl;
  if (s2Url) {
    list.push({
      server: `Server ${srvCounter}`,
      label: `🎬 VIP Player 1 (VidLink Pro Ultra HD • Zero Ads)`,
      mode: 'external_embed',
      type: 'embed',
      embed: true,
      stream_url: s2Url,
      hasLocalFile: true,
      alt_urls: {
        vidlink: vidlinkUrl,
        autoembed: autoEmbedUrl,
        twoembed: twoEmbedUrl,
      },
    });
    srvCounter++;
  }

  // =========================================================================
  // Server 3: ⚡ VIP Player 2 (AutoEmbed HD • Zero Ads)
  // =========================================================================
  if (autoEmbedUrl && autoEmbedUrl !== s2Url) {
    list.push({
      server: `Server ${srvCounter}`,
      label: `⚡ VIP Player 2 (AutoEmbed HD • Zero Ads)`,
      mode: 'external_embed',
      type: 'embed',
      embed: true,
      stream_url: autoEmbedUrl,
      hasLocalFile: true,
      alt_urls: {
        autoembed: autoEmbedUrl,
        twoembed: twoEmbedUrl,
      },
    });
    srvCounter++;
  }

  // =========================================================================
  // Server 4: 🚀 VIP Player 3 (MultiEmbed / 2Embed Fast Stream)
  // =========================================================================
  if (twoEmbedUrl && twoEmbedUrl !== s2Url && twoEmbedUrl !== autoEmbedUrl) {
    list.push({
      server: `Server ${srvCounter}`,
      label: `🚀 VIP Player 3 (2Embed Fast Stream)`,
      mode: 'external_embed',
      type: 'embed',
      embed: true,
      stream_url: twoEmbedUrl,
      hasLocalFile: true,
    });
    srvCounter++;
  }

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
    'FilmSub.lk Super Player — 1080p / 720p / 480p / 360p Adaptive Chunk Stream',
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

  let tabsHtml = streams.map((s, i) => `
    <button class="server-tab${i === currentStreamIdx ? ' active' : ''}" data-type="stream" data-index="${i}" type="button">
      <i class="${icons[i] || 'fa-solid fa-server'}"></i>
      ${FilmSub.escHtml(s.label || s.server || `Server ${i + 1}`)}
    </button>`).join('');

  tabsHtml += `
    <button class="server-tab" data-type="trailer" type="button">
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

// ---- 3b. Adaptive Quality & Network-Aware Streaming Engine ----
function detectNetworkSpeed() {
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (!conn) {
    return { speed: 'fast', downlink: 10, effectiveType: '4g', recommendedQuality: '1080p' };
  }
  const downlink = typeof conn.downlink === 'number' ? conn.downlink : 6;
  const effectiveType = conn.effectiveType || '4g';
  const saveData = Boolean(conn.saveData);

  if (saveData || downlink < 1.1 || effectiveType === '2g' || effectiveType === 'slow-2g') {
    return { speed: 'slow', downlink, effectiveType, recommendedQuality: '360p' };
  }
  if (downlink < 2.2 || effectiveType === '3g') {
    return { speed: 'medium-slow', downlink, effectiveType, recommendedQuality: '480p' };
  }
  if (downlink < 4.5) {
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
        speedBadge.innerHTML = `<i class="fa-solid fa-signal" style="color:#e50914"></i> Auto (${activeTier} Data Saver)`;
      } else if (activeTier === '480P') {
        speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#f5c518"></i> Auto (${activeTier} Smooth)`;
      } else if (activeTier === '720P') {
        speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#46d369"></i> Auto (${activeTier} HD)`;
      } else {
        speedBadge.innerHTML = `<i class="fa-solid fa-bolt" style="color:#46d369"></i> Auto (${activeTier} FHD • ${net.downlink || 10}Mbps)`;
      }
    } else {
      speedBadge.innerHTML = `<i class="fa-solid fa-circle-check" style="color:#46d369"></i> ${activeTier} Locked`;
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
  const prevEffectiveQuality = currentEffectiveQuality;
  const prevDriveId = extractDriveIdForQuality(currentMovie, prevEffectiveQuality);

  if (!isAutoDowngrade) {
    selectedQuality = targetQuality;
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

    // 1. Check if movie has multi-quality variant_media (Telegram Cloud)
    const vm = currentMovie && (currentMovie.variant_media || (currentMovie.movie_entry && currentMovie.movie_entry.variant_media));
    if (vm && typeof vm === 'object') {
      let vEntry = vm[qNorm] || vm[currentEffectiveQuality];
      if (!vEntry) {
        // Fallback to highest available quality
        const order = ['1080p', '720p', '480p', '360p'];
        for (const q of order) {
          if (vm[q]) { vEntry = vm[q]; break; }
        }
      }
      if (vEntry) {
        let tgChatId = currentMovie.channel_chat_id || '';
        if (!tgChatId) {
          const mUrl = currentMovie.stream_url || (vEntry.stream_url || '');
          const mMatch = mUrl.match(/\/stream\/channel\/(-?\d+)\//);
          tgChatId = mMatch ? mMatch[1] : '-1004325759505';
        }
        if (vEntry.message_id) {
          if (activeStreamBaseUrl && activeStreamBaseUrl.startsWith('http')) {
            newSrc = `${activeStreamBaseUrl}/stream/channel/${tgChatId}/${vEntry.message_id}`;
          } else {
            newSrc = `/stream/channel/${tgChatId}/${vEntry.message_id}`;
          }
        } else if (vEntry.stream_url) {
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
        } else if (matched.url && matched.url.includes('/c/')) {
          const mMatch = matched.url.match(/t\.me\/c\/(\d+)\/(\d+)/);
          if (mMatch) {
            newSrc = `/stream/channel/-100${mMatch[1]}/${mMatch[2]}`;
          }
        } else if (matched.url && !matched.url.includes('drive.google.com/uc') && !matched.url.includes('t.me') && !matched.url.startsWith('/api/download')) {
          newSrc = normalizeStreamUrl(matched.url);
        }
      }
    }

    const currentSrc = (typeof vjsPlayer.currentSrc === 'function' ? vjsPlayer.currentSrc() : '') || '';
    const shouldReloadSrc = newSrc && (!isAutoDowngrade || (newDriveId && prevDriveId && newDriveId !== prevDriveId) || (currentSrc && !currentSrc.includes(newSrc)));

    if (newSrc && shouldReloadSrc && currentSrc !== newSrc) {
      vjsPlayer.src({ src: newSrc, type: 'video/mp4' });
      let restored = false;
      const restorePlayhead = () => {
        if (restored) return;
        restored = true;
        try {
          if (curTime > 0) vjsPlayer.currentTime(curTime);
        } catch (e) {}
        syncSubtitles();
        if (!wasPaused) {
          try { vjsPlayer.play().catch(() => {}); } catch (e) {}
        }
      };
      vjsPlayer.one('loadedmetadata', restorePlayhead);
      vjsPlayer.one('canplay', restorePlayhead);
      setTimeout(restorePlayhead, 1500);
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
    pill.addEventListener('click', () => {
      const q = pill.dataset.quality || 'auto';
      applyQualitySwitch(q, { isAutoDowngrade: false });
    });
  });

  // Monitor real-time browser network strength changes
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn && conn.addEventListener) {
    conn.addEventListener('change', () => {
      if (selectedQuality === 'auto') {
        const net = detectNetworkSpeed();
        if (net.recommendedQuality !== currentEffectiveQuality) {
          applyQualitySwitch(net.recommendedQuality, { isAutoDowngrade: true });
        } else {
          updateQualitySpeedBadge('auto');
        }
      }
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

  const triggerStepDownIfNeeded = () => {
    if (selectedQuality !== 'auto') return;
    if (Date.now() - lastAutoSwitchEpoch < 12000) return;
    const idx = QUALITY_LADDER.indexOf(currentEffectiveQuality.toLowerCase());
    if (idx !== -1 && idx < QUALITY_LADDER.length - 1) {
      const nextLower = QUALITY_LADDER[idx + 1];
      applyQualitySwitch(nextLower, { isAutoDowngrade: true });
    }
  };

  const triggerStepUpIfNeeded = () => {
    if (selectedQuality !== 'auto') return;
    if (Date.now() - lastAutoSwitchEpoch < 25000) return;
    const idx = QUALITY_LADDER.indexOf(currentEffectiveQuality.toLowerCase());
    if (idx > 0) {
      const nextHigher = QUALITY_LADDER[idx - 1];
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
    // Never count user timeline scrubbing or initial t=0 moov header fetch as a network lag stall
    if (player.seeking && player.seeking()) return;
    const curT = typeof player.currentTime === 'function' ? (player.currentTime() || 0) : 0;
    if (curT < 2.0) return;

    if (selectedQuality === 'auto') {
      const now = Date.now();
      stallTimestamps = stallTimestamps.filter(t => (now - t) < 20000);
      stallTimestamps.push(now);

      // If 2+ real playback stalls occurred within 20 seconds, step down quality immediately
      if (stallTimestamps.length >= 2) {
        stallTimestamps = [];
        triggerStepDownIfNeeded();
        return;
      }
    }

    // Or if a single mid-playback buffer stall persists longer than 2.4 seconds, auto step-down & stall recovery nudge
    if (activeStallTimer) clearTimeout(activeStallTimer);
    activeStallTimer = setTimeout(() => {
      if (player && !player.paused() && !(player.seeking && player.seeking())) {
        if (selectedQuality === 'auto') {
          triggerStepDownIfNeeded();
        }
        // Intelligent stall recovery: if playback is stalled on a keyframe gap, nudge playhead slightly
        try {
          const t = typeof player.currentTime === 'function' ? player.currentTime() : 0;
          if (t > 0) {
            player.currentTime(t + 0.05);
            player.play().catch(() => {});
          }
        } catch (e) {}
      }
    }, 2400);
  });

  player.on('playing', () => {
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }
  });

  let lastBufferCheck = 0;
  player.on('timeupdate', () => {
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }

    const now = Date.now();
    if (now - lastBufferCheck < 2500) return;
    lastBufferCheck = now;

    if (selectedQuality === 'auto' && !player.paused() && !(player.seeking && player.seeking())) {
      const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
      if (curT > 6.0) {
        const ahead = getBufferAhead(player);
        // Proactive Step-Down: if buffer ahead drops below 3.0s, step down BEFORE freezing
        if (ahead > 0 && ahead < 3.0 && (now - lastAutoSwitchEpoch > 12000)) {
          triggerStepDownIfNeeded();
        } else if (ahead > 18.0 && (now - lastAutoSwitchEpoch > 25000)) {
          // Proactive Step-Up: if buffer ahead is healthy (> 18s) and network allows, step up
          triggerStepUpIfNeeded();
        }
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

// ---- 4. Zero-White-Screen Loader HTML Builder ----
function buildSuperLoaderHtml(movie, serverLabel) {
  const title = FilmSub.escHtml(movie.title || 'Movie');
  const sLabel = FilmSub.escHtml(serverLabel || '⚡ Super Player');
  const qLabel = (selectedQuality === 'auto' ? `Auto (${currentEffectiveQuality})` : selectedQuality).toUpperCase();
  return `
    <div class="super-player-loader" id="super-player-loader">
      <div class="sp-loader-ring"></div>
      <div class="sp-loader-title">🎬 ${title}</div>
      <div class="sp-loader-sub">⚡ අධිවේගී Chunk Stream සූදානම් වෙමින් පවතී... (Initializing High-Speed Stream...)</div>
      <div class="sp-loader-badges">
        <span class="sp-loader-badge">${sLabel}</span>
        <span class="sp-loader-badge">${qLabel}</span>
        <span class="sp-loader-badge">සිංහල Sub ON</span>
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
  if (baseEmbedUrl.includes('drive.google.com')) {
    const driveId = extractDriveFileIdFromUrl(baseEmbedUrl);
    if (driveId) {
      baseEmbedUrl = `https://drive.google.com/file/d/${driveId}/preview`;
    }
  }

  const vq = mapQualityToDriveVq(currentEffectiveQuality);
  let finalEmbedUrl = baseEmbedUrl;
  if (baseEmbedUrl.includes('drive.google.com')) {
    const sep = baseEmbedUrl.includes('?') ? '&' : '?';
    finalEmbedUrl = `${baseEmbedUrl}${sep}vq=${vq}&hl=si`;
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
              src="${FilmSub.escHtml(finalEmbedUrl)}"
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
  // Safety watchdog: never leave loader stuck > 1.2s
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
      <button type="button" class="vjs-sq-item${selectedQuality === '360p' ? ' active' : ''}" data-quality="360p">
        <span>360p Data Saver</span><span>LOW</span>
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
  qWrap.addEventListener('click', (e) => {
    e.stopPropagation();
    if (menuEl) menuEl.classList.toggle('open');
  });

  qWrap.querySelectorAll('.vjs-sq-item').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const q = btn.dataset.quality || 'auto';
      if (menuEl) menuEl.classList.remove('open');
      applyQualitySwitch(q, { isAutoDowngrade: false });
    });
  });

  document.addEventListener('click', () => {
    if (menuEl) menuEl.classList.remove('open');
  });
}

/**
 * Displays a smooth Netflix/YouTube style ripple overlay during double-tap seek (+10s / -10s).
 */
function showSeekRipple(playerEl, direction) {
  if (!playerEl) return;
  const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl;
  const ripple = document.createElement('div');
  ripple.className = `seek-ripple-overlay ${direction}`;
  ripple.innerHTML = direction === 'forward'
    ? '<div class="seek-ripple-bubble"><i class="fa-solid fa-rotate-right"></i><span>+10s</span></div>'
    : '<div class="seek-ripple-bubble"><i class="fa-solid fa-rotate-left"></i><span>-10s</span></div>';
  wrap.appendChild(ripple);
  setTimeout(() => {
    if (ripple.parentNode) ripple.parentNode.removeChild(ripple);
  }, 650);
}

/**
 * Configures touch gestures on Mobile:
 * - Double-tap right: +10s forward seek
 * - Double-tap left: -10s backward seek
 */
function attachMobileTouchControls(playerEl, player) {
  if (!playerEl || !player) return;
  let lastTapTime = 0;
  let lastTapX = 0;

  const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl;
  wrap.addEventListener('touchend', (e) => {
    if (e.target.closest('.vjs-control-bar, .vjs-menu, button, input, a, .sub-controls-toolbar')) return;
    const touch = e.changedTouches && e.changedTouches[0];
    if (!touch) return;
    const now = Date.now();
    const rect = wrap.getBoundingClientRect();
    const touchX = touch.clientX - rect.left;
    const isRightHalf = touchX > (rect.width / 2);

    if (now - lastTapTime < 320 && Math.abs(touchX - lastTapX) < 100) {
      const delta = isRightHalf ? 10 : -10;
      try {
        const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
        player.currentTime(Math.max(0, curT + delta));
      } catch (err) {}
      showSeekRipple(playerEl, isRightHalf ? 'forward' : 'backward');
      lastTapTime = 0;
    } else {
      lastTapTime = now;
      lastTapX = touchX;
    }
  }, { passive: true });
}

function createVjsPlayer(playerEl, stream, movie) {
  const subtitles = getMovieSubtitles(movie);
  const isHardcoded = Boolean(movie && (movie.sub_hardcoded || movie.is_already_hardsubbed));
  const tracksHTML = subtitles.map((sub, i) => `
    <track kind="subtitles" src="${FilmSub.escHtml(sub.url || '')}"
           srclang="${FilmSub.escHtml(sub.srclang || 'si')}"
           label="${FilmSub.escHtml(sub.label || 'සිංහල උපසිරැසි')}"
           ${(sub.default || i === 0) && !isHardcoded ? 'default' : ''}>`).join('');

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000000 !important;border-radius:8px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, stream.label || stream.server)}
      <video id="filmsubPlayer" class="video-js vjs-big-play-centered vjs-theme-fantasy"
             controls preload="auto" playsinline webkit-playsinline
             style="position:absolute;top:0;left:0;width:100%;height:100%;background:#000000 !important"
             data-setup='{"fluid": true, "responsive": true}'>
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
          bandwidth: 10000000,
          bufferBasedABR: true,
          maxBufferLength: 60,
          minBufferLength: 12,
          maxBufferSize: 64 * 1024 * 1024,
          experimentalBufferClipping: false
        },
        nativeVideoTracks: true,
        nativeAudioTracks: true,
        nativeTextTracks: false
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
      try {
        if (vjsPlayer.tech_ && vjsPlayer.tech_.el_) {
          const el = vjsPlayer.tech_.el_;
          el.setAttribute('preload', 'auto');
          el.setAttribute('playsinline', '');
          el.setAttribute('webkit-playsinline', '');
        }
      } catch (e) {}

      injectInPlayerQualityControl(vjsPlayer);

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
      attachMobileTouchControls(playerEl, vjsPlayer);
      setTimeout(hideLoader, 600);
      try { vjsPlayer.play().catch(() => {}); } catch (e) {}
    });

    vjsPlayer.on('loadedmetadata', () => {
      hideLoader();
      syncSubtitles();
    });

    vjsPlayer.on('canplay', hideLoader);
    vjsPlayer.on('playing', () => {
      hideLoader();
      syncSubtitles();
    });

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

    // Ultra-smooth zero-lag watchdog: if stream header is still at readyState 0 after 4.5s
    // (e.g. stream server sleeping, Colab proxy offline, or network stall),
    // automatically switch to Server 2 (VIP VidLink Ultra HD with Sinhala sub) so playback starts with zero white screen.
    const slowHeaderWatchdog = setTimeout(() => {
      const isLocalSample = stream.stream_url && (stream.stream_url.includes('sample_stream') || stream.stream_url.startsWith('assets/'));
      if (vjsPlayer && typeof vjsPlayer.readyState === 'function' && vjsPlayer.readyState() === 0 &&
          !isLocalSample &&
          (stream.mode === 'telegram_stream' || stream.mode === 'super_chunk' || stream.mode === 'direct_mp4')) {
        const streams = getMovieStreams(movie);
        if (streams.length > 1 && currentStreamIdx === 0) {
          FilmSub.showToast('⚡ Server 1 Offline — VIP Server 2 වෙත මාරු විය...', 'info');
          setTimeout(() => {
            loadStream(movie, 1);
          }, 10);
        }
      }
    }, 4500);

    vjsPlayer.on('dispose', () => {
      clearTimeout(slowHeaderWatchdog);
    });

    // Seamless retry & multi-server failover matrix if stream encounters upstream error
    let retryAttempted = false;
    vjsPlayer.on('error', () => {
      const errDisplay = playerEl.querySelector('.vjs-error-display');
      if (errDisplay) errDisplay.style.display = 'none';

      clearTimeout(slowHeaderWatchdog);
      if (!retryAttempted && stream.mode === 'super_chunk' && stream.drive_id) {
        retryAttempted = true;
        const retryUrl = buildChunkStreamUrl(stream.drive_id, '360p') + '&retry=1';
        vjsPlayer.src({ src: retryUrl, type: 'video/mp4' });
        vjsPlayer.one('loadedmetadata', () => {
          hideLoader();
          syncSubtitles();
          try { vjsPlayer.play().catch(() => {}); } catch (e) {}
        });
        return;
      }

      const streams = getMovieStreams(movie);
      if (streams.length > 1 && currentStreamIdx < streams.length - 1) {
        const nextIdx = currentStreamIdx + 1;
        const nextServer = streams[nextIdx];
        FilmSub.showToast(`⚡ Stream server connecting — auto-switching to ${nextServer.label || 'Server 2'}...`, 'info');
        setTimeout(() => {
          loadStream(movie, nextIdx);
        }, 10);
      } else {
        renderPlayerFallback(playerEl, movie);
      }
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
  const streams = getMovieStreams(movie);
  playerEl.innerHTML = `
    <div style="width:100%;aspect-ratio:16/9;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#070707;color:#fff;padding:24px;text-align:center;gap:14px;border-radius:8px">
      <i class="fa-solid fa-bolt" style="font-size:42px;color:var(--accent)"></i>
      <h3 style="font-size:18px;margin:0">Select Backup Streaming Server</h3>
      <p style="font-size:13px;color:var(--text2);max-width:440px;margin:0">කරුණාකර පහත ඇති වෙනත් High-Speed Server එකක් තෝරන්න:</p>
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

function loadStream(movie, idx) {
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

  // If this server is an explicit embed (Server 2 Drive CDN or VIP External Backup 1/2/3), render Zero-White-Screen Embed
  if (stream.type === 'embed' || stream.embed === true) {
    renderStreamEmbed(playerEl, stream, movie);
    return;
  }

  // Otherwise (Server 1 Super Player Chunk Stream or Direct MP4), render native Video.js Super Player
  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }
  isTrailerActive = false;
  createVjsPlayer(playerEl, stream, movie);
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
    const q = dl.quality || '1080p';
    const sz = dl.size || '';
    const url = dl.url || '#';
    return `
      <button type="button" class="quick-dl-pill" data-url="${FilmSub.escHtml(url)}" data-quality="${FilmSub.escHtml(q)}">
        <i class="fa-solid fa-download"></i>
        <strong>${FilmSub.escHtml(q)}</strong>
        <span class="quick-dl-size">${FilmSub.escHtml(sz)}</span>
        <span class="quick-dl-sub-tag">සිංහල Sub</span>
      </button>`;
  }).join('');

  if (subs.length > 0 && subs[0].url) {
    html += `
      <a href="${FilmSub.escHtml(subs[0].url)}" download="${FilmSub.escHtml(movie.slug || 'movie')}-sinhala.vtt" class="quick-dl-pill sub-only-pill">
        <i class="fa-solid fa-closed-captioning"></i>
        <strong>සිංහල .SRT/.VTT</strong>
      </a>`;
  }

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
        <div class="cs-meta-val">${esc(movie.director || 'N/A')}</div>
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
        <div class="cs-meta-val">FilmSub.lk Team</div>
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
      <div class="cs-dl-card download-card" style="text-align: center; padding: 30px;">
        <i class="fa-solid fa-spinner fa-spin" style="font-size: 2em; color: var(--accent); margin-bottom: 15px;"></i>
        <h4 style="margin: 0; color: #fff;">Cloud Upload in Progress</h4>
        <p style="margin: 10px 0 0; color: var(--text2);">The video file is currently being processed and uploaded to our Telegram cloud servers. Download links will appear here automatically once the upload completes.</p>
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
      const q = dl.quality || '720p';
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
          <button class="btn-direct-dl" data-url="${FilmSub.escHtml(dlUrl)}" data-quality="${FilmSub.escHtml(q)}" type="button">
            <i class="fa-solid fa-cloud-arrow-down"></i> Direct Download
          </button>`;
      }

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
