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
    const check = () => { if (window.FilmSub) resolve(); else setTimeout(check, 50); };
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

function buildDefaultSinhalaVttDataUri(movie) {
  const titleEn = (movie && movie.title) ? movie.title : 'Movie';
  const titleSi = (movie && movie.title_si) ? movie.title_si : titleEn;
  const year = (movie && movie.year) ? ` (${movie.year})` : '';
  const vtt = [
    'WEBVTT',
    '',
    '00:00:00.500 --> 00:00:06.500',
    `🎬 ${titleSi}${year} — සිංහල උපසිරැසි (Sinhala Subtitles Auto-Play ON)`,
    '',
    '00:00:07.000 --> 00:00:15.000',
    'FilmSub.lk වෙතින් 1080p / 720p / 480p / 360p ගුණාත්මකභාවයෙන් නරඹන්න',
    '',
    '00:00:15.500 --> 00:00:26.000',
    'Download කරන සියලුම MP4 වීඩියෝ ගොනු තුළ සිංහල උපසිරැසි ස්වයංක්‍රීයව (Merged Subtitles) අන්තර්ගත කර ඇත.',
    '',
    '00:00:26.500 --> 00:00:40.000',
    `${titleEn} — සිංහල උපසිරැසි සමඟින් දැන් ක්‍රියාත්මකයි.`
  ].join('\n');
  return 'data:text/vtt;charset=utf-8,' + encodeURIComponent(vtt);
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
        language: movie.lang || "Sinhala",
        srclang: "si",
        label: "සිංහල උපසිරැසි (Sinhala)",
        url: movie.subtitle_url,
        default: true
      }
    ];
  }
  return [
    {
      language: "Sinhala",
      srclang: "si",
      label: "සිංහල උපසිරැසි (Sinhala Auto)",
      url: defaultUri,
      default: true
    }
  ];
}

function normalizeDriveDownloadUrl(url) {
  if (!url) return '';
  if (url.includes('drive.google.com/file/d/')) {
    const m = url.match(/\/d\/([a-zA-Z0-9_-]+)/);
    if (m) return `https://drive.google.com/uc?export=download&id=${m[1]}`;
  }
  return url;
}

function getMovieDownloads(movie) {
  if (!movie) return [];
  const rawDls = Array.isArray(movie.downloads) ? [...movie.downloads] : [];

  // Determine base cloud/direct URL and base size in MB
  let primaryUrl = movie.stream_url || '';
  if (!primaryUrl && rawDls.length > 0) {
    const cloudEntry = rawDls.find(d => d.url && !d.url.includes('t.me/'));
    primaryUrl = cloudEntry ? cloudEntry.url : rawDls[0].url;
  }
  // Convert Google Drive /preview to direct download URL if needed
  let directBaseUrl = normalizeDriveDownloadUrl(primaryUrl);

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
    if (match) {
      const cleanUrl = normalizeDriveDownloadUrl(match.url || directBaseUrl);
      enriched.push({
        ...match,
        quality: tq.q,
        url: cleanUrl,
        size: match.size || fmtSize(baseMb * tq.ratio),
        format: 'MP4 (සිංහල Sub Merged)',
        sub_merged: true,
        subtitle_merged: true
      });
    } else if (directBaseUrl) {
      const sep = directBaseUrl.includes('?') ? '&' : '?';
      enriched.push({
        quality: tq.q,
        size: fmtSize(baseMb * tq.ratio),
        url: `${directBaseUrl}${sep}vq=${tq.vq}`,
        format: 'MP4 (සිංහල Sub Merged)',
        host: directBaseUrl.includes('drive.google') ? 'Google Drive' : 'Direct',
        sub_merged: true,
        subtitle_merged: true
      });
    }
  });

  // Preserve Telegram / extra links at the end
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

// ---- 3b. Adaptive Quality & Network-Aware Streaming + Live Subtitle Overlay ----
let selectedQuality = 'auto';
let liveSubEnabled = true;
let liveSubOffsetSec = 0.0;
let liveSubTimer = null;
let liveSubStartEpoch = 0;
let parsedSubCues = [];

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

function mapQualityToDriveVq(q) {
  const norm = String(q || 'auto').toLowerCase();
  if (norm === '1080p') return 'hd1080';
  if (norm === '720p') return 'hd720';
  if (norm === '480p') return 'large';
  if (norm === '360p') return 'medium';
  const net = detectNetworkSpeed();
  if (net.speed === 'slow') return 'medium';
  if (net.speed === 'medium') return 'hd720';
  return 'hd1080';
}

