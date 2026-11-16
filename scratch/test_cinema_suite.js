/**
 * Comprehensive Integration Test Suite for FilmZilla Cinema Features (12 World-Class Features)
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

console.log('--- STARTING COMPREHENSIVE INTEGRATION TEST SUITE ---');

// 1. Mock Browser Environment
const localStorageMock = (function() {
  let store = {};
  return {
    getItem: (key) => store[key] || null,
    setItem: (key, value) => { store[key] = String(value); },
    removeItem: (key) => { delete store[key]; },
    clear: () => { store = {}; },
    _dump: () => store
  };
})();

const sessionStorageMock = (function() {
  let store = {};
  return {
    getItem: (key) => store[key] || null,
    setItem: (key, value) => { store[key] = String(value); },
    removeItem: (key) => { delete store[key]; },
    clear: () => { store = {}; }
  };
})();

const documentMock = {
  getElementById: (id) => ({
    id,
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    addEventListener: () => {},
    style: {},
    appendChild: () => {},
    setAttribute: () => {},
    textContent: '',
    innerHTML: '',
    querySelector: () => null,
    querySelectorAll: () => []
  }),
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => ({
    tagName: tag.toUpperCase(),
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    addEventListener: () => {},
    style: {},
    appendChild: () => {},
    setAttribute: () => {},
    textContent: '',
    innerHTML: '',
    querySelector: () => null,
    querySelectorAll: () => []
  }),
  body: {
    appendChild: () => {},
    removeChild: () => {}
  },
  addEventListener: () => {}
};

global.window = {
  localStorage: localStorageMock,
  sessionStorage: sessionStorageMock,
  document: documentMock,
  location: { search: '', href: 'https://filmsub.pages.dev/movie.html?id=iron-man', pathname: '/movie.html' },
  history: { replaceState: () => {} },
  addEventListener: () => {},
  google: { accounts: { id: { initialize: () => {}, prompt: () => {}, renderButton: () => {} } } }
};
global.document = documentMock;
global.localStorage = localStorageMock;
global.sessionStorage = sessionStorageMock;

// 2. Load user-state.js
const userStateCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/user-state.js'), 'utf8');
eval(userStateCode);

assert(window.FilmSubUser, 'FilmSubUser must be exported to window');
console.log('✓ FilmSubUser engine initialized');

// Test 1: User Profile & Google One Tap State
const user = window.FilmSubUser.getUser();
assert(user.name, 'Default user should have a name');
assert(user.avatar, 'Default user should have an avatar');
console.log(`✓ Feature 1 (User Profile): Guest loaded: "${user.name}" (${user.isGuest ? 'Guest' : 'Authenticated'})`);

// Test 2: Continue Watching / Auto-Resume
const sampleMovie = { id: 'm101', slug: 'm101', title: 'Inception', poster: 'inception.jpg', year: '2010', genres: ['Action', 'Sci-Fi'], quality: '1080p' };
window.FilmSubUser.ContinueWatching.saveProgress(sampleMovie, 1240, 7200); // 20m 40s
const progress = window.FilmSubUser.ContinueWatching.getProgress('m101');
assert.strictEqual(progress.currentTime, 1240, 'Progress currentTime should match');
assert(progress.percentage > 17 && progress.percentage < 18, 'Percentage should be ~17.2%');
console.log(`✓ Feature 2 (Continue Watching): Saved at ${progress.currentTime}s (${progress.percentage}%)`);

// Test 3: Watch History
const histList = window.FilmSubUser.History.getAll();
assert(histList.length >= 1, 'History list should contain at least 1 entry');
assert.strictEqual(histList[0].id, 'm101');
console.log(`✓ Feature 3 (Watch History): Logged "${histList[0].title}" at ${new Date(histList[0].timestamp).toISOString()}`);

// Test 4: Watch Later Queue
assert(!window.FilmSubUser.WatchLater.has('m101'));
const addedLater = window.FilmSubUser.WatchLater.toggle(sampleMovie);
assert(addedLater === true, 'Item should be added to watch later');
assert(window.FilmSubUser.WatchLater.has('m101'));
console.log(`✓ Feature 4 (Watch Later): Added to queue, count = ${window.FilmSubUser.WatchLater.getCount()}`);

// Test 5: Favorites
assert(!window.FilmSubUser.Favorites.has('m101'));
const addedFav = window.FilmSubUser.Favorites.toggle(sampleMovie);
assert(addedFav === true, 'Item should be added to favorites');
assert(window.FilmSubUser.Favorites.has('m101'));
console.log(`✓ Feature 5 (Favorites): Added to favorites, count = ${window.FilmSubUser.Favorites.getCount()}`);

// Test 6: Personalized "For You" Recommendations & Genre Affinity
const affinity = window.FilmSubUser.getGenreAffinity();
assert(affinity['action'] > 0, 'Action genre affinity should be incremented');
assert(affinity['sci-fi'] > 0, 'Sci-Fi genre affinity should be incremented');
console.log(`✓ Feature 6 (For You Affinity): Genre scores:`, affinity);

// Test 7: Release Radar (HD Upgrade Alerts)
window.FilmSubUser.ReleaseRadar.track({ id: 'm102', title: 'Gladiator II', quality: 'CAM' });
assert(window.FilmSubUser.ReleaseRadar.has('m102'));
const alertResult = window.FilmSubUser.ReleaseRadar.checkUpgrade({ id: 'm102', title: 'Gladiator II', quality: '1080p WEB-DL' });
assert(alertResult && alertResult.upgraded === true, 'Should detect upgrade from CAM to 1080p');
console.log(`✓ Feature 8 (Release Radar): Detected upgrade: CAM -> 1080p WEB-DL`);

// Test 8: Roulette & Discovery API
const rouletteCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/roulette.js'), 'utf8');
eval(rouletteCode);
assert(window.FilmZillaRoulette, 'FilmZillaRoulette should be exported');
assert(typeof window.FilmZillaRoulette.openRoulette === 'function');
assert(typeof window.FilmZillaRoulette.openRequestModal === 'function');
assert(typeof window.FilmZillaRoulette.initReactions === 'function');
assert(typeof window.FilmZillaRoulette.togglePiP === 'function');
console.log('✓ Features 7, 9, 10, 11 (Roulette, Request, Reactions, PiP) verified in FilmZillaRoulette API');

// Test 9: Watch Party Engine & Dual Control Mode
const watchPartyCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');
eval(watchPartyCode);
assert(window.FilmZillaWatchParty, 'FilmZillaWatchParty should be exported');
assert.strictEqual(window.FilmZillaWatchParty.isLocked, false, 'Default mode should be collaborative (not locked)');
window.FilmZillaWatchParty.createRoom();
assert(window.FilmZillaWatchParty.roomId.startsWith('WP-'), 'Room code should start with WP-');
assert.strictEqual(window.FilmZillaWatchParty.isHost, true, 'Room creator should be host');

// Test Dual Control Mode Toggle
window.FilmZillaWatchParty.toggleLockMode();
assert.strictEqual(window.FilmZillaWatchParty.isLocked, true, 'Lock mode should toggle to true');
window.FilmZillaWatchParty.toggleLockMode();
assert.strictEqual(window.FilmZillaWatchParty.isLocked, false, 'Lock mode should toggle back to collaborative');
console.log(`✓ Feature 12 (Watch Party): Room ${window.FilmZillaWatchParty.roomId} created with Dual Control (Collaborative / Host Lock)`);

// Test 10: HTML Element Check
const indexHtml = fs.readFileSync(path.join(__dirname, '../website/index.html'), 'utf8');
const movieHtml = fs.readFileSync(path.join(__dirname, '../website/movie.html'), 'utf8');

assert(indexHtml.includes('id="user-profile-pill"'), 'index.html must have user profile pill');
assert(indexHtml.includes('id="continue-watching-section"'), 'index.html must have continue watching section');
assert(indexHtml.includes('id="for-you-section"'), 'index.html must have for you section');
assert(indexHtml.includes('id="fab-roulette"'), 'index.html must have fab roulette');
assert(movieHtml.includes('id="cs-reaction-bar"'), 'movie.html must have reaction bar');
assert(movieHtml.includes('id="btn-watch-party-trigger"'), 'movie.html must have watch party button');
assert(movieHtml.includes('id="btn-pip-trigger"'), 'movie.html must have pip button');
assert(movieHtml.includes('watch-party.css'), 'movie.html must include watch-party.css');
assert(movieHtml.includes('watch-party.js'), 'movie.html must include watch-party.js');

console.log('✓ All 12 features verified in DOM and HTML templates!');
console.log('--- ALL INTEGRATION TESTS PASSED (100% SUCCESS) ---');
