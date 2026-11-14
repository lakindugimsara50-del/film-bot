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
  if (document.getElementById('trending-track')) renderCarousel('trending-track', getTrending(), { isTrending: true });
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
  try { initSearchOverlay(); } catch (e) { console.error('initSearchOverlay error:', e); }
  try { initMobileBottomNav(); } catch (e) { console.error('initMobileBottomNav error:', e); }

  // 3. Fetch remote updates in background without blocking rendering
  fetchRemoteMovies().then(updated => {
    if (updated) renderAllSections();
  }).catch(() => {});
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
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    if (yB !== yA) return yB - yA;
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    return timeB - timeA;
  }).slice(0, 20);
}
function getTrending() {
  const catalog = groupSeriesCatalog(allMovies);
  const tr = catalog.filter(m => m.trending);
  const list = tr.length > 0 ? tr : catalog;
  return [...list].sort((a, b) => {
    const yA = Number(a.year) || 0;
    const yB = Number(b.year) || 0;
    if (yB !== yA) return yB - yA;
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    return timeB - timeA;
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

  // 2. Score movies so newly uploaded films (at index 0 of movies.json) ALWAYS take top spot on Hero Banner!
  const scored = pool.map((m, idx) => {
    let score = 0;

    // Highest Priority: Array position (Newly added movies are unshifted to index 0)
    score += (pool.length - idx) * 1000;

    // Added timestamp bonus
    const addedTime = m.added_at ? new Date(m.added_at).getTime() : (m.added_date ? new Date(m.added_date).getTime() : 0);
    if (addedTime > 0) {
      score += (addedTime / 10000);
    }

    // Release year bonus
    const yr = Number(m.year) || 2024;
    score += (yr - 2000) * 10;

    // Backdrop presence bonus
    if (m.backdrop || m.backdrop_url) score += 50;
    if (m.poster || m.poster_url) score += 20;

    return { movie: m, score };
  });

  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);

  const merged = [];
  const seenTitles = new Set();
  for (const item of scored) {
    const m = item.movie;
    const rawTitle = (m.title || '').split(' - Season')[0].trim().toLowerCase().replace(/[^a-z0-9]/g, '');
    if (rawTitle && !seenTitles.has(rawTitle)) {
      seenTitles.add(rawTitle);
      merged.push(m);
    }
    if (merged.length >= 6) break;
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
let isHeroTransitioning = false;
let heroIsPaused = false;
let hero3DTouchStartX = 0;
let hero3DTouchStartY = 0;
const HERO_FS_DURATION = 5500; // Duration card stays expanded in full size display ("chuttak wela thibila")

function renderHeroShowcase() {
  const section = document.getElementById('hero-section');
  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (!section || !stage) return;

  const spotlight = getHeroSpotlightList();
  if (!spotlight || spotlight.length === 0) return;
  heroShowcaseMovies = spotlight.slice(0, 6);
  heroShowcaseCurrent = Math.min(heroShowcaseCurrent, heroShowcaseMovies.length - 1);

  // 1. Build the 16:9 Widescreen Landscape Cards ("lap eke diga athata")
  stage.innerHTML = buildHero3DCardsHTML();

  // 2. Attach click listeners on each card: clicking any card rotates & expands it into full screen
  bindHeroCardClicks(stage);

  // 3. Navigation controls (Prev / Next / Toggle Deck)
  const prevBtn = document.getElementById('hero-deck-prev');
  const nextBtn = document.getElementById('hero-deck-next');
  const toggleBtn = document.getElementById('hero-deck-toggle');

  if (prevBtn) {
    prevBtn.onclick = (e) => {
      e.stopPropagation();
      if (isHeroTransitioning) return;
      const len = heroShowcaseMovies.length;
      const prevIdx = (heroShowcaseCurrent - 1 + len) % len;
      selectHeroMovieAndExpand(prevIdx);
    };
  }

  if (nextBtn) {
    nextBtn.onclick = (e) => {
      e.stopPropagation();
      if (isHeroTransitioning) return;
      cycleHeroShowcaseNext();
    };
  }

  if (toggleBtn) {
    toggleBtn.onclick = (e) => {
      e.stopPropagation();
      toggleHeroDeckView();
    };
  }

  // 4. Hover pause: when user hovers over hero, pause auto-rotation to allow reading synopsis
  section.onmouseenter = () => {
    heroIsPaused = true;
    pauseProgressBar();
  };
  section.onmouseleave = () => {
    heroIsPaused = false;
    resumeProgressBar();
    scheduleNextHeroCycle();
  };

  // 5. Mobile Touch Swipe support
  initHero3DTouchSwipe(stage);

  // 6. Initial expansion of spotlight movie 0 into full screen
  expandHeroFrontCard(heroShowcaseCurrent, false);
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
          <img src="${FilmSub.escHtml(backdrop)}" class="hero-3d-card-img" alt="${FilmSub.escHtml(movie.title || '')}" loading="lazy">
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
      if (isHeroTransitioning) return;
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
    const cIdx = parseInt(card.dataset.idx, 10);
    const rel = (cIdx - heroShowcaseCurrent + len) % len;
    card.className = `hero-3d-card slot-${Math.min(rel, 5)}${rel === 0 ? ' is-active' : ''}`;
  });
}

function updateHeroNavCounter() {
  const counter = document.getElementById('hero-deck-counter');
  if (counter && heroShowcaseMovies.length > 0) {
    counter.textContent = `${heroShowcaseCurrent + 1} / ${heroShowcaseMovies.length}`;
  }
}

function expandHeroFrontCard(targetIdx, animate = true) {
  const section = document.getElementById('hero-section');
  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (!section || !stage) return;

  if (typeof targetIdx === 'number') {
    heroShowcaseCurrent = targetIdx;
  }
  const movie = heroShowcaseMovies[heroShowcaseCurrent];
  if (!movie) return;

  updateHeroContent(movie);
  updateHeroNavCounter();
  assignHeroSlotClasses();

  const frontCard = stage.querySelector(`.hero-3d-card[data-idx="${heroShowcaseCurrent}"]`);

  if (animate) {
    isHeroTransitioning = true;
    clearTimeout(heroCycleTimer);
    stopProgressBar();

    // 1. Front card executes 3D rotation & expansion ("lassana animation ekakin karakavila full size wela")
    if (frontCard) {
      frontCard.classList.remove('is-retreating-to-back');
      frontCard.classList.add('is-spinning-to-fullscreen');
    }

    // 2. Mid-expansion: transition section to full-screen mode
    setTimeout(() => {
      section.classList.remove('is-deck-mode');
      section.classList.add('is-expanded-mode');
      startProgressBar();
    }, 420);

    // 3. Complete expansion
    setTimeout(() => {
      if (frontCard) {
        frontCard.classList.remove('is-spinning-to-fullscreen');
      }
      assignHeroSlotClasses();
      isHeroTransitioning = false;
      scheduleNextHeroCycle();
    }, 850);
  } else {
    // Initial display without delay
    section.classList.remove('is-deck-mode');
    section.classList.add('is-expanded-mode');
    startProgressBar();
    scheduleNextHeroCycle();
  }
}

function cycleHeroShowcaseNext() {
  if (isHeroTransitioning || heroIsPaused) return;
  const section = document.getElementById('hero-section');
  const stage = document.getElementById('hero-3d-deck-stage') || document.getElementById('hero-cards-deck');
  if (!section || !stage) return;

  const len = heroShowcaseMovies.length;
  if (len < 2) return;

  clearTimeout(heroCycleTimer);
  stopProgressBar();
  isHeroTransitioning = true;

  const oldIdx = heroShowcaseCurrent;
  const nextIdx = (oldIdx + 1) % len;
  heroShowcaseCurrent = nextIdx;

  const oldCard = stage.querySelector(`.hero-3d-card[data-idx="${oldIdx}"]`);

  // Step 1: Fullscreen exits with animation; 3D deck view appears on dark stage (No poster in background!)
  // ("aye full screen ain wenawa lassana animation ekakin ara diga hadathi card tika aye awith")
  section.classList.remove('is-expanded-mode');
  section.classList.add('is-deck-mode');

  // Step 2: The card that was just full-screen visibly sweeps around to the back of the 3D stack
  // ("ara apu card eka pitipassata yanawa penawa")
  if (oldCard) {
    oldCard.className = 'hero-3d-card is-retreating-to-back';
  }

  // Step 3: Advance all other cards in the 3D deck forward
  stage.querySelectorAll('.hero-3d-card').forEach(card => {
    const cIdx = parseInt(card.dataset.idx, 10);
    if (cIdx !== oldIdx) {
      const rel = (cIdx - nextIdx + len) % len;
      card.className = `hero-3d-card slot-${Math.min(rel, 5)}${rel === 0 ? ' is-active' : ''}`;
    }
  });

  // Step 4: After 850ms, old card has reached the rear slot of the deck
  setTimeout(() => {
    assignHeroSlotClasses();

    // Step 5: Brief beat in 3D deck view (~350ms) so user clearly sees the deck and new front card
    setTimeout(() => {
      // Step 6: Next front card rotates in 3D and comes forward to full screen!
      // ("aye issaraha thiyana card eka lassana animation ekakin issarahata awith full screen wenawa")
      expandHeroFrontCard(nextIdx, true);
    }, 350);
  }, 850);
}

function selectHeroMovieAndExpand(targetIdx) {
  if (isHeroTransitioning) return;
  const len = heroShowcaseMovies.length;
  if (targetIdx < 0 || targetIdx >= len) return;

  clearTimeout(heroCycleTimer);
  stopProgressBar();

  const section = document.getElementById('hero-section');
  if (section && section.classList.contains('is-expanded-mode') && targetIdx === heroShowcaseCurrent) {
    return; // Already viewing this movie full-screen
  }

  heroShowcaseCurrent = targetIdx;
  assignHeroSlotClasses();
  expandHeroFrontCard(targetIdx, true);
}

function toggleHeroDeckView() {
  if (isHeroTransitioning) return;
  const section = document.getElementById('hero-section');
  if (!section) return;

  if (section.classList.contains('is-expanded-mode')) {
    // Manually switch from full screen to 3D deck view
    clearTimeout(heroCycleTimer);
    stopProgressBar();
    section.classList.remove('is-expanded-mode');
    section.classList.add('is-deck-mode');
    assignHeroSlotClasses();
  } else {
    // Expand front card to full screen
    expandHeroFrontCard(heroShowcaseCurrent, true);
  }
}

function scheduleNextHeroCycle() {
  clearTimeout(heroCycleTimer);
  heroCycleTimer = setTimeout(() => {
    // If modal is open or paused, recheck
    const modal = document.getElementById('trailer-modal');
    if ((modal && modal.style.display === 'flex') || heroIsPaused) {
      scheduleNextHeroCycle();
      return;
    }
    cycleHeroShowcaseNext();
  }, HERO_FS_DURATION);
}

function startProgressBar() {
  const bar = document.getElementById('hero-fs-progress-bar');
  if (!bar) return;
  bar.style.transition = 'none';
  bar.style.width = '0%';
  void bar.offsetWidth;
  bar.style.transition = `width ${HERO_FS_DURATION}ms linear`;
  bar.style.width = '100%';
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
  bar.style.transition = `width 3500ms linear`;
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
      const len = heroShowcaseMovies.length;
      if (len < 2 || isHeroTransitioning) return;
      if (dx < 0) {
        // Swipe Left -> Next movie
        cycleHeroShowcaseNext();
      } else {
        // Swipe Right -> Prev movie
        const prevIdx = (heroShowcaseCurrent - 1 + len) % len;
        selectHeroMovieAndExpand(prevIdx);
      }
    }
  }, { passive: true });
}