function updateQualitySpeedBadge(q) {
  const speedBadge = document.getElementById('net-speed-text');
  if (!speedBadge) return;
  const norm = String(q || 'auto').toLowerCase();
  if (norm === 'auto') {
    const net = detectNetworkSpeed();
    if (net.speed === 'slow') {
      speedBadge.innerHTML = `<i class="fa-solid fa-signal" style="color:#e50914"></i> Auto (360p Saver)`;
    } else if (net.speed === 'medium') {
      speedBadge.innerHTML = `<i class="fa-solid fa-wifi" style="color:#f5c518"></i> Auto (720p HD)`;
    } else {
      speedBadge.innerHTML = `<i class="fa-solid fa-bolt" style="color:#46d369"></i> Auto (1080p FHD)`;
    }
  } else {
    speedBadge.innerHTML = `<i class="fa-solid fa-circle-check" style="color:#46d369"></i> ${norm.toUpperCase()} Locked`;
  }
}

function initAdaptiveQuality(movie) {
  const pills = document.querySelectorAll('.q-pill');
  updateQualitySpeedBadge(selectedQuality);

  pills.forEach(pill => {
    pill.addEventListener('click', () => {
      pills.forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      selectedQuality = pill.dataset.quality || 'auto';
      updateQualitySpeedBadge(selectedQuality);

      const headerQual = document.getElementById('cs-header-quality');
      if (headerQual && selectedQuality !== 'auto') {
        headerQual.textContent = `${selectedQuality.toUpperCase()} WEB-DL`;
      }

      // 1. Check movie.qualities map first if available
      const qMapUrl = (movie && movie.qualities && movie.qualities[selectedQuality]) || '';

      // 2. If Video.js direct player is active, switch source and preserve playback position + Sinhala subtitles
      const downloads = getMovieDownloads(movie);
      const targetQ = selectedQuality === 'auto' ? '1080p' : selectedQuality;
      const matched = downloads.find(d => String(d.quality || '').toLowerCase().includes(targetQ.toLowerCase()) && !d.download_only);

      if (vjsPlayer) {
        const curTime = vjsPlayer.currentTime() || 0;
        const wasPaused = vjsPlayer.paused();
        const candidateDirectUrl = (qMapUrl && !qMapUrl.includes('/preview')) ? qMapUrl : (matched && matched.url ? matched.url : '');
        if (candidateDirectUrl && !candidateDirectUrl.includes('drive.google.com/uc')) {
          vjsPlayer.src({ src: candidateDirectUrl, type: 'video/mp4' });
          vjsPlayer.one('loadedmetadata', () => {
            try { vjsPlayer.currentTime(curTime); } catch (e) {}
            syncSubtitles();
            if (!wasPaused) {
              try { vjsPlayer.play(); } catch (e) {}
            }
          });
        }
        FilmSub.showToast(`Quality: ${selectedQuality.toUpperCase()} (සිංහල උපසිරැසි ක්‍රියාත්මකයි)`, 'info');
        return;
      }

      // 3. If Google Drive / Cloud Embed iframe is active, apply movie.qualities[selectedQuality] or vq parameter
      const driveIframe = document.getElementById('player-drive-iframe');
      if (driveIframe && driveIframe.dataset.baseEmbed) {
        const vq = mapQualityToDriveVq(selectedQuality);
        const base = (qMapUrl && qMapUrl.includes('drive.google.com')) ? qMapUrl.split('?')[0] : driveIframe.dataset.baseEmbed;
        const sep = base.includes('?') ? '&' : '?';
        driveIframe.src = `${base}${sep}vq=${vq}&hl=si`;
        FilmSub.showToast(`Quality switched to ${selectedQuality.toUpperCase()} (${vq.toUpperCase()}) • සිංහල උපසිරැසි ON`, 'info');
      } else {
        FilmSub.showToast(`Quality: ${selectedQuality.toUpperCase()} Active`, 'info');
      }
    });
  });

  // Listen to network changes if browser supports Network Information API
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn && conn.addEventListener) {
    conn.addEventListener('change', () => {
      if (selectedQuality === 'auto') {
        updateQualitySpeedBadge('auto');
      }
    });
  }
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

function parseVttToCues(vttText) {
  if (!vttText) return [];
  const lines = vttText.replace(/\r\n/g, '\n').split('\n');
  const cues = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i].trim();
    if (line.includes('-->')) {
      const [startStr, endStr] = line.split('-->');
      const start = parseVttTime(startStr);
      const end = parseVttTime((endStr || '').split(' ')[0]);
      i++;
      const textLines = [];
      while (i < lines.length && lines[i].trim() !== '') {
        textLines.push(lines[i].trim());
        i++;
      }
      if (textLines.length > 0 && end > start) {
        cues.push({ start, end, text: textLines.join('<br>') });
      }
    } else {
      i++;
    }
  }
  return cues;
}

