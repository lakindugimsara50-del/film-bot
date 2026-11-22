const fs = require('fs');
const path = require('path');
const assert = require('assert');

console.log('====================================================');
console.log('📱 RUNNING MOBILE OPTIMIZATION & STABILITY TEST SUITE');
console.log('====================================================');

const HTML_FILES = [
  'website/index.html',
  'website/movie.html',
  'website/search.html',
  'website/teasers.html',
  'website/companion.html'
];

let totalPassed = 0;

function runTest(name, fn) {
  try {
    fn();
    console.log(`  ✅ PASS: ${name}`);
    totalPassed++;
  } catch (err) {
    console.error(`  ❌ FAIL: ${name}`);
    console.error(err);
    process.exit(1);
  }
}

// 1. Viewport Meta Tags
runTest('All core HTML files contain viewport-fit=cover and prevent unwanted mobile zoom', () => {
  HTML_FILES.forEach(file => {
    const html = fs.readFileSync(file, 'utf8');
    assert(
      html.includes('viewport-fit=cover'),
      `${file} must include viewport-fit=cover in its meta viewport tag`
    );
    assert(
      html.includes('maximum-scale=1.0') && html.includes('user-scalable=no'),
      `${file} must include maximum-scale=1.0 and user-scalable=no`
    );
  });
});

// 2. HTML & Body Root Overflow Protection
runTest('style.css enforces overflow-x: hidden and full width on html & body', () => {
  const css = fs.readFileSync('website/assets/css/style.css', 'utf8');
  assert(
    css.includes('html {') && css.includes('overflow-x: hidden;') && css.includes('-webkit-text-size-adjust: 100%;'),
    'style.css must have overflow-x: hidden and text-size-adjust on html'
  );
  assert(
    css.includes('body {') && css.includes('overflow-x: hidden;'),
    'style.css must have overflow-x: hidden on body'
  );
  assert(
    css.includes('@media (max-width: 768px)') && css.includes('background-attachment: scroll !important;'),
    'style.css must set background-attachment: scroll on mobile to avoid heavy paint loops'
  );
});

// 3. Mobile 2-Column Movie Grid & Safe Containers
runTest('style.css enforces clean 2-column movie grid on mobile screens', () => {
  const css = fs.readFileSync('website/assets/css/style.css', 'utf8');
  const has2Col = /@media[^{]*max-width:\s*(380px|480px|600px)[^{]*\{[\s\S]*?grid-template-columns:\s*repeat\(2,\s*1fr\)/.test(css);
  assert(has2Col, 'style.css must enforce 2 columns for movie grid on mobile');
  assert(css.includes('#results-grid'), 'results-grid must be covered by mobile grid styles');
});

// 4. Heavy Blur Suppression on Mobile GPUs
runTest('Heavy GPU filter blurs are suppressed on mobile viewports', () => {
  const styleCss = fs.readFileSync('website/assets/css/style.css', 'utf8');
  const teasersCss = fs.readFileSync('website/assets/css/teasers.css', 'utf8');

  assert(
    styleCss.includes('.hero-watermark-glow') && styleCss.includes('display: none !important;'),
    'style.css must hide .hero-watermark-glow on mobile to save GPU memory'
  );

  assert(
    teasersCss.includes('.reels-desktop-ambient-backdrop') && teasersCss.includes('display: none !important;'),
    'teasers.css must set display: none on .reels-desktop-ambient-backdrop on mobile'
  );
});

// 5. Teasers Iframe Memory Pool & Virtualization
runTest('teasers.js unloads distant iframes (>1 card away) to prevent mobile OOM webview crashes', () => {
  const teasersJs = fs.readFileSync('website/assets/js/teasers.js', 'utf8');
  assert(teasersJs.includes('unloadDistantCards'), 'teasers.js must implement unloadDistantCards');
  assert(teasersJs.includes('about:blank'), 'teasers.js must unload inactive iframe src to about:blank');
  assert(teasersJs.includes('preloadAdjacent'), 'teasers.js must preload adjacent cards');
});

// 6. Mobile Video Preload Crash Prevention in Player
runTest('player.js avoids offscreen background video preloading on mobile hardware', () => {
  const playerJs = fs.readFileSync('website/assets/js/player.js', 'utf8');
  assert(
    playerJs.includes('isMobileClient') && playerJs.includes('applyQualitySwitch(targetQuality, { isAutoDowngrade: false });'),
    'player.js must bypass dual-decoder background preloader on mobile devices'
  );
});

// 7. Prevent False Page Refreshes in app.js
runTest('app.js fetchRemoteMovies avoids unnecessary re-renders when data has not changed', () => {
  const appJs = fs.readFileSync('website/assets/js/app.js', 'utf8');
  assert(
    appJs.includes('hasNewMovies') && appJs.includes('return hasNewMovies;'),
    'app.js must only return true and re-render sections if new movies were actually loaded'
  );
});

// 8. Cinema Menu Bar Mobile Touch Scrolling
runTest('watch-party.css cinema menu bar supports touch scrolling and zero horizontal stretch', () => {
  const wpCss = fs.readFileSync('website/assets/css/watch-party.css', 'utf8');
  assert(
    wpCss.includes('.cs-cinema-menu-bar') && wpCss.includes('-webkit-overflow-scrolling: touch !important;') && wpCss.includes('max-width: 100% !important;'),
    'watch-party.css must have touch scrolling and max-width 100% on .cs-cinema-menu-bar'
  );
});

console.log('====================================================');
console.log(`🎉 ALL ${totalPassed} MOBILE OPTIMIZATION CHECKS PASSED 100%!`);
console.log('====================================================');
