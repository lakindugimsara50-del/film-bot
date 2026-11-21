const fs = require('fs');
const vm = require('vm');
let code = fs.readFileSync('website/assets/js/player.js', 'utf8');

const OrigBC = global.BroadcastChannel;
global.BroadcastChannel = function(...args) {
  console.log('BroadcastChannel constructor called with:', args);
  console.trace();
  return new OrigBC(...args);
};

global.window = { FilmSub: {} };
global.document = {
    addEventListener: () => {},
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => []
};
global.navigator = {};
global.location = { search: '', href: '' };
global.FilmSub = { escHtml: s => s };
global.currentSeason = 1;
global.currentEpisode = 1;
global.currentEffectiveQuality = '1080p';

vm.runInThisContext(code);
process.exit(0);
