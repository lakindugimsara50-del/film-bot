/* ============================================================
   FilmSub – app.js | Main JavaScript
   ============================================================ */

'use strict';

// ---- Config ----
const SITE_CONFIG = {
  moviesPath: 'data/movies.json',
  defaultPoster: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='450' viewBox='0 0 300 450'%3E%3Crect width='300' height='450' fill='%231f1f1f'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23666' font-family='sans-serif' font-size='16'%3ENo Poster%3C/text%3E%3C/svg%3E",
  defaultBackdrop: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' viewBox='0 0 1280 720'%3E%3Crect width='1280' height='720' fill='%23141414'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23333' font-family='sans-serif' font-size='24'%3EFilmZilla%3C/text%3E%3C/svg%3E",
  moviePage: 'movie.html',
  searchPage: 'search.html',
};

// ---- State ----
let allMovies = [];
let siteData = {};
let heroSpotlightMovies = [];
let heroCurrentIdx = 0;
let heroRotateTimer = null;
let heroTrailerTimer = null;
let heroTrailerActive = false;
let heroTrailerMuted = true;
let heroHoverListenersBound = false;
const HERO_ROTATE_INTERVAL = 6000;

// ---- Init ----
function renderAllSections() {
  if (document.getElementById('hero-section')) {
    if (document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck')) {
      renderHeroShowcase();
    } else {
      renderHero();
      renderHeroAppleIndicators();
    }
  }
  if (document.getElementById('continue-track')) renderContinueWatchingShelf();
  if (document.getElementById('trending-track')) renderCarousel('trending-track', getTrending(), { isTrending: true });
  if (document.getElementById('teasers-preview-rail')) renderTeasersPreviewShelf();
  if (document.getElementById('for-you-track')) renderForYouShelf();
  if (document.getElementById('all-movies-grid')) initMoviesShowcaseGrid();
  if (document.getElementById('new-releases-track')) renderCarousel('new-releases-track', getNewReleases(), { isTrending: false });
  if (document.getElementById('tv-series-track')) renderCarousel('tv-series-track', getAllTVSeries(), { isTrending: false });
  if (document.getElementById('action-track')) renderCarousel('action-track', getMoviesByGenre('action'), { isTrending: false });
  if (document.getElementById('comedy-track')) renderCarousel('comedy-track', getMoviesByGenre('comedy'), { isTrending: false });
  if (document.getElementById('drama-track')) renderCarousel('drama-track', getMoviesByGenre('drama'), { isTrending: false });
  if (document.getElementById('scifi-track')) renderCarousel('scifi-track', getMoviesByGenre('sci-fi'), { isTrending: false });
  if (document.getElementById('animation-track')) renderCarousel('animation-track', getMoviesByGenre('animation'), { isTrending: false });
  if (document.getElementById('sinhala-dub-track')) renderCarousel('sinhala-dub-track', getSinhalaFilms(), { isTrending: false });
  setupCarouselArrows();
  initHighlightPopup();
  initTrailerModalListeners();
  initScrollReveal();
}

function renderTeasersPreviewShelf() {
  const rail = document.getElementById('teasers-preview-rail');
  if (!rail) return;
  const teasers = window.FILMSUB_TEASERS || [];
  if (!teasers.length) {
    const shelf = document.getElementById('teasers-shelf-section');
    if (shelf) shelf.style.display = 'none';
    return;
  }
  const displayTeasers = teasers.slice(0, 16);
  rail.innerHTML = displayTeasers.map(t => {
    return `
      <a href="teasers.html?id=${encodeURIComponent(t.movie_id)}" class="teaser-mini-card" title="Watch 30s teaser: ${escapeAttr(t.title)}">
        <img src="${t.poster || t.backdrop}" alt="${escapeAttr(t.title)}" class="teaser-mini-poster" loading="lazy">
        <div class="teaser-mini-overlay">
          <div class="teaser-mini-badge">
            <i class="fa-solid fa-fire"></i>
            <span>30s Teaser</span>
          </div>
          <div class="teaser-mini-play">
            <i class="fa-solid fa-play"></i>
          </div>
          <div class="teaser-mini-info">
            <h4 class="teaser-mini-title">${escapeHtml(t.title)}</h4>
            <div class="teaser-mini-meta">★ ${t.imdb || '7.5'} • ${(t.genres || []).slice(0, 1).join('')}</div>
          </div>
        </div>
      </a>
    `;
  }).join('');
}

function initApp() {
  // 1. Instantly populate from local window.FILMSUB_DATA and render immediately
  try {
    loadBaselineMovies();
    renderAllSections();
  } catch (err) {
    console.error('Error rendering baseline:', err);
  }

  // 2. Init header, search, nav with safe isolation
  try { initHeader(); } catch (e) { console.error('initHeader error:', e); }
  try { initUserUI(); } catch (e) { console.error('initUserUI error:', e); }
  try { initGlobalCinemaDrawer(); } catch (e) { console.error('initGlobalCinemaDrawer error:', e); }
  try { initSearchOverlay(); } catch (e) { console.error('initSearchOverlay error:', e); }
  try { initMobileBottomNav(); } catch (e) { console.error('initMobileBottomNav error:', e); }

  // 3. Fetch remote updates in background without blocking rendering
  fetchRemoteMovies().then(updated => {
    if (updated) renderAllSections();
  }).catch(() => {});

  // 4. Background live updates: auto-check for newly uploaded movies on visibility & periodically
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      fetchRemoteMovies().then(updated => {
        if (updated) renderAllSections();
      }).catch(() => {});
    }
  });
  setInterval(() => {
    if (document.visibilityState === 'visible') {
      fetchRemoteMovies().then(updated => {
        if (updated) renderAllSections();
      }).catch(() => {});
    }
  }, 90000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

// ---- Load Movies ----
function _isValidMovieEntry(m) {
  if (!m) return false;
  return Boolean(m.title || m.slug || m.id);
}

function loadBaselineMovies() {
  const data = window.FILMZILLA_DATA || window.FILMSUB_DATA;
  if (data && Array.isArray(data.movies)) {
    siteData = data.site || {};
    const valid = data.movies.filter(_isValidMovieEntry);
    allMovies = valid.length > 0 ? valid : data.movies;
  }
}

async function fetchRemoteMovies() {
  const cacheBuster = `?_t=${Date.now()}&_r=${Math.random()}`;
  const localUrl = `${SITE_CONFIG.moviesPath}${cacheBuster}`;
  const remoteUrl = `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json${cacheBuster}`;

  for (const url of [localUrl, remoteUrl]) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 12000);
      const res = await fetch(url, { cache: 'no-store', signal: controller.signal });
      clearTimeout(timer);
      if (res.ok) {
        const data = await res.json();
        const moviesList = Array.isArray(data) ? data : (data.movies || []);
        if (Array.isArray(moviesList) && moviesList.length > 0) {
          const validRemote = moviesList.filter(_isValidMovieEntry);
          if (validRemote.length > 0) {
            siteData = data.site || siteData;
            const seenSlugs = new Set();
            const merged = [];
            const addMovies = (arr) => {
              if (!Array.isArray(arr)) return;
              for (const m of arr) {
                if (!m) continue;
                const s = String(m.slug || m.id || '').toLowerCase();
                if (s && !seenSlugs.has(s)) {
                  seenSlugs.add(s);
                  merged.push(m);
                }
              }
            };
            addMovies(validRemote);
            addMovies(allMovies);
            if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) addMovies(window.FILMSUB_DATA.movies);
            allMovies = merged;
            if (window.FILMSUB_DATA) window.FILMSUB_DATA.movies = merged;
            if (window.FILMZILLA_DATA) window.FILMZILLA_DATA.movies = merged;
            return true;
          }
        }
      }
    } catch (e) {
      // Continue to next source
    }
  }
  return false;
}

async function loadMovies() {
  loadBaselineMovies();
  await fetchRemoteMovies();
}

// ---- Group Series Catalog (Show only ONE card per TV Series on Catalog) ----
function groupSeriesCatalog(movies) {
  if (!Array.isArray(movies)) return [];
  const seenSeries = new Map();
  const result = [];

  for (const m of movies) {
    const isSeries = m.type === 'series' || m.season || m.episode || (m.slug && m.slug.match(/[-_.]s\d+[-_.]?e\d+/i));
    if (!isSeries) {
      result.push(m);
      continue;
    }

    const baseTitle = (m.title || '').replace(/\s*[-:]?\s*Season\s*\d+.*$/i, '').trim().toLowerCase();
    const key = (m.tmdb_id && String(m.tmdb_id)) ? `tmdb_${m.tmdb_id}` : baseTitle;

    if (!seenSeries.has(key)) {
      const master = { ...m };
      master.title = (m.title || '').replace(/\s*[-:]?\s*Season\s*\d+.*$/i, '').trim();
      master.type = 'series';
      master.is_series_master = true;
      seenSeries.set(key, master);
      result.push(master);
    }
  }
  return result;
}

// ---- Extra Catalog Helpers ----
function getAllTVSeries() {
  const catalog = groupSeriesCatalog(allMovies);
  return catalog.filter(m => m.type === 'series' || m.is_series_master || Boolean(m.season)).sort((a, b) => {
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    if (yB !== yA) return yB - yA;
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    return timeB - timeA;
  }).slice(0, 20);
}
function getMoviesByGenre(keyword) {
  const catalog = groupSeriesCatalog(allMovies);
  const kw = String(keyword).toLowerCase();
  return catalog.filter(m => {
    const list = Array.isArray(m.genres) ? m.genres : [];
    return list.some(g => String(g).toLowerCase().includes(kw));
  }).sort((a, b) => {
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    if (yB !== yA) return yB - yA;
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    return timeB - timeA;
  }).slice(0, 20);
}

function _getMovieTimestamp(m) {
  if (!m) return 0;
  if (m.added_at) {
    const t = new Date(m.added_at).getTime();
    if (!isNaN(t) && t > 0) return t;
  }
  if (m.added_date) {
    const t = new Date(m.added_date).getTime();
    if (!isNaN(t) && t > 0) return t;
  }
  const yr = Number(m.year) || 2024;
  return new Date(yr, 0, 1).getTime();
}

function _getMovieRating(m) {
  if (!m) return 0;
  const r = parseFloat(m.imdb || m.rating || 0);
  return isNaN(r) ? 0 : r;
}

// ---- Data Helpers ----
function getFeatured() {
  const spotlight = getHeroSpotlightList();
  if (spotlight && spotlight.length > 0) return spotlight[0];
  const catalog = groupSeriesCatalog(allMovies);
  return catalog.find(m => m.featured) || catalog[0] || null;
}
function getNewReleases() {
  const catalog = groupSeriesCatalog(allMovies);
  return [...catalog].sort((a, b) => {
    const timeA = _getMovieTimestamp(a);
    const timeB = _getMovieTimestamp(b);
    if (timeB !== timeA) return timeB - timeA;
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    return yB - yA;
  }).slice(0, 20);
}
function getTrending() {
  const catalog = groupSeriesCatalog(allMovies);
  const tr = catalog.filter(m => m.trending);
  const list = tr.length > 0 ? tr : catalog;
  return [...list].sort((a, b) => {
    const timeA = _getMovieTimestamp(a);
    const timeB = _getMovieTimestamp(b);
    if (timeB !== timeA) return timeB - timeA;
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    return yB - yA;
  }).slice(0, 20);
}
function getSinhalaFilms() {
  const catalog = groupSeriesCatalog(allMovies);
  const sf = catalog.filter(m => (Array.isArray(m.subtitles) && m.subtitles.length > 0) || m.subtitle_url || m.has_sinhala_sub);
  const list = sf.length > 0 ? sf : catalog;
  return [...list].sort((a, b) => {
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    if (yB !== yA) return yB - yA;
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    return timeB - timeA;
  }).slice(0, 20);
}
function getRelated(movie) {
  if (!movie) return [];
  const catalog = groupSeriesCatalog(allMovies);
  const genres = Array.isArray(movie.genres) ? movie.genres : [];
  return catalog.filter(m => m.slug !== movie.slug && Array.isArray(m.genres) && m.genres.some(g => genres.includes(g))).slice(0, 12);
}
function findMovieBySlug(slug) {
  if (!slug) return null;
  const s = decodeURIComponent(String(slug)).trim().replace(/\/+$/, '').toLowerCase();
  const clean = s.replace(/-sinhala(-sub|-subtitles)?$/, '');

  // 1. Exact match by slug or id
  let found = allMovies.find(m => {
    const mSlug = (m.slug || '').toLowerCase();
    const mId = String(m.id || '').toLowerCase();
    const mClean = mSlug.replace(/-sinhala(-sub|-subtitles)?$/, '');
    return mSlug === s || mId === s || mSlug === clean || mId === clean || mClean === s || mClean === clean;
  });
  if (found) return found;

  // Extract requested season and episode if specified in slug
  const epMatch = clean.match(/[-_.]s(\d+)[-_.]?e(\d+)/i) || clean.match(/[-_.](\d+)x(\d+)/i);
  const reqSeason = epMatch ? parseInt(epMatch[1], 10) : null;
  const reqEpisode = epMatch ? parseInt(epMatch[2], 10) : null;

  // 1b. Series episode specific match: match series base slug + exact season/episode
  const baseEpSlug = clean.replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '');
  if (baseEpSlug && baseEpSlug !== clean) {
    const normBase = baseEpSlug.replace(/[^a-z0-9]/g, '');
    if (reqSeason !== null && reqEpisode !== null) {
      found = allMovies.find(m => {
        if (parseInt(m.season, 10) !== reqSeason || parseInt(m.episode, 10) !== reqEpisode) return false;
        const mSlug = (m.slug || '').toLowerCase().replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
        const mId = String(m.id || '').toLowerCase().replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
        return mSlug === normBase || mId === normBase || (normBase && (mSlug.startsWith(normBase) || normBase.startsWith(mSlug)));
      });
      if (found) return found;
    }
  }

  // If a specific episode was explicitly requested (e.g. s01e02) and not found in local catalog,
  // do NOT match a conflicting episode (e.g. s01e01) via loose rules. Return null so caller can fetch remote movies.json.
  if (reqSeason !== null && reqEpisode !== null) {
    return null;
  }

  // 1c. Series base slug match for non-episode queries (e.g. "game-of-thrones" matching first episode or master)
  if (baseEpSlug && baseEpSlug !== clean) {
    const normBase = baseEpSlug.replace(/[^a-z0-9]/g, '');
    found = allMovies.find(m => {
      const mSlug = (m.slug || '').toLowerCase().replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
      const mId = String(m.id || '').toLowerCase().replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[^a-z0-9]/g, '');
      return mSlug === normBase || mId === normBase || (normBase && (mSlug.startsWith(normBase) || normBase.startsWith(mSlug)));
    });
    if (found) return found;
  }

  // 2. Prefix / Episode match (e.g. game-of-thrones-2011-s01e01 matches game-of-thrones)
  const normS = clean.replace(/[^a-z0-9]/g, '');
  found = allMovies.find(m => {
    const mSlug = (m.slug || '').toLowerCase().replace(/-sinhala(-sub|-subtitles)?$/, '');
    const mId = String(m.id || '').toLowerCase();
    const normMSlug = mSlug.replace(/[^a-z0-9]/g, '');
    const normMId = mId.replace(/[^a-z0-9]/g, '');
    return (normMSlug && (normMSlug.startsWith(normS) || normS.startsWith(normMSlug))) ||
           (normMId && (normMId.startsWith(normS) || normS.startsWith(normMId)));
  });
  if (found) return found;

  // 3. Title match fallback
  const byTitle = allMovies.find(m => {
    const t = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    return t && (t === normS || normS.startsWith(t) || t.startsWith(normS));
  });
  if (byTitle) return byTitle;

  // 4. Built-in deep-link / verification fallback catalog (never pollutes homepage carousels)
  const fallbackCatalog = {
    'gameofthrones': {
      id: 'game-of-thrones-2011-s01e01',
      slug: 'game-of-thrones-2011-s01e01',
      title: 'Game of Thrones',
      title_si: 'ගේම් ඔෆ් ත්‍රෝන්ස්',
      year: 2011,
      imdb: '9.2',
      imdb_id: 'tt0944947',
      tmdb_id: '1399',
      type: 'series',
      quality: '1080p',
      number_of_seasons: 8,
      number_of_episodes: 73,
      poster: 'https://image.tmdb.org/t/p/w500/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg',
      backdrop: 'https://image.tmdb.org/t/p/original/zZqpAXxVSBtxV9qPBcscfXBcL2w.jpg',
      description: 'Seven noble families fight for control of the mythical land of Westeros. Friction between the houses leads to full-scale war. All while a very ancient evil awakens in the farthest north.'
    },
    'gameofthrones2011': {
      id: 'game-of-thrones-2011-s01e01',
      slug: 'game-of-thrones-2011-s01e01',
      title: 'Game of Thrones',
      title_si: 'ගේම් ඔෆ් ත්‍රෝන්ස්',
      year: 2011,
      imdb: '9.2',
      imdb_id: 'tt0944947',
      tmdb_id: '1399',
      type: 'series',
      quality: '1080p',
      number_of_seasons: 8,
      number_of_episodes: 73,
      poster: 'https://image.tmdb.org/t/p/w500/1XS1oqL89opfnbLl8WnZY1O1uJx.jpg',
      backdrop: 'https://image.tmdb.org/t/p/original/zZqpAXxVSBtxV9qPBcscfXBcL2w.jpg',
      description: 'Seven noble families fight for control of the mythical land of Westeros. Friction between the houses leads to full-scale war. All while a very ancient evil awakens in the farthest north.'
    },
    'interstellar': {
      id: 'interstellar-2014',
      slug: 'interstellar-2014',
      title: 'Interstellar',
      title_si: 'ඉන්ටර්ස්ටෙලර්',
      year: 2014,
      imdb: '8.7',
      imdb_id: 'tt0816692',
      tmdb_id: '157336',
      type: 'movie',
      quality: '1080p',
      duration: '169 min',
      genres: ['Sci-Fi', 'Adventure', 'Drama'],
      poster: 'https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg',
      backdrop: 'https://image.tmdb.org/t/p/original/xJHokMbljvjADYdit5fK5VQsXEG.jpg',
      description: 'The adventures of a group of explorers who make use of a newly discovered wormhole to surpass the limitations on human space travel.',
      stream_url: 'assets/sample_stream.mp4',
      streams: [
        { server: 'Server 1', label: '🎬 Super Player (1080p Full HD • Auto Sub)', type: 'video/mp4', stream_url: 'assets/sample_stream.mp4' }
      ],
      downloads: [
        { quality: '1080p', size: '1.45 GB', url: 'assets/sample_stream.mp4', format: 'MP4', host: 'Direct', subtitle_merged: true }
      ]
    },
    'interstellar2014': {
      id: 'interstellar-2014',
      slug: 'interstellar-2014',
      title: 'Interstellar',
      title_si: 'ඉන්ටර්ස්ටෙලර්',
      year: 2014,
      imdb: '8.7',
      imdb_id: 'tt0816692',
      tmdb_id: '157336',
      type: 'movie',
      quality: '1080p',
      duration: '169 min',
      genres: ['Sci-Fi', 'Adventure', 'Drama'],
      poster: 'https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIx.jpg',
      backdrop: 'https://image.tmdb.org/t/p/original/xJHokMbljvjADYdit5fK5VQsXEG.jpg',
      description: 'The adventures of a group of explorers who make use of a newly discovered wormhole to surpass the limitations on human space travel.',
      stream_url: 'assets/sample_stream.mp4',
      streams: [
        { server: 'Server 1', label: '🎬 Super Player (1080p Full HD • Auto Sub)', type: 'video/mp4', stream_url: 'assets/sample_stream.mp4' }
      ],
      downloads: [
        { quality: '1080p', size: '1.45 GB', url: 'assets/sample_stream.mp4', format: 'MP4', host: 'Direct', subtitle_merged: true }
      ]
    },
    'irumudi2026': {
      id: 'irumudi-2026',
      slug: 'irumudi-2026',
      title: 'Irumudi',
      title_si: 'ඉරුමුඩි',
      year: 2026,
      imdb: '8.1',
      imdb_id: 'tt31000001',
      tmdb_id: '1300001',
      type: 'movie',
      quality: '1080p',
      duration: '148 min',
      genres: ['Action', 'Drama', 'Thriller'],
      poster: 'https://image.tmdb.org/t/p/w500/niQ4NBh2jqAf1hDZP5m6ReWFAb7.jpg',
      backdrop: 'https://image.tmdb.org/t/p/original/8giIQcHpxgsPVP6c7aQtHl3txuh.jpg',
      description: 'An action-packed thriller with Sinhala subtitles.',
      stream_url: 'assets/sample_stream.mp4',
      streams: [
        { server: 'Server 1', label: '🎬 Super Player (1080p Full HD • Auto Sub)', type: 'video/mp4', stream_url: 'assets/sample_stream.mp4' }
      ],
      downloads: [
        { quality: '1080p', size: '1.35 GB', url: 'assets/sample_stream.mp4', format: 'MP4', host: 'Direct', subtitle_merged: true }
      ]
    },
    'inception2010': {
      id: 'inception-2010',
      slug: 'inception-2010',
      title: 'Inception',
      title_si: 'ඉන්සෙප්ෂන්',
      year: 2010,
      imdb: '8.8',
      imdb_id: 'tt1375666',
      tmdb_id: '27205',
      type: 'movie',
      quality: '1080p',
      duration: '148 min',
      genres: ['Action', 'Sci-Fi', 'Adventure'],
      poster: 'https://image.tmdb.org/t/p/w500/oYuLEt3zVCKq57qu2F8dT7NIa6f.jpg',
      backdrop: 'https://image.tmdb.org/t/p/original/8ZTVqvKDQ8emSGUEMjsS4yHAwrp.jpg',
      description: 'Cobb, a skilled thief who commits corporate espionage by infiltrating the subconscious of his targets is offered a chance to regain his old life.',
      stream_url: 'https://vidsrc.to/embed/movie/tt1375666',
      streams: [
        { server: 'Server 1', label: '🌐 VIP Player 1 (VidSrc Embed)', type: 'embed', embed: true, stream_url: 'https://vidsrc.to/embed/movie/tt1375666' }
      ],
      downloads: [
        { quality: '1080p', size: '1.48 GB', url: 'https://vidsrc.to/embed/movie/tt1375666', format: 'MP4', host: 'Direct', subtitle_merged: true }
      ]
    }
  };
  return fallbackCatalog[normS] || null;
}


