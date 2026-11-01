/**
 * stream_worker.js  –  FilmSite Streaming Proxy
 * ================================================
 * Deploy as a Cloudflare Worker at: stream.yourdomain.workers.dev
 *
 * Routes:
 *   GET     /stream/{file_id}           → proxy video from VPS / Telegram CDN
 *   GET     /stream/{file_id}?token=XX  → same, with optional token auth
 *   OPTIONS /stream/*                   → CORS preflight
 *   GET     /health                     → health check
 *
 * Environment Variables (set in Cloudflare dashboard or wrangler.toml):
 *   VPS_STREAM_BASE_URL   – e.g. https://stream.yourserver.com/file
 *   STREAM_SECRET_TOKEN   – shared secret (leave empty to disable token check)
 *   VPS_INTERNAL_SECRET   – secret header sent to VPS to verify Worker origin
 *   ALLOWED_ORIGINS       – comma-separated allowed origins (default: *)
 */

// ── CORS helper ─────────────────────────────────────────────────────────────
function buildCorsHeaders(request, env) {
  const allowed = env.ALLOWED_ORIGINS
    ? env.ALLOWED_ORIGINS.split(',').map(o => o.trim())
    : ['*'];

  const origin = request.headers.get('Origin') || '';
  const allowOrigin = allowed[0] === '*' ? '*'
    : (allowed.includes(origin) ? origin : allowed[0]);

  return {
    'Access-Control-Allow-Origin':   allowOrigin,
    'Access-Control-Allow-Methods':  'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers':  'Range, Content-Type, Authorization',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Disposition',
    'Access-Control-Max-Age':        '86400',
    'Vary':                          'Origin',
  };
}

// ── Token validation ─────────────────────────────────────────────────────────
function isTokenValid(url, env) {
  if (!env.STREAM_SECRET_TOKEN || env.STREAM_SECRET_TOKEN.trim() === '') return true;
  const token = url.searchParams.get('token') || '';
  return token === env.STREAM_SECRET_TOKEN;
}

