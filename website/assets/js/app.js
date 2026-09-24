/* ============================================================
   FilmSub – app.js | Main JavaScript
   ============================================================ */

'use strict';

// ---- Config ----
const SITE_CONFIG = {
  moviesPath: 'data/movies.json',
  defaultPoster: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='450' viewBox='0 0 300 450'%3E%3Crect width='300' height='450' fill='%231f1f1f'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23666' font-family='sans-serif' font-size='16'%3ENo Poster%3C/text%3E%3C/svg%3E",
  defaultBackdrop: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' viewBox='0 0 1280 720'%3E%3Crect width='1280' height='720' fill='%23141414'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' fill='%23333' font-family='sans-serif' font-size='24'%3EFilmSub%3C/text%3E%3C/svg%3E",
  moviePage: 'movie.html',
  searchPage: 'search.html',
};

// ---- State ----
let allMovies = [];
let siteData = {};

// ---- Init ----
document.addEventListener('DOMContentLoaded', async () => {
  initHeader();
  initSearchOverlay();
  await loadMovies();
  if (document.getElementById('hero-section')) renderHero();
  if (document.getElementById('trending-track')) renderCarousel('trending-track', getTrending(), { isTrending: true });
  if (document.getElementById('new-releases-track')) renderCarousel('new-releases-track', getNewReleases(), { isTrending: false });
  if (document.getElementById('sinhala-dub-track')) renderCarousel('sinhala-dub-track', getSinhalaFilms(), { isTrending: false });
  setupCarouselArrows();
});

// ---- Load Movies ----
async function loadMovies() {
  // 1. Initial baseline from pre-loaded script tag
  if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
    siteData = window.FILMSUB_DATA.site || {};
    allMovies = window.FILMSUB_DATA.movies;
  }

  const cacheBuster = `?_t=${Date.now()}`;
  // 2. Fetch live data: First try GitHub Raw (instant bot updates), then local data/movies.json
  const remoteUrl = `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json${cacheBuster}`;
  const localUrl = `${SITE_CONFIG.moviesPath}${cacheBuster}`;

  let fetched = false;
  for (const url of [remoteUrl, localUrl]) {
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.movies) && data.movies.length > 0) {
          siteData = data.site || siteData;
          allMovies = data.movies;
          fetched = true;
          break;
        }
      }
    } catch (e) {
      // Continue to next source
    }
  }

  if (!fetched && allMovies.length === 0) {
    console.info('Using local FILMSUB_DATA fallback.');
  }
}

// ---- Data Helpers ----
function getFeatured() {
  return allMovies.find(m => m.featured) || allMovies[0] || null;
}
function getNewReleases() {
  return [...allMovies].sort((a, b) => (b.year || 0) - (a.year || 0)).slice(0, 20);
}
function getTrending() {
  return allMovies.filter(m => m.trending).slice(0, 20);
}
function getSinhalaFilms() {
  return allMovies.filter(m => (Array.isArray(m.subtitles) && m.subtitles.length > 0) || m.subtitle_url).slice(0, 20);
}
function getRelated(movie) {
  if (!movie) return [];
  const genres = Array.isArray(movie.genres) ? movie.genres : [];
  return allMovies.filter(m => m.slug !== movie.slug && Array.isArray(m.genres) && m.genres.some(g => genres.includes(g))).slice(0, 12);
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
  return allMovies.find(m => {
    const t = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    return t && (t === normS || normS.startsWith(t) || t.startsWith(normS));
  }) || null;
}