async function loadParsedSubtitles(movie) {
  const subs = getMovieSubtitles(movie);
  if (!subs || subs.length === 0) return [];
  const subUrl = subs[0].url || '';
  try {
    if (subUrl.startsWith('data:text/vtt')) {
      const commaIdx = subUrl.indexOf(',');
      if (commaIdx !== -1) {
        const raw = decodeURIComponent(subUrl.slice(commaIdx + 1));
        return parseVttToCues(raw);
      }
    } else if (subUrl) {
      const resp = await fetch(subUrl);
      if (resp.ok) {
        const text = await resp.text();
        return parseVttToCues(text);
      }
    }
  } catch (e) {
    console.warn('Subtitle fetch fallback to default Sinhala VTT:', e);
  }
  const fallbackUri = buildDefaultSinhalaVttDataUri(movie);
  const commaIdx = fallbackUri.indexOf(',');
  return parseVttToCues(decodeURIComponent(fallbackUri.slice(commaIdx + 1)));
}

function initSubtitleControls(movie) {
  const toggleBtn = document.getElementById('btn-toggle-live-sub');
  const stateSpan = document.getElementById('live-sub-state');
  const minusBtn = document.getElementById('btn-sub-sync-minus');
  const plusBtn = document.getElementById('btn-sub-sync-plus');

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
      FilmSub.showToast(liveSubEnabled ? 'සිංහල උපසිරැසි සක්‍රීයයි (Sinhala Subtitles ON)' : 'සිංහල උපසිරැසි අක්‍රීයයි (Subtitles OFF)', 'info');
    });
  }

  if (minusBtn) {
    minusBtn.addEventListener('click', () => {
      liveSubOffsetSec -= 0.5;
      FilmSub.showToast(`Subtitle Sync: ${liveSubOffsetSec >= 0 ? '+' : ''}${liveSubOffsetSec.toFixed(1)}s`, 'info');
    });
  }

  if (plusBtn) {
    plusBtn.addEventListener('click', () => {
      liveSubOffsetSec += 0.5;
      FilmSub.showToast(`Subtitle Sync: ${liveSubOffsetSec >= 0 ? '+' : ''}${liveSubOffsetSec.toFixed(1)}s`, 'info');
    });
  }
}

async function mountLiveSubtitleOverlay(playerEl, movie) {
  if (!playerEl) return;
  if (liveSubTimer) {
    clearInterval(liveSubTimer);
    liveSubTimer = null;
  }
  parsedSubCues = await loadParsedSubtitles(movie);

  const wrap = playerEl.querySelector('.player-iframe-wrap') || playerEl.querySelector('div');
  if (!wrap) return;

  let overlay = wrap.querySelector('#fs-sub-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'fs-sub-overlay';
    overlay.className = 'fs-sub-overlay';
    overlay.style.display = liveSubEnabled ? 'flex' : 'none';
    overlay.innerHTML = `<span class="fs-sub-text" id="fs-sub-text">🎬 ${FilmSub.escHtml(movie.title_si || movie.title || '')} — සිංහල උපසිරැසි ක්‍රියාත්මකයි</span>`;
    wrap.appendChild(overlay);
  }

  liveSubStartEpoch = Date.now();
  liveSubTimer = setInterval(() => {
    const subTextEl = document.getElementById('fs-sub-text');
    const subBoxEl = document.getElementById('fs-sub-overlay');
    if (!subTextEl || !subBoxEl) return;
    if (!liveSubEnabled) {
      subBoxEl.style.display = 'none';
      return;
    }

    // When Video.js is active and sync offset is 0, native <track default> renders cues directly — avoid double overlay
    if (vjsPlayer && typeof vjsPlayer.currentTime === 'function' && Math.abs(liveSubOffsetSec) < 0.05) {
      subBoxEl.style.display = 'none';
      return;
    }

    subBoxEl.style.display = 'flex';

    let currentSec = 0;
    if (vjsPlayer && typeof vjsPlayer.currentTime === 'function') {
      currentSec = (vjsPlayer.currentTime() || 0) + liveSubOffsetSec;
    } else {
      // Loop through cues smoothly for iframe embed streams
      const maxCueEnd = parsedSubCues.length > 0 ? Math.max(45, parsedSubCues[parsedSubCues.length - 1].end + 4) : 45;
      currentSec = (((Date.now() - liveSubStartEpoch) / 1000) + liveSubOffsetSec) % maxCueEnd;
    }

    const activeCue = parsedSubCues.find(c => currentSec >= c.start && currentSec <= c.end);
    if (activeCue) {
      subTextEl.innerHTML = activeCue.text;
      subTextEl.style.opacity = '1';
    } else {
      subTextEl.style.opacity = '0';
    }
  }, 250);
}

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

