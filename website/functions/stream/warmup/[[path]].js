/**
 * Cloudflare Pages Function: /stream/warmup/[[path]]
 * ============================================================
 * Edge Warmup Proxy for Telegram Cloud HD
 * Primes both Cloudflare Edge Cache (caches.default) and upstream RAM cache
 */

const GITHUB_ENDPOINT_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json';

const EDGE_INITIAL_CHUNK_BYTES = 8 * 1024 * 1024; // 8 MiB initial chunk
const CANONICAL_CACHE_ORIGIN = 'https://filmsub.pages.dev';

const DEFAULT_FALLBACK_TUNNEL =
  'https://insertion-cult-pipes-sandy.trycloudflare.com';
const DEFAULT_FALLBACK_RENDER = 'https://film-bot-2.onrender.com';

let cachedStreamBaseUrl = DEFAULT_FALLBACK_TUNNEL;
let cachedFallbackBaseUrl = DEFAULT_FALLBACK_RENDER;
let cachedAtEpoch = 0;
const CACHE_TTL_MS = 60000;

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Type, X-Stream-Backend, X-Edge-Cache',
    'Access-Control-Max-Age': '86400',
  };
}

function getCanonicalCacheKey(chatId, msgId) {
  return new Request(
    `${CANONICAL_CACHE_ORIGIN}/__edge_stream_cache/v2/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}/chunk_0_8m`,
    { method: 'GET' }
  );
}

async function readExactBytes(readableStream, skip, limit) {
  const reader = readableStream.getReader();
  const out = new Uint8Array(limit);
  let bytesSkipped = 0;
  let bytesWritten = 0;

  try {
    while (bytesWritten < limit) {
      const { done, value } = await reader.read();
      if (done || !value) break;

      let start = 0;
      if (bytesSkipped < skip) {
        const remainingToSkip = skip - bytesSkipped;
        if (value.length <= remainingToSkip) {
          bytesSkipped += value.length;
          continue;
        }
        start = remainingToSkip;
        bytesSkipped = skip;
      }

      const available = value.length - start;
      const needed = limit - bytesWritten;
      const toCopy = Math.min(available, needed);

      out.set(value.subarray(start, start + toCopy), bytesWritten);
      bytesWritten += toCopy;
    }
  } finally {
    try { await reader.cancel(); } catch (e) {}
  }

  return bytesWritten === limit ? out : out.subarray(0, bytesWritten);
}

