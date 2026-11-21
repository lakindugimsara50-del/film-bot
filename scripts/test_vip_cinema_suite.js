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
const vm = require('vm');

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
    console.error(`   Error: ${err.stack}\n`);
    failCount++;
  }
}

// ---------------------------------------------------------------------
// SECTION A: Static Contract & Markup Integrity
// ---------------------------------------------------------------------

test('1. All required project files exist including remote redirect', () => {
  const files = [
    'website/movie.html',
    'website/companion.html',
    'website/remote.html',
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
  assert(wpCss.includes('.wp-msg-item.is-whisper'), 'watch-party.css must style .wp-msg-item.is-whisper');
});

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

// ---------------------------------------------------------------------
// SECTION B: Deep Dynamic Runtime Simulation & Execution
// ---------------------------------------------------------------------

test('10. Safe Mode Runtime Execution: Auto-Skip & Seek-Back Replay Bug Fix', () => {
  const playerCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
  const cuesDataCode = fs.readFileSync(path.join(__dirname, '../website/data/censor_cues_data.js'), 'utf8');

  // Create mock browser sandbox
  const sandbox = {
    window: {},
    document: {
      addEventListener: () => {},
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => []
    },
    navigator: { vibrate: () => {} },
    location: { search: '', href: '', origin: 'https://filmsub.pages.dev', protocol: 'https:' },
    FilmSub: { escHtml: s => s, showToast: () => {} },
    currentSeason: 1,
    currentEpisode: 1,
    currentEffectiveQuality: '1080p',
    getMovieStreams: () => []
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);

  vm.runInContext(cuesDataCode, sandbox);
  vm.runInContext(playerCode, sandbox);

  assert(sandbox.window.FilmZillaSafeMode, 'FilmZillaSafeMode must be exported to window');

  // Mock Video.js player
  let currentTimeVal = 0;
  let mutedVal = false;
  const eventListeners = {};
  const mockVjs = {
    currentTime(t) {
      if (typeof t === 'number') currentTimeVal = t;
      return currentTimeVal;
    },
    muted(m) {
      if (typeof m === 'boolean') mutedVal = m;
      return mutedVal;
    },
    on(event, cb) {
      eventListeners[event] = cb;
    },
    trigger(event) {
      if (eventListeners[event]) eventListeners[event]();
    }
  };

  const safeMode = sandbox.window.FilmZillaSafeMode;
  safeMode.init(mockVjs, { slug: 'sugriva-2026' });
  safeMode.isEnabled = true;
  safeMode.action = 'skip';

  // Sugriva cue: c1: 45.0s to 58.0s
  currentTimeVal = 46.0;
  mockVjs.trigger('timeupdate');

  // Verify auto-skip jumped past end of cue (58.3s)
  assert(currentTimeVal >= 58.0, `Player should have auto-skipped past 58.0s, got ${currentTimeVal}`);

  // CRITICAL EDGE CASE VERIFICATION:
  // User seeks back to 10s and replays forward. Does lastSkippedCueId clear so it skips again?
  currentTimeVal = 10.0;
  mockVjs.trigger('seeking');
  mockVjs.trigger('timeupdate');
  assert.strictEqual(safeMode.lastSkippedCueId, null, 'lastSkippedCueId should be cleared after seeking outside the cue');

  // Advances to 46.0s again
  currentTimeVal = 46.0;
  mockVjs.trigger('timeupdate');
  assert(currentTimeVal >= 58.0, 'Player must auto-skip again on replay of the same scene');
});

test('11. Safe Mode Blur & Mute + Category Filtering Runtime', () => {
  const playerCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');
  const cuesDataCode = fs.readFileSync(path.join(__dirname, '../website/data/censor_cues_data.js'), 'utf8');

  const classes = new Set();
  const mockPlayerWrap = {
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c)
    },
    appendChild: () => {}
  };

  const sandbox = {
    window: {},
    document: {
      addEventListener: () => {},
      createElement: () => ({ setAttribute: () => {}, style: {}, innerHTML: '', appendChild: () => {}, classList: { add: () => {}, remove: () => {} } }),
      getElementById: (id) => (id === 'video-player-container' ? mockPlayerWrap : null),
      querySelector: () => mockPlayerWrap,
      querySelectorAll: () => []
    },
    navigator: { vibrate: () => {} },
    location: { search: '', href: '', origin: 'https://filmsub.pages.dev', protocol: 'https:' },
    FilmSub: { escHtml: s => s, showToast: () => {} },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout: () => {}
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);

  vm.runInContext(cuesDataCode, sandbox);
  vm.runInContext(playerCode, sandbox);

  let curTime = 46.0;
  let isMuted = false;
  const listeners = {};
  const mockVjs = {
    currentTime: () => curTime,
    muted: (m) => { if (typeof m === 'boolean') isMuted = m; return isMuted; },
    on: (evt, cb) => { listeners[evt] = cb; },
    trigger: (evt) => { if (listeners[evt]) listeners[evt](); }
  };

  const safeMode = sandbox.window.FilmZillaSafeMode;
  safeMode.init(mockVjs, { slug: 'sugriva-2026' });
  safeMode.isEnabled = true;
  safeMode.action = 'blur_mute';

  // 46.0s is inside cue 'c1' (gore: 45.0 to 58.0)
  mockVjs.trigger('timeupdate');
  assert.strictEqual(safeMode.isCurrentlyCensoring, true, 'Safe mode should be currently censoring');
  assert.strictEqual(isMuted, true, 'Player should be muted during censor');
  assert.strictEqual(classes.has('cs-safe-blurred'), true, 'cs-safe-blurred class should be added');

  // Advance time beyond cue
  curTime = 60.0;
  mockVjs.trigger('timeupdate');
  assert.strictEqual(safeMode.isCurrentlyCensoring, false, 'Censorship should lift past cue end');
  assert.strictEqual(isMuted, false, 'Player should be unmuted after censor lifts');
  assert.strictEqual(classes.has('cs-safe-blurred'), false, 'cs-safe-blurred class should be removed');
});

