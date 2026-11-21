const fs = require('fs');
const vm = require('vm');
let code = fs.readFileSync('website/assets/js/player.js', 'utf8');

code = code.replace(
  "bc: typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('fz_wifi_p2p_mesh') : null,",
  "bc: null,"
);
code = code.replace(
  "bc: typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('fz_companion_sync') : null,",
  "bc: null,"
);

const OrigBC = global.BroadcastChannel;
global.BroadcastChannel = function(...args) {
  console.log('BroadcastChannel called with:', args);
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

const handles = process._getActiveHandles();
console.log('Active handles count:', handles.length);
handles.forEach(h => console.log('Constructor:', h.constructor.name));
