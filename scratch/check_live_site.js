const https = require('https');

function fetch(url, maxRedirects = 5) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      if ([301, 302, 307, 308].includes(res.statusCode) && res.headers.location && maxRedirects > 0) {
        let nextUrl = res.headers.location;
        if (nextUrl.startsWith('/')) {
          nextUrl = 'https://filmsub.pages.dev' + nextUrl;
        }
        return resolve(fetch(nextUrl, maxRedirects - 1));
      }
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ statusCode: res.statusCode, headers: res.headers, body: data, finalUrl: url }));
    }).on('error', reject);
  });
}

async function run() {
  console.log('=== CHECKING LIVE PRODUCTION SITE filmsub.pages.dev (FOLLOWING REDIRECTS) ===');
  
  // 1. Check index.html
  const home = await fetch('https://filmsub.pages.dev/');
  console.log('Home status:', home.statusCode, 'Final URL:', home.finalUrl);
  console.log('Home has header-sidebar-toggle-btn:', home.body.includes('header-sidebar-toggle-btn'));
  console.log('Home has cinema-global-nav-drawer:', home.body.includes('cinema-global-nav-drawer'));
  
  // 2. Check movie page
  const movie = await fetch('https://filmsub.pages.dev/movie?id=insidious-out-of-the-further-2026');
  console.log('Movie status:', movie.statusCode, 'Final URL:', movie.finalUrl);
  console.log('Movie has cs-3d-download-bubble-box in HTML or script:', movie.body.includes('cs-3d-download-bubble-box'));
  console.log('Movie has header-sidebar-toggle-btn:', movie.body.includes('header-sidebar-toggle-btn'));
  console.log('Movie has cs-reaction-floating-stage:', movie.body.includes('cs-reaction-floating-stage'));
  console.log('Movie has cs-video-chat-overlay:', movie.body.includes('cs-video-chat-overlay'));
  console.log('Movie has cs-couple-cam-dock:', movie.body.includes('cs-couple-cam-dock'));
  console.log('Movie has btn-bar-mic:', movie.body.includes('btn-bar-mic'));
  console.log('Movie has btn-bar-cam:', movie.body.includes('btn-bar-cam'));

  // 3. Check what script tags are in movie HTML
  const scriptTags = movie.body.match(/<script[^>]*src=[\"'][^\"']*[\"'][^>]*>/g) || [];
  console.log('Movie script tags count:', scriptTags.length);

  // 4. Check CSS links in movie HTML
  const cssTags = movie.body.match(/<link[^>]*href=[\"'][^\"']*[\"'][^>]*>/g) || [];
  console.log('Movie CSS tags count:', cssTags.length);

  // 5. Check if cinema-global-nav-drawer is created dynamically in app.js
  const appMatch = movie.body.match(/assets\/js\/app\.js[^\"]*/);
  const appUrl = appMatch ? `https://filmsub.pages.dev/${appMatch[0]}` : 'https://filmsub.pages.dev/assets/js/app.js';
  console.log('Live app.js URL:', appUrl);
  const liveApp = await fetch(appUrl);
  console.log('Live app.js has Cloud Synced:', liveApp.body.includes('Cloud Synced'));
  console.log('Live app.js has openAvatarPickerModal:', liveApp.body.includes('openAvatarPickerModal'));
  console.log('Live app.js has vip-hub-count-pill:', liveApp.body.includes('vip-hub-count-pill'));
  console.log('Live app.js has initGlobalCinemaDrawer:', liveApp.body.includes('initGlobalCinemaDrawer'));
  console.log('Live app.js creates cinema-global-nav-drawer:', liveApp.body.includes('cinema-global-nav-drawer'));

  // 6. Check player.js on live
  const playerMatch = movie.body.match(/assets\/js\/player\.js[^\"]*/);
  const playerUrl = playerMatch ? `https://filmsub.pages.dev/${playerMatch[0]}` : 'https://filmsub.pages.dev/assets/js/player.js';
  console.log('Live player.js URL:', playerUrl);
  const livePlayer = await fetch(playerUrl);
  console.log('Live player.js has cs-3d-download-bubble-box:', livePlayer.body.includes('cs-3d-download-bubble-box'));
  console.log('Live player.js has 1.76 GB:', livePlayer.body.includes('1.76 GB'));

  // 7. Check watch-party.js on live
  const wpMatch = movie.body.match(/assets\/js\/watch-party\.js[^\"]*/);
  const wpUrl = wpMatch ? `https://filmsub.pages.dev/${wpMatch[0]}` : 'https://filmsub.pages.dev/assets/js/watch-party.js';
  console.log('Live watch-party.js URL:', wpUrl);
  const liveWp = await fetch(wpUrl);
  console.log('Live watch-party.js has toggleMic:', liveWp.body.includes('toggleMic'));
  console.log('Live watch-party.js has toggleCam:', liveWp.body.includes('toggleCam'));
  console.log('Live watch-party.js has showInVideoChatPopup:', liveWp.body.includes('showInVideoChatPopup'));
  console.log('Live watch-party.js has Cinema Live Synchronizer:', liveWp.body.includes('Cinema Live Synchronizer'));

  // 8. Check roulette.js on live
  const rouletteMatch = movie.body.match(/assets\/js\/roulette\.js[^\"]*/);
  const rouletteUrl = rouletteMatch ? `https://filmsub.pages.dev/${rouletteMatch[0]}` : 'https://filmsub.pages.dev/assets/js/roulette.js';
  console.log('Live roulette.js URL:', rouletteUrl);
  const liveRoulette = await fetch(rouletteUrl);
  console.log('Live roulette.js has 1f525/512.webp:', liveRoulette.body.includes('1f525/512.webp'));
  console.log('Live roulette.js has fire_3d.png:', liveRoulette.body.includes('fire_3d.png'));
}

run().catch(console.error);
