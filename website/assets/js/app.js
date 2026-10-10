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
function renderAllSections() {
  if (document.getElementById('hero-section')) renderHero();
  if (document.getElementById('trending-track')) renderCarousel('trending-track', getTrending(), { isTrending: true });
  if (document.getElementById('new-releases-track')) renderCarousel('new-releases-track', getNewReleases(), { isTrending: false });
  if (document.getElementById('sinhala-dub-track')) renderCarousel('sinhala-dub-track', getSinhalaFilms(), { isTrending: false });
  setupCarouselArrows();
  initHighlightPopup();
}

function initApp() {
  initHeader();
  initSearchOverlay();
  initMobileBottomNav();
  // 1. Instantly populate from local window.FILMSUB_DATA
  loadBaselineMovies();
  // 2. Render all sections immediately with zero blank screen
  renderAllSections();
  // 3. Fetch remote updates in background without blocking rendering
  fetchRemoteMovies().then(updated => {
    if (updated) renderAllSections();
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initApp);
} else {
  initApp();
}

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

function loadBaselineMovies() {
  if (window.FILMSUB_DATA && Array.isArray(window.FILMSUB_DATA.movies)) {
    siteData = window.FILMSUB_DATA.site || {};
    const valid = window.FILMSUB_DATA.movies.filter(_isValidMovieEntry);
    allMovies = valid.length > 0 ? valid : window.FILMSUB_DATA.movies;
  }
}

async function fetchRemoteMovies() {
  const cacheBuster = `?_t=${Date.now()}`;
  const localUrl = `${SITE_CONFIG.moviesPath}${cacheBuster}`;
  const remoteUrl = `https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json${cacheBuster}`;

  for (const url of [localUrl, remoteUrl]) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3500);
      const res = await fetch(url, { cache: 'no-store', signal: controller.signal });
      clearTimeout(timer);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.movies) && data.movies.length > 0) {
          const validRemote = data.movies.filter(_isValidMovieEntry);
          if (validRemote.length > 0) {
            siteData = data.site || siteData;
            const seenSlugs = new Set(validRemote.map(m => String(m.slug || m.id || '').toLowerCase()));
            const merged = [...validRemote];
            allMovies.forEach(bm => {
              const s = String(bm.slug || bm.id || '').toLowerCase();
              if (s && !seenSlugs.has(s)) {
                seenSlugs.add(s);
                merged.push(bm);
              }
            });
            allMovies = merged;
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

  // 1b. Series base slug match: e.g. "game-of-thrones-2011-s02e03" -> base "game-of-thrones-2011" or "game-of-thrones"
  const baseEpSlug = clean.replace(/[-_.]s\d+[-_.]?e\d+.*$/i, '').replace(/[-_.]\d+x\d+.*$/i, '');
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
  const imdb = imdbVal ? `<span class="badge badge-imdb"><i class="fa-solid fa-star"></i> ${imdbVal}</span>` : '';
  const quality = movie.quality ? `<span class="badge badge-quality">${movie.quality}</span>` : '<span class="badge badge-quality">1080p FHD</span>';
  const year = movie.year ? `<span class="badge badge-year">${movie.year}</span>` : '';
  const durationVal = movie.duration ? (typeof movie.duration === 'number' ? `${movie.duration} min` : movie.duration) : '';
  const duration = durationVal ? `<span class="badge badge-duration"><i class="fa-regular fa-clock"></i> ${durationVal}</span>` : '';
  const subBadge = `<span class="badge badge-sub"><i class="fa-solid fa-closed-captioning"></i> සිංහල Sub</span>`;
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
  if (badgeEl) badgeEl.innerHTML = `<i class="fa-solid fa-bolt"></i> NETFLIX SPOTLIGHT • සිංහල උපසිරැසි`;
  document.getElementById('hero-title').textContent = movie.title || '';
  document.getElementById('hero-title-si').textContent = movie.title_si || '';
  document.getElementById('hero-meta').innerHTML = `${imdb}${quality}${year}${duration}${subBadge}`;
  document.getElementById('hero-genres').innerHTML = genreChips;
  document.getElementById('hero-description').textContent = movie.description || '';
  
  const watchBtn = document.getElementById('hero-watch-btn');
  if (watchBtn) {
    watchBtn.href = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug)}`;
    watchBtn.innerHTML = `<i class="fa-solid fa-play"></i> Watch Now`;
  }
  const dlBtn = document.getElementById('hero-download-btn');
  if (dlBtn) {
    dlBtn.href = `${SITE_CONFIG.moviePage}?id=${encodeURIComponent(movie.slug)}#downloads`;
    dlBtn.innerHTML = `<i class="fa-solid fa-cloud-arrow-down"></i> Download`;
  }
}

// ---- Generate Movie Card (Netflix Cinema Style) ----
function generateMovieCard(movie, opts = {}) {
  const isTrending = typeof opts === 'boolean' ? opts : Boolean(opts.isTrending);
  const rank = opts.rank ? `<span class="card-rank-num">${opts.rank}</span>` : '';
  const poster = movie.poster || movie.poster_url || SITE_CONFIG.defaultPoster;
  const imdbVal = movie.imdb || movie.rating || '';
  const imdb = imdbVal ? `<span class="badge-top-right">★ ${imdbVal}</span>` : '';
  const rawQ = movie.quality || '1080p';
  const quality = `<span class="badge-top-left">${escHtml(rawQ)}</span>`;
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
        ${quality}
        ${typeBadge}
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
      const quality = movie.quality ? `<span class="badge badge-quality">${escHtml(movie.quality)}</span>` : '<span class="badge badge-quality">1080p FHD</span>';
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