function isLowDataMode() {
  if (typeof navigator === 'undefined') return false;
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (conn && (conn.saveData === true || conn.effectiveType === '2g' || conn.effectiveType === 'slow-2g')) {
    return true;
  }
  if (typeof window !== 'undefined' && window.innerWidth <= 768) return true;
  return false;
}

function getMovieTrailerEmbedUrl(movie, autoplay = true, muted = true) {
  if (!movie) return '';
  const ap = autoplay ? 1 : 0;
  const m = muted ? 1 : 0;
  const origin = typeof window !== 'undefined' && window.location && window.location.origin ? encodeURIComponent(window.location.origin) : '';
  const originParam = origin ? `&origin=${origin}` : '';

  if (movie.trailer_id) {
    const tid = encodeURIComponent(movie.trailer_id);
    return `https://www.youtube-nocookie.com/embed/${tid}?autoplay=${ap}&mute=${m}&controls=0&loop=1&playlist=${tid}&playsinline=1&rel=0&modestbranding=1&enablejsapi=1&iv_load_policy=3${originParam}`;
  }
  if (movie.trailer_url) {
    const sep = movie.trailer_url.includes('?') ? '&' : '?';
    return `${movie.trailer_url}${sep}autoplay=${ap}&mute=${m}&controls=0&loop=1&playsinline=1&rel=0&modestbranding=1&enablejsapi=1&iv_load_policy=3${originParam}`;
  }
  return '';
}

// ---- Official Cinema Trailer Modal ----
function openTrailerModal(movie) {
  if (!movie) return;
  const modal = document.getElementById('trailer-modal');
  const iframe = document.getElementById('trailer-modal-iframe');
  const titleEl = document.getElementById('trailer-modal-title');
  const fallbackBox = document.getElementById('trailer-modal-fallback');
  const fallbackLink = document.getElementById('trailer-fallback-link');

  if (!modal) return;

  const title = movie.title || 'Movie';
  if (titleEl) {
    titleEl.innerHTML = `<i class="fa-solid fa-clapperboard" style="color:var(--accent)"></i> ${title} - Official Trailer`;
  }

  let trailerId = movie.trailer_id;
  if (!trailerId && movie.trailer_url) {
    const match = movie.trailer_url.match(/(?:embed\/|v=|\/)([a-zA-Z0-9_-]{11})/);
    if (match) trailerId = match[1];
  }

  if (trailerId) {
    if (iframe) {
      iframe.src = `https://www.youtube-nocookie.com/embed/${trailerId}?autoplay=1&controls=1&rel=0&modestbranding=1&enablejsapi=1`;
      iframe.style.display = 'block';
    }
    if (fallbackBox) {
      fallbackBox.style.display = 'none';
      if (fallbackLink) {
        fallbackLink.href = `https://www.youtube.com/watch?v=${trailerId}`;
      }
    }
  } else {
    if (iframe) {
      iframe.style.display = 'none';
      iframe.src = 'about:blank';
    }
    if (fallbackBox) {
      fallbackBox.style.display = 'block';
      if (fallbackLink) {
        fallbackLink.href = `https://www.youtube.com/results?search_query=${encodeURIComponent(title + ' ' + (movie.year || '') + ' official trailer')}`;
      }
    }
  }

  modal.style.display = 'flex';
  modal.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';
}

function closeTrailerModal() {
  const modal = document.getElementById('trailer-modal');
  const iframe = document.getElementById('trailer-modal-iframe');
  if (modal) {
    modal.style.display = 'none';
    modal.setAttribute('aria-hidden', 'true');
  }
  if (iframe) {
    iframe.src = 'about:blank';
  }
  document.body.style.overflow = '';
}

function initTrailerModalListeners() {
  const modal = document.getElementById('trailer-modal');
  const closeBtn = document.getElementById('trailer-modal-close');
  if (closeBtn) {
    closeBtn.onclick = (e) => {
      e.preventDefault();
      closeTrailerModal();
    };
  }
  if (modal) {
    modal.onclick = (e) => {
      if (e.target === modal) {
        closeTrailerModal();
      }
    };
  }
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeTrailerModal();
    }
  });
}

function stopHeroTrailer() {
  // Kept for backward compatibility
  const videoWrap = document.getElementById('hero-video-wrap');
  const backdrop = document.getElementById('hero-backdrop');
  if (videoWrap) videoWrap.classList.remove('active');
  if (backdrop) backdrop.style.opacity = '1';
}

function getHeroSpotlightList() {
  let catalog = groupSeriesCatalog(allMovies);
  if (!catalog || catalog.length === 0) {
    const raw = (window.FILMZILLA_DATA && window.FILMZILLA_DATA.movies) || (window.FILMSUB_DATA && window.FILMSUB_DATA.movies) || [];
    if (raw.length > 0) {
      allMovies = raw;
      catalog = groupSeriesCatalog(raw);
    }
  }
  if (!catalog || catalog.length === 0) return [];

  // 1. Any movie with a backdrop or poster image is eligible for Hero Spotlight!
  const eligible = catalog.filter(m => Boolean(m.backdrop || m.backdrop_url || m.poster || m.poster_url));
  const pool = eligible.length > 0 ? eligible : [...catalog];

  // 2. Select recent arrivals (newest ~45 movies) to focus on "aluthin apu"
  const sortedByDate = [...pool].sort((a, b) => _getMovieTimestamp(b) - _getMovieTimestamp(a));
  const recentPool = sortedByDate.slice(0, 45);

  const currentYear = new Date().getFullYear() || 2026;
  const minReleaseYear = currentYear - 2; // e.g. 2024 or newer - exclude old classics from 1961, 2009, etc.

  // 3. Score recent arrivals balancing recent release year and high ratings ("rating wadi saha me lagadi release karapuwa")
  const scored = recentPool.map((m, idx) => {
    const time = _getMovieTimestamp(m);
    const timeHours = time / (1000 * 3600);
    const rating = _getMovieRating(m);
    const yr = Number(m.year) || currentYear;
    const hasBackdrop = Boolean(m.backdrop || m.backdrop_url);
    const hasTrailer = Boolean(m.trailer_url || m.trailer_id);

    // Release Year score: Prioritize recent release years (2026 > 2025 > 2024), disqualify old release years
    let yearScore = 0;
    if (yr < minReleaseYear) {
      yearScore = -10000; // exclude old classics (e.g. 1961, 2009) from hero spotlight
    } else {
      yearScore = (yr - minReleaseYear) * 20;
    }

    // Rating score: high rated movies (7.0 - 8.5+) rise to the top of the spotlight
    let ratingScore = rating * 8;
    if (rating >= 8.0) ratingScore += 24;
    else if (rating >= 7.0) ratingScore += 16;
    else if (rating >= 6.0) ratingScore += 8;
    else if (rating > 0 && rating < 6.0) ratingScore -= 40; // penalize low rated (e.g. 3.3, 4.5)

    let score = timeHours + yearScore + ratingScore;
    if (hasBackdrop) score += 12;
    if (hasTrailer) score += 4;
    if (m.featured) score += 16;

    return { movie: m, score };
  });

  // Sort by score descending (Highest score = newest release & highest rated)
  scored.sort((a, b) => b.score - a.score);

  const merged = [];
  const seenTitles = new Set();
  for (const item of scored) {
    if (item.score < -5000) continue; // skip heavily penalized old classics
    const m = item.movie;
    const rawTitle = (m.title || '').split(' - Season')[0].trim().toLowerCase().replace(/[^a-z0-9]/g, '');
    if (rawTitle && !seenTitles.has(rawTitle)) {
      seenTitles.add(rawTitle);
      merged.push(m);
    }
    if (merged.length >= 10) break;
  }

  // Fallback: If less than 10, fill from recent releases (>= minReleaseYear)
  if (merged.length < 10) {
    for (const m of sortedByDate) {
      const yr = Number(m.year) || currentYear;
      if (yr < minReleaseYear) continue;
      const rawTitle = (m.title || '').split(' - Season')[0].trim().toLowerCase().replace(/[^a-z0-9]/g, '');
      if (rawTitle && !seenTitles.has(rawTitle)) {
        seenTitles.add(rawTitle);
        merged.push(m);
      }
      if (merged.length >= 10) break;
    }
  }

  return merged;
}

function ensureHeroDOMStructure(heroEl) {
  if (!heroEl) return;
  // If heroEl was previously emptied or missing core elements, restore full Grand Cinema Billboard structure
  if (!document.getElementById('hero-title') || !document.getElementById('hero-backdrop')) {
    heroEl.innerHTML = `
      <div class="hero-backdrop active" id="hero-backdrop"></div>
      <div class="hero-backdrop-buffer" id="hero-backdrop-buffer"></div>
      <div class="hero-video-wrap" id="hero-video-wrap" aria-hidden="true">
        <iframe id="hero-video-iframe" class="hero-video-iframe" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" playsinline></iframe>
      </div>
      <div class="hero-gradient"></div>
      <div class="hero-noise"></div>
      <div class="hero-content">
        <div class="hero-info-main">
          <div class="hero-badge-row">
            <div class="hero-badge" id="hero-badge"><i class="fa-solid fa-bolt"></i> SPOTLIGHT</div>
          </div>
          <h1 class="hero-title" id="hero-title">Loading...</h1>
          <p class="hero-title-si" id="hero-title-si"></p>
          <div class="hero-meta" id="hero-meta"></div>
          <div class="hero-genres" id="hero-genres"></div>
          <p class="hero-description" id="hero-description"></p>
          <div class="hero-actions">
            <a href="#" class="btn btn-primary btn-hero hero-btn-watch" id="hero-watch-btn">
              <i class="fa-solid fa-play"></i> Watch Now
            </a>
            <button class="btn btn-secondary btn-hero hero-btn-trailer" id="hero-trailer-btn" type="button" title="Watch Official Trailer">
              <i class="fa-solid fa-clapperboard"></i> <span>Trailer</span>
            </button>
            <a href="#" class="btn btn-secondary btn-hero hero-btn-download" id="hero-download-btn">
              <i class="fa-solid fa-cloud-arrow-down"></i> Download
            </a>
          </div>
        </div>
      </div>
      <button class="hero-edge-nav prev" id="hero-edge-prev" type="button" aria-label="Previous Slide">
        <i class="fa-solid fa-chevron-left"></i>
      </button>
      <button class="hero-edge-nav next" id="hero-edge-next" type="button" aria-label="Next Slide">
        <i class="fa-solid fa-chevron-right"></i>
      </button>
      <button class="hero-sound-btn" id="hero-sound-btn" type="button" aria-label="Toggle Sound" title="Sound On / Off" style="display:none">
        <i class="fa-solid fa-volume-xmark"></i>
      </button>
      <div class="hero-apple-pagination" id="hero-apple-pagination" role="tablist" aria-label="Featured Spotlight"></div>
    `;
    renderHeroAppleIndicators();
  }
}