// ---- Hero ----
function renderHero() {
  const movie = getFeatured();
  const heroEl = document.getElementById('hero-section');
  if (!heroEl) return;
  if (!movie) {
    heroEl.innerHTML = `<div class="hero-gradient"></div><div class="hero-content"><p class="text-muted">No featured movies yet.</p></div>`;
    return;
  }
  const backdrop = movie.backdrop || movie.backdrop_url || SITE_CONFIG.defaultBackdrop;
  const genres = Array.isArray(movie.genres) ? movie.genres : [];
  const imdbVal = movie.imdb || movie.rating || '';
  const imdb = imdbVal ? `<span class="badge badge-imdb"><i class="fa-solid fa-star"></i>${imdbVal}</span>` : '';
  const quality = movie.quality ? `<span class="badge badge-quality">${movie.quality}</span>` : '';
  const year = movie.year ? `<span class="badge badge-year">${movie.year}</span>` : '';
  const durationVal = movie.duration ? (typeof movie.duration === 'number' ? `${movie.duration} min` : movie.duration) : '';
  const duration = durationVal ? `<span class="badge badge-duration"><i class="fa-regular fa-clock"></i>${durationVal}</span>` : '';
  const genreChips = genres.map(g => `<a href="search.html?genre=${encodeURIComponent(g)}" class="genre-chip">${g}</a>`).join('');

  document.querySelector('.hero-backdrop').style.backgroundImage = `url('${backdrop}')`;
  document.getElementById('hero-badge').textContent = 'Featured';
  document.getElementById('hero-title').textContent = movie.title || '';
  document.getElementById('hero-title-si').textContent = movie.title_si || '';
  document.getElementById('hero-meta').innerHTML = `${imdb}${quality}${year}${duration}`;
  document.getElementById('hero-genres').innerHTML = genreChips;
  document.getElementById('hero-description').textContent = movie.description || '';
  document.getElementById('hero-watch-btn').href = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug)}`;
  document.getElementById('hero-download-btn').href = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug)}#downloads`;
}

// ---- Generate Movie Card (CineSubz Style) ----
function generateMovieCard(movie, opts = {}) {
  const isTrending = typeof opts === 'boolean' ? opts : Boolean(opts.isTrending);
  const rank = opts.rank ? `<span class="card-rank-num">${opts.rank}</span>` : '';
  const poster = movie.poster || movie.poster_url || SITE_CONFIG.defaultPoster;
  const imdbVal = movie.imdb || movie.rating || '';
  const imdb = imdbVal ? `<span class="badge-top-right">★ ${imdbVal}</span>` : '';
  const quality = movie.quality ? `<span class="badge-top-left">${escHtml(movie.quality)}</span>` : '<span class="badge-top-left">WEB-DL</span>';
  const url = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug || movie.id)}`;
  const cardClass = isTrending ? 'movie-card trending-card' : 'movie-card';

  return `
    <a href="${url}" class="${cardClass}" title="${escHtml(movie.title || '')}">
      <div class="card-poster">
        <img src="${escHtml(poster)}" alt="${escHtml(movie.title || '')}" loading="lazy"
             onerror="this.src='${SITE_CONFIG.defaultPoster}'">
        ${quality}
        ${imdb}
        ${rank}
        <div class="card-bottom-bar">
          <span class="card-title-text">${escHtml(movie.title || 'Untitled')}${movie.year ? ` (${movie.year})` : ''}</span>
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
      const results = allMovies.filter(m =>
        (m.title || '').toLowerCase().includes(q) ||
        (m.title_si || '').toLowerCase().includes(q) ||
        (Array.isArray(m.genres) && m.genres.some(g => g.toLowerCase().includes(q)))
      ).slice(0, 12);
      if (results.length === 0) {
        liveResults.innerHTML = `<div class="search-no-results"><i class="fa-solid fa-film"></i> No results for "<strong>${escHtml(q)}</strong>"</div>`;
      } else {
        liveResults.innerHTML = results.map(m => generateMovieCard(m)).join('');
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
  if (!movies || movies.length === 0) {
    el.innerHTML = `
      <div class="empty-state">
        <div class="empty-state-icon"><i class="fa-solid fa-film-slash"></i></div>
        <h3>No movies found</h3>
        <p>Try different keywords or browse by genre.</p>
      </div>`;
    return;
  }
  el.innerHTML = movies.map(m => generateMovieCard(m)).join('');
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
function escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function debounce(fn, delay) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), delay); };
}

// ---- Expose globals ----
window.FilmSub = {
  loadMovies,
  allMovies: () => allMovies,
  siteData: () => siteData,
  getFeatured,
  getNewReleases,
  getTrending,
  getRelated,
  findMovieBySlug,
  generateMovieCard,
  renderMovieGrid,
  renderCarousel,
  escHtml,
  showToast,
  trackView,
  SITE_CONFIG,
};
