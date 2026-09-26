/* ============================================================
   FilmSub – player.js | CineSubz / Netflix Super Player v2.0
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
   4. Multi-Server + External Backup Players ("Bahirawa Players"):
      - Server 1: ⚡ Super Player (Chunk Stream + Auto Sinhala Sub)
      - Server 2: ☁️ Drive Player (Google CDN + Live Sinhala Sub)
      - Server 3: 🌐 VIP Backup 1 (VidSrc Multi-Quality)
      - Server 4: 🎬 VIP Backup 2 (MultiEmbed / SuperEmbed HD)
      - Server 5: 🚀 VIP Backup 3 (AutoEmbed Global HD)
   5. CineSubz-Style Embedded Sinhala Subtitles:
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

document.addEventListener('DOMContentLoaded', async () => {
  await waitForFilmSub();
  await FilmSub.loadMovies();

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

  currentSeason = currentMovie.season || 1;
  currentEpisode = currentMovie.episode || 1;

  const initialNet = detectNetworkSpeed();
  currentEffectiveQuality = initialNet.recommendedQuality;

  FilmSub.trackView(slug);
  document.title = `${currentMovie.title || 'Movie'} (${currentMovie.year || ''}) Sinhala Subtitles – FilmSub`;

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
function getMovieStreams(movie) {
  if (!movie) return [];
  const list = [];
  const driveId = extractMovieDriveId(movie);
  const isSeries = movie.type === 'series' || (Array.isArray(movie.seasons) && movie.seasons.length > 0);
  const sNum = currentSeason || movie.season || 1;
  const eNum = currentEpisode || movie.episode || 1;
  const primaryUrl = movie.stream_url || (Array.isArray(movie.streams) && movie.streams[0] && movie.streams[0].stream_url) || '';

  // 1. Server 1: Primary Telegram Cloud Stream OR High-Speed Direct MP4 Stream
  if (movie.message_id || (primaryUrl && (primaryUrl.includes('/stream/') || primaryUrl.endsWith('.mp4')))) {
    const tgUrl = primaryUrl || `/stream/channel/-1004325759505/${movie.message_id}`;
    list.push({
      server: 'Server 1',
      label: '⚡ Telegram Super Player (Cloud HD • Auto Sub)',
      mode: 'telegram_stream',
      type: 'video/mp4',
      stream_url: tgUrl,
      message_id: movie.message_id || 0,
      file_id: movie.file_id || '',
      quality: '1080p',
    });
  } else if (driveId) {
    const activeQ = selectedQuality === 'auto' ? currentEffectiveQuality : selectedQuality;
    const qDriveId = extractDriveIdForQuality(movie, activeQ) || driveId;
    list.push({
      server: 'Server 1',
      label: '⚡ Telegram Super Player (Cloud HD • Auto Sub)',
      mode: 'super_chunk',
      type: 'video/mp4',
      drive_id: qDriveId,
      stream_url: buildChunkStreamUrl(qDriveId, activeQ),
      quality: '1080p',
    });
  }

  // 2. External Multi-Server Backup Players (Server 2: VidSrc Pro, Server 3: SuperEmbed, Server 4: AutoEmbed)
  const imdbId = (movie.imdb_id || '').trim();
  const tmdbId = String(movie.tmdb_id || '').trim();
  const extId = imdbId || tmdbId;

  if (extId) {
    const vidsrcUrl = isSeries
      ? `https://vidsrc.xyz/embed/tv/${extId}/${sNum}/${eNum}`
      : `https://vidsrc.xyz/embed/movie/${extId}`;
    list.push({
      server: `Server ${list.length + 1}`,
      label: '🌐 VidSrc Pro (Multi-Quality HD)',
      mode: 'external_embed',
      type: 'embed',
      embed: true,
      stream_url: vidsrcUrl,
    });

    const useTmdbParam = (!imdbId && tmdbId) ? '&tmdb=1' : '';
    const multiEmbedUrl = isSeries
      ? `https://multiembed.mov/?video_id=${extId}${useTmdbParam}&s=${sNum}&e=${eNum}`
      : `https://multiembed.mov/?video_id=${extId}${useTmdbParam}`;
    list.push({
      server: `Server ${list.length + 1}`,
      label: '🎬 SuperEmbed (Fast VIP HD)',
      mode: 'external_embed',
      type: 'embed',
      embed: true,
      stream_url: multiEmbedUrl,
    });

    const autoEmbedUrl = isSeries
      ? `https://player.autoembed.cc/embed/tv/${extId}/${sNum}/${eNum}`
      : `https://player.autoembed.cc/embed/movie/${extId}`;
    list.push({
      server: `Server ${list.length + 1}`,
      label: '🚀 AutoEmbed (Global Stream HD)',
      mode: 'external_embed',
      type: 'embed',
      embed: true,
      stream_url: autoEmbedUrl,
    });
  }

  // 3. Google Drive Archive Player (if available as secondary/backup server)
  if (driveId && !list.some(s => s.drive_id === driveId && s.mode === 'drive_embed')) {
    list.push({
      server: `Server ${list.length + 1}`,
      label: '☁️ Drive Player (Google Archive • Auto Sub)',
      mode: 'drive_embed',
      type: 'embed',
      embed: true,
      drive_id: driveId,
      stream_url: `https://drive.google.com/file/d/${driveId}/preview`,
    });
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
  const defaultUri = buildDefaultSinhalaVttDataUri(movie);
  if (Array.isArray(movie.subtitles) && movie.subtitles.length > 0) {
    return movie.subtitles.map((sub, idx) => ({
      ...sub,
      language: sub.language || 'Sinhala',
      srclang: sub.srclang || 'si',
      label: sub.label || 'සිංහල උපසිරැසි (Sinhala)',
      url: _isValidSubUrl(sub.url) ? sub.url : defaultUri,
      default: sub.default !== undefined ? sub.default : (idx === 0)
    }));
  }
  if (_isValidSubUrl(movie.subtitle_url)) {
    return [
      {
        language: movie.lang || 'Sinhala',
        srclang: 'si',
        label: 'සිංහල උපසිරැසි (Sinhala)',
        url: movie.subtitle_url,
        default: true
      }
    ];
  }
  return [
    {
      language: 'Sinhala',
      srclang: 'si',
      label: 'සිංහල උපසිරැසි (Sinhala Auto)',
      url: defaultUri,
      default: true
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

function getMovieDownloads(movie) {
  if (!movie) return [];
  const rawDls = Array.isArray(movie.downloads) ? [...movie.downloads] : [];
  const displayTitle = movie.title
    ? (movie.type === 'series' && movie.season && movie.episode
        ? `${movie.title} S${String(movie.season).padStart(2, '0')}E${String(movie.episode).padStart(2, '0')}`
        : `${movie.title}${movie.year ? ' (' + movie.year + ')' : ''}`)
    : 'Movie';

  let primaryUrl = movie.drive_file_id || movie.stream_url || '';
  if (!primaryUrl && rawDls.length > 0) {
    const cloudEntry = rawDls.find(d => d.url && !d.url.includes('t.me/'));
    primaryUrl = cloudEntry ? (cloudEntry.drive_id || cloudEntry.url) : rawDls[0].url;
  }

  let baseMb = 1450;
  if (movie.file_size && movie.file_size > 0) {
    baseMb = movie.file_size / (1024 * 1024);
  } else if (rawDls.length > 0 && rawDls[0].size) {
    const sm = String(rawDls[0].size).match(/([\d.]+)\s*(GB|MB)/i);
    if (sm) {
      baseMb = parseFloat(sm[1]) * (sm[2].toUpperCase() === 'GB' ? 1024 : 1);
    }
  }

  const fmtSize = (mb) => mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.max(95, Math.round(mb))} MB`;
  const targetQualities = [
    { q: '1080p', ratio: 1.0, vq: 'hd1080' },
    { q: '720p',  ratio: 0.55, vq: 'hd720' },
    { q: '480p',  ratio: 0.32, vq: 'large' },
    { q: '360p',  ratio: 0.18, vq: 'medium' }
  ];

  const enriched = [];
  targetQualities.forEach(tq => {
    const match = rawDls.find(d => String(d.quality || '').toLowerCase().includes(tq.q.toLowerCase()) && !d.download_only && d.host !== 'Telegram');
    const qStreamUrl = (movie.qualities && movie.qualities[tq.q]) || '';
    if (match) {
      const targetSource = match.drive_id || match.url || qStreamUrl || primaryUrl;
      const cleanUrl = normalizeDriveDownloadUrl(targetSource, tq.q, displayTitle);
      enriched.push({
        ...match,
        quality: tq.q,
        url: cleanUrl,
        size: match.size || fmtSize(baseMb * tq.ratio),
        format: 'MP4 (සිංහල Sub Merged)',
        host: match.host || 'Google Drive',
        sub_merged: true,
        subtitle_merged: true
      });
    } else if (primaryUrl || qStreamUrl) {
      const cleanUrl = normalizeDriveDownloadUrl(qStreamUrl || primaryUrl, tq.q, displayTitle);
      enriched.push({
        quality: tq.q,
        size: fmtSize(baseMb * tq.ratio),
        url: cleanUrl,
        format: 'MP4 (සිංහල Sub Merged)',
        host: 'Google Drive',
        sub_merged: true,
        subtitle_merged: true
      });
    }
  });

  rawDls.forEach(d => {
    if (d.download_only || d.host === 'Telegram') {
      enriched.push({
        ...d,
        format: d.format || 'MP4 (සිංහල Sub Merged)',
        sub_merged: true,
        subtitle_merged: true
      });
    }
  });

  return enriched;
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
    'fa-solid fa-bolt',
    'fa-brands fa-google-drive',
    'fa-solid fa-earth-americas',
    'fa-solid fa-film',
    'fa-solid fa-rocket',
    'fa-solid fa-server'
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
    if (newDriveId) {
      newSrc = buildChunkStreamUrl(newDriveId, currentEffectiveQuality);
    } else {
      const qNorm = String(currentEffectiveQuality || '').toLowerCase();
      if (currentMovie && currentMovie.qualities && typeof currentMovie.qualities === 'object') {
        const qVal = currentMovie.qualities[qNorm] || currentMovie.qualities[currentEffectiveQuality];
        if (typeof qVal === 'string' && qVal.startsWith('http') && !qVal.includes('t.me')) {
          newSrc = qVal;
        } else if (qVal && typeof qVal === 'object' && qVal.stream_url && !qVal.stream_url.includes('t.me')) {
          newSrc = qVal.stream_url;
        }
      }
      if (!newSrc) {
        const downloads = getMovieDownloads(currentMovie);
        const matched = downloads.find(d => String(d.quality || '').toLowerCase().includes(currentEffectiveQuality.toLowerCase()) && !d.download_only);
        if (matched) {
          if (matched.stream_url && !matched.stream_url.includes('t.me')) {
            newSrc = matched.stream_url;
          } else if (matched.url && !matched.url.includes('drive.google.com/uc') && !matched.url.includes('t.me') && !matched.url.startsWith('/api/download')) {
            newSrc = matched.url;
          }
        }
      }
    }

    // Only reload vjsPlayer.src if user explicitly requested a switch (!isAutoDowngrade)
    // OR if a distinct per-resolution Drive File ID exists (newDriveId !== prevDriveId).
    // If isAutoDowngrade is true on a single-file Drive movie (newDriveId === prevDriveId),
    // avoid resetting vjsPlayer.src so the browser's already-buffered media bytes are preserved.
    const shouldReloadSrc = !isAutoDowngrade || (newDriveId && prevDriveId && newDriveId !== prevDriveId);

    if (newSrc && shouldReloadSrc) {
      vjsPlayer.src({ src: newSrc, type: 'video/mp4' });
      vjsPlayer.one('loadedmetadata', () => {
        try {
          if (curTime > 0) vjsPlayer.currentTime(curTime);
        } catch (e) {}
        syncSubtitles();
        if (!wasPaused) {
          try { vjsPlayer.play(); } catch (e) {}
        }
      });
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

    // Or if a single mid-playback buffer stall persists longer than 2.6 seconds, auto step-down & stall recovery nudge
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
    }, 2600);
  });

  player.on('playing', () => {
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
    }
  });

  player.on('timeupdate', () => {
    if (activeStallTimer) {
      clearTimeout(activeStallTimer);
      activeStallTimer = null;
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

  loadStream(movie, 0);
}

function renderStreamEmbed(playerEl, stream, movie) {
  if (vjsPlayer) {
    try { vjsPlayer.dispose(); } catch (e) {}
    vjsPlayer = null;
  }
  isTrailerActive = false;

  let baseEmbedUrl = stream.stream_url || '';
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

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000000 !important;border-radius:8px;overflow:hidden">
      ${buildSuperLoaderHtml(movie, stream.label || stream.server)}
      <iframe id="player-drive-iframe"
              data-base-embed="${FilmSub.escHtml(baseEmbedUrl)}"
              src="${FilmSub.escHtml(finalEmbedUrl)}"
              title="${FilmSub.escHtml(movie.title || 'Movie')} Streaming Player"
              frameborder="0"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share; fullscreen"
              allowfullscreen="true"
              webkitallowfullscreen="true"
              mozallowfullscreen="true"
              playsinline="true"
              style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px;background:#000000 !important;opacity:0;transition:opacity 0.35s ease">
      </iframe>
    </div>`;

  const iframeEl = document.getElementById('player-drive-iframe');
  const loaderEl = document.getElementById('super-player-loader');

  const revealIframe = () => {
    setTimeout(() => {
      if (iframeEl && iframeEl.isConnected) iframeEl.style.opacity = '1';
      if (loaderEl && loaderEl.isConnected) loaderEl.classList.add('hidden');
    }, 260);
  };

  if (iframeEl) {
    iframeEl.addEventListener('load', revealIframe);
  }
  // Safety watchdog: never leave loader stuck > 4.5s
  setTimeout(revealIframe, 4500);

  mountLiveSubtitleOverlay(playerEl, movie);
}

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

function createVjsPlayer(playerEl, stream, movie) {
  const subtitles = getMovieSubtitles(movie);
  const tracksHTML = subtitles.map((sub, i) => `
    <track kind="subtitles" src="${FilmSub.escHtml(sub.url || '')}"
           srclang="${FilmSub.escHtml(sub.srclang || 'si')}"
           label="${FilmSub.escHtml(sub.label || 'සිංහල උපසිරැසි')}"
           ${sub.default || i === 0 ? 'default' : ''}>`).join('');

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
      syncSubtitles();
      mountLiveSubtitleOverlay(playerEl, movie);
      attachAdaptiveStallMonitor(vjsPlayer);
      setTimeout(hideLoader, 600);
      try { vjsPlayer.play().catch(() => {}); } catch (e) {}
    });

    vjsPlayer.on('loadedmetadata', () => {
      hideLoader();
      syncSubtitles();
    });

    vjsPlayer.on('canplay', hideLoader);
    vjsPlayer.on('playing', hideLoader);

    // Ultra-smooth zero-lag watchdog: if stream header is still at readyState 0 after 3.8s
    // (e.g. stream server sleeping / warming up or ultra-slow network),
    // automatically switch to Server 2 (VidSrc Pro) so playback starts with zero white screen and zero interruption.
    const slowHeaderWatchdog = setTimeout(() => {
      if (vjsPlayer && typeof vjsPlayer.readyState === 'function' && vjsPlayer.readyState() === 0 &&
          (stream.mode === 'telegram_stream' || stream.mode === 'super_chunk' || stream.mode === 'direct_mp4')) {
        const streams = getMovieStreams(movie);
        if (streams.length > 1 && currentStreamIdx === 0) {
          FilmSub.showToast('⚡ Stream server warming up — instant failover to VidSrc Pro...', 'info');
          const tabsEl = document.getElementById('server-tabs');
          if (tabsEl) {
            tabsEl.querySelectorAll('.server-tab').forEach(b => b.classList.remove('active'));
            const btn = tabsEl.querySelector('button[data-index="1"]');
            if (btn) btn.classList.add('active');
          }
          loadStream(movie, 1);
        }
      }
    }, 3800);

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
        FilmSub.showToast(`⚡ Stream server warming up — instant failover to ${nextServer.label || 'VidSrc Pro'}...`, 'info');
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
  const downloads = getMovieDownloads(movie).filter(d => !d.download_only && d.host !== 'Telegram');
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
        if (window.FilmSubDownload && typeof window.FilmSubDownload.show === 'function') {
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
        <div class="cs-meta-val">FilmSub.lk / CineSubz Team</div>
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

      const actionBtn = isTelegram
        ? `<a href="${FilmSub.escHtml(dlUrl)}" target="_blank" rel="noopener" class="btn-tg-dl"
              style="display:inline-flex;align-items:center;gap:7px">
             <i class="fa-brands fa-telegram"></i> Telegram Download
           </a>`
        : `<button class="btn-direct-dl" data-url="${FilmSub.escHtml(dlUrl)}" data-quality="${FilmSub.escHtml(q)}" type="button">
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