/* ============================================================
   Dynamic Theme Color Extraction — Canvas API
   Extracts dominant color from movie poster/backdrop and
   applies it as the site accent color for immersive theming.
   Avatar (blue) -> blue theme, red movie -> red theme, etc.
============================================================ */
function extractDominantColor(imageUrl, callback) {
  if (!imageUrl || imageUrl.startsWith('data:')) {
    callback(null);
    return;
  }
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = function() {
    try {
      const canvas = document.createElement('canvas');
      const SIZE = 80; // Sample small version for speed
      canvas.width = SIZE;
      canvas.height = Math.round(SIZE * img.height / img.width) || SIZE;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;

      // Collect color buckets (ignore very dark + very gray pixels)
      const buckets = {};
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i], g = data[i+1], b = data[i+2], a = data[i+3];
        if (a < 128) continue; // skip transparent
        const brightness = (r + g + b) / 3;
        if (brightness < 30 || brightness > 230) continue; // skip near black/white
        const saturation = Math.max(r,g,b) - Math.min(r,g,b);
        if (saturation < 35) continue; // skip gray

        // Quantize to buckets of 32 for grouping
        const rq = Math.round(r / 32) * 32;
        const gq = Math.round(g / 32) * 32;
        const bq = Math.round(b / 32) * 32;
        const key = `${rq},${gq},${bq}`;
        buckets[key] = (buckets[key] || 0) + 1;
      }

      // Find most common bucket
      let best = null, bestCount = 0;
      for (const key in buckets) {
        if (buckets[key] > bestCount) {
          bestCount = buckets[key];
          best = key;
        }
      }

      if (best) {
        const [r, g, b] = best.split(',').map(Number);
        // Boost saturation and ensure minimum brightness
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
    // Reset to default Netflix Red theme
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


/* ============================================================
   CINEMATIC 3D WIDESCREEN DECK & EXPANDING FULLSCREEN HERO CONTROLLER
   ("lap eke diga hadathi card tika 3D pitu peliya & full size display")
   ============================================================ */
let heroShowcaseMovies = [];
let heroShowcaseCurrent = 0;
let heroCycleTimer = null;
let heroCycleToken = 0;
let heroIsPaused = false;
let heroFsRemainingTime = 5000;
let heroFsStartTime = 0;
let hero3DTouchStartX = 0;
let hero3DTouchStartY = 0;

const HERO_FS_DURATION = 5000;        // Display time in Fullscreen ("chuttak wela thibila")
const HERO_RETREAT_MS = 880;          // Unified fluid 3D retreat arc & simultaneous card step forward
const HERO_DECK_PAUSE_MS = 1800;      // Appreciation beat in 3D deck view (shows 3D typography & card depth)
const HERO_EXPAND_MS = 820;           // Forward 3D bloom into Fullscreen

// States: 'FULLSCREEN' | 'RETREATING' | 'PAUSED_DECK' | 'EXPANDING' | 'MANUAL_DECK'
let heroCurrentState = 'DECK';
let heroShowcaseInitialized = false;

function clearAllHeroTimers() {
  if (heroCycleTimer) {
    clearTimeout(heroCycleTimer);
    heroCycleTimer = null;
  }
}

function setHeroTimeout(fn, ms) {
  clearAllHeroTimers();
  heroCycleTimer = setTimeout(fn, ms);
}

function isTouchDevice() {
  return !window.matchMedia('(hover: hover) and (pointer: fine)').matches;
}

function renderHeroShowcase() {
  const section = document.getElementById('hero-section');
  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (!section || !stage) return;

  const spotlight = getHeroSpotlightList();
  if (!spotlight || spotlight.length === 0) return;
  const newSpotlight = spotlight.slice(0, 10);

  // If already initialized and identical movies, do not tear down the running DOM or animation!
  if (heroShowcaseInitialized && heroShowcaseMovies.length > 0 && stage.children.length > 0) {
    const oldIds = heroShowcaseMovies.map(m => m.slug || m.id || m.title).join('|');
    const newIds = newSpotlight.map(m => m.slug || m.id || m.title).join('|');
    if (oldIds === newIds) {
      return;
    }
  }

  heroShowcaseMovies = newSpotlight;
  heroShowcaseCurrent = Math.min(heroShowcaseCurrent, heroShowcaseMovies.length - 1);
  heroShowcaseInitialized = true;

  // 1. Build the 16:9 Widescreen Landscape Cards ("lap eke diga athata")
  stage.innerHTML = buildHero3DCardsHTML();

  // 2. Attach click listeners on each card: clicking rotates & expands it into full screen
  bindHeroCardClicks(stage);

  // 3. Navigation controls (Prev / Next / Toggle Deck)
  const prevBtn = document.getElementById('hero-deck-prev');
  const nextBtn = document.getElementById('hero-deck-next');
  const toggleBtn = document.getElementById('hero-deck-toggle');

  if (prevBtn) {
    prevBtn.onclick = (e) => {
      e.stopPropagation();
      selectPreviousHeroMovie();
    };
  }

  if (nextBtn) {
    nextBtn.onclick = (e) => {
      e.stopPropagation();
      cycleHeroShowcaseNext();
    };
  }

  if (toggleBtn) {
    toggleBtn.onclick = (e) => {
      e.stopPropagation();
      toggleHeroDeckView();
    };
  }

  // 4. Hover pause: ONLY pause when mouse hovers over action buttons or nav controls!
  // NEVER pause on the general hero background so auto-play never freezes unexpectedly on desktops!
  const actionButtons = section.querySelector('.hero-fs-actions');
  const deckNavBar = document.getElementById('hero-deck-nav-bar');
  let hoverResumeTimer = null;

  const handleInteractiveHoverEnter = () => {
    if (isTouchDevice()) return;
    heroIsPaused = true;
    pauseProgressBar();
    if (hoverResumeTimer) clearTimeout(hoverResumeTimer);
    // Safety auto-resume after 12s so it never gets permanently stuck
    hoverResumeTimer = setTimeout(() => {
      heroIsPaused = false;
      resumeProgressBar();
    }, 12000);
  };

  const handleInteractiveHoverLeave = () => {
    if (isTouchDevice()) return;
    if (hoverResumeTimer) clearTimeout(hoverResumeTimer);
    heroIsPaused = false;
    resumeProgressBar();
    if (heroCurrentState === 'FULLSCREEN') {
      const remaining = Math.max(heroFsRemainingTime - (Date.now() - heroFsStartTime), 1500);
      scheduleCycleAfter(remaining, heroCycleToken);
    } else if (heroCurrentState === 'PAUSED_DECK' || heroCurrentState === 'DECK') {
      scheduleCycleAfter(800, heroCycleToken);
    }
  };

  if (actionButtons) {
    actionButtons.onmouseenter = handleInteractiveHoverEnter;
    actionButtons.onmouseleave = handleInteractiveHoverLeave;
  }
  if (deckNavBar) {
    deckNavBar.onmouseenter = handleInteractiveHoverEnter;
    deckNavBar.onmouseleave = handleInteractiveHoverLeave;
  }
  section.onmouseenter = null;
  section.onmouseleave = null;

  // 5. Mobile Touch Swipe support
  initHero3DTouchSwipe(stage);

  // 6. Tab Visibility API: pause timers when backgrounded, safely resume without desync
  initHeroVisibilityHandler();

  // 6b. Spatial 3D Parallax: subtle dynamic depth response to cursor position in deck view
  initHero3DParallax(section, stage);

  // 7. Initial State: Start directly in majestic Fullscreen Cinema Mode (like Netflix / Apple TV+)
  const urlParams = new URLSearchParams(window.location.search);
  const stayDeckMode = urlParams.get('mode') === 'deck' || urlParams.get('deck') === '1';

  if (stayDeckMode) {
    section.classList.remove('is-expanded-mode', 'is-receding-mode');
    section.classList.add('is-deck-mode');
    assignHeroSlotClasses();
    updateHeroNavCounter();
    updateDeckToggleBtn('deck');
    heroCurrentState = 'DECK';
  } else {
    section.classList.remove('is-deck-mode', 'is-receding-mode');
    section.classList.add('is-expanded-mode');
    updateHeroContent(heroShowcaseMovies[heroShowcaseCurrent]);
    assignHeroSlotClasses();
    updateHeroNavCounter();
    updateDeckToggleBtn('expanded');
    heroCurrentState = 'FULLSCREEN';
    startProgressBar(HERO_FS_DURATION);
    scheduleCycleAfter(HERO_FS_DURATION, ++heroCycleToken);
  }

  // Preload next backdrop image into cache
  preloadHeroImage(1);
}

function updateDeckToggleBtn(mode) {
  const toggleBtn = document.getElementById('hero-deck-toggle');
  if (!toggleBtn) return;
  if (mode === 'expanded') {
    toggleBtn.innerHTML = '<i class="fa-solid fa-layer-group"></i> <span>Cards Deck</span>';
    toggleBtn.setAttribute('title', 'Switch to 3D Cards Deck');
  } else {
    toggleBtn.innerHTML = '<i class="fa-solid fa-expand"></i> <span>Full Screen</span>';
    toggleBtn.setAttribute('title', 'Expand to Full Screen');
  }
}

function buildHero3DCardsHTML() {
  const total = heroShowcaseMovies.length;
  return heroShowcaseMovies.map((movie, idx) => {
    const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url || SITE_CONFIG.defaultBackdrop;
    const rel = (idx - heroShowcaseCurrent + total) % total;
    const slotClass = rel < 6 ? `slot-${rel}` : 'slot-hidden';
    const yr = movie.year || '';
    const qLabel = formatQualityLabel(movie.quality);
    const rating = movie.imdb || movie.rating || '8.2';

    return `
      <div class="hero-3d-card ${slotClass}${rel === 0 ? ' is-active' : ''}" data-idx="${idx}">
        <div class="hero-3d-card-inner">
          <img src="${FilmSub.escHtml(backdrop)}" class="hero-3d-card-img" alt="${FilmSub.escHtml(movie.title || '')}" loading="${idx < 2 ? 'eager' : 'lazy'}" decoding="async">
          <div class="hero-3d-card-grad"></div>
          <div class="hero-3d-card-top-row">
            <span class="hero-3d-card-badge">${idx + 1}</span>
            <span class="hero-3d-card-quality">${FilmSub.escHtml(qLabel)}</span>
          </div>
          <div class="hero-3d-card-info">
            <span class="hero-3d-card-title">${FilmSub.escHtml(movie.title || '')}</span>
            <span class="hero-3d-card-meta">${FilmSub.escHtml(yr ? yr + ' • ' : '')}<i class="fa-solid fa-star" style="color:#f5c518"></i> ${FilmSub.escHtml(String(rating))}</span>
          </div>
          <div class="hero-3d-card-shine"></div>
        </div>
      </div>
    `;
  }).join('');
}

function bindHeroCardClicks(stage) {
  if (!stage) return;
  stage.querySelectorAll('.hero-3d-card').forEach(card => {
    card.onclick = () => {
      const targetIdx = parseInt(card.dataset.idx, 10);
      selectHeroMovieAndExpand(targetIdx);
    };
  });
}

function assignHeroSlotClasses() {
  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (!stage) return;
  const len = heroShowcaseMovies.length;
  stage.querySelectorAll('.hero-3d-card').forEach(card => {
    card.classList.remove('is-shuffling-to-rear', 'is-retreating-to-back', 'is-spinning-to-fullscreen');
    const cIdx = parseInt(card.dataset.idx, 10);
    const rel = (cIdx - heroShowcaseCurrent + len) % len;
    const slotClass = rel < 6 ? `slot-${rel}` : 'slot-hidden';
    card.className = `hero-3d-card ${slotClass}${rel === 0 ? ' is-active' : ''}`;
  });
}

function updateHeroNavCounter() {
  const counter = document.getElementById('hero-deck-counter');
  if (counter && heroShowcaseMovies.length > 0) {
    counter.textContent = `${heroShowcaseCurrent + 1} / ${heroShowcaseMovies.length}`;
  }
}

function preloadHeroImage(idx) {
  if (!heroShowcaseMovies || heroShowcaseMovies.length === 0) return;
  const movie = heroShowcaseMovies[idx % heroShowcaseMovies.length];
  if (!movie) return;
  const src = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url;
  if (src && typeof Image !== 'undefined') {
    const img = new Image();
    img.decoding = 'async';
    img.src = src;
    if (img.decode) {
      img.decode().catch(() => {});
    }
  }
}

function expandHeroCard(targetIdx, autoAdvance = true) {
  const currentToken = ++heroCycleToken;
  clearAllHeroTimers();

  if (typeof targetIdx === 'number') {
    heroShowcaseCurrent = targetIdx;
  }
  const movie = heroShowcaseMovies[heroShowcaseCurrent];
  if (!movie) return;

  heroCurrentState = 'EXPANDING';

  // 1. Prepare DOM & content before transition begins (ensures instant decode from card memory)
  updateHeroContent(movie);
  updateHeroNavCounter();
  assignHeroSlotClasses();

  const section = document.getElementById('hero-section');
  if (section) {
    section.classList.remove('is-deck-mode', 'is-receding-mode');
    section.classList.add('is-expanded-mode');
  }
  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (stage) stage.style.transform = '';
  const watermark = document.getElementById('hero-stage-watermark');
  if (watermark) watermark.style.transform = '';
  updateDeckToggleBtn('expanded');

  // Preload upcoming next movie backdrop
  preloadHeroImage((heroShowcaseCurrent + 1) % heroShowcaseMovies.length);

  // 2. Complete expansion into full size display (~700ms)
  setHeroTimeout(() => {
    if (currentToken !== heroCycleToken) return;
    heroCurrentState = 'FULLSCREEN';

    if (autoAdvance) {
      startProgressBar(HERO_FS_DURATION);
      scheduleCycleAfter(HERO_FS_DURATION, currentToken);
    }
  }, HERO_EXPAND_MS);
}

function scheduleCycleAfter(duration, expectedToken) {
  clearAllHeroTimers();
  heroFsRemainingTime = duration;
  heroFsStartTime = Date.now();

  setHeroTimeout(() => {
    if (expectedToken !== heroCycleToken) return;

    // If modal is open or user is currently hovering on desktop:
    const modal = document.getElementById('trailer-modal');
    const isModalOpen = modal && modal.style.display === 'flex';
    if (isModalOpen || heroIsPaused) {
      // Recheck smoothly while paused - never kill the loop!
      scheduleCycleAfter(1000, expectedToken);
      return;
    }

    advanceHeroShowcase(expectedToken);
  }, duration);
}

function advanceHeroShowcase(token) {
  if (token && token !== heroCycleToken) return;
  const currentToken = ++heroCycleToken;
  clearAllHeroTimers();
  stopProgressBar();

  const section = document.getElementById('hero-section');
  if (!section) return;

  const len = heroShowcaseMovies.length;
  if (len < 2) return;

  const nextIdx = (heroShowcaseCurrent + 1) % len;

  // CINEMATIC FULLSCREEN VIDEO CROSSFADE:
  // When in Fullscreen mode, smoothly crossfade directly to the next movie!
  // Never shrink down to a small card or pop a small card in the user's face!
  if (section.classList.contains('is-expanded-mode')) {
    heroShowcaseCurrent = nextIdx;
    const nextMovie = heroShowcaseMovies[nextIdx];
    updateHeroContent(nextMovie);
    assignHeroSlotClasses();
    updateHeroNavCounter();
    preloadHeroImage((nextIdx + 1) % len);

    const mainEl = document.querySelector('.hero-showcase-main');
    if (mainEl) {
      mainEl.classList.remove('hero-info-enter');
      void mainEl.offsetWidth;
      mainEl.classList.add('hero-info-enter');
    }

    startProgressBar(HERO_FS_DURATION);
    scheduleCycleAfter(HERO_FS_DURATION, currentToken);
    return;
  }

  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (!stage) return;

  const oldIdx = heroShowcaseCurrent;
  const oldCard = stage.querySelector(`.hero-3d-card[data-idx="${oldIdx}"]`);

  heroCurrentState = 'RETREATING';

  // Reset any residual spatial parallax tilt so retreat begins from zero-tilt alignment
  stage.style.transform = '';
  const watermark = document.getElementById('hero-stage-watermark');
  if (watermark) watermark.style.transform = '';

  section.classList.remove('is-expanded-mode');
  section.classList.add('is-receding-mode');
  updateDeckToggleBtn('deck');

  if (oldCard) {
    oldCard.className = 'hero-3d-card is-retreating-to-back';
  }

  // Simultaneously, advance all other cards forward in 3D unison
  stage.querySelectorAll('.hero-3d-card').forEach(card => {
    const cIdx = parseInt(card.dataset.idx, 10);
    if (cIdx !== oldIdx) {
      const rel = (cIdx - nextIdx + len) % len;
      card.className = `hero-3d-card slot-${Math.min(rel, 5)}${rel === 0 ? ' is-active' : ''}`;
    }
  });

  // Once the retreat arc and slot advance completes, settle deck
  setHeroTimeout(() => {
    if (currentToken !== heroCycleToken) return;
    section.classList.remove('is-receding-mode');
    section.classList.add('is-deck-mode');
    heroShowcaseCurrent = nextIdx;
    assignHeroSlotClasses();
    updateHeroNavCounter();
    heroCurrentState = 'PAUSED_DECK';

    // Preload next movie image into cache
    preloadHeroImage((nextIdx + 1) % len);

    // Continue deck rotation
    scheduleCycleAfter(3500, currentToken);
  }, HERO_RETREAT_MS);
}

function cycleHeroShowcaseNext() {
  const len = heroShowcaseMovies.length;
  if (len < 2) return;
  const section = document.getElementById('hero-section');
  if (section && section.classList.contains('is-expanded-mode')) {
    advanceHeroShowcase();
  } else {
    const nextIdx = (heroShowcaseCurrent + 1) % len;
    selectHeroMovieAndExpand(nextIdx);
  }
}

function selectPreviousHeroMovie() {
  const len = heroShowcaseMovies.length;
  if (len < 2) return;
  const prevIdx = (heroShowcaseCurrent - 1 + len) % len;
  selectHeroMovieAndExpand(prevIdx);
}

function selectHeroMovieAndExpand(targetIdx) {
  const len = heroShowcaseMovies.length;
  if (targetIdx < 0 || targetIdx >= len) return;

  const currentToken = ++heroCycleToken;
  clearAllHeroTimers();
  stopProgressBar();

  heroShowcaseCurrent = targetIdx;
  assignHeroSlotClasses();
  updateHeroNavCounter();

  const section = document.getElementById('hero-section');
  if (section && section.classList.contains('is-expanded-mode')) {
    // If already in full screen, crossfade content smoothly
    updateHeroContent(heroShowcaseMovies[targetIdx]);
    startProgressBar(HERO_FS_DURATION);
    scheduleCycleAfter(HERO_FS_DURATION, currentToken);
  } else {
    // In deck view: expand into full screen
    expandHeroCard(targetIdx, true);
  }
}

function toggleHeroDeckView() {
  const section = document.getElementById('hero-section');
  if (!section) return;

  if (section.classList.contains('is-expanded-mode')) {
    // Switch to 3D deck view: recede gracefully and stay in manual deck view
    const currentToken = ++heroCycleToken;
    clearAllHeroTimers();
    stopProgressBar();
    heroCurrentState = 'RETREATING';

    section.classList.remove('is-expanded-mode');
    section.classList.add('is-receding-mode');
    updateDeckToggleBtn('deck');

    setHeroTimeout(() => {
      if (currentToken !== heroCycleToken) return;
      section.classList.remove('is-receding-mode');
      section.classList.add('is-deck-mode');
      assignHeroSlotClasses();
      heroCurrentState = 'MANUAL_DECK';
    }, HERO_RETREAT_MS);
  } else {
    // Expand front card into full screen and resume continuous cycle
    expandHeroCard(heroShowcaseCurrent, true);
  }
}

function startProgressBar(duration = HERO_FS_DURATION) {
  const bar = document.getElementById('hero-fs-progress-bar');
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      bar.style.transition = `width ${duration}ms linear`;
      bar.style.width = '100%';
    });
  });
}

function stopProgressBar() {
  const bar = document.getElementById('hero-fs-progress-bar');
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
}

function pauseProgressBar() {
  const bar = document.getElementById('hero-fs-progress-bar');
  if (!bar) return;
  const computed = window.getComputedStyle(bar).width;
  bar.style.transition = 'none';
  bar.style.width = computed;
}

function resumeProgressBar() {
  const bar = document.getElementById('hero-fs-progress-bar');
  if (!bar) return;
  const remaining = Math.max(heroFsRemainingTime - (Date.now() - heroFsStartTime), 1000);
  bar.style.transition = `width ${remaining}ms linear`;
  bar.style.width = '100%';
}

function initHero3DTouchSwipe(stage) {
  if (!stage || stage._swipeBound) return;
  stage._swipeBound = true;

  stage.addEventListener('touchstart', (e) => {
    if (!e.touches || !e.touches[0]) return;
    hero3DTouchStartX = e.touches[0].clientX;
    hero3DTouchStartY = e.touches[0].clientY;
  }, { passive: true });

  stage.addEventListener('touchend', (e) => {
    if (!e.changedTouches || !e.changedTouches[0]) return;
    const dx = e.changedTouches[0].clientX - hero3DTouchStartX;
    const dy = e.changedTouches[0].clientY - hero3DTouchStartY;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
      if (heroShowcaseMovies.length < 2) return;
      if (dx < 0) {
        cycleHeroShowcaseNext();
      } else {
        selectPreviousHeroMovie();
      }
    }
  }, { passive: true });
}