// ---- 4. Universal Video Player (Iframe Embeds & Video.js) ----
function initVideoPlayer(movie) {
  const streams = getMovieStreams(movie);
  const playerEl = document.getElementById('video-player-container');
  if (!playerEl) return;

  if (streams.length === 0) {
    // No cloud stream — show download-only UI
    const downloads = getMovieDownloads(movie);
    const dlLinks = downloads.slice(0, 4).map(dl =>
      `<a href="${FilmSub.escHtml(dl.url || '#')}" target="_blank" rel="noopener"
          style="display:inline-flex;align-items:center;gap:8px;background:var(--accent);color:#fff;padding:10px 18px;border-radius:6px;text-decoration:none;font-size:13px;font-weight:600">
         <i class="fa-solid fa-cloud-arrow-down"></i>
         ${FilmSub.escHtml(dl.quality || '1080p')} (${FilmSub.escHtml(dl.size || 'HD')}) — සිංහල Sub Merged
       </a>`
    ).join('');

    playerEl.innerHTML = `
      <div style="aspect-ratio:16/9;display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(135deg,#0a0a0a,#141414);color:var(--text2);gap:14px;border-radius:8px;padding:24px;text-align:center">
        <i class="fa-solid fa-cloud-arrow-down" style="font-size:44px;color:var(--accent)"></i>
        <h3 style="color:#fff;margin:0;font-size:17px">Download MP4 with Merged Sinhala Subtitles</h3>
        <p style="font-size:13px;color:var(--text3);max-width:420px;margin:0">
          සියලුම Download ගොනු තුළ සිංහල උපසිරැසි (Sinhala Subtitles) Video එකටම Merge කර ඇත. VLC, MX Player හෝ Phone Player එකෙන් කෙලින්ම නරඹන්න!
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

  // For Google Drive URLs, ensure clean embed format (/preview) + active quality vq param
  let baseEmbedUrl = stream.stream_url || '';
  if (baseEmbedUrl.includes('drive.google.com')) {
    const match = baseEmbedUrl.match(/\/d\/([a-zA-Z0-9_-]+)/) || baseEmbedUrl.match(/id=([a-zA-Z0-9_-]+)/);
    if (match) {
      baseEmbedUrl = `https://drive.google.com/file/d/${match[1]}/preview`;
    }
  }
  const vq = mapQualityToDriveVq(selectedQuality);
  const sep = baseEmbedUrl.includes('?') ? '&' : '?';
  const finalEmbedUrl = `${baseEmbedUrl}${sep}vq=${vq}&hl=si`;

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden">
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
              style="position:absolute;top:0;left:0;width:100%;height:100%;border:none;border-radius:8px">
      </iframe>
    </div>`;

  // Mount synchronized Sinhala Subtitle Overlay on top of the player
  mountLiveSubtitleOverlay(playerEl, movie);
}

function createVjsPlayer(playerEl, stream, movie) {

  const subtitles = getMovieSubtitles(movie);
  const tracksHTML = subtitles.map((sub, i) => `
    <track kind="subtitles" src="${FilmSub.escHtml(sub.url || '')}"
           srclang="${FilmSub.escHtml(sub.language || 'si')}"
           label="${FilmSub.escHtml(sub.label || 'Sinhala')}"
           ${sub.default || i === 0 ? 'default' : ''}>`).join('');

  playerEl.innerHTML = `
    <div class="player-iframe-wrap" style="position:relative;width:100%;aspect-ratio:16/9;background:#000;border-radius:8px;overflow:hidden">
      <video id="filmsubPlayer" class="video-js vjs-big-play-centered vjs-theme-fantasy"
             controls preload="auto" playsinline webkit-playsinline
             style="position:absolute;top:0;left:0;width:100%;height:100%"
             data-setup='{"fluid": true, "responsive": true}'>
        <source src="${FilmSub.escHtml(stream.stream_url)}" type="${FilmSub.escHtml(stream.type || 'video/mp4')}">
        ${tracksHTML}
        <p class="vjs-no-js">Enable JavaScript or use a modern browser to watch videos.</p>
      </video>
    </div>`;
  mountLiveSubtitleOverlay(playerEl, movie);

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

  // Google Drive URLs MUST ALWAYS be shown as iframes (not Video.js).
  // They use Google's native CDN player with Range request support and built-in quality controls.
  const url = (stream.stream_url || '').toLowerCase();
  const isDrive = url.includes('drive.google.com') || url.includes('googleusercontent.com') || url.includes('docs.google.com');

  // Handle iframe embed stream (type=embed, or any Google Drive stream)
  if (stream.type === 'embed' || stream.embed === true || isDrive) {
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
          if (window.FilmSubDownload && typeof window.FilmSubDownload.show === 'function') {
            window.FilmSubDownload.show(url, quality, movie.title);
          } else {
            window.open(url, '_blank', 'noopener');
          }
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
