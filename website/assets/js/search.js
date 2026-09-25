/* ============================================================
   FilmSub – search.js | Search & Filtering Engine
   ============================================================ */

'use strict';

function waitForFS() {
  return new Promise(r => {
    const c = () => {
      if (typeof window !== 'undefined' && window.FilmSub) r();
      else setTimeout(c, 50);
    };
    c();
  });
}

function filterByGenre(movies, genre) {
  if (!genre || genre === 'all') return movies;
  const target = String(genre).toLowerCase().trim();
  return movies.filter(m => Array.isArray(m.genres) && m.genres.some(g => {
    const gn = String(g).toLowerCase().trim();
    if (gn === target) return true;
    const parts = gn.split(/[&,/]+/).map(p => p.trim());
    return parts.includes(target) || gn.includes(target);
  }));
}

function computeResults(movies, { query, genre, sort, type, sub } = {}) {
  let res = Array.isArray(movies) ? [...movies] : [];

  // Filter by Type (movie vs series)
  if (type) {
    const t = String(type).toLowerCase().trim();
    if (t === 'series') {
      res = res.filter(m => String(m.type || '').toLowerCase() === 'series');
    } else if (t === 'movie') {
      res = res.filter(m => String(m.type || 'movie').toLowerCase() === 'movie');
    }
  }

  // Filter by Search Query
  if (query) {
    const q = String(query).toLowerCase().trim();
    res = res.filter(m =>
      (m.title || '').toLowerCase().includes(q) ||
      (m.title_si || '').toLowerCase().includes(q) ||
      (Array.isArray(m.genres) && m.genres.some(g => String(g).toLowerCase().includes(q)))
    );
  }

  // Filter by Genre
  if (genre && genre !== 'all') {
    res = filterByGenre(res, genre);
  }

  // Filter by Sinhala Subtitle
  if (sub === 'sinhala') {
    res = res.filter(m => (Array.isArray(m.subtitles) && m.subtitles.length > 0) || m.subtitle_url || m.has_sinhala_sub);
  }

  // Sorting
  if (sort === 'newest') {
    res.sort((a, b) => (b.year || 0) - (a.year || 0));
  } else if (sort === 'trending') {
    res = res.filter(m => m.trending);
  }

  return res;
}

function renderResults(movies) {
  const countEl = document.getElementById('results-count');
  if (countEl) {
    const len = Array.isArray(movies) ? movies.length : 0;
    countEl.innerHTML = `<strong>${len}</strong> item${len !== 1 ? 's' : ''} found`;
  }
  if (typeof FilmSub !== 'undefined' && typeof FilmSub.renderMovieGrid === 'function') {
    FilmSub.renderMovieGrid('results-grid', movies);
  }
}

async function initSearchPage() {
  await waitForFS();
  await FilmSub.loadMovies();

  const params = new URLSearchParams(window.location.search);
  const query = params.get('q') || '';
  const genre = params.get('genre') || '';
  const sort  = params.get('sort') || '';
  const type  = params.get('type') || '';
  const sub   = params.get('sub') || '';

  const pageTitle = document.getElementById('search-page-title');
  const pageInput = document.getElementById('search-page-input');

  // Prefill search box
  if (pageInput && query) pageInput.value = query;

  // Title formatting
  if (pageTitle) {
    if (genre) pageTitle.innerHTML = `<i class="fa-solid fa-layer-group"></i> Genre: ${FilmSub.escHtml(genre)}`;
    else if (sort === 'newest') pageTitle.innerHTML = `<i class="fa-solid fa-star"></i> New Releases`;
    else if (sort === 'trending') pageTitle.innerHTML = `<i class="fa-solid fa-fire"></i> Trending Now`;
    else if (type === 'series') pageTitle.innerHTML = `<i class="fa-solid fa-tv"></i> TV Series`;
    else if (type === 'movie') pageTitle.innerHTML = `<i class="fa-solid fa-film"></i> Movies`;
    else if (query) pageTitle.innerHTML = `<i class="fa-solid fa-magnifying-glass"></i> Results for: "<strong>${FilmSub.escHtml(query)}</strong>"`;
  }

  // Activate genre chip
  if (genre) {
    document.querySelectorAll('.filter-chip').forEach(c => {
      c.classList.toggle('active', c.dataset.filter === genre);
    });
  }

  // Active filter state
  let currentGenre = genre || 'all';

  // Filter chips interaction
  document.querySelectorAll('.filter-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      currentGenre = chip.dataset.filter || 'all';
      const filtered = computeResults(FilmSub.allMovies(), {
        query: (pageInput ? pageInput.value.trim() : query),
        genre: currentGenre,
        sort,
        type,
        sub
      });
      renderResults(filtered);
    });
  });

  // Search form
  const form = document.getElementById('search-page-form');
  if (form) {
    form.addEventListener('submit', e => {
      e.preventDefault();
      const q = pageInput ? pageInput.value.trim() : '';
      const newParams = new URLSearchParams(window.location.search);
      if (q) {
        newParams.set('q', q);
      } else {
        newParams.delete('q');
      }
      window.location.href = `search.html?${newParams.toString()}`;
    });
  }

  // Initial computation and rendering
  const results = computeResults(FilmSub.allMovies(), { query, genre: currentGenre, sort, type, sub });
  renderResults(results);
}

if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', initSearchPage);
}

if (typeof window !== 'undefined') {
  window.FilmSubSearch = {
    waitForFS,
    filterByGenre,
    computeResults,
    renderResults,
    initSearchPage,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    filterByGenre,
    computeResults,
  };
}