function updateHeroContent(movie) {
  if (!movie) return;
  const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url || SITE_CONFIG.defaultBackdrop;

  const bgCurrent = document.getElementById('hero-bg-current');
  if (bgCurrent) {
    bgCurrent.style.backgroundImage = `url('${backdrop}')`;
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

  // Trigger smooth slide animation on main content text
  const mainInfo = document.querySelector('.hero-showcase-main');
  if (mainInfo) {
    mainInfo.style.animation = 'none';
    void mainInfo.offsetWidth;
    mainInfo.style.animation = '';
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

// ---- Format Quality Label (e.g. 1080p -> Full HD) ----
function formatQualityLabel(q) {
  if (!q) return 'Full HD';
  const s = String(q).trim().toLowerCase();
  if (s.includes('2160') || s.includes('4k')) return '4K UHD';
  if (s.includes('1080') || s.includes('fhd')) return 'Full HD';
  if (s.includes('720') || s.includes('hd')) return 'HD';
  if (s.includes('480') || s.includes('sd')) return 'SD';
  return 'Full HD';
}

// ---- Generate Movie Card (Netflix Cinema Style) ----
function generateMovieCard(movie, opts = {}) {
  const isTrending = typeof opts === 'boolean' ? opts : Boolean(opts.isTrending);
  const rank = opts.rank ? `<span class="card-rank-num">${opts.rank}</span>` : '';
  const poster = movie.poster || movie.poster_url || SITE_CONFIG.defaultPoster;
  
  // Clean IMDb badge (top right, completely unobstructed)
  const imdbVal = movie.imdb || movie.rating || '';
  const imdb = (imdbVal && imdbVal !== '0.0') ? `<span class="badge-top-right"><i class="fa-solid fa-star"></i> ${imdbVal}</span>` : '';
  
  // Left badges (Always prominent Full HD / 4K / Quality label)
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
  const cardClass = isTrending ? 'movie-card trending-card' : 'movie-card';

  return `
    <a href="${url}" class="${cardClass}" data-slug="${escHtml(movie.slug || movie.id || '')}" title="${escHtml(movie.title || '')}">
      <div class="card-poster">
        <img src="${escHtml(poster)}" alt="${escHtml(movie.title || '')}" loading="lazy" decoding="async"
             onerror="this.onerror=null; if(window.SITE_CONFIG) this.src=window.SITE_CONFIG.defaultPoster;">
        <div class="card-hover-play"><i class="fa-solid fa-play"></i></div>
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

// ---- Modern Scroll-Reveal Animation Engine ----
function initScrollReveal() {
  const sections = document.querySelectorAll('.section, .cs-player-section, .cs-movie-details-section, .cs-downloads-section, .cs-related-section, .cs-series-section');
  if (!sections.length) return;

  if (typeof IntersectionObserver === 'undefined') {
    sections.forEach(s => s.classList.add('is-visible'));
    document.querySelectorAll('.movie-card').forEach(c => c.classList.add('is-card-visible'));
    return;
  }

  const observer = new IntersectionObserver((entries, obs) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');

        const cards = entry.target.querySelectorAll('.movie-card');
        cards.forEach((c, idx) => {
          c.style.transitionDelay = `${Math.min(idx * 0.04, 0.4)}s`;
          c.classList.add('is-card-visible');
        });

        obs.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.08,
    rootMargin: '0px 0px -40px 0px'
  });

  sections.forEach(sec => {
    sec.classList.add('scroll-reveal-section');
    observer.observe(sec);
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
    const card = e.target.closest('.movie-card');
    if (!card) return;
    if (card === activeCard && popup.classList.contains('active')) return;

    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (showTimer) clearTimeout(showTimer);

    showTimer = setTimeout(() => {
      const slug = card.dataset.slug;
      if (!slug) return;
      const movie = findMovieBySlug(slug) || allMovies.find(m => (m.slug || m.id) === slug);
      if (!movie) return;

      activeCard = card;
      const rect = card.getBoundingClientRect();
      const backdrop = movie.backdrop || movie.backdrop_url || movie.poster || movie.poster_url || SITE_CONFIG.defaultBackdrop;
      const imdbVal = movie.imdb || movie.rating || '';
      const imdb = imdbVal ? `<span class="badge badge-imdb"><i class="fa-solid fa-star"></i> ${imdbVal}</span>` : '';
      const quality = movie.quality ? `<span class="badge badge-quality">${escHtml(movie.quality)}</span>` : '<span class="badge badge-quality"><i class="fa-solid fa-film"></i> Full HD</span>';
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
    }, 180);
  });

  document.addEventListener('mouseout', (e) => {
    const card = e.target.closest('.movie-card');
    if (!card) return;
    if (e.relatedTarget && (card.contains(e.relatedTarget) || popup.contains(e.relatedTarget))) {
      return;
    }
    hidePopup();
  });

  window.addEventListener('scroll', () => {
    if (popup.classList.contains('active')) {
      popup.classList.remove('active');
      activeCard = null;
    }
  }, { passive: true });
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
