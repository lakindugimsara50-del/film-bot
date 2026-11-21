/**
 * End-to-End Test Suite for FilmZilla 30s Scene Teaser Reels Feed
 * Tests: teasers.html markup, teasers.js runtime engine, data contracts, and interactive contracts.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT_DIR = path.resolve(__dirname, '..');
const TEASERS_HTML_PATH = path.join(ROOT_DIR, 'website', 'teasers.html');
const TEASERS_JS_PATH = path.join(ROOT_DIR, 'website', 'assets', 'js', 'teasers.js');
const TEASERS_CSS_PATH = path.join(ROOT_DIR, 'website', 'assets', 'css', 'teasers.css');
const TEASERS_JSON_PATH = path.join(ROOT_DIR, 'website', 'data', 'teasers.json');
const TEASERS_DATA_JS_PATH = path.join(ROOT_DIR, 'website', 'data', 'teasers_data.js');

let passedTests = 0;
let totalTests = 0;

function runTest(name, fn) {
  totalTests++;
  try {
    fn();
    passedTests++;
    console.log(`  ✅ PASS: ${name}`);
  } catch (err) {
    console.error(`  ❌ FAIL: ${name}`);
    console.error(`     Error: ${err.message}`);
    process.exitCode = 1;
  }
}

console.log('====================================================');
console.log('🎬 RUNNING E2E SUITE: TEASER REELS FEED SYSTEM');
console.log('====================================================\n');

// ----------------------------------------------------
// TEST GROUP 1: FILE EXISTENCE & INTEGRITY
// ----------------------------------------------------
console.log('--- GROUP 1: FILE INTEGRITY & ASSETS ---');

runTest('1.1 Required teaser files exist on disk', () => {
  assert(fs.existsSync(TEASERS_HTML_PATH), 'teasers.html missing');
  assert(fs.existsSync(TEASERS_JS_PATH), 'teasers.js missing');
  assert(fs.existsSync(TEASERS_CSS_PATH), 'teasers.css missing');
  assert(fs.existsSync(TEASERS_JSON_PATH), 'teasers.json missing');
  assert(fs.existsSync(TEASERS_DATA_JS_PATH), 'teasers_data.js missing');
});

runTest('1.2 Teasers CSS stylesheet contains key styles & animations', () => {
  const css = fs.readFileSync(TEASERS_CSS_PATH, 'utf8');
  assert(css.includes('.reels-feed-container'), 'CSS missing .reels-feed-container');
  assert(css.includes('.teaser-card'), 'CSS missing .teaser-card');
  assert(css.includes('.teaser-action-rail'), 'CSS missing .teaser-action-rail');
  assert(css.includes('.smart-seek-modal-backdrop'), 'CSS missing .smart-seek-modal-backdrop');
  assert(css.includes('.btn-watch-full-movie'), 'CSS missing .btn-watch-full-movie');
});

// ----------------------------------------------------
// TEST GROUP 2: HTML DOM STRUCTURE & CONTRACTS
// ----------------------------------------------------
console.log('\n--- GROUP 2: TEASERS.HTML DOM STRUCTURE ---');

const htmlContent = fs.readFileSync(TEASERS_HTML_PATH, 'utf8');

runTest('2.1 teasers.html contains essential viewport and feed container', () => {
  assert(htmlContent.includes('class="reels-app-viewport"'), 'Viewport container missing');
  assert(htmlContent.includes('id="reels-feed-container"'), '#reels-feed-container missing');
  assert(htmlContent.includes('role="main"'), 'Feed container must have role="main"');
});

runTest('2.2 teasers.html contains sound toggle button and header actions', () => {
  assert(htmlContent.includes('id="global-sound-toggle"'), '#global-sound-toggle missing');
  assert(htmlContent.includes('class="reels-brand-badge"'), 'Brand badge missing');
  assert(htmlContent.includes('class="reels-back-btn"'), 'Back to home button missing');
});

runTest('2.3 teasers.html contains category filter rail with all genre pills', () => {
  assert(htmlContent.includes('id="reels-category-rail"'), '#reels-category-rail missing');
  const expectedFilters = ['All', 'Action', 'Comedy', 'Horror', 'Sci-Fi', 'Drama', 'Sinhala Sub'];
  for (const filter of expectedFilters) {
    assert(htmlContent.includes(`data-filter="${filter}"`), `Missing filter pill: ${filter}`);
  }
});

runTest('2.4 teasers.html contains desktop navigation buttons', () => {
  assert(htmlContent.includes('id="desktop-nav-prev"'), '#desktop-nav-prev missing');
  assert(htmlContent.includes('id="desktop-nav-next"'), '#desktop-nav-next missing');
});

runTest('2.5 teasers.html contains Smart Seek Modal with scene and beginning options', () => {
  assert(htmlContent.includes('id="smart-seek-modal"'), '#smart-seek-modal missing');
  assert(htmlContent.includes("FilmZillaReels.executeWatch('scene')"), 'Scene seek action missing');
  assert(htmlContent.includes("FilmZillaReels.executeWatch('beginning')"), 'Start from beginning action missing');
  assert(htmlContent.includes('id="smart-seek-title-text"'), '#smart-seek-title-text missing');
  assert(htmlContent.includes('id="smart-seek-scene-time"'), '#smart-seek-scene-time missing');
});

runTest('2.6 teasers.html includes all required data and engine scripts', () => {
  assert(htmlContent.includes('src="data/movies_data.js"'), 'movies_data.js script missing');
  assert(htmlContent.includes('src="data/teasers_data.js"'), 'teasers_data.js script missing');
  assert(htmlContent.includes('src="assets/js/teasers.js'), 'teasers.js script missing');
});

// ----------------------------------------------------
// TEST GROUP 3: DATA INTEGRITY & SCHEMAS
// ----------------------------------------------------
console.log('\n--- GROUP 3: TEASERS DATA CONTRACTS ---');

const rawJson = fs.readFileSync(TEASERS_JSON_PATH, 'utf8');
const teasersData = JSON.parse(rawJson);

runTest('3.1 teasers.json has non-empty teasers collection', () => {
  assert(Array.isArray(teasersData.teasers), 'teasers must be an array');
  assert(teasersData.teasers.length >= 5, `Expected at least 5 teasers, got ${teasersData.teasers.length}`);
});

runTest('3.2 Each teaser entry adheres to schema contracts', () => {
  teasersData.teasers.forEach((t, i) => {
    assert(t.id && t.id.startsWith('teaser-'), `Teaser #${i} invalid id: ${t.id}`);
    assert(typeof t.movie_id === 'string' && t.movie_id.length > 0, `Teaser #${i} missing movie_id`);
    assert(typeof t.title === 'string' && t.title.length > 0, `Teaser #${i} missing title`);
    assert(typeof t.clip_start === 'number' && t.clip_start >= 0, `Teaser #${i} invalid clip_start`);
    assert(typeof t.clip_end === 'number' && t.clip_end > t.clip_start, `Teaser #${i} invalid clip_end`);
    assert(typeof t.duration === 'number' && t.duration > 0, `Teaser #${i} invalid duration`);
    assert(typeof t.formatted_seek_time === 'string', `Teaser #${i} missing formatted_seek_time`);
    assert(typeof t.scene_hook === 'string', `Teaser #${i} missing scene_hook`);
    assert(typeof t.youtube_embed_url === 'string' && t.youtube_embed_url.includes('youtube-nocookie.com/embed/'), `Teaser #${i} invalid youtube_embed_url`);
  });
});

runTest('3.3 teasers_data.js synchronizes window.FILMSUB_TEASERS', () => {
  const jsDataContent = fs.readFileSync(TEASERS_DATA_JS_PATH, 'utf8');
  assert(jsDataContent.includes('window.FILMSUB_TEASERS ='), 'window.FILMSUB_TEASERS missing');
});

// ----------------------------------------------------
// TEST GROUP 4: TEASERS ENGINE RUNTIME SIMULATION
// ----------------------------------------------------
console.log('\n--- GROUP 4: TEASERS.JS RUNTIME ENGINE SIMULATION ---');

// Setup mock DOM environment in VM
function createMockEnvironment() {
  const storage = {};
  const elements = {};

  class MockElement {
    constructor(tagName, id = '', className = '') {
      this.tagName = tagName.toUpperCase();
      this.id = id;
      this.className = className;
      this.classList = {
        _classes: new Set(className ? className.split(' ') : []),
        add: (c) => this.classList._classes.add(c),
        remove: (c) => this.classList._classes.delete(c),
        contains: (c) => this.classList._classes.has(c),
        toggle: (c) => {
          if (this.classList._classes.has(c)) {
            this.classList._classes.delete(c);
            return false;
          } else {
            this.classList._classes.add(c);
            return true;
          }
        }
      };
      this.children = [];
      this.innerHTML = '';
      this.textContent = '';
      this.style = {};
      this.dataset = {};
      this.eventListeners = {};
    }

    addEventListener(event, handler) {
      if (!this.eventListeners[event]) this.eventListeners[event] = [];
      this.eventListeners[event].push(handler);
    }

    dispatchEvent(event) {
      const handlers = this.eventListeners[event.type] || [];
      handlers.forEach(h => h(event));
    }

    querySelector(sel) {
      if (sel.startsWith('.')) {
        const cls = sel.slice(1);
        if (this.innerHTML.includes(`class="${cls}`) || this.innerHTML.includes(`class='${cls}`)) {
          return new MockElement('div', '', cls);
        }
      }
      return null;
    }

    querySelectorAll(sel) {
      const results = [];
      if (sel === '.teaser-card') {
        const matches = this.innerHTML.match(/<article class="teaser-card"[^>]*>/g) || [];
        matches.forEach((m, idx) => {
          const el = new MockElement('article', `teaser-card-${idx}`, 'teaser-card');
          el.dataset = { paused: 'true', index: String(idx) };
          results.push(el);
        });
      } else if (sel === '.reels-pill') {
        const matches = this.innerHTML.match(/<button[^>]*class="reels-pill[^"]*"[^>]*>/g) || [];
        matches.forEach((_, idx) => results.push(new MockElement('button', '', 'reels-pill')));
      }
      return results;
    }

    appendChild(child) {
      this.children.push(child);
    }

    remove() {}

    getBoundingClientRect() {
      return { left: 0, top: 0, width: 360, height: 640 };
    }
  }

  // Create core DOM elements
  elements['reels-feed-container'] = new MockElement('div', 'reels-feed-container');
  elements['global-sound-toggle'] = new MockElement('button', 'global-sound-toggle');
  elements['reels-category-rail'] = new MockElement('nav', 'reels-category-rail');
  elements['smart-seek-modal'] = new MockElement('div', 'smart-seek-modal');
  elements['smart-seek-title-text'] = new MockElement('strong', 'smart-seek-title-text');
  elements['smart-seek-scene-time'] = new MockElement('span', 'smart-seek-scene-time');
  elements['reels-info-sheet-backdrop'] = new MockElement('div', 'reels-info-sheet-backdrop');
  elements['reels-info-sheet'] = new MockElement('div', 'reels-info-sheet');
  elements['desktop-nav-prev'] = new MockElement('button', 'desktop-nav-prev');
  elements['desktop-nav-next'] = new MockElement('button', 'desktop-nav-next');

  const context = {
    console,
    Date,
    Math,
    Set,
    Array,
    Object,
    String,
    parseInt,
    parseFloat,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    URLSearchParams: class {
      constructor(search) { this.search = search || ''; }
      get() { return null; }
    },
    localStorage: {
      getItem: (k) => storage[k] || null,
      setItem: (k, v) => { storage[k] = String(v); },
      removeItem: (k) => { delete storage[k]; }
    },
    document: {
      readyState: 'complete',
      addEventListener: () => {},
      getElementById: (id) => elements[id] || new MockElement('div', id),
      createElement: (tag) => new MockElement(tag),
      body: new MockElement('body'),
      querySelectorAll: (sel) => []
    },
    window: {
      FILMSUB_TEASERS: teasersData.teasers,
      FILMSUB_DATA: { movies: [] },
      location: { href: 'http://localhost/teasers.html', search: '', origin: 'http://localhost' },
      FilmSub: { showToast: () => {} },
      FilmSubUser: { WatchLater: { has: () => false, toggle: () => {} } }
    },
    navigator: {
      clipboard: { writeText: () => Promise.resolve() }
    },
    IntersectionObserver: class {
      constructor(callback) { this.callback = callback; }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  };

  context.window.document = context.document;
  context.global = context.window;
  vm.createContext(context);
  return { context, elements, storage };
}

runTest('4.1 teasers.js evaluates and initializes FilmZillaReels public API in VM', () => {
  const { context } = createMockEnvironment();
  const teasersJs = fs.readFileSync(TEASERS_JS_PATH, 'utf8');
  vm.runInContext(teasersJs, context);

  assert(context.window.FilmZillaReels, 'window.FilmZillaReels must be defined');
  const requiredMethods = [
    'setFilter',
    'handleLike',
    'handleBookmark',
    'handleShare',
    'promptWatchModal',
    'executeWatch',
    'closeSmartSeekModal',
    'openInfoSheet',
    'closeInfoSheet',
    'scrollByStep'
  ];
  for (const method of requiredMethods) {
    assert(typeof context.window.FilmZillaReels[method] === 'function', `FilmZillaReels.${method} missing or not a function`);
  }
});

runTest('4.2 Feed container renders video cards with rich action buttons and metadata', () => {
  const { context, elements } = createMockEnvironment();
  const teasersJs = fs.readFileSync(TEASERS_JS_PATH, 'utf8');
  vm.runInContext(teasersJs, context);

  const feedHtml = elements['reels-feed-container'].innerHTML;
  assert(feedHtml.includes('class="teaser-card"'), 'No teaser-card rendered in feed');
  assert(feedHtml.includes('class="teaser-action-rail"'), 'Action rail missing from rendered card');
  assert(feedHtml.includes('class="btn-watch-full-movie"'), 'Watch Full Movie button missing from card');
  assert(feedHtml.includes('class="teaser-bottom-progress"'), 'Bottom 30s progress bar missing');
  assert(feedHtml.includes('reels-yt-iframe') || feedHtml.includes('reels-video'), 'Media element missing in card');
});

runTest('4.3 Category filter updates feed to show specific genre teasers', () => {
  const { context, elements } = createMockEnvironment();
  const teasersJs = fs.readFileSync(TEASERS_JS_PATH, 'utf8');
  vm.runInContext(teasersJs, context);

  // Apply Action filter
  context.window.FilmZillaReels.setFilter('Action');
  const actionHtml = elements['reels-feed-container'].innerHTML;
  assert(actionHtml.includes('class="teaser-card"'), 'Action filter should render cards');

  // Reset to All
  context.window.FilmZillaReels.setFilter('All');
  const allHtml = elements['reels-feed-container'].innerHTML;
  assert(allHtml.includes('class="teaser-card"'), 'All filter should render cards');
});

runTest('4.4 Like handler persists teaser like state in localStorage', () => {
  const { context, storage } = createMockEnvironment();
  const teasersJs = fs.readFileSync(TEASERS_JS_PATH, 'utf8');
  vm.runInContext(teasersJs, context);

  const testTeaserId = teasersData.teasers[0].id;
  context.window.FilmZillaReels.handleLike(testTeaserId);

  const storedLikes = JSON.parse(storage['filmzilla_reels_likes'] || '[]');
  assert(storedLikes.includes(testTeaserId), `Expected ${testTeaserId} to be stored in liked list`);

  // Toggle unlike
  context.window.FilmZillaReels.handleLike(testTeaserId);
  const updatedLikes = JSON.parse(storage['filmzilla_reels_likes'] || '[]');
  assert(!updatedLikes.includes(testTeaserId), `Expected ${testTeaserId} to be unliked`);
});

runTest('4.5 Smart Seek modal prompts correct movie details and executes seek navigation', () => {
  const { context, elements } = createMockEnvironment();
  const teasersJs = fs.readFileSync(TEASERS_JS_PATH, 'utf8');
  vm.runInContext(teasersJs, context);

  // Prompt modal for first movie
  const t = teasersData.teasers[0];
  context.window.FilmZillaReels.promptWatchModal(t.movie_id, t.title, t.formatted_seek_time, t.scene_timestamp_movie);

  assert(elements['smart-seek-title-text'].textContent === t.title, 'Modal title not updated');
  assert(elements['smart-seek-scene-time'].textContent === t.formatted_seek_time, 'Modal seek time not updated');
  assert(elements['smart-seek-modal'].classList.contains('open'), 'Modal should have class "open"');

  // Test executeWatch for scene
  context.window.FilmZillaReels.executeWatch('scene');
  assert(context.window.location.href.includes(`movie.html?id=${t.movie_id}&t=${t.scene_timestamp_movie}&from_teaser=1`), 'Scene watch navigation URL incorrect');

  // Test executeWatch for beginning
  context.window.FilmZillaReels.executeWatch('beginning');
  assert(context.window.location.href.includes(`movie.html?id=${t.movie_id}&from_teaser=1`), 'Beginning watch navigation URL incorrect');

  // Test close modal
  context.window.FilmZillaReels.closeSmartSeekModal();
  assert(!elements['smart-seek-modal'].classList.contains('open'), 'Modal should close');
});

// ----------------------------------------------------
// SUMMARY REPORT
// ----------------------------------------------------
console.log('\n====================================================');
console.log(`SUMMARY: ${passedTests} passed, ${totalTests - passedTests} failed`);
console.log('====================================================');

if (passedTests === totalTests) {
  console.log('🎉 ALL TEASER REELS E2E VERIFICATION CHECKS PASSED PERFECTLY!\n');
  process.exit(0);
} else {
  console.error('❌ SOME CHECKS FAILED!\n');
  process.exit(1);
}
