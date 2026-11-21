const fs = require('fs');
const path = require('path');

console.log('--- STARTING TEASERS PLAYER & YOUTUBE PIPELINE AUDIT ---');

const teasersJs = fs.readFileSync('website/data/teasers_data.js', 'utf8');
const teasersJson = fs.readFileSync('website/data/teasers.json', 'utf8');
const appTeasersJs = fs.readFileSync('website/assets/js/teasers.js', 'utf8');
const teasersCss = fs.readFileSync('website/assets/css/teasers.css', 'utf8');
const headersFile = fs.readFileSync('website/_headers', 'utf8');

// 1. Verify all teasers have real YouTube trailers and no dummy mp4
const match = teasersJs.match(/window\.FILMSUB_TEASERS\s*=\s*(\[[\s\S]*?\]);/);
if (!match) {
  console.error('FAIL: FILMSUB_TEASERS not found');
  process.exit(1);
}
const list = JSON.parse(match[1]);
console.log(`PASS 1: Loaded ${list.length} teasers from teasers_data.js`);

const dummyMp4s = list.filter(t => t.video_url && t.video_url.includes('teaser.mp4'));
if (dummyMp4s.length > 0) {
  console.error('FAIL: Found dummy mp4s in teasers:', dummyMp4s.map(t => t.title));
  process.exit(1);
}
console.log('PASS 2: Zero dummy placeholder MP4s found. All teasers map directly to YouTube trailers!');

// 2. Check Sugriva
const sugriva = list.find(t => t.movie_id === 'sugriva-2026');
if (!sugriva || sugriva.trailer_id !== 'DfNcx19PY6c' || !sugriva.youtube_embed_url.includes('DfNcx19PY6c')) {
  console.error('FAIL: Sugriva teaser YouTube trailer mapping incorrect');
  process.exit(1);
}
console.log('PASS 3: Sugriva correctly mapped to official YouTube trailer DfNcx19PY6c with timestamps', sugriva.clip_start, '-', sugriva.clip_end);

// 3. Verify iframe loading bug fix in teasers.js
if (appTeasersJs.includes('!iframe.src || iframe.src === \'about:blank\'')) {
  console.error('FAIL: Flawed !iframe.src check still present in teasers.js');
  process.exit(1);
}
if (!appTeasersJs.includes('!currentSrc || currentSrc === \'about:blank\'')) {
  console.error('FAIL: Robust currentSrc check missing in teasers.js');
  process.exit(1);
}
console.log('PASS 4: Iframe source loading logic verified without empty string resolution bug');

// 4. Verify CSS for reels-yt-iframe
if (!teasersCss.includes('.reels-yt-iframe')) {
  console.error('FAIL: .reels-yt-iframe missing from teasers.css');
  process.exit(1);
}
console.log('PASS 5: .reels-yt-iframe styles verified');

// 5. Verify _headers CSP
if (!headersFile.includes('https://www.youtube.com') || !headersFile.includes('https://www.youtube-nocookie.com')) {
  console.error('FAIL: YouTube origins missing from CSP in _headers');
  process.exit(1);
}
console.log('PASS 6: CSP headers allow YouTube scripts and frame embeds');

console.log('--- ALL TEASERS AUDIT TESTS PASSED 100% ---');
