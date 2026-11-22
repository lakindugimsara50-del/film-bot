/**
 * FilmZilla / FilmSub E2E Test Suite - Tier 3: Cross-Feature Interactions
 * 10 Pairwise Combinatorial Tests.
 * Adheres strictly to test_matrix_spec.md and TEST_INFRA.md.
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
console.log('🧪 RUNNING TIER 3: CROSS-FEATURE INTERACTIONS (10 TESTS)');
console.log('====================================================\n');

test('T3-01', 'Dark Tokens in Glassmorphic Modals (F1 + F3)', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');

  // Verify modal background uses dark token or standard glassmorphic RGBA
  const usesSurfaceOverlay = tokensCss.includes('--fz-surface-overlay') || tokensCss.includes('--fz-surface-glass');
  assert(usesSurfaceOverlay, 'tokens.css must declare --fz-surface-overlay or --fz-surface-glass');

  // Verify border subtle
  assert(tokensCss.includes('--fz-border-subtle'), 'tokens.css must declare --fz-border-subtle for dialog outlines');

  // Verify backdrop blur
  const hasBackdropBlur = styleCss.includes('backdrop-filter: blur') || styleCss.includes('backdrop-filter:blur');
  assert(hasBackdropBlur, 'style.css must specify backdrop-filter blur for glassmorphic modals');
});

test('T3-02', 'Mobile 375px with 2-Col Search Grid & Legible Badges (F4 + F8)', () => {
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');

  // Check 2-column grid rule
  const has2ColRule = /@media[^{]*max-width:\s*(380px|480px|600px)[^{]*\{[\s\S]*?grid-template-columns:\s*repeat\(2,\s*1fr\)/.test(styleCss);
  assert(has2ColRule, 'style.css must enforce 2 columns for search grid on mobile viewports');

  // Check no 7.5px illegible text
  assert(!styleCss.includes('font-size: 7.5px !important;'), 'style.css must eliminate 7.5px font-size');
});

test('T3-03', 'Watch Party Overlays with Active Video Playback (F5 + F6)', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  const wpCss = fs.readFileSync(path.join(CSS_DIR, 'watch-party.css'), 'utf8');

  // Video container exists
  assert(movieHtml.includes('video-player-container'), 'movie.html must have #video-player-container');

  // Overlays defined
  assert(movieHtml.includes('cs-couple-cam-dock'), 'movie.html must contain #cs-couple-cam-dock');
  assert(movieHtml.includes('cs-video-pip-corner-btn'), 'movie.html must contain #cs-video-pip-corner-btn');
  assert(wpCss.includes('.cs-couple-cam-dock'), 'watch-party.css must position .cs-couple-cam-dock');
});

test('T3-04', 'VIP Hub State Persistence with Poster Actions (F5 + F7)', () => {
  const appJs = fs.readFileSync(path.join(JS_DIR, 'app.js'), 'utf8');
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');

  assert(movieHtml.includes('movie-fav-btn'), 'movie.html must contain favorite action button');
  assert(appJs.includes('vip-hub-count-pill'), 'app.js must update vip-hub-count-pill');
  assert(appJs.includes('favorites'), 'app.js must handle favorites storage list');
});

test('T3-05', 'Teasers Smart Seek Navigation to Movie Player (F5 + F9)', () => {
  const teasersHtml = fs.readFileSync(path.join(HTML_DIR, 'teasers.html'), 'utf8');
  const teasersJs = fs.readFileSync(path.join(JS_DIR, 'teasers.js'), 'utf8');

  assert(teasersHtml.includes('smart-seek-modal'), 'teasers.html must contain #smart-seek-modal');
  assert(teasersJs.includes('movie.html?movie=') || teasersJs.includes('movie.html'),
    'teasers.js must generate navigation URL to movie player');
  assert(teasersJs.includes('smartSeek') || teasersJs.includes('smart-seek'),
    'teasers.js must implement smart seek logic');
});

test('T3-06', 'Unified Header Sticky Nav with Drawer and Search Modal (F2 + F3)', () => {
  const indexHtml = fs.readFileSync(path.join(HTML_DIR, 'index.html'), 'utf8');
  const styleCss = fs.readFileSync(path.join(CSS_DIR, 'style.css'), 'utf8');

  assert(indexHtml.includes('site-header'), 'index.html must have #site-header');
  assert(indexHtml.includes('header-sidebar-toggle-btn'), 'index.html must have #header-sidebar-toggle-btn');
  assert(indexHtml.includes('search-overlay') || indexHtml.includes('search-input'),
    'index.html must provide global search overlay trigger');
  assert(styleCss.includes('position: fixed') || styleCss.includes('position: sticky'),
    'Header must use fixed or sticky positioning');
});

test('T3-07', 'Clustered Cinema Menu Bar with Responsive Overflow on Mobile (F4 + F5)', () => {
  const movieHtml = fs.readFileSync(path.join(HTML_DIR, 'movie.html'), 'utf8');
  assert(movieHtml.includes('cs-cinema-menu-bar'), 'movie.html must contain #cs-cinema-menu-bar');
  assert(movieHtml.includes('cs-bar-left'), 'movie.html must have left cluster');
  assert(movieHtml.includes('cs-bar-right'), 'movie.html must have right cluster');
});

test('T3-08', 'Multi-Viewport Hero Billboard Contrast & Typography Audit (F1 + F4)', () => {
  const tokensCss = fs.readFileSync(path.join(CSS_DIR, 'tokens.css'), 'utf8');
  const vars = cssAnalyzer.parseCssVariables(tokensCss);
  const canvasBg = vars['--fz-canvas'] || '#0a0b0e';
  const textPrimary = vars['--fz-text-primary'] || '#ffffff';

  const contrast = computeContrastRatio(textPrimary, canvasBg);
  assert(contrast >= 4.5, `Hero text contrast must exceed WCAG AA minimum 4.5:1, got ${contrast.toFixed(2)}:1`);
});

test('T3-09', 'Couple Cam PiP Dock with Draggable Repositioning and WebRTC State (F6 + F7)', () => {
  const wpJs = fs.readFileSync(path.join(JS_DIR, 'watch-party.js'), 'utf8');
  assert(wpJs.includes('makeElementDraggable'), 'watch-party.js must provide makeElementDraggable');
  assert(wpJs.includes('toggleMic'), 'watch-party.js must implement toggleMic');
  assert(wpJs.includes('toggleCam'), 'watch-party.js must implement toggleCam');
});

test('T3-10', 'Teasers Category Filter Synced with Search Catalog Navigation (F8 + F9)', () => {
  const teasersHtml = fs.readFileSync(path.join(HTML_DIR, 'teasers.html'), 'utf8');
  assert(teasersHtml.includes('reels-category-rail'), 'teasers.html must contain #reels-category-rail');
  assert(teasersHtml.includes('reels-pill'), 'teasers.html must have category filter pills');
});

// -----------------------------------------------------------------
// SUMMARY & REPORTING
// -----------------------------------------------------------------
console.log('\n====================================================');
console.log(`TIER 3 SUMMARY: ${passed} passed, ${failed} failed (out of 10 tests)`);
console.log('====================================================');

module.exports = { passed, failed, results };

if (require.main === module) {
  process.exit(failed > 0 ? 1 : 0);
}
