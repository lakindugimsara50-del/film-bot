/**
 * Deep Automated Verification Suite for FilmZilla VIP 7 Killer Features Suite:
 * 1. Family / Parent Safe Mode (Instant Censor / Skip)
 * 2. Interactive Local P2P Sharing (Wi-Fi Data Sharing)
 * 3. Mobile 4D Haptic Feedback (<70Hz Web Audio Filter)
 * 4. Long-Distance Couple Mode (Touch Glow, Hug, Whisper)
 * 5. In-Party Live Predictions & Betting Arena (Popcorn Coins, Leaderboard)
 * 6. Virtual Popcorn & Reaction Bombs (Splatters, Explosions, Synthesizer)
 * 7. Phone as Second Screen / Dual-Screen Companion (QR Scan, Remote Control)
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

console.log('====================================================');
console.log('🚀 RUNNING DEEP VERIFICATION SUITE FOR 7 VIP FEATURES');
console.log('====================================================\n');

let passCount = 0;
let failCount = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`✅ PASS: ${name}`);
    passCount++;
  } catch (err) {
    console.error(`❌ FAIL: ${name}`);
    console.error(`   Error: ${err.message}\n`);
    failCount++;
  }
}

// 1. Check Files Existence
test('1. All required project files exist', () => {
  const files = [
    'website/movie.html',
    'website/companion.html',
    'website/assets/js/player.js',
    'website/assets/js/watch-party.js',
    'website/assets/css/watch-party.css',
    'website/data/censor_cues.json',
    'website/data/censor_cues_data.js'
  ];
  for (const f of files) {
    const fullPath = path.join(__dirname, '..', f);
    assert(fs.existsSync(fullPath), `File missing: ${f}`);
  }
});

// 2. Family Safe Mode (Feature 1)
test('2. Family Safe Mode - JSON & JS Cues Database integrity', () => {
  const jsonPath = path.join(__dirname, '../website/data/censor_cues.json');
  const jsPath = path.join(__dirname, '../website/data/censor_cues_data.js');
  const json = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const jsContent = fs.readFileSync(jsPath, 'utf8');

  assert(json.categories && json.categories.includes('nudity'), 'Categories should include nudity');
  assert(json.movies['sugriva-2026'], 'Should contain sugriva-2026 cues');
  assert(json.movies['default'], 'Should contain fallback default cues');

  const sugrivaCues = json.movies['sugriva-2026'];
  assert(sugrivaCues.some(c => c.type === 'gore' && c.start > 0), 'Sugriva should have gore cue');
  assert(sugrivaCues.some(c => c.type === 'kissing'), 'Sugriva should have kissing cue');
  assert(sugrivaCues.some(c => c.type === 'adult'), 'Sugriva should have adult cue');

  assert(jsContent.includes('window.FILMSUB_CENSOR_CUES'), 'censor_cues_data.js must export global FILMSUB_CENSOR_CUES');
});

test('3. Family Safe Mode - Player Engine & DOM Integration', () => {
  const playerJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
  const movieHtml = fs.readFileSync(path.join(__dirname, '../website/movie.html'), 'utf8');

  assert(playerJs.includes('window.FilmZillaSafeMode'), 'player.js must define FilmZillaSafeMode');
  assert(playerJs.includes('applyBlurAndMute'), 'FilmZillaSafeMode must have applyBlurAndMute');
  assert(playerJs.includes('liftCensor'), 'FilmZillaSafeMode must have liftCensor');
  assert(playerJs.includes('openSettingsModal'), 'FilmZillaSafeMode must have openSettingsModal');
  assert(playerJs.includes('cs-safe-blurred'), 'FilmZillaSafeMode must toggle cs-safe-blurred class');
  assert(playerJs.includes('cs-safe-censor-overlay'), 'FilmZillaSafeMode must mount cs-safe-censor-overlay');

  assert(movieHtml.includes('id="btn-toggle-safe-mode"'), 'movie.html must include Safe Mode button');
  assert(movieHtml.includes('id="cs-safe-badge"'), 'movie.html must include Safe Mode status badge');
  assert(movieHtml.includes('censor_cues_data.js'), 'movie.html must load censor_cues_data.js');
});

// 3. Local Wi-Fi P2P Mesh Sharing (Feature 2)
test('4. Local Wi-Fi P2P Data Sharing - Mesh Engine & UI Badge', () => {
  const playerJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
  const movieHtml = fs.readFileSync(path.join(__dirname, '../website/movie.html'), 'utf8');

  assert(playerJs.includes('window.FilmZillaP2P'), 'player.js must define FilmZillaP2P');
  assert(playerJs.includes('fz_wifi_p2p_mesh'), 'FilmZillaP2P must use fz_wifi_p2p_mesh BroadcastChannel');
  assert(playerJs.includes('savedBytes'), 'FilmZillaP2P must track savedBytes');
  assert(playerJs.includes('openDashboardModal'), 'FilmZillaP2P must have openDashboardModal');

  assert(movieHtml.includes('id="cs-p2p-pill"'), 'movie.html must include P2P pill element');
  assert(movieHtml.includes('id="cs-p2p-saved-val"'), 'movie.html must include P2P saved value element');
});

// 4. Mobile 4D Haptic Feedback (Feature 3)
test('5. Mobile 4D Haptic Feedback - <70Hz Filter & Distinct Vibration Patterns', () => {
  const playerJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
  const movieHtml = fs.readFileSync(path.join(__dirname, '../website/movie.html'), 'utf8');

  assert(playerJs.includes('window.FilmZilla4DHaptic'), 'player.js must define FilmZilla4DHaptic');
  assert(playerJs.includes('createBiquadFilter'), 'Must create Web Audio BiquadFilter');
  assert(playerJs.includes('frequency.value = 70'), 'Low-Pass filter must be set to 70Hz for pure sub-bass');
  assert(playerJs.includes('triggerHapticPattern'), 'Must have triggerHapticPattern method');
  assert(playerJs.includes('navigator.vibrate'), 'Must call navigator.vibrate');
  assert(playerJs.includes('haptic-impact-pulse'), 'Must trigger visual tactile pulse class');

  assert(movieHtml.includes('id="btn-toggle-haptics"'), 'movie.html must include 4D Haptic button');
  assert(movieHtml.includes('id="cs-haptic-badge"'), 'movie.html must include 4D Haptic badge');
});

// 5. Long-Distance Couple Mode (Feature 4)
test('6. Long-Distance Couple Mode - Synchronized Touch Glow, Hugs, and Whisper Chat', () => {
  const wpJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');
  const wpCss = fs.readFileSync(path.join(__dirname, '../website/assets/css/watch-party.css'), 'utf8');

  assert(wpJs.includes('this.isCoupleMode'), 'watch-party.js must track isCoupleMode state');
  assert(wpJs.includes('initCoupleTouchListener'), 'watch-party.js must have initCoupleTouchListener');
  assert(wpJs.includes('renderCoupleTouchGlow'), 'watch-party.js must have renderCoupleTouchGlow');
  assert(wpJs.includes('triggerCoupleHug'), 'watch-party.js must have triggerCoupleHug');
  assert(wpJs.includes('showCoupleHugOverlay'), 'watch-party.js must have showCoupleHugOverlay');
  assert(wpJs.includes('isWhisper'), 'watch-party.js must support private whisper chat messages');

  assert(wpCss.includes('.couple-touch-glow'), 'watch-party.css must style couple-touch-glow');
  assert(wpCss.includes('.couple-hug-overlay'), 'watch-party.css must style couple-hug-overlay');
  assert(wpCss.includes('.wp-msg-item.is-whisper') || wpCss.includes('.is-whisper'), 'watch-party.css must style whisper messages');
});

// 6. In-Party Live Predictions & Betting Arena (Feature 5)
test('7. In-Party Live Predictions & Betting Arena - Gamified Popcorn Bets & Leaderboard', () => {
  const wpJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');
  const wpCss = fs.readFileSync(path.join(__dirname, '../website/assets/css/watch-party.css'), 'utf8');

  assert(wpJs.includes('openPredictionManager'), 'watch-party.js must have openPredictionManager');
  assert(wpJs.includes('startPrediction'), 'watch-party.js must have startPrediction');
  assert(wpJs.includes('placePredictionBet'), 'watch-party.js must have placePredictionBet');
  assert(wpJs.includes('resolvePrediction'), 'watch-party.js must have resolvePrediction');
  assert(wpJs.includes('popcornCoins'), 'watch-party.js must track popcornCoins wallet balance');
  assert(wpJs.includes('cs-prediction-card'), 'watch-party.js must render cs-prediction-card overlay');
  assert(wpJs.includes('openPredictionLeaderboard'), 'watch-party.js must have leaderboard modal');

  assert(wpCss.includes('.cs-prediction-card'), 'watch-party.css must style cs-prediction-card');
  assert(wpCss.includes('.cs-prediction-options'), 'watch-party.css must style cs-prediction-options');
});

// 7. Virtual Popcorn & Reaction Bombs (Feature 6)
test('8. Virtual Popcorn & Reaction Bombs - Splatters, Explosions & Synthesizer', () => {
  const wpJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');
  const wpCss = fs.readFileSync(path.join(__dirname, '../website/assets/css/watch-party.css'), 'utf8');

  assert(wpJs.includes('launchReactionBomb'), 'watch-party.js must have launchReactionBomb');
  assert(wpJs.includes('playSynthesizedSound'), 'watch-party.js must have Web Audio playSynthesizedSound');
  assert(wpJs.includes('bomb-popcorn-kernel'), 'watch-party.js must spawn popcorn kernels');
  assert(wpJs.includes('bomb-tomato-splat'), 'watch-party.js must spawn tomato splat');
  assert(wpJs.includes('cs-screen-shake-intense'), 'watch-party.js must trigger violent screen shake');

  assert(wpCss.includes('.wp-reaction-bombs-tray'), 'watch-party.css must style bombs tray');
  assert(wpCss.includes('.bomb-tomato-splat'), 'watch-party.css must style tomato splat');
  assert(wpCss.includes('.cs-screen-shake-intense'), 'watch-party.css must style intense screen shake');
});

// 8. Phone as Second Screen / Companion Remote (Feature 7)
test('9. Phone as Second Screen Remote - companion.html & QR Pairing Integration', () => {
  const compHtml = fs.readFileSync(path.join(__dirname, '../website/companion.html'), 'utf8');
  const playerJs = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
  const movieHtml = fs.readFileSync(path.join(__dirname, '../website/movie.html'), 'utf8');

  assert(compHtml.includes('FilmZilla <span>REMOTE</span>'), 'companion.html must have luxury header');
  assert(compHtml.includes('remote-seek'), 'companion.html must have scrubbable timeline slider');
  assert(compHtml.includes('btn-play-pause-hero'), 'companion.html must have big tactile play/pause button');
  assert(compHtml.includes('btn-remote-bomb'), 'companion.html must have reaction bombs launcher');
  assert(compHtml.includes('fz_companion_sync'), 'companion.html must use fz_companion_sync BroadcastChannel');
  assert(compHtml.includes('remote-hug-btn'), 'companion.html must have hold for hug gesture');

  assert(playerJs.includes('window.FilmZillaCompanion'), 'player.js must define FilmZillaCompanion');
  assert(playerJs.includes('openPairModal'), 'FilmZillaCompanion must have openPairModal with QR code');
  assert(playerJs.includes('broadcastHaptic'), 'FilmZillaCompanion must broadcast haptic pulses to phone');

  assert(movieHtml.includes('id="btn-open-companion"'), 'movie.html must include Phone Remote button in cinema menu bar');
});

console.log('\n====================================================');
console.log(`SUMMARY: ${passCount} passed, ${failCount} failed`);
console.log('====================================================');

if (failCount > 0) {
  process.exit(1);
} else {
  console.log('🎉 ALL 9 VERIFICATION CHECKS PASSED PERFECTLY!');
  process.exit(0);
}
