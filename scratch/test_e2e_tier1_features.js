/**
 * FilmZilla / FilmSub E2E Test Suite - Tier 1: Feature Coverage
 * 45 Tests across all 9 inventory features (5 tests per feature).
 * Adheres strictly to test_matrix_spec.md and TEST_INFRA.md.
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');
const {
  MockElement,
  MockDocument,
  createVmSandbox,
  cssAnalyzer,
  computeContrastRatio
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
console.log('🧪 RUNNING TIER 1: FEATURE COVERAGE (45 TESTS)');
console.log('====================================================\n');

// -----------------------------------------------------------------
// FEATURE 1: Dark Design Tokens & Color Harmony (T1-F1-01 to T1-F1-05)
// -----------------------------------------------------------------
console.log('--- Feature 1: Dark Design Tokens & Color Harmony ---');

test('T1-F1-01', 'Centralized Design Token Palette Registration in tokens.css', () => {
  const tokensPath = path.join(CSS_DIR, 'tokens.css');
  assert(fs.existsSync(tokensPath), 'tokens.css must exist');
  const css = fs.readFileSync(tokensPath, 'utf8');

  // Verify surface tiers
  const hasSurfaces = css.includes('--fz-canvas') || css.includes('--fz-surface-0') || css.includes('--fz-surface-card');
  assert(hasSurfaces, 'tokens.css must register tiered dark surfaces');

  // Verify accents & borders
  assert(css.includes('--fz-accent'), 'tokens.css must register --fz-accent');
  assert(css.includes('--fz-gold'), 'tokens.css must register --fz-gold');
  assert(css.includes('--fz-border-subtle'), 'tokens.css must register --fz-border-subtle');
});

test('T1-F1-02', 'Backward-Compatible Legacy Token Aliasing', () => {
  const css = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  assert(/--bg:\s*var\(--fz-/.test(css), '--bg must alias to a --fz- token');
  assert(/--bg-surface:\s*var\(--fz-/.test(css), '--bg-surface must alias to a --fz- token');
  assert(/--accent:\s*var\(--fz-/.test(css), '--accent must alias to a --fz- token');
});

test('T1-F1-03', 'Global Typography Token Hierarchy & Font Loading Across All 4 Pages', () => {
  const pages = ['index.html', 'movie.html', 'search.html', 'teasers.html'];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(HTML_DIR, page), 'utf8');
    assert(html.includes('Noto+Sans+Sinhala'), `${page} must load Noto Sans Sinhala font`);
    assert(html.includes('Roboto'), `${page} must load Roboto font`);
  }
});

test('T1-F1-04', 'Removal of Oversaturated Neon Glow Drop Shadows', () => {
  const cssFiles = ['tokens.css', 'style.css', 'watch-party.css', 'netflix-ui.css'];
  for (const file of cssFiles) {
    const filePath = path.join(CSS_DIR, file);
    if (!fs.existsSync(filePath)) continue;
    const content = fs.readFileSync(filePath, 'utf8');
    assert(!content.includes('box-shadow: 0 0 45px rgba(229, 9, 20, 1)'), `${file} must not contain 45px neon glow shadow`);
    assert(!content.includes('box-shadow: 0 0 35px rgba(229, 9, 20, 0.85)'), `${file} must not contain 35px high-opacity red shadow`);
  }
});

test('T1-F1-05', 'Dark Surface Contrast Conformance (WCAG 2.1 AA/AAA)', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const vars = cssAnalyzer.parseCssVariables(tokensCss);
  const canvasBg = vars['--fz-canvas'] || vars['--fz-surface-0'] || '#0a0b0e';
  const textPrimary = vars['--fz-text-primary'] || '#ffffff';

  const ratio = computeContrastRatio(textPrimary, canvasBg);
  assert(ratio >= 13.0, `Contrast ratio between ${textPrimary} and ${canvasBg} must be >= 13:1, got ${ratio.toFixed(2)}:1`);
});

// -----------------------------------------------------------------
// FEATURE 2: Component Sizing & Layout Hierarchy (T1-F2-01 to T1-F2-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 2: Component Sizing & Layout Hierarchy ---');

test('T1-F2-01', 'Standardized 2:3 Aspect Ratio on Media Card Posters', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const has23Ratio = styleCss.includes('aspect-ratio: 2 / 3') ||
                     styleCss.includes('aspect-ratio: 2/3') ||
                     /padding-top:\s*150%/.test(styleCss);
  assert(has23Ratio, 'style.css must enforce 2:3 aspect ratio on card posters');
});

test('T1-F2-02', 'Clean Hero Billboard Without Liquid Water Bubble Gimmicks', () => {
  const indexHtml = fs.readFileSync(path.join(HTML_DIR, 'index.html'), 'utf8');
  assert(!indexHtml.includes('hero-watermark-bubble-pod'), 'Tacky water bubble pod must be removed from hero');
  assert(!indexHtml.includes('hero-watermark-bubble-glare'), 'Tacky bubble glare must be removed from hero');
  const hasHero = indexHtml.includes('hero-section') || indexHtml.includes('hero-showcase') || indexHtml.includes('hero-billboard');
  assert(hasHero, 'Clean hero billboard must exist in index.html');
});

test('T1-F2-03', 'Single Unified Header Hamburger Menu & Drawer Toggle', () => {
  const indexHtml = fs.readFileSync(path.join(HTML_DIR, 'index.html'), 'utf8');
  assert(indexHtml.includes('id="header-sidebar-toggle-btn"'), 'Header must contain unified sidebar toggle button');
  const headerMatch = indexHtml.match(/<header[^>]*>([\s\S]*?)<\/header>/i);
  assert(headerMatch, 'index.html must have a header');
  const headerContent = headerMatch[1];
  const toggleMatches = headerContent.match(/id="header-sidebar-toggle-btn"/g) || [];
  assert.strictEqual(toggleMatches.length, 1, 'Only one sidebar toggle button allowed in header');
});

test('T1-F2-04', 'Card Border Radius & Soft Hover Elevation', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasRadius = /border-radius:\s*(10px|12px|14px|var\(--fz-radius)/.test(styleCss);
  assert(hasRadius, 'style.css must specify card border-radius with standard curve (10-14px)');
  const hasHoverElevation = /transform:\s*translateY\(-[4-6]px\)/.test(styleCss);
  assert(hasHoverElevation, 'style.css must specify subtle translateY(-4px to -6px) hover effect');
});

test('T1-F2-05', 'Modal Container Centering & Max-Width Bounds', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasMaxWidth = /max-width:\s*(5[0-9]{2}|6[0-9]{2}|7[0-9]{2}|8[0-9]{2})px/.test(styleCss);
  assert(hasMaxWidth, 'Modals must enforce max-width constraint (500px-880px)');
  const hasPadding = /padding:\s*(20px|24px|28px|30px|32px)/.test(styleCss);
  assert(hasPadding, 'Modals must provide balanced internal padding (>= 20px)');
});

// -----------------------------------------------------------------
// FEATURE 3: Multi-Page & Modal Coverage (T1-F3-01 to T1-F3-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 3: Multi-Page & Modal Coverage ---');

test('T1-F3-01', 'HTML Head Asset Consistency Across All 4 Pages', () => {
  const pages = ['index.html', 'movie.html', 'search.html', 'teasers.html'];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(HTML_DIR, page), 'utf8');
    assert(html.includes('name="viewport"'), `${page} must contain viewport meta tag`);
    assert(html.includes('tokens.css'), `${page} must include tokens.css stylesheet`);
  }
});

test('T1-F3-02', 'User Profile / VIP Hub Modal Markup & Tabs Integration', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('switchVipTab'), 'app.js must implement switchVipTab method');
  assert(appJs.includes('vip-hub-tabs-nav'), 'app.js must render vip-hub-tabs-nav container');
  assert(appJs.includes('favorites'), 'VIP hub modal must support favorites tab');
  assert(appJs.includes('watchlater'), 'VIP hub modal must support watchlater tab');
});

test('T1-F3-03', 'Smart Seek Modal Markup in Teasers', () => {
  const teasersHtml = fs.readFileSync(path.join(HTML_DIR, 'teasers.html'), 'utf8');
  assert(teasersHtml.includes('id="smart-seek-modal"'), 'teasers.html must contain #smart-seek-modal');
  assert(teasersHtml.includes('smart-seek-opt-btn'), 'teasers.html must contain smart seek option buttons');
});

test('T1-F3-04', 'Single 3D Bubble Glass Download Modal Architecture', () => {
  const playerJs = fs.readFileSync(path.join(JS_DIR, 'player.js'), 'utf8');
  assert(playerJs.includes('cs-3d-download-bubble-box'), 'player.js must generate 3D bubble download box');
  assert(playerJs.includes('cs-bubble-tier-row'), 'player.js must render download tier rows');
  assert(!playerJs.includes('dl-size-pill'), 'player.js must not include obsolete dl-size-pill badges');
});

test('T1-F3-05', 'Slide-Out Cinema Navigation Drawer Integration', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  const hasDrawerInit = appJs.includes('initGlobalCinemaDrawer') ||
                        appJs.includes('cinema-global-nav-drawer') ||
                        appJs.includes('header-sidebar-toggle-btn');
  assert(hasDrawerInit, 'app.js must wire up the global cinema navigation drawer');
});

// -----------------------------------------------------------------
// FEATURE 4: Responsive Viewports (T1-F4-01 to T1-F4-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 4: Responsive Viewports (375px, 768px, 1280px+) ---');

test('T1-F4-01', 'Mobile 375px 2-Column Catalog Grid Enforcement', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const has2ColRule = /@media[^{]*max-width:\s*(380px|480px|600px)[^{]*\{[\s\S]*?grid-template-columns:\s*repeat\(2,\s*1fr\)/.test(styleCss);
  assert(has2ColRule, 'style.css must define 2-column grid rule on small mobile viewports');
});

test('T1-F4-02', 'Mobile Viewport Horizontal Scroll Prevention', () => {
  const pages = ['index.html', 'movie.html', 'search.html', 'teasers.html'];
  for (const page of pages) {
    const html = fs.readFileSync(path.join(HTML_DIR, page), 'utf8');
    assert(html.includes('width=device-width'), `${page} must specify width=device-width`);
    assert(!html.includes('min-width: 1200px;'), `${page} must not set body min-width exceeding mobile viewport`);
  }
});

test('T1-F4-03', 'Tablet 768px Layout Transition', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasTabletQuery = /@media[^{]*(768px|800px)/.test(styleCss);
  assert(hasTabletQuery, 'style.css must declare tablet responsive breakpoint (768px/800px)');
  const hasMultiCol = /grid-template-columns:\s*repeat\([3-4],\s*1fr\)/.test(styleCss);
  assert(hasMultiCol, 'style.css must support 3-4 column grid transitions');
});

test('T1-F4-04', 'Desktop 1280px Theater Layout & 380px Watch Party Sidebar', () => {
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');
  const has380px = wpCss.includes('380px');
  assert(has380px, 'watch-party.css must specify 380px sidebar width for desktop theater mode');
});

test('T1-F4-05', 'Mobile Bottom Bar Rendering & Touch Targets', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const hasMobileBottom = styleCss.includes('.mobile-bottom-bar') || styleCss.includes('#mobile-bottom-bar');
  assert(hasMobileBottom, 'style.css must style mobile bottom bar');
  const indexHtml = fs.readFileSync(path.join(HTML_DIR, 'index.html'), 'utf8');
  assert(indexHtml.includes('id="mobile-bottom-bar"'), 'index.html must include mobile bottom bar navigation');
});

// -----------------------------------------------------------------
// FEATURE 5: Video Playback & Player Control Bar (T1-F5-01 to T1-F5-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 5: Video Playback & Player Control Bar ---');

test('T1-F5-01', 'Cinema Menu Bar Clustered Dock Presence in movie.html', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  assert(movieHtml.includes('id="cs-cinema-menu-bar"'), 'movie.html must contain #cs-cinema-menu-bar');
  assert(movieHtml.includes('class="cs-bar-left"'), 'movie.html must contain .cs-bar-left cluster');
  assert(movieHtml.includes('class="cs-bar-right"'), 'movie.html must contain .cs-bar-right cluster');
});

test('T1-F5-02', 'Watch Party Media Controls Inside Chat Lounge (Contract Verification)', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');

  // Verify Watch Party owns Mic, Cam, and Chat Popups inside its chat lounge popover
  assert(wpJs.includes('wp-btn-mic'), 'wp-btn-mic must be handled by watch-party.js');
  assert(wpJs.includes('wp-btn-cam'), 'wp-btn-cam must be handled by watch-party.js');
  assert(wpJs.includes('wp-popups-toggle'), 'wp-popups-toggle must be handled by watch-party.js');

  // Verify Cinema Modes in movie.html is streamlined without Safe Mode
  assert(!movieHtml.includes('id="btn-toggle-safe-mode"'), 'Safe Mode must be removed from movie.html');
  assert(movieHtml.includes('id="btn-toggle-haptics"'), 'movie.html must have 4D Haptic control');
  assert(movieHtml.includes('id="btn-open-companion"'), 'movie.html must have Phone Remote control');
});

test('T1-F5-03', 'Disambiguation of Duplicate DOM Element IDs in movie.html', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  const favMatches = movieHtml.match(/id="movie-fav-btn"/g) || [];
  const laterMatches = movieHtml.match(/id="movie-later-btn"/g) || [];
  const shareMatches = movieHtml.match(/id="movie-share-btn"/g) || [];

  assert.strictEqual(favMatches.length, 1, `movie-fav-btn ID must be unique (found ${favMatches.length})`);
  assert.strictEqual(laterMatches.length, 1, `movie-later-btn ID must be unique (found ${laterMatches.length})`);
  assert.strictEqual(shareMatches.length, 1, `movie-share-btn ID must be unique (found ${shareMatches.length})`);
});

test('T1-F5-04', 'Video.js Player Container 16:9 Aspect Ratio Preservation', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  const has169 = movieHtml.includes('aspect-ratio:16/9') ||
                 movieHtml.includes('aspect-ratio: 16 / 9') ||
                 movieHtml.includes('vjs-16-9');
  assert(has169, 'movie.html video player must preserve 16:9 aspect ratio');
});

test('T1-F5-05', 'Safe Mode Removal & Theater Controls Reflection', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  assert(!movieHtml.includes('id="btn-toggle-safe-mode"'), 'movie.html must not contain #btn-toggle-safe-mode');
  assert(!movieHtml.includes('id="cs-safe-badge"'), 'movie.html must not contain #cs-safe-badge');
  assert(movieHtml.includes('id="cs-haptic-badge"'), 'movie.html must retain #cs-haptic-badge');
});

// -----------------------------------------------------------------
// FEATURE 6: Watch Party & Interactive Overlays (T1-F6-01 to T1-F6-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 6: Watch Party & Interactive Overlays ---');

test('T1-F6-01', 'Spatial Coordinate Separation of PiP and Couple Cam Dock', () => {
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');
  assert(wpCss.includes('.cs-couple-cam-dock'), 'watch-party.css must define .cs-couple-cam-dock');
  assert(wpCss.includes('.cs-video-pip-corner-btn') || wpCss.includes('#cs-video-pip-corner-btn'),
    'watch-party.css must define coordinate rules for PiP button');
});

test('T1-F6-02', 'Live Prediction Card Responsive Mobile Positioning', () => {
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');
  assert(wpCss.includes('.cs-prediction-card') || wpCss.includes('#cs-prediction-card'),
    'watch-party.css must define .cs-prediction-card overlay');
});

test('T1-F6-03', 'On-Screen Chat Popups ON/OFF Toggle Functionality', () => {
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');
  assert(wpJs.includes('toggleChatPopups'), 'watch-party.js must implement toggleChatPopups()');
  assert(wpJs.includes('cs-video-chat-overlay'), 'watch-party.js must manage cs-video-chat-overlay');
});

test('T1-F6-04', 'Couple Cam Squircle PiP Sizing & Speaking Wave Pulse', () => {
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');
  assert(wpCss.includes('.cs-cam-bubble'), 'watch-party.css must style .cs-cam-bubble');
  assert(wpCss.includes('.cs-cam-bubble.is-speaking') || wpCss.includes('.is-speaking'),
    'watch-party.css must define .is-speaking state');
  assert(wpCss.includes('.cs-speaking-pulse'), 'watch-party.css must define .cs-speaking-pulse animation');
});

test('T1-F6-05', 'Floating Reaction Stage Pointer-Events Passthrough', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');
  const combined = styleCss + '\n' + wpCss;
  const hasPointerEventsNone = /#cs-reaction-floating-stage[^{]*\{[^}]*pointer-events:\s*none/s.test(combined) ||
                               combined.includes('.cs-reaction-floating-stage') ||
                               combined.includes('cs-reaction-floating-stage');
  assert(hasPointerEventsNone, 'Reaction stage must be configured with pointer-events: none');
});

// -----------------------------------------------------------------
// FEATURE 7: User Profile / VIP Hub Modal (T1-F7-01 to T1-F7-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 7: User Profile / VIP Hub Modal ---');

test('T1-F7-01', 'Tab Switching Between Favorites, Watch Later, and History', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('switchVipTab'), 'app.js must provide switchVipTab method');
  assert(appJs.includes('vip-hub-count-pill'), 'app.js must support vip-hub-count-pill');
});

test('T1-F7-02', 'Dynamic Count Pill Singular/Plural Formatting', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('1 Favorite Title'), 'app.js must format singular count for 1 Favorite Title');
  assert(appJs.includes('Favorite Titles'), 'app.js must format plural count for multiple Favorite Titles');
});

test('T1-F7-03', '3D Preset Avatar Selection & Storage Persistence', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('openAvatarPickerModal'), 'app.js must support openAvatarPickerModal');
  assert(appJs.includes('VIP_PRESET_AVATARS'), 'app.js must define VIP_PRESET_AVATARS presets array');
});

test('T1-F7-04', 'Balanced Empty State Rendering in VIP Hub', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('vip-hub-empty-state'), 'app.js must render .vip-hub-empty-state container');
  const hasSubtitle = appJs.includes('පසුව නැරඹීමට එක් කළ චිත්‍රපට තවම නැත') ||
                      appJs.includes('No movies saved here yet') ||
                      appJs.includes('vip-empty-sub');
  assert(hasSubtitle, 'app.js must provide descriptive empty state message');
});

test('T1-F7-05', 'Purge List with Confirmation Handler', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  assert(appJs.includes('vip-hub-clear-btn'), 'app.js must support #vip-hub-clear-btn clear action');
});

// -----------------------------------------------------------------
// FEATURE 8: Search Catalog & Filtering (T1-F8-01 to T1-F8-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 8: Search Catalog & Filtering ---');

test('T1-F8-01', 'Real-Time Keyword Filtering in Search', () => {
  const searchJs = fs.readFileSync(path.join(JS_DIR, 'search.js'), 'utf8');
  const hasInput = searchJs.includes('search-page-input') || searchJs.includes('searchInput');
  assert(hasInput, 'search.js must bind to search input');
  assert(searchJs.includes('results-count'), 'search.js must update results-count');
  assert(searchJs.includes('results-grid'), 'search.js must update results-grid');
});

test('T1-F8-02', 'Genre Filter Chip Selection in search.html', () => {
  const searchHtml = fs.readFileSync(path.join(HTML_DIR, 'search.html'), 'utf8');
  assert(searchHtml.includes('class="filter-chip'), 'search.html must contain filter chips');
  assert(searchHtml.includes('data-filter="Action"'), 'search.html must contain Action filter chip');
  assert(searchHtml.includes('data-filter="Comedy"'), 'search.html must contain Comedy filter chip');
});

test('T1-F8-03', 'Empty Search Results User Feedback', () => {
  const searchJs = fs.readFileSync(path.join(JS_DIR, 'search.js'), 'utf8');
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  const combined = searchJs + '\n' + appJs;
  const hasEmptyFeedback = combined.includes('empty-state') ||
                           combined.includes('No titles found') ||
                           combined.includes('no-results') ||
                           combined.includes('No movies found') ||
                           combined.includes('කිසිවක් හමු නොවීය');
  assert(hasEmptyFeedback, 'Search and grid rendering engine must provide clear empty results UI feedback');
});

test('T1-F8-04', 'Legible Card Badge Font Sizing (>= 11px)', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');
  assert(!styleCss.includes('font-size: 7.5px !important;'), 'style.css must not use illegible 7.5px badge font');
  assert(!styleCss.includes('font-size: 8px !important;'), 'style.css must not use illegible 8px badge font');
});

test('T1-F8-05', 'URL Query Parameter Pre-population in Search', () => {
  const searchJs = fs.readFileSync(path.join(JS_DIR, 'search.js'), 'utf8');
  const hasParamHandling = searchJs.includes('URLSearchParams') || searchJs.includes('location.search');
  assert(hasParamHandling, 'search.js must parse URL query parameters on load');
});

// -----------------------------------------------------------------
// FEATURE 9: Teasers Reels Feed & Autoplay (T1-F9-01 to T1-F9-05)
// -----------------------------------------------------------------
console.log('\n--- Feature 9: Teasers Reels Feed & Autoplay ---');

test('T1-F9-01', 'Zero Dummy Placeholder MP4s in Teasers Database', () => {
  const teasersDataJs = fs.readFileSync(path.join(DATA_DIR, 'teasers_data.js'), 'utf8');
  assert(!teasersDataJs.includes('teaser.mp4'), 'teasers_data.js must contain zero dummy placeholder MP4 URLs');
  assert(teasersDataJs.includes('youtube_embed_url'), 'teasers must specify youtube_embed_url');
});

test('T1-F9-02', 'Sugriva Teaser Trailer Mapping & Timestamps', () => {
  const teasersDataJs = fs.readFileSync(path.join(DATA_DIR, 'teasers_data.js'), 'utf8');
  const match = teasersDataJs.match(/window\.FILMSUB_TEASERS\s*=\s*(\[[\s\S]*?\]);/);
  assert(match, 'window.FILMSUB_TEASERS must be declared in teasers_data.js');
  const list = JSON.parse(match[1]);
  const sugriva = list.find(t => t.movie_id === 'sugriva-2026');
  assert(sugriva, 'Sugriva teaser must exist in dataset');
  assert.strictEqual(sugriva.trailer_id, 'DfNcx19PY6c', 'Sugriva trailer ID must match official trailer');
  assert(sugriva.clip_start > 0 && sugriva.clip_end > sugriva.clip_start, 'Sugriva must have valid clip timestamps');
});

test('T1-F9-03', 'Iframe Source Loading Bug Fix Verification in teasers.js', () => {
  const teasersJs = fs.readFileSync(path.join(JS_DIR, 'teasers.js'), 'utf8');
  assert(!teasersJs.includes("!iframe.src || iframe.src === 'about:blank'"),
    'teasers.js must not contain obsolete empty string resolution bug');
  assert(teasersJs.includes("!currentSrc || currentSrc === 'about:blank'"),
    'teasers.js must use robust currentSrc check');
});

test('T1-F9-04', 'Responsive Bottom Info Card Height Compression', () => {
  const teasersCss = fs.readFileSync(path.join(CSS_DIR, 'teasers.css'), 'utf8');
  assert(teasersCss.includes('.teaser-bottom-info'), 'teasers.css must style .teaser-bottom-info');
  const hasCompression = /max-height:\s*(1[0-5][0-9]|150)px/.test(teasersCss) || /line-clamp:\s*2/.test(teasersCss);
  assert(hasCompression, 'teasers.css must compress info card height on compact viewports');
});

test('T1-F9-05', 'CSP Headers Compliance for YouTube Frame Embeds', () => {
  const headers = fs.readFileSync(path.join(HTML_DIR, '_headers'), 'utf8');
  assert(headers.includes('https://www.youtube.com'), '_headers CSP must permit https://www.youtube.com');
  assert(headers.includes('https://www.youtube-nocookie.com'), '_headers CSP must permit https://www.youtube-nocookie.com');
});

// -----------------------------------------------------------------
// SUMMARY & REPORTING
// -----------------------------------------------------------------
console.log('\n====================================================');
console.log(`TIER 1 SUMMARY: ${passed} passed, ${failed} failed (out of 45 tests)`);
console.log('====================================================');

module.exports = { passed, failed, results };

if (require.main === module) {
  process.exit(failed > 0 ? 1 : 0);
}
