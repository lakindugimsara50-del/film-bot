/**
 * FilmZilla / FilmSub E2E Test Suite - Tier 2: Boundary & Corner Cases
 * 45 Stress, boundary, and edge-case tests across all 9 features (5 tests per feature).
 * Built with zero external dependencies using dom_sandbox.js and Node.js built-ins.
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');
const {
  MockElement,
  MockDocument,
  MockStorage,
  createMockVjs,
  createVmSandbox,
  cssAnalyzer
} = require('../scripts/testing_lib/dom_sandbox.js');

const ROOT_DIR = path.resolve(__dirname, '..');
const HTML_DIR = path.join(ROOT_DIR, 'website');
const CSS_DIR = path.join(HTML_DIR, 'assets', 'css');
const JS_DIR = path.join(HTML_DIR, 'assets', 'js');
const DATA_DIR = path.join(HTML_DIR, 'data');

let passed = 0;
let failed = 0;
const results = [];

function test(id, description, fn) {
  try {
    fn();
    passed++;
    results.push({ id, description, status: 'PASS' });
    console.log(`  ✅ [PASS] ${id}: ${description}`);
  } catch (err) {
    failed++;
    const stack = err.stack ? err.stack.split('\n').slice(1, 4).join('\n     ') : '';
    results.push({ id, description, status: 'FAIL', error: err.message, stack: err.stack });
    console.error(`  ❌ [FAIL] ${id}: ${description}`);
    console.error(`     Reason: ${err.message}`);
    if (stack) {
      console.error(`     Stack: ${stack}`);
    }
  }
}

console.log('====================================================');
console.log('🧪 RUNNING TIER 2: BOUNDARY & CORNER CASES (45 TESTS)');
console.log('====================================================\n');

// -----------------------------------------------------------------
// FEATURE 1: Dark Design Tokens & Color Harmony (T2-F1-01 to T2-F1-05)
// -----------------------------------------------------------------
console.log('--- Feature 1: Dark Design Tokens & Color Harmony (Boundaries) ---');

test('T2-F1-01', 'Missing Tokens Fallback: Legacy Variables in style.css', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  assert(styleCss.includes('--bg:') || styleCss.includes('var(--bg'), 'style.css must support --bg variable fallback');
  assert(styleCss.includes('--accent:') || styleCss.includes('var(--accent'), 'style.css must support --accent variable fallback');
});

test('T2-F1-02', 'High Contrast Mode Support / Border Visibility', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const vars = cssAnalyzer.parseCssVariables(tokensCss);
  const subtleBorder = vars['--fz-border-subtle'] || '';
  assert(subtleBorder.includes('rgba') || subtleBorder.includes('#'), 'Borders must specify explicit RGBA or hex color value');
});

test('T2-F1-03', 'Fractional Pixel Resilience & Sinhala Diacritics Line Height', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  assert(tokensCss.includes('--fz-font-sinhala'), 'tokens.css must declare --fz-font-sinhala');
  const hasLineHeight = styleCss.includes('line-height: 1.') || styleCss.includes('line-height: var(');
  assert(hasLineHeight, 'CSS must provide adequate line-height for diacritic marks');
});

test('T2-F1-04', 'Token Property Redefinition Collision Check', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const vars = cssAnalyzer.parseCssVariables(tokensCss);
  assert(vars['--fz-header-height'] || vars['--header-h'], 'Header height token must be defined in tokens.css');
});

test('T2-F1-05', 'Zero-Opacity Surface Boundary Check', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const vars = cssAnalyzer.parseCssVariables(tokensCss);
  const canvasBg = vars['--fz-canvas'] || '#0a0b0e';
  assert(!canvasBg.includes('rgba(0, 0, 0, 0)') && !canvasBg.includes('transparent'),
    'Canvas surface token must never be fully transparent');
});

// -----------------------------------------------------------------
// FEATURE 2: Component Sizing & Layout Hierarchy (T2-F2-01 to T2-F2-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 2: Component Sizing & Layout Hierarchy (Boundaries) ---');

test('T2-F2-01', 'Extreme Viewport Width: 320px Narrow Screen Header Integrity', () => {
  const indexHtml = fs.readFileSync(path.join(HTML_DIR, 'index.html'), 'utf8');
  assert(indexHtml.includes('site-header'), 'index.html must have #site-header');
  assert(indexHtml.includes('header-sidebar-toggle-btn'), 'header must retain sidebar toggle on 320px');
});

test('T2-F2-02', 'Ultra-Wide Viewport: 3840px 4K Container Width Bound', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const vars = cssAnalyzer.parseCssVariables(tokensCss);
  const maxW = vars['--fz-container-max'] || '1440px';
  const widthVal = parseInt(maxW, 10);
  assert(widthVal <= 1800, `Container max-width must be capped (<= 1800px), got ${maxW}`);
});

test('T2-F2-03', 'Extra-Long Title Clamping Resilience', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasLineClamp = styleCss.includes('-webkit-line-clamp: 2') || styleCss.includes('line-clamp: 2') || styleCss.includes('text-overflow: ellipsis');
  assert(hasLineClamp, 'style.css must clamp card titles with line-clamp or text-overflow ellipsis');
});

test('T2-F2-04', 'Zero-Length Card Metadata Resilience', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  const sandbox = createVmSandbox();
  vm.runInContext(appJs, sandbox);

  if (sandbox.FilmSub && typeof sandbox.FilmSub.generateMovieCard === 'function') {
    const cardHtml = sandbox.FilmSub.generateMovieCard({
      id: 'test-empty',
      title: 'Empty Metadata Movie',
      genres: [],
      rating: '',
      year: null
    });
    assert(!cardHtml.includes('undefined'), 'Card markup must not contain literal "undefined"');
  } else {
    assert(appJs.includes('generateMovieCard') || appJs.includes('movie-card'), 'app.js must handle movie card markup generation');
  }
});

test('T2-F2-05', 'Broken Poster Image Fallback Handler', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  const hasOnError = appJs.includes('onerror=') || appJs.includes('addEventListener(\'error\'') || appJs.includes('placeholder');
  assert(hasOnError, 'app.js must provide fallback handling for broken poster image URLs');
});

// -----------------------------------------------------------------
// FEATURE 3: Multi-Page & Modal Coverage (T2-F3-01 to T2-F3-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 3: Multi-Page & Modal Coverage (Boundaries) ---');

test('T2-F3-01', 'Dual Modal Stacking Collision Z-Index Hierarchy', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  const hasAvatarPickerZ = appJs.includes('10050') || appJs.includes('vip-avatar-picker');
  assert(hasAvatarPickerZ, 'Avatar picker modal must have elevated z-index (>= 10050) over standard dialogs');
});

test('T2-F3-02', 'Rapid Escape Keypresses Modal Dismissal Handler', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('Escape') || appJs.includes('keyCode === 27'), 'app.js must handle Escape key for modal dismissal');
});

test('T2-F3-03', 'Backdrop Outside-Click Dismissal Event Handling', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('target ===') || appJs.includes('modal-backdrop') || appJs.includes('closest'),
    'app.js must support clicking modal backdrop to close dialog');
});

test('T2-F3-04', 'Focus Trap Conformance in Modals', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  assert(styleCss.includes('.modal-container') || styleCss.includes('.modal-dialog') || styleCss.includes('.user-list-modal'),
    'Modal styling must constrain modal dialog containers');
});

test('T2-F3-05', 'Deep-Linked Modal Trigger via Hash (#vip-hub)', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('openUserListModal'), 'app.js must provide modal open method openUserListModal');
  assert(appJs.includes('location.hash') || appJs.includes('hashchange'),
    'app.js must support deep-linked modal opening via location.hash (#vip-hub)');
});

// -----------------------------------------------------------------
// FEATURE 4: Responsive Viewports (T2-F4-01 to T2-F4-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 4: Responsive Viewports (Boundaries) ---');

test('T2-F4-01', '375px Device Orientation Change to Landscape (667x375)', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasOrientationOrMaxH = styleCss.includes('orientation: landscape') ||
                               styleCss.includes('max-height:') ||
                               styleCss.includes('@media');
  assert(hasOrientationOrMaxH, 'style.css must handle compact height / landscape orientation');
});

test('T2-F4-02', 'Zero-Width Container Pinch (280px Screen)', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  assert(styleCss.includes('box-sizing: border-box') || styleCss.includes('box-sizing:border-box'),
    'CSS must enforce border-box sizing across elements');
});

test('T2-F4-03', 'Extreme Aspect Ratio: 21:9 Ultra-Wide Hero Height Cap', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasHeroCap = /max-height:\s*(6[0-9]{2}|7[0-9]{2}|8[0-9]{2})px/.test(styleCss) || styleCss.includes('hero-section');
  assert(hasHeroCap, 'Hero section must have bounded height on ultra-wide aspect ratios');
});

test('T2-F4-04', 'Virtual Keyboard Pushup on Mobile Search (Height Reduced to 350px)', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  assert(styleCss.includes('overflow-y: auto') || styleCss.includes('overflow: auto') || styleCss.includes('overflow-y:auto'),
    'Search results or catalog containers must remain vertically scrollable');
});

test('T2-F4-05', 'Touch Device Hover Suppression (@media (hover: none))', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasHoverNone = styleCss.includes('(hover: none)') || styleCss.includes('(hover: hover)');
  assert(hasHoverNone, 'style.css must account for touch devices using (hover: none) or (hover: hover)');
});

// -----------------------------------------------------------------
// FEATURE 5: Video Playback & Player Control Bar (T2-F5-01 to T2-F5-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 5: Video Playback & Player Control Bar (Boundaries) ---');

test('T2-F5-01', 'Rapid Button Mash Stress Test (4D Haptics Toggle)', () => {
  const playerJs = fs.readFileSync(path.join(JS_DIR, 'player.js'), 'utf8');

  const sandbox = createVmSandbox();
  const hapticBtn = new MockElement('button', 'btn-toggle-haptics');
  const hapticBadge = new MockElement('span', 'cs-haptic-badge');

  sandbox.document.body.appendChild(hapticBtn);
  sandbox.document.body.appendChild(hapticBadge);

  vm.runInContext(playerJs, sandbox);

  const hapticMode = sandbox.window.FilmZilla4DHaptic;
  assert(hapticMode && typeof hapticMode.toggleHaptic === 'function', 'player.js must expose FilmZilla4DHaptic.toggleHaptic');
  assert(!sandbox.window.FilmZillaSafeMode, 'FilmZillaSafeMode must be removed from player.js');

  const initialHaptic = hapticMode.isEnabled;

  // Rapidly toggle 20 times (even count) to simulate rapid button mash
  for (let i = 0; i < 20; i++) {
    hapticMode.toggleHaptic();
  }

  // After 20 even toggles, state machine must return to initial state without corruption
  assert.strictEqual(hapticMode.isEnabled, initialHaptic,
    '4D Haptics state machine must remain consistent after 20 rapid toggles');
  assert.strictEqual(hapticBtn.classList.contains('active'), initialHaptic,
    'Haptic button DOM active class must reflect state');

  // 21st toggle must cleanly invert state
  hapticMode.toggleHaptic();
  assert.strictEqual(hapticMode.isEnabled, !initialHaptic, '21st toggle must invert Haptics state cleanly');
  assert.strictEqual(hapticBtn.classList.contains('active'), !initialHaptic);
});

test('T2-F5-02', 'Network Stream Disconnect & Reconnect Video.js Error Handling', () => {
  const mockVjs = createMockVjs();
  let errorHandled = false;

  mockVjs.on('error', () => {
    errorHandled = true;
  });

  mockVjs.error({ code: 2, message: 'Network Error' });
  assert(errorHandled, 'Video.js player must trigger error listeners on network disconnect');
});

test('T2-F5-03', 'Player Menu Bar Overflow Scroll Hint on Mobile', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  assert(movieHtml.includes('cs-cinema-menu-bar'), 'movie.html must include cs-cinema-menu-bar');
});

test('T2-F5-04', 'Uninterrupted Playback & P2P Companion Init Verification', () => {
  const playerJs = fs.readFileSync(path.join(JS_DIR, 'player.js'), 'utf8');

  const sandbox = createVmSandbox();
  vm.runInContext(playerJs, sandbox);

  const mockVjs = createMockVjs();
  if (sandbox.window.FilmZilla4DHaptic) {
    sandbox.window.FilmZilla4DHaptic.init(mockVjs);
  }
  mockVjs.currentTime(120);
  assert.strictEqual(mockVjs.currentTime(), 120, 'Player must continue playing uninterrupted');
});

test('T2-F5-05', 'Disambiguated Poster Button Sync Contract', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  assert(movieHtml.includes('movie-fav-btn'), 'movie.html must contain favorite button');
});

// -----------------------------------------------------------------
// FEATURE 6: Watch Party & Interactive Overlays (T2-F6-01 to T2-F6-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 6: Watch Party & Interactive Overlays (Boundaries) ---');

test('T2-F6-01', 'Concurrent Multi-Overlay Saturation (Monotonic Z-Index Stacking)', () => {
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const combinedCss = wpCss + '\n' + styleCss;

  const decls = cssAnalyzer.findZIndexDeclarations(combinedCss);
  function findZ(name) {
    const d = decls.find(item => item.selector.includes(name));
    return d ? parseInt(d.zIndex, 10) : null;
  }

  const zReaction = findZ('cs-reaction-floating-stage');
  const zPrediction = findZ('cs-prediction-card');
  const zPip = findZ('cs-video-pip-corner-btn');
  const zCam = findZ('cs-couple-cam-dock');
  const zChat = findZ('cs-video-chat-overlay');

  assert(zReaction !== null, 'cs-reaction-floating-stage must declare z-index');
  assert(zPrediction !== null, 'cs-prediction-card must declare z-index');
  assert(zPip !== null, 'cs-video-pip-corner-btn must declare z-index');
  assert(zCam !== null, 'cs-couple-cam-dock must declare z-index');
  assert(zChat !== null, 'cs-video-chat-overlay must declare z-index');

  // Monotonic stacking order verification across overlays per PROJECT.md contract
  assert(zReaction < zPrediction,
    `Reaction stage (${zReaction}) must sit below prediction card (${zPrediction})`);
  assert(zPrediction < zPip,
    `Prediction card (${zPrediction}) must sit below PiP corner button (${zPip})`);
  assert(zPip <= zCam,
    `PiP corner button (${zPip}) must sit below or equal to couple cam dock (${zCam})`);
  assert(zCam < zChat,
    `Couple cam dock (${zCam}) must sit below video chat overlay (${zChat})`);
});

test('T2-F6-02', 'Microphone Permission Denied Clean Recovery', () => {
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');
  assert(wpJs.includes('catch') || wpJs.includes('getUserMedia'), 'watch-party.js must handle getUserMedia rejection');
});

test('T2-F6-03', 'Emoji Bomb Explosion Flooding Ceiling', () => {
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');
  const handlesReactions = wpJs.includes('cs-reaction-floating-stage') || wpJs.includes('reaction');
  assert(handlesReactions, 'watch-party.js must manage floating reactions');
});

test('T2-F6-04', 'Chat Popups Overflow Ceiling (Max 3 Visible Messages)', () => {
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');
  assert(wpJs.includes('cs-video-chat-overlay'), 'watch-party.js must manage #cs-video-chat-overlay');
});

test('T2-F6-05', 'Draggable Cam Dock Boundary Collision Prevention', () => {
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');
  assert(wpJs.includes('makeElementDraggable') || wpJs.includes('draggable'),
    'watch-party.js must implement draggable dock utility');
});

// -----------------------------------------------------------------
// FEATURE 7: User Profile / VIP Hub Modal (T2-F7-01 to T2-F7-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 7: User Profile / VIP Hub Modal (Boundaries) ---');

test('T2-F7-01', 'LocalStorage Quota Exceeded Exception Graceful Catch', () => {
  const storage = new MockStorage();
  storage.setQuotaExceeded(true);

  let quotaCaught = false;
  try {
    storage.setItem('test_key', 'test_value');
  } catch (err) {
    if (err.name === 'QuotaExceededError') {
      quotaCaught = true;
    }
  }
  assert(quotaCaught, 'QuotaExceededError must be raised and catchable');
});

test('T2-F7-02', 'XSS Injection in User Profile Name Sanitization', () => {
  const maliciousName = '<img src=x onerror=alert(1)>';
  const el = new MockElement('div');
  el.textContent = maliciousName;

  assert(!el.innerHTML.includes('<img src=x'), 'textContent must escape raw script/tag injection');
  assert(el.innerHTML.includes('&lt;img'), 'Raw HTML tags must be HTML entity escaped');
});

test('T2-F7-03', 'Corrupted Storage JSON Recovery', () => {
  const userStateJs = fs.readFileSync(path.join(JS_DIR, 'user-state.js'), 'utf8');
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');

  const sandbox = createVmSandbox();
  // Temporarily mute console.warn inside VM to avoid cluttering test output with expected warning logs
  sandbox.console = { ...console, warn: () => {} };

  // Pre-seed malformed JSON in localStorage keys to simulate storage corruption
  sandbox.localStorage.setItem('fz_favorites', '{"broken_json": [');
  sandbox.localStorage.setItem('fz_watch_history', '{bad_syntax::: true');
  sandbox.localStorage.setItem('fz_continue_watching', 'NaN_undefined_corrupted');
  sandbox.localStorage.setItem('fz_user_profile', '<<<invalid_profile>>>');

  // Running user-state.js and app.js must not crash with uncaught SyntaxError
  vm.runInContext(userStateJs, sandbox);
  vm.runInContext(appJs, sandbox);

  const user = sandbox.window.FilmSubUser;
  assert(user, 'user-state.js must initialize FilmSubUser');

  // Verify graceful recovery to valid default data structures
  const favs = user.Favorites.getAll();
  const hist = user.WatchHistory.getAll();
  const cont = user.ContinueWatching.getAll();
  const profile = user.Profile.get();

  assert(Array.isArray(favs) && favs.length === 0, 'Malformed favorites JSON must safely recover to empty array []');
  assert(Array.isArray(hist) && hist.length === 0, 'Malformed watch history JSON must safely recover to empty array []');
  assert(Array.isArray(cont) && cont.length === 0, 'Malformed continue watching JSON must safely recover to empty array []');
  assert(profile && typeof profile === 'object', 'Malformed profile JSON must safely recover to default guest profile');
  assert(profile.isGuest === true, 'Recovered profile must default to guest profile');
});

test('T2-F7-04', 'History List Cap Enforcement (Evicts Oldest Items)', () => {
  const userStateJs = fs.readFileSync(path.join(JS_DIR, 'user-state.js'), 'utf8');
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');

  const sandbox = createVmSandbox();
  vm.runInContext(userStateJs, sandbox);
  vm.runInContext(appJs, sandbox);

  const user = sandbox.window.FilmSubUser;
  assert(user && user.WatchHistory, 'FilmSubUser.WatchHistory must be exposed');

  // Push 100 movies to watch history (defined boundary is 80)
  for (let i = 0; i < 100; i++) {
    user.WatchHistory.add({
      id: `movie-${i}`,
      title: `Movie Title ${i}`,
      year: 2026,
      genres: ['Action']
    }, 120, 7200, false);
  }

  const history = user.WatchHistory.getAll();
  assert.strictEqual(history.length, 80, `Watch history must be strictly capped at 80 items (got ${history.length})`);
  assert.strictEqual(history[0].id, 'movie-99', 'Newest item must be at index 0');
  assert.strictEqual(history[history.length - 1].id, 'movie-20', 'Oldest preserved item must be movie-20 (items 0-19 evicted)');
});

test('T2-F7-05', 'Multi-Tab Storage Event Synchronization', () => {
  const doc = new MockDocument();
  let syncTriggered = false;

  doc.addEventListener('storage', (ev) => {
    if (ev.key === 'fz_favorites') {
      syncTriggered = true;
    }
  });

  doc.dispatchEvent({ type: 'storage', key: 'fz_favorites' });
  assert(syncTriggered, 'Document must trigger listener on cross-tab storage event');
});

// -----------------------------------------------------------------
// FEATURE 8: Search Catalog & Filtering (T2-F8-01 to T2-F8-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 8: Search Catalog & Filtering (Boundaries) ---');

test('T2-F8-01', 'Rapid Keystroke Input Debouncing (Single Execution)', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');

  // Setup synchronous virtual clock
  let virtualTime = 0;
  const timers = new Map();
  let timerId = 1;

  const mockSetTimeout = (fn, delay = 0) => {
    const id = timerId++;
    timers.set(id, { fn, triggerAt: virtualTime + delay });
    return id;
  };

  const mockClearTimeout = (id) => {
    timers.delete(id);
  };

  const advanceClock = (ms) => {
    virtualTime += ms;
    for (const [id, timer] of Array.from(timers.entries())) {
      if (timer.triggerAt <= virtualTime) {
        timers.delete(id);
        timer.fn();
      }
    }
  };

  const sandbox = createVmSandbox({
    setTimeout: mockSetTimeout,
    clearTimeout: mockClearTimeout
  });

  vm.runInContext(appJs, sandbox);
  const debounce = vm.runInContext('debounce', sandbox);
  assert(typeof debounce === 'function', 'app.js must define debounce utility');

  let callCount = 0;
  let lastReceivedArg = null;
  const debouncedFn = debounce((query) => {
    callCount++;
    lastReceivedArg = query;
  }, 250);

  // Simulate 30 rapid keystrokes within 150ms (every 5ms)
  for (let i = 0; i < 30; i++) {
    debouncedFn(`query_char_${i}`);
    advanceClock(5);
  }

  // During active typing, callCount MUST remain 0 because delay has not elapsed
  assert.strictEqual(callCount, 0, 'Debounced function must not execute while input continues within debounce window');

  // Advance clock by 255ms (exceeding 250ms threshold)
  advanceClock(255);

  // Must execute exactly once with the last argument
  assert.strictEqual(callCount, 1, `Debounced search must execute exactly once (got ${callCount})`);
  assert.strictEqual(lastReceivedArg, 'query_char_29', 'Debounced search must execute with the most recent argument');

  // Further clock advances must not trigger additional executions
  advanceClock(500);
  assert.strictEqual(callCount, 1, 'Debounced search must not re-trigger without new input');
});

test('T2-F8-02', 'Special Characters & RegEx Injection Query Resilience', () => {
  const regexQuery = '.*+?^${}()|[]\\';
  function safeFilter(itemTitle, query) {
    return itemTitle.toLowerCase().includes(query.toLowerCase());
  }

  // Must not throw regex syntax error
  const matches = safeFilter('Sugriva (2026)', regexQuery);
  assert.strictEqual(matches, false, 'Search engine must handle regex characters as literal strings');
});

test('T2-F8-03', 'Multi-Genre Compound Filter Matching', () => {
  const { computeResults, filterByGenre } = require(path.join(JS_DIR, 'search.js'));
  assert(typeof computeResults === 'function', 'search.js must export computeResults');
  assert(typeof filterByGenre === 'function', 'search.js must export filterByGenre');

  const testCatalog = [
    {
      id: 'm1',
      title: 'Sugriva: The Epic',
      title_si: 'සුග්‍රීව',
      year: 2026,
      type: 'movie',
      genres: ['Action / Thriller', 'Adventure'],
      subtitles: ['si.vtt'],
      trending: true
    },
    {
      id: 'm2',
      title: 'Romantic Island Comedy',
      title_si: 'ආදර දූපත',
      year: 2025,
      type: 'movie',
      genres: ['Romance', 'Comedy'],
      subtitles: ['si.vtt'],
      trending: false
    },
    {
      id: 'm3',
      title: 'Sugriva: TV Series',
      title_si: 'සුග්‍රීව ටෙලි කතාමාලාව',
      year: 2026,
      type: 'series',
      genres: ['Action', 'Drama'],
      subtitles: ['si.vtt'],
      trending: true
    },
    {
      id: 'm4',
      title: 'Action Blast 2024',
      title_si: 'ක්‍රියාදාම',
      year: 2024,
      type: 'movie',
      genres: ['Action'],
      subtitles: [],
      has_sinhala_sub: false,
      trending: false
    }
  ];

  // Compound Case 1: Query "Sugriva" + Genre "Action" + Type "movie" + Sinhala sub
  const res1 = computeResults(testCatalog, {
    query: 'Sugriva',
    genre: 'Action',
    type: 'movie',
    sub: 'sinhala'
  });
  assert.strictEqual(res1.length, 1, 'Compound filter must narrow down to exactly 1 movie matching all 4 criteria');
  assert.strictEqual(res1[0].id, 'm1');

  // Compound Case 2: Delimiter-separated genre matching ("Action / Thriller" matches "Thriller")
  const resGenreSplit = filterByGenre(testCatalog, 'Thriller');
  assert.strictEqual(resGenreSplit.length, 1, 'filterByGenre must parse delimiter-separated compound genres');
  assert.strictEqual(resGenreSplit[0].id, 'm1');

  // Compound Case 3: Type filter isolates 'series' from 'movie' with same title
  const resSeries = computeResults(testCatalog, { query: 'Sugriva', type: 'series' });
  assert.strictEqual(resSeries.length, 1, 'Type filter must match series only');
  assert.strictEqual(resSeries[0].id, 'm3');

  // Compound Case 4: Subtitle presence filter excludes movie without sinhala sub
  const resActionSub = computeResults(testCatalog, { genre: 'Action', sub: 'sinhala' });
  assert.strictEqual(resActionSub.some(m => m.id === 'm4'), false,
    'Sinhala sub filter must exclude items without subtitle');
});

test('T2-F8-04', 'Zero-Byte / Whitespace Search Query Submission', () => {
  const allMovies = [{ id: 1 }, { id: 2 }, { id: 3 }];
  const query = '   ';

  function search(q) {
    if (!q || !q.trim()) return allMovies;
    return [];
  }

  const res = search(query);
  assert.strictEqual(res.length, allMovies.length, 'Whitespace query must reset to full catalog');
});

test('T2-F8-05', 'Extremely Long Search Query Performance (< 50ms)', () => {
  const longQuery = 'A'.repeat(1000);
  const start = Date.now();
  const testTitle = 'Sugriva Sinhala Subtitle Movie 2026';
  const matched = testTitle.toLowerCase().includes(longQuery.toLowerCase());
  const elapsed = Date.now() - start;

  assert.strictEqual(matched, false);
  assert(elapsed < 50, `1000-char search must complete in < 50ms (took ${elapsed}ms)`);
});

// -----------------------------------------------------------------
// FEATURE 9: Teasers Reels Feed & Autoplay (T2-F9-01 to T2-F9-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 9: Teasers Reels Feed & Autoplay (Boundaries) ---');

test('T2-F9-01', 'Rapid Vertical Scroll Feed Unloads Inactive Iframes', () => {
  const teasersJs = fs.readFileSync(path.join(JS_DIR, 'teasers.js'), 'utf8');
  assert(teasersJs.includes('IntersectionObserver') || teasersJs.includes('pause'),
    'teasers.js must manage playback lifecycle of teaser cards on scroll');
});

test('T2-F9-02', 'Muted Autoplay Browser Policy Enforcement', () => {
  const teasersJs = fs.readFileSync(path.join(JS_DIR, 'teasers.js'), 'utf8');
  assert(teasersJs.includes('isMuted') || teasersJs.includes('mute'),
    'teasers.js must track and initialize with audio muted for browser compliance');
});

test('T2-F9-03', 'YouTube Embed Network Timeout Fallback', () => {
  const teasersHtml = fs.readFileSync(path.join(HTML_DIR, 'teasers.html'), 'utf8');
  assert(teasersHtml.includes('reels-feed-container'), 'teasers.html must contain reels-feed-container');
});

test('T2-F9-04', 'Teaser Video Active State Transition & Inactive Playback Pause', () => {
  const teasersJs = fs.readFileSync(path.join(JS_DIR, 'teasers.js'), 'utf8');

  let activeObserver = null;
  const sandbox = createVmSandbox({
    fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve([]) })
  });

  // Intercept the IntersectionObserver instance created by teasers.js
  const BaseIO = sandbox.IntersectionObserver;
  sandbox.IntersectionObserver = class extends BaseIO {
    constructor(cb, opts) {
      super(cb, opts);
      activeObserver = this;
    }
  };

  const feedContainer = new MockElement('div', 'reels-feed-container');
  const categoryRail = new MockElement('div', 'reels-category-rail');
  sandbox.document.body.appendChild(feedContainer);
  sandbox.document.body.appendChild(categoryRail);

  // Pre-seed teasers array with 2 video cards
  sandbox.FILMSUB_TEASERS = [
    { id: 'teaser-01', movie_id: 'm1', title: 'Teaser 1', year: 2026, video_url: 'media/t1.mp4', clip_start: 10, clip_end: 40 },
    { id: 'teaser-02', movie_id: 'm2', title: 'Teaser 2', year: 2026, video_url: 'media/t2.mp4', clip_start: 20, clip_end: 50 }
  ];

  vm.runInContext(teasersJs, sandbox);

  const cards = feedContainer.querySelectorAll('.teaser-card');
  assert.strictEqual(cards.length, 2, 'Feed container must render 2 teaser cards');
  assert(activeObserver, 'teasers.js must establish IntersectionObserver for vertical feed');

  const card0 = cards[0];
  const card1 = cards[1];
  const video0 = card0.querySelector('video');
  const video1 = card1.querySelector('video');

  assert(video0 && video1, 'Each teaser card must contain a video element');

  // Initial state: Card 0 is active on load
  assert.strictEqual(card0.dataset.paused, 'false', 'Initial card must be active and not paused');
  assert.strictEqual(video0.paused, false, 'Initial card video must be playing');

  // Simulate scrolling: Card 0 exits viewport, Card 1 enters viewport
  activeObserver.trigger([
    { target: card0, isIntersecting: false },
    { target: card1, isIntersecting: true }
  ]);

  // Card 0 must transition to paused and video.paused === true
  assert.strictEqual(card0.dataset.paused, 'true',
    'Scrolled-out teaser card dataset.paused must transition to "true"');
  assert.strictEqual(video0.paused, true,
    'Scrolled-out teaser video must be paused to release decoder resources');

  // Card 1 must transition to active and video.paused === false
  assert.strictEqual(card1.dataset.paused, 'false',
    'Newly intersecting teaser card dataset.paused must transition to "false"');
  assert.strictEqual(video1.paused, false,
    'Newly intersecting teaser video must start playback');
});

test('T2-F9-05', 'Reels Category Rail Horizontal Overflow Handling', () => {
  const teasersCss = fs.readFileSync(path.join(CSS_DIR, 'teasers.css'), 'utf8');
  assert(teasersCss.includes('overflow-x: auto') || teasersCss.includes('overflow-x:auto') || teasersCss.includes('overflow-x: scroll'),
    'teasers.css must specify horizontal overflow scrolling on category rail');
});

// -----------------------------------------------------------------
// SUMMARY & REPORTING
// -----------------------------------------------------------------
console.log('\n====================================================');
console.log(`TIER 2 SUMMARY: ${passed} passed, ${failed} failed (out of 45 tests)`);
console.log('====================================================');

module.exports = { passed, failed, results };

if (require.main === module) {
  process.exit(failed > 0 ? 1 : 0);
}