test('12. Mobile 4D Haptic Feedback: Distinct Patterns & Pulse Dispatch', () => {
  const playerCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');

  let lastVibratedPattern = null;
  const sandbox = {
    window: {},
    document: {
      addEventListener: () => {},
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => []
    },
    navigator: {
      vibrate: (p) => { lastVibratedPattern = p; }
    },
    location: { search: '', href: '' },
    FilmSub: { showToast: () => {} }
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(playerCode, sandbox);

  const haptics = sandbox.window.FilmZilla4DHaptic;
  assert(haptics, 'FilmZilla4DHaptic must exist');

  // Trigger Gunshot
  haptics.triggerHapticPattern([35, 25, 45], 'gunshot');
  assert.deepStrictEqual(lastVibratedPattern, [35, 25, 45], 'Gunshot pattern mismatch');

  // Trigger Explosion
  haptics.triggerHapticPattern([120, 40, 90, 40, 220], 'explosion');
  assert.deepStrictEqual(lastVibratedPattern, [120, 40, 90, 40, 220], 'Explosion pattern mismatch');

  // Trigger Heavy Bass
  haptics.triggerHapticPattern([80, 50, 160], 'bass');
  assert.deepStrictEqual(lastVibratedPattern, [80, 50, 160], 'Bass pattern mismatch');
});

test('13. Local Wi-Fi P2P Mesh Sharing: Incremental Tracking & State Dispatch', () => {
  const playerCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');

  let badgeVal = '';
  const sandbox = {
    window: {},
    document: {
      addEventListener: () => {},
      getElementById: (id) => (id === 'cs-p2p-saved-val' ? { textContent: '' } : null),
      querySelector: () => null,
      querySelectorAll: () => []
    },
    location: { search: '', href: '' },
    FilmSub: { showToast: () => {} }
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(playerCode, sandbox);

  const p2p = sandbox.window.FilmZillaP2P;
  assert(p2p, 'FilmZillaP2P must exist');

  const initialBytes = p2p.savedBytes;
  p2p.addSavedBytes(1048576); // +1 MB
  assert.strictEqual(p2p.savedBytes, initialBytes + 1048576, 'savedBytes should increment correctly');
});

test('14. Phone Companion Remote: Command Handling & State Reflection', () => {
  const playerCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/player.js'), 'utf8');

  let played = false;
  let paused = false;
  let currentSec = 20;

  const sandbox = {
    window: {},
    document: {
      addEventListener: () => {},
      getElementById: () => null,
      querySelector: () => null,
      querySelectorAll: () => []
    },
    location: { search: '', href: '', origin: 'https://filmsub.pages.dev', pathname: '/movie.html' },
    FilmSub: { showToast: () => {} },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout: () => {}
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(playerCode, sandbox);

  const companion = sandbox.window.FilmZillaCompanion;
  assert(companion, 'FilmZillaCompanion must exist');

  const mockVjs = {
    play: () => { played = true; },
    pause: () => { paused = true; },
    currentTime: (t) => { if (typeof t === 'number') currentSec = t; return currentSec; },
    paused: () => paused,
    duration: () => 120,
    volume: () => 1,
    muted: () => false
  };

  companion.init(mockVjs, { title: 'Dune Part 2', slug: 'dune-2' });

  // Test Play Command from phone
  companion.handleRemoteCommand({ type: 'REMOTE_CMD', action: 'PLAY' });
  assert.strictEqual(played, true, 'PLAY command should execute vjs.play()');

  // Test Pause Command from phone
  companion.handleRemoteCommand({ type: 'REMOTE_CMD', action: 'PAUSE' });
  assert.strictEqual(paused, true, 'PAUSE command should execute vjs.pause()');

  // Test Relative Seek (+10s)
  companion.handleRemoteCommand({ type: 'REMOTE_CMD', action: 'SEEK_REL', value: 10 });
  assert.strictEqual(currentSec, 30, 'SEEK_REL command should seek +10s to 30s');
});

test('15. Watch Party Whisper Chat & Long-Distance Couple Mode', () => {
  const wpCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');

  const messagesAppended = [];
  const sandbox = {
    window: {},
    document: {
      addEventListener: () => {},
      createElement: () => ({ setAttribute: () => {}, style: {}, innerHTML: '', appendChild: () => {}, classList: { add: () => {}, remove: () => {} } }),
      getElementById: (id) => {
        if (id === 'wp-chat-box') {
          return { appendChild: (el) => messagesAppended.push(el), scrollTop: 0, scrollHeight: 100 };
        }
        return null;
      },
      querySelector: () => null,
      querySelectorAll: () => []
    },
    location: { search: '', href: '', hash: '' },
    URLSearchParams: URLSearchParams,
    navigator: { vibrate: () => {} },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout: () => {}
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(wpCode, sandbox);

  const wp = sandbox.window.FilmZillaWatchParty;
  assert(wp, 'FilmZillaWatchParty must exist');

  // Verify receiving whisper message preserves isWhisper flag
  wp.handleMessage({
    type: 'CHAT_MESSAGE',
    userName: 'Partner',
    text: 'I love watching this with you ❤️',
    isWhisper: true
  });

  assert(messagesAppended.length > 0, 'A message item should have been appended to the chat box');
  const lastMsg = messagesAppended[messagesAppended.length - 1];
  assert(lastMsg.className.includes('is-whisper'), 'Appended message MUST contain is-whisper class on recipient');
});

test('16. Watch Party Predictions Betting & Reward Resolution', () => {
  const wpCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');

  const mockPlayerWrap = { appendChild: () => {}, querySelector: () => null, addEventListener: () => {} };
  const sandbox = {
    window: {},
    document: {
      body: { appendChild: () => {} },
      addEventListener: () => {},
      createElement: () => ({ setAttribute: () => {}, style: {}, innerHTML: '', appendChild: () => {}, classList: { add: () => {}, remove: () => {} }, querySelector: () => null }),
      getElementById: (id) => (id === 'video-player-container' ? mockPlayerWrap : null),
      querySelector: () => mockPlayerWrap,
      querySelectorAll: () => []
    },
    location: { search: '', href: '' },
    URLSearchParams: URLSearchParams,
    navigator: { vibrate: () => {} },
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout: () => {}
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(wpCode, sandbox);

  const wp = sandbox.window.FilmZillaWatchParty;
  const initialCoins = wp.popcornCoins;

  // Host starts a live prediction
  wp.startPrediction('Will the hero defeat the dragon?', 'Yes, Victorious', 'No, Retreats', 30);
  assert(wp.activePrediction, 'Active prediction must be created');
  assert.strictEqual(wp.activePrediction.question, 'Will the hero defeat the dragon?');

  // Place bet of 25 coins on option A
  wp.placePredictionBet('A', 25);
  assert.strictEqual(wp.popcornCoins, initialCoins - 25, '25 coins should be deducted for bet');
  assert.strictEqual(wp.activePrediction.myVote, 'A');

  // Resolve prediction: Option A wins!
  wp.handlePredictionResolve({
    type: 'PREDICTION_RESOLVE',
    predictionId: wp.activePrediction.id,
    winningOption: 'A',
    winnerName: 'Yes, Victorious'
  });

  // Winner receives 2x payout (50 coins)
  assert.strictEqual(wp.popcornCoins, initialCoins - 25 + 50, 'Winning bettor should receive 2x payout');
});

console.log('\n====================================================');
console.log(`SUMMARY: ${passCount} passed, ${failCount} failed`);
console.log('====================================================');

if (failCount > 0) {
  process.exit(1);
} else {
  console.log('🎉 ALL 16 COMPREHENSIVE VERIFICATION CHECKS PASSED PERFECTLY!');
  process.exit(0);
}
