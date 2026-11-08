/**
 * Cloudflare Pages Function: /movie.html
 * ============================================================
 * Dynamic Server-Side Open Graph (OG) & Twitter Card Injector
 *
 * Intercepts social platform crawlers (WhatsApp, TelegramBot, Facebook,
 * Twitter, Discord) and injects dynamic movie metadata so shared links
 * render high-definition posters, titles, and Sinhala synopses.
 * Regular browser visits pass through directly to static assets (zero latency).
 */

const GITHUB_MOVIES_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/movies.json';

let cachedMovies = null;
let cachedEpoch = 0;
const CACHE_TTL_MS = 60000; // 60 seconds edge cache

async function getMoviesList() {
  const now = Date.now();
  if (cachedMovies && now - cachedEpoch < CACHE_TTL_MS) {
    return cachedMovies;
  }
  try {
    const res = await fetch(GITHUB_MOVIES_JSON, {
      cf: { cacheTtl: 60, cacheEverything: true },
    });
    if (res.ok) {
      const data = await res.json();
      cachedMovies = Array.isArray(data) ? data : data.movies || [];
      cachedEpoch = now;
      return cachedMovies;
    }
  } catch (err) {
    console.warn('[OG-Function] Could not fetch movies.json:', err);
  }
  return cachedMovies || [];
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export async function onRequest(context) {
  const { request } = context;
  const userAgent = request.headers.get('User-Agent') || '';

  const isSocialCrawler =
    /bot|spider|crawl|facebookexternalhit|whatsapp|telegrambot|twitterbot|slackbot|discordbot/i.test(
      userAgent
    );

  const url = new URL(request.url);
  const movieId = (url.searchParams.get('id') || '').trim();

  // For regular human visitors or requests without a movie id, pass through immediately
  if (!isSocialCrawler || !movieId) {
    return context.next();
  }

  // Social crawler detected: fetch static HTML and inject rich OpenGraph tags
  const staticResponse = await context.next();
  let html = await staticResponse.text();

  const movies = await getMoviesList();
  const idLower = movieId.toLowerCase();
  const movie = movies.find(
    (m) =>
      (m.slug && m.slug.toLowerCase() === idLower) ||
      (m.id && String(m.id).toLowerCase() === idLower)
  );

  if (!movie) {
    return new Response(html, {
      headers: staticResponse.headers,
      status: staticResponse.status,
    });
  }

  const title = `${movie.title || 'Movie'} (${movie.year || ''}) Sinhala Subtitles – FilmZilla`;
  const desc =
    movie.description ||
    movie.description_si ||
    `Watch ${movie.title} with Sinhala Subtitles on FilmZilla. High-Speed 1080p FHD & 720p HD streaming.`;
  const img =
    movie.backdrop ||
    movie.poster ||
    'https://filmsub.pages.dev/assets/img/filmzilla_icon.png';
  const pageUrl = request.url;

  // Replace title & description
  html = html.replace(/<title>.*?<\/title>/i, `<title>${escapeHtml(title)}</title>`);
  html = html.replace(
    /<meta\s+name=["']description["'][^>]*>/i,
    `<meta name="description" content="${escapeHtml(desc)}">`
  );

  // Replace OpenGraph meta tags
  html = html.replace(
    /<meta\s+property=["']og:title["'][^>]*>/i,
    `<meta property="og:title" content="${escapeHtml(title)}">`
  );
  html = html.replace(
    /<meta\s+property=["']og:description["'][^>]*>/i,
    `<meta property="og:description" content="${escapeHtml(desc)}">`
  );
  html = html.replace(
    /<meta\s+property=["']og:image["'][^>]*>/i,
    `<meta property="og:image" content="${escapeHtml(img)}">`
  );
  html = html.replace(
    /<meta\s+property=["']og:url["'][^>]*>/i,
    `<meta property="og:url" content="${escapeHtml(pageUrl)}">`
  );

  // Replace Twitter card tags
  html = html.replace(
    /<meta\s+name=["']twitter:title["'][^>]*>/i,
    `<meta name="twitter:title" content="${escapeHtml(title)}">`
  );
  html = html.replace(
    /<meta\s+name=["']twitter:description["'][^>]*>/i,
    `<meta name="twitter:description" content="${escapeHtml(desc)}">`
  );
  html = html.replace(
    /<meta\s+name=["']twitter:image["'][^>]*>/i,
    `<meta name="twitter:image" content="${escapeHtml(img)}">`
  );

  const responseHeaders = new Headers(staticResponse.headers);
  responseHeaders.set('Content-Type', 'text/html; charset=UTF-8');
  responseHeaders.set('X-Edge-Prerender', 'Social-OG-Injected');

  return new Response(html, {
    status: staticResponse.status,
    headers: responseHeaders,
  });
}
