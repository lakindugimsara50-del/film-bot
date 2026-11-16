const https = require('https');

const urls = [
  'https://filmsub.pages.dev/index.html',
  'https://filmsub.pages.dev/assets/js/user-state.js',
  'https://filmsub.pages.dev/assets/js/roulette.js',
  'https://filmsub.pages.dev/assets/js/watch-party.js',
  'https://filmsub.pages.dev/assets/css/watch-party.css',
  'https://filmsub.pages.dev/movie.html'
];

async function checkUrl(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      if ((res.statusCode === 301 || res.statusCode === 302 || res.statusCode === 307 || res.statusCode === 308) && res.headers.location) {
        let redirectUrl = res.headers.location;
        if (redirectUrl.startsWith('/')) {
          redirectUrl = 'https://filmsub.pages.dev' + redirectUrl;
        }
        return resolve(checkUrl(redirectUrl));
      }
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        resolve({ url, statusCode: res.statusCode, length: data.length, content: data });
      });
    }).on('error', reject);
  });
}

(async () => {
  console.log('--- CHECKING LIVE CLOUDFLARE PAGES (filmsub.pages.dev) ---');
  let allPass = true;
  for (const u of urls) {
    const r = await checkUrl(u);
    console.log(`[HTTP ${r.statusCode}] ${r.url} (${r.length} bytes)`);
    if (r.statusCode !== 200) allPass = false;

    if (u.includes('index')) {
      const hasProfile = r.content.includes('id="user-profile-pill"');
      const hasContinue = r.content.includes('id="continue-watching-section"');
      const hasForYou = r.content.includes('id="for-you-section"');
      const hasRoulette = r.content.includes('id="fab-roulette"');
      console.log(`  -> index.html verification: Profile=${hasProfile}, ContinueWatching=${hasContinue}, ForYou=${hasForYou}, Roulette=${hasRoulette}`);
      if (!hasProfile || !hasContinue || !hasForYou || !hasRoulette) allPass = false;
    }

    if (u.includes('movie')) {
      const hasReactions = r.content.includes('id="cs-reaction-bar"');
      const hasWatchParty = r.content.includes('id="btn-watch-party-trigger"');
      const hasPiP = r.content.includes('id="btn-pip-trigger"');
      console.log(`  -> movie.html verification: Reactions=${hasReactions}, WatchParty=${hasWatchParty}, PiP=${hasPiP}`);
      if (!hasReactions || !hasWatchParty || !hasPiP) allPass = false;
    }
  }

  if (allPass) {
    console.log('🎉 100% OF LIVE FILES VERIFIED AND ACTIVE ON HTTPS://FILMSUB.PAGES.DEV!');
  } else {
    console.error('❌ Verification failed on some files.');
    process.exit(1);
  }
})();
