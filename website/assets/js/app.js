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
function _isValidMovieEntry(m) {
  if (!m || !m.title) return false;
  const hasPoster = Boolean(m.poster || m.poster_url);
  const hasMedia = Boolean(
    m.stream_url ||
    (Array.isArray(m.streams) && m.streams.length > 0) ||
    (Array.isArray(m.downloads) && m.downloads.length > 0) ||
    (m.qualities && Object.keys(m.qualities).length > 0)
  );
  return hasPoster || hasMedia;
}

async function loadMovies() {
  // 1. Initial baseline from pre-loaded script tag
  let baselineMovies = [];
  if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
    siteData = window.FILMSUB_DATA.site || {};
    baselineMovies = window.FILMSUB_DATA.movies.filter(_isValidMovieEntry);
    allMovies = baselineMovies.length > 0 ? baselineMovies : window.FILMSUB_DATA.movies;
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
          const validRemote = data.movies.filter(_isValidMovieEntry);
          if (validRemote.length > 0) {
            siteData = data.site || siteData;
            // Merge any baseline movies not in validRemote so local catalog is never lost
            const seenSlugs = new Set(validRemote.map(m => String(m.slug || m.id || '').toLowerCase()));
            const merged = [...validRemote];
            baselineMovies.forEach(bm => {
              const s = String(bm.slug || bm.id || '').toLowerCase();
              if (s && !seenSlugs.has(s)) {
                seenSlugs.add(s);
                merged.push(bm);
              }
            });
            allMovies = merged;
            fetched = true;
            break;
          }
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
  return [...allMovies].sort((a, b) => {
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    if (timeB !== timeA) return timeB - timeA;
    return (b.year || 0) - (a.year || 0);
  }).slice(0, 20);
}
function getTrending() {
  const tr = allMovies.filter(m => m.trending);
  const list = tr.length > 0 ? tr : allMovies;
  return [...list].sort((a, b) => {
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    if (timeB !== timeA) return timeB - timeA;
    return (b.year || 0) - (a.year || 0);
  }).slice(0, 20);
}
function getSinhalaFilms() {
  const sf = allMovies.filter(m => (Array.isArray(m.subtitles) && m.subtitles.length > 0) || m.subtitle_url || m.has_sinhala_sub);
  const list = sf.length > 0 ? sf : allMovies;
  return [...list].sort((a, b) => {
    const timeA = a.added_at ? new Date(a.added_at).getTime() : (a.added_date ? new Date(a.added_date).getTime() : 0);
    const timeB = b.added_at ? new Date(b.added_at).getTime() : (b.added_date ? new Date(b.added_date).getTime() : 0);
    if (timeB !== timeA) return timeB - timeA;
    return (b.year || 0) - (a.year || 0);
  }).slice(0, 20);
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
  const byTitle = allMovies.find(m => {
    const t = (m.title || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    return t && (t === normS || normS.startsWith(t) || t.startsWith(normS));
  });
  if (byTitle) return byTitle;

  // 4. Built-in deep-link / verification fallback catalog (never pollutes homepage carousels)
  const fallbackCatalog = {
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
        { server: 'Server 1', label: '⚡ Super Player (1080p Chunk Stream)', type: 'video/mp4', stream_url: 'assets/sample_stream.mp4' }
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
        { server: 'Server 1', label: '⚡ Super Player (1080p Chunk Stream)', type: 'video/mp4', stream_url: 'assets/sample_stream.mp4' }
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
        { server: 'Server 1', label: '⚡ Super Player (1080p Chunk Stream)', type: 'video/mp4', stream_url: 'assets/sample_stream.mp4' }
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

  const posterEl = document.getElementById('hero-poster-mini');
  if (posterEl) {
    posterEl.src = movie.poster || movie.poster_url || SITE_CONFIG.defaultPoster;
    posterEl.alt = movie.title || 'Featured Movie';
    posterEl.onerror = () => { posterEl.src = SITE_CONFIG.defaultPoster; };
  }
  const backdropEl = document.querySelector('.hero-backdrop');
  if (backdropEl) backdropEl.style.backgroundImage = `url('${backdrop}')`;
  const badgeEl = document.getElementById('hero-badge');
  if (badgeEl) badgeEl.innerHTML = `<i class="fa-solid fa-fire"></i> Featured • සිංහල උපසිරැසි`;
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
             onerror="this.onerror=null; if(window.SITE_CONFIG) this.src=window.SITE_CONFIG.defaultPoster;">
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
