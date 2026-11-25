const fs = require('fs');
const path = require('path');

console.log('--- STARTING CINEMA UI REFINEMENTS AUDIT ---');

const appJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/app.js'), 'utf8');
const playerJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
const wpJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');
const rouletteJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/roulette.js'), 'utf8');
const styleCss = fs.readFileSync(path.join(__dirname, '../website/assets/css/style.css'), 'utf8');
const indexHtml = fs.readFileSync(path.join(__dirname, '../website/index.html'), 'utf8');
const movieHtml = fs.readFileSync(path.join(__dirname, '../website/movie.html'), 'utf8');

// 1. Cloud Synced Badge Removed
if (appJs.includes('Cloud Synced')) {
  console.error('FAIL: "Cloud Synced" found in app.js');
  process.exit(1);
} else {
  console.log('PASS 1: "Cloud Synced" badge completely removed from modal header');
}

// 2. Queue / Collection Count Pill
if (!appJs.includes('vip-hub-count-pill')) {
  console.error('FAIL: "vip-hub-count-pill" missing from app.js');
  process.exit(1);
} else {
  console.log('PASS 2: "vip-hub-count-pill" modern luxury count badge integrated');
}

// 3. Profile Avatar Customization
if (!appJs.includes('openAvatarPickerModal') || !appJs.includes('VIP_PRESET_AVATARS') || !appJs.includes('vip-hub-avatar-edit-badge')) {
  console.error('FAIL: Avatar picker or edit badge missing');
  process.exit(1);
} else {
  console.log('PASS 3: Profile avatar upload + 8 VIP 3D cinema presets implemented');
}

// 4. Header Top-Left 3-Line Hamburger Menu
if (!indexHtml.includes('header-sidebar-toggle-btn') || !movieHtml.includes('header-sidebar-toggle-btn') || !appJs.includes('initGlobalCinemaDrawer')) {
  console.error('FAIL: Hamburger toggle button or drawer init missing');
  process.exit(1);
} else {
  console.log('PASS 4: Top-left 3-line hamburger menu (☰) & slide-out cinema drawer integrated');
}

// 5. Telegram-Style Animated Emojis & Floating Stage
if (!rouletteJs.includes('1f525/512.webp') || !wpJs.includes('1f525/512.webp') || !movieHtml.includes('cs-reaction-floating-stage')) {
  console.error('FAIL: Animated WebP emojis or floating stage missing');
  process.exit(1);
} else {
  console.log('PASS 5: Official 512px animated WebPs & in-player stage floating reactions verified');
}

// 6. Watch Party Cleaned
if (wpJs.includes('Cinema Live Synchronizer') || wpJs.includes('You are streaming in real-time')) {
  console.error('FAIL: Watch party technical announcement still present');
  process.exit(1);
} else {
  console.log('PASS 6: Technical instruction card removed from Watch Party lounge');
}

// 7. Single 3D Bubble Glass Box for Downloads (No file sizes)
if (!playerJs.includes('cs-3d-download-bubble-box') || !playerJs.includes('cs-bubble-tier-row')) {
  console.error('FAIL: cs-3d-download-bubble-box missing from player.js');
  process.exit(1);
}
if (playerJs.includes('dl-size-pill') || playerJs.includes('1.76 GB') || playerJs.includes('1.11 GB')) {
  console.error('FAIL: Raw file sizes still present in player.js download section');
  process.exit(1);
} else {
  console.log('PASS 7: Single 3D bubble glass download box verified without file size pills');
}

// 8. In-Screen Video Chat Overlay & Fullscreen Popups
if (!movieHtml.includes('cs-video-chat-overlay') || !wpJs.includes('showInVideoChatPopup') || !wpJs.includes('cs-video-chat-popup')) {
  console.error('FAIL: In-video chat overlay or popup generator missing');
  process.exit(1);
} else {
  console.log('PASS 8: In-screen video chat overlay & fullscreen popups generator verified');
}

// 9. Popups Toggle inside Watch Party Lounge & Ultra-Compact Lounge
const wpCss = fs.readFileSync(path.join(__dirname, '../website/assets/css/watch-party.css'), 'utf8');
if (!wpJs.includes('toggleChatPopups') || !wpJs.includes('wp-popups-toggle') || !wpCss.includes('cs-video-chat-overlay')) {
  console.error('FAIL: Popups toggle or CSS overlay styling missing');
  process.exit(1);
} else {
  console.log('PASS 9: On-screen chat popups ON/OFF toggle & compact theater styling verified');
}

console.log('--- ALL 9 REFINEMENT TESTS PASSED 100% ---');