let heroVisibilityBound = false;
function initHeroVisibilityHandler() {
  if (heroVisibilityBound) return;
  heroVisibilityBound = true;

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearAllHeroTimers();
      pauseProgressBar();
    } else {
      // User returned to tab: clean up any lingering animations and resume
      const section = document.getElementById('hero-section');
      if (!section) return;

      const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
      if (stage) {
        stage.querySelectorAll('.hero-3d-card').forEach(c => {
          c.classList.remove('is-shuffling-to-rear', 'is-retreating-to-back', 'is-spinning-to-fullscreen');
        });
      }
      assignHeroSlotClasses();

      if (heroCurrentState === 'MANUAL_DECK') {
        // User deliberately switched to manual 3D deck view: keep it in manual deck view
        return;
      }

      if (section.classList.contains('is-expanded-mode')) {
        heroCurrentState = 'FULLSCREEN';
        startProgressBar(HERO_FS_DURATION);
        scheduleCycleAfter(HERO_FS_DURATION, ++heroCycleToken);
      } else {
        heroCurrentState = 'DECK';
        const resumeToken = ++heroCycleToken;
        setHeroTimeout(() => {
          if (resumeToken === heroCycleToken) {
            expandHeroCard(heroShowcaseCurrent, true);
          }
        }, 500);
      }
    }
  });
}

let heroParallaxBound = false;
function initHero3DParallax(section, stage) {
  if (heroParallaxBound || !section || !stage) return;
  heroParallaxBound = true;

  const watermark = document.getElementById('hero-stage-watermark');
  let rafId = null;
  let targetRotX = 0;
  let targetRotY = 0;
  let currentRotX = 0;
  let currentRotY = 0;

  const updateTilt = () => {
    currentRotX += (targetRotX - currentRotX) * 0.12;
    currentRotY += (targetRotY - currentRotY) * 0.12;

    if (section.classList.contains('is-deck-mode') || section.classList.contains('is-receding-mode')) {
      stage.style.transform = `rotateX(${currentRotX.toFixed(2)}deg) rotateY(${currentRotY.toFixed(2)}deg)`;
      if (watermark) {
        watermark.style.transform = `translate(-50%, -50%) rotateY(${(currentRotY * 0.5).toFixed(2)}deg)`;
      }
    } else {
      stage.style.transform = '';
      if (watermark) {
        watermark.style.transform = '';
      }
    }

    if (Math.abs(targetRotX - currentRotX) > 0.04 || Math.abs(targetRotY - currentRotY) > 0.04) {
      rafId = requestAnimationFrame(updateTilt);
    } else {
      rafId = null;
    }
  };

  section.addEventListener('mousemove', (e) => {
    if (isTouchDevice() || !section.classList.contains('is-deck-mode')) return;
    const rect = section.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width - 0.5;
    const ny = (e.clientY - rect.top) / rect.height - 0.5;
    targetRotY = nx * 8;
    targetRotX = -ny * 5;
    if (!rafId) rafId = requestAnimationFrame(updateTilt);
  }, { passive: true });

  section.addEventListener('mouseleave', () => {
    targetRotX = 0;
    targetRotY = 0;
    if (!rafId) rafId = requestAnimationFrame(updateTilt);
  });
}

function updateHeroContent(movie) {
  if (!movie) return;
  const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url || SITE_CONFIG.defaultBackdrop;

  // Pre-buffered smooth backdrop crossfade
  const bgCurrent = document.getElementById('hero-bg-current');
  const bgNext = document.getElementById('hero-bg-next');
  const section = document.getElementById('hero-section');
  const isAlreadyExpanded = section && section.classList.contains('is-expanded-mode');

  if (bgCurrent && bgNext) {
    const targetBg = `url("${backdrop}")`;
    if (!isAlreadyExpanded) {
      // In deck/receding mode: immediately set primary background so it blossoms in full synchronization with the 3D card bloom
      bgCurrent.style.backgroundImage = targetBg;
      bgCurrent.style.opacity = '1';
      bgNext.style.opacity = '0';
    } else {
      const currentBg = bgCurrent.style.backgroundImage || '';
      if (!currentBg || currentBg === 'none' || bgCurrent.style.opacity === '0') {
        bgCurrent.style.backgroundImage = targetBg;
        bgCurrent.style.opacity = '1';
        bgNext.style.opacity = '0';
      } else if (!currentBg.includes(backdrop)) {
        bgNext.style.backgroundImage = targetBg;
        bgNext.style.opacity = '1';
        setTimeout(() => {
          bgCurrent.style.backgroundImage = targetBg;
          bgCurrent.style.opacity = '1';
          bgNext.style.opacity = '0';
        }, 650);
      }
    }
  } else if (bgCurrent) {
    bgCurrent.style.backgroundImage = `url("${backdrop}")`;
  }

  // Update text & details
  const titleEl = document.getElementById('hero-fs-title');
  if (titleEl) titleEl.textContent = movie.title || '';

  const titleSiEl = document.getElementById('hero-fs-title-si');
  if (titleSiEl) titleSiEl.textContent = movie.title_si || '';

  const descEl = document.getElementById('hero-fs-desc');
  if (descEl) descEl.textContent = movie.description || '';

  const genres = Array.isArray(movie.genres) ? movie.genres.slice(0, 4) : [];
  const genresEl = document.getElementById('hero-fs-genres');
  if (genresEl) {
    genresEl.innerHTML = genres.map(g => `<a href="search.html?genre=${encodeURIComponent(g)}" class="genre-chip">${FilmSub.escHtml(g)}</a>`).join('');
  }

  // Typography Metadata Line
  const metaEl = document.getElementById('hero-fs-meta');
  if (metaEl) {
    const yr = movie.year ? `<span class="hero-meta-item">${FilmSub.escHtml(String(movie.year))}</span>` : '';
    const qLabel = `<span class="hero-meta-quality">${FilmSub.escHtml(formatQualityLabel(movie.quality))}</span>`;
    const imdbVal = movie.imdb || movie.rating || '';
    const imdb = imdbVal ? `<span class="hero-meta-imdb"><i class="fa-solid fa-star"></i> ${FilmSub.escHtml(String(imdbVal))}</span>` : '';
    const sub = `<span class="hero-meta-sub"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි</span>`;
    const items = [yr, qLabel, imdb, sub].filter(Boolean);
    metaEl.innerHTML = items.join('<span class="hero-meta-dot">•</span>');
  }

  // Action Buttons
  const movieUrl = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug || movie.id)}`;
  const watchBtn = document.getElementById('hero-fs-watch-btn');
  if (watchBtn) watchBtn.href = movieUrl;

  const dlBtn = document.getElementById('hero-fs-download-btn');
  if (dlBtn) dlBtn.href = `${movieUrl}#downloads`;

  const trailerBtn = document.getElementById('hero-fs-trailer-btn');
  if (trailerBtn) {
    if (movie.trailer_id || movie.trailer_url) {
      trailerBtn.style.display = 'inline-flex';
      trailerBtn.onclick = (e) => {
        e.preventDefault();
        openTrailerModal(movie);
      };
    } else {
      trailerBtn.style.display = 'none';
    }
  }

  // Restart text animation without layout thrashing
  const mainInfo = document.getElementById('hero-showcase-main');
  if (mainInfo) {
    mainInfo.classList.remove('hero-info-enter');
    requestAnimationFrame(() => {
      mainInfo.classList.add('hero-info-enter');
    });
  }

  // Dynamic theme extraction from backdrop
  if (backdrop && backdrop.startsWith('http')) {
    extractDominantColor(backdrop, (col) => {
      if (col) applyDynamicTheme(col.r, col.g, col.b);
      else applyDynamicTheme(null);
    });
  }
}


// ---- Hero ----
function renderHero(targetMovie, forcedIdx) {
  if (document.getElementById('hero-deck-stage')) { renderHeroDeck(); return; }
  const heroEl = document.getElementById('hero-section');
  if (!heroEl) return;

  const spotlight = getHeroSpotlightList();
  heroSpotlightMovies = spotlight;

  if (typeof forcedIdx === 'number') {
    heroCurrentIdx = forcedIdx;
  } else if (heroCurrentIdx >= spotlight.length) {
    heroCurrentIdx = 0;
  }

  const movie = targetMovie || spotlight[heroCurrentIdx] || getFeatured();
  ensureHeroDOMStructure(heroEl);

  if (!movie) {
    const titleEl = document.getElementById('hero-title');
    if (titleEl) titleEl.textContent = 'FilmZilla Cinema';
    const descEl = document.getElementById('hero-description');
    if (descEl) descEl.textContent = 'Discover latest Sinhala Subtitles, HD Movies & TV Series with direct streaming.';
    const metaEl = document.getElementById('hero-meta');
    if (metaEl) metaEl.innerHTML = '<span class="badge badge-quality"><i class="fa-solid fa-film"></i> Full HD</span><span class="badge badge-sub"><i class="fa-solid fa-closed-captioning"></i> සිංහල Sub</span>';
    return;
  }

  // Smooth Backdrop Crossfade
  const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url || SITE_CONFIG.defaultBackdrop;
  const backdropEl = document.getElementById('hero-backdrop');
  const bufferEl = document.getElementById('hero-backdrop-buffer');

  if (backdropEl && bufferEl) {
    bufferEl.style.backgroundImage = `url('${backdrop}')`;
    bufferEl.style.opacity = '1';
    setTimeout(() => {
      backdropEl.style.backgroundImage = `url('${backdrop}')`;
      backdropEl.style.opacity = '1';
      bufferEl.style.opacity = '0';
    }, 450);
  } else if (backdropEl) {
    backdropEl.style.backgroundImage = `url('${backdrop}')`;
    backdropEl.style.opacity = '1';
  }

  // Dynamic theme color extraction from poster
  const colorSrc = movie.poster || movie.poster_url || movie.backdrop || backdrop;
  if (colorSrc && colorSrc.startsWith('http')) {
    extractDominantColor(colorSrc, function(color) {
      if (color) {
        applyDynamicTheme(color.r, color.g, color.b);
        // Also update hero poster thumbnail
        const heroPosterEl = document.getElementById('hero-poster-thumb');
        if (heroPosterEl) heroPosterEl.src = colorSrc;
      } else {
        applyDynamicTheme(null);
      }
    });
  } else {
    applyDynamicTheme(null);
  }

  // Update hero right poster
  const heroPosterEl = document.getElementById('hero-poster-thumb');
  if (heroPosterEl) {
    const posterSrc = movie.poster || movie.poster_url || '';
    if (posterSrc) {
      heroPosterEl.src = posterSrc;
      heroPosterEl.style.display = '';
    }
  }

  const genres = Array.isArray(movie.genres) ? movie.genres : [];
  const yr = movie.year ? `<span class="hero-meta-item">${movie.year}</span>` : '';
  const qLabel = `<span class="hero-meta-quality">${formatQualityLabel(movie.quality)}</span>`;
  const imdbVal = movie.imdb || movie.rating || '';
  const imdb = (imdbVal && imdbVal !== '0.0') ? `<span class="hero-meta-imdb"><i class="fa-solid fa-star"></i> ${imdbVal}</span>` : '';
  const sub = `<span class="hero-meta-sub"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි</span>`;
  const items = [yr, qLabel, imdb, sub].filter(Boolean);
  const genreChips = genres.map(g => `<a href="search.html?genre=${encodeURIComponent(g)}" class="genre-chip">${g}</a>`).join('');

  const badgeEl = document.getElementById('hero-badge');
  if (badgeEl) badgeEl.innerHTML = `<i class="fa-solid fa-bolt"></i> SPOTLIGHT`;

  const titleEl = document.getElementById('hero-title');
  if (titleEl) titleEl.textContent = movie.title || '';

  const titleSiEl = document.getElementById('hero-title-si');
  if (titleSiEl) titleSiEl.textContent = movie.title_si || '';

  const metaEl = document.getElementById('hero-meta');
  if (metaEl) metaEl.innerHTML = items.join('<span class="hero-meta-dot">•</span>');

  const genresEl = document.getElementById('hero-genres');
  if (genresEl) genresEl.innerHTML = genreChips;

  const descEl = document.getElementById('hero-description');
  if (descEl) descEl.textContent = movie.description || '';

  const movieUrl = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug || movie.id)}`;
  const watchBtn = document.getElementById('hero-watch-btn');
  if (watchBtn) {
    watchBtn.href = movieUrl;
    watchBtn.innerHTML = `<i class="fa-solid fa-play"></i> Watch Now`;
  }
  const dlBtn = document.getElementById('hero-download-btn');
  if (dlBtn) {
    dlBtn.href = `${movieUrl}#downloads`;
    dlBtn.innerHTML = `<i class="fa-solid fa-cloud-arrow-down"></i> Download`;
  }

  // Setup Trailer Button to open dedicated Cinema Modal (or hide if no trailer)
  const trailerBtn = document.getElementById('hero-trailer-btn');
  if (trailerBtn) {
    if (movie.trailer_id || movie.trailer_url) {
      trailerBtn.style.display = 'inline-flex';
      trailerBtn.onclick = (e) => {
        e.preventDefault();
        openTrailerModal(movie);
      };
    } else {
      trailerBtn.style.display = 'none';
    }
  }

  // Re-trigger text slide-in animation smoothly on each slide transition
  const infoMain = document.querySelector('.hero-info-main');
  if (infoMain) {
    infoMain.style.animation = 'none';
    void infoMain.offsetWidth; // Force CSS reflow
    infoMain.style.animation = '';
  }

  // Update Apple TV+ indicator dots
  updateHeroAppleIndicators(heroCurrentIdx);
  initHeroTouchSwipe();
}

// ---- Apple TV+ Style Hero Carousel & Indicator Dots ----
function renderHeroAppleIndicators() {
  const container = document.getElementById('hero-apple-pagination');
  const list = getHeroSpotlightList();
  if (container) {
    if (list && list.length > 1) {
      container.style.display = 'flex';
      container.innerHTML = list.map((_, idx) => `
        <button class="hero-apple-dot${idx === heroCurrentIdx ? ' active' : ''}" data-idx="${idx}" type="button" aria-label="Slide ${idx + 1}"></button>
      `).join('');

      container.querySelectorAll('.hero-apple-dot').forEach(dot => {
        dot.onclick = () => {
          const idx = parseInt(dot.dataset.idx, 10);
          if (!isNaN(idx) && list[idx]) {
            heroCurrentIdx = idx;
            renderHero(list[idx], idx);
            restartHeroTimer();
          }
        };
      });
    } else {
      container.innerHTML = '';
      container.style.display = 'none';
    }
  }

  // Setup edge navigation chevrons (< and >)
  const prevBtn = document.getElementById('hero-edge-prev') || document.getElementById('hero-prev-btn');
  const nextBtn = document.getElementById('hero-edge-next') || document.getElementById('hero-next-btn');
  if (prevBtn) {
    prevBtn.style.display = (list && list.length > 1) ? 'flex' : 'none';
    prevBtn.onclick = () => {
      const total = list.length || 1;
      heroCurrentIdx = (heroCurrentIdx - 1 + total) % total;
      renderHero(list[heroCurrentIdx], heroCurrentIdx);
      restartHeroTimer();
    };
  }
  if (nextBtn) {
    nextBtn.style.display = (list && list.length > 1) ? 'flex' : 'none';
    nextBtn.onclick = () => {
      const total = list.length || 1;
      heroCurrentIdx = (heroCurrentIdx + 1) % total;
      renderHero(list[heroCurrentIdx], heroCurrentIdx);
      restartHeroTimer();
    };
  }

  restartHeroTimer();
}

function updateHeroAppleIndicators(currentIdx) {
  const container = document.getElementById('hero-apple-pagination');
  if (!container) return;
  const dots = container.querySelectorAll('.hero-apple-dot');
  dots.forEach((d, idx) => {
    d.classList.toggle('active', idx === currentIdx);
  });
}

function restartHeroTimer() {
  if (heroRotateTimer) {
    clearInterval(heroRotateTimer);
    heroRotateTimer = null;
  }
  const heroEl = document.getElementById('hero-section');
  if (!heroEl) return;

  const spotlight = (heroSpotlightMovies && heroSpotlightMovies.length > 0) ? heroSpotlightMovies : getHeroSpotlightList();
  heroSpotlightMovies = spotlight;
  if (!spotlight || spotlight.length < 2) return;

  heroRotateTimer = setInterval(() => {
    // If trailer modal is open, pause auto-rotation
    const modal = document.getElementById('trailer-modal');
    if (modal && modal.style.display === 'flex') return;

    if (!heroSpotlightMovies || heroSpotlightMovies.length < 2) return;
    heroCurrentIdx = (heroCurrentIdx + 1) % heroSpotlightMovies.length;
    renderHero(heroSpotlightMovies[heroCurrentIdx], heroCurrentIdx);
  }, HERO_ROTATE_INTERVAL);

  initHeroHoverControls();
  initHeroTouchSwipe();
}

function initHeroHoverControls() {
  if (heroHoverListenersBound) return;
  heroHoverListenersBound = true;

  const heroActions = document.querySelector('.hero-actions');
  const pagination = document.getElementById('hero-apple-pagination');
  const edgePrev = document.getElementById('hero-edge-prev') || document.getElementById('hero-prev-btn');
  const edgeNext = document.getElementById('hero-edge-next') || document.getElementById('hero-next-btn');

  // Pause rotation only when interacting with buttons/controls
  [heroActions, pagination, edgePrev, edgeNext].forEach(el => {
    if (!el) return;
    el.addEventListener('mouseenter', () => {
      if (heroRotateTimer) {
        clearInterval(heroRotateTimer);
        heroRotateTimer = null;
      }
    });
    el.addEventListener('mouseleave', () => {
      restartHeroTimer();
    });
  });

  // Resume rotation when tab becomes visible
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      restartHeroTimer();
    } else if (heroRotateTimer) {
      clearInterval(heroRotateTimer);
      heroRotateTimer = null;
    }
  });
}