// ── Route parser ─────────────────────────────────────────────────────────────
function parseRoute(pathname) {
  const matchDl = pathname.match(/^\/stream\/download\/([^/?#]+)$/);
  if (matchDl) return { route: 'stream', fileId: decodeURIComponent(matchDl[1]), isDownload: true };
  const matchFile = pathname.match(/^\/stream\/file\/([^/?#]+)$/);
  if (matchFile) return { route: 'stream', fileId: decodeURIComponent(matchFile[1]) };
  const match = pathname.match(/^\/stream\/([^/?#]+)$/);
  if (match) return { route: 'stream', fileId: decodeURIComponent(match[1]) };
  if (pathname === '/health' || pathname === '/ping') return { route: 'health' };
  return { route: 'not_found' };
}

// ── Build upstream URL ────────────────────────────────────────────────────────
function buildUpstreamUrl(fileId, env, search = '', isDownload = false) {
  const base = (env.VPS_STREAM_BASE_URL || '').replace(/\/$/, '');
  if (!base) throw new Error('VPS_STREAM_BASE_URL is not configured.');

  const searchParams = new URLSearchParams(search);
  if (isDownload && !searchParams.has('dl')) {
    searchParams.set('dl', '1');
  }
  const qs = searchParams.toString();
  return `${base}/${encodeURIComponent(fileId)}${qs ? '?' + qs : ''}`;
}

// ── Proxy the stream ──────────────────────────────────────────────────────────
async function proxyStream(request, routeInfo, env, corsHeaders) {
  const fileId = typeof routeInfo === 'string' ? routeInfo : routeInfo.fileId;
  const isRouteDl = typeof routeInfo === 'object' && Boolean(routeInfo.isDownload);
  const url = new URL(request.url);

  if (!isTokenValid(url, env)) {
    return new Response(JSON.stringify({ error: 'Unauthorized – invalid or missing token.' }), {
      status:  401,
      headers: { 'Content-Type': 'application/json', ...corsHeaders },
    });
  }

  const secFetchDest = (request.headers.get('Sec-Fetch-Dest') || '').toLowerCase();
  const clientUA = request.headers.get('User-Agent') || '';
  const isDownloaderUA = /idm|adm|1dm|fdm|aria2|curl|wget|python|httpx|aiohttp|download/i.test(clientUA);
  const isDownload = Boolean(
    isRouteDl
    || url.searchParams.get('dl') === '1'
    || url.searchParams.get('download') === '1'
    || url.pathname.includes('/download/')
    || secFetchDest === 'document'
    || secFetchDest === 'download'
    || isDownloaderUA
  );

  let upstreamUrl;
  try {
    upstreamUrl = buildUpstreamUrl(fileId, env, url.search, isDownload);
  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), {
      status:  500,
      headers: { 'Content-Type': 'application/json', ...corsHeaders },
    });
  }

  // Forward Range header so the VPS can serve partial content for seeking
  const upstreamHeaders = new Headers();
  const rangeHeader = request.headers.get('Range');
  if (rangeHeader) upstreamHeaders.set('Range', rangeHeader);

  // Optional internal auth so VPS can reject requests not from this Worker
  if (env.VPS_INTERNAL_SECRET) {
    upstreamHeaders.set('X-Stream-Secret', env.VPS_INTERNAL_SECRET);
  }
  upstreamHeaders.set('User-Agent', clientUA || 'FilmSite-StreamWorker/1.0');
  if (request.headers.get('Sec-Fetch-Dest')) {
    upstreamHeaders.set('Sec-Fetch-Dest', request.headers.get('Sec-Fetch-Dest'));
  }

  let upstreamResponse;
  try {
    upstreamResponse = await fetch(upstreamUrl, {
      method:  request.method === 'HEAD' ? 'HEAD' : 'GET',
      headers: upstreamHeaders,
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: 'Failed to reach stream server.', detail: err.message }), {
      status:  502,
      headers: { 'Content-Type': 'application/json', ...corsHeaders },
    });
  }

  // Pass through non-2xx responses verbatim (404, 416 Range Not Satisfiable…)
  if (!upstreamResponse.ok && upstreamResponse.status !== 206) {
    return new Response(upstreamResponse.body, {
      status:  upstreamResponse.status,
      headers: { ...corsHeaders },
    });
  }

  // Build response headers
  const responseHeaders = new Headers(corsHeaders);
  const passthroughHeaders = [
    'Content-Type', 'Content-Length', 'Content-Range',
    'Accept-Ranges', 'Cache-Control', 'ETag', 'Last-Modified',
    'Content-Disposition',
  ];
  for (const h of passthroughHeaders) {
    const v = upstreamResponse.headers.get(h);
    if (v) responseHeaders.set(h, v);
  }
  if (!responseHeaders.has('Cache-Control')) {
    responseHeaders.set('Cache-Control', 'public, max-age=60');
  }
  responseHeaders.set('X-Content-Type-Options', 'nosniff');

  return new Response(upstreamResponse.body, {
    status:  upstreamResponse.status,   // 200 or 206
    headers: responseHeaders,
  });
}

// ── Main fetch handler ────────────────────────────────────────────────────────
export default {
  async fetch(request, env, ctx) {
    const url    = new URL(request.url);
    const method = request.method.toUpperCase();
    const cors   = buildCorsHeaders(request, env);
    const routeInfo = parseRoute(url.pathname);
    const { route } = routeInfo;

    // CORS preflight
    if (method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors });
    }

    // Health check
    if (route === 'health') {
      return new Response(JSON.stringify({
        status:    'ok',
        timestamp: new Date().toISOString(),
        worker:    'stream_worker',
        version:   '1.0.0',
      }), {
        status:  200,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    }

    // Stream route
    if (route === 'stream') {
      if (method !== 'GET' && method !== 'HEAD') {
        return new Response('Method Not Allowed', {
          status:  405,
          headers: { Allow: 'GET, HEAD, OPTIONS', ...cors },
        });
      }
      return proxyStream(request, routeInfo, env, cors);
    }

    // 404
    return new Response(JSON.stringify({
      error: 'Not found',
      validRoutes: [
        'GET /stream/{file_id}',
        'GET /stream/{file_id}?token=SECRET',
        'GET /health',
      ],
    }), {
      status:  404,
      headers: { 'Content-Type': 'application/json', ...cors },
    });
  },
};
