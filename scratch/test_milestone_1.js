const fs = require('fs');
const path = require('path');
const assert = require('assert');

console.log('--- STARTING MILESTONE 1 DESIGN SYSTEM AUDIT ---');

const projectRoot = path.join(__dirname, '..');
const tokensPath = path.join(projectRoot, 'website/assets/css/tokens.css');

// 1. Verify tokens.css existence
assert(fs.existsSync(tokensPath), 'FAIL: tokens.css does not exist');
const tokensCss = fs.readFileSync(tokensPath, 'utf8');
console.log('PASS 1: website/assets/css/tokens.css exists');

// 2. Verify 4-tier dark surfaces
const surfaceTokens = [
  '--fz-canvas: #0a0b0e;',
  '--fz-surface-ground: #101217;',
  '--fz-surface-card: #161820;',
  '--fz-surface-raised: #1e212c;',
  '--fz-surface-overlay: #262a38;',
  '--fz-surface-glass: rgba(16, 18, 24, 0.82);'
];
for (const token of surfaceTokens) {
  const name = token.split(':')[0].trim();
  assert(tokensCss.includes(name), `FAIL: missing surface token ${name}`);
}
console.log('PASS 2: 4-tier dark surfaces verified');

// 3. Verify borders & separators
const borderTokens = [
  '--fz-border-subtle: rgba(255, 255, 255, 0.08);',
  '--fz-border-default: rgba(255, 255, 255, 0.14);',
  '--fz-border-hover: rgba(255, 255, 255, 0.28);',
  '--fz-border-active: rgba(229, 9, 20, 0.60);'
];
for (const token of borderTokens) {
  const name = token.split(':')[0].trim();
  assert(tokensCss.includes(name), `FAIL: missing border token ${name}`);
}
console.log('PASS 3: borders and separators verified');

// 4. Verify brand & accent palette
const accentTokens = [
  '--fz-accent',
  '--fz-accent-hover',
  '--fz-accent-subtle',
  '--fz-accent-glow',
  '--fz-gold',
  '--fz-gold-subtle',
  '--fz-blue',
  '--fz-green'
];
for (const token of accentTokens) {
  assert(tokensCss.includes(token), `FAIL: missing accent token ${token}`);
}
console.log('PASS 4: brand and accent palette verified');

// 5. Verify typography hierarchy & modular scale
const typographyTokens = [
  '--fz-font-sans',
  '--fz-font-sinhala',
  '--fz-font-display',
  '--fz-text-xs',
  '--fz-text-sm',
  '--fz-text-base',
  '--fz-text-lg',
  '--fz-text-xl',
  '--fz-text-2xl',
  '--fz-text-3xl',
  '--fz-text-4xl'
];
for (const token of typographyTokens) {
  assert(tokensCss.includes(token), `FAIL: missing typography token ${token}`);
}
console.log('PASS 5: typography hierarchy and modular scale verified');

// 6. Verify font weights & text colors
const textTokens = [
  '--fz-weight-regular',
  '--fz-weight-medium',
  '--fz-weight-semibold',
  '--fz-weight-bold',
  '--fz-weight-black',
  '--fz-text-primary',
  '--fz-text-secondary',
  '--fz-text-muted',
  '--fz-text-dim'
];
for (const token of textTokens) {
  assert(tokensCss.includes(token), `FAIL: missing text token ${token}`);
}
console.log('PASS 6: font weights and text colors verified');

// 7. Verify shadows, radiuses, z-indices, motion, layout
const layoutTokens = [
  '--fz-shadow-sm',
  '--fz-shadow-md',
  '--fz-shadow-lg',
  '--fz-shadow-modal',
  '--fz-radius-sm',
  '--fz-radius-md',
  '--fz-radius-lg',
  '--fz-radius-xl',
  '--fz-radius-full',
  '--fz-z-base',
  '--fz-z-card',
  '--fz-z-header',
  '--fz-z-dropdown',
  '--fz-z-dock',
  '--fz-z-overlay',
  '--fz-z-modal',
  '--fz-z-toast',
  '--fz-ease-out',
  '--fz-transition-fast',
  '--fz-transition-norm',
  '--fz-transition-slow',
  '--fz-header-height',
  '--fz-container-max'
];
for (const token of layoutTokens) {
  assert(tokensCss.includes(token), `FAIL: missing layout token ${token}`);
}
console.log('PASS 7: shadows, radiuses, z-indices, motion, layout verified');

// 8. Verify backward-compatible alias mappings
const aliasTokens = [
  '--bg: var(--fz-canvas);',
  '--bg-surface: var(--fz-surface-ground);',
  '--bg-card: var(--fz-surface-card);',
  '--accent: var(--fz-accent);',
  '--accent-hover: var(--fz-accent-hover);',
  '--text: var(--fz-text-primary);',
  '--text2: var(--fz-text-secondary);',
  '--text3: var(--fz-text-muted);',
  '--gold: var(--fz-gold);',
  '--header-h: var(--fz-header-height);'
];
for (const token of aliasTokens) {
  const name = token.split(':')[0].trim();
  assert(tokensCss.includes(name), `FAIL: missing alias token ${name}`);
}
console.log('PASS 8: backward-compatible aliases verified');

// 9. Verify 5 HTML files have standardized fonts and tokens.css
const targetPages = ['index.html', 'movie.html', 'search.html', 'teasers.html', 'companion.html'];
const fontUrlSubstring = 'family=Noto+Sans+Sinhala:wght@400;500;600;700;800&family=Outfit:wght@400;600;700&family=Roboto:wght@300;400;500;700;900&display=swap';

for (const page of targetPages) {
  const filePath = path.join(projectRoot, 'website', page);
  const html = fs.readFileSync(filePath, 'utf8');

  // Check tokens.css is linked
  assert(html.includes('assets/css/tokens.css'), `FAIL: ${page} missing tokens.css`);

  // Check standardized font link
  assert(html.includes(fontUrlSubstring), `FAIL: ${page} missing standardized Google fonts`);

  // Check preconnect tags
  assert(html.includes('https://fonts.googleapis.com'), `FAIL: ${page} missing preconnect to fonts.googleapis.com`);
  assert(html.includes('https://fonts.gstatic.com'), `FAIL: ${page} missing preconnect to fonts.gstatic.com`);

  // Ensure no duplicate font link
  const fontMatches = html.match(/fonts\.googleapis\.com\/css2/g) || [];
  assert.strictEqual(fontMatches.length, 1, `FAIL: ${page} has ${fontMatches.length} font links (expected exactly 1)`);
}
console.log('PASS 9: all 5 HTML pages standardized with font links and tokens.css, zero duplicates');

console.log('--- ALL MILESTONE 1 AUDIT CHECKS PASSED 100% ---');
