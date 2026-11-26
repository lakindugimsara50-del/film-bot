/**
 * Cloudflare Pages Function: /stream/seekinfo/[[path]]
 * ============================================================
 * Proxies MP4 moov keyframe seek index JSON from live backend with 7-day Cloudflare Edge Cache
 * so instant middle-seeking and zero-reload ABR quality handover hit edge PoPs in <20ms.
 */

const GITHUB_ENDPOINT_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json';
const DEFAULT_FALLBACK_RENDER = 'https://film-bot-2.onrender.com';

let cachedStreamBaseUrl = '';
let cachedFallbackBaseUrl = DEFAULT_FALLBACK_RENDER;
let cachedAtEpoch = 0;

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Accept, Origin',
    'Access-Control-Max-Age': '86400',
  };
}

async function resolveUrls(env) {
  if (env && env.STREAM_BACKEND_URL) {
    return [String(env.STREAM_BACKEND_URL).trim().replace(/\/+$/, ''), cachedFallbackBaseUrl];
  }
  const now = Date.now();
  if (!cachedStreamBaseUrl || now - cachedAtEpoch >= 15000) {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 2500);
      const r = await fetch(`${GITHUB_ENDPOINT_JSON}?t=${now}`, { signal: ctrl.signal });
      clearTimeout(timer);
      if (r.ok) {
        const d = await r.json();
        if (d && d.stream_base_url && String(d.stream_base_url).startsWith('http')) {
          cachedStreamBaseUrl = String(d.stream_base_url).trim().replace(/\/+$/, '');
          cachedAtEpoch = now;
        }
        if (d && d.fallback_stream_url && String(d.fallback_stream_url).startsWith('http')) {
          cachedFallbackBaseUrl = String(d.fallback_stream_url).trim().replace(/\/+$/, '');
        }
      }
    } catch (e) {}
  }
  return [cachedStreamBaseUrl, cachedFallbackBaseUrl].filter(Boolean);
}

export async function onRequest(context) {
  const { request, env, params } = context;
  const corsHeaders = buildCorsHeaders();

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const urlObj = new URL(request.url);
  let pathSegments = [];
  if (params && Array.isArray(params.path)) {
    pathSegments = params.path;
  } else if (params && typeof params.path === 'string') {
    pathSegments = params.path.split('/').filter(Boolean);
  } else {
    const m = urlObj.pathname.match(/\/stream\/seekinfo\/([^/]+)\/(\d+)/);
    if (m) pathSegments = [m[1], m[2]];
  }

  if (pathSegments.length < 2) {
    return new Response(JSON.stringify({ duration: 0, anchors: [] }), {
      status: 200,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  const [chatId, msgId] = pathSegments;
  const edgeCache = typeof caches !== 'undefined' ? caches.default : null;
  const cacheKey = new Request(
    `https://filmsub.pages.dev/__edge_stream_cache/v3/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}/seekinfo.json`,
    { method: 'GET' }
  );

  if (edgeCache) {
    try {
      const hit = await edgeCache.match(cacheKey);
      if (hit) {
        return new Response(hit.body, {
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'application/json', 'X-Edge-Cache': 'HIT', 'Cache-Control': 'public, max-age=86400' },
        });
      }
    } catch (e) {}
  }

  const urls = await resolveUrls(env);
  for (const base of urls) {
    try {
      const ctrl = new AbortController();
      const tId = setTimeout(() => ctrl.abort(), 8000);
      const r = await fetch(`${base}/stream/seekinfo/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}`, {
        headers: { 'User-Agent': 'FilmSub-Edge-Proxy/2.0' },
        signal: ctrl.signal,
      });
      clearTimeout(tId);
      if (r.ok) {
        const text = await r.text();
        if (edgeCache && text && text.includes('"anchors"')) {
          const putTask = edgeCache.put(
            cacheKey,
            new Response(text, {
              status: 200,
              headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=604800, s-maxage=604800' },
            })
          ).catch(() => {});
          if (context.waitUntil) context.waitUntil(putTask);
        }
        return new Response(text, {
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'application/json', 'X-Edge-Cache': 'MISS', 'Cache-Control': 'public, max-age=86400' },
        });
      }
    } catch (e) {}
  }

  return new Response(JSON.stringify({ duration: 0, anchors: [] }), {
    status: 200,
    headers: { ...corsHeaders, 'Content-Type': 'application/json' },
  });
}