async function resolveLiveStreamBaseUrl(env, context) {
  if (env && env.STREAM_BACKEND_URL) {
    return {
      primary: String(env.STREAM_BACKEND_URL).trim().replace(/\/+$/, ''),
      fallback: (env.FALLBACK_STREAM_URL ? String(env.FALLBACK_STREAM_URL).trim().replace(/\/+$/, '') : cachedFallbackBaseUrl),
    };
  }

  const now = Date.now();
  // 1. If we already have a cached tunnel URL and TTL is fresh, return in 0ms
  if (cachedStreamBaseUrl && (now - cachedAtEpoch < CACHE_TTL_MS)) {
    return { primary: cachedStreamBaseUrl, fallback: cachedFallbackBaseUrl };
  }

  // Helper background task to refresh endpoints from GitHub Raw without blocking client
  const refreshTask = async () => {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 2500);
      const resp = await fetch(`${GITHUB_ENDPOINT_JSON}?t=${Date.now()}`, {
        headers: { 'User-Agent': 'FilmSub-Edge-Proxy/2.0', 'Cache-Control': 'no-cache' },
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.stream_base_url && typeof data.stream_base_url === 'string') {
          const cleaned = data.stream_base_url.trim().replace(/\/+$/, '');
          if (cleaned.startsWith('http')) {
            cachedStreamBaseUrl = cleaned;
            cachedAtEpoch = Date.now();
          }
        }
        if (data && data.fallback_stream_url && typeof data.fallback_stream_url === 'string') {
          const cleanedFb = data.fallback_stream_url.trim().replace(/\/+$/, '');
          if (cleanedFb.startsWith('http')) {
            cachedFallbackBaseUrl = cleanedFb;
          }
        }
      }
    } catch (err) {}
  };

  // 2. If cached URL exists, return immediately (0ms) and revalidate in background
  if (cachedStreamBaseUrl) {
    if (context && typeof context.waitUntil === 'function') {
      context.waitUntil(refreshTask());
    } else {
      refreshTask().catch(() => {});
    }
    return { primary: cachedStreamBaseUrl, fallback: cachedFallbackBaseUrl };
  }

  // 3. Fallback only if no URL present
  await refreshTask();
  return {
    primary: cachedStreamBaseUrl || DEFAULT_FALLBACK_TUNNEL,
    fallback: cachedFallbackBaseUrl || DEFAULT_FALLBACK_RENDER,
  };
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
    const m = urlObj.pathname.match(/\/stream\/warmup\/([^/]+)\/(\d+)/);
    if (m) pathSegments = [m[1], m[2]];
  }

  if (pathSegments.length < 2) {
    return new Response(
      JSON.stringify({ error: 'Invalid warmup path. Expected /stream/warmup/:chat_id/:msg_id' }),
      {
        status: 400,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      }
    );
  }

  const chatId = pathSegments[0];
  const msgId = pathSegments[1];

  const edgeCache = typeof caches !== 'undefined' ? caches.default : null;
  const cacheKey = getCanonicalCacheKey(chatId, msgId);

  const { primary: baseUrl, fallback: fallbackUrl } = await resolveLiveStreamBaseUrl(env, context);
  const urlsToTry = [baseUrl, fallbackUrl].filter(Boolean);

  // Background task to prime Cloudflare Edge Cache if not already cached
  const primeEdgeCache = async () => {
    if (!edgeCache) return;
    try {
      const isAlreadyCached = await edgeCache.match(cacheKey);
      if (isAlreadyCached) return;

      for (const activeBase of urlsToTry) {
        // Trigger upstream RAM cache pre-warming (16MB) in parallel
        fetch(`${activeBase}/stream/warmup/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}`, {
          method: 'POST',
          headers: { 'User-Agent': 'FilmSub-Edge-Warmup/2.0' },
        }).catch(() => {});

        const streamUrl = `${activeBase}/stream/channel/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}`;
        try {
          const resp = await fetch(streamUrl, {
            headers: {
              'Range': `bytes=0-${EDGE_INITIAL_CHUNK_BYTES - 1}`,
              'User-Agent': 'FilmSub-Edge-Warmup/2.0',
            },
          });
          if (resp.ok || resp.status === 206) {
            const contentRange = resp.headers.get('Content-Range');
            let totalSize = '*';
            if (contentRange) {
              const m = contentRange.match(/bytes\s+\d+-\d+\/(\d+|\*)/);
              if (m) totalSize = m[1];
            } else {
              totalSize = resp.headers.get('Content-Length') || '*';
            }
            const buf = await readExactBytes(resp.body, 0, EDGE_INITIAL_CHUNK_BYTES);
            if (buf && buf.byteLength > 0) {
              const cacheableRes = new Response(buf, {
                status: 200,
                headers: {
                  'Content-Type': resp.headers.get('Content-Type') || 'video/mp4',
                  'Content-Length': String(buf.byteLength),
                  'X-Total-Size': String(totalSize),
                  'Cache-Control': 'public, max-age=604800, s-maxage=604800',
                },
              });
              await edgeCache.put(cacheKey, cacheableRes);
              break;
            }
          }
        } catch (e) {}
      }
    } catch (e) {}
  };

  if (context.waitUntil && typeof context.waitUntil === 'function') {
    context.waitUntil(primeEdgeCache());
  } else {
    primeEdgeCache().catch(() => {});
  }

  // Trigger upstream server RAM cache warmup
  for (const activeBase of urlsToTry) {
    const targetUrl = `${activeBase}/stream/warmup/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}`;
    try {
      const upstreamRes = await fetch(targetUrl, {
        method: request.method,
        headers: {
          'User-Agent': 'FilmSub-Edge-Proxy/2.0',
          'Accept': 'application/json',
        },
      });
      if (upstreamRes.ok) {
        const bodyText = await upstreamRes.text();
        return new Response(bodyText, {
          status: 200,
          headers: { ...corsHeaders, 'Content-Type': 'application/json', 'X-Stream-Backend': activeBase, 'X-Edge-Cache': 'WARMING' },
        });
      }
    } catch (err) {}
  }

  // Graceful response even if backend is offline so frontend never errors
  return new Response(
    JSON.stringify({ status: 'warming_fallback', chat_id: chatId, message_id: Number(msgId), edge_cache: 'priming' }),
    {
      status: 200,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    }
  );
}