let heroSwipeListenersBound = false;

/**
 * Initializes buttery-smooth horizontal touch/swipe gestures on mobile screens
 * for the Hero Billboard (Swipe left -> Next movie, Swipe right -> Previous movie).
 * Also supports desktop mouse drag & click suppression during swipe.
 */
function initHeroTouchSwipe() {
  if (heroSwipeListenersBound) return;
  const heroEl = document.getElementById('hero-section');
  if (!heroEl) return;
  heroSwipeListenersBound = true;

  let startX = 0;
  let startY = 0;
  let startTime = 0;
  let isTouching = false;
  let isHorizontal = false;
  let blockClickUntil = 0;

  // Intercept any click during/right after swipe gesture so it doesn't trigger links accidentally
  heroEl.addEventListener('click', (e) => {
    if (Date.now() < blockClickUntil) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);

  const handleStart = (clientX, clientY) => {
    startX = clientX;
    startY = clientY;
    startTime = Date.now();
    isTouching = true;
    isHorizontal = false;

    // Pause auto-rotation while user is touching/dragging
    if (heroRotateTimer) {
      clearInterval(heroRotateTimer);
      heroRotateTimer = null;
    }
  };

  const handleMove = (clientX, clientY, e) => {
    if (!isTouching) return;
    const diffX = clientX - startX;
    const diffY = clientY - startY;

    // Detect gesture angle once finger moves beyond 8px
    if (!isHorizontal && (Math.abs(diffX) > 8 || Math.abs(diffY) > 8)) {
      if (Math.abs(diffX) > Math.abs(diffY)) {
        isHorizontal = true;
      } else {
        // Mostly vertical scrolling; release swipe so user can scroll page down smoothly
        isTouching = false;
        return;
      }
    }

    if (isHorizontal) {
      // Prevent browser default vertical scroll bounce while swiping horizontally
      if (e && e.cancelable) {
        e.preventDefault();
      }

      // Elastic tactile feedback on the hero content
      const heroContent = heroEl.querySelector('.hero-content');
      if (heroContent) {
        const damp = Math.sign(diffX) * Math.min(Math.abs(diffX) * 0.35, 45);
        heroContent.style.transform = `translateX(${damp}px)`;
        heroContent.style.transition = 'none';
      }
    }
  };

  const handleEnd = (clientX, clientY) => {
    if (!isTouching) {
      restartHeroTimer();
      return;
    }

    // Smoothly restore visual transform
    const heroContent = heroEl.querySelector('.hero-content');
    if (heroContent) {
      heroContent.style.transition = 'transform 0.28s cubic-bezier(0.2, 0.8, 0.2, 1)';
      heroContent.style.transform = '';
    }

    const deltaX = clientX - startX;
    const deltaTime = Date.now() - startTime;

    isTouching = false;

    if (isHorizontal) {
      const minDistance = 35; // minimum px swipe threshold
      const isQuickFlick = Math.abs(deltaX) > 20 && deltaTime < 320;

      if (Math.abs(deltaX) >= minDistance || isQuickFlick) {
        blockClickUntil = Date.now() + 350; // prevent accidental button click

        const list = getHeroSpotlightList();
        const total = list.length || 1;

        if (deltaX < 0) {
          // SWIPE LEFT -> NEXT MOVIE POSTER
          heroCurrentIdx = (heroCurrentIdx + 1) % total;
          renderHero(list[heroCurrentIdx], heroCurrentIdx);
        } else {
          // SWIPE RIGHT -> PREVIOUS MOVIE POSTER
          heroCurrentIdx = (heroCurrentIdx - 1 + total) % total;
          renderHero(list[heroCurrentIdx], heroCurrentIdx);
        }
      }
    }

    isHorizontal = false;
    restartHeroTimer();
  };

  // 1. Mobile Touch Events (iOS Safari, Android Chrome, Telegram Webview)
  heroEl.addEventListener('touchstart', (e) => {
    if (!e.touches || e.touches.length !== 1) return;
    handleStart(e.touches[0].clientX, e.touches[0].clientY);
  }, { passive: true });

  heroEl.addEventListener('touchmove', (e) => {
    if (!e.touches || e.touches.length !== 1) return;
    handleMove(e.touches[0].clientX, e.touches[0].clientY, e);
  }, { passive: false });

  heroEl.addEventListener('touchend', (e) => {
    const t = (e.changedTouches && e.changedTouches[0]) ? e.changedTouches[0] : null;
    handleEnd(t ? t.clientX : startX, t ? t.clientY : startY);
  }, { passive: true });

  heroEl.addEventListener('touchcancel', (e) => {
    handleEnd(startX, startY);
  }, { passive: true });

  // 2. Desktop Mouse Dragging (Bonus: Users can also drag with mouse left/right)
  let isMouseDown = false;
  heroEl.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return; // left click only
    if (e.target.closest('a, button')) return; // don't drag if clicking buttons directly
    isMouseDown = true;
    handleStart(e.clientX, e.clientY);
  });

  window.addEventListener('mousemove', (e) => {
    if (!isMouseDown) return;
    handleMove(e.clientX, e.clientY, e);
  });

  window.addEventListener('mouseup', (e) => {
    if (!isMouseDown) return;
    isMouseDown = false;
    handleEnd(e.clientX, e.clientY);
  });
}

// ---- Format Quality Label (e.g. 1080p -> WEB-DL) ----
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

// ---- Generate Movie Card (Netflix Cinema Style) ----
function generateMovieCard(movie, opts = {}) {
  const isTrending = typeof opts === 'boolean' ? opts : Boolean(opts.isTrending);
  const rank = opts.rank ? `<span class="card-rank-num">${opts.rank}</span>` : '';
  const poster = movie.poster || movie.poster_url || SITE_CONFIG.defaultPoster;
  
  // Clean IMDb badge (top right, completely unobstructed)
  const imdbVal = movie.imdb || movie.rating || '';
  const imdb = (imdbVal && imdbVal !== '0.0') ? `<span class="badge-top-right"><i class="fa-solid fa-star"></i> ${imdbVal}</span>` : '';
  
  // Left badges (Quality label in sleek liquid water bubble pill design)
  const rawQ = formatQualityLabel(movie.quality);
  const qualityBadge = `<span class="badge-top-left">${escHtml(rawQ)}</span>`;
  
  // Calculate if movie is New (added within last 21 days)
  const addedAt = movie.added_at || movie.added_date;
  let isNew = false;
  if (addedAt) {
    const daysDiff = (Date.now() - new Date(addedAt).getTime()) / (1000 * 60 * 60 * 24);
    if (daysDiff <= 21) isNew = true;
  }
  const newBadge = isNew ? `<span class="badge-card-new">NEW</span>` : '';
  
  const isSeries = movie.type === 'series' || (Array.isArray(movie.seasons) && movie.seasons.length > 0) || Boolean(movie.season);
  const typeBadge = isSeries ? `<span class="badge-card-type"><i class="fa-solid fa-tv"></i> TV</span>` : '';
  const url = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug || movie.id)}`;
  const cardClass = isTrending ? 'movie-card trending-card is-card-visible visible' : 'movie-card is-card-visible visible';

  const mId = movie.id || movie.slug || '';
  const isFav = window.FilmSubUser ? window.FilmSubUser.Favorites.has(mId) : false;
  const isLater = window.FilmSubUser ? window.FilmSubUser.WatchLater.has(mId) : false;

  return `
    <a href="${url}" class="${cardClass}" data-slug="${escHtml(movie.slug || movie.id || '')}" title="${escHtml(movie.title || '')}">
      <div class="card-poster">
        <img src="${escHtml(poster)}" alt="${escHtml(movie.title || '')}" loading="lazy" decoding="async"
             onerror="this.onerror=null; if(window.SITE_CONFIG) this.src=window.SITE_CONFIG.defaultPoster;">
        <div class="card-hover-play"><i class="fa-solid fa-play"></i></div>
        <div class="card-quick-actions">
          <button type="button" class="btn-card-action fav ${isFav ? 'active' : ''}" data-movie-id="${escHtml(mId)}" title="${isFav ? 'Remove Favorite' : 'Add to Favorites'}">
            <i class="${isFav ? 'fa-solid' : 'fa-regular'} fa-heart"></i>
          </button>
          <button type="button" class="btn-card-action later ${isLater ? 'active' : ''}" data-movie-id="${escHtml(mId)}" title="${isLater ? 'Remove Watch Later' : 'Watch Later'}">
            <i class="${isLater ? 'fa-solid' : 'fa-regular'} fa-clock"></i>
          </button>
        </div>
        <div class="card-badges-left">
          ${qualityBadge}
          ${newBadge}
          ${typeBadge}
        </div>
        ${imdb}
        ${rank}
        <div class="card-bottom-bar">
          <span class="card-title-text">${escHtml(movie.title || 'Untitled')}${movie.year ? ` (${movie.year})` : ''}</span>
          <span class="card-sub-tag"><i class="fa-solid fa-closed-captioning"></i> සිංහල උපසිරැසි</span>
        </div>
      </div>
    </a>`;
}

// ---- Render Carousels ----
function renderCarousel(trackId, movies, opts = {}) {
  const track = document.getElementById(trackId);
  if (!track) return;
  const isTrending = typeof opts === 'boolean' ? opts : Boolean(opts.isTrending);
  if (!movies || movies.length === 0) {
    track.innerHTML = renderSkeletons(isTrending ? 4 : 6);
    return;
  }
  track.innerHTML = movies.map((m, idx) => generateMovieCard(m, { isTrending, rank: isTrending ? (idx + 1) : null })).join('');
  const parentSec = track.closest('.section');
  if (parentSec && parentSec.classList.contains('is-visible')) {
    track.querySelectorAll('.movie-card').forEach((c, idx) => {
      c.style.transitionDelay = `${Math.min(idx * 0.04, 0.4)}s`;
      c.classList.add('is-card-visible');
    });
  }
}

// ---- Skeletons ----
function renderSkeletons(count) {
  return Array.from({ length: count }, () => `
    <div class="skeleton-card">
      <div class="skeleton skeleton-poster"></div>
    </div>`).join('');
}

// ---- Carousel Arrows ----
function setupCarouselArrows() {
  document.querySelectorAll('.carousel-wrap').forEach(wrap => {
    const track = wrap.querySelector('.carousel-track');
    const leftBtn = wrap.querySelector('.carousel-arrow.left');
    const rightBtn = wrap.querySelector('.carousel-arrow.right');
    if (!track) return;
    const scrollAmt = 480;
    if (leftBtn) leftBtn.addEventListener('click', () => track.scrollBy({ left: -scrollAmt, behavior: 'smooth' }));
    if (rightBtn) rightBtn.addEventListener('click', () => track.scrollBy({ left: scrollAmt, behavior: 'smooth' }));
  });
}

// ---- Header scroll effect ----
function initHeader() {
  const header = document.querySelector('.site-header');
  if (!header) return;
  const onScroll = () => {
    if (window.scrollY > 60) header.classList.add('solid');
    else header.classList.remove('solid');
  };
  window.addEventListener('scroll', onScroll, { passive: true });

  // Hamburger
  const hamburger = document.querySelector('.hamburger');
  const mobileNav = document.querySelector('.mobile-nav');
  if (hamburger && mobileNav) {
    hamburger.addEventListener('click', () => {
      const open = mobileNav.classList.toggle('open');
      hamburger.classList.toggle('open', open);
      document.body.classList.toggle('no-scroll', open);
    });
    mobileNav.querySelectorAll('a').forEach(link => {
      link.addEventListener('click', () => {
        mobileNav.classList.remove('open');
        hamburger.classList.remove('open');
        document.body.classList.remove('no-scroll');
      });
    });
  }
}

// ---- Continue Watching Shelf (Auto-Resume) ----
function renderContinueWatchingShelf() {
  const section = document.getElementById('continue-watching-section');
  const track = document.getElementById('continue-track');
  const countBadge = document.getElementById('continue-watching-count');
  if (!section || !track || !window.FilmSubUser) return;

  const items = window.FilmSubUser.ContinueWatching.getAll();
  if (!items || items.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  if (countBadge) countBadge.textContent = `${items.length} Item${items.length > 1 ? 's' : ''}`;

  track.innerHTML = items.map(item => {
    const url = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(item.slug || item.id)}&resume=true`;
    const backdrop = item.backdrop || item.poster || SITE_CONFIG.defaultBackdrop;
    const durSec = item.duration || 7200;
    const remainingMin = Math.max(1, Math.round((durSec - (item.currentTime || 0)) / 60));
    const subText = item.season ? `Season ${item.season} • Episode ${item.episode || 1}` : `${remainingMin} min left`;

    return `
      <div class="continue-card-wrap" style="flex:0 0 280px;max-width:280px;">
        <a href="${url}" class="continue-card" data-movie-id="${escHtml(item.id || item.slug)}" title="Resume ${escHtml(item.title)}">
          <img src="${escHtml(backdrop)}" alt="${escHtml(item.title)}" class="continue-card-img" loading="lazy"
               onerror="this.onerror=null; if(window.SITE_CONFIG) this.src=window.SITE_CONFIG.defaultBackdrop;">
          <div class="continue-card-vignette"></div>
          <div class="continue-card-badge">
            <i class="fa-solid fa-clock"></i> ${subText}
          </div>
          <button type="button" class="continue-card-remove-btn" title="Remove from Continue Watching" data-remove-id="${escHtml(item.id || item.slug)}">
            <i class="fa-solid fa-xmark"></i>
          </button>
          <div class="continue-card-play-hover">
            <i class="fa-solid fa-play"></i>
          </div>
          <div class="continue-card-body">
            <h4 class="continue-card-title">${escHtml(item.title)}</h4>
            <span class="continue-card-sub">${item.year ? item.year : ''} • ${Math.round(item.percentage)}% watched</span>
          </div>
          <div class="continue-card-progress-bar">
            <div class="continue-card-progress-fill" style="width:${Math.min(100, Math.max(4, item.percentage))}%"></div>
          </div>
        </a>
      </div>
    `;
  }).join('');

  // Wire remove buttons
  track.querySelectorAll('.continue-card-remove-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      const id = btn.dataset.removeId;
      if (id && window.FilmSubUser) {
        window.FilmSubUser.ContinueWatching.remove(id);
        renderContinueWatchingShelf();
      }
    });
  });

  // Wire clear all button
  const clearBtn = document.getElementById('btn-clear-continue');
  if (clearBtn && !clearBtn._bound) {
    clearBtn._bound = true;
    clearBtn.addEventListener('click', () => {
      if (confirm('Clear all Continue Watching items?')) {
        window.FilmSubUser.ContinueWatching.clear();
        renderContinueWatchingShelf();
      }
    });
  }
}

