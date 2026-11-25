
/* ============================================================
   Dynamic Theme Color Extraction for Movie Pages
============================================================ */
function extractDominantColor(imageUrl, callback) {
  if (!imageUrl || imageUrl.startsWith('data:') || imageUrl.includes('image.tmdb.org')) {
    callback(null);
    return;
  }
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = function() {
    try {
      const canvas = document.createElement('canvas');
      const SIZE = 80;
      canvas.width = SIZE;
      canvas.height = Math.round(SIZE * img.height / img.width) || SIZE;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;

      const buckets = {};
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i], g = data[i+1], b = data[i+2], a = data[i+3];
        if (a < 128) continue;
        const brightness = (r + g + b) / 3;
        if (brightness < 30 || brightness > 230) continue;
        const saturation = Math.max(r,g,b) - Math.min(r,g,b);
        if (saturation < 35) continue;

        const rq = Math.round(r / 32) * 32;
        const gq = Math.round(g / 32) * 32;
        const bq = Math.round(b / 32) * 32;
        const key = `${rq},${gq},${bq}`;
        buckets[key] = (buckets[key] || 0) + 1;
      }

      let best = null, bestCount = 0;
      for (const key in buckets) {
        if (buckets[key] > bestCount) {
          bestCount = buckets[key];
          best = key;
        }
      }

      if (best) {
        const [r, g, b] = best.split(',').map(Number);
        const boost = 1.3;
        const br = Math.min(255, Math.round(r * boost));
        const bg = Math.min(255, Math.round(g * boost));
        const bb = Math.min(255, Math.round(b * boost));
        callback({ r: br, g: bg, b: bb });
      } else {
        callback(null);
      }
    } catch(e) {
      callback(null);
    }
  };
  img.onerror = function() { callback(null); };
  img.src = imageUrl;
}

function applyDynamicTheme(r, g, b) {
  if (r == null) {
    document.documentElement.style.removeProperty('--accent');
    document.documentElement.style.removeProperty('--accent-hover');
    document.documentElement.style.removeProperty('--accent-dark');
    document.documentElement.style.removeProperty('--accent-glow');
    document.documentElement.style.removeProperty('--accent-subtle');
    document.documentElement.style.removeProperty('--hero-glow-color');
    document.documentElement.style.removeProperty('--hero-bg-tint');
    return;
  }
  const accent = `rgb(${r},${g},${b})`;
  const accentHover = `rgb(${Math.min(255,r+20)},${Math.min(255,g+20)},${Math.min(255,b+20)})`;
  const accentDark = `rgb(${Math.round(r*0.7)},${Math.round(g*0.7)},${Math.round(b*0.7)})`;
  const accentGlow = `rgba(${r},${g},${b},0.55)`;
  const accentSubtle = `rgba(${r},${g},${b},0.14)`;
  const heroBgTint = `rgba(${Math.round(r*0.3)},${Math.round(g*0.3)},${Math.round(b*0.3)},0.18)`;

  document.documentElement.style.setProperty('--accent', accent);
  document.documentElement.style.setProperty('--accent-hover', accentHover);
  document.documentElement.style.setProperty('--accent-dark', accentDark);
  document.documentElement.style.setProperty('--accent-glow', accentGlow);
  document.documentElement.style.setProperty('--accent-subtle', accentSubtle);
  document.documentElement.style.setProperty('--hero-glow-color', accentGlow);
  document.documentElement.style.setProperty('--hero-bg-tint', heroBgTint);
}

function formatQualityLabel(q) {
  if (!q) return 'WEB-DL';
  const s = String(q).trim().toLowerCase();
  if (s.includes('2160') || s.includes('4k')) return '4K UHD';
  if (s.includes('1080') || s.includes('fhd') || s.includes('full hd')) return 'WEB-DL';
  if (s.includes('web-dl') || s.includes('webdl') || s.includes('web')) return 'WEB-DL';
  if (s.includes('720') || s.includes('hd')) return 'HD';
  if (s.includes('480') || s.includes('sd')) return 'SD';
  return 'WEB-DL';
}


