const fs = require('fs');
const path = require('path');
const assert = require('assert');
const vm = require('vm');

console.log('🧪 Testing Telegram/WhatsApp Style Poll Creator in Watch Party...');

function createMockElement(tag) {
  const el = {
    tagName: tag.toUpperCase(),
    id: '',
    className: '',
    classList: {
      add: () => {},
      remove: () => {},
      contains: () => false
    },
    style: {},
    innerHTML: '',
    value: '',
    dataset: {},
    children: [],
    attributes: {},
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k] || null; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter(c => c !== child); },
    addEventListener(ev, fn) { this['on' + ev] = fn; },
    querySelector(sel) {
      if (sel.startsWith('#')) {
        const id = sel.slice(1);
        return findById(this, id);
      }
      return null;
    },
    querySelectorAll(sel) {
      const results = [];
      findAll(this, sel, results);
      return results;
    }
  };
  return el;
}

function findById(node, id) {
  if (node.id === id) return node;
  if (node.children) {
    for (const child of node.children) {
      const found = findById(child, id);
      if (found) return found;
    }
  }
  return null;
}

function findAll(node, sel, results) {
  if (sel.startsWith('.') && node.className && node.className.includes(sel.slice(1))) {
    results.push(node);
  }
  if (node.children) {
    for (const child of node.children) {
      findAll(child, sel, results);
    }
  }
}

const docElements = new Map();
const mockBody = createMockElement('body');

const sandbox = {
  window: {},
  document: {
    body: mockBody,
    createElement: (tag) => createMockElement(tag),
    getElementById: (id) => docElements.get(id) || null,
    querySelector: (sel) => {
      if (sel.startsWith('#')) return docElements.get(sel.slice(1)) || null;
      return null;
    },
    querySelectorAll: (sel) => {
      const results = [];
      docElements.forEach(el => findAll(el, sel, results));
      return results;
    },
    addEventListener: () => {}
  },
  location: { search: '?id=inception-2010&room=testroom1', href: 'https://filmsub.pages.dev/movie.html?id=inception-2010&room=testroom1' },
  URLSearchParams: URLSearchParams,
  navigator: { vibrate: () => {} },
  setInterval: () => 1,
  clearInterval: () => {},
  setTimeout: (fn) => { fn(); return 1; },
  clearTimeout: () => {},
  console: console
};
sandbox.window = sandbox;

// Save elements registered in body
const origAppendChild = mockBody.appendChild.bind(mockBody);
mockBody.appendChild = (child) => {
  if (child.id) docElements.set(child.id, child);
  return origAppendChild(child);
};

const wpCode = fs.readFileSync(path.join(__dirname, '../website/assets/js/watch-party.js'), 'utf8');
vm.createContext(sandbox);
vm.runInContext(wpCode, sandbox);

const wp = sandbox.window.FilmZillaWatchParty;
assert(wp, 'FilmZillaWatchParty should be initialized');

// Test 1: Start Prediction with Free Poll (wager = 0)
const initialCoins = wp.popcornCoins;
wp.startPrediction('Will hero escape?', 'Yes', 'No', 25, 0);
assert(wp.activePrediction, 'Active prediction must be created');
assert.strictEqual(wp.activePrediction.wager, 0, 'Wager must be 0 for free poll');
assert.strictEqual(wp.activePrediction.question, 'Will hero escape?');

// Place free vote on option A
wp.placePredictionBet('A');
assert.strictEqual(wp.popcornCoins, initialCoins, '0 coins deducted for free poll vote');
assert.strictEqual(wp.activePrediction.votesA, 1, 'Option A votes must increment');
assert.strictEqual(wp.activePrediction.myVote, 'A');
console.log('✅ 1. Free poll voting with 0 coin deduction verified.');

// Resolve Free Poll
wp.handlePredictionResolve({
  type: 'PREDICTION_RESOLVE',
  predictionId: wp.activePrediction.id,
  winningOption: 'A',
  winnerName: 'Yes'
});
assert.strictEqual(wp.activePrediction, null, 'Prediction should be resolved');
assert.strictEqual(wp.popcornCoins, initialCoins + 10, 'Free poll correct guess gets 10 bonus coins');
console.log('✅ 2. Free poll winner resolution with bonus verified.');

// Test 2: Start Prediction with Popcorn Bet (wager = 25)
const coinsBeforeBet = wp.popcornCoins;
wp.startPrediction('Who is the traitor?', 'Friend', 'Boss', 30, 25);
assert(wp.activePrediction, 'Active prediction must be created');
assert.strictEqual(wp.activePrediction.wager, 25);

// Place 25 coins bet on option B
wp.placePredictionBet('B', 25);
assert.strictEqual(wp.popcornCoins, coinsBeforeBet - 25, '25 coins deducted for wager');
assert.strictEqual(wp.activePrediction.myBet, 25);
assert.strictEqual(wp.activePrediction.myVote, 'B');
console.log('✅ 3. Popcorn bet with 25 coins deduction verified.');

// Resolve Popcorn Bet (2x win payout)
wp.handlePredictionResolve({
  type: 'PREDICTION_RESOLVE',
  predictionId: wp.activePrediction.id,
  winningOption: 'B',
  winnerName: 'Boss'
});
assert.strictEqual(wp.activePrediction, null, 'Active prediction resolved');
assert.strictEqual(wp.popcornCoins, coinsBeforeBet - 25 + 50, 'Winner receives 2x payout (50 coins)');
console.log('✅ 4. Popcorn bet 2x payout resolution verified.');

// Test 3: Cancel / Draw refund
const coinsBeforeCancel = wp.popcornCoins;
wp.startPrediction('Will the bomb explode?', 'Yes', 'No', 25, 25);
wp.placePredictionBet('A', 25);
assert.strictEqual(wp.popcornCoins, coinsBeforeCancel - 25);

wp.handlePredictionResolve({
  type: 'PREDICTION_RESOLVE',
  predictionId: wp.activePrediction.id,
  winningOption: 'CANCEL',
  winnerName: 'Cancelled'
});
assert.strictEqual(wp.activePrediction, null);
assert.strictEqual(wp.popcornCoins, coinsBeforeCancel, 'Refund returned on cancel/draw');
console.log('✅ 5. Cancel/Draw refund verification passed.');

console.log('🎉 ALL TELEGRAM / WHATSAPP POLL CREATOR UNIT TESTS PASSED PERFECTLY!');