// ---- Personalized Recommendations Shelf ("For You") ----
function renderForYouShelf() {
  const section = document.getElementById('for-you-section');
  const track = document.getElementById('for-you-track');
  const titleEl = document.getElementById('for-you-title');
  if (!section || !track) return;

  const allM = (typeof allMovies !== 'undefined' && allMovies.length > 0) ? allMovies : (window.FILMSUB_DATA?.movies || []);
  if (!allM || allM.length === 0) return;

  let topGenres = [];
  if (window.FilmSubUser && typeof window.FilmSubUser.Affinity?.getTopGenres === 'function') {
    topGenres = window.FilmSubUser.Affinity.getTopGenres(2);
  }

  // Fallback to Action & Sci-Fi if user is fresh
  if (!topGenres || topGenres.length === 0) {
    topGenres = ['action', 'sci-fi'];
  }

  const matched = allM.filter(m => {
    if (!m || !Array.isArray(m.genres)) return false;
    return m.genres.some(g => topGenres.includes(String(g).toLowerCase()));
  });

  const catalog = groupSeriesCatalog(matched.length > 5 ? matched : allM).slice(0, 10);
  if (catalog.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  if (titleEl) {
    const formattedGenres = topGenres.map(g => g.charAt(0).toUpperCase() + g.slice(1)).join(' & ');
    titleEl.innerHTML = `<i class="fa-solid fa-bullseye" style="color:var(--accent)"></i> Top Picks For You (${formattedGenres})`;
  }

  track.innerHTML = catalog.map(m => generateMovieCard(m)).join('');
}

// ---- User Profile, Dropdown & Lists Manager ----
function initUserUI() {
  if (!window.FilmSubUser) return;

  const profilePill = document.getElementById('user-profile-pill');
  const profileDrawer = document.getElementById('user-dropdown-drawer');
  const greetingName = document.getElementById('user-greeting-name');
  const avatarImg = document.getElementById('user-avatar-img');
  const drawerAvatar = document.getElementById('user-drawer-avatar');
  const drawerName = document.getElementById('user-drawer-name');
  const drawerStatus = document.getElementById('user-drawer-status');
  const badgeContinue = document.getElementById('badge-drawer-continue');
  const badgeFavorites = document.getElementById('badge-drawer-favorites');
  const badgeWatchlater = document.getElementById('badge-drawer-watchlater');
  const badgeHistory = document.getElementById('badge-drawer-history');
  const headerLaterBadge = document.getElementById('header-watchlater-count');
  const btnSignIn = document.getElementById('btn-drawer-signin');
  const btnSignOut = document.getElementById('btn-drawer-signout');

  function updateUI() {
    const profile = window.FilmSubUser.Profile.get();
    const favCount = window.FilmSubUser.Favorites.count();
    const laterCount = window.FilmSubUser.WatchLater.count();
    const continueCount = window.FilmSubUser.ContinueWatching.getAll().length;
    const historyCount = window.FilmSubUser.WatchHistory.getAll().length;

    if (greetingName) greetingName.textContent = `Hello, ${profile.given_name || 'Friend'} 👋`;
    if (avatarImg) avatarImg.src = profile.picture || '';
    if (drawerAvatar) drawerAvatar.src = profile.picture || '';
    if (drawerName) drawerName.textContent = profile.name || 'User';
    if (drawerStatus) drawerStatus.textContent = profile.isGuest ? 'Guest Profile (Local)' : 'Google Verified Account';

    if (badgeContinue) badgeContinue.textContent = continueCount;
    if (badgeFavorites) badgeFavorites.textContent = favCount;
    if (badgeWatchlater) badgeWatchlater.textContent = laterCount;
    if (badgeHistory) badgeHistory.textContent = historyCount;

    if (headerLaterBadge) {
      if (laterCount > 0) {
        headerLaterBadge.textContent = laterCount > 99 ? '99+' : laterCount;
        headerLaterBadge.style.display = 'flex';
      } else {
        headerLaterBadge.style.display = 'none';
      }
    }

    if (btnSignIn && btnSignOut) {
      if (profile.isGuest) {
        btnSignIn.style.display = 'flex';
        btnSignIn.innerHTML = '<i class="fa-brands fa-google"></i> Sign In / Edit Profile';
        btnSignOut.style.display = 'none';
      } else {
        btnSignIn.style.display = 'none';
        btnSignOut.style.display = 'flex';
      }
    }
  }

  updateUI();

  // Toggle drawer
  if (profilePill && profileDrawer && !profilePill._bound) {
    profilePill._bound = true;
    profilePill.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = profileDrawer.classList.toggle('open');
      profilePill.classList.toggle('active', open);
    });

    document.addEventListener('click', (e) => {
      if (!profileDrawer.contains(e.target) && !profilePill.contains(e.target)) {
        profileDrawer.classList.remove('open');
        profilePill.classList.remove('active');
      }
    });
  }

  // Header Watch Later button click
  const headerLaterBtn = document.getElementById('header-watchlater-btn');
  if (headerLaterBtn && !headerLaterBtn._bound) {
    headerLaterBtn._bound = true;
    headerLaterBtn.addEventListener('click', () => {
      openUserListModal('watchlater');
    });
  }

  // Drawer buttons
  const linkDrawerCont = document.getElementById('link-drawer-continue');
  if (linkDrawerCont && !linkDrawerCont._bound) {
    linkDrawerCont._bound = true;
    linkDrawerCont.addEventListener('click', (e) => {
      e.preventDefault();
      if (profileDrawer) profileDrawer.classList.remove('open');
      openUserListModal('continue');
    });
  }

  const btnDrawerFav = document.getElementById('btn-drawer-favorites');
  if (btnDrawerFav && !btnDrawerFav._bound) {
    btnDrawerFav._bound = true;
    btnDrawerFav.addEventListener('click', () => {
      if (profileDrawer) profileDrawer.classList.remove('open');
      openUserListModal('favorites');
    });
  }

  const btnDrawerLater = document.getElementById('btn-drawer-watchlater');
  if (btnDrawerLater && !btnDrawerLater._bound) {
    btnDrawerLater._bound = true;
    btnDrawerLater.addEventListener('click', () => {
      if (profileDrawer) profileDrawer.classList.remove('open');
      openUserListModal('watchlater');
    });
  }

  const btnDrawerHist = document.getElementById('btn-drawer-history');
  if (btnDrawerHist && !btnDrawerHist._bound) {
    btnDrawerHist._bound = true;
    btnDrawerHist.addEventListener('click', () => {
      if (profileDrawer) profileDrawer.classList.remove('open');
      openUserListModal('history');
    });
  }

  // Profile Avatar Picker Shortcut in Drawer
  const btnDrawerChangeAv = document.getElementById('btn-drawer-change-avatar');
  if (btnDrawerChangeAv && !btnDrawerChangeAv._bound) {
    btnDrawerChangeAv._bound = true;
    btnDrawerChangeAv.addEventListener('click', () => {
      if (profileDrawer) profileDrawer.classList.remove('open');
      openAvatarPickerModal();
    });
  }

  // Explore Global Cinema Menu Shortcut in Drawer
  const btnDrawerOpenMenu = document.getElementById('btn-drawer-open-menu');
  if (btnDrawerOpenMenu && !btnDrawerOpenMenu._bound) {
    btnDrawerOpenMenu._bound = true;
    btnDrawerOpenMenu.addEventListener('click', () => {
      if (profileDrawer) profileDrawer.classList.remove('open');
      if (typeof window.openCinemaDrawer === 'function') {
        window.openCinemaDrawer();
      }
    });
  }

  // Sign in / Edit profile
  if (btnSignIn && !btnSignIn._bound) {
    btnSignIn._bound = true;
    btnSignIn.addEventListener('click', () => {
      const current = window.FilmSubUser.Profile.get();
      const name = prompt('Enter your name / nickname for your profile:', current.isGuest ? (current.name.startsWith('Guest #') ? '' : current.name) : current.name);
      if (name && name.trim()) {
        window.FilmSubUser.Profile.setCustomName(name.trim());
        if (typeof showToast === 'function') showToast(`Profile updated to "${name.trim()}"! 👋`);
      }
    });
  }

  // Sign out
  if (btnSignOut && !btnSignOut._bound) {
    btnSignOut._bound = true;
    btnSignOut.addEventListener('click', () => {
      window.FilmSubUser.Profile.signOut();
      if (typeof showToast === 'function') showToast('Signed out. Switched to Guest Profile.');
    });
  }

  // React dynamically to profile & list updates
  window.addEventListener('fz:profile-updated', () => updateUI());
  window.addEventListener('fz:lists-updated', () => updateUI());

  // Setup Delegated Click Listener for Card Quick Actions (❤️ Favorite & ⏱️ Watch Later)
  if (!window._cardActionBound) {
    window._cardActionBound = true;
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.btn-card-action');
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();

      const movieId = btn.dataset.movieId;
      if (!movieId || !window.FilmSubUser) return;
      const movie = (allMovies || []).find(m => (m.id === movieId || m.slug === movieId)) || { id: movieId, title: 'Movie' };

      if (btn.classList.contains('fav')) {
        const isAdded = window.FilmSubUser.Favorites.toggle(movie);
        btn.classList.toggle('active', isAdded);
        btn.classList.add('burst');
        setTimeout(() => btn.classList.remove('burst'), 400);
        const icon = btn.querySelector('i');
        if (icon) icon.className = isAdded ? 'fa-solid fa-heart' : 'fa-regular fa-heart';
        if (typeof showToast === 'function') showToast(isAdded ? `Added to Favorites ❤️` : `Removed from Favorites`);
      } else if (btn.classList.contains('later')) {
        const isAdded = window.FilmSubUser.WatchLater.toggle(movie);
        btn.classList.toggle('active', isAdded);
        const icon = btn.querySelector('i');
        if (icon) icon.className = isAdded ? 'fa-solid fa-clock' : 'fa-regular fa-clock';
        if (typeof showToast === 'function') showToast(isAdded ? `Saved to Watch Later ⏱️` : `Removed from Watch Later`);
      }
    });
  }

  // Listen to state changes
  window.addEventListener('fz:state-changed', updateUI);
  window.addEventListener('fz:continue-changed', renderContinueWatchingShelf);
}

// ---- World-Class VIP Cinema Collection Hub (User Profile & Library Modal) ----
function formatVipTime(sec) {
  if (!sec || isNaN(sec)) return '0:00';
  sec = Math.round(sec);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${rm}m`;
  }
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

function openUserListModal(initialTab = 'favorites') {
  let modal = document.getElementById('user-list-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'user-list-modal';
    modal.className = 'user-list-modal-backdrop';
    modal.innerHTML = `
      <div class="user-list-modal-dialog vip-cinema-hub-dialog">
        <!-- VIP Cinema Pass Banner Header -->
        <div class="vip-hub-header">
          <div class="vip-hub-profile-row">
            <div class="vip-hub-avatar-wrap" id="vip-hub-avatar-trigger" title="Click to Change Profile Picture">
              <img src="" alt="User" class="vip-hub-avatar" id="vip-hub-avatar">
              <span class="vip-hub-halo"></span>
              <span class="vip-hub-avatar-edit-badge" title="Change Avatar"><i class="fa-solid fa-camera"></i></span>
            </div>
            <input type="file" id="vip-avatar-file-input" accept="image/*" style="display:none">
            <div class="vip-hub-meta">
              <div class="vip-hub-name-wrap">
                <h3 class="vip-hub-name" id="vip-hub-name">Loading...</h3>
                <button type="button" class="vip-hub-edit-btn" id="vip-hub-edit-btn" title="Edit Profile Name">
                  <i class="fa-solid fa-pen-to-square"></i>
                </button>
              </div>
              <div class="vip-hub-badge-row">
                <span class="vip-hub-badge vip-gold" id="vip-hub-status-badge">
                  <i class="fa-solid fa-crown"></i> VIP Cinema Pass
                </span>
              </div>
            </div>
          </div>
          <button type="button" class="user-list-modal-close" id="vip-hub-close-btn" aria-label="Close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- Real-Time Cinema Navigation Tabs -->
        <div class="vip-hub-tabs-nav" id="vip-hub-tabs-nav" role="tablist">
          <button type="button" class="vip-tab-btn" data-tab="favorites" role="tab">
            <img src="https://fonts.gstatic.com/s/e/notoemoji/latest/2764_fe0f/512.webp" class="tab-3d-icon" alt="Heart">
            <span>Favorites</span>
            <span class="vip-tab-count" id="vip-count-favorites">0</span>
          </button>
          <button type="button" class="vip-tab-btn" data-tab="watchlater" role="tab">
            <img src="https://fonts.gstatic.com/s/e/notoemoji/latest/1f37f/512.webp" class="tab-3d-icon" alt="Watch Later">
            <span>Watch Later</span>
            <span class="vip-tab-count" id="vip-count-watchlater">0</span>
          </button>
          <button type="button" class="vip-tab-btn" data-tab="continue" role="tab">
            <img src="https://fonts.gstatic.com/s/e/notoemoji/latest/1f525/512.webp" class="tab-3d-icon" alt="Continue">
            <span>Continue Watching</span>
            <span class="vip-tab-count" id="vip-count-continue">0</span>
          </button>
          <button type="button" class="vip-tab-btn" data-tab="history" role="tab">
            <i class="fa-solid fa-clock-rotate-left tab-history-icon"></i>
            <span>History</span>
            <span class="vip-tab-count" id="vip-count-history">0</span>
          </button>
        </div>

        <!-- Tab Toolbar (Counter & Clear Action) -->
        <div class="vip-hub-toolbar">
          <div class="vip-hub-toolbar-info" id="vip-hub-toolbar-info">Showing collection</div>
          <div class="vip-hub-toolbar-actions">
            <button type="button" class="vip-clear-btn" id="vip-hub-clear-btn" style="display:none">
              <i class="fa-solid fa-trash-can"></i> Clear List
            </button>
          </div>
        </div>

        <!-- Dynamic Collection Grid / List Body -->
        <div class="user-list-modal-body vip-hub-body" id="user-list-modal-body">
          <!-- Dynamically injected items -->
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    // Close handlers
    modal.querySelector('#vip-hub-close-btn').addEventListener('click', () => {
      modal.classList.remove('open');
    });
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.classList.remove('open');
    });

    // Wire Avatar Photo Changer
    const avTrigger = modal.querySelector('#vip-hub-avatar-trigger');
    const avInput = modal.querySelector('#vip-avatar-file-input');
    if (avTrigger && avInput) {
      avTrigger.addEventListener('click', () => {
        openAvatarPickerModal();
      });

      avInput.addEventListener('change', (e) => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        if (!file.type.startsWith('image/')) {
          if (typeof showToast === 'function') showToast('Please select an image file 🖼️');
          return;
        }

        const reader = new FileReader();
        reader.onload = (ev) => {
          const img = new Image();
          img.onload = () => {
            const canvas = document.createElement('canvas');
            canvas.width = 128;
            canvas.height = 128;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, 128, 128);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.85);

            window.FilmSubUser.Profile.setPicture(dataUrl);
            renderVipHeader();
            const mainAv = document.getElementById('user-avatar-img');
            if (mainAv) mainAv.src = dataUrl;
            const drawerAv = document.getElementById('user-drawer-avatar');
            if (drawerAv) drawerAv.src = dataUrl;
            const slideAv = document.getElementById('nav-drawer-avatar');
            if (slideAv) slideAv.src = dataUrl;

            if (typeof showToast === 'function') showToast('Profile photo updated! ✨');
          };
          img.src = ev.target.result;
        };
        reader.readAsDataURL(file);
      });
    }

    // Wire edit nickname button
    modal.querySelector('#vip-hub-edit-btn').addEventListener('click', () => {
      const current = window.FilmSubUser.Profile.get();
      const newName = prompt('Enter custom profile name / nickname:', current.isGuest ? (current.name.startsWith('Guest #') ? '' : current.name) : current.name);
      if (newName && newName.trim()) {
        window.FilmSubUser.Profile.setCustomName(newName.trim());
        if (typeof showToast === 'function') showToast(`Profile name updated to "${newName.trim()}"! 👋`);
        renderVipHeader();
      }
    });

    // Tab buttons click
    modal.querySelectorAll('.vip-tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        switchVipTab(tab);
      });
    });

    // Clear list action
    modal.querySelector('#vip-hub-clear-btn').addEventListener('click', () => {
      const activeTab = modal.dataset.currentTab || 'favorites';
      if (activeTab === 'history') {
        if (confirm('Clear all your watch history?')) {
          window.FilmSubUser.WatchHistory.clear();
          switchVipTab('history');
          if (typeof showToast === 'function') showToast('Watch history cleared! 🧹');
        }
      } else if (activeTab === 'continue') {
        if (confirm('Clear all continue watching progress?')) {
          window.FilmSubUser.ContinueWatching.clear();
          switchVipTab('continue');
          if (typeof showToast === 'function') showToast('Continue watching progress cleared! 🧹');
        }
      } else if (activeTab === 'favorites') {
        if (confirm('Clear your favorites list?')) {
          window.FilmSubUser.Favorites.clear();
          switchVipTab('favorites');
          if (typeof showToast === 'function') showToast('Favorites list cleared! 🧹');
        }
      } else if (activeTab === 'watchlater') {
        if (confirm('Clear your watch later queue?')) {
          window.FilmSubUser.WatchLater.clear();
          switchVipTab('watchlater');
          if (typeof showToast === 'function') showToast('Watch later queue cleared! 🧹');
        }
      }
    });
  }

  function renderVipHeader() {
    if (!window.FilmSubUser) return;
    const profile = window.FilmSubUser.Profile.get();
    const avatarEl = document.getElementById('vip-hub-avatar');
    const nameEl = document.getElementById('vip-hub-name');
    const statusBadge = document.getElementById('vip-hub-status-badge');

    if (avatarEl) avatarEl.src = profile.picture || '';
    if (nameEl) nameEl.textContent = profile.name || 'Cinema Fan';

    if (statusBadge) {
      statusBadge.innerHTML = profile.isGuest 
        ? '<i class="fa-solid fa-crown" style="color:#ffd700"></i> VIP Guest Cinema Pass' 
        : '<i class="fa-brands fa-google" style="color:#4285f4"></i> Verified Google Member';
    }

    // Update Tab Counters
    const favCount = window.FilmSubUser.Favorites.count();
    const laterCount = window.FilmSubUser.WatchLater.count();
    const contCount = window.FilmSubUser.ContinueWatching.getAll().length;
    const histCount = window.FilmSubUser.WatchHistory.getAll().length;

    const cfEl = document.getElementById('vip-count-favorites');
    const clEl = document.getElementById('vip-count-watchlater');
    const ccEl = document.getElementById('vip-count-continue');
    const chEl = document.getElementById('vip-count-history');

    if (cfEl) cfEl.textContent = favCount;
    if (clEl) clEl.textContent = laterCount;
    if (ccEl) ccEl.textContent = contCount;
    if (chEl) chEl.textContent = histCount;
  }

  function switchVipTab(tab) {
    modal.dataset.currentTab = tab;
    modal.querySelectorAll('.vip-tab-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.tab === tab);
    });

    renderVipHeader();

    const bodyEl = document.getElementById('user-list-modal-body');
    const toolbarInfo = document.getElementById('vip-hub-toolbar-info');
    const clearBtn = document.getElementById('vip-hub-clear-btn');
    if (!bodyEl) return;

    let items = [];
    let emptyTitle = 'No movies saved here yet';
    let emptySub = 'Explore movies and tap the button to add them!';

    if (tab === 'favorites') {
      items = window.FilmSubUser.Favorites.getAll();
      toolbarInfo.innerHTML = `
        <div class="vip-hub-count-pill">
          <i class="fa-solid fa-heart" style="color:#ff3366"></i>
          <span>${items.length === 1 ? '1 Favorite Title' : `${items.length} Favorite Titles`}</span>
        </div>`;
      emptyTitle = 'කැමතිම චිත්‍රපට තවම නැත';
      emptySub = 'චිත්‍රපටයක ඇති ❤️ Heart අයිකනය ඔබා මෙහි සුරැකීමට හැකිය.';
      if (clearBtn) clearBtn.style.display = items.length > 0 ? 'inline-flex' : 'none';
    } else if (tab === 'watchlater') {
      items = window.FilmSubUser.WatchLater.getAll();
      toolbarInfo.innerHTML = `
        <div class="vip-hub-count-pill">
          <i class="fa-solid fa-clock" style="color:#00b4d8"></i>
          <span>${items.length === 1 ? '1 Title Saved for Later' : `${items.length} Titles Saved for Later`}</span>
        </div>`;
      emptyTitle = 'පසුව නැරඹීමට එක් කළ චිත්‍රපට තවම නැත';
      emptySub = 'චිත්‍රපටයක ඇති ⏱️ Watch Later ඔබා මෙහි සුරකින්න.';
      if (clearBtn) clearBtn.style.display = items.length > 0 ? 'inline-flex' : 'none';
    } else if (tab === 'continue') {
      items = window.FilmSubUser.ContinueWatching.getAll();
      toolbarInfo.innerHTML = `
        <div class="vip-hub-count-pill">
          <i class="fa-solid fa-play" style="color:var(--accent)"></i>
          <span>${items.length === 1 ? '1 Title In-Progress' : `${items.length} Titles In-Progress`}</span>
        </div>`;
      emptyTitle = 'නැවතු තැනින් නැවත බලන්න තවම චිත්‍රපට නැත';
      emptySub = 'ඔබ නරඹමින් සිටි චිත්‍රපට නැවතු වෙලාවේ සිටම මෙතැනින් Play කළ හැක.';
      if (clearBtn) clearBtn.style.display = items.length > 0 ? 'inline-flex' : 'none';
    } else if (tab === 'history') {
      items = window.FilmSubUser.WatchHistory.getAll();
      toolbarInfo.innerHTML = `
        <div class="vip-hub-count-pill">
          <i class="fa-solid fa-clock-rotate-left" style="color:#f39c12"></i>
          <span>${items.length === 1 ? '1 Title in History' : `${items.length} Titles in History`}</span>
        </div>`;
      emptyTitle = 'Watch History හිස්ව ඇත';
      emptySub = 'ඔබ නරඹන සියලුම චිත්‍රපට වෙලාවත් සමග මෙහි සටහන් වේ.';
      if (clearBtn) clearBtn.style.display = items.length > 0 ? 'inline-flex' : 'none';
    }

    if (!items || items.length === 0) {
      bodyEl.innerHTML = `
        <div class="vip-hub-empty-state">
          <div class="vip-empty-icon-box">
            <img src="https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Popcorn/3D/popcorn_3d.png" alt="Empty" class="vip-empty-3d-img">
          </div>
          <h4 class="vip-empty-title">${emptyTitle}</h4>
          <p class="vip-empty-sub">${emptySub}</p>
          <button type="button" class="vip-empty-browse-btn" onclick="document.getElementById('user-list-modal').classList.remove('open'); window.scrollTo({top: 600, behavior: 'smooth'});">
            <i class="fa-solid fa-compass"></i> Explore Trending Movies
          </button>
        </div>
      `;
      return;
    }

    // Render Luxury Collection Cards
    bodyEl.innerHTML = `
      <div class="vip-hub-cards-grid">
        ${items.map(m => {
          const movieId = m.id || m.slug;
          const movieUrl = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(m.slug || m.id)}${m.currentTime ? `&resume=${m.currentTime}` : ''}`;
          const isContinue = tab === 'continue';
          const pct = isContinue && m.duration ? Math.min(100, Math.round((m.currentTime / m.duration) * 100)) : 0;
          const timeLabel = isContinue && m.currentTime ? `${formatVipTime(m.currentTime)} / ${formatVipTime(m.duration)}` : '';

          return `
            <div class="vip-card-item" data-movie-id="${movieId}">
              <a href="${movieUrl}" class="vip-card-poster-wrap">
                <img src="${m.poster || SITE_CONFIG.defaultPoster}" alt="${m.title}" class="vip-card-poster" loading="lazy"
                     onerror="this.onerror=null; if(window.SITE_CONFIG) this.src=window.SITE_CONFIG.defaultPoster;">
                <span class="vip-card-rating">★ ${m.rating || '0.0'}</span>
                ${isContinue ? `<div class="vip-card-progress-bar"><div class="vip-card-progress-fill" style="width:${pct}%"></div></div>` : ''}
              </a>
              <div class="vip-card-details">
                <a href="${movieUrl}" class="vip-card-title" title="${escHtml(m.title)}">${escHtml(m.title)}</a>
                <div class="vip-card-subinfo">
                  <span class="vip-card-year">${m.year || 'Movie'}</span>
                  ${timeLabel ? `<span class="vip-card-time-badge">${timeLabel} (${pct}%)</span>` : ''}
                </div>
                <div class="vip-card-actions-row">
                  <a href="${movieUrl}" class="vip-card-watch-btn">
                    <i class="fa-solid fa-play"></i> ${isContinue ? 'Resume' : 'Watch'}
                  </a>
                  <button type="button" class="vip-card-remove-btn" title="Remove from list" data-action-remove="${tab}" data-movie-id="${movieId}">
                    <i class="fa-solid fa-trash-can"></i>
                  </button>
                </div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    `;

    // Wire Remove Buttons on cards
    bodyEl.querySelectorAll('[data-action-remove]').forEach(rmBtn => {
      rmBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const rmTab = rmBtn.dataset.actionRemove;
        const mId = rmBtn.dataset.movieId;
        if (!mId || !window.FilmSubUser) return;

        if (rmTab === 'favorites') {
          window.FilmSubUser.Favorites.remove(mId);
          if (typeof showToast === 'function') showToast('Removed from Favorites ❤️');
        } else if (rmTab === 'watchlater') {
          window.FilmSubUser.WatchLater.remove(mId);
          if (typeof showToast === 'function') showToast('Removed from Watch Later ⏱️');
        } else if (rmTab === 'continue') {
          window.FilmSubUser.ContinueWatching.remove(mId);
          if (typeof showToast === 'function') showToast('Removed from Continue Watching ⏯️');
        } else if (rmTab === 'history') {
          window.FilmSubUser.WatchHistory.remove(mId);
          if (typeof showToast === 'function') showToast('Removed from Watch History 📜');
        }

        switchVipTab(rmTab);
      });
    });
  }

  // Open & switch to requested tab
  renderVipHeader();
  switchVipTab(initialTab);
  modal.classList.add('open');
}