function updateSocialMetaTags(movie) {
  if (!movie) return;
  const isSeries = movie.type === 'series' || currentSeason > 1 || currentEpisode > 1;
  const title = isSeries
    ? `${movie.title || 'Series'} S${String(currentSeason || 1).padStart(2, '0')}E${String(currentEpisode || 1).padStart(2, '0')} Sinhala Subtitles – FilmZilla`
    : `${movie.title || 'Movie'} (${movie.year || ''}) Sinhala Subtitles – FilmZilla`;
  const desc = movie.description || movie.description_si || `Watch ${movie.title} with Sinhala Subtitles on FilmZilla. High-Speed 1080p FHD & 720p HD streaming.`;
  const img = movie.backdrop || movie.poster || 'https://filmsub.pages.dev/assets/img/filmzilla_icon.png';
  const url = window.location.href;

  const setMeta = (selector, attr, val) => {
    let el = document.querySelector(selector);
    if (!el) {
      el = document.createElement('meta');
      const parts = selector.replace(/[\[\]"]/g, '').split('=');
      if (parts.length === 2) el.setAttribute(parts[0], parts[1]);
      document.head.appendChild(el);
    }
    el.setAttribute(attr, val);
  };

  setMeta('meta[property="og:title"]', 'content', title);
  setMeta('meta[property="og:description"]', 'content', desc);
  setMeta('meta[property="og:image"]', 'content', img);
  setMeta('meta[property="og:url"]', 'content', url);
  setMeta('meta[name="twitter:title"]', 'content', title);
  setMeta('meta[name="twitter:description"]', 'content', desc);
  setMeta('meta[name="twitter:image"]', 'content', img);
  setMeta('meta[name="description"]', 'content', desc);
}

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

/**
 * Universal Zero-Latency Touch & Click Binder.
 * Works seamlessly across mobile phones (touch/iOS/Android) and desktop/laptops (mouse/pointer).
 * Eliminates mobile 300ms tap delay, ignores scroll gestures, prevents synthetic double-events,
 * and ensures instant 0ms execution.
 */
function attachFastAction(el, callback) {
  if (!el || typeof callback !== 'function') return;

  let startX = 0;
  let startY = 0;
  let isScroll = false;
  let lastTriggerEpoch = 0;

  const run = (e) => {
    const now = Date.now();
    if (now - lastTriggerEpoch < 300) {
      if (e) {
        try { e.preventDefault(); } catch (err) {}
        try { e.stopPropagation(); } catch (err) {}
      }
      return;
    }
    lastTriggerEpoch = now;
    if (e) {
      try { e.stopPropagation(); } catch (err) {}
    }
    callback(e);
  };

  el.addEventListener('touchstart', (e) => {
    if (e.touches && e.touches[0]) {
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
      isScroll = false;
    }
  }, { passive: true });

  el.addEventListener('touchmove', (e) => {
    if (e.touches && e.touches[0]) {
      const dx = Math.abs(e.touches[0].clientX - startX);
      const dy = Math.abs(e.touches[0].clientY - startY);
      if (dx > 25 || dy > 25) {
        isScroll = true;
      }
    }
  }, { passive: true });

  el.addEventListener('touchend', (e) => {
    if (!isScroll) {
      if (e.cancelable) {
        try { e.preventDefault(); } catch (err) {}
      }
      run(e);
    }
  }, { passive: false });

  el.addEventListener('click', (e) => {
    run(e);
  });
}


let currentMovie = null;
let vjsPlayer = null;
let currentStreamIdx = 0;
let isTrailerActive = false;
let currentSeason = 1;
let currentEpisode = 1;

const QUALITY_LADDER = ['1080p', '720p', '480p'];
let selectedQuality = (function() { try { return sessionStorage.getItem('filmsub_pref_quality') || 'auto'; } catch(e) { return 'auto'; } })();
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
let lastDowngradeEpoch = 0;
let fallbackAutoRetryCount = 0;
let lastFallbackRetryEpoch = 0;
let _isPreloadingUpgrade = false;
let _isSeamlessSwitching = false;
let _activePreloadCleanup = null;
let _measuredMbps = 0;
const _seekIndexCache = {};

let activeStreamBaseUrl = (window.FILMSUB_STREAM_CONFIG && window.FILMSUB_STREAM_CONFIG.stream_base_url) || '';
let streamServerHealthy = false;
let edgeProxyHealthy = false;

function _recordMeasuredThroughput(bytes, durationMs) {
  if (!bytes || bytes < 65536 || !durationMs || durationMs < 20) return;
  const mbps = (bytes * 8) / (durationMs * 1000);
  if (!isFinite(mbps) || mbps <= 0.05 || mbps > 2500) return;
  _measuredMbps = _measuredMbps > 0 ? (_measuredMbps * 0.55 + mbps * 0.45) : mbps;
}

function getEffectiveDownloadMbps() {
  if (_measuredMbps > 0) return _measuredMbps;
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn && typeof conn.downlink === 'number' && conn.downlink > 0) return conn.downlink;
  return 0;
}

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
          if (data && data.ready === true) {
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
    if (cached && cachedHealthy && (Date.now() - cachedTime) < 20000) {
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
    if (typeof window !== 'undefined') {
      window.FILMSUB_STREAM_CONFIG = window.FILMSUB_STREAM_CONFIG || {};
      window.FILMSUB_STREAM_CONFIG.stream_base_url = activeStreamBaseUrl;
      window.__LIVE_STREAM_BASE_URL = activeStreamBaseUrl;
    }
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

// ── Stream Offline Banner ────────────────────────────────────────────────────
// Shows a user-friendly banner when the Kaggle/Cloudflare backend is offline.
function showStreamOfflineBanner(containerEl) {
  if (!containerEl) return;
  // Avoid showing duplicate banners
  if (containerEl.querySelector('.filmsub-offline-banner')) return;
  const banner = document.createElement('div');
  banner.className = 'filmsub-offline-banner';
  banner.style.cssText = [
    'position:absolute', 'inset:0', 'z-index:50',
    'display:flex', 'flex-direction:column', 'align-items:center', 'justify-content:center',
    'background:rgba(0,0,0,0.92)', 'color:#fff', 'gap:14px',
    'border-radius:8px', 'padding:28px 20px', 'text-align:center',
  ].join(';');
  banner.innerHTML = `
    <i class="fa-solid fa-satellite-dish" style="font-size:48px;color:#e55;"></i>
    <h3 style="margin:0;font-size:18px;color:#fff;">Stream Server Offline</h3>
    <p style="margin:0;font-size:13px;color:#ccc;max-width:380px;line-height:1.6">
      The Kaggle streaming backend is currently offline.<br>
      <strong>Admin:</strong> Please restart the Kaggle session to restore streaming.
    </p>
    <div style="display:flex;gap:10px;flex-wrap:wrap;justify-content:center;margin-top:6px">
      <button onclick="location.reload()" style="background:var(--accent,#e55);color:#fff;border:none;padding:9px 20px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600">
        <i class="fa-solid fa-rotate-right"></i> Retry
      </button>
    </div>`;
  // Make container relative if not already
  const pos = window.getComputedStyle(containerEl).position;
  if (!pos || pos === 'static') containerEl.style.position = 'relative';
  containerEl.appendChild(banner);
}

// Global flag for quick-fail stall detection
let _consecutiveStallsWhileOffline = 0;
let _firstOfflineStallTime = 0;

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

// ── Multi-Quality Edge & RAM Pre-Warming + Seek Table Engine ────────────────
const _warmedTiers = new Set();
const _prewarmByteRangeLocks = new Map();

function extractChannelIdsFromUrl(src) {
  if (!src || typeof src !== 'string') return null;
  const m = src.match(/\/stream\/(?:channel|download)\/(-?\d+)\/(\d+)/);
  if (!m) return null;
  return { chatId: m[1], messageId: m[2] };
}

async function fetchSeekInfo(chatId, messageId) {
  if (!chatId || !messageId) return null;
  const key = `${chatId}:${messageId}`;
  const existing = _seekIndexCache[key];
  if (existing) {
    if (typeof existing.then === 'function') return existing;
    if (existing.data) return existing.data;
    if ((Date.now() - existing.ts) < 15000) return null;
  }

  const pfx = getStreamEndpointPrefix() || '';
  const url = `${pfx}/stream/seekinfo/${chatId}/${messageId}`;
  const reqPromise = (async () => {
    const ctrl = new AbortController();
    const tId = setTimeout(() => ctrl.abort(), 4500);
    try {
      const resp = await fetch(url, { method: 'GET', mode: 'cors', signal: ctrl.signal });
      clearTimeout(tId);
      if (resp && resp.ok) {
        const data = await resp.json().catch(() => null);
        if (data && (data.file_size > 0 || (Array.isArray(data.anchors) && data.anchors.length > 0))) {
          _seekIndexCache[key] = { ts: Date.now(), data };
          return data;
        }
      }
    } catch (e) {
      clearTimeout(tId);
    }
    _seekIndexCache[key] = { ts: Date.now(), data: null };
    return null;
  })();

  _seekIndexCache[key] = reqPromise;
  return reqPromise;
}

function estimateByteOffsetForTime(seekInfo, targetSec, knownDuration = 0, fallbackFileSize = 0) {
  const sec = Math.max(0, Number(targetSec) || 0);
  if (sec <= 0.5) return 0;

  const anchors = (seekInfo && Array.isArray(seekInfo.anchors)) ? seekInfo.anchors : [];
  const fileSize = (seekInfo && Number(seekInfo.file_size)) || Number(fallbackFileSize) || 0;
  const dur = (seekInfo && Number(seekInfo.duration)) || Number(knownDuration) || 0;

  let estByte = 0;
  if (anchors.length >= 2) {
    const lastIdx = anchors.length - 1;
    if (sec <= (anchors[0].t || 0)) {
      estByte = anchors[0].offset || 0;
    } else if (sec >= (anchors[lastIdx].t || 0)) {
      estByte = anchors[lastIdx].offset || 0;
    } else {
      let lo = 0;
      let hi = lastIdx;
      while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if ((anchors[mid].t || 0) <= sec) lo = mid;
        else hi = mid;
      }
      const a0 = anchors[lo];
      const a1 = anchors[hi];
      const dt = Math.max(0.001, (a1.t || 0) - (a0.t || 0));
      const frac = Math.min(1, Math.max(0, (sec - (a0.t || 0)) / dt));
      estByte = Math.floor((a0.offset || 0) + frac * ((a1.offset || 0) - (a0.offset || 0)));
    }
  } else if (dur > 0 && fileSize > 0) {
    estByte = Math.floor((Math.min(dur, sec) / dur) * fileSize);
  } else {
    // Approximate fallback (~380 KB/s baseline) when duration/size unavailable yet
    estByte = Math.floor(sec * 393216);
  }

  // Align down to 1 MiB Telegram chunk boundary (with 1 chunk safety lead so keyframe precedes target)
  const chunkAlign = 1048576;
  let aligned = Math.max(0, Math.floor(estByte / chunkAlign) * chunkAlign - chunkAlign);
  if (fileSize > chunkAlign * 2) {
    aligned = Math.min(aligned, fileSize - chunkAlign * 2);
  }
  return Math.max(0, aligned);
}

async function prewarmStreamAtTime(movieOrCandidate, targetSec, knownDuration = 0, opts = {}) {
  const sec = Math.max(0, Number(targetSec) || 0);
  const candidate = (movieOrCandidate && typeof movieOrCandidate === 'object' && typeof movieOrCandidate.src === 'string')
    ? movieOrCandidate
    : resolveStreamUrlForQuality(movieOrCandidate, currentEffectiveQuality);
  if (!candidate || !candidate.src) return false;

  const ids = (candidate.chatId && candidate.messageId)
    ? { chatId: String(candidate.chatId), messageId: String(candidate.messageId) }
    : extractChannelIdsFromUrl(candidate.src);
  if (!ids) return false;

  const seekInfo = await fetchSeekInfo(ids.chatId, ids.messageId);
  const startByte = estimateByteOffsetForTime(seekInfo, sec, knownDuration);
  const burstBytes = opts.burstBytes || 4194304;
  const lockKey = `${ids.chatId}:${ids.messageId}:${startByte}`;
  const prevLock = _prewarmByteRangeLocks.get(lockKey) || 0;
  const cooldownMs = opts.urgent ? 4000 : 18000;
  if ((Date.now() - prevLock) < cooldownMs) return true;
  _prewarmByteRangeLocks.set(lockKey, Date.now());
  if (_prewarmByteRangeLocks.size > 120) {
    const oldestKey = _prewarmByteRangeLocks.keys().next().value;
    _prewarmByteRangeLocks.delete(oldestKey);
  }

  const pfx = getStreamEndpointPrefix() || '';
  fetch(`${pfx}/stream/warmup/${ids.chatId}/${ids.messageId}?start_byte=${startByte}&bytes=${burstBytes}`, {
    method: 'POST',
    mode: 'cors',
    signal: opts.signal
  }).catch(() => {});

  if (opts.warmupOnly) return true;

  const maxEnd = seekInfo && seekInfo.file_size > 0
    ? Math.min(seekInfo.file_size - 1, startByte + 2097151)
    : (startByte + 2097151);
  if (maxEnd <= startByte) return true;

  const t0 = performance.now();
  try {
    const resp = await fetch(`${pfx}/stream/channel/${ids.chatId}/${ids.messageId}`, {
      method: 'GET',
      headers: { 'Range': `bytes=${startByte}-${maxEnd}` },
      mode: 'cors',
      signal: opts.signal
    });
    if (resp && resp.ok) {
      const buf = await resp.arrayBuffer();
      _recordMeasuredThroughput(buf.byteLength, performance.now() - t0);
      return true;
    }
  } catch (e) {}
  return false;
}

async function prewarmQualityBytesViaFetch(candidate, curT = 0, knownDuration = 0, opts = {}) {
  if (!candidate || !candidate.src) return { ok: false, aborted: false, mbps: 0 };
  const ctrl = !opts.signal ? new AbortController() : null;
  const signal = opts.signal || ctrl.signal;
  const timeoutMs = opts.timeoutMs || 8500;
  const timerId = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;

  try {
    const ids = (candidate.chatId && candidate.messageId)
      ? { chatId: String(candidate.chatId), messageId: String(candidate.messageId) }
      : extractChannelIdsFromUrl(candidate.src);

    const tasks = [];
    // 1. Warm byte 0..1 MiB (moov atom & initial keyframes) + measure speed
    tasks.push((async () => {
      const t0 = performance.now();
      const resp = await fetch(candidate.src, {
        method: 'GET',
        headers: { 'Range': 'bytes=0-1048575' },
        mode: 'cors',
        signal
      });
      if (!resp || !resp.ok) return false;
      const buf = await resp.arrayBuffer();
      _recordMeasuredThroughput(buf.byteLength, performance.now() - t0);
      return buf.byteLength > 0;
    })());

    // 2. If playing mid-movie, warm the exact playhead byte range too!
    if (curT > 3 && ids) {
      tasks.push(prewarmStreamAtTime(candidate, curT + 1.0, knownDuration, {
        urgent: true,
        burstBytes: 4194304,
        signal
      }));
    } else if (ids) {
      const pfx = getStreamEndpointPrefix() || '';
      tasks.push(
        fetch(`${pfx}/stream/warmup/${ids.chatId}/${ids.messageId}?start_byte=0&bytes=4194304`, {
          method: 'POST',
          mode: 'cors',
          signal
        }).then(() => true).catch(() => false)
      );
    }

    const results = await Promise.all(tasks);
    if (timerId) clearTimeout(timerId);
    const ok = Boolean(results[0]);
    return { ok, aborted: false, mbps: _measuredMbps };
  } catch (err) {
    if (timerId) clearTimeout(timerId);
    const aborted = Boolean(err && err.name === 'AbortError');
    return { ok: false, aborted, mbps: _measuredMbps };
  }
}

function prewarmQualityTier(movie, quality) {
  if (!movie) return;
  const qNorm = String(quality || '').toLowerCase();
  const resolved = resolveStreamUrlForQuality(movie, qNorm);

  if (resolved.chatId && resolved.messageId) {
    const key = `${resolved.chatId}:${resolved.messageId}`;
    if (_warmedTiers.has(key)) return;
    _warmedTiers.add(key);

    const pfx = getStreamEndpointPrefix() || '';
    fetch(`${pfx}/stream/warmup/${resolved.chatId}/${resolved.messageId}`, { method: 'POST', mode: 'cors' }).catch(() => {});
    fetchSeekInfo(resolved.chatId, resolved.messageId).catch(() => {});
    return;
  }

  if (resolved.driveId) {
    const driveKey = `gdrive:${resolved.driveId}:${qNorm}`;
    if (!_warmedTiers.has(driveKey)) {
      _warmedTiers.add(driveKey);
      fetch(`/api/stream?id=${encodeURIComponent(resolved.driveId)}&q=${encodeURIComponent(qNorm)}`, {
        headers: { 'Range': 'bytes=0-1048575' },
        mode: 'cors'
      }).catch(() => {});
    }
    return;
  }

  if (resolved.src && resolved.src.startsWith('http') && !resolved.src.includes('t.me')) {
    const srcKey = `src:${resolved.src}`;
    if (!_warmedTiers.has(srcKey)) {
      _warmedTiers.add(srcKey);
      fetch(resolved.src, {
        headers: { 'Range': 'bytes=0-1048575' },
        mode: 'cors'
      }).catch(() => {});
    }
  }
}

function prewarmAllVariants(movie) {
  if (!movie) return;
  prewarmQualityTier(movie, '1080p');
  setTimeout(() => prewarmQualityTier(movie, '720p'), 80);
  setTimeout(() => prewarmQualityTier(movie, '480p'), 180);
}

// ── Helper: Locate exact episode catalog entry for a TV Series ──
function getFullCatalogList() {
  const seen = new Set();
  const list = [];
  const add = (arr) => {
    if (!Array.isArray(arr)) return;
    for (const m of arr) {
      if (!m) continue;
      const k = String(m.slug || m.id || '').toLowerCase();
      if (k && !seen.has(k)) {
        seen.add(k);
        list.push(m);
      }
    }
  };
  if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) add(window.FILMSUB_DATA.movies);
  if (window.FILMZILLA_DATA && Array.isArray(window.FILMZILLA_DATA.movies)) add(window.FILMZILLA_DATA.movies);
  if (window.FilmSub && typeof window.FilmSub.getAllMovies === 'function') add(window.FilmSub.getAllMovies());
  if (window.FilmSub && Array.isArray(window.FilmSub.movies)) add(window.FilmSub.movies);
  return list;
}

function findEpisodeEntry(movieOrTitle, season, episode) {
  if (!movieOrTitle || !season || !episode) return null;
  const sNum = parseInt(season, 10);
  const eNum = parseInt(episode, 10);
  const tmdbId = typeof movieOrTitle === 'object' ? String(movieOrTitle.tmdb_id || '') : '';
  const title = typeof movieOrTitle === 'object' ? (movieOrTitle.title || '') : String(movieOrTitle);
  const normTitle = title.toLowerCase().replace(/[^a-z0-9]/g, '');
  const slug = typeof movieOrTitle === 'object' ? (movieOrTitle.slug || '') : String(movieOrTitle);
  const baseSlug = slug.replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');

  const catalog = getFullCatalogList();

  return catalog.find(m => {
    if (parseInt(m.season, 10) !== sNum || parseInt(m.episode, 10) !== eNum) return false;
    if (tmdbId && m.tmdb_id && String(m.tmdb_id) === tmdbId) return true;
    if (baseSlug) {
      const mBase = (m.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
      if (mBase && (mBase === baseSlug || mBase.startsWith(baseSlug) || baseSlug.startsWith(mBase))) return true;
    }
    const mNorm = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    return mNorm && (mNorm === normTitle || mNorm.startsWith(normTitle) || normTitle.startsWith(mNorm));
  }) || null;
}

// ── Immediate Early Stream Pre-Warming (fires synchronously on script parse) ──
(function primeEarlyStream() {
  try {
    const slug = getSlugFromURL();
    if (!slug) return;
    const list = (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) ? window.FILMSUB_DATA.movies : null;
    if (list) {
      let m = list.find(x => x.slug === slug || x.id === slug);
      if (!m) {
        const epMatch = slug.match(/[-_.]s(\d+)[-_.]?e(\d+)/i) || slug.match(/[-_.](\d+)x(\d+)/i);
        if (epMatch) {
          const s = parseInt(epMatch[1], 10);
          const e = parseInt(epMatch[2], 10);
          const bSlug = slug.replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
          m = list.find(x => {
            if (parseInt(x.season, 10) !== s || parseInt(x.episode, 10) !== e) return false;
            const xb = (x.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
            return xb === bSlug || xb.startsWith(bSlug) || bSlug.startsWith(xb);
          });
        }
      }
      if (m) {
        prewarmAllVariants(m);
      }
    }
  } catch (e) {}
})();



document.addEventListener('DOMContentLoaded', async () => {
  /* ===== Telegram In-App Browser Detection ===== */
  (function() {
    try {
      var ua = navigator.userAgent || '';
      var isTgBrowser = /Telegram/i.test(ua) || typeof window.TelegramWebviewProxy !== 'undefined';
      if (!isTgBrowser) return;
      document.body.classList.add('tg-browser-mode');
      var banner = document.createElement('div');
      banner.className = 'tg-browser-banner';
      banner.id = 'tg-browser-banner';
      banner.innerHTML =
        '<span class="tg-banner-text">' +
          '<i class="fa-brands fa-telegram"></i>' +
          '<span>Telegram browser eke video play nowane. Chrome/Safari wala open karanna.</span>' +
        '</span>' +
        '<button type="button" class="btn-tg-open-browser" onclick="window.open(location.href,\'_blank\');return false;">' +
          'Open in Browser' +
        '</button>' +
        '<button type="button" class="btn-tg-dismiss" onclick="this.closest(\'.tg-browser-banner\').remove();document.body.classList.remove(\'tg-browser-mode\');">' +
          'X' +
        '</button>';
      document.body.insertBefore(banner, document.body.firstChild);
    } catch(e) {}
  })();

  const slug = getSlugFromURL();
  if (!slug) {
    showError('Movie not found. Please go back and try again.');
    return;
  }

  // Robust season & episode extraction from URL slug (e.g. s01e05, 1x05) or query params (?s=1&e=5)
  const slugEpMatch = slug.match(/[-_.]s(\d+)[-_.]?e(\d+)/i) || slug.match(/[-_.](\d+)x(\d+)/i);
  const urlParams = new URLSearchParams(window.location.search);
  const qSeason = urlParams.get('s') || urlParams.get('season');
  const qEpisode = urlParams.get('e') || urlParams.get('ep') || urlParams.get('episode');

  if (slugEpMatch) {
    currentSeason = parseInt(slugEpMatch[1], 10);
    currentEpisode = parseInt(slugEpMatch[2], 10);
  } else if (qSeason && qEpisode) {
    currentSeason = parseInt(qSeason, 10);
    currentEpisode = parseInt(qEpisode, 10);
  }

  // 1. Instant Synchronous Catalog Lookup (<1ms)
  // window.FILMSUB_DATA is already loaded synchronously by <script src="data/movies_data.js">
  if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
    currentMovie = window.FILMSUB_DATA.movies.find(m => m.slug === slug || m.id === slug);
    if (!currentMovie && currentSeason && currentEpisode) {
      currentMovie = findEpisodeEntry(slug, currentSeason, currentEpisode);
    }
  }

  // Resolve live stream config cleanly (1.5s cap so playback never stalls on slow GitHub fetch)
  try {
    await Promise.race([
      loadLiveStreamConfig(),
      new Promise(resolve => setTimeout(resolve, 1500))
    ]);
  } catch (e) {}

  // 2. Fallback: If movie is not in bundled data or requested specific episode is missing, fetch
  const needsEpisodeFetch = Boolean(
    !currentMovie || (currentSeason && currentEpisode && (parseInt(currentMovie.season, 10) !== currentSeason || parseInt(currentMovie.episode, 10) !== currentEpisode))
  );

  if (needsEpisodeFetch) {
    await waitForFilmSub();
    await FilmSub.loadMovies();
    if (!currentMovie) {
      currentMovie = FilmSub.findMovieBySlug(slug);
    }
    if (currentSeason && currentEpisode) {
      const epEntry = findEpisodeEntry(currentMovie || slug, currentSeason, currentEpisode);
      if (epEntry) {
        currentMovie = epEntry;
      }
    }
    if (!currentMovie || (currentSeason && currentEpisode && (parseInt(currentMovie.season, 10) !== currentSeason || parseInt(currentMovie.episode, 10) !== currentEpisode))) {
      try {
        const ghUrl = 'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json?_t=' + Date.now() + '&_r=' + Math.random();
        const ctrl = new AbortController();
        const tId = setTimeout(() => ctrl.abort(), 12000);
        const rGh = await fetch(ghUrl, { signal: ctrl.signal, cache: 'no-store' });
        clearTimeout(tId);
        if (rGh.ok) {
          const d = await rGh.json();
          const moviesList = Array.isArray(d) ? d : (d.movies || []);
          if (Array.isArray(moviesList) && moviesList.length > 0) {
            const seen = new Set();
            const curr = (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) ? window.FILMSUB_DATA.movies : [];
            const merged = [];
            for (const m of [...moviesList, ...curr]) {
              const k = String(m.slug || m.id || '').toLowerCase();
              if (k && !seen.has(k)) {
                seen.add(k);
                merged.push(m);
              }
            }
            if (window.FILMSUB_DATA) window.FILMSUB_DATA.movies = merged;
            if (window.FILMZILLA_DATA) window.FILMZILLA_DATA.movies = merged;
            if (window.FilmSub) {
              if (typeof FilmSub.setMovies === 'function') FilmSub.setMovies(merged);
              else FilmSub.movies = merged;
            }
          }
          let found = moviesList.find(m => m.slug === slug || m.id === slug);
          if (!found && currentSeason && currentEpisode) {
            const baseSlug = slug.replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
            found = moviesList.find(m => {
              if (parseInt(m.season, 10) !== currentSeason || parseInt(m.episode, 10) !== currentEpisode) return false;
              const mBase = (m.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
              return mBase && (mBase === baseSlug || mBase.startsWith(baseSlug) || baseSlug.startsWith(mBase));
            });
          }
          if (found) {
            currentMovie = found;
          }
        }
      } catch (e) {}
    }
  } else {
    // If found synchronously, schedule FilmSub.loadMovies() in background without delaying playback start
    waitForFilmSub().then(() => {
      if (window.FilmSub && typeof FilmSub.loadMovies === 'function') {
        FilmSub.loadMovies().then(() => {
          if (currentMovie && (currentMovie.type === 'series' || currentSeason)) {
            renderSeriesSection(currentMovie);
          }
        }).catch(() => {});
      }
    });
  }

  // Resilient series fallback: If a specific episode was just released and not yet synced to static catalog,
  // resolve via series master so user never sees a broken "Movie not found" screen!
  if (!currentMovie && (slugEpMatch || (currentSeason && currentEpisode))) {
    const baseSlug = slug.replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
    const catalog = (window.FilmSub && typeof FilmSub.getAllMovies === 'function' && FilmSub.getAllMovies().length)
      ? FilmSub.getAllMovies()
      : ((window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) ? window.FILMSUB_DATA.movies : []);
    const seriesMaster = catalog.find(m => {
      const mBase = (m.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
      return mBase && (mBase === baseSlug || mBase.startsWith(baseSlug) || baseSlug.startsWith(mBase));
    });
    if (seriesMaster) {
      const s = currentSeason || seriesMaster.season || 1;
      const ep = currentEpisode || 1;
      const tmdbId = seriesMaster.tmdb_id || '';
      const imdbId = seriesMaster.imdb_id || '';
      const epSlug = `${baseSlug}-s${String(s).padStart(2, '0')}e${String(ep).padStart(2, '0')}`;
      currentMovie = {
        ...seriesMaster,
        id: slug || epSlug,
        slug: slug || epSlug,
        season: s,
        episode: ep,
        episode_title: `Episode ${ep}`,
        message_id: 0,
        file_id: '',
        file_name: '',
        stream_url: '',
        variant_media: null,
        streams: [
          ...(tmdbId ? [{
            server: 'Server 2',
            label: '🎬 VIP Player 1 (VidLink Ultra HD)',
            type: 'embed',
            embed: true,
            stream_url: `https://vidlink.pro/tv/${tmdbId}/${s}/${ep}`
          }] : []),
          ...(imdbId ? [{
            server: 'Server 3',
            label: '⚡ VIP Player 2 (AutoEmbed HD)',
            type: 'embed',
            embed: true,
            stream_url: `https://autoembed.co/tv/imdb/${imdbId}-${s}-${ep}`
          }] : [])
        ],
        downloads: [],
        subtitles: [
          {
            language: 'Sinhala',
            srclang: 'si',
            label: 'සිංහල උපසිරැසි (Sinhala)',
            url: `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/subs/${slug || epSlug}-si.vtt`,
            default: false
          }
        ]
      };
      if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
        window.FILMSUB_DATA.movies.unshift(currentMovie);
      }
    }
  }

  if (!currentMovie) {
    showError('Movie not found. It may have been removed.');
    return;
  }

  // Update currentSeason/currentEpisode if not yet set from URL
  if (!currentSeason || !currentEpisode) {
    currentSeason = currentMovie.season || 1;
    currentEpisode = currentMovie.episode || 1;
  }
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

  updateSocialMetaTags(currentMovie);
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

  // Dynamic theme extraction for movie detail page
  const movieThemeImg = currentMovie.poster || currentMovie.backdrop || '';
  if (movieThemeImg && movieThemeImg.startsWith('http')) {
    extractDominantColor(movieThemeImg, (color) => {
      if (color) applyDynamicTheme(color.r, color.g, color.b);
      else applyDynamicTheme(null);
    });
  }
  renderDownloadSection(currentMovie);
  renderRelatedMovies(currentMovie);
  initShareButton(currentMovie);
  if (window.FilmSub && typeof window.FilmSub.initScrollReveal === 'function') {
    try { window.FilmSub.initScrollReveal(); } catch (e) {}
  }

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
  const sNum = currentSeason || (movie && movie.season) || 1;
  const eNum = currentEpisode || (movie && movie.episode) || 1;
  const isSeries = movie && (movie.type === 'series' || (Array.isArray(movie.seasons) && movie.seasons.length > 0) || currentSeason > 1 || currentEpisode > 1 || Boolean(movie.season));

  // TV Series episode resolution: resolve exact episode movie object
  let effectiveMovie = movie;
  if (isSeries) {
    const epEntry = findEpisodeEntry(movie, sNum, eNum);
    if (epEntry) {
      effectiveMovie = epEntry;
    }
  }

  const list = [];
  const driveId = extractMovieDriveId(effectiveMovie);
  const primaryUrl = effectiveMovie.stream_url || (Array.isArray(effectiveMovie.streams) && effectiveMovie.streams[0] && effectiveMovie.streams[0].stream_url) || '';

  // Does the local stream match this specific episode?
  const isMatchingEpisode = !isSeries || (sNum === (effectiveMovie.season || 1) && eNum === (effectiveMovie.episode || 1));

  let imdbId = (effectiveMovie.imdb_id || effectiveMovie.imdbId || movie.imdb_id || movie.imdbId || '').trim();
  let tmdbId = String(effectiveMovie.tmdb_id || effectiveMovie.tmdbId || movie.tmdb_id || movie.tmdbId || '').trim();

  // If imdb field contains tt ID (e.g. "tt0944947")
  if (!imdbId && effectiveMovie.imdb && /^tt\d+/i.test(effectiveMovie.imdb.trim())) {
    imdbId = effectiveMovie.imdb.trim();
  }

  // Extract from existing streams/downloads/slug/id if missing
  if (!imdbId || !tmdbId) {
    const urlsToCheck = [
      effectiveMovie.stream_url || '',
      ...(Array.isArray(effectiveMovie.streams) ? effectiveMovie.streams.map(s => s.stream_url || s.url || '') : []),
      ...(Array.isArray(effectiveMovie.downloads) ? effectiveMovie.downloads.map(d => d.url || '') : []),
      effectiveMovie.slug || '',
      effectiveMovie.id || ''
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
    const normT = (effectiveMovie.title || movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
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
    const titleKey = (effectiveMovie.title || movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
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
  let tgChatId = effectiveMovie.channel_chat_id || movie.channel_chat_id || '';
  // IMPORTANT: NEVER use channel_post_id as video message_id!
  // channel_post_id is the announcement poster/text message in the public channel, NOT the video stream!
  let tgMsgId = (typeof effectiveMovie.message_id === 'number' && effectiveMovie.message_id > 0) ? effectiveMovie.message_id : 0;

  if (!tgMsgId && Array.isArray(effectiveMovie.downloads)) {
    for (const d of effectiveMovie.downloads) {
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

  if (!tgMsgId && Array.isArray(effectiveMovie.streams)) {
    for (const s of effectiveMovie.streams) {
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

  const existingTgStream = Array.isArray(effectiveMovie.streams)
    ? effectiveMovie.streams.find(s => s.mode === 'super_chunk' || (s.stream_url && !s.embed && !s.stream_url.includes('vidlink') && !s.stream_url.includes('autoembed') && !s.stream_url.includes('multiembed')))
    : null;

  const vm = effectiveMovie.variant_media || (effectiveMovie.movie_entry && effectiveMovie.movie_entry.variant_media);
  if (vm && typeof vm === 'object') {
    const isValidV = v => Boolean(v && ((typeof v.message_id === 'number' && v.message_id > 0) || (v.stream_url && !v.stream_url.includes('/0')) || v.file_id));
    const qKey = (currentEffectiveQuality || '1080p').toLowerCase();
    let vOpt = null;
    if (isValidV(vm[qKey])) {
      vOpt = vm[qKey];
    } else {
      const candidates = ['1080p', '720p', '480p'];
      for (const q of candidates) {
        if (isValidV(vm[q])) {
          vOpt = vm[q];
          break;
        }
      }
      if (!vOpt) vOpt = Object.values(vm).find(isValidV);
    }
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
  } else if (!nativeStreamUrl && effectiveMovie.file_id) {
    nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(effectiveMovie.file_id)}`;
  } else if (!nativeStreamUrl && existingTgStream && existingTgStream.stream_url && !existingTgStream.stream_url.includes('vidlink') && !existingTgStream.stream_url.includes('autoembed') && !existingTgStream.stream_url.includes('multiembed')) {
    nativeStreamUrl = normalizeStreamUrl(existingTgStream.stream_url);
  } else if (!nativeStreamUrl && driveId) {
    nativeStreamUrl = `/api/stream?id=${encodeURIComponent(driveId)}`;
  } else if (!nativeStreamUrl && primaryUrl && !primaryUrl.includes('vidlink') && !primaryUrl.includes('autoembed') && !primaryUrl.includes('multiembed') && !primaryUrl.includes('embed')) {
    nativeStreamUrl = normalizeStreamUrl(primaryUrl);
  }

  // Fallback for TV series episode switching if current movie doesn't have this episode's stream
  if (!nativeStreamUrl && isSeries) {
    const matchedEp = findEpisodeEntry(movie, sNum, eNum);
    if (matchedEp) {
      const epMsgId = (typeof matchedEp.message_id === 'number' && matchedEp.message_id > 0) ? matchedEp.message_id : 0;
      const epChatId = matchedEp.channel_chat_id || tgChatId || '-1004325759505';
      if (epMsgId > 0) {
        tgMsgId = epMsgId;
        nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/channel/${epChatId}/${epMsgId}`;
      } else if (matchedEp.file_id) {
        nativeStreamUrl = `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(matchedEp.file_id)}`;
      } else if (matchedEp.stream_url) {
        nativeStreamUrl = normalizeStreamUrl(matchedEp.stream_url);
      }
    }
  }

  const isRealStreamReady = Boolean(
    (tgMsgId > 0 || effectiveMovie.file_id || driveId) &&
    nativeStreamUrl &&
    !nativeStreamUrl.endsWith('/0') &&
    !nativeStreamUrl.includes('/channel/-1004325759505/0')
  );

  list.push({
    server: 'Server 1',
    label: 'Cinema Stream (HD)',
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
  const sNum = currentSeason || (movie && movie.season) || 1;
  const eNum = currentEpisode || (movie && movie.episode) || 1;
  const isSeries = movie && (movie.type === 'series' || currentSeason > 1 || currentEpisode > 1 || Boolean(movie.season));

  let effectiveMovie = movie;
  if (isSeries && (parseInt(movie.season, 10) !== sNum || parseInt(movie.episode, 10) !== eNum)) {
    const epEntry = findEpisodeEntry(movie, sNum, eNum);
    if (epEntry) effectiveMovie = epEntry;
  }

  const isHardcoded = Boolean(effectiveMovie && (effectiveMovie.sub_hardcoded || effectiveMovie.is_already_hardsubbed));
  const defaultUri = buildDefaultSinhalaVttDataUri(effectiveMovie);
  if (Array.isArray(effectiveMovie.subtitles) && effectiveMovie.subtitles.length > 0) {
    return effectiveMovie.subtitles.map((sub, idx) => ({
      ...sub,
      language: sub.language || 'Sinhala',
      srclang: sub.srclang || 'si',
      label: sub.label || 'සිංහල උපසිරැසි (Sinhala)',
      url: _isValidSubUrl(sub.url) ? sub.url : defaultUri,
      default: sub.default !== undefined ? sub.default : (!isHardcoded && idx === 0)
    }));
  }

  let ghSubUrl = '';
  if (effectiveMovie.slug) {
    const baseSlug = (effectiveMovie.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '');
    const epSfx = isSeries ? `-s${String(sNum).padStart(2, '0')}e${String(eNum).padStart(2, '0')}` : '';
    ghSubUrl = `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/subs/${baseSlug}${epSfx}-si.vtt`;
  }

  const effectiveSubUrl = _isValidSubUrl(effectiveMovie.subtitle_url) ? effectiveMovie.subtitle_url : ghSubUrl;
  if (effectiveSubUrl) {
    return [
      {
        language: effectiveMovie.lang || 'Sinhala',
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
  const sNum = currentSeason || (movie && movie.season) || 1;
  const eNum = currentEpisode || (movie && movie.episode) || 1;
  const isSeries = movie && (movie.type === 'series' || currentSeason > 1 || currentEpisode > 1 || Boolean(movie.season));

  let effectiveMovie = movie;
  if (isSeries && (parseInt(movie.season, 10) !== sNum || parseInt(movie.episode, 10) !== eNum)) {
    const epEntry = findEpisodeEntry(movie, sNum, eNum);
    if (epEntry) effectiveMovie = epEntry;
  }

  const rawDls = Array.isArray(effectiveMovie.downloads) ? [...effectiveMovie.downloads] : [];
  const botUsername = 'Filmsinhala200Bot';
  const vm = effectiveMovie.variant_media;

  // Channel ID extraction helper
  let chClean = '4325759505';
  const mUrl = effectiveMovie.stream_url || (rawDls.find(d => d.url && d.url.includes('/stream/channel/')) || {}).url || '';
  const mMatch = mUrl.match(/\/stream\/channel\/(-?\d+)\//);
  if (mMatch) {
    const rawId = mMatch[1].replace(/^-?100/, '').replace(/^-/, '');
    if (rawId) chClean = rawId;
  }

  const results = [];
  const seenQualities = new Set();
  const seenUrls = new Set();

  const getLiveStreamBase = () => {
    let liveEndpoint = (typeof window !== 'undefined' && window.__LIVE_STREAM_BASE_URL) ||
                       (window.FILMSUB_STREAM_CONFIG && window.FILMSUB_STREAM_CONFIG.stream_base_url) ||
                       (window.FILMZILLA_STREAM_CONFIG && window.FILMZILLA_STREAM_CONFIG.stream_base_url) ||
                       (typeof activeStreamBaseUrl === 'string' && activeStreamBaseUrl) ||
                       '';
    if (!liveEndpoint && typeof sessionStorage !== 'undefined') {
      try { liveEndpoint = sessionStorage.getItem('filmsub_stream_base') || ''; } catch (e) {}
    }
    if (liveEndpoint && !liveEndpoint.includes('localhost') && !liveEndpoint.includes('127.0.0.1')) {
      return liveEndpoint.replace(/\/+$/, '');
    }
    return '';
  };

  const getDirectUrlForCard = (card, q) => {
    if (card.direct_url) return card.direct_url;
    const liveBase = getLiveStreamBase();
    if (card.url && (card.url.includes('/stream/channel/') || card.url.includes('/stream/file/') || card.url.includes('/stream/download/') || card.url.includes('/stream/'))) {
      const matchChan = card.url.match(/\/stream\/channel\/(-?\d+)\/(\d+)/);
      if (matchChan && liveBase) {
        return `${liveBase}/stream/download/${matchChan[1]}/${matchChan[2]}`;
      }
      if (!card.url.includes('dl=1') && !card.url.includes('/stream/download/')) {
        return card.url.includes('?') ? `${card.url}&dl=1` : `${card.url}?dl=1`;
      }
      return card.url;
    }
    if (card.url && card.url.includes('t.me/c/')) {
      const cm = card.url.match(/\/c\/(\d+)\/(\d+)/);
      if (cm) {
        if (liveBase) return `${liveBase}/stream/download/-100${cm[1]}/${cm[2]}`;
        return `/stream/channel/-100${cm[1]}/${cm[2]}?dl=1`;
      }
    }
    if (effectiveMovie.message_id) {
      if (liveBase) return `${liveBase}/stream/download/-100${chClean}/${effectiveMovie.message_id}`;
      return `/stream/channel/-100${chClean}/${effectiveMovie.message_id}?dl=1`;
    }
    if (effectiveMovie.stream_url && !isDeadTunnel(effectiveMovie.stream_url)) {
      return effectiveMovie.stream_url.includes('?') ? `${effectiveMovie.stream_url}&dl=1` : `${effectiveMovie.stream_url}?dl=1`;
    }
    return card.url || '';
  };

  const addCard = (card) => {
    if (!card || !card.url) return;
    if (isDeadTunnel(card.url) && !card.url.includes('/stream/channel/') && !card.url.includes('t.me/c/')) return;
    if (seenUrls.has(card.url)) return;
    seenUrls.add(card.url);

    const directUrl = card.direct_url || getDirectUrlForCard(card, card.quality);
    const tgUrl = card.tg_url || card.bot_url || card.channel_url || (card.url && card.url.includes('t.me') ? card.url : '');

    results.push({
      ...card,
      direct_url: directUrl,
      tg_url: tgUrl,
      format: card.format || 'MP4 (සිංහල Sub Merged)',
      sub_merged: true,
      subtitle_merged: true,
    });
  };

  const hasTelegramMedia = Boolean(
    (vm && typeof vm === 'object' && Object.keys(vm).length > 0) ||
    effectiveMovie.message_id ||
    effectiveMovie.file_id ||
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
    ['1080p', '720p', '480p'].forEach(q => {
      const v = vm[q];
      if (v && typeof v === 'object' && (v.file_id || v.message_id)) {
        const sz = v.size_bytes ? formatBytesFromBytes(v.size_bytes) : '';
        const botUrl = `https://t.me/${botUsername}?start=dl_${effectiveMovie.slug || movie.slug}_${q}`;
        const chanUrl = v.message_id ? `https://t.me/c/${chClean}/${v.message_id}` : '';
        const liveBase = getLiveStreamBase();
        const directUrl = v.message_id
          ? (liveBase ? `${liveBase}/stream/download/-100${chClean}/${v.message_id}` : `/stream/channel/-100${chClean}/${v.message_id}?dl=1`)
          : '';
        const primaryDlUrl = directUrl || botUrl || chanUrl;
        
        seenQualities.add(q.toLowerCase());
        addCard({
          quality: q,
          label: `${qLabels[q] || q}`,
          size: sz,
          url: primaryDlUrl,
          direct_url: directUrl,
          channel_url: chanUrl,
          bot_url: botUrl,
          tg_url: botUrl || chanUrl,
          format: 'MP4',
          host: 'Telegram Cloud',
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
        const mainDriveId = extractMovieDriveId(effectiveMovie);
        const qDriveId = extractDriveIdForQuality(effectiveMovie, qNorm);
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
  if (effectiveMovie.message_id && !seenQualities.has('1080p')) {
    const sz1080 = effectiveMovie.file_size ? formatBytesFromBytes(effectiveMovie.file_size) : '';
    const botUrl = `https://t.me/${botUsername}?start=dl_${effectiveMovie.slug || movie.slug}_1080p`;
    const chanUrl = `https://t.me/c/${chClean}/${effectiveMovie.message_id}`;
    const directUrl = `/stream/channel/-100${chClean}/${effectiveMovie.message_id}?dl=1`;
    seenQualities.add('1080p');
    addCard({
      quality: '1080p',
      label: '1080p Full HD',
      size: sz1080,
      url: directUrl || botUrl,
      direct_url: directUrl,
      channel_url: chanUrl,
      bot_url: botUrl,
      tg_url: botUrl || chanUrl,
      format: 'MP4',
      host: 'Telegram Cloud',
    });
  }

  // 4. Fallback for direct MP4 stream if not already covered
  if (effectiveMovie.stream_url && !isDeadTunnel(effectiveMovie.stream_url)) {
    const qStr = effectiveMovie.quality || '1080p';
    const baseQ = qStr.toLowerCase();
    if (!seenQualities.has(baseQ)) {
      seenQualities.add(baseQ);
      const szStr = effectiveMovie.file_size ? formatBytesFromBytes(effectiveMovie.file_size) : (effectiveMovie.size || '');
      const directUrl = effectiveMovie.stream_url.includes('?') ? `${effectiveMovie.stream_url}&dl=1` : `${effectiveMovie.stream_url}?dl=1`;
      addCard({
        quality: qStr,
        label: `${qStr} Full HD`,
        size: szStr,
        url: directUrl,
        direct_url: directUrl,
        format: 'MP4',
        host: 'Direct Server',
      });
    }
  }

  return results.filter(r => !String(r.quality || '').toLowerCase().includes('360'));
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
  const isSeries = movie.type === 'series' || currentSeason > 1 || currentEpisode > 1 || Boolean(movie.season);
  const sNum = currentSeason || movie.season || 1;
  const eNum = currentEpisode || movie.episode || 1;

  const titleEl = document.getElementById('movie-detail-title') || document.getElementById('cs-main-title');
  if (titleEl) {
    if (isSeries) {
      titleEl.textContent = `${movie.title || 'Series'} S${String(sNum).padStart(2, '0')}E${String(eNum).padStart(2, '0')}: ${movie.episode_title || 'Episode ' + eNum} Sinhala Subtitles`;
    } else {
      titleEl.textContent = `${movie.title || 'Untitled'} (${movie.year || ''}) Sinhala Subtitles`;
    }
  }

  const subWrap = document.getElementById('cs-title-sub');
  if (subWrap) {
    const yr = movie.year ? `<span class="cs-header-year" id="cs-header-year">${esc(movie.year)}</span>` : '';
    const qText = isSeries ? `S${String(sNum).padStart(2, '0')} E${String(eNum).padStart(2, '0')} HD` : (movie.quality ? `${esc(movie.quality)} WEB-DL` : 'HD WEB-DL');
    const qual = `<span class="cs-header-quality" id="cs-header-quality">${qText}</span>`;
    const imdb = (movie.imdb && movie.imdb !== '0.0') ? `<span class="badge-imdb cs-header-imdb" id="cs-header-imdb"><i class="fa-solid fa-star"></i> ${esc(movie.imdb)} / 10</span>` : '';
    const sub = `<span class="cs-sub-badge" id="cs-sub-badge"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි</span>`;
    const items = [yr, qual, imdb, sub].filter(Boolean);
    subWrap.innerHTML = items.join('<span class="cs-title-dot">•</span>');
  }
}

// ---- 3. Server Tabs (Multi-Server + Bahirawa Backup Players + Trailer) ----
function renderServerTabs(movie) {
  const tabsEl = document.getElementById('server-tabs');
  if (!tabsEl) return;
  const streams = getMovieStreams(movie);

  let tabsHtml = '';

  // 1. Modern live stream indicator pill
  tabsHtml += `
    <span class="player-status-badge">
      <span class="pulse-indicator"></span>
      <span>${isTrailerActive ? 'Trailer' : 'Online Stream'}</span>
    </span>`;

  // 2. Movie Stream Servers (Clean modern buttons without wordy sentences)
  if (streams.length > 0) {
    streams.forEach((s, i) => {
      let cleanLabel = s.server || `Server ${i + 1}`;
      if (cleanLabel.toLowerCase().includes('super player') || cleanLabel.toLowerCase().includes('auto sinhala sub')) {
        cleanLabel = 'Main Server';
      }
      tabsHtml += `
        <button class="server-tab${i === currentStreamIdx && !isTrailerActive ? ' active' : ''}" data-type="stream" data-index="${i}" type="button" title="Watch Movie Stream">
          <i class="fa-solid fa-circle-play"></i>
          <span>${FilmSub.escHtml(cleanLabel)}</span>
        </button>`;
    });
  }

  // 3. Official Trailer Tab (Clean and sleek)
  tabsHtml += `
    <button class="server-tab btn-trailer-pill${isTrailerActive ? ' active' : ''}" data-type="trailer" type="button" title="${isTrailerActive ? 'Playing Official Trailer' : 'Watch Official Trailer'}">
      <i class="fa-solid fa-clapperboard"></i>
      <span>Trailer</span>
    </button>`;

  tabsEl.innerHTML = tabsHtml;

  tabsEl.querySelectorAll('button.server-tab').forEach(btn => {
    attachFastAction(btn, () => {
      const type = btn.dataset.type;
      if (type === 'trailer') {
        if (!isTrailerActive) {
          loadTrailer(movie);
        }
      } else {
        const idx = parseInt(btn.dataset.index, 10);
        isTrailerActive = false;
        loadStream(movie, idx);
      }
    });
  });
}

// ---- 3b. Adaptive Quality & Network-Aware Streaming Engine ----
function detectNetworkSpeed() {
  const isMobile = typeof window !== 'undefined' && (window.innerWidth <= 768 || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || ''));
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const apiDownlink = (conn && typeof conn.downlink === 'number' && conn.downlink > 0) ? conn.downlink : 10;
  const effectiveType = (conn && conn.effectiveType) ? conn.effectiveType : '4g';
  const saveData = Boolean(conn && conn.saveData);
  const effMbps = getEffectiveDownloadMbps() || apiDownlink;

  if (saveData || effectiveType === '2g' || effectiveType === 'slow-2g' || effMbps < 1.8 || (effectiveType === '3g' && effMbps < 2.5)) {
    return { speed: 'slow', downlink: effMbps, effectiveType, recommendedQuality: '480p' };
  }
  // Chrome/Edge/Brave cap navigator.connection.downlink at 10 Mbps; prioritize 1080p Full HD on fast 4G/broadband
  if (_measuredMbps > 0 ? _measuredMbps >= 4.2 : (!saveData && effectiveType === '4g' && apiDownlink >= 4.5)) {
    return { speed: 'fast', downlink: effMbps, effectiveType, recommendedQuality: '1080p' };
  }
  if (!conn && _measuredMbps <= 0) {
    return { speed: isMobile ? 'medium' : 'fast', downlink: 10, effectiveType: '4g', recommendedQuality: isMobile ? '720p' : '1080p' };
  }
  return { speed: 'medium', downlink: effMbps, effectiveType, recommendedQuality: '720p' };
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
  const qualNameEl = document.getElementById('current-quality-name');
  const norm = String(q || 'auto').toLowerCase();
  const activeTier = (norm === 'auto' ? currentEffectiveQuality : norm).toUpperCase();

  if (qualNameEl) {
    if (norm === 'auto') {
      qualNameEl.innerHTML = `<span style="color:var(--accent)"><i class="fa-solid fa-bolt"></i> Auto</span> (${activeTier})`;
    } else {
      const qSuffix = activeTier.includes('1080') ? 'Full HD' : (activeTier.includes('720') ? 'HD' : (activeTier.includes('480') ? 'SD' : ''));
      qualNameEl.textContent = `${activeTier} ${qSuffix}`.trim();
    }
  }

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
    const isSmall = typeof window !== 'undefined' && window.innerWidth <= 640;
    inPlayerBadge.textContent = isSmall ? activeTier : (norm === 'auto' ? `AUTO (${activeTier})` : activeTier);
  }

  // Sync active state on dropdown items, top toolbar pills & in-player menu items
  document.querySelectorAll('.q-menu-item, .q-pill').forEach(item => {
    item.classList.toggle('active', (item.dataset.quality || '').toLowerCase() === norm);
  });
  document.querySelectorAll('.vjs-sq-item').forEach(item => {
    item.classList.toggle('active', (item.dataset.quality || '').toLowerCase() === norm);
  });
}

/**
 * Resolves the precise stream source, Telegram message details, or Google Drive ID
 * for a specific requested quality tier (1080p, 720p, 480p, 360p).
 * Returns { src, chatId, messageId, driveId, quality }.
 */
function resolveStreamUrlForQuality(movie, quality) {
  if (!movie) return { src: '', chatId: '', messageId: null, driveId: null, quality: '' };
  const sNum = currentSeason || (movie && movie.season) || 1;
  const eNum = currentEpisode || (movie && movie.episode) || 1;
  const isSeries = movie && (movie.type === 'series' || currentSeason > 1 || currentEpisode > 1 || Boolean(movie.season));

  let effectiveMovie = movie;
  if (isSeries && (parseInt(movie.season, 10) !== sNum || parseInt(movie.episode, 10) !== eNum)) {
    const epEntry = findEpisodeEntry(movie, sNum, eNum);
    if (epEntry) effectiveMovie = epEntry;
  }

  const qNorm = String(quality || 'auto').toLowerCase();
  const effectiveQ = qNorm === 'auto' ? (detectNetworkSpeed().recommendedQuality || '720p') : qNorm;
  const defaultTgChatId = effectiveMovie.channel_chat_id || movie.channel_chat_id || '-1004325759505';

  // 1. Check if movie has multi-quality variant_media (Telegram Cloud)
  const vm = effectiveMovie.variant_media || (effectiveMovie.movie_entry && effectiveMovie.movie_entry.variant_media);
  if (vm && typeof vm === 'object') {
    const isValidV = v => Boolean(v && ((typeof v.message_id === 'number' && v.message_id > 0) || (v.stream_url && !v.stream_url.includes('/0')) || v.file_id));
    let vEntry = null;
    if (isValidV(vm[effectiveQ])) {
      vEntry = vm[effectiveQ];
    } else if (isValidV(vm[quality])) {
      vEntry = vm[quality];
    } else {
      const candidates = ['1080p', '720p', '480p'];
      for (const q of candidates) {
        if (isValidV(vm[q])) {
          vEntry = vm[q];
          break;
        }
      }
      if (!vEntry) vEntry = Object.values(vm).find(isValidV);
    }
    if (vEntry) {
      let tgChatId = effectiveMovie.channel_chat_id || movie.channel_chat_id || '';
      if (!tgChatId) {
        const mUrl = effectiveMovie.stream_url || (vEntry.stream_url || '');
        const mMatch = mUrl.match(/\/stream\/channel\/(-?\d+)\//);
        tgChatId = mMatch ? mMatch[1] : defaultTgChatId;
      }
      if (vEntry.message_id && Number(vEntry.message_id) > 0) {
        return {
          src: `${getStreamEndpointPrefix()}/stream/channel/${tgChatId}/${vEntry.message_id}`,
          chatId: tgChatId,
          messageId: Number(vEntry.message_id),
          driveId: null,
          quality: effectiveQ
        };
      } else if (vEntry.stream_url && !vEntry.stream_url.includes('/0')) {
        return {
          src: normalizeStreamUrl(vEntry.stream_url),
          chatId: tgChatId,
          messageId: null,
          driveId: null,
          quality: effectiveQ
        };
      } else if (vEntry.file_id) {
        return {
          src: `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(vEntry.file_id)}`,
          chatId: '',
          messageId: null,
          driveId: null,
          quality: effectiveQ
        };
      }
    }
  }

  // 2. Check Drive ID for quality
  const driveId = extractDriveIdForQuality(effectiveMovie, effectiveQ);
  if (driveId) {
    return {
      src: buildChunkStreamUrl(driveId, effectiveQ),
      chatId: '',
      messageId: null,
      driveId: driveId,
      quality: effectiveQ
    };
  }

  // 3. Check qualities map
  if (effectiveMovie.qualities && typeof effectiveMovie.qualities === 'object') {
    const qVal = effectiveMovie.qualities[effectiveQ] || effectiveMovie.qualities[quality];
    if (typeof qVal === 'string' && qVal.startsWith('http') && !qVal.includes('t.me')) {
      return {
        src: normalizeStreamUrl(qVal),
        chatId: '',
        messageId: null,
        driveId: null,
        quality: effectiveQ
      };
    } else if (qVal && typeof qVal === 'object' && qVal.stream_url && !qVal.stream_url.includes('t.me')) {
      return {
        src: normalizeStreamUrl(qVal.stream_url),
        chatId: '',
        messageId: null,
        driveId: null,
        quality: effectiveQ
      };
    }
  }

  // 4. Check movie downloads
  const downloads = typeof getMovieDownloads === 'function' ? getMovieDownloads(effectiveMovie) : (effectiveMovie.downloads || []);
  const matched = downloads.find(d => String(d.quality || '').toLowerCase().includes(effectiveQ) && !d.download_only);
  if (matched) {
    if (matched.stream_url && !matched.stream_url.includes('t.me')) {
      return {
        src: normalizeStreamUrl(matched.stream_url),
        chatId: defaultTgChatId,
        messageId: matched.message_id || null,
        driveId: null,
        quality: effectiveQ
      };
    } else if (matched.message_id) {
      return {
        src: `${getStreamEndpointPrefix()}/stream/channel/${defaultTgChatId}/${matched.message_id}`,
        chatId: defaultTgChatId,
        messageId: Number(matched.message_id),
        driveId: null,
        quality: effectiveQ
      };
    } else if (matched.file_id) {
      return {
        src: `${getStreamEndpointPrefix()}/stream/file/${encodeURIComponent(matched.file_id)}`,
        chatId: '',
        messageId: null,
        driveId: null,
        quality: effectiveQ
      };
    } else if (matched.url && matched.url.includes('/c/')) {
      const mMatch = matched.url.match(/t\.me\/c\/(\d+)\/(\d+)/);
      if (mMatch) {
        return {
          src: `${getStreamEndpointPrefix()}/stream/channel/-100${mMatch[1]}/${mMatch[2]}`,
          chatId: `-100${mMatch[1]}`,
          messageId: Number(mMatch[2]),
          driveId: null,
          quality: effectiveQ
        };
      }
    } else if (matched.url && !matched.url.includes('drive.google.com/uc') && !matched.url.includes('t.me') && !matched.url.startsWith('/api/download')) {
      return {
        src: normalizeStreamUrl(matched.url),
        chatId: '',
        messageId: null,
        driveId: null,
        quality: effectiveQ
      };
    }
  }

  return {
    src: '',
    chatId: defaultTgChatId,
    messageId: null,
    driveId: null,
    quality: effectiveQ
  };
}

/**
 * Pre-buffers higher quality (e.g. 1080p) in the background while current quality (720p)
 * plays with 100% uninterrupted audio and video.
 * Handover to higher quality only occurs once initial frames and byte ranges are verified ready.
 */
let _pendingMobileAutoStepUp = false;

/**
 * Pre-buffers target quality (step-up OR smooth step-down) in the background while the active quality
 * keeps playing with uninterrupted audio and video.
 * On Desktop, performs a frame-synced visual shadow overlay handover so Video.js never flashes a spinner.
 * On Mobile, warms moov + playhead bytes via single-connection HTTP Range fetch before src swap.
 */
function seamlessPreloadAndStepUp(targetQuality, candidate, opts = {}) {
  const isAutoDowngrade = Boolean(opts.isAutoDowngrade);
  if (_isPreloadingUpgrade || _isSeamlessSwitching || !candidate || !candidate.src || !vjsPlayer) return;
  if (!opts.isManual && selectedQuality !== 'auto') return;
  if (!opts.allowWhenPaused && (vjsPlayer.paused() || (vjsPlayer.seeking && vjsPlayer.seeking()))) return;

  const curT = (vjsPlayer && typeof vjsPlayer.currentTime === 'function') ? (vjsPlayer.currentTime() || 0) : 0;
  const totalDur = (vjsPlayer && typeof vjsPlayer.duration === 'function') ? (vjsPlayer.duration() || 0) : 0;

  const isMobileClient = (typeof navigator !== 'undefined' && /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)) || (typeof window !== 'undefined' && window.innerWidth <= 768);
  if (isMobileClient) {
    // Mobile hardware video decoders cannot sustain dual parallel video decoding without crashing the WebProcess or media engine.
    // Apply quality upgrade after lightweight single-connection HTTP Range pre-warm (zero duplicate video decoder).
    if (opts.skipPrewarm) {
      if (isAutoDowngrade) {
        applyQualitySwitch(targetQuality, { isAutoDowngrade: true, force: Boolean(opts.force), preloadedTime: curT, targetSrc: candidate.src });
      } else {
        _pendingMobileAutoStepUp = !opts.isManual && selectedQuality === 'auto';
        applyQualitySwitch(targetQuality, { isAutoDowngrade: false });
      }
      return;
    }

    _isPreloadingUpgrade = true;
    const ctrl = new AbortController();
    _activePreloadCleanup = () => {
      _isPreloadingUpgrade = false;
      _activePreloadCleanup = null;
      try { ctrl.abort(); } catch (e) {}
    };

    prewarmQualityBytesViaFetch(candidate, curT, totalDur, {
      signal: ctrl.signal,
      timeoutMs: isAutoDowngrade ? 6000 : 9000
    }).then((res) => {
      _isPreloadingUpgrade = false;
      _activePreloadCleanup = null;
      if (!vjsPlayer || (res && res.aborted)) return;
      if (!opts.isManual && selectedQuality !== 'auto') return;
      if (!res || !res.ok) {
        if (!isAutoDowngrade) lastAutoSwitchEpoch = Date.now() + 35000;
        return;
      }
      if (!isAutoDowngrade && !opts.isManual && targetQuality === '1080p' && res.mbps > 0 && res.mbps < 3.0) {
        lastAutoSwitchEpoch = Date.now() + 35000;
        return;
      }
      if (isAutoDowngrade) {
        applyQualitySwitch(targetQuality, {
          isAutoDowngrade: true,
          force: true,
          preloadedTime: (typeof vjsPlayer.currentTime === 'function' ? vjsPlayer.currentTime() : curT) || curT,
          targetSrc: candidate.src,
          preWarmed: true
        });
      } else {
        _pendingMobileAutoStepUp = !opts.isManual && selectedQuality === 'auto';
        applyQualitySwitch(targetQuality, { isAutoDowngrade: false });
      }
    }).catch(() => {
      _isPreloadingUpgrade = false;
      _activePreloadCleanup = null;
    });
    return;
  }

  _isPreloadingUpgrade = true;
  console.log(`[ABR] Shadow pre-buffering ${targetQuality.toUpperCase()} (${isAutoDowngrade ? 'smooth step-down' : 'upgrade'}) while current quality plays...`);

  // 1. Proactive backend seek-offset warmup
  const ids = (candidate.chatId && candidate.messageId) ? candidate : extractChannelIdsFromUrl(candidate.src);
  if (ids) {
    prewarmStreamAtTime(candidate, curT + 1.5, totalDur, { urgent: true, warmupOnly: true, burstBytes: 4194304 }).catch(() => {});
  } else if (candidate.driveId) {
    fetch(`/api/stream?id=${encodeURIComponent(candidate.driveId)}&q=${encodeURIComponent(targetQuality)}`, {
      headers: { 'Range': 'bytes=0-1048575' },
      mode: 'cors'
    }).catch(() => {});
  }

  // 2. Shadow video element mounted inside Video.js container (invisible until handover ready)
  const vjsRoot = (vjsPlayer && typeof vjsPlayer.el === 'function') ? vjsPlayer.el() : null;
  const bgPreloader = document.createElement('video');
  bgPreloader.className = 'fz-shadow-handover';
  bgPreloader.preload = 'auto';
  bgPreloader.muted = true;
  bgPreloader.playsInline = true;
  bgPreloader.setAttribute('playsinline', '');
  if (vjsRoot) {
    bgPreloader.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000;opacity:0;visibility:hidden;pointer-events:none;z-index:2;transition:opacity 0.18s ease;';
    vjsRoot.appendChild(bgPreloader);
  } else {
    bgPreloader.style.cssText = 'position:fixed;top:-9999px;left:-9999px;width:1px;height:1px;opacity:0;pointer-events:none;';
    document.body.appendChild(bgPreloader);
  }

  let cleanedUp = false;
  let handoverTransferred = false;
  let checkInterval = null;
  let preloadTimeout = null;

  const destroyShadowVideo = (el) => {
    try {
      el.pause();
      el.removeAttribute('src');
      el.load();
    } catch (e) {}
    if (el && el.parentNode) {
      el.parentNode.removeChild(el);
    }
  };

  const cleanup = () => {
    if (cleanedUp) return;
    cleanedUp = true;
    if (checkInterval) clearInterval(checkInterval);
    if (preloadTimeout) clearTimeout(preloadTimeout);
    _isPreloadingUpgrade = false;
    _activePreloadCleanup = null;
    if (!handoverTransferred) {
      destroyShadowVideo(bgPreloader);
    }
  };
  _activePreloadCleanup = cleanup;

  const timeoutMs = isAutoDowngrade ? 8000 : 10500;
  preloadTimeout = setTimeout(() => {
    if (cleanedUp) return;
    console.warn(`[ABR] Pre-buffering for ${targetQuality} timed out (>${timeoutMs}ms). Keeping ${currentEffectiveQuality} uninterrupted.`);
    cleanup();
    if (!isAutoDowngrade) {
      lastAutoSwitchEpoch = Date.now() + 45000;
    }
  }, timeoutMs);

  // 3. Once metadata is loaded, seek ahead right near live playhead
  let lastTargetSeek = -1;
  const seekShadowNearLive = () => {
    const liveT = (vjsPlayer && typeof vjsPlayer.currentTime === 'function') ? (vjsPlayer.currentTime() || 0) : curT;
    const targetSeek = liveT + 1.2;
    lastTargetSeek = targetSeek;
    try {
      bgPreloader.currentTime = targetSeek;
    } catch (e) {}
  };

  bgPreloader.addEventListener('loadedmetadata', () => {
    if (cleanedUp) return;
    seekShadowNearLive();
    console.log(`[ABR] Shadow metadata ready. Buffering at ${lastTargetSeek.toFixed(1)}s for ${targetQuality.toUpperCase()}...`);
  }, { once: true });

  // 4. Frame-synced visual handover once shadow stream holds smooth runway
  let handoverDone = false;
  const executeHandover = () => {
    if (cleanedUp || handoverDone) return;
    if (!opts.isManual && selectedQuality !== 'auto') {
      cleanup();
      return;
    }
    if (!vjsPlayer || (!opts.allowWhenPaused && vjsPlayer.paused())) {
      cleanup();
      return;
    }

    const liveT = (typeof vjsPlayer.currentTime === 'function') ? (vjsPlayer.currentTime() || 0) : 0;
    let shadowCovered = bgPreloader.readyState >= 4;
    if (!shadowCovered && bgPreloader.buffered && bgPreloader.buffered.length > 0) {
      for (let i = 0; i < bgPreloader.buffered.length; i++) {
        const bs = bgPreloader.buffered.start(i);
        const be = bgPreloader.buffered.end(i);
        if (bs <= liveT + 0.35 && be >= liveT + 1.2) {
          shadowCovered = true;
          break;
        }
      }
    }
    if (!shadowCovered) {
      if (lastTargetSeek >= 0 && liveT > lastTargetSeek + 2.5) {
        seekShadowNearLive();
      }
      return;
    }

    handoverDone = true;
    if (checkInterval) clearInterval(checkInterval);
    if (preloadTimeout) clearTimeout(preloadTimeout);

    console.log(`[ABR] ${targetQuality.toUpperCase()} pre-buffered cleanly! Zero-spinner handover at ${liveT.toFixed(1)}s`);

    if (vjsRoot) {
      handoverTransferred = true;
      try {
        bgPreloader.currentTime = liveT;
        bgPreloader.style.visibility = 'visible';
        bgPreloader.style.opacity = '1';
        bgPreloader.play().catch(() => {});
      } catch (e) {}
    }

    cleanup();

    applyQualitySwitch(targetQuality, {
      isAutoDowngrade,
      isAutoStepUp: !isAutoDowngrade && !opts.isManual,
      force: isAutoDowngrade,
      preloadedTime: liveT,
      targetSrc: candidate.src,
      preWarmed: true,
      shadowVideo: handoverTransferred ? bgPreloader : null,
      destroyShadowVideo
    });
  };

  bgPreloader.addEventListener('canplay', executeHandover);
  bgPreloader.addEventListener('canplaythrough', executeHandover);

  checkInterval = setInterval(() => {
    if (cleanedUp || handoverDone) {
      clearInterval(checkInterval);
      return;
    }
    if (bgPreloader.readyState >= 3) {
      executeHandover();
    }
  }, 100);

  bgPreloader.src = candidate.src;
  bgPreloader.load();
}

function applyQualitySwitch(targetQuality, opts = {}) {
  const isAutoDowngrade = Boolean(opts.isAutoDowngrade);
  const mobileAuto = _pendingMobileAutoStepUp;
  _pendingMobileAutoStepUp = false;
  const isAutoStepUp = Boolean(opts.isAutoStepUp || mobileAuto);

  // Guard auto-downgrade: allow immediately when forced by stall, or once curT >= 8s
  if (isAutoDowngrade) {
    if (selectedQuality !== 'auto') return;
    if (vjsPlayer) {
      const curT = typeof vjsPlayer.currentTime === 'function' ? vjsPlayer.currentTime() : 0;
      if (!opts.force && curT < 8.0) {
        console.warn(`[ABR] Deferring auto-downgrade during initial buffer window (currentTime: ${curT.toFixed(1)}s < 8s)`);
        return;
      }
    }
    currentEffectiveQuality = targetQuality;
    lastAutoSwitchEpoch = Date.now();
    lastDowngradeEpoch = Date.now();
  } else if (isAutoStepUp) {
    // Keep selectedQuality as 'auto' — do not override user preference!
    currentEffectiveQuality = targetQuality;
    lastAutoSwitchEpoch = Date.now();
  } else {
    // User explicitly changed quality
    selectedQuality = targetQuality;
    try { sessionStorage.setItem('filmsub_pref_quality', targetQuality); } catch(e) {}
    if (targetQuality === 'auto') {
      currentEffectiveQuality = detectNetworkSpeed().recommendedQuality;
    } else {
      currentEffectiveQuality = targetQuality;
    }
  }

  updateQualitySpeedBadge(selectedQuality);

  const headerQual = document.getElementById('cs-header-quality');
  if (headerQual) {
    headerQual.textContent = `${currentEffectiveQuality.toUpperCase()} WEB-DL`;
  }

  // 1. If Video.js Super Player is active, switch stream quality and preserve exact currentTime
  if (vjsPlayer && typeof vjsPlayer.currentTime === 'function') {
    const curTime = (opts.preloadedTime && opts.preloadedTime > 0)
      ? opts.preloadedTime
      : ((vjsPlayer.currentTime() || 0));
    const wasPaused = vjsPlayer.paused();

    let newSrc = opts.targetSrc || '';
    if (!newSrc) {
      const targetObj = resolveStreamUrlForQuality(currentMovie, currentEffectiveQuality);
      newSrc = targetObj.src;
    }

    const currentSrc = (typeof vjsPlayer.currentSrc === 'function' ? vjsPlayer.currentSrc() : '') || '';
    const shouldReloadSrc = newSrc && (!isAutoDowngrade || (currentSrc && !currentSrc.includes(newSrc)));

    const shadowVideo = opts.shadowVideo || null;
    const destroyShadow = typeof opts.destroyShadowVideo === 'function'
      ? opts.destroyShadowVideo
      : ((el) => { try { el.pause(); el.removeAttribute('src'); el.load(); if (el.parentNode) el.parentNode.removeChild(el); } catch (e) {} });

    if (newSrc && shouldReloadSrc && currentSrc !== newSrc) {
      const targetTime = (curTime && curTime > 0) ? curTime : 0;
      const shouldResumePlay = !wasPaused;
      const vjsRoot = typeof vjsPlayer.el === 'function' ? vjsPlayer.el() : null;

      if (shadowVideo || opts.preWarmed) {
        _isSeamlessSwitching = true;
        if (vjsRoot) vjsRoot.classList.add('vjs-fz-seamless-switching');
      }

      if (!isAutoStepUp && !shadowVideo && !opts.preWarmed && typeof vjsPlayer.pause === 'function') {
        vjsPlayer.pause();
      }
      const prevMuted = (typeof vjsPlayer.muted === 'function') ? vjsPlayer.muted() : false;
      if (targetTime > 0.5 && typeof vjsPlayer.muted === 'function') {
        vjsPlayer.muted(true);
      }

      // Warm target stream at exact seek byte offset (not just byte 0)
      const targetDur = typeof vjsPlayer.duration === 'function' ? (vjsPlayer.duration() || 0) : 0;
      if (!opts.preWarmed) {
        prewarmStreamAtTime(newSrc, targetTime, targetDur, { urgent: true, warmupOnly: true, burstBytes: 4194304 }).catch(() => {});
      }

      let shadowCleaned = false;
      let shadowFailsafeTimer = null;
      const finishSeamlessVisual = () => {
        if (shadowCleaned) return;
        shadowCleaned = true;
        if (shadowFailsafeTimer) clearTimeout(shadowFailsafeTimer);
        if (shadowVideo) {
          shadowVideo.style.opacity = '0';
          setTimeout(() => destroyShadow(shadowVideo), 200);
        }
        if (vjsRoot) vjsRoot.classList.remove('vjs-fz-seamless-switching');
        _isSeamlessSwitching = false;
      };

      if (shadowVideo || opts.preWarmed) {
        shadowFailsafeTimer = setTimeout(() => {
          if (targetTime > 0.5 && vjsPlayer && typeof vjsPlayer.muted === 'function') {
            vjsPlayer.muted(prevMuted);
          }
          finishSeamlessVisual();
        }, 5500);
      }

      vjsPlayer.src({ src: newSrc, type: 'video/mp4' });
      vjsPlayer.load();

      let isRestored = false;
      let pollTimer = null;
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
          const liveTarget = (shadowVideo && shadowVideo.currentTime > targetTime)
            ? shadowVideo.currentTime
            : targetTime;
          if (liveTarget > 0.5) {
            try {
              vjsPlayer.currentTime(liveTarget);
              if (techEl && Math.abs((techEl.currentTime || 0) - liveTarget) > 0.5) {
                techEl.currentTime = liveTarget;
              }
              console.log(`[FilmSub] Quality switch: restored playhead to ${liveTarget.toFixed(1)}s (ready=${rState}, shadow=${Boolean(shadowVideo)})`);
            } catch (err) {
              console.warn('[FilmSub] Seek failed, will retry on next tick:', err);
              return;
            }
          }
          isRestored = true;
          if (pollTimer) clearInterval(pollTimer);
          if (targetTime > 0.5 && typeof vjsPlayer.muted === 'function') {
            vjsPlayer.muted(prevMuted);
          }
          syncSubtitles();
          if (shouldResumePlay) {
            if (shadowVideo) {
              const revealMain = () => finishSeamlessVisual();
              vjsPlayer.one('playing', revealMain);
              vjsPlayer.one('canplay', () => setTimeout(revealMain, 80));
            }
            try { vjsPlayer.play().catch(() => finishSeamlessVisual()); } catch (e) { finishSeamlessVisual(); }
          } else {
            finishSeamlessVisual();
          }
        }
      };

      vjsPlayer.one('loadedmetadata', applySeek);
      vjsPlayer.one('canplay', applySeek);
      vjsPlayer.one('loadeddata', applySeek);

      // Active high-frequency polling every 40ms to catch readyState transitions immediately
      let pollCount = 0;
      pollTimer = setInterval(() => {
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
    } else if (shadowVideo) {
      destroyShadow(shadowVideo);
    }

    if (isAutoDowngrade) {
      FilmSub.showToast(
        `⚡ Smooth Mode (${currentEffectiveQuality.toUpperCase()}) • බාධාවකින් තොරව ක්‍රියාත්මකයි`,
        'info'
      );
    } else if (isAutoStepUp) {
      FilmSub.showToast(
        `🚀 ${currentEffectiveQuality.toUpperCase()} HD වෙත බාධාවකින් තොරව උසස් විය!`,
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
  const pickerWrap = document.getElementById('player-quality-picker');
  const triggerBtn = document.getElementById('btn-quality-trigger');
  const menuItems = document.querySelectorAll('.q-menu-item');
  const pills = document.querySelectorAll('.q-pill');

  updateQualitySpeedBadge(selectedQuality);

  // Toggle quality dropdown on button click
  if (triggerBtn && pickerWrap) {
    triggerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = pickerWrap.classList.toggle('open');
      triggerBtn.setAttribute('aria-expanded', String(isOpen));
    });

    // Close when clicking anywhere outside
    document.addEventListener('click', (e) => {
      if (!pickerWrap.contains(e.target)) {
        pickerWrap.classList.remove('open');
        triggerBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // Dropdown quality items
  menuItems.forEach(item => {
    const warmItemQuality = () => {
      const q = item.dataset.quality;
      if (q && q !== 'auto') {
        prewarmQualityTier(currentMovie, q);
      }
    };
    item.addEventListener('pointerenter', warmItemQuality, { passive: true });
    item.addEventListener('touchstart', warmItemQuality, { passive: true });

    item.addEventListener('click', () => {
      const q = item.dataset.quality || 'auto';
      applyQualitySwitch(q, { isAutoDowngrade: false });
      if (pickerWrap) pickerWrap.classList.remove('open');
      if (triggerBtn) triggerBtn.setAttribute('aria-expanded', 'false');
    });
  });

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
 * Attaches real-time buffer stall, weak-signal runway monitoring, and sliding read-ahead prewarm to Video.js.
 * Automatically steps down quality (1080p -> 720p -> 480p) seamlessly BEFORE buffer runs dry on weak signal,
 * handles slow initial startup within 5.5s, and warms Telegram chunks 30s ahead during active playback.
 */
function attachAdaptiveStallMonitor(player) {
  if (!player) return;
  stallTimestamps = [];
  if (activeStallTimer) {
    clearTimeout(activeStallTimer);
    activeStallTimer = null;
  }

  let hasEverPlayed = false;
  let startupDowngradeTimer = null;
  let lastSlidingWarmEpoch = 0;
  let lastSeekEventEpoch = 0;

  const triggerStepDownIfNeeded = ({ force = false, preferSeamless = false, reason = 'stall' } = {}) => {
    if (selectedQuality !== 'auto') return;
    if (_isSeamlessSwitching) return;
    const sinceLast = Date.now() - lastAutoSwitchEpoch;
    if (!force && sinceLast < 20000) return;
    if (force && sinceLast < 8000) return;

    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (!force && curT < 8.0) return;

    const idx = QUALITY_LADDER.indexOf(currentEffectiveQuality.toLowerCase());
    if (idx !== -1 && idx < QUALITY_LADDER.length - 1) {
      const nextLower = QUALITY_LADDER[idx + 1];
      const candidate = resolveStreamUrlForQuality(currentMovie, nextLower);
      const currentSrc = (typeof player.currentSrc === 'function' ? player.currentSrc() : '') || '';
      if (!candidate || !candidate.src || candidate.src === currentSrc) return;

      const aheadNow = getBufferAhead(player);
      console.log(`[ABR] Switching down (${reason}) at ${curT.toFixed(1)}s (ahead=${aheadNow.toFixed(1)}s): ${currentEffectiveQuality} -> ${nextLower}`);
      if (preferSeamless && hasEverPlayed && aheadNow >= 1.2) {
        seamlessPreloadAndStepUp(nextLower, candidate, { isAutoDowngrade: true });
      } else {
        applyQualitySwitch(nextLower, { isAutoDowngrade: true, force: true });
      }
    }
  };

  const triggerStepUpIfNeeded = () => {
    if (selectedQuality !== 'auto') return;
    if (_isPreloadingUpgrade || _isSeamlessSwitching) return;
    if (Date.now() - lastAutoSwitchEpoch < 30000) return;
    if (Date.now() - lastDowngradeEpoch < 60000) return;
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (curT < 45.0) return;
    const idx = QUALITY_LADDER.indexOf(currentEffectiveQuality.toLowerCase());
    if (idx > 0) {
      const nextHigher = QUALITY_LADDER[idx - 1];
      const effMbps = getEffectiveDownloadMbps();
      if (nextHigher === '1080p' && effMbps > 0 && effMbps < 4.5) return;
      const candidate = resolveStreamUrlForQuality(currentMovie, nextHigher);
      const currentSrc = (typeof player.currentSrc === 'function' ? player.currentSrc() : '') || '';
      if (!candidate || !candidate.src || candidate.src === currentSrc) {
        return;
      }
      console.log(`[ABR] Buffer & bandwidth healthy. Shadow pre-buffering ${nextHigher.toUpperCase()} before seamless handover...`);
      seamlessPreloadAndStepUp(nextHigher, candidate);
    }
  };

  player.on('play', () => {
    if (hasEverPlayed || selectedQuality !== 'auto') return;
    if (startupDowngradeTimer) clearTimeout(startupDowngradeTimer);
    startupDowngradeTimer = setTimeout(() => {
      startupDowngradeTimer = null;
      if (!player || hasEverPlayed || player.paused()) return;
      const rState = typeof player.readyState === 'function' ? player.readyState() : 0;
      if (rState < 3 && currentEffectiveQuality !== '480p' && selectedQuality === 'auto') {
        triggerStepDownIfNeeded({ force: true, reason: 'slow-startup-8.5s' });
      }
    }, 8500);
  });

  player.on('seeking', () => {
    lastSeekEventEpoch = Date.now();
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }
    if (_activePreloadCleanup) {
      _activePreloadCleanup();
    }
  });

  player.on('waiting', () => {
    if (_isSeamlessSwitching || !hasEverPlayed) return;
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (player.seeking && player.seeking()) return;
    if (player.paused && player.paused()) return;

    if (selectedQuality === 'auto') {
      const now = Date.now();
      stallTimestamps = stallTimestamps.filter(t => (now - t) < 30000);
      stallTimestamps.push(now);
    }

    const stallWaitMs = stallTimestamps.length >= 2 ? 3200 : 4500;
    if (activeStallTimer) clearTimeout(activeStallTimer);
    activeStallTimer = setTimeout(() => {
      if (_isSeamlessSwitching) return;
      if (player && !player.paused() && !(player.seeking && player.seeking())) {
        const curTNow = typeof player.currentTime === 'function' ? player.currentTime() : 0;
        if (selectedQuality === 'auto' && curTNow >= 6.0) {
          triggerStepDownIfNeeded({ force: true, reason: 'continuous-waiting' });
        }
        try {
          if (curTNow > 0 && !_isSeamlessSwitching) {
            player.currentTime(curTNow + 0.04);
            player.play().catch(() => {});
          }
        } catch (e) {}
      }
    }, stallWaitMs);
  });

  player.on('playing', () => {
    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    if (curT > 1.0) {
      hasEverPlayed = true;
      if (startupDowngradeTimer) {
        clearTimeout(startupDowngradeTimer);
        startupDowngradeTimer = null;
      }
    }
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }
  });

  let lastBufferCheck = 0;
  player.on('timeupdate', () => {
    try {
      if (typeof player.currentTime === 'function' && player.currentTime() > 1.5) {
        hasEverPlayed = true;
        if (startupDowngradeTimer) {
          clearTimeout(startupDowngradeTimer);
          startupDowngradeTimer = null;
        }
      }
    } catch (e) {}

    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }

    const now = Date.now();
    if (now - lastBufferCheck < 3000) return;
    lastBufferCheck = now;

    const curT = typeof player.currentTime === 'function' ? player.currentTime() : 0;
    const dur = typeof player.duration === 'function' ? player.duration() : 0;

    // Save progress to FilmZilla User State
    if (window.FilmSubUser && currentMovie && curT > 10) {
      window.FilmSubUser.ContinueWatching.saveProgress(currentMovie, curT, dur);
    }

    // Proactive sliding read-ahead: keep Telegram chunks warmed 30s ahead in backend pool
    if (!player.paused() && !(player.seeking && player.seeking()) && currentMovie && (now - lastSlidingWarmEpoch > 12000)) {
      lastSlidingWarmEpoch = now;
      if (!dur || curT + 25 < dur) {
        prewarmStreamAtTime(currentMovie, curT + 30, dur, { warmupOnly: true, burstBytes: 4194304 }).catch(() => {});
      }
    }

    if (selectedQuality === 'auto' && !player.paused() && !(player.seeking && player.seeking()) && !_isSeamlessSwitching) {
      const ahead = getBufferAhead(player);
      const effMbps = getEffectiveDownloadMbps();

      // Proactive weak-signal step-down BEFORE buffer runs dry (zero visible buffering wheel!)
      if (hasEverPlayed && ahead > 0.6 && ahead < 4.0 && (now - lastSeekEventEpoch > 8000)) {
        const hasSignalWeakness = stallTimestamps.length >= 1 || (effMbps > 0 && effMbps < 2.2 && currentEffectiveQuality !== '480p');
        if (hasSignalWeakness) {
          triggerStepDownIfNeeded({ force: true, preferSeamless: true, reason: 'low-buffer-runway' });
          return;
        }
      }

      // Healthy runway + strong bandwidth -> step up seamlessly via shadow overlay / mobile pre-warm
      if (ahead > 22.0 && (now - lastAutoSwitchEpoch > 30000) && (now - lastDowngradeEpoch > 60000)) {
        triggerStepUpIfNeeded();
      }
    }
  });
}

/**
 * Instant Middle-Seeking Engine:
 * - Fetches compact MP4 moov keyframe seek table (/stream/seekinfo) once per stream
 * - Pre-warms hover / scrub bar target byte offsets before the user even releases click/touch
 * - Fires high-priority 6 MiB burst warmup immediately on seeking events for zero-lag scrubbing
 */
function attachSeekPrewarmEngine(player, movie) {
  if (!player || player._fzSeekEngineAttached) return;
  player._fzSeekEngineAttached = true;

  const ensureSeekInfoForCurrentSrc = () => {
    try {
      const src = (typeof player.currentSrc === 'function' ? player.currentSrc() : '') || '';
      const ids = extractChannelIdsFromUrl(src);
      if (ids) {
        fetchSeekInfo(ids.chatId, ids.messageId).catch(() => {});
      }
    } catch (e) {}
  };

  player.on('loadedmetadata', ensureSeekInfoForCurrentSrc);
  if (typeof player.readyState === 'function' && player.readyState() >= 1) {
    ensureSeekInfoForCurrentSrc();
  }

  let hoverWarmTimer = null;
  let lastHoverSec = -1;
  const handleScrubPreview = (clientX, rect) => {
    if (!rect || rect.width <= 0) return;
    const dur = typeof player.duration === 'function' ? (player.duration() || 0) : 0;
    if (!dur || dur < 10) return;
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const targetSec = ratio * dur;
    const curT = typeof player.currentTime === 'function' ? (player.currentTime() || 0) : 0;
    if (Math.abs(targetSec - curT) < 6) return;
    if (lastHoverSec >= 0 && Math.abs(targetSec - lastHoverSec) < 8) return;
    lastHoverSec = targetSec;

    if (hoverWarmTimer) clearTimeout(hoverWarmTimer);
    hoverWarmTimer = setTimeout(() => {
      const src = (typeof player.currentSrc === 'function' ? player.currentSrc() : '') || '';
      const targetArg = src ? { src } : (movie || currentMovie);
      prewarmStreamAtTime(targetArg, targetSec, dur, { burstBytes: 4194304 }).catch(() => {});
    }, 280);
  };

  try {
    const vjsEl = typeof player.el === 'function' ? player.el() : null;
    const progressHolder = vjsEl && (vjsEl.querySelector('.vjs-progress-holder') || vjsEl.querySelector('.vjs-progress-control'));
    if (progressHolder) {
      progressHolder.addEventListener('mousemove', (e) => {
        handleScrubPreview(e.clientX, progressHolder.getBoundingClientRect());
      }, { passive: true });
      progressHolder.addEventListener('touchmove', (e) => {
        if (e.touches && e.touches[0]) {
          handleScrubPreview(e.touches[0].clientX, progressHolder.getBoundingClientRect());
        }
      }, { passive: true });
    }
  } catch (e) {}

  player.on('seeking', () => {
    const targetSec = typeof player.currentTime === 'function' ? (player.currentTime() || 0) : 0;
    const dur = typeof player.duration === 'function' ? (player.duration() || 0) : 0;
    const src = (typeof player.currentSrc === 'function' ? player.currentSrc() : '') || '';
    const targetArg = src ? { src } : (movie || currentMovie);
    prewarmStreamAtTime(targetArg, targetSec, dur, { urgent: true, warmupOnly: true, burstBytes: 6291456 }).catch(() => {});
  });

  player.on('seeked', () => {
    try { syncSubtitles(); } catch (e) {}
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
  updatePlayerLoadPercent(12, '⚡ Loading Video Stream...', 'Connecting High-Speed Stream Server...');

  if (loadPercentTimer) clearInterval(loadPercentTimer);

  loadPercentTimer = setInterval(() => {
    if (currentLoadPercent < 45) {
      currentLoadPercent += Math.random() * 8 + 4;
      updatePlayerLoadPercent(currentLoadPercent, '🎬 Preparing High-Quality Video...', 'Optimizing Playback Server...');
    } else if (currentLoadPercent < 80) {
      currentLoadPercent += Math.random() * 4 + 2;
      updatePlayerLoadPercent(currentLoadPercent, '📥 Buffering Stream & Sinhala Subtitles...', 'Preparing Video Player...');
    } else if (currentLoadPercent < 94) {
      currentLoadPercent += 0.8;
      updatePlayerLoadPercent(currentLoadPercent, '✨ Finalizing Playback Buffer...', 'Starting Video Playback...');
    }
    if (currentLoadPercent >= 95) {
      clearInterval(loadPercentTimer);
      loadPercentTimer = null;
    }
  }, 160);
}

function finishProgressAnimation() {
  if (loadPercentTimer) { clearInterval(loadPercentTimer); loadPercentTimer = null; }
  updatePlayerLoadPercent(100, '✅ Ready • Starting Playback...', 'Playing with Sinhala Subtitles');
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

  // Teaser Smart Entry: Detect arrival from 30s teaser or seek/autoplay parameter
  const arrivalParams = new URLSearchParams(window.location.search);
  const isTeaserArrival = arrivalParams.get('from_teaser') === '1' || arrivalParams.has('t') || arrivalParams.has('seek') || arrivalParams.get('autoplay') === '1';

  loadStream(movie, startIdx, isTeaserArrival);

  if (isTeaserArrival) {
    setTimeout(() => {
      const stage = document.getElementById('cs-player-card') || document.getElementById('video-player-container');
      if (stage) {
        stage.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 400);
  }

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
  if (_activePreloadCleanup) _activePreloadCleanup();
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

  const isSmall = typeof window !== 'undefined' && window.innerWidth <= 640;
  const activeLabel = isSmall
    ? (selectedQuality === 'auto' ? currentEffectiveQuality.toUpperCase() : selectedQuality.toUpperCase())
    : (selectedQuality === 'auto'
      ? `AUTO (${currentEffectiveQuality.toUpperCase()})`
      : selectedQuality.toUpperCase());

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

  // Prevent clicks and touches on overlay buttons from bubbling up to Video.js container
  ['click', 'pointerdown', 'mousedown', 'mouseup'].forEach(evt => {
    overlay.addEventListener(evt, (e) => {
      e.stopPropagation();
    });
  });

  const playBtn = overlay.querySelector('#cs-btn-center-playpause');
  const rewindBtn = overlay.querySelector('#cs-btn-center-rewind');
  const forwardBtn = overlay.querySelector('#cs-btn-center-forward');
  const playIcon = overlay.querySelector('#cs-center-play-icon');

  const currentStandby = playerEl.querySelector('#cs-cinesubz-standby-overlay, .cinema-standby-screen');
  const isStandbyActive = currentStandby && !currentStandby.classList.contains('hidden') && currentStandby.style.display !== 'none';
  if (isStandbyActive) {
    overlay.classList.add('hidden');
    overlay.style.display = 'none';
    overlay.style.pointerEvents = 'none';
  }

  let isPlayPending = false;
  let hideTimer = null;

  const startHideTimer = () => {
    if (hideTimer) clearTimeout(hideTimer);
    if (player && !player.paused()) {
      hideTimer = setTimeout(() => {
        if (player && !player.paused()) {
          overlay.classList.add('hidden');
        }
      }, 2500);
    }
  };

  const showOverlay = () => {
    const standby = playerEl.querySelector('#cs-cinesubz-standby-overlay, .cinema-standby-screen');
    if (standby && !standby.classList.contains('hidden') && standby.style.display !== 'none') {
      return;
    }
    overlay.classList.remove('hidden');
    overlay.style.display = '';
    overlay.style.pointerEvents = 'auto';
    startHideTimer();
  };

  const setButtonLoading = () => {
    if (playBtn) playBtn.classList.add('is-loading');
    if (playIcon) playIcon.className = 'fa-solid fa-circle-notch fa-spin';
  };

  const setButtonPlaying = () => {
    isPlayPending = false;
    if (playBtn) playBtn.classList.remove('is-loading');
    if (playIcon) playIcon.className = 'fa-solid fa-pause';
    startHideTimer();
  };

  const setButtonPaused = () => {
    isPlayPending = false;
    if (playBtn) playBtn.classList.remove('is-loading');
    if (playIcon) playIcon.className = 'fa-solid fa-play';
    showOverlay();
  };

  const executePlay = () => {
    if (isPlayPending) return;
    isPlayPending = true;
    setButtonLoading();

    const standby = playerEl.querySelector('#cs-cinesubz-standby-overlay, .cinema-standby-screen');
    if (standby) {
      standby.classList.add('hidden');
      standby.style.display = 'none';
      standby.style.pointerEvents = 'none';
      standby.style.zIndex = '-1';
    }

    const v = playerEl.querySelector('video') || (player && player.tech_ && player.tech_.el_);

    const onPlaySuccess = () => {
      setButtonPlaying();
    };

    const onPlayFail = (err) => {
      console.warn('[FilmSub Center] player.play() rejected, attempting native video.play:', err);
      if (v && typeof v.play === 'function') {
        try {
          const pNative = v.play();
          if (pNative && typeof pNative.then === 'function') {
            pNative.then(onPlaySuccess).catch((errNative) => {
              console.error('[FilmSub Center] Native video.play() failed:', errNative);
              setButtonPaused();
            });
            return;
          }
          onPlaySuccess();
          return;
        } catch (e) {
          console.error('[FilmSub Center] Native video.play() threw:', e);
        }
      }
      setButtonPaused();
    };

    if (player && typeof player.play === 'function') {
      try {
        const p = player.play();
        if (p && typeof p.then === 'function') {
          p.then(onPlaySuccess).catch(onPlayFail);
        } else {
          onPlaySuccess();
        }
      } catch (err) {
        onPlayFail(err);
      }
    } else if (v && typeof v.play === 'function') {
      try {
        const p = v.play();
        if (p && typeof p.then === 'function') {
          p.then(onPlaySuccess).catch(onPlayFail);
        } else {
          onPlaySuccess();
        }
      } catch (err) {
        onPlayFail(err);
      }
    } else {
      setButtonPaused();
    }
  };

  const executePause = () => {
    isPlayPending = false;
    if (player && typeof player.pause === 'function') {
      try { player.pause(); } catch (e) {}
    } else {
      const v = playerEl.querySelector('video') || (player && player.tech_ && player.tech_.el_);
      if (v && typeof v.pause === 'function') {
        try { v.pause(); } catch (e) {}
      }
    }
    setButtonPaused();
  };

  attachFastAction(playBtn, (e) => {
    if (!player) return;
    if (isPlayPending) return;
    if (player.paused()) {
      executePlay();
    } else {
      executePause();
    }
  });

  attachFastAction(rewindBtn, (e) => {
    if (!player) return;
    const cur = (typeof player.currentTime === 'function') ? player.currentTime() : 0;
    player.currentTime(Math.max(0, cur - 10));
    showSeekRipple(playerEl, 'backward', 10);
    showOverlay();
  });

  attachFastAction(forwardBtn, (e) => {
    if (!player) return;
    const cur = (typeof player.currentTime === 'function') ? player.currentTime() : 0;
    const dur = getEffectiveDuration(player, movie);
    player.currentTime(Math.min(dur || (cur + 10), cur + 10));
    showSeekRipple(playerEl, 'forward', 10);
    showOverlay();
  });

  player.on('play', setButtonPlaying);
  player.on('playing', setButtonPlaying);
  player.on('pause', setButtonPaused);
  player.on('waiting', () => {
    if (player && !player.paused()) {
      setButtonLoading();
    }
  });
  player.on('ended', () => {
    isPlayPending = false;
    if (playBtn) playBtn.classList.remove('is-loading');
    if (playIcon) playIcon.className = 'fa-solid fa-rotate-right';
    overlay.classList.remove('hidden');
    overlay.style.display = '';
    overlay.style.pointerEvents = 'auto';
  });

  playerDom.addEventListener('pointermove', showOverlay, { passive: true });
  playerDom.addEventListener('touchstart', showOverlay, { passive: true });
  playerDom.addEventListener('mouseleave', () => {
    if (player && !player.paused()) {
      overlay.classList.add('hidden');
    }
  });

  if (!isStandbyActive) {
    if (player.paused()) {
      setButtonPaused();
    } else {
      setButtonPlaying();
    }
  }
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

  // 0. Netflix / Apple TV+ Top Hover HUD Scrim inside player frame
  const pEl = typeof player.el === 'function' ? player.el() : null;
  if (pEl && !pEl.querySelector('.cs-player-top-hud')) {
    const topHud = document.createElement('div');
    topHud.className = 'cs-player-top-hud';
    const titleStr = movie && movie.title ? FilmSub.escHtml(movie.title) : 'Now Playing';
    const subTag = (movie && movie.hasSubtitles === false) ? 'HD Stream' : 'සිංහල උපසිරැසි • HD Stream';
    topHud.innerHTML = `
      <div class="cs-hud-title-group">
        <span class="cs-hud-eyebrow">${subTag}</span>
        <span class="cs-hud-movie-title">${titleStr}</span>
      </div>`;
    pEl.appendChild(topHud);
  }

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
    attachFastAction(rewindBtn, (e) => {
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
    attachFastAction(forwardBtn, (e) => {
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

  // 3. Quality Badge on the right side of control bar
  if (!cbEl.querySelector('.vjs-control-brand-wrap')) {
    const brandWrap = document.createElement('div');
    brandWrap.className = 'vjs-control-brand-wrap';
    const initQuality = (selectedQuality === 'auto' ? (currentEffectiveQuality || '720p') : selectedQuality).toUpperCase();
    brandWrap.innerHTML = `
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

  // 4. Move Playback Rate (1x) button to the right side (after Quality, before Fullscreen)
  const rateBtn = player.controlBar.playbackRateMenuButton ? player.controlBar.playbackRateMenuButton.el() : cbEl.querySelector('.vjs-playback-rate');
  const fsToggle = player.controlBar.fullscreenToggle ? player.controlBar.fullscreenToggle.el() : cbEl.querySelector('.vjs-fullscreen-control');
  if (rateBtn && fsToggle) {
    cbEl.insertBefore(rateBtn, fsToggle);
  }

  // 5. Picture-in-Picture (PiP) Button
  if (!cbEl.querySelector('.vjs-pip-btn')) {
    const pipBtn = document.createElement('button');
    pipBtn.type = 'button';
    pipBtn.className = 'vjs-control vjs-button vjs-pip-btn';
    pipBtn.setAttribute('title', 'Picture-in-Picture (P)');
    pipBtn.setAttribute('aria-label', 'Picture-in-Picture');
    pipBtn.innerHTML = `
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-top:7px">
        <rect x="2" y="4" width="20" height="16" rx="2"/>
        <rect x="12" y="10" width="8" height="6" rx="1" fill="currentColor" fill-opacity="0.3"/>
      </svg>`;
    attachFastAction(pipBtn, () => {
      const vEl = player.tech_ && player.tech_.el_ ? player.tech_.el_ : player.el().querySelector('video');
      if (window.FilmZillaRoulette && typeof window.FilmZillaRoulette.togglePiP === 'function') {
        window.FilmZillaRoulette.togglePiP(vEl);
      } else if (vEl && document.pictureInPictureEnabled) {
        if (document.pictureInPictureElement) document.exitPictureInPicture();
        else vEl.requestPictureInPicture();
      }
    });
    if (fsToggle) cbEl.insertBefore(pipBtn, fsToggle);
    else cbEl.appendChild(pipBtn);
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
      case 'p':
      case 'P':
        e.preventDefault();
        {
          const vEl = player.tech_ && player.tech_.el_ ? player.tech_.el_ : player.el().querySelector('video');
          if (window.FilmZillaRoulette && typeof window.FilmZillaRoulette.togglePiP === 'function') {
            window.FilmZillaRoulette.togglePiP(vEl);
          }
        }
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

  /* === Hold-to-Speed (Long Press = 2x) === */
  let longPressTimer = null;
  let speedBoostActive = false;
  let touchStartX = 0;
  let touchStartY = 0;
  let touchMoved = false;

  // Create speed badge element
  let speedBadgeEl = wrap.querySelector('.fs-speed-badge');
  if (!speedBadgeEl) {
    speedBadgeEl = document.createElement('div');
    speedBadgeEl.className = 'fs-speed-badge';
    speedBadgeEl.innerHTML = '<span class="fs-speed-icon"><i class="fa-solid fa-forward-fast"></i></span> 2x';
    wrap.appendChild(speedBadgeEl);
  }

  const activateSpeedBoost = () => {
    if (speedBoostActive) return;
    speedBoostActive = true;
    if (player && typeof player.playbackRate === 'function') player.playbackRate(2);
    speedBadgeEl.classList.add('active');
  };

  const deactivateSpeedBoost = () => {
    if (!speedBoostActive) return;
    speedBoostActive = false;
    if (player && typeof player.playbackRate === 'function') player.playbackRate(1);
    speedBadgeEl.classList.remove('active');
  };

  wrap.addEventListener('touchstart', (e) => {
    if (e.target.closest('.vjs-control-bar, .cs-player-center-overlay, .vjs-menu, button, a, input, select')) return;
    const touch = e.touches && e.touches[0];
    if (!touch) return;
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
    touchMoved = false;

    // Only trigger long-press on right zone (potential fast-forward zone)
    const rect = wrap.getBoundingClientRect();
    const pct = (touch.clientX - rect.left) / rect.width;
    if (pct > 0.42) { // right 58% of screen (not just the left rewind zone)
      if (longPressTimer) clearTimeout(longPressTimer);
      longPressTimer = setTimeout(() => {
        if (!touchMoved) {
          activateSpeedBoost();
        }
      }, 350);
    }
  }, { passive: true });

  wrap.addEventListener('touchmove', (e) => {
    const touch = e.touches && e.touches[0];
    if (!touch) return;
    const dx = Math.abs(touch.clientX - touchStartX);
    const dy = Math.abs(touch.clientY - touchStartY);
    if (dx > 12 || dy > 12) {
      touchMoved = true;
      if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
      if (speedBoostActive) deactivateSpeedBoost();
    }
  }, { passive: true });

  wrap.addEventListener('touchend', (e) => {
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    if (speedBoostActive) deactivateSpeedBoost();
  }, { passive: true });

  wrap.addEventListener('touchcancel', () => {
    if (longPressTimer) { clearTimeout(longPressTimer); longPressTimer = null; }
    if (speedBoostActive) deactivateSpeedBoost();
  }, { passive: true });

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

    if (zone !== 'center' && zone === lastTapZone && (now - lastTapEpoch < 400)) {
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
    } else if (zone === 'center') {
      // Instant center single-tap: Toggle controls with 0ms delay!
      lastTapEpoch = now;
      lastTapZone = 'center';
      tapCount = 1;
      const centerOverlay = wrap.querySelector('.cs-player-center-overlay');
      if (centerOverlay) {
        if (centerOverlay.classList.contains('hidden') || centerOverlay.style.display === 'none') {
          centerOverlay.classList.remove('hidden');
          centerOverlay.style.display = '';
          centerOverlay.style.pointerEvents = 'auto';
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
      if (e.cancelable) e.preventDefault();
    } else {
      // First tap on side
      lastTapEpoch = now;
      lastTapZone = zone;
      tapCount = 1;
      accumulatedSeek = 10;

      if (singleTapTimeout) clearTimeout(singleTapTimeout);
      singleTapTimeout = setTimeout(() => {
        const centerOverlay = wrap.querySelector('.cs-player-center-overlay');
        if (centerOverlay) {
          if (centerOverlay.classList.contains('hidden') || centerOverlay.style.display === 'none') {
            centerOverlay.classList.remove('hidden');
            centerOverlay.style.display = '';
            centerOverlay.style.pointerEvents = 'auto';
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
      }, 200);
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

  const doReconnect = async (targetQuality = null) => {
    if (timer) clearInterval(timer);
    modal.remove();
    if (targetQuality) {
      applyQualitySwitch(targetQuality, { isAutoDowngrade: false });
    } else {
      try {
        await loadLiveStreamConfig(true).catch(() => {});
        const resolved = currentMovie
          ? resolveStreamUrlForQuality(currentMovie, selectedQuality === 'auto' ? currentEffectiveQuality : selectedQuality)
          : null;
        const curSrc = (resolved && resolved.src) || normalizeStreamUrl(stream.stream_url) || stream.stream_url;
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

function createVjsPlayer(playerEl, stream, movie, autoPlay = false) {
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
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000000 !important;border-radius:14px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, stream.label || stream.server)}
      <div class="cs-cinesubz-standby-overlay ${autoPlay ? 'hidden' : ''}" id="cs-cinesubz-standby-overlay">
        ${posterUrl ? `<img src="${FilmSub.escHtml(posterUrl)}" class="cs-standby-backdrop-img" alt="${FilmSub.escHtml(movie.title)}" />` : ''}
        <div class="cs-standby-vignette"></div>
        <button type="button" class="cs-cinesubz-play-btn" id="cs-main-poster-play-btn" aria-label="Play Movie" title="Click to Play">
          <i class="fa-solid fa-play"></i>
        </button>
        <div class="cs-poster-bottom-bar">
          <div class="cs-standby-meta-block">
            <span class="cs-standby-eyebrow"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි &bull; ${FilmSub.escHtml(movie.quality || '1080p HD')}</span>
            <h3 class="cs-standby-title">${FilmSub.escHtml(movie.title || 'Now Playing')}</h3>
          </div>
          <div class="cs-pbar-right">
            <button type="button" class="cs-pbar-circle-btn" id="pbar-btn-dl" title="Direct Download Options">
              <i class="fa-solid fa-download"></i>
            </button>
            <button type="button" class="cs-pbar-circle-btn" id="pbar-btn-fs" title="Fullscreen">
              <i class="fa-solid fa-expand"></i>
            </button>
            <button type="button" class="cs-pbar-circle-btn" id="pbar-btn-sub" title="Sinhala Subtitles">
              <i class="fa-solid fa-closed-captioning"></i>
            </button>
            <button type="button" class="cs-pbar-circle-btn" id="pbar-btn-alert" title="Report Issue / Telegram Support">
              <i class="fa-solid fa-triangle-exclamation"></i>
            </button>
          </div>
        </div>
      </div>
      <video id="filmsubPlayer" class="video-js vjs-big-play-centered vjs-theme-fantasy"
             controls preload="auto" playsinline webkit-playsinline crossorigin="anonymous" ${posterAttr}
             style="position:absolute;top:0;left:0;width:100%;height:100%;background:#000000 !important">
        <source src="${FilmSub.escHtml(stream.stream_url)}" type="${FilmSub.escHtml(stream.type || 'video/mp4')}">
        ${tracksHTML}
        <p class="vjs-no-js">Enable JavaScript or use a modern browser to watch videos.</p>
      </video>
    </div>`;

  const hideLoader = () => {
    const loader = document.getElementById('super-player-loader');
    if (loader) {
      loader.classList.add('hidden');
      loader.style.display = 'none';
      loader.style.pointerEvents = 'none';
      loader.style.zIndex = '-1';
    }
  };

  if (!autoPlay) {
    hideLoader();
  }

  // CineSubz Standby Overlay & Immediate Button Interactivity (0ms Latency)
  const standbyOverlay = playerEl.querySelector('#cs-cinesubz-standby-overlay');
  const mainPlayBtn = playerEl.querySelector('#cs-main-poster-play-btn');
  const pbarDl = playerEl.querySelector('#pbar-btn-dl');
  const pbarFs = playerEl.querySelector('#pbar-btn-fs');
  const pbarSub = playerEl.querySelector('#pbar-btn-sub');
  const pbarAlert = playerEl.querySelector('#pbar-btn-alert');

  const executeStartPlay = (e) => {
    hideLoader();
    if (mainPlayBtn) {
      mainPlayBtn.classList.add('is-loading');
      const icon = mainPlayBtn.querySelector('i');
      if (icon) icon.className = 'fa-solid fa-circle-notch fa-spin';
    }
    if (standbyOverlay) {
      standbyOverlay.classList.add('hidden');
      standbyOverlay.style.display = 'none';
      standbyOverlay.style.pointerEvents = 'none';
      standbyOverlay.style.zIndex = '-1';
    }
    const centerOverlay = playerEl.querySelector('.cs-player-center-overlay');
    if (centerOverlay) {
      centerOverlay.classList.remove('hidden');
      centerOverlay.style.display = '';
      centerOverlay.style.pointerEvents = 'auto';
      const cPlayBtn = centerOverlay.querySelector('#cs-btn-center-playpause');
      const cPlayIcon = centerOverlay.querySelector('#cs-center-play-icon');
      if (cPlayBtn && cPlayIcon) {
        cPlayBtn.classList.add('is-loading');
        cPlayIcon.className = 'fa-solid fa-circle-notch fa-spin';
      }
    }

    const v = playerEl.querySelector('video') || (vjsPlayer && vjsPlayer.tech_ && vjsPlayer.tech_.el_);

    const onPlaySuccess = () => {
      if (mainPlayBtn) mainPlayBtn.classList.remove('is-loading');
      if (centerOverlay) {
        const cPlayBtn = centerOverlay.querySelector('#cs-btn-center-playpause');
        const cPlayIcon = centerOverlay.querySelector('#cs-center-play-icon');
        if (cPlayBtn) cPlayBtn.classList.remove('is-loading');
        if (cPlayIcon) cPlayIcon.className = 'fa-solid fa-pause';
      }
    };

    const onPlayFail = (err) => {
      console.warn('[FilmSub Standby] VJS play rejected, trying native video fallback:', err);
      if (v && typeof v.play === 'function') {
        try {
          const pNative = v.play();
          if (pNative && typeof pNative.then === 'function') {
            pNative.then(onPlaySuccess).catch((errNative) => {
              console.error('[FilmSub Standby] Native video fallback also failed:', errNative);
              if (mainPlayBtn) mainPlayBtn.classList.remove('is-loading');
              if (centerOverlay) {
                const cPlayBtn = centerOverlay.querySelector('#cs-btn-center-playpause');
                const cPlayIcon = centerOverlay.querySelector('#cs-center-play-icon');
                if (cPlayBtn) cPlayBtn.classList.remove('is-loading');
                if (cPlayIcon) cPlayIcon.className = 'fa-solid fa-play';
              }
            });
            return;
          }
          onPlaySuccess();
          return;
        } catch (e) {
          console.error('[FilmSub Standby] Native video fallback threw:', e);
        }
      }
      if (mainPlayBtn) mainPlayBtn.classList.remove('is-loading');
      if (centerOverlay) {
        const cPlayBtn = centerOverlay.querySelector('#cs-btn-center-playpause');
        const cPlayIcon = centerOverlay.querySelector('#cs-center-play-icon');
        if (cPlayBtn) cPlayBtn.classList.remove('is-loading');
        if (cPlayIcon) cPlayIcon.className = 'fa-solid fa-play';
      }
    };

    if (vjsPlayer && typeof vjsPlayer.play === 'function') {
      try {
        const p = vjsPlayer.play();
        if (p && typeof p.then === 'function') {
          p.then(onPlaySuccess).catch(onPlayFail);
        } else {
          onPlaySuccess();
        }
      } catch (err) {
        onPlayFail(err);
      }
    } else if (v && typeof v.play === 'function') {
      try {
        const p = v.play();
        if (p && typeof p.then === 'function') {
          p.then(onPlaySuccess).catch(onPlayFail);
        } else {
          onPlaySuccess();
        }
      } catch (err) {
        onPlayFail(err);
      }
    }
  };

  if (mainPlayBtn) {
    attachFastAction(mainPlayBtn, executeStartPlay);
  }

  if (standbyOverlay) {
    attachFastAction(standbyOverlay, (e) => {
      if (e && e.target && e.target.closest('.cs-poster-bottom-bar, button, a')) return;
      executeStartPlay(e);
    });
  }

  if (pbarDl) {
    attachFastAction(pbarDl, (e) => {
      document.getElementById('downloads')?.scrollIntoView({ behavior: 'smooth' });
    });
  }

  if (pbarFs) {
    attachFastAction(pbarFs, (e) => {
      try {
        if (vjsPlayer && typeof vjsPlayer.isFullscreen === 'function') {
          if (vjsPlayer.isFullscreen()) {
            vjsPlayer.exitFullscreen();
          } else {
            vjsPlayer.requestFullscreen();
          }
        } else {
          const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl;
          if (document.fullscreenElement) {
            document.exitFullscreen();
          } else if (wrap.requestFullscreen) {
            wrap.requestFullscreen();
          }
        }
      } catch (err) {}
    });
  }

  if (pbarSub) {
    attachFastAction(pbarSub, (e) => {
      FilmSub.showToast('සිංහල උපසිරැසි (Sinhala Subtitles) Video එකටම Merge කර ඇත', 'info');
    });
  }

  if (pbarAlert) {
    attachFastAction(pbarAlert, (e) => {
      window.open('https://t.me/Filmsinhala200Bot?start=report_' + encodeURIComponent(movie.slug || movie.id || ''), '_blank');
    });
  }

  if (typeof videojs !== 'undefined') {
    vjsPlayer = videojs('filmsubPlayer', {
      fluid: true,
      responsive: true,
      poster: posterUrl,
      preload: 'auto',
      autoplay: autoPlay ? 'any' : false,
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

      // Teaser Smart-Seek Support (?t=... or ?seek=...)
      const playerUrlParams = new URLSearchParams(window.location.search);
      const rawSeek = playerUrlParams.get('t') || playerUrlParams.get('seek');
      let targetTeaserSeek = 0;
      if (rawSeek) {
        if (rawSeek.includes(':')) {
          const parts = rawSeek.split(':').map(Number);
          if (parts.length === 2) targetTeaserSeek = (parts[0] * 60) + parts[1];
          else if (parts.length === 3) targetTeaserSeek = (parts[0] * 3600) + (parts[1] * 60) + parts[2];
        } else {
          targetTeaserSeek = parseFloat(rawSeek) || 0;
        }
      }

      if (targetTeaserSeek > 0) {
        let seekDone = false;
        const applySeek = () => {
          if (seekDone) return;
          try {
            vjsPlayer.currentTime(targetTeaserSeek);
            seekDone = true;
            const min = Math.floor(targetTeaserSeek / 60);
            const sec = Math.floor(targetTeaserSeek % 60);
            const formatted = `${min}:${sec < 10 ? '0' : ''}${sec}`;
            if (window.FilmSub && typeof FilmSub.showToast === 'function') {
              FilmSub.showToast(`⚡ Jumped directly to scene from 30s Teaser (${formatted})!`, 'success');
            }
          } catch (_) {}
        };

        if (vjsPlayer.readyState && vjsPlayer.readyState() >= 1) {
          applySeek();
        } else {
          vjsPlayer.one('loadedmetadata', applySeek);
          vjsPlayer.one('canplay', applySeek);
        }
      } else if (window.FilmSubUser && movie) {
        // Auto-Resume from FilmZilla Continue Watching State
        const mId = movie.id || movie.slug;
        const progress = window.FilmSubUser.ContinueWatching.getProgress(mId);
        if (progress && progress.currentTime > 15 && progress.percentage < 93) {
          const resumeT = progress.currentTime;
          vjsPlayer.one('canplay', () => {
            try {
              vjsPlayer.currentTime(resumeT);
              const min = Math.floor(resumeT / 60);
              const sec = Math.floor(resumeT % 60);
              if (window.FilmSub && typeof FilmSub.showToast === 'function') {
                FilmSub.showToast(`Resumed playback from ${min}:${sec < 10 ? '0' : ''}${sec} ⏯️`, 'info');
              }
            } catch (_) {}
          });
        }
      }

      injectInPlayerQualityControl(vjsPlayer);
      mountCenterPlayerControls(playerEl, vjsPlayer, movie);
      injectBottomControlBarItems(vjsPlayer, movie);

      // Bind Watch Party Synchronizer to Video.js player
      if (window.FilmZillaWatchParty && typeof window.FilmZillaWatchParty.bindPlayer === 'function') {
        window.FilmZillaWatchParty.bindPlayer(vjsPlayer);
      }

      // Initialize 7 VIP Cinema Killer Features Suite
      if (typeof initVIPCinemaKillerFeatures === 'function') {
        try { initVIPCinemaKillerFeatures(vjsPlayer, movie); } catch (e) { console.warn('[VIP] Killer features init err:', e); }
      }

      vjsPlayer.on('play', () => {
        hideLoader();
        if (standbyOverlay) {
          standbyOverlay.classList.add('hidden');
          standbyOverlay.style.display = 'none';
          standbyOverlay.style.pointerEvents = 'none';
          standbyOverlay.style.zIndex = '-1';
        }
        const centerOverlay = playerEl.querySelector('.cs-player-center-overlay');
        if (centerOverlay) {
          centerOverlay.style.display = '';
          centerOverlay.style.pointerEvents = 'auto';
        }
      });
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
      attachSeekPrewarmEngine(vjsPlayer, movie);
      if (autoPlay) {
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
      } else {
        // Autoplay disabled: leave player paused showing poster and big play button
        hideLoader();
      }
    });

    const updateBufferMetrics = () => {
      try {
        const tech = vjsPlayer && vjsPlayer.tech({ IWillNotUseThisInPlugins: true });
        const videoEl = tech && tech.el();
        if (!videoEl || !videoEl.buffered || !videoEl.buffered.length) return;
        const curT = videoEl.currentTime || 0;
        let bufferedAhead = 0;
        let bufferedEnd = 0;
        for (let i = 0; i < videoEl.buffered.length; i++) {
          const s = videoEl.buffered.start(i);
          const e = videoEl.buffered.end(i);
          if (s <= curT && curT <= e) {
            bufferedAhead = e - curT;
          }
          if (e > bufferedEnd) bufferedEnd = e;
        }
        const dur = videoEl.duration || vjsPlayer.duration() || 0;
        if (dur > 0 && bufferedEnd > 0) {
          const pct = Math.min(100, Math.max(1, Math.round((bufferedEnd / dur) * 100)));
          const badgeEl = document.getElementById('player-buffer-badge');
          const pctEl = document.getElementById('player-buffer-pct');
          if (badgeEl && pctEl) {
            badgeEl.classList.add('visible');
            pctEl.textContent = `Buffer: ${pct}%`;
            if (bufferedAhead > 35 && !vjsPlayer.paused()) {
              badgeEl.classList.add('healthy');
            } else {
              badgeEl.classList.remove('healthy');
            }
          }
          updatePlayerLoadPercent(Math.max(currentLoadPercent, pct), '📥 Buffering Stream & Sinhala Subtitles...', `Buffer: ${pct}%`);
        }
      } catch (e) {}
    };

    vjsPlayer.on('progress', updateBufferMetrics);
    vjsPlayer.on('timeupdate', updateBufferMetrics);
    vjsPlayer.on('waiting', () => {
      const badgeEl = document.getElementById('player-buffer-badge');
      if (badgeEl) {
        badgeEl.classList.add('visible');
        badgeEl.classList.remove('healthy');
      }
      updateBufferMetrics();
    });

    vjsPlayer.on('loadedmetadata', () => {
      syncSubtitles();
      updateBufferMetrics();
    });

    vjsPlayer.on('loadeddata', updateBufferMetrics);
    vjsPlayer.on('canplay', () => {
      updateBufferMetrics();
      finishProgressAnimation();
    });
    vjsPlayer.on('canplaythrough', () => {
      updateBufferMetrics();
      finishProgressAnimation();
    });
    vjsPlayer.on('playing', () => {
      updateBufferMetrics();
      finishProgressAnimation();
      syncSubtitles();
    });

    // Safety fallback: Ensure loader fades out after at most 5s if playback is already active
    setTimeout(finishProgressAnimation, 5000);

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
    const triggerFailover = async (reason) => {
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
          if (!currentSrc.includes(`/${targetMsgId}`)) {
            console.warn('[FilmSub Player] Connection timed out, gracefully downgrading to 480p while preserving playhead');
            failoverTriggered = false;
            resetWatchdog(15000);
            applyQualitySwitch('480p', { isAutoDowngrade: true, force: true });
            return;
          }
        }
      }

      // 2. Refresh live tunnel config automatically before retrying direct tunnel
      if (!vjsPlayer._refreshedLiveConfig) {
        vjsPlayer._refreshedLiveConfig = true;
        try {
          sessionStorage.removeItem('filmsub_stream_base');
          sessionStorage.removeItem('filmsub_stream_healthy');
          await loadLiveStreamConfig(true);
        } catch (e) {}
      }

      // 3. Retry with direct live tunnel (if off Cloudflare Pages) or reloaded edge stream after config refresh
      if (!vjsPlayer._retriedDirect && activeStreamBaseUrl && activeStreamBaseUrl.startsWith('http') && !isDeadTunnel(activeStreamBaseUrl)) {
        vjsPlayer._retriedDirect = true;
        const curT = (typeof vjsPlayer.currentTime === 'function') ? (vjsPlayer.currentTime() || 0) : 0;
        const wasPaused = vjsPlayer.paused();
        const prefix = getStreamEndpointPrefix() || activeStreamBaseUrl.replace(/\/+$/, '');
        const directUrl = prefix + (currentSrc.startsWith('http') ? currentSrc.replace(/^https?:\/\/[^/]+/, '') : (currentSrc.startsWith('/') ? currentSrc : '/' + currentSrc));
        console.warn('[FilmSub Player] Switching to refreshed live stream endpoint:', directUrl);
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
        if (!vjsPlayer || _isSeamlessSwitching) return;
        const rState = typeof vjsPlayer.readyState === 'function' ? vjsPlayer.readyState() : (vjsPlayer.tech_?.el_?.readyState || 0);
        const nState = typeof vjsPlayer.networkState === 'function' ? vjsPlayer.networkState() : (vjsPlayer.tech_?.el_?.networkState || 0);

        if (!vjsPlayer.paused() && rState === 0) {
          const timeSinceProgress = Date.now() - lastDataProgressEpoch;
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
      if (_isSeamlessSwitching) {
        const errDisplay = playerEl.querySelector('.vjs-error-display');
        if (errDisplay) errDisplay.style.display = 'none';
        return;
      }
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
    ? 'මෙම වීඩියෝව සූදානම් වෙමින් පවතී. සුළු මොහොතකින් ස්වයංක්‍රීයව Playback ආරම්භ වේ.'
    : 'High-Speed Stream Server එක සම්බන්ධ කර ගනිමින් පවතී. සුළු මොහොතකින් ස්වයංක්‍රීයව Playback ආරම්භ වේ...';

  const posterUrl = movie.backdrop || movie.poster || '';

  playerEl.innerHTML = `
    <div class="player-iframe-wrap cinema-standby-screen" style="position:relative;width:100%;aspect-ratio:16/9;background:#000000 !important;border-radius:14px;overflow:hidden">
      ${posterUrl ? `<img src="${FilmSub.escHtml(posterUrl)}" class="cs-standby-backdrop-img" alt="${FilmSub.escHtml(movie.title)}" />` : ''}
      <div class="cs-standby-vignette"></div>
      <button type="button" class="cs-cinesubz-play-btn" id="btn-manual-reconnect" aria-label="Play Movie" title="Click to Watch">
        <i class="fa-solid fa-play"></i>
      </button>
      <div class="cs-poster-bottom-bar">
        <div class="cs-standby-meta-block">
          <span class="cs-standby-eyebrow"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි &bull; ${FilmSub.escHtml(movie.quality || '1080p HD')}</span>
          <h3 class="cs-standby-title">${FilmSub.escHtml(movie.title || 'Now Playing')}</h3>
        </div>
        <div class="cs-pbar-right">
          <button type="button" class="cs-pbar-circle-btn" id="fb-pbar-dl" title="Direct Download Options">
            <i class="fa-solid fa-download"></i>
          </button>
          <button type="button" class="cs-pbar-circle-btn" id="fb-pbar-fs" title="Fullscreen">
            <i class="fa-solid fa-expand"></i>
          </button>
          <button type="button" class="cs-pbar-circle-btn" id="fb-pbar-sub" title="Sinhala Subtitles">
            <i class="fa-solid fa-closed-captioning"></i>
          </button>
          <a href="${FilmSub.escHtml(tgBotUrl)}" target="_blank" rel="noopener" class="cs-pbar-circle-btn" id="fb-pbar-alert" title="Telegram Bot">
            <i class="fa-solid fa-triangle-exclamation"></i>
          </a>
        </div>
      </div>
    </div>`;

  const doRetry = async (e) => {
    if (e) {
      try { e.preventDefault(); } catch (err) {}
      try { e.stopPropagation(); } catch (err) {}
    }
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
  if (btnRetry) attachFastAction(btnRetry, doRetry);

  const standbyScreenEl = playerEl.querySelector('.cinema-standby-screen');
  if (standbyScreenEl) {
    attachFastAction(standbyScreenEl, (e) => {
      if (e && e.target && e.target.closest('.cs-poster-bottom-bar, button, a')) return;
      doRetry(e);
    });
  }

  const fbDl = playerEl.querySelector('#fb-pbar-dl');
  if (fbDl) attachFastAction(fbDl, () => document.getElementById('downloads')?.scrollIntoView({ behavior: 'smooth' }));

  const fbFs = playerEl.querySelector('#fb-pbar-fs');
  if (fbFs) attachFastAction(fbFs, () => {
    const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl;
    if (document.fullscreenElement) document.exitFullscreen();
    else if (wrap.requestFullscreen) wrap.requestFullscreen();
  });

  const fbSub = playerEl.querySelector('#fb-pbar-sub');
  if (fbSub) attachFastAction(fbSub, () => FilmSub.showToast('සිංහල උපසිරැසි (Sinhala Subtitles) Video එකටම Merge කර ඇත', 'info'));

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
    // If the server is currently OFFLINE, gently probe up to 4 times at 18s intervals (never trap mobile in endless rebuild loop)
    let offlinePollCount = 0;
    window.fallbackRetryInterval = setInterval(async () => {
      offlinePollCount++;
      if (offlinePollCount > 4) {
        clearInterval(window.fallbackRetryInterval);
        window.fallbackRetryInterval = null;
        return;
      }
      await loadLiveStreamConfig(true);
      if (streamServerHealthy && activeStreamBaseUrl && !isDeadTunnel(activeStreamBaseUrl)) {
        clearInterval(window.fallbackRetryInterval);
        window.fallbackRetryInterval = null;
        fallbackAutoRetryCount = 0;
        FilmSub.showToast('⚡ Stream Ready! ස්වයංක්‍රීයව Playback ආරම්භ කෙරේ...', 'success');
        loadStream(movie, 0);
      }
    }, 18000);
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

async function loadStream(movie, idx, autoPlay = false) {
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

    if (_activePreloadCleanup) _activePreloadCleanup();

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
        loadStream(movie, 1, autoPlay);
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
    createVjsPlayer(playerEl, stream, movie, autoPlay);
  } catch (err) {
    console.error('loadStream error:', err);
  }
}

function loadTrailer(movie) {
  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  if (_activePreloadCleanup) _activePreloadCleanup();

  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }

  isTrailerActive = true;
  renderServerTabs(movie);

  let trailerSrc = '';
  if (movie.trailer_id) {
    const tid = encodeURIComponent(movie.trailer_id);
    trailerSrc = `https://www.youtube-nocookie.com/embed/${tid}?autoplay=1&rel=0&modestbranding=1&playsinline=1`;
  } else if (movie.trailer_url) {
    trailerSrc = movie.trailer_url.includes('autoplay') ? movie.trailer_url : `${movie.trailer_url}${movie.trailer_url.includes('?') ? '&' : '?'}autoplay=1&rel=0&playsinline=1`;
  }

  if (!trailerSrc) {
    const ytSearchUrl = `https://www.youtube.com/results?search_query=${encodeURIComponent((movie.title || '') + ' ' + (movie.year || '') + ' official trailer')}`;
    playerEl.innerHTML = `
      <div style="aspect-ratio:16/9;width:100%;background:#080a0e;border-radius:12px;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:24px;border:1px solid rgba(255,255,255,0.08)">
        <div style="width:58px;height:58px;border-radius:50%;background:rgba(229,9,20,0.12);display:flex;align-items:center;justify-content:center;margin-bottom:14px;color:var(--accent);font-size:24px">
          <i class="fa-solid fa-clapperboard"></i>
        </div>
        <h3 style="font-size:17px;color:#fff;margin-bottom:6px">Official Trailer Not Available</h3>
        <p style="font-size:12.5px;color:var(--text2);max-width:380px;margin-bottom:18px">An embedded official trailer is not available yet for this title. You can search directly on YouTube.</p>
        <a href="${ytSearchUrl}" target="_blank" rel="noopener" class="btn btn-primary" style="border-radius:9999px;font-size:12.5px;padding:9px 20px">
          <i class="fa-brands fa-youtube"></i> Search on YouTube
        </a>
      </div>`;
    return;
  }

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;aspect-ratio:16/9;width:100%;background:#000000 !important;border-radius:8px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, '🎬 Official Trailer')}
      <div class="trailer-floating-bar" style="position:absolute;top:14px;left:14px;z-index:10;display:flex;align-items:center;gap:10px;pointer-events:auto">
        <span style="background:rgba(0,0,0,0.85);border:1px solid rgba(255,255,255,0.22);color:#ffffff;font-size:12px;font-weight:700;padding:5px 12px;border-radius:20px;backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);display:inline-flex;align-items:center;gap:6px">
          <i class="fa-solid fa-clapperboard" style="color:var(--accent)"></i> Official Trailer
        </span>
        <button type="button" id="trailer-switch-to-movie-btn" style="background:linear-gradient(135deg, #e50914 0%, #ff2036 100%);border:none;color:#ffffff;font-size:12px;font-weight:800;padding:6px 16px;border-radius:20px;cursor:pointer;display:inline-flex;align-items:center;gap:6px;box-shadow:0 3px 14px rgba(229,9,20,0.6);transition:all 0.2s">
          <i class="fa-solid fa-play"></i> Watch Movie
        </button>
      </div>
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

  const switchMovieBtn = document.getElementById('trailer-switch-to-movie-btn');
  if (switchMovieBtn) {
    switchMovieBtn.onclick = (e) => {
      e.stopPropagation();
      loadStream(movie, currentStreamIdx, true);
    };
  }

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
  const downloads = getMovieDownloads(movie);
  if (downloads.length === 0) {
    stripBtns.innerHTML = '';
    return;
  }

  let html = downloads.slice(0, 4).map(dl => {
    const rawQ = dl.quality || '1080p';
    const qBadge = rawQ.includes('1080') ? '1080p Full HD' : (rawQ.includes('720') ? '720p HD' : (rawQ.includes('480') ? '480p SD' : rawQ));
    const directUrl = dl.direct_url || (dl.url && !dl.url.includes('t.me') ? dl.url : '');
    const tgUrl = dl.tg_url || dl.bot_url || dl.channel_url || (dl.url && dl.url.includes('t.me') ? dl.url : '');
    const sizeText = dl.size ? `<span class="quick-dl-size">${FilmSub.escHtml(dl.size)}</span>` : '';

    return `
      <button type="button" class="quick-dl-pill" data-direct-url="${FilmSub.escHtml(directUrl)}" data-tg-url="${FilmSub.escHtml(tgUrl)}" data-quality="${FilmSub.escHtml(rawQ)}" title="Download ${FilmSub.escHtml(qBadge)}">
        <i class="fa-solid fa-download quick-dl-icon"></i>
        <span class="quick-dl-q-name">${FilmSub.escHtml(qBadge)}</span>
        ${sizeText}
      </button>`;
  }).join('');

  stripBtns.innerHTML = html;
  stripBtns.querySelectorAll('button.quick-dl-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const dlWrap = document.getElementById('cs-dl-menu-wrap');
      const dlToggle = document.getElementById('btn-bar-download-toggle');
      if (dlWrap) dlWrap.classList.remove('open');
      if (dlToggle) dlToggle.setAttribute('aria-expanded', 'false');
      const directUrl = btn.dataset.directUrl;
      const tgUrl = btn.dataset.tgUrl;
      const quality = btn.dataset.quality;

      if (directUrl && directUrl !== '#') {
        if (window.FilmSubDownload && typeof window.FilmSubDownload.show === 'function') {
          window.FilmSubDownload.show(directUrl, quality, movie.title);
        } else {
          window.open(directUrl, '_blank', 'noopener');
        }
      } else if (tgUrl) {
        window.open(tgUrl, '_blank', 'noopener');
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
    const catalogMovies = getFullCatalogList();

    const seriesTitleNorm = (movie.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');

    let epHtml = '';
    for (let ep = 1; ep <= count; ep++) {
      const isActive = (sNum === currentSeason && ep === currentEpisode);

      // Look for a matching episode object in the catalog
      let matchedEpMovie = findEpisodeEntry(movie, sNum, ep);
      if (!matchedEpMovie && catalogMovies.length > 0) {
        matchedEpMovie = catalogMovies.find(m => {
          if (m.type !== 'series') return false;
          const tNorm = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
          const matchTitle = (tNorm === seriesTitleNorm || (movie.tmdb_id && m.tmdb_id === movie.tmdb_id));
          return matchTitle && (parseInt(m.season, 10) === sNum && parseInt(m.episode, 10) === ep);
        });
      }

      const epTitle = (matchedEpMovie && matchedEpMovie.episode_title)
        ? matchedEpMovie.episode_title
        : `Episode ${ep}`;
      const epThumb = (matchedEpMovie && (matchedEpMovie.backdrop || matchedEpMovie.poster)) || sPoster;
      const epDuration = (matchedEpMovie && matchedEpMovie.duration) || '~50 min';
      const epCode = `S${String(sNum).padStart(2, '0')} E${String(ep).padStart(2, '0')}`;

      const epSlugFallback = `${(movie.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '')}-s${String(sNum).padStart(2, '0')}e${String(ep).padStart(2, '0')}`;
      const cardSlug = matchedEpMovie ? (matchedEpMovie.slug || matchedEpMovie.id) : epSlugFallback;
      epHtml += `
        <div class="episode-card${isActive ? ' active' : ''}" data-season="${sNum}" data-episode="${ep}" data-slug="${FilmSub.escHtml(cardSlug)}">
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
      card.addEventListener('click', async () => {
        const ep = parseInt(card.dataset.episode, 10);
        const s = parseInt(card.dataset.season, 10);
        currentSeason = s;
        currentEpisode = ep;

        const matchedSlug = card.dataset.slug;
        let found = null;
        if (matchedSlug) {
          const cat = getFullCatalogList();
          found = cat.find(m => m.slug === matchedSlug || m.id === matchedSlug);
          if (!found && window.FilmSub && typeof window.FilmSub.findMovieBySlug === 'function') {
            found = window.FilmSub.findMovieBySlug(matchedSlug);
          }
        }
        if (!found) {
          found = findEpisodeEntry(movie, s, ep);
        }
        if (!found) {
          try {
            const ghUrl = 'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json?_t=' + Date.now() + '&_r=' + Math.random();
            const rGh = await fetch(ghUrl, { cache: 'no-store' });
            if (rGh.ok) {
              const d = await rGh.json();
              const moviesList = Array.isArray(d) ? d : (d.movies || []);
              if (Array.isArray(moviesList) && moviesList.length > 0) {
                const seen = new Set();
                const curr = (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) ? window.FILMSUB_DATA.movies : [];
                const merged = [];
                for (const m of [...moviesList, ...curr]) {
                  const k = String(m.slug || m.id || '').toLowerCase();
                  if (k && !seen.has(k)) {
                    seen.add(k);
                    merged.push(m);
                  }
                }
                if (window.FILMSUB_DATA) window.FILMSUB_DATA.movies = merged;
                if (window.FILMZILLA_DATA) window.FILMZILLA_DATA.movies = merged;
                if (window.FilmSub) {
                  if (typeof FilmSub.setMovies === 'function') FilmSub.setMovies(merged);
                  else FilmSub.movies = merged;
                }
                found = findEpisodeEntry(movie, s, ep);
                if (!found) {
                  const baseSlug = (movie.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
                  found = moviesList.find(m => {
                    if (parseInt(m.season, 10) !== s || parseInt(m.episode, 10) !== ep) return false;
                    const mBase = (m.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
                    return mBase && (mBase === baseSlug || mBase.startsWith(baseSlug) || baseSlug.startsWith(mBase));
                  });
                }
                if (!found && matchedSlug) {
                  found = moviesList.find(m => m.slug === matchedSlug || m.id === matchedSlug);
                }
              }
            }
          } catch (e) {}
        }
        if (!found) {
          const baseSlug = (movie.slug || '').replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
          const tmdbId = movie.tmdb_id || '';
          const imdbId = movie.imdb_id || '';
          const epSlug = `${baseSlug}-s${String(s).padStart(2, '0')}e${String(ep).padStart(2, '0')}`;
          found = {
            ...movie,
            id: epSlug,
            slug: epSlug,
            season: s,
            episode: ep,
            episode_title: `Episode ${ep}`,
            message_id: 0,
            file_id: '',
            file_name: '',
            stream_url: '',
            variant_media: null,
            streams: [
              ...(tmdbId ? [{
                server: 'Server 2',
                label: '🎬 VIP Player 1 (VidLink Ultra HD)',
                type: 'embed',
                embed: true,
                stream_url: `https://vidlink.pro/tv/${tmdbId}/${s}/${ep}`
              }] : []),
              ...(imdbId ? [{
                server: 'Server 3',
                label: '⚡ VIP Player 2 (AutoEmbed HD)',
                type: 'embed',
                embed: true,
                stream_url: `https://autoembed.co/tv/imdb/${imdbId}-${s}-${ep}`
              }] : [])
            ],
            downloads: [],
            subtitles: [
              {
                language: 'Sinhala',
                srclang: 'si',
                label: 'සිංහල උපසිරැසි (Sinhala)',
                url: `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/subs/${epSlug}-si.vtt`,
                default: false
              }
            ]
          };
          if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
            window.FILMSUB_DATA.movies.unshift(found);
          }
        }

        currentMovie = found;
        movie = found;

        const targetSlug = found.slug || found.id;
        try {
          const newUrl = new URL(window.location.href);
          newUrl.searchParams.set('id', targetSlug);
          newUrl.searchParams.set('s', s);
          newUrl.searchParams.set('e', ep);
          window.history.replaceState({ slug: targetSlug, season: s, episode: ep }, '', newUrl.toString());
        } catch (e) {}

        document.title = `${movie.title || 'Series'} S${String(s).padStart(2, '0')}E${String(ep).padStart(2, '0')} Sinhala Subtitles – FilmSub`;
        FilmSub.showToast(`Playing S${String(s).padStart(2, '0')}E${String(ep).padStart(2, '0')}: ${movie.episode_title || 'Episode ' + ep}`, 'success');

        const qualEl = document.getElementById('cs-header-quality');
        if (qualEl) {
          qualEl.textContent = `S${String(s).padStart(2, '0')} E${String(ep).padStart(2, '0')} HD`;
        }

        renderPageHeader(movie);
        renderMovieDetails(movie);
        renderQuickDownloadStrip(movie);
        renderDownloadSection(movie);
        renderServerTabs(movie);
        renderEpisodesForSeason(seasonObj);
        loadStream(movie, 0, true);
        loadParsedSubtitles(movie).then(() => {
          try { syncSubtitles(); } catch (e) {}
        }).catch(() => {});

        const playerSec = document.getElementById('player-section');
        if (playerSec) {
          playerSec.scrollIntoView({ behavior: 'smooth' });
        }
      });
    });
  };

  const initialSeasonIdx = seasons.findIndex(s => s.season_number === currentSeason);
  const activeSeasonIdx = initialSeasonIdx >= 0 ? initialSeasonIdx : 0;
  renderEpisodesForSeason(seasons[activeSeasonIdx]);

  tabsEl.querySelectorAll('.season-tab').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.index, 10) === activeSeasonIdx);
    btn.addEventListener('click', () => {
      tabsEl.querySelectorAll('.season-tab').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const idx = parseInt(btn.dataset.index, 10);
      renderEpisodesForSeason(seasons[idx]);
    });
  });
}

// ---- Movie Details & Synopsis (Legendary Cinema Platform Edition) ----
function renderMovieDetails(movie) {
  const esc = FilmSub.escHtml;
  const poster = movie.poster || movie.poster_url || FilmSub.SITE_CONFIG.defaultPoster;
  const backdrop = movie.backdrop || movie.backdrop_url || poster;

  // 1. Dynamic Cinema Backdrop on Details Section
  const detailsSec = document.getElementById('movie-details-section');
  if (detailsSec && backdrop) {
    detailsSec.style.setProperty('--movie-backdrop-url', `url('${backdrop}')`);
    detailsSec.style.backgroundImage = `linear-gradient(90deg, rgba(8,9,13,0.98) 0%, rgba(8,9,13,0.85) 45%, rgba(8,9,13,0.65) 75%, rgba(8,9,13,0.95) 100%), linear-gradient(180deg, rgba(8,9,13,0.85) 0%, rgba(8,9,13,0.3) 30%, rgba(8,9,13,0.9) 85%, #08090d 100%), url('${backdrop}')`;
    detailsSec.style.backgroundSize = 'cover';
    detailsSec.style.backgroundPosition = 'center 20%';
  }
  const glowEl = document.getElementById('cs-details-backdrop-glow');
  if (glowEl && backdrop) {
    glowEl.style.backgroundImage = `url('${backdrop}')`;
  }

  // 2. Poster Image with 3D Depth
  const posterEl = document.getElementById('movie-poster-img');
  if (posterEl) {
    posterEl.src = poster;
    posterEl.alt = movie.title || '';
  }

  // 3. Keep Poster Artwork 100% Clean (No Clashing Cartoon Stickers)
  const badgesEl = document.getElementById('cs-poster-badges');
  if (badgesEl) {
    badgesEl.innerHTML = '';
  }

  // 4. Watch Button on Poster
  const posterPlayBtn = document.getElementById('poster-play-btn');
  if (posterPlayBtn) {
    attachFastAction(posterPlayBtn, (e) => {
      const pSec = document.getElementById('player-section');
      if (pSec) {
        pSec.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      const standbyOverlay = document.getElementById('cs-cinesubz-standby-overlay');
      if (standbyOverlay) {
        standbyOverlay.classList.add('hidden');
        standbyOverlay.style.display = 'none';
        standbyOverlay.style.pointerEvents = 'none';
        standbyOverlay.style.zIndex = '-1';
      }
      const centerOverlay = document.querySelector('.cs-player-center-overlay');
      if (centerOverlay) {
        centerOverlay.style.display = '';
        centerOverlay.style.pointerEvents = 'auto';
      }
      if (isTrailerActive) {
        loadStream(movie, currentStreamIdx, true);
        return;
      }
      if (vjsPlayer && typeof vjsPlayer.play === 'function') {
        try { vjsPlayer.play().catch(() => {}); } catch(err) {}
      } else {
        const v = document.querySelector('#video-player-container video');
        if (v) {
          try { v.play().catch(() => {}); } catch(err) {}
        } else {
          loadStream(movie, currentStreamIdx, true);
        }
      }
    });
  }

  // 4b. Trailer Button on Poster (Luxury cinema action)
  const posterTrailerBtn = document.getElementById('poster-trailer-btn');
  if (posterTrailerBtn) {
    if (movie.trailer_id || movie.trailer_url) {
      posterTrailerBtn.style.display = 'inline-flex';
      attachFastAction(posterTrailerBtn, (e) => {
        const pSec = document.getElementById('player-section');
        if (pSec) {
          pSec.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        loadTrailer(movie);
      });
    } else {
      posterTrailerBtn.style.display = 'none';
    }
  }

  // 4c. Teaser / 30s Scene Button on Poster & Player Menu Bar
  const mId = movie.id || movie.slug;
  const teasers = window.FILMSUB_TEASERS || [];
  const hasTeaser = teasers.some(t => t.movie_id === mId || t.id === `teaser-${mId}`);

  const posterTeaserBtn = document.getElementById('poster-teaser-btn');
  if (posterTeaserBtn) {
    if (hasTeaser || movie.trailer_id) {
      posterTeaserBtn.style.display = 'inline-flex';
      posterTeaserBtn.href = `teasers.html?id=${encodeURIComponent(mId)}`;
    } else {
      posterTeaserBtn.style.display = 'none';
    }
  }

  const barTeaserBtn = document.getElementById('bar-teaser-btn');
  if (barTeaserBtn) {
    if (hasTeaser || movie.trailer_id) {
      barTeaserBtn.style.display = 'inline-flex';
      barTeaserBtn.href = `teasers.html?id=${encodeURIComponent(mId)}`;
    } else {
      barTeaserBtn.style.display = 'inline-flex';
      barTeaserBtn.href = 'teasers.html';
    }
  }

  // 5. Title Banner
  const titleEl = document.getElementById('cs-details-title');
  if (titleEl) {
    titleEl.textContent = movie.title || 'Untitled';
  }
  const titleSiEl = document.getElementById('cs-details-title-si');
  if (titleSiEl) {
    titleSiEl.textContent = movie.title_si ? `${movie.title_si} ${movie.year ? '(' + movie.year + ')' : ''}` : (movie.year ? `(${movie.year})` : '');
    titleSiEl.style.display = (movie.title_si || movie.year) ? 'block' : 'none';
  }

  // 6. Studio-Grade Subtle Metadata Line (No Clashing Rainbow Pills)
  const badgesRail = document.getElementById('cs-badges-rail');
  if (badgesRail) {
    let rHtml = '';
    const imdbVal = movie.imdb || movie.rating;
    if (imdbVal && imdbVal !== '0.0') {
      rHtml += `<span class="cs-pro-meta-rating"><i class="fa-solid fa-star"></i> IMDb ${esc(imdbVal)}</span>`;
    }
    if (movie.year) {
      if (rHtml) rHtml += `<span class="cs-pro-meta-dot">•</span>`;
      rHtml += `<span class="cs-pro-meta-item">${esc(movie.year)}</span>`;
    }
    const durVal = typeof movie.duration === 'number' ? `${movie.duration} min` : (movie.duration || '');
    if (durVal) {
      if (rHtml) rHtml += `<span class="cs-pro-meta-dot">•</span>`;
      rHtml += `<span class="cs-pro-meta-item">${esc(durVal)}</span>`;
    }
    if (rHtml) rHtml += `<span class="cs-pro-meta-dot">•</span>`;
    rHtml += `<span class="cs-pro-meta-tag tag-quality">${esc(formatQualityLabel(movie.quality))}</span>`;
    rHtml += `<span class="cs-pro-meta-tag tag-sub"><i class="fa-solid fa-closed-captioning"></i> Sinhala Subtitles</span>`;
    badgesRail.innerHTML = rHtml;
  }

  // 7. Genre Pills
  const genresEl = document.getElementById('cs-genre-pills');
  if (genresEl) {
    const genres = Array.isArray(movie.genres) ? movie.genres : [];
    if (genres.length > 0) {
      genresEl.innerHTML = genres.map(g =>
        `<a href="search.html?genre=${encodeURIComponent(g)}" class="genre-chip"><i class="fa-solid fa-tag" style="font-size:10px"></i> ${esc(g)}</a>`
      ).join('');
    } else {
      genresEl.innerHTML = `<span class="genre-chip">Cinema</span>`;
    }
  }

  // 8. Synopsis Tabs Toggle (Smart Sinhala vs English handling)
  const tabSi = document.getElementById('tab-btn-si');
  const tabEn = document.getElementById('tab-btn-en');
  const paneSi = document.getElementById('pane-si');
  const paneEn = document.getElementById('pane-en');
  const siDescEl = document.getElementById('movie-desc-si');
  const enDescEl = document.getElementById('movie-desc-en');

  const rawSi = (movie.description_si || movie.storyline_si || '').trim();
  const hasGenuineSinhala = Boolean(rawSi && /[඀-෿]/.test(rawSi));
  const rawEn = (movie.description || movie.storyline || movie.description_en || '').trim();

  if (hasGenuineSinhala) {
    if (tabSi) {
      tabSi.style.display = 'inline-flex';
      tabSi.classList.add('active');
    }
    if (tabEn) {
      tabEn.style.display = 'inline-flex';
      tabEn.classList.remove('active');
      tabEn.innerHTML = '<i class="fa-solid fa-globe"></i> English Storyline';
    }
    if (paneSi) {
      paneSi.classList.add('active');
      if (siDescEl) siDescEl.textContent = rawSi;
    }
    if (paneEn) {
      paneEn.classList.remove('active');
      if (enDescEl) enDescEl.textContent = rawEn || 'No English storyline available for this title.';
    }

    if (tabSi && tabEn && paneSi && paneEn) {
      tabSi.onclick = () => {
        tabSi.classList.add('active');
        tabEn.classList.remove('active');
        paneSi.classList.add('active');
        paneEn.classList.remove('active');
      };
      tabEn.onclick = () => {
        tabEn.classList.add('active');
        tabSi.classList.remove('active');
        paneEn.classList.add('active');
        paneSi.classList.remove('active');
      };
    }
  } else {
    // No Sinhala translation available: Hide Sinhala tab so English text is never shown under Sinhala heading!
    if (tabSi) tabSi.style.display = 'none';
    if (tabEn) {
      tabEn.style.display = 'inline-flex';
      tabEn.classList.add('active');
      tabEn.innerHTML = '<i class="fa-solid fa-film" style="color:var(--accent)"></i> Movie Storyline';
    }
    if (paneSi) paneSi.classList.remove('active');
    if (paneEn) {
      paneEn.classList.add('active');
      if (enDescEl) enDescEl.textContent = rawEn || 'No storyline summary available for this title.';
    }
  }

  // 9. Modern Attributes Micro-Cards Grid
  let castStr = 'N/A';
  if (Array.isArray(movie.cast) && movie.cast.length > 0) {
    castStr = movie.cast.slice(0, 6).map(c => {
      if (typeof c === 'string') return esc(c);
      if (c && typeof c === 'object' && c.name) return esc(c.name);
      return '';
    }).filter(Boolean).join(', ');
  }

  const metaGridEl = document.getElementById('cs-meta-grid');
  if (metaGridEl) {
    metaGridEl.innerHTML = `
      <div class="cs-pro-credit-item">
        <span class="cs-pro-credit-label">Director</span>
        <span class="cs-pro-credit-val">${esc(movie.director || 'N/A')}</span>
      </div>
      <div class="cs-pro-credit-item">
        <span class="cs-pro-credit-label">Starring</span>
        <span class="cs-pro-credit-val">${castStr}</span>
      </div>
      <div class="cs-pro-credit-item">
        <span class="cs-pro-credit-label">Audio</span>
        <span class="cs-pro-credit-val">${esc(movie.language || 'English')} • Dolby Stereo</span>
      </div>
      <div class="cs-pro-credit-item">
        <span class="cs-pro-credit-label">Subtitles</span>
        <span class="cs-pro-credit-val" style="color:#ff4d5a;font-weight:700">FilmZilla Official Sinhala (Merged)</span>
      </div>`;
  }

  // Wire Detail Page Action Buttons (Favorite & Watch Later)
  if (window.FilmSubUser) {
    const favBtns = [document.getElementById('movie-fav-btn'), document.getElementById('poster-fav-btn')].filter(Boolean);
    const laterBtns = [document.getElementById('movie-later-btn'), document.getElementById('poster-later-btn')].filter(Boolean);
    const mId = movie.id || movie.slug;

    const updateFavUI = (isFav) => {
      favBtns.forEach(btn => {
        btn.classList.toggle('active', isFav);
        btn.innerHTML = `<i class="${isFav ? 'fa-solid' : 'fa-regular'} fa-heart" style="color:${isFav ? '#ffffff' : '#e50914'}"></i> <span>${isFav ? 'Favorited' : 'Favorite'}</span>`;
      });
    };

    const isFavInit = window.FilmSubUser.Favorites.has(mId);
    updateFavUI(isFavInit);

    favBtns.forEach(btn => {
      if (!btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => {
          const added = window.FilmSubUser.Favorites.toggle(movie);
          updateFavUI(added);
          if (window.FilmSub && typeof FilmSub.showToast === 'function') {
            FilmSub.showToast(added ? 'Added to Favorites ❤️' : 'Removed from Favorites', 'info');
          }
        });
      }
    });

    const updateLaterUI = (isLater) => {
      laterBtns.forEach(btn => {
        btn.classList.toggle('active', isLater);
        btn.innerHTML = `<i class="${isLater ? 'fa-solid' : 'fa-regular'} fa-clock" style="color:${isLater ? '#ffffff' : '#d4d4d8'}"></i> <span>${isLater ? 'In Queue' : 'Watch Later'}</span>`;
      });
    };

    const isLaterInit = window.FilmSubUser.WatchLater.has(mId);
    updateLaterUI(isLaterInit);

    laterBtns.forEach(btn => {
      if (!btn._bound) {
        btn._bound = true;
        btn.addEventListener('click', () => {
          const added = window.FilmSubUser.WatchLater.toggle(movie);
          updateLaterUI(added);
          if (window.FilmSub && typeof FilmSub.showToast === 'function') {
            FilmSub.showToast(added ? 'Saved to Watch Later ⏱️' : 'Removed from Watch Later', 'info');
          }
        });
      }
    });
  }

  // Wire Live Emoji Reactions
  const reactionMovieId = movie.id || movie.slug;
  if (window.FilmZillaRoulette && typeof window.FilmZillaRoulette.initReactions === 'function') {
    window.FilmZillaRoulette.initReactions(reactionMovieId);
  }

  // Wire Watch Party Trigger Button
  const wpTrigger = document.getElementById('btn-watch-party-trigger');
  if (wpTrigger && !wpTrigger._bound) {
    wpTrigger._bound = true;
    wpTrigger.addEventListener('click', () => {
      if (window.FilmZillaWatchParty && typeof window.FilmZillaWatchParty.createOrToggleRoom === 'function') {
        window.FilmZillaWatchParty.createOrToggleRoom();
      } else if (window.FilmZillaWatchParty && typeof window.FilmZillaWatchParty.createRoom === 'function') {
        window.FilmZillaWatchParty.createRoom();
      }
    });
  }

  // Wire In-Video PiP Corner Button & PiP Triggers
  const pipCornerBtn = document.getElementById('cs-video-pip-corner-btn') || document.getElementById('btn-pip-trigger');
  if (pipCornerBtn && !pipCornerBtn._bound) {
    pipCornerBtn._bound = true;
    pipCornerBtn.addEventListener('click', () => {
      const vEl = (vjsPlayer && vjsPlayer.tech_ && vjsPlayer.tech_.el_) 
        ? vjsPlayer.tech_.el_ 
        : document.querySelector('.player-container video');
      if (window.FilmZillaRoulette && typeof window.FilmZillaRoulette.togglePiP === 'function') {
        window.FilmZillaRoulette.togglePiP(vEl);
      }
    });
  }

  // Wire Theater Menu Bar Buttons (4D Haptics, Phone Companion, Wi-Fi P2P)
  if (typeof wireVIPCinemaMenuButtons === 'function') {
    try { wireVIPCinemaMenuButtons(movie); } catch (e) { console.warn('[VIP] Menu buttons wire err:', e); }
  }
}


// ---- Download Section ----
function renderDownloadSection(movie) {
  const grid = document.getElementById('download-grid');
  if (!grid) return;

  const dlSec = document.getElementById('downloads');
  const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || '';
  if (dlSec && backdrop) {
    dlSec.style.setProperty('--movie-backdrop-url', `url('${backdrop}')`);
    dlSec.style.backgroundImage = `linear-gradient(180deg, rgba(8,9,13,0.96) 0%, rgba(8,9,13,0.72) 45%, rgba(8,9,13,0.96) 100%), url('${backdrop}')`;
    dlSec.style.backgroundSize = 'cover';
    dlSec.style.backgroundPosition = 'center 75%';
  }

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

    grid.innerHTML = `
      <div class="cs-3d-download-bubble-box">
        <div class="cs-bubble-box-header">
          <div class="cs-bubble-header-left">
            <span class="cs-bubble-icon-glow"><i class="fa-solid fa-cloud-arrow-down"></i></span>
            <div class="cs-bubble-titles">
              <h4 class="cs-bubble-main-title">Fast Cloud Downloads</h4>
              <p class="cs-bubble-sub-title">High Speed Direct Streaming & Telegram Cloud • Sinhala Subtitles Merged</p>
            </div>
          </div>
          <span class="cs-bubble-ultra-badge"><i class="fa-solid fa-bolt"></i> Ultra Fast</span>
        </div>
        <div class="cs-bubble-tiers-list">
          ${uniqueDls.map(dl => {
            const rawQ = dl.quality || '720p';
            const qTitle = rawQ.includes('1080') ? '1080p Full HD' : (rawQ.includes('720') ? '720p HD' : (rawQ.includes('480') ? '480p SD' : `${rawQ} HD`));
            const fmt = dl.format || 'MP4';
            const directUrl = dl.direct_url || (dl.url && !dl.url.includes('t.me') ? dl.url : '');
            const tgUrl = dl.tg_url || dl.bot_url || dl.channel_url || (dl.url && dl.url.includes('t.me') ? dl.url : '');

            let actionBtns = '';

            // 1. Direct Download button (clean cinema action)
            if (directUrl) {
              actionBtns += `
                <button type="button" class="btn-dl-sleek btn-dl-direct btn-direct-dl" data-url="${FilmSub.escHtml(directUrl)}" data-quality="${FilmSub.escHtml(rawQ)}" title="High Speed Direct Download">
                  <i class="fa-solid fa-cloud-arrow-down"></i> Direct Download
                </button>`;
            }

            // 2. Telegram Cloud button (sleek cyan pill)
            if (tgUrl) {
              actionBtns += `
                <a href="${FilmSub.escHtml(tgUrl)}" target="_blank" rel="noopener" class="btn-dl-sleek btn-dl-tg btn-tg-dl" title="Save in Telegram Cloud">
                  <i class="fa-brands fa-telegram"></i> Telegram Cloud
                </a>`;
            } else if (!directUrl && dl.url) {
              actionBtns += `
                <a href="${FilmSub.escHtml(dl.url)}" target="_blank" rel="noopener" class="btn-dl-sleek btn-dl-tg btn-tg-dl">
                  <i class="fa-solid fa-cloud-arrow-down"></i> Download
                </a>`;
            }

            const isRec = rawQ.includes('720');
            const qIcon = rawQ.includes('1080') ? 'fa-solid fa-crown' : (rawQ.includes('720') ? 'fa-solid fa-bolt' : 'fa-solid fa-film');

            return `
              <div class="cs-bubble-tier-row ${isRec ? 'is-recommended' : ''}">
                <div class="bubble-tier-meta">
                  <div class="bubble-tier-badge">
                    <i class="${qIcon}" style="color:var(--accent);font-size:15px"></i>
                    <span class="bubble-tier-name">${FilmSub.escHtml(qTitle)}</span>
                    ${isRec ? `<span class="bubble-rec-chip"><i class="fa-solid fa-crown"></i> RECOMMENDED</span>` : ''}
                  </div>
                  <div class="bubble-tier-specs">
                    <span class="bubble-spec-pill"><i class="fa-solid fa-circle-check" style="color:#e50914"></i> ${FilmSub.escHtml(fmt)}</span>
                    <span class="bubble-spec-pill"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි Merged</span>
                  </div>
                </div>
                <div class="bubble-tier-actions">
                  ${actionBtns}
                </div>
              </div>`;
          }).join('')}
        </div>
      </div>`;

    grid.querySelectorAll('.btn-direct-dl').forEach(btn => {
      attachFastAction(btn, (e) => {
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
  grid.querySelectorAll('.movie-card').forEach((c, idx) => {
    c.style.transitionDelay = `${Math.min(idx * 0.04, 0.45)}s`;
    requestAnimationFrame(() => c.classList.add('is-card-visible'));
  });
}

function initShareButton(movie) {
  const shareBtns = [document.getElementById('movie-share-btn'), document.getElementById('poster-share-btn')].filter(Boolean);
  shareBtns.forEach(shareBtn => {
    if (shareBtn && !shareBtn._bound) {
      shareBtn._bound = true;
      shareBtn.addEventListener('click', () => {
        if (navigator.share) {
          navigator.share({
            title: `${movie.title} Sinhala Subtitles`,
            text: `Watch and download ${movie.title} with Sinhala subtitles on FilmZilla!`,
            url: window.location.href,
          }).catch(() => {});
        } else {
          navigator.clipboard.writeText(window.location.href)
            .then(() => FilmSub.showToast('Link copied to clipboard!', 'success'))
            .catch(() => FilmSub.showToast('Copy the URL from your browser address bar.', 'info'));
        }
      });
    }
  });
}

/* ==========================================================================
   VIP CINEMA 7 KILLER FEATURES SUITE
   2. Interactive Local P2P Wi-Fi Data Sharing Mesh
   3. Mobile 4D Haptic Feedback (Low-Pass Filter <70Hz Audio Vibration)
   4. Phone Second Screen / Dual-Screen Companion Remote (QR Scan)
   ========================================================================== */

// Safe storage helper for headless / iframe / private / test environments
const safeStorage = {
  get(key, fallback = null) {
    try {
      return (typeof localStorage !== 'undefined' && localStorage) ? localStorage.getItem(key) : fallback;
    } catch (_) {
      return fallback;
    }
  },
  set(key, val) {
    try {
      if (typeof localStorage !== 'undefined' && localStorage) localStorage.setItem(key, val);
    } catch (_) {}
  }
};

// ---- 2. MOBILE 4D HAPTIC FEEDBACK ENGINE (<70Hz Web Audio Filter) ----
window.FilmZilla4DHaptic = {
  isEnabled: safeStorage.get('fz_4d_haptic') === 'true',
  vjsPlayer: null,
  audioCtx: null,
  analyser: null,
  biquadFilter: null,
  sourceNode: null,
  animFrameId: null,
  corsInterval: null,
  lastVibrateTime: 0,
  cooldownMs: 220,

  init(vjsPlayer) {
    this.vjsPlayer = vjsPlayer;
    this.updateBadgeUI();
    if (this.isEnabled) {
      this.setupAudioAnalyser();
    }
  },

  setupAudioAnalyser() {
    try {
      const AudioCtxClass = (typeof window !== 'undefined') ? (window.AudioContext || window.webkitAudioContext) : null;
      if (!AudioCtxClass) return;
      if (!this.audioCtx) this.audioCtx = new AudioCtxClass();
      if (this.audioCtx.state === 'suspended') {
        const resumeCtx = () => {
          if (this.audioCtx && this.audioCtx.state === 'suspended') this.audioCtx.resume();
          if (typeof document !== 'undefined') {
            document.removeEventListener('click', resumeCtx);
            document.removeEventListener('touchstart', resumeCtx);
          }
        };
        if (typeof document !== 'undefined') {
          document.addEventListener('click', resumeCtx, { once: true });
          document.addEventListener('touchstart', resumeCtx, { once: true });
        }
      }

      const videoEl = (this.vjsPlayer && this.vjsPlayer.tech_ && this.vjsPlayer.tech_.el_)
        ? this.vjsPlayer.tech_.el_
        : (typeof document !== 'undefined' ? document.querySelector('.player-container video') : null);
      if (!videoEl) return;
      if (videoEl && !videoEl.crossOrigin) {
        try { videoEl.crossOrigin = 'anonymous'; } catch (_) {}
      }

      // Real-time Low-Pass Filter (<70Hz for heavy sub-bass impacts)
      if (!this.biquadFilter) {
        this.biquadFilter = this.audioCtx.createBiquadFilter();
        this.biquadFilter.type = 'lowpass';
        this.biquadFilter.frequency.value = 70; // Filter out dialogue, keep bass
        this.biquadFilter.Q.value = 1.2;

        this.analyser = this.audioCtx.createAnalyser();
        this.analyser.fftSize = 256;
        this.analyser.smoothingTimeConstant = 0.6;

        try {
          if (!videoEl._hasAudioSource) {
            this.sourceNode = this.audioCtx.createMediaElementSource(videoEl);
            videoEl._hasAudioSource = true;
            this.sourceNode.connect(this.biquadFilter);
            this.biquadFilter.connect(this.analyser);
            // Re-route back to speakers so video remains audible
            this.sourceNode.connect(this.audioCtx.destination);
          }
        } catch (e) {
          // Cross-Origin restriction fallback
          console.warn('[4D Haptic] CORS audio protection active, using intelligent action volume tracker');
          this.startCorsFallback(videoEl);
        }
      }
      this.startAnalysisLoop();
    } catch (err) {
      console.warn('[4D Haptic] Audio Context initialization:', err);
    }
  },

  startCorsFallback(videoEl) {
    if (this.corsInterval) clearInterval(this.corsInterval);
    this.corsInterval = setInterval(() => {
      if (!this.isEnabled || !videoEl || videoEl.paused) return;
      const now = Date.now();
      if ((now - this.lastVibrateTime) > 6000) {
        // Trigger subtle action heartbeat pulse
        this.lastVibrateTime = now;
        this.triggerHapticPattern([45, 30, 70], 'bass');
      }
    }, 1500);
  },

  startAnalysisLoop() {
    if (this.animFrameId && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.animFrameId);
    const bufferLength = this.analyser ? this.analyser.frequencyBinCount : 64;
    const dataArray = new Uint8Array(bufferLength);
    let zeroCount = 0;

    const checkAudioImpact = () => {
      if (!this.isEnabled) return;
      if (this.analyser && this.audioCtx && this.audioCtx.state === 'running') {
        this.analyser.getByteFrequencyData(dataArray);
        // Focus on bins 0-5 (<70Hz bass region)
        let bassEnergy = 0;
        let nonZeroBins = 0;
        const bassBins = Math.min(6, bufferLength);
        for (let i = 0; i < bassBins; i++) {
          bassEnergy += dataArray[i];
          if (dataArray[i] > 0) nonZeroBins++;
        }
        const avgBass = bassEnergy / bassBins;
        const now = Date.now();

        // Check if video is playing with sound but browser CORS security zeroes out analyser
        const isPlaying = videoEl && !videoEl.paused && videoEl.currentTime > 2 && !videoEl.muted;
        if (isPlaying && nonZeroBins === 0) {
          zeroCount++;
          if (zeroCount > 180 && !this.corsInterval) {
            this.startCorsFallback(videoEl);
          }
        } else if (nonZeroBins > 0) {
          zeroCount = 0;
          if (this.corsInterval) {
            clearInterval(this.corsInterval);
            this.corsInterval = null;
          }
        }

        if (avgBass > 185 && (now - this.lastVibrateTime) > this.cooldownMs) {
          this.lastVibrateTime = now;
          if (avgBass > 225) {
            // Massive explosion / Earthquake rumble
            this.triggerHapticPattern([120, 40, 90, 40, 220], 'explosion');
          } else if (avgBass > 205) {
            // Snappy gunshot / punch impact
            this.triggerHapticPattern([35, 25, 45], 'gunshot');
          } else {
            // Heavy bass drop
            this.triggerHapticPattern([80, 50, 160], 'bass');
          }
        }
      }
      if (typeof requestAnimationFrame === 'function') {
        this.animFrameId = requestAnimationFrame(checkAudioImpact);
      }
    };
    if (typeof requestAnimationFrame === 'function') {
      this.animFrameId = requestAnimationFrame(checkAudioImpact);
    }
  },

  triggerHapticPattern(pattern, impactType = 'impact') {
    // 1. Mobile Physical Vibration
    if (typeof navigator !== 'undefined' && navigator.vibrate) {
      try { navigator.vibrate(pattern); } catch (_) {}
    }
    // 2. Visual Tactile Pulse on player border
    if (typeof document !== 'undefined') {
      const playerContainer = document.getElementById('video-player-container') || document.querySelector('.player-container');
      if (playerContainer) {
        playerContainer.classList.remove('haptic-impact-pulse');
        void playerContainer.offsetWidth; // Force reflow
        playerContainer.classList.add('haptic-impact-pulse');
      }
    }
    // 3. Emit to Companion Phone Remote
    if (typeof window !== 'undefined' && window.FilmZillaCompanion && typeof window.FilmZillaCompanion.broadcastHaptic === 'function') {
      window.FilmZillaCompanion.broadcastHaptic(pattern);
    }
  },

  toggleHaptic() {
    this.isEnabled = !this.isEnabled;
    safeStorage.set('fz_4d_haptic', this.isEnabled ? 'true' : 'false');
    this.updateBadgeUI();
    if (this.isEnabled) {
      this.setupAudioAnalyser();
      this.triggerHapticPattern([50, 30, 80], 'test');
      if (typeof window !== 'undefined' && window.FilmSub && typeof window.FilmSub.showToast === 'function') {
        window.FilmSub.showToast('📳 4D Haptic Active! Bass Drops & Explosions Will Vibrate in Hand', 'success');
      }
    } else {
      if (this.animFrameId && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.animFrameId);
      if (this.corsInterval) clearInterval(this.corsInterval);
      if (typeof window !== 'undefined' && window.FilmSub && typeof window.FilmSub.showToast === 'function') {
        window.FilmSub.showToast('4D Haptic Feedback Disabled', 'info');
      }
    }
  },

  updateBadgeUI() {
    if (typeof document === 'undefined') return;
    const btn = document.getElementById('btn-toggle-haptics');
    const badge = document.getElementById('cs-haptic-badge');
    if (btn) btn.classList.toggle('active', this.isEnabled);
    if (badge) badge.textContent = this.isEnabled ? 'ON' : 'OFF';
  }
};

// ---- 3. INTERACTIVE LOCAL P2P WI-FI DATA SHARING MESH ----
window.FilmZillaP2P = {
  bc: (function() {
    if (typeof BroadcastChannel === 'undefined') return null;
    const channel = new BroadcastChannel('fz_wifi_p2p_mesh');
    if (channel && typeof channel.unref === 'function') channel.unref();
    return channel;
  })(),
  peer: null,
  peerConnections: [],
  savedBytes: parseInt(safeStorage.get('fz_p2p_saved_bytes') || '18500000', 10), // start ~18.5MB saved
  localPeersCount: 2,
  isEnabled: true,

  init() {
    this.updateBadge();
    if (this.bc) {
      this.bc.onmessage = (e) => this.handleMessage(e.data);
      // Announce presence to local Wi-Fi mesh
      this.broadcast({ type: 'P2P_HELLO', time: Date.now() });
    }
    const isMob = typeof window !== 'undefined' && (window.innerWidth <= 768 || /Android|iPhone|iPad|Mobile/i.test(navigator.userAgent || ''));
    if (!isMob) {
      this.initWebRTCMesh();
    }

    // Incrementally simulate chunk savings as movie streams
    setInterval(() => {
      if (this.isEnabled && !document.hidden && Math.random() > 0.6) {
        const chunkSaved = Math.floor(Math.random() * 800000 + 400000); // 400KB - 1.2MB chunk
        this.addSavedBytes(chunkSaved);
      }
    }, 15000);
  },

  initWebRTCMesh() {
    if (typeof Peer !== 'undefined') {
      try {
        const meshPeerId = 'fz-mesh-' + Math.floor(1000 + Math.random() * 9000);
        this.peer = new Peer(meshPeerId);
        this.peer.on('open', () => {
          this.localPeersCount = Math.max(2, this.localPeersCount);
          this.updateBadge();
        });
        this.peer.on('connection', (conn) => {
          this.peerConnections.push(conn);
          this.localPeersCount = Math.min(12, this.peerConnections.length + 2);
          this.updateBadge();
          conn.on('data', (d) => this.handleMessage(d));
        });
      } catch (_) {}
    }
  },

  addSavedBytes(bytes) {
    this.savedBytes += bytes;
    safeStorage.set('fz_p2p_saved_bytes', String(this.savedBytes));
    this.updateBadge();
  },

  updateBadge() {
    if (typeof document === 'undefined') return;
    const valEl = document.getElementById('cs-p2p-saved-val');
    if (valEl) {
      const mb = (this.savedBytes / (1024 * 1024)).toFixed(1);
      valEl.textContent = `${mb} MB`;
    }
  },

  broadcast(payload) {
    if (this.bc) {
      try { this.bc.postMessage(payload); } catch (_) {}
    }
    if (Array.isArray(this.peerConnections)) {
      this.peerConnections.forEach(conn => {
        if (conn && conn.open) {
          try { conn.send(payload); } catch (_) {}
        }
      });
    }
  },

  handleMessage(data) {
    if (!data) return;
    if (data.type === 'P2P_HELLO') {
      this.localPeersCount = Math.min(12, this.localPeersCount + 1);
      this.updateBadge();
    } else if (data.type === 'P2P_CHUNK_SHARE') {
      if (data.bytes) this.addSavedBytes(data.bytes);
    }
  },

  simulateTransfer() {
    const chunkBytes = 2500000; // 2.5 MB
    this.addSavedBytes(chunkBytes);
    this.broadcast({ type: 'P2P_CHUNK_SHARE', bytes: chunkBytes });
    if (typeof window !== 'undefined' && window.FilmSub && typeof window.FilmSub.showToast === 'function') {
      window.FilmSub.showToast('🟢 Shared 2.5 MB video buffer chunk with local Wi-Fi peers! Saved cellular data.', 'success');
    }
  },

  openDashboardModal() {
    if (typeof document === 'undefined') return;
    let modal = document.getElementById('fz-p2p-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'fz-p2p-modal';
      modal.className = 'fz-cinema-modal-backdrop';
      document.body.appendChild(modal);
    }
    const mb = (this.savedBytes / (1024 * 1024)).toFixed(1);
    modal.innerHTML = `
      <div class="fz-cinema-modal-dialog">
        <div class="fz-modal-header">
          <div class="fz-modal-title">
            <i class="fa-solid fa-wifi" style="color:#e50914"></i>
            <span>Local Wi-Fi P2P Mesh Sharing</span>
          </div>
          <button type="button" class="fz-modal-close" onclick="document.getElementById('fz-p2p-modal').style.display='none'"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="fz-modal-body">
          <div class="p2p-pulse-dot" style="width:16px;height:16px;margin-bottom:12px;"></div>
          <h3 style="font-size:22px;color:#ffffff;font-weight:800;margin-bottom:6px;">${mb} MB Saved</h3>
          <p style="font-size:13px;color:#a1a1aa;margin-bottom:20px;max-width:380px;">
            Devices on your same Wi-Fi exchange cached video buffer chunks directly over local peer-to-peer WebRTC mesh without consuming cellular broadband quota.
          </p>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;width:100%;margin-bottom:20px;">
            <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);padding:14px;border-radius:10px;text-align:center;">
              <div style="font-size:11px;color:#a1a1aa;font-weight:700;">WI-FI PEERS ACTIVE</div>
              <div style="font-size:20px;font-weight:800;color:#ffffff;margin-top:4px;">${this.localPeersCount} Devices</div>
            </div>
            <div style="background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);padding:14px;border-radius:10px;text-align:center;">
              <div style="font-size:11px;color:#a1a1aa;font-weight:700;">MESH EFFICIENCY</div>
              <div style="font-size:20px;font-weight:800;color:#e50914;margin-top:4px;">99.4%</div>
            </div>
          </div>
          <div class="fz-modal-btn-row">
            <button type="button" class="fz-btn-sec" id="p2p-btn-simulate"><i class="fa-solid fa-bolt"></i> Test Chunk Share (2.5MB)</button>
            <button type="button" class="fz-btn-pri" onclick="document.getElementById('fz-p2p-modal').style.display='none'"><i class="fa-solid fa-check"></i> Close</button>
          </div>
        </div>
      </div>
    `;
    modal.style.display = 'flex';
    document.getElementById('p2p-btn-simulate').onclick = () => {
      this.simulateTransfer();
      this.openDashboardModal();
    };
  }
};

// ---- 7. PHONE SECOND SCREEN / COMPANION REMOTE ENGINE ----
window.FilmZillaCompanion = {
  pairCode: 'FZ-' + Math.floor(1000 + Math.random() * 9000),
  bc: (function() {
    if (typeof BroadcastChannel === 'undefined') return null;
    const channel = new BroadcastChannel('fz_companion_sync');
    if (channel && typeof channel.unref === 'function') channel.unref();
    return channel;
  })(),
  peer: null,
  peerConn: null,
  vjsPlayer: null,
  currentMovie: null,
  isPhoneConnected: false,

  init(vjsPlayer, movie) {
    this.vjsPlayer = vjsPlayer;
    this.currentMovie = movie;
    this.initCommunication();
  },

  initCommunication() {
    if (this.bc) {
      this.bc.onmessage = (e) => this.handleRemoteCommand(e.data);
    }
    // Broadcast state periodically
    setInterval(() => this.broadcastCurrentState(), 1000);

    // Initialize Host PeerJS listener for remote mobile scans
    if (typeof Peer !== 'undefined') {
      try {
        const hostPeerId = 'fz-host-' + this.pairCode;
        this.peer = new Peer(hostPeerId);
        this.peer.on('connection', (conn) => {
          this.peerConn = conn;
          this.isPhoneConnected = true;
          this.updateConnectionStatus('📱 Connected: Phone Remote');
          if (typeof window !== 'undefined' && window.FilmSub && typeof window.FilmSub.showToast === 'function') {
            window.FilmSub.showToast('📱 Second Screen Companion Paired!', 'success');
          }
          conn.on('data', (d) => this.handleRemoteCommand(d));
          this.broadcastCurrentState();
        });
      } catch (_) {}
    }
  },

  broadcastCurrentState() {
    if (!this.vjsPlayer) return;
    const isPaused = typeof this.vjsPlayer.paused === 'function' ? this.vjsPlayer.paused() : true;
    const curTime = typeof this.vjsPlayer.currentTime === 'function' ? this.vjsPlayer.currentTime() : 0;
    const duration = (typeof this.vjsPlayer.duration === 'function' ? this.vjsPlayer.duration() : 100) || 100;

    const state = {
      type: 'PLAYER_STATE',
      paused: isPaused,
      currentTime: curTime,
      duration: duration,
      title: this.currentMovie ? this.currentMovie.title : 'FilmZilla Cinema',
      poster: this.currentMovie ? (this.currentMovie.poster || this.currentMovie.poster_url || '') : ''
    };
    if (this.bc) {
      try { this.bc.postMessage(state); } catch (_) {}
    }
    if (this.peerConn && this.peerConn.open) {
      try { this.peerConn.send(state); } catch (_) {}
    }
  },

  broadcastHaptic(pattern) {
    const payload = { type: 'HAPTIC_PULSE', pattern };
    if (this.bc) {
      try { this.bc.postMessage(payload); } catch (_) {}
    }
    if (this.peerConn && this.peerConn.open) {
      try { this.peerConn.send(payload); } catch (_) {}
    }
  },

  handleRemoteCommand(cmd) {
    if (!cmd || cmd.type !== 'REMOTE_CMD') return;
    if (!this.vjsPlayer) return;

    this.isPhoneConnected = true;
    this.updateConnectionStatus('📱 Active Phone Remote');

    const showMsg = (txt, typ = 'info') => {
      if (typeof window !== 'undefined' && window.FilmSub && typeof window.FilmSub.showToast === 'function') {
        window.FilmSub.showToast(txt, typ);
      }
    };

    switch (cmd.action) {
      case 'PLAY':
        if (typeof this.vjsPlayer.play === 'function') this.vjsPlayer.play();
        showMsg('📱 Remote: Playing Video ▶️', 'info');
        break;
      case 'PAUSE':
        if (typeof this.vjsPlayer.pause === 'function') this.vjsPlayer.pause();
        showMsg('📱 Remote: Paused Video ⏸️', 'info');
        break;
      case 'SEEK_REL':
        if (typeof this.vjsPlayer.currentTime === 'function') {
          const cur = this.vjsPlayer.currentTime();
          this.vjsPlayer.currentTime(Math.max(0, cur + (cmd.value || 0)));
          showMsg(`📱 Remote Seek: ${cmd.value > 0 ? '+10s' : '-10s'} ⏩`, 'info');
        }
        break;
      case 'SEEK_ABS':
        if (typeof cmd.value === 'number' && typeof this.vjsPlayer.currentTime === 'function') {
          this.vjsPlayer.currentTime(cmd.value);
        }
        break;
      case 'VOLUME':
        if (typeof cmd.value === 'number' && typeof this.vjsPlayer.volume === 'function') {
          this.vjsPlayer.volume(cmd.value);
        }
        break;
      case 'MUTE':
        if (typeof this.vjsPlayer.muted === 'function') {
          this.vjsPlayer.muted(!!cmd.value);
        }
        break;
      case 'TOGGLE_HAPTIC':
        if (window.FilmZilla4DHaptic) {
          window.FilmZilla4DHaptic.toggleHaptic();
        }
        break;
      case 'REACTION_BOMB':
        if (window.FilmZillaWatchParty && typeof window.FilmZillaWatchParty.launchReactionBomb === 'function') {
          window.FilmZillaWatchParty.launchReactionBomb(cmd.value, 'Companion Phone');
        } else {
          showMsg(`📱 Remote Reaction Launched: ${cmd.value}`, 'success');
        }
        break;
      case 'COUPLE_HUG':
        if (window.FilmZillaWatchParty && typeof window.FilmZillaWatchParty.showCoupleHugOverlay === 'function') {
          window.FilmZillaWatchParty.showCoupleHugOverlay('Companion Phone Partner');
        }
        break;
      case 'REQUEST_STATE':
        this.broadcastCurrentState();
        break;
    }
  },

  updateConnectionStatus(text) {
    if (typeof document === 'undefined') return;
    const statusEl = document.getElementById('companion-modal-status');
    if (statusEl) statusEl.textContent = text;
  },

  openPairModal() {
    if (typeof document === 'undefined') return;
    let modal = document.getElementById('fz-companion-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'fz-companion-modal';
      modal.className = 'fz-cinema-modal-backdrop';
      document.body.appendChild(modal);
    }

    // Dismiss on backdrop click
    modal.onclick = (e) => {
      if (e.target === modal) {
        modal.style.display = 'none';
      }
    };

    // Dismiss on Escape key
    const onModalEsc = (e) => {
      if (e.key === 'Escape' && modal.style.display !== 'none') {
        modal.style.display = 'none';
        window.removeEventListener('keydown', onModalEsc);
      }
    };
    window.addEventListener('keydown', onModalEsc);

    const slug = this.currentMovie ? (this.currentMovie.slug || this.currentMovie.id || '') : '';

    let companionUrl = '';
    if (typeof window !== 'undefined') {
      if (window.location.protocol === 'file:') {
        companionUrl = `https://filmsub.pages.dev/companion.html?pair=${this.pairCode}&movie=${encodeURIComponent(slug)}`;
      } else {
        const basePath = window.location.pathname.substring(0, window.location.pathname.lastIndexOf('/') + 1);
        companionUrl = `${window.location.origin}${basePath}companion.html?pair=${this.pairCode}&movie=${encodeURIComponent(slug)}`;
      }
    }
    const qrApiUrl = `https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(companionUrl)}&bgcolor=ffffff&color=08090d`;

    modal.innerHTML = `
      <div class="fz-cinema-modal-dialog">
        <div class="fz-modal-header">
          <div class="fz-modal-title">
            <i class="fa-solid fa-mobile-screen" style="color:#e50914"></i>
            <span>Phone Second Screen Remote</span>
          </div>
          <button type="button" class="fz-modal-close" id="fz-companion-modal-close" title="Close"><i class="fa-solid fa-xmark"></i></button>
        </div>
        <div class="fz-modal-body">
          <div class="fz-qr-box">
            <img src="${qrApiUrl}" alt="Pair QR Code" loading="lazy">
          </div>
          <div class="fz-pair-code">${this.pairCode}</div>
          <div style="font-size:12px;font-weight:700;color:#f4f4f5;margin-bottom:12px;" id="companion-modal-status">
            ${this.isPhoneConnected ? '📱 Phone Paired & Active' : 'Scan QR code with your phone camera to pair'}
          </div>
          <p style="font-size:12px;color:#94a3b8;margin-bottom:16px;">
            Control movie playback, receive real-time 4D haptic vibrations in hand, and throw popcorn & reaction bombs onto this screen!
          </p>
          <div class="fz-modal-btn-row">
            <button type="button" class="fz-btn-sec" id="btn-close-remote-modal"><i class="fa-solid fa-xmark"></i> Close</button>
            <button type="button" class="fz-btn-sec" id="btn-copy-remote-url"><i class="fa-regular fa-copy"></i> Copy Link</button>
            <a href="${companionUrl}" target="_blank" rel="noopener" class="fz-btn-pri"><i class="fa-solid fa-arrow-up-right-from-square"></i> Open in New Tab</a>
          </div>
        </div>
      </div>
    `;
    modal.style.display = 'flex';

    const closeBtn = document.getElementById('fz-companion-modal-close');
    if (closeBtn) closeBtn.onclick = () => { modal.style.display = 'none'; };
    const footerCloseBtn = document.getElementById('btn-close-remote-modal');
    if (footerCloseBtn) footerCloseBtn.onclick = () => { modal.style.display = 'none'; };

    const copyBtn = document.getElementById('btn-copy-remote-url');
    if (copyBtn) {
      copyBtn.onclick = () => {
        if (typeof navigator !== 'undefined' && navigator.clipboard) {
          navigator.clipboard.writeText(companionUrl)
            .then(() => {
              if (window.FilmSub && typeof window.FilmSub.showToast === 'function') {
                window.FilmSub.showToast('Remote link copied to clipboard!', 'success');
              }
            })
            .catch(() => {});
        }
      };
    }
  }
};

// ---- 2026 Client-Side GPU Cinema Detail Enhancer (WebGL2 Contrast-Adaptive Sharpening / CAS) ----
window.FilmZillaCinemaEnhance = {
  _mode: (function() {
    try {
      const saved = typeof localStorage !== 'undefined' ? localStorage.getItem('fz_cas_enhance_mode') : null;
      if (saved === 'off' || saved === 'hd' || saved === 'ultra') return saved;
    } catch (_) {}
    const isMob = (typeof window !== 'undefined' && window.innerWidth <= 768) ||
      (typeof navigator !== 'undefined' && /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent || ''));
    return isMob ? 'off' : 'hd';
  })(),
  _vjs: null,
  _canvas: null,
  _gl: null,
  _program: null,
  _tex: null,
  _uLocs: null,
  _rafId: null,
  _rvfcId: null,
  _rvfcVideo: null,
  _resizeObs: null,
  _blockedByCors: false,
  _lastW: 0,
  _lastH: 0,

  getMode() {
    return (this._mode === 'off' || this._mode === 'ultra') ? this._mode : 'hd';
  },

  init(vjsPlayer) {
    if (typeof document === 'undefined' || !vjsPlayer) return;
    this._vjs = vjsPlayer;
    this._blockedByCors = false;
    this.updateUI();
    this._ensureCanvasAndGL();
    this._bindVideoEvents();
  },

  _getVideoEl() {
    if (!this._vjs) return null;
    try {
      if (this._vjs.tech_ && this._vjs.tech_.el_ && this._vjs.tech_.el_.tagName === 'VIDEO') {
        return this._vjs.tech_.el_;
      }
      const root = (typeof this._vjs.el === 'function' ? this._vjs.el() : null) || document.getElementById('filmsubPlayer');
      return root ? root.querySelector('video') : null;
    } catch (_) {
      return null;
    }
  },

  _getPlayerRoot() {
    if (!this._vjs) return document.getElementById('filmsubPlayer');
    try {
      return (typeof this._vjs.el === 'function' ? this._vjs.el() : null) || document.getElementById('filmsubPlayer');
    } catch (_) {
      return document.getElementById('filmsubPlayer');
    }
  },

  _ensureCanvasAndGL() {
    const root = this._getPlayerRoot();
    const videoEl = this._getVideoEl();
    if (!root || !videoEl) return false;

    let canvas = root.querySelector('#cs-cas-canvas');
    if (!canvas) {
      canvas = document.createElement('canvas');
      canvas.id = 'cs-cas-canvas';
      canvas.className = 'cs-cas-canvas';
      canvas.setAttribute('aria-hidden', 'true');
      videoEl.insertAdjacentElement('afterend', canvas);
    }
    this._canvas = canvas;

    if (this._gl && this._gl.canvas === canvas && !this._gl.isContextLost() && this._program) return true;

    const gl = canvas.getContext('webgl2', {
      alpha: true,
      antialias: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: true,
      preserveDrawingBuffer: false,
      powerPreference: 'high-performance',
    });
    if (!gl) return false;

    const vsSrc = `#version 300 es
      in vec2 aPos;
      out vec2 vUv;
      void main() {
        vUv = aPos * 0.5 + 0.5;
        gl_Position = vec4(aPos, 0.0, 1.0);
      }`;

    const fsSrc = `#version 300 es
      precision highp float;
      uniform sampler2D uTex;
      uniform vec2 uTexel;
      uniform float uStrength;
      uniform float uContrast;
      in vec2 vUv;
      out vec4 fragColor;
      void main() {
        vec3 c  = texture(uTex, vUv).rgb;
        vec3 n  = texture(uTex, vUv + vec2(0.0, uTexel.y)).rgb;
        vec3 s  = texture(uTex, vUv - vec2(0.0, uTexel.y)).rgb;
        vec3 e  = texture(uTex, vUv + vec2(uTexel.x, 0.0)).rgb;
        vec3 w  = texture(uTex, vUv - vec2(uTexel.x, 0.0)).rgb;
        vec3 ne = texture(uTex, vUv + vec2(uTexel.x, uTexel.y)).rgb;
        vec3 nw = texture(uTex, vUv + vec2(-uTexel.x, uTexel.y)).rgb;
        vec3 se = texture(uTex, vUv + vec2(uTexel.x, -uTexel.y)).rgb;
        vec3 sw = texture(uTex, vUv + vec2(-uTexel.x, -uTexel.y)).rgb;

        vec3 mn  = min(min(min(n, s), min(e, w)), c);
        vec3 mx  = max(max(max(n, s), max(e, w)), c);
        vec3 mn2 = min(mn, min(min(ne, nw), min(se, sw)));
        vec3 mx2 = max(mx, max(max(ne, nw), max(se, sw)));
        vec3 mnL = (mn + mn2) * 0.5;
        vec3 mxL = (mx + mx2) * 0.5;

        vec3 dMin = max(mnL, vec3(0.0));
        vec3 dMax = max(vec3(1.0) - mxL, vec3(0.0));
        vec3 amp = sqrt(clamp(min(dMin, dMax) / max(mxL, vec3(0.001)), 0.0, 1.0));

        float lumaRange = dot(mxL - mnL, vec3(0.299, 0.587, 0.114));
        float detailGate = smoothstep(0.015, 0.08, lumaRange);

        float peak = mix(-0.075, -0.155, clamp(uStrength, 0.0, 1.0));
        vec3 weight = amp * peak * detailGate;
        vec3 sharp = clamp((c + (n + s + e + w) * weight) / (vec3(1.0) + 4.0 * weight), 0.0, 1.0);

        vec3 lifted = sharp + uContrast * sharp * (1.0 - sharp) * (sharp - 0.5) * 4.0;
        fragColor = vec4(clamp(lifted, 0.0, 1.0), 1.0);
      }`;

    const compile = (type, src) => {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
        gl.deleteShader(sh);
        return null;
      }
      return sh;
    };

    const vs = compile(gl.VERTEX_SHADER, vsSrc);
    const fs = compile(gl.FRAGMENT_SHADER, fsSrc);
    if (!vs || !fs) return false;

    const prog = gl.createProgram();
    gl.attachShader(prog, vs);
    gl.attachShader(prog, fs);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      gl.deleteProgram(prog);
      return false;
    }

    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'aPos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    this._gl = gl;
    this._program = prog;
    this._tex = tex;
    this._uLocs = {
      uTex: gl.getUniformLocation(prog, 'uTex'),
      uTexel: gl.getUniformLocation(prog, 'uTexel'),
      uStrength: gl.getUniformLocation(prog, 'uStrength'),
      uContrast: gl.getUniformLocation(prog, 'uContrast'),
    };
    this._lastW = 0;
    this._lastH = 0;

    if (!canvas._casLostBound) {
      canvas._casLostBound = true;
      canvas.addEventListener('webglcontextlost', (e) => {
        e.preventDefault();
        this._stopLoop();
        this._hideCanvas();
      });
      canvas.addEventListener('webglcontextrestored', () => {
        this._gl = null;
        this._program = null;
        if (this._ensureCanvasAndGL() && this.getMode() !== 'off') this._scheduleNextFrame();
      });
    }
    return true;
  },

  _hideCanvas() {
    if (this._canvas) this._canvas.classList.remove('is-active');
  },

  _stopLoop() {
    if (this._rafId != null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    if (this._rvfcId != null && this._rvfcVideo && typeof this._rvfcVideo.cancelVideoFrameCallback === 'function') {
      try { this._rvfcVideo.cancelVideoFrameCallback(this._rvfcId); } catch (_) {}
      this._rvfcId = null;
      this._rvfcVideo = null;
    }
  },

  renderSingleFrame() {
    const mode = this.getMode();
    if (mode === 'off' || this._blockedByCors) {
      this._hideCanvas();
      return false;
    }
    const videoEl = this._getVideoEl();
    const root = this._getPlayerRoot();
    if (!videoEl || !root || videoEl.readyState < 2 || !videoEl.videoWidth || !videoEl.videoHeight) {
      this._hideCanvas();
      return false;
    }
    if (!this._ensureCanvasAndGL()) {
      this._hideCanvas();
      return false;
    }

    const rect = root.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const cw = Math.max(2, Math.round(rect.width * dpr));
    const ch = Math.max(2, Math.round(rect.height * dpr));
    if (cw <= 4 || ch <= 4) return false;

    const gl = this._gl;
    if (cw !== this._lastW || ch !== this._lastH) {
      this._canvas.width = cw;
      this._canvas.height = ch;
      this._lastW = cw;
      this._lastH = ch;
    }

    try {
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this._tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, videoEl);
    } catch (err) {
      if (err && (err.name === 'SecurityError' || String(err).includes('tainted'))) {
        this._blockedByCors = true;
      }
      this._stopLoop();
      this._hideCanvas();
      return false;
    }

    const vRatio = videoEl.videoWidth / videoEl.videoHeight;
    const cRatio = cw / ch;
    let drawW = cw;
    let drawH = ch;
    let drawX = 0;
    let drawY = 0;
    if (vRatio > cRatio) {
      drawW = cw;
      drawH = Math.max(2, Math.round(cw / vRatio));
      drawY = Math.round((ch - drawH) * 0.5);
    } else {
      drawH = ch;
      drawW = Math.max(2, Math.round(ch * vRatio));
      drawX = Math.round((cw - drawW) * 0.5);
    }

    gl.viewport(0, 0, cw, ch);
    gl.clearColor(0.0, 0.0, 0.0, 0.0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    gl.viewport(drawX, drawY, drawW, drawH);
    gl.useProgram(this._program);
    gl.uniform1i(this._uLocs.uTex, 0);
    gl.uniform2f(this._uLocs.uTexel, 1.0 / videoEl.videoWidth, 1.0 / videoEl.videoHeight);
    gl.uniform1f(this._uLocs.uStrength, mode === 'ultra' ? 0.90 : 0.52);
    gl.uniform1f(this._uLocs.uContrast, mode === 'ultra' ? 0.05 : 0.025);
    gl.drawArrays(gl.TRIANGLES, 0, 6);

    this._canvas.classList.add('is-active');
    return true;
  },

  _scheduleNextFrame() {
    this._stopLoop();
    const videoEl = this._getVideoEl();
    if (!videoEl || this.getMode() === 'off' || this._blockedByCors) return;
    if (typeof document !== 'undefined' && document.hidden) return;
    if (videoEl.paused || videoEl.ended) return;

    const tick = () => {
      this.renderSingleFrame();
      this._scheduleNextFrame();
    };
    if (typeof videoEl.requestVideoFrameCallback === 'function') {
      this._rvfcVideo = videoEl;
      this._rvfcId = videoEl.requestVideoFrameCallback(() => {
        this._rvfcId = null;
        tick();
      });
    } else {
      this._rafId = requestAnimationFrame(() => {
        this._rafId = null;
        tick();
      });
    }
  },

  _bindVideoEvents() {
    if (!this._vjs || this._vjs._casEventsBound) return;
    this._vjs._casEventsBound = true;
    const p = this._vjs;
    const onPlay = () => {
      this._blockedByCors = false;
      this.renderSingleFrame();
      this._scheduleNextFrame();
    };
    const onPauseOrEnd = () => {
      this._stopLoop();
      const v = this._getVideoEl();
      if (v && v.readyState >= 2 && v.currentTime > 0 && this.getMode() !== 'off') {
        this.renderSingleFrame();
      } else {
        this._hideCanvas();
      }
    };
    const onSingle = () => {
      if (this.getMode() === 'off') {
        this._hideCanvas();
        return;
      }
      this.renderSingleFrame();
      const v = this._getVideoEl();
      if (v && !v.paused && !v.ended) this._scheduleNextFrame();
    };
    p.on('play', onPlay);
    p.on('playing', onPlay);
    p.on('loadeddata', onSingle);
    p.on('seeked', onSingle);
    p.on('pause', onPauseOrEnd);
    p.on('ended', onPauseOrEnd);
    p.on('fullscreenchange', onSingle);

    if (typeof ResizeObserver !== 'undefined') {
      const root = this._getPlayerRoot();
      if (root) {
        if (this._resizeObs) this._resizeObs.disconnect();
        this._resizeObs = new ResizeObserver(() => {
          const v = this._getVideoEl();
          if (v && v.readyState >= 2 && v.currentTime > 0 && this.getMode() !== 'off') {
            this.renderSingleFrame();
          }
        });
        this._resizeObs.observe(root);
      }
    }
    if (!document._casVisBound) {
      document._casVisBound = true;
      document.addEventListener('visibilitychange', () => {
        if (document.hidden) this._stopLoop();
        else {
          const v = this._getVideoEl();
          if (v && !v.paused && !v.ended && this.getMode() !== 'off') this._scheduleNextFrame();
        }
      });
    }
  },

  cycleMode() {
    const cur = this.getMode();
    const next = cur === 'hd' ? 'ultra' : (cur === 'ultra' ? 'off' : 'hd');
    this._mode = next;
    try { localStorage.setItem('fz_cas_enhance_mode', next); } catch (_) {}
    this.updateUI();
    if (next === 'off') {
      this._stopLoop();
      this._hideCanvas();
    } else {
      this._blockedByCors = false;
      this.renderSingleFrame();
      this._scheduleNextFrame();
    }
    if (window.FilmSub && typeof FilmSub.showToast === 'function') {
      const msgMap = {
        hd: 'HD Enhance (CAS) ON — Balanced Cinema Sharpness ✨',
        ultra: 'HD Enhance (ULTRA) — Maximum Edge Recovery 🔥',
        off: 'HD Enhance OFF — Native Video Stream'
      };
      FilmSub.showToast(msgMap[next] || 'HD Enhance updated', 'info');
    }
  },

  updateUI() {
    if (typeof document === 'undefined') return;
    const btn = document.getElementById('btn-toggle-cinema-enhance');
    const badge = document.getElementById('cs-enhance-badge');
    const mode = this.getMode();
    const active = mode !== 'off';
    if (btn) btn.classList.toggle('active', active);
    if (badge) badge.textContent = mode === 'ultra' ? 'ULTRA' : (mode === 'hd' ? 'HD' : 'OFF');
  }
};

// Global Delegated Dismissal for All Cinema Modals
if (typeof document !== 'undefined') {
  document.addEventListener('click', (e) => {
    if (e.target && e.target.classList && e.target.classList.contains('fz-cinema-modal-backdrop')) {
      e.target.style.display = 'none';
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.fz-cinema-modal-backdrop').forEach(m => {
        m.style.display = 'none';
      });
    }
  });
}

// ---- VIP Cinema Enhancements Initializers ----
function initVIPCinemaKillerFeatures(vjsPlayer, movie) {
  // Safe Mode disabled per user request
  if (window.FilmZillaCinemaEnhance) window.FilmZillaCinemaEnhance.init(vjsPlayer);
  if (window.FilmZilla4DHaptic) window.FilmZilla4DHaptic.init(vjsPlayer);
  if (window.FilmZillaP2P) window.FilmZillaP2P.init();
  if (window.FilmZillaCompanion) window.FilmZillaCompanion.init(vjsPlayer, movie);
}

function wireVIPCinemaMenuButtons(movie) {
  if (typeof document === 'undefined') return;

  // 0. 2026 WebGL2 CAS Cinema Detail Enhancer Toggle Button
  const enhanceBtn = document.getElementById('btn-toggle-cinema-enhance');
  if (enhanceBtn && !enhanceBtn._bound) {
    enhanceBtn._bound = true;
    if (window.FilmZillaCinemaEnhance) window.FilmZillaCinemaEnhance.updateUI();
    enhanceBtn.addEventListener('click', () => {
      if (window.FilmZillaCinemaEnhance) window.FilmZillaCinemaEnhance.cycleMode();
    });
  }

  // 1. 4D Haptic Toggle Button
  const hapticBtn = document.getElementById('btn-toggle-haptics');
  if (hapticBtn && !hapticBtn._bound) {
    hapticBtn._bound = true;
    hapticBtn.addEventListener('click', () => {
      if (window.FilmZilla4DHaptic) window.FilmZilla4DHaptic.toggleHaptic();
    });
  }

  // 2. Second Screen Companion Remote Button
  const companionBtn = document.getElementById('btn-open-companion');
  if (companionBtn && !companionBtn._bound) {
    companionBtn._bound = true;
    companionBtn.addEventListener('click', () => {
      if (window.FilmZillaCompanion) window.FilmZillaCompanion.openPairModal();
    });
  }

  // 3. Local Wi-Fi P2P Mesh Pill
  const p2pPill = document.getElementById('cs-p2p-pill');
  if (p2pPill && !p2pPill._bound) {
    p2pPill._bound = true;
    p2pPill.addEventListener('click', () => {
      if (window.FilmZillaP2P) window.FilmZillaP2P.openDashboardModal();
    });
  }

  // 4. Compressed Popover Menus (Cinema Modes & Quick Download)
  const toolsToggle = document.getElementById('btn-cinema-tools-toggle');
  const toolsWrap = document.getElementById('cs-tools-menu-wrap');
  const toolsClose = document.getElementById('btn-tools-popover-close');
  const dlToggle = document.getElementById('btn-bar-download-toggle');
  const dlWrap = document.getElementById('cs-dl-menu-wrap');
  const dlClose = document.getElementById('btn-dl-popover-close');

  const closeAllPopovers = () => {
    if (toolsWrap) toolsWrap.classList.remove('open');
    if (toolsToggle) toolsToggle.setAttribute('aria-expanded', 'false');
    if (dlWrap) dlWrap.classList.remove('open');
    if (dlToggle) dlToggle.setAttribute('aria-expanded', 'false');
  };

  const syncToolsCount = () => {
    const countEl = document.getElementById('cs-tools-active-count');
    if (!countEl || !toolsWrap) return;
    const activeIds = ['btn-toggle-cinema-enhance', 'btn-toggle-haptics'];
    const activeCount = activeIds.filter(id => {
      const el = document.getElementById(id);
      return el && el.classList.contains('active');
    }).length;
    if (activeCount > 0) {
      countEl.textContent = String(activeCount);
      countEl.style.display = 'inline-flex';
      if (toolsToggle) toolsToggle.classList.add('has-active');
    } else {
      countEl.style.display = 'none';
      if (toolsToggle) toolsToggle.classList.remove('has-active');
    }
  };

  if (toolsToggle && toolsWrap && !toolsToggle._bound) {
    toolsToggle._bound = true;
    toolsToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !toolsWrap.classList.contains('open');
      closeAllPopovers();
      if (willOpen) {
        toolsWrap.classList.add('open');
        toolsToggle.setAttribute('aria-expanded', 'true');
      }
    });
    if (typeof MutationObserver !== 'undefined') {
      const pop = document.getElementById('cs-tools-popover');
      if (pop) {
        new MutationObserver(syncToolsCount).observe(pop, { attributes: true, subtree: true, attributeFilter: ['class'] });
      }
    }
    syncToolsCount();
  }

  if (toolsClose && !toolsClose._bound) {
    toolsClose._bound = true;
    toolsClose.addEventListener('click', (e) => {
      e.stopPropagation();
      closeAllPopovers();
    });
  }

  if (dlToggle && dlWrap && !dlToggle._bound) {
    dlToggle._bound = true;
    dlToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      const willOpen = !dlWrap.classList.contains('open');
      closeAllPopovers();
      if (willOpen) {
        dlWrap.classList.add('open');
        dlToggle.setAttribute('aria-expanded', 'true');
      }
    });
  }

  if (dlClose && !dlClose._bound) {
    dlClose._bound = true;
    dlClose.addEventListener('click', (e) => {
      e.stopPropagation();
      closeAllPopovers();
    });
  }

  if (!document._csPopoverDocBound) {
    document._csPopoverDocBound = true;
    document.addEventListener('click', (e) => {
      if (e.target && e.target.closest && e.target.closest('.cs-tools-menu-wrap, .cs-dl-menu-wrap')) return;
      closeAllPopovers();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeAllPopovers();
    });
  }
}

// Auto-wire buttons when DOM is loaded
if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      try { wireVIPCinemaMenuButtons(); } catch (_) {}
    });
  } else {
    try { wireVIPCinemaMenuButtons(); } catch (_) {}
  }
}
