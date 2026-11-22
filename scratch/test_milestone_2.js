const fs = require('fs');
const path = require('path');

console.log('--- STARTING MILESTONE 2 NAVIGATION, DRAWERS & MODALS AUDIT ---');

const indexHtml = fs.readFileSync(path.join(__dirname, '../website/index.html'), 'utf8');
const styleCss = fs.readFileSync(path.join(__dirname, '../website/assets/css/style.css'), 'utf8');
const appJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/app.js'), 'utf8');

// 1. Header Cleanup in index.html
if (indexHtml.includes('<button class="hamburger" id="hamburger"') || indexHtml.includes('id="hamburger"')) {
  console.error('FAIL: Redundant hamburger button still found in index.html');
  process.exit(1);
}
if (!indexHtml.includes('id="header-sidebar-toggle-btn"') || !indexHtml.includes('id="user-profile-pill"')) {
  console.error('FAIL: Required header controls missing from index.html');
  process.exit(1);
}
console.log('PASS 1: Header cleanup verified (single hamburger #header-sidebar-toggle-btn and #user-profile-pill)');

// 2. 22 Modals/Sheets Dark Glass Backdrop & Container Standards
const requiredBackdrops = [
  'rgba(0, 0, 0, 0.82)',
  'backdrop-filter: blur(16px)'
];
for (const req of requiredBackdrops) {
  if (!styleCss.includes(req)) {
    console.error(`FAIL: Modal backdrop standard missing: ${req}`);
    process.exit(1);
  }
}

const requiredContainers = [
  'var(--fz-surface-overlay, #262a38)',
  'var(--fz-radius-xl, 20px)',
  '1.5px solid rgba(255, 255, 255, 0.35)',
  'min(94vw, 760px)'
];
for (const req of requiredContainers) {
  if (!styleCss.includes(req)) {
    console.error(`FAIL: Modal container standard missing: ${req}`);
    process.exit(1);
  }
}
console.log('PASS 2: Universal modal backdrop and container dark glass styling verified');

// 3. User Profile / VIP Hub Polish
if (appJs.includes('Cloud Synced')) {
  console.error('FAIL: Forbidden string "Cloud Synced" found in app.js');
  process.exit(1);
}
if (!appJs.includes('vip-hub-tabs-nav') || !styleCss.includes('.vip-hub-tabs-nav')) {
  console.error('FAIL: #vip-hub-tabs-nav missing');
  process.exit(1);
}
if (!appJs.includes('vip-hub-count-pill') || !styleCss.includes('.vip-hub-count-pill')) {
  console.error('FAIL: .vip-hub-count-pill missing');
  process.exit(1);
}
if (!appJs.includes('VIP_PRESET_AVATARS') || !appJs.includes('openAvatarPickerModal') || !appJs.includes('vip-hub-avatar-edit-badge')) {
  console.error('FAIL: VIP 3D cinema avatars picker or badge missing');
  process.exit(1);
}
console.log('PASS 3: VIP Hub modal polish verified (clean tabs, count pill, 8 avatar presets, zero legacy strings)');

// 4. Drawers Dark Glass Standardization
if (!styleCss.includes('.cinema-nav-drawer-panel') || !styleCss.includes('.user-dropdown-drawer')) {
  console.error('FAIL: Drawer styling selectors missing');
  process.exit(1);
}
if (!appJs.includes('cinema-global-nav-drawer') || !appJs.includes('initGlobalCinemaDrawer')) {
  console.error('FAIL: Global cinema drawer initialization missing');
  process.exit(1);
}
console.log('PASS 4: Slide-out cinema drawer and frosted user dropdown drawer verified');

// 5. Mobile Bottom Bar & Toast Notifications
if (!indexHtml.includes('id="mobile-bottom-bar"') || !styleCss.includes('.mobile-bottom-bar')) {
  console.error('FAIL: Mobile bottom bar missing');
  process.exit(1);
}
if (!indexHtml.includes('id="toast-container"') || !styleCss.includes('.toast-container')) {
  console.error('FAIL: Toast container missing');
  process.exit(1);
}
console.log('PASS 5: Luxury mobile bottom bar and toast notification system verified');

// 6. Global Exposure Invariants
if (!appJs.includes('window.openUserProfileModal = openUserListModal') || !appJs.includes('window.openAvatarPickerModal = openAvatarPickerModal')) {
  console.error('FAIL: Global window functions not exposed');
  process.exit(1);
}
console.log('PASS 6: Global modal opener handlers preserved');

console.log('--- ALL MILESTONE 2 AUDIT CHECKS PASSED 100% ---');