// ---- Profile Avatar Picker & 3D Cinema Avatar Studio ----
const VIP_PRESET_AVATARS = [
  { id: 'popcorn', name: 'Popcorn Cinephile', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Popcorn/3D/popcorn_3d.png' },
  { id: 'director', name: 'Master Director', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Clapper%20board/3D/clapper_board_3d.png' },
  { id: 'fire', name: 'Flame Legend', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Fire/3D/fire_3d.png' },
  { id: 'crown', name: 'VIP Royalty', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Crown/3D/crown_3d.png' },
  { id: 'star', name: 'Cinema Star', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Glowing%20star/3D/glowing_star_3d.png' },
  { id: 'rocket', name: 'Cosmic Voyager', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rocket/3D/rocket_3d.png' },
  { id: 'party', name: 'Party Master', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Party%20popper/3D/party_popper_3d.png' },
  { id: 'heart', name: 'Movie Lover', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Red%20heart/3D/red_heart_3d.png' },
  { id: 'mindblown', name: 'Plot Wizard', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Exploding%20head/3D/exploding_head_3d.png' },
  { id: 'laugh', name: 'Comedy Fan', img: 'https://cdn.jsdelivr.net/gh/microsoft/fluentui-emoji@main/assets/Rolling%20on%20the%20floor%20laughing/3D/rolling_on_the_floor_laughing_3d.png' }
];

function applyNewAvatar(avatarUrl) {
  if (!window.FilmSubUser || !window.FilmSubUser.Profile) return;
  window.FilmSubUser.Profile.setPicture(avatarUrl);
  const selectors = ['#vip-hub-avatar', '#user-avatar-img', '#nav-drawer-avatar', '#user-drawer-avatar', '#mob-user-avatar'];
  selectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      el.src = avatarUrl;
    });
  });
  if (typeof showToast === 'function') showToast('Profile avatar updated! ✨');
}

function openAvatarPickerModal() {
  let modal = document.getElementById('vip-avatar-picker-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'vip-avatar-picker-modal';
    modal.className = 'vip-avatar-picker-overlay';
    modal.innerHTML = `
      <div class="vip-avatar-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="avatar-picker-title">
        <div class="vip-avatar-picker-header">
          <div class="vip-avatar-picker-title-wrap">
            <i class="fa-solid fa-wand-magic-sparkles" style="color:var(--accent,#e50914)"></i>
            <h3 id="avatar-picker-title">Profile Avatar Studio</h3>
          </div>
          <button type="button" class="vip-avatar-picker-close" id="avatar-picker-close-btn" aria-label="Close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>
        <div class="vip-avatar-picker-body">
          <div class="vip-avatar-upload-cta">
            <button type="button" class="vip-upload-photo-btn" id="vip-upload-trigger-btn">
              <i class="fa-solid fa-camera"></i>
              <span>Upload Custom Photo (Device / Camera)</span>
            </button>
            <p class="vip-upload-subtext">Supports PNG, JPG, WebP. Automatically centered & optimized for your cinema profile.</p>
          </div>
          <div class="vip-avatar-divider">
            <span>OR CHOOSE A 3D CINEMA AVATAR</span>
          </div>
          <div class="vip-avatar-grid" id="vip-avatar-presets-grid">
            ${VIP_PRESET_AVATARS.map(p => `
              <button type="button" class="vip-avatar-preset-btn" data-avatar-uri="${p.img}" title="${p.name}">
                <div class="preset-avatar-img-wrap">
                  <img src="${p.img}" alt="${p.name}" class="preset-avatar-img" loading="lazy">
                </div>
                <span class="preset-avatar-label">${p.name}</span>
              </button>
            `).join('')}
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.querySelector('#avatar-picker-close-btn').addEventListener('click', () => {
      modal.classList.remove('open');
    });
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.classList.remove('open');
    });

    modal.querySelector('#vip-upload-trigger-btn').addEventListener('click', () => {
      let fileInp = document.getElementById('vip-avatar-file-input');
      if (!fileInp) {
        fileInp = document.createElement('input');
        fileInp.type = 'file';
        fileInp.id = 'vip-avatar-file-input';
        fileInp.accept = 'image/*';
        fileInp.style.display = 'none';
        document.body.appendChild(fileInp);
      }
      fileInp.onchange = (e) => {
        const file = e.target.files && e.target.files[0];
        if (!file) return;
        if (!file.type.startsWith('image/')) {
          if (typeof showToast === 'function') showToast('Please select an image file 🖼️');
          return;
        }

        const reader = new FileReader();
        reader.onload = (ev) => {
          const img = new Image();
          img.onload = () => {
            const canvas = document.createElement('canvas');
            canvas.width = 160;
            canvas.height = 160;
            const ctx = canvas.getContext('2d');
            const size = Math.min(img.width, img.height);
            const sx = (img.width - size) / 2;
            const sy = (img.height - size) / 2;
            ctx.drawImage(img, sx, sy, size, size, 0, 0, 160, 160);
            const dataUrl = canvas.toDataURL('image/jpeg', 0.88);
            applyNewAvatar(dataUrl);
            modal.classList.remove('open');
          };
          img.src = ev.target.result;
        };
        reader.readAsDataURL(file);
      };
      fileInp.click();
    });

    modal.querySelectorAll('.vip-avatar-preset-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const uri = btn.dataset.avatarUri;
        applyNewAvatar(uri);
        modal.classList.remove('open');
      });
    });
  }

  modal.classList.add('open');
}

// ---- Global Cinema Left Drawer (☰ Hamburger Menu) ----
function initGlobalCinemaDrawer() {
  if (!document.getElementById('cinema-global-nav-drawer')) {
    const backdrop = document.createElement('div');
    backdrop.id = 'cinema-global-nav-backdrop';
    backdrop.className = 'cinema-nav-drawer-backdrop';

    const drawer = document.createElement('aside');
    drawer.id = 'cinema-global-nav-drawer';
    drawer.className = 'cinema-nav-drawer-panel';
    drawer.setAttribute('aria-label', 'Global Cinema Navigation Menu');
    drawer.innerHTML = `
      <div class="cinema-drawer-header">
        <a href="index.html" class="drawer-brand-link">
          <img src="assets/img/filmzilla_brand_horizontal.png" alt="FilmZilla" class="drawer-brand-img">
        </a>
        <button type="button" class="cinema-drawer-close" id="cinema-drawer-close-btn" aria-label="Close Menu">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>

      <!-- User Profile Quick Card in Drawer -->
      <div class="cinema-drawer-user-card" id="cinema-drawer-user-card" title="Click to open VIP Cinema Hub">
        <div class="drawer-user-avatar-wrap">
          <img src="" alt="Avatar" id="nav-drawer-avatar" class="drawer-user-avatar">
          <span class="drawer-user-halo"></span>
        </div>
        <div class="drawer-user-info">
          <div class="drawer-user-name" id="nav-drawer-name">Cinema Fan</div>
          <div class="drawer-user-badge"><i class="fa-solid fa-crown" style="color:#f39c12"></i> VIP Cinema Member</div>
        </div>
        <button type="button" class="drawer-user-edit-btn" id="drawer-user-hub-btn" title="Open VIP Cinema Hub">
          <i class="fa-solid fa-chevron-right"></i>
        </button>
      </div>

      <div class="cinema-drawer-scrollable">
        <!-- Section: Browse -->
        <div class="cinema-drawer-section">
          <div class="drawer-section-title">EXPLORE</div>
          <ul class="drawer-nav-list">
            <li><a href="index.html" class="drawer-nav-item"><i class="fa-solid fa-house"></i><span>Home</span></a></li>
            <li><a href="teasers.html" class="drawer-nav-item drawer-nav-teaser"><i class="fa-solid fa-bolt" style="color:#ff3366"></i><span>30s Teasers (Reels)</span><span class="drawer-pill-hot" style="background:linear-gradient(45deg,#e50914,#ff3366)">NEW</span></a></li>
            <li><a href="search.html?type=movie" class="drawer-nav-item"><i class="fa-solid fa-film"></i><span>Movies</span><span class="drawer-pill-hot">POPULAR</span></a></li>
            <li><a href="search.html?type=tv" class="drawer-nav-item"><i class="fa-solid fa-tv"></i><span>TV Series</span></a></li>
            <li><a href="search.html?genre=Action" class="drawer-nav-item"><i class="fa-solid fa-bolt" style="color:#f39c12"></i><span>Action & Thriller</span></a></li>
            <li><a href="search.html?genre=Horror" class="drawer-nav-item"><i class="fa-solid fa-ghost" style="color:#e50914"></i><span>Horror & Mystery</span></a></li>
            <li><a href="search.html?genre=Animation" class="drawer-nav-item"><i class="fa-solid fa-wand-magic-sparkles" style="color:#a855f7"></i><span>Animation & Fantasy</span></a></li>
          </ul>
        </div>

        <!-- Section: Your Cinema Collection -->
        <div class="cinema-drawer-section">
          <div class="drawer-section-title">YOUR COLLECTION</div>
          <ul class="drawer-nav-list">
            <li>
              <button type="button" class="drawer-nav-item drawer-hub-btn" data-hub-tab="favorites">
                <i class="fa-solid fa-heart" style="color:#ff3366"></i>
                <span>Favorites</span>
                <span class="drawer-count-badge" id="drawer-count-fav">0</span>
              </button>
            </li>
            <li>
              <button type="button" class="drawer-nav-item drawer-hub-btn" data-hub-tab="watchlater">
                <i class="fa-solid fa-clock" style="color:#00b4d8"></i>
                <span>Watch Later</span>
                <span class="drawer-count-badge" id="drawer-count-later">0</span>
              </button>
            </li>
            <li>
              <button type="button" class="drawer-nav-item drawer-hub-btn" data-hub-tab="continue">
                <i class="fa-solid fa-play" style="color:#e50914"></i>
                <span>Continue Watching</span>
                <span class="drawer-count-badge" id="drawer-count-continue">0</span>
              </button>
            </li>
            <li>
              <button type="button" class="drawer-nav-item drawer-hub-btn" data-hub-tab="history">
                <i class="fa-solid fa-clock-rotate-left" style="color:#f39c12"></i>
                <span>Watch History</span>
                <span class="drawer-count-badge" id="drawer-count-history">0</span>
              </button>
            </li>
          </ul>
        </div>

        <!-- Section: VIP Cinema Experiences -->
        <div class="cinema-drawer-section">
          <div class="drawer-section-title">SPECIAL EXPERIENCES</div>
          <ul class="drawer-nav-list">
            <li>
              <button type="button" class="drawer-nav-item" id="drawer-roulette-btn">
                <i class="fa-solid fa-dice" style="color:#a855f7"></i>
                <span>Movie Roulette (Surprise Me)</span>
                <span class="drawer-pill-vip">VIP</span>
              </button>
            </li>
            <li>
              <button type="button" class="drawer-nav-item" id="drawer-change-avatar-btn">
                <i class="fa-solid fa-camera" style="color:#10b981"></i>
                <span>Change Profile Photo / 3D Avatar</span>
              </button>
            </li>
            <li>
              <a href="https://t.me/FilmZillaOfficial" target="_blank" rel="noopener" class="drawer-nav-item">
                <i class="fa-brands fa-telegram" style="color:#229ed9"></i>
                <span>Official Telegram Channel</span>
              </a>
            </li>
            <li>
              <a href="https://t.me/FilmZillaRequestBot" target="_blank" rel="noopener" class="drawer-nav-item">
                <i class="fa-solid fa-paper-plane" style="color:#10b981"></i>
                <span>Request Movie / Series</span>
              </a>
            </li>
          </ul>
        </div>
      </div>
    `;

    document.body.appendChild(backdrop);
    document.body.appendChild(drawer);

    const closeDrawer = () => {
      drawer.classList.remove('open');
      backdrop.classList.remove('open');
      document.body.classList.remove('cinema-drawer-lock');
    };

    backdrop.addEventListener('click', closeDrawer);
    const closeBtn = drawer.querySelector('#cinema-drawer-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', closeDrawer);

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && drawer.classList.contains('open')) closeDrawer();
    });

    drawer.querySelectorAll('.drawer-hub-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.hubTab;
        closeDrawer();
        openUserListModal(tab);
      });
    });

    const hubTrigger = drawer.querySelector('#cinema-drawer-user-card');
    if (hubTrigger) {
      hubTrigger.addEventListener('click', () => {
        closeDrawer();
        openUserListModal('favorites');
      });
    }

    const rouBtn = drawer.querySelector('#drawer-roulette-btn');
    if (rouBtn) {
      rouBtn.addEventListener('click', () => {
        closeDrawer();
        if (typeof window.openMovieRouletteModal === 'function') {
          window.openMovieRouletteModal();
        } else if (typeof showToast === 'function') {
          showToast('Opening Cinema Roulette... 🎲');
        }
      });
    }

    const changeAvBtn = drawer.querySelector('#drawer-change-avatar-btn');
    if (changeAvBtn) {
      changeAvBtn.addEventListener('click', () => {
        closeDrawer();
        openAvatarPickerModal();
      });
    }
  }

  const safeCount = (obj) => {
    if (!obj) return 0;
    if (typeof obj.count === 'function') {
      try { return obj.count(); } catch (_) { return 0; }
    }
    if (typeof obj.getAll === 'function') {
      try { return obj.getAll().length; } catch (_) { return 0; }
    }
    return 0;
  };

  const openDrawer = () => {
    let drawer = document.getElementById('cinema-global-nav-drawer');
    let backdrop = document.getElementById('cinema-global-nav-backdrop');
    if (!drawer || !backdrop) {
      initGlobalCinemaDrawer();
      drawer = document.getElementById('cinema-global-nav-drawer');
      backdrop = document.getElementById('cinema-global-nav-backdrop');
      if (!drawer || !backdrop) return;
    }

    try {
      if (window.FilmSubUser && window.FilmSubUser.Profile) {
        const p = window.FilmSubUser.Profile.get();
        const av = drawer.querySelector('#nav-drawer-avatar');
        const name = drawer.querySelector('#nav-drawer-name');
        if (av) av.src = p.picture || 'assets/img/avatars/avatar_1.png';
        if (name) name.textContent = p.name || 'Cinema Fan';

        const favEl = drawer.querySelector('#drawer-count-fav');
        const laterEl = drawer.querySelector('#drawer-count-later');
        const contEl = drawer.querySelector('#drawer-count-continue');
        const histEl = drawer.querySelector('#drawer-count-history');

        if (favEl) favEl.textContent = safeCount(window.FilmSubUser.Favorites);
        if (laterEl) laterEl.textContent = safeCount(window.FilmSubUser.WatchLater);
        if (contEl) contEl.textContent = safeCount(window.FilmSubUser.ContinueWatching);
        if (histEl) histEl.textContent = safeCount(window.FilmSubUser.WatchHistory);
      }
    } catch (err) {
      console.warn('Cinema drawer user sync:', err);
    }

    drawer.classList.add('open');
    backdrop.classList.add('open');
    document.body.classList.add('cinema-drawer-lock');
  };

  window.openCinemaDrawer = openDrawer;
  window.openUserListModal = openUserListModal;
  window.openAvatarPickerModal = openAvatarPickerModal;

  const toggleBtns = document.querySelectorAll('#header-sidebar-toggle-btn, .header-sidebar-toggle-btn');
  toggleBtns.forEach(btn => {
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      openDrawer();
    };
  });
}

// Global delegated listener: Guarantee hamburger menu (☰) opens from any page/element
document.addEventListener('click', (e) => {
  const btn = e.target.closest('#header-sidebar-toggle-btn, .header-sidebar-toggle-btn');
  if (btn) {
    e.preventDefault();
    e.stopPropagation();
    if (typeof window.openCinemaDrawer === 'function') {
      window.openCinemaDrawer();
    } else {
      initGlobalCinemaDrawer();
      if (typeof window.openCinemaDrawer === 'function') {
        window.openCinemaDrawer();
      }
    }
  }
});

// ---- Search Overlay ----
function initSearchOverlay() {
  const overlay = document.getElementById('search-overlay');
  const input = document.getElementById('search-input');
  const liveResults = document.getElementById('search-results-live');
  const openBtn = document.getElementById('search-open-btn');
  const closeBtn = document.getElementById('search-close-btn');

  if (!overlay) return;

  const open = () => {
    overlay.classList.add('active');
    document.body.classList.add('no-scroll');
    setTimeout(() => input && input.focus(), 100);
  };
  const close = () => {
    overlay.classList.remove('active');
    document.body.classList.remove('no-scroll');
    if (input) input.value = '';
    if (liveResults) liveResults.innerHTML = '';
  };

  if (openBtn) openBtn.addEventListener('click', open);
  if (closeBtn) closeBtn.addEventListener('click', close);

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') close();
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); open(); }
  });

  if (input && liveResults) {
    input.addEventListener('input', debounce(() => {
      const q = input.value.trim().toLowerCase();
      if (!q) { liveResults.innerHTML = ''; return; }
      const matched = allMovies.filter(m =>
        (m.title || '').toLowerCase().includes(q) ||
        (m.title_si || '').toLowerCase().includes(q) ||
        (Array.isArray(m.genres) && m.genres.some(g => g.toLowerCase().includes(q)))
      );
      const results = groupSeriesCatalog(matched).slice(0, 12);
      if (results.length === 0) {
        liveResults.innerHTML = `<div class="search-no-results"><i class="fa-solid fa-film"></i> No results for "<strong>${escHtml(q)}</strong>"</div>`;
      } else {
        liveResults.innerHTML = results.map(m => generateMovieCard(m)).join('');
        liveResults.querySelectorAll('.movie-card').forEach(c => c.classList.add('is-card-visible'));
      }
    }, 250));

    // Full search on Enter
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        const q = input.value.trim();
        if (q) window.location.href = `search.html?q=${encodeURIComponent(q)}`;
      }
    });
  }
}

// ---- Render Movie Grid (search/browse pages) ----
function renderMovieGrid(containerId, movies) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const grouped = groupSeriesCatalog(movies);
  if (!grouped || grouped.length === 0) {
    el.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon"><i class="fa-solid fa-film"></i></div>
        <h3>No titles found</h3>
        <p>Try different keywords or browse by genre.</p>
      </div>`;
    return;
  }
  el.innerHTML = grouped.map(m => generateMovieCard(m)).join('');
  el.querySelectorAll('.movie-card').forEach((c, idx) => {
    c.style.transitionDelay = `${Math.min(idx * 0.03, 0.45)}s`;
    requestAnimationFrame(() => c.classList.add('is-card-visible'));
  });
}

// ---- Curated Multi-Row Movie Showcase Engine (3-4 Rows with Instant Filters) ----
let showcaseCurrentGenre = 'all';
let showcaseDisplayedLimit = 18; // 3-4 rows of 6 cards on desktop
let showcaseEventsBound = false;

function initMoviesShowcaseGrid() {
  const grid = document.getElementById('all-movies-grid');
  if (!grid) return;

  function getFilteredMovies() {
    const catalog = groupSeriesCatalog(allMovies);
    if (showcaseCurrentGenre === 'all') {
      return [...catalog].sort((a, b) => {
        const tA = _getMovieTimestamp(a);
        const tB = _getMovieTimestamp(b);
        if (tB !== tA) return tB - tA;
        const yA = Number(a.year) || 0;
        const yB = Number(b.year) || 0;
        return yB - yA;
      });
    }
    if (showcaseCurrentGenre === 'sinhala') {
      return getSinhalaFilms();
    }
    if (showcaseCurrentGenre === 'top-rated') {
      return [...catalog].sort((a, b) => {
        const rA = parseFloat(a.imdb || a.rating || 0);
        const rB = parseFloat(b.imdb || b.rating || 0);
        return rB - rA;
      });
    }
    if (showcaseCurrentGenre === 'favorites') {
      const favList = window.FilmSubUser ? window.FilmSubUser.Favorites.getAll() : [];
      const favIds = new Set(favList.map(f => f.id || f.slug));
      return catalog.filter(m => favIds.has(m.id) || favIds.has(m.slug));
    }
    return getMoviesByGenre(showcaseCurrentGenre);
  }

  function renderShowcase() {
    const list = getFilteredMovies();
    const countEl = document.getElementById('movies-showcase-count');
    if (countEl) countEl.textContent = `${list.length} Films`;

    const visible = list.slice(0, showcaseDisplayedLimit);
    grid.innerHTML = visible.map(m => generateMovieCard(m)).join('');

    // Smooth staggered reveal
    grid.querySelectorAll('.movie-card').forEach((c, idx) => {
      c.style.transitionDelay = `${Math.min(idx * 0.025, 0.35)}s`;
      requestAnimationFrame(() => c.classList.add('is-card-visible'));
    });

    const loadMoreWrap = document.getElementById('showcase-load-more-wrap');
    if (loadMoreWrap) {
      loadMoreWrap.style.display = showcaseDisplayedLimit >= list.length ? 'none' : 'flex';
    }
  }

  // Ensure event listeners are attached only once to prevent duplicate handler firing
  if (!showcaseEventsBound) {
    showcaseEventsBound = true;

    // Bind filter pills
    const pills = document.querySelectorAll('.showcase-filter-pill');
    pills.forEach(pill => {
      pill.addEventListener('click', () => {
        pills.forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        showcaseCurrentGenre = pill.dataset.genre || 'all';
        showcaseDisplayedLimit = 18;
        renderShowcase();
      });
    });

    // Load more button
    const loadMoreBtn = document.getElementById('btn-load-more-movies');
    if (loadMoreBtn) {
      loadMoreBtn.addEventListener('click', () => {
        showcaseDisplayedLimit += 12; // Append 2 more full rows
        renderShowcase();
      });
    }

    // Grid / Carousel view switcher
    const btnGrid = document.getElementById('btn-view-grid');
    const btnCarousel = document.getElementById('btn-view-carousel');
    const carouselWrap = document.getElementById('all-movies-carousel-wrap');
    const loadMoreWrap = document.getElementById('showcase-load-more-wrap');

    if (btnGrid && btnCarousel && carouselWrap) {
      btnGrid.addEventListener('click', () => {
        btnGrid.classList.add('active');
        btnCarousel.classList.remove('active');
        grid.style.display = 'grid';
        carouselWrap.style.display = 'none';
        if (loadMoreWrap) loadMoreWrap.style.display = showcaseDisplayedLimit >= getFilteredMovies().length ? 'none' : 'flex';
      });
      btnCarousel.addEventListener('click', () => {
        btnCarousel.classList.add('active');
        btnGrid.classList.remove('active');
        grid.style.display = 'none';
        carouselWrap.style.display = 'block';
        if (loadMoreWrap) loadMoreWrap.style.display = 'none';
        renderCarousel('new-releases-track', getNewReleases(), { isTrending: false });
      });
    }
  }

  renderShowcase();
}

// ---- Instant Zero-Lag Catalog Reveal Engine ----
function initScrollReveal() {
  const sections = document.querySelectorAll('.section, .cs-player-section, .cs-movie-details-section, .cs-downloads-section, .cs-related-section, .cs-series-section');
  sections.forEach(s => s.classList.add('is-visible', 'in-view'));
  document.querySelectorAll('.movie-card').forEach(c => {
    c.classList.add('is-card-visible', 'visible');
  });
}

// ---- View Counter ----
function trackView(slug) {
  if (!slug) return;
  const key = `view_${slug}`;
  const views = parseInt(localStorage.getItem(key) || '0', 10);
  localStorage.setItem(key, views + 1);
}

// ---- Show Toast ----
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container') || (() => {
    const c = document.createElement('div');
    c.className = 'toast-container';
    c.id = 'toast-container';
    document.body.appendChild(c);
    return c;
  })();
  const toast = document.createElement('div');
  toast.className = `toast${type === 'error' ? ' error' : ''}`;
  toast.innerHTML = `<i class="fa-solid fa-${type === 'error' ? 'circle-xmark' : 'circle-check'}"></i><span>${escHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

// ---- Utils ----
function debounce(fn, delay = 250) {
  let timer;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- CineSubz-Style Advanced Highlight Popup on Card Hover ----
function initHighlightPopup() {
  if (window.matchMedia && window.matchMedia('(hover: none)').matches) {
    // Touch screen device: skip hover popups so tap navigates instantly
    return;
  }

  let popup = document.getElementById('cs-highlight-popup');
  if (!popup) {
    popup = document.createElement('div');
    popup.id = 'cs-highlight-popup';
    popup.className = 'cs-highlight-popup';
    document.body.appendChild(popup);
  }

  let showTimer = null;
  let hideTimer = null;
  let activeCard = null;
  let isScrolling = false;
  let scrollTimeout = null;

  const hidePopup = () => {
    if (showTimer) { clearTimeout(showTimer); showTimer = null; }
    hideTimer = setTimeout(() => {
      popup.classList.remove('active');
      activeCard = null;
    }, 180);
  };

  popup.addEventListener('mouseenter', () => {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  });
  popup.addEventListener('mouseleave', hidePopup);

  document.addEventListener('mouseover', (e) => {
    // Suppress hover popups while scrolling
    if (isScrolling) return;
    const card = e.target.closest('.movie-card');
    if (!card) return;
    if (card === activeCard && popup.classList.contains('active')) return;

    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (showTimer) clearTimeout(showTimer);

    // Enforce sustained hover intent delay (~450ms) so popups ONLY appear when cursor is stationary
    showTimer = setTimeout(() => {
      if (isScrolling) return;
      const slug = card.dataset.slug;
      if (!slug) return;
      const movie = findMovieBySlug(slug) || allMovies.find(m => (m.slug || m.id) === slug);
      if (!movie) return;

      activeCard = card;
      const rect = card.getBoundingClientRect();
      const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url || SITE_CONFIG.defaultBackdrop;
      const imdbVal = movie.imdb || movie.rating || '';
      const imdb = imdbVal ? `<span class="badge badge-imdb"><i class="fa-solid fa-star"></i> ${imdbVal}</span>` : '';
      const quality = movie.quality ? `<span class="badge badge-quality">${escHtml(movie.quality)}</span>` : '<span class="badge badge-quality"><i class="fa-solid fa-film"></i> WEB-DL</span>';
      const year = movie.year ? `<span class="badge badge-year">${escHtml(movie.year)}</span>` : '';
      const durationVal = movie.duration ? (typeof movie.duration === 'number' ? `${movie.duration} min` : movie.duration) : '';
      const duration = durationVal ? `<span class="badge badge-duration"><i class="fa-regular fa-clock"></i> ${escHtml(durationVal)}</span>` : '';
      const isSeries = movie.type === 'series' || (Array.isArray(movie.seasons) && movie.seasons.length > 0) || Boolean(movie.season);
      const typeBadge = isSeries ? `<span class="badge badge-lang" style="background:#8a2be2"><i class="fa-solid fa-tv"></i> TV Series</span>` : '';
      const genres = Array.isArray(movie.genres) ? movie.genres.slice(0, 3) : [];
      const genrePills = genres.map(g => `<span class="cs-popup-genre-pill">${escHtml(g)}</span>`).join('');
      const desc = movie.description || movie.description_si || 'සිංහල උපසිරැසි සමඟ නරඹන්න සහ බාගත කරගන්න.';
      const shortDesc = desc.length > 130 ? desc.substring(0, 130) + '...' : desc;
      const movieUrl = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug || movie.id)}`;

      popup.innerHTML = `
        <div class="cs-popup-banner" style="background-image:url('${escHtml(backdrop)}')">
          <div class="cs-popup-banner-badges">
            <span class="badge badge-sub"><i class="fa-solid fa-closed-captioning"></i> සිංහල Sub</span>
            <div style="display:flex;gap:4px">
              ${typeBadge}
              ${quality}
            </div>
          </div>
        </div>
        <div class="cs-popup-body">
          <h3 class="cs-popup-title">${escHtml(movie.title || 'Untitled')}</h3>
          ${movie.title_si ? `<div class="cs-popup-title-si">${escHtml(movie.title_si)}</div>` : ''}
          <div class="cs-popup-meta">
            ${imdb}
            ${year}
            ${duration}
          </div>
          ${genrePills ? `<div class="cs-popup-genres">${genrePills}</div>` : ''}
          <p class="cs-popup-desc">${escHtml(shortDesc)}</p>
          <div class="cs-popup-actions">
            <a href="${movieUrl}" class="cs-popup-btn-watch">
              <i class="fa-solid fa-play"></i> Watch Now
            </a>
            <a href="${movieUrl}#downloads" class="cs-popup-btn-dl">
              <i class="fa-solid fa-cloud-arrow-down"></i> Download
            </a>
          </div>
        </div>
      `;

      // Smart positioning:
      const popupWidth = 320;
      const popupHeight = 340;
      let left = rect.left + rect.width / 2 - popupWidth / 2;
      let top = rect.top - 8;

      if (left + popupWidth > window.innerWidth - 12) {
        left = window.innerWidth - popupWidth - 12;
      }
      if (left < 12) {
        left = 12;
      }

      if (rect.top - popupHeight > 60) {
        top = rect.top - popupHeight + 30;
      } else if (rect.bottom + popupHeight < window.innerHeight - 20) {
        top = rect.bottom - 30;
      } else {
        top = Math.max(70, Math.min(window.innerHeight - popupHeight - 20, rect.top));
        if (rect.right + popupWidth + 15 < window.innerWidth) {
          left = rect.right + 10;
        } else if (rect.left - popupWidth - 15 > 0) {
          left = rect.left - popupWidth - 10;
        }
      }

      popup.style.left = `${Math.round(left)}px`;
      popup.style.top = `${Math.round(top)}px`;
      popup.classList.add('active');
    }, 450);
  });

  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest('.movie-card');
    if (!card) return;
    if (e.relatedTarget && (card.contains(e.relatedTarget) || popup.contains(e.relatedTarget))) {
      return;
    }
    hidePopup();
  });

  // Scroll suppression: immediately cancel show timer and dismiss active popup during scroll
  const onWindowScroll = () => {
    isScrolling = true;
    document.body.classList.add('is-window-scrolling');
    if (showTimer) {
      clearTimeout(showTimer);
      showTimer = null;
    }
    if (popup.classList.contains('active')) {
      popup.classList.remove('active');
      activeCard = null;
    }
    if (scrollTimeout) clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      isScrolling = false;
      document.body.classList.remove('is-window-scrolling');
    }, 200);
  };

  window.addEventListener('scroll', onWindowScroll, { passive: true });
}

// ---- Mobile Bottom Navigation Bar Support ----
function initMobileBottomNav() {
  const mobSearchBtn = document.getElementById('mob-search-btn');
  if (mobSearchBtn) {
    mobSearchBtn.addEventListener('click', (e) => {
      e.preventDefault();
      const overlay = document.getElementById('search-overlay');
      const input = document.getElementById('search-input');
      if (overlay) {
        overlay.classList.add('active');
        if (input) input.focus();
      }
    });
  }
}

// ---- Expose globals ----
window.FilmZilla = window.FilmSub = {
  loadMovies,
  renderHero,
  groupSeriesCatalog,
  allMovies: () => allMovies,
  getAllMovies: () => allMovies,
  get movies() { return allMovies; },
  set movies(val) { if (Array.isArray(val)) allMovies = val; },
  setMovies: (val) => { if (Array.isArray(val)) allMovies = val; },
  siteData: () => siteData,
  getFeatured,
  getNewReleases,
  getTrending,
  getRelated,
  findMovieBySlug,
  generateMovieCard,
  renderMovieGrid,
  renderCarousel,
  initScrollReveal,
  escHtml,
  showToast,
  trackView,
  SITE_CONFIG,
};
