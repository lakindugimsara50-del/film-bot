/**
 * Cloudflare Pages Function: /stream/channel/[[path]]
 * ============================================================
 * Dynamic Edge Byte-Range Stream Proxy for Telegram Cloud HD
 *
 * Solves:
 *  1. Stale _redirects / static stream_endpoint.json when Colab restarts and
 *     generates a new *.trycloudflare.com tunnel URL.
 *  2. Local ISP / DNS blocking of *.trycloudflare.com on user devices — browser
 *     requests same-origin /stream/channel/:chat_id/:msg_id on filmsub.pages.dev,
 *     and Cloudflare's edge proxies HTTP 206 byte-range chunks from the live tunnel.
 *  3. Fast 503 failover if the tunnel is offline so Video.js immediately switches
 *     to VIP Server 2 without a white screen or long hang.
 */

const GITHUB_ENDPOINT_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json';

let cachedStreamBaseUrl = '';
let cachedFallbackBaseUrl = '';
let cachedAtEpoch = 0;
const CACHE_TTL_MS = 60000; // 60 seconds — GitHub Raw cold fetch adds 200-500ms; cache 60s for ultra-low stream latency

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers':
      'Content-Length, Content-Range, Accept-Ranges, Content-Type, X-Stream-Backend',
    'Access-Control-Max-Age': '86400',
  };
}

async function resolveLiveStreamBaseUrl(env) {
  const now = Date.now();
  if (cachedStreamBaseUrl && now - cachedAtEpoch < CACHE_TTL_MS) {
    return { primary: cachedStreamBaseUrl, fallback: cachedFallbackBaseUrl };
  }

  // 1. Fetch fresh stream_endpoint.json from GitHub Raw
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 2500);
    const resp = await fetch(`${GITHUB_ENDPOINT_JSON}?t=${now}`, {
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
          cachedAtEpoch = now;
        }
      }
      if (data && data.fallback_stream_url && typeof data.fallback_stream_url === 'string') {
        const cleanedFb = data.fallback_stream_url.trim().replace(/\/+$/, '');
        if (cleanedFb.startsWith('http')) {
          cachedFallbackBaseUrl = cleanedFb;
        }
      }
      if (cachedStreamBaseUrl) {
        return { primary: cachedStreamBaseUrl, fallback: cachedFallbackBaseUrl };
      }
    }
  } catch (err) {
    // Ignore and fall through to env/fallback
  }

  // 2. Fallback to Cloudflare env var if configured
  if (env && env.STREAM_BACKEND_URL) {
    cachedStreamBaseUrl = String(env.STREAM_BACKEND_URL).trim().replace(/\/+$/, '');
    cachedAtEpoch = now;
  }

  return { primary: cachedStreamBaseUrl, fallback: cachedFallbackBaseUrl };
}

export async function onRequest(context) {
  const { request, env, params } = context;
  const corsHeaders = buildCorsHeaders();

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response(JSON.stringify({ error: 'Method Not Allowed' }), {
      status: 405,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  // Extract chat_id and message_id from params.path or URL pathname
  let pathSegments = [];
  if (params && Array.isArray(params.path)) {
    pathSegments = params.path;
  } else if (params && typeof params.path === 'string') {
    pathSegments = params.path.split('/').filter(Boolean);
  } else {
    const urlObj = new URL(request.url);
    const m = urlObj.pathname.match(/\/stream\/channel\/([^/]+)\/(\d+)/);
    if (m) pathSegments = [m[1], m[2]];
  }

  if (pathSegments.length < 2) {
    return new Response(
      JSON.stringify({ error: 'Invalid stream path. Expected /stream/channel/:chat_id/:msg_id' }),
      {
        status: 400,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      }
    );
  }

  const chatId = pathSegments[0];
  const msgId = pathSegments[1];

  const { primary: baseUrl, fallback: fallbackUrl } = await resolveLiveStreamBaseUrl(env);
  if (!baseUrl && !fallbackUrl) {
    return new Response(
      JSON.stringify({ error: 'Stream backend endpoint not configured' }),
      {
        status: 503,
        headers: { ...corsHeaders, 'Content-Type': 'application/json' },
      }
    );
  }

  const urlsToTry = [baseUrl, fallbackUrl].filter(Boolean);
  let lastErr = null;
  let lastStatus = 503;

  for (const activeBase of urlsToTry) {
    const targetUrl = `${activeBase}/stream/channel/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}`;
    const rangeHeader = request.headers.get('Range');

    const upstreamHeaders = {
      'User-Agent': request.headers.get('User-Agent') || 'FilmSub-Edge-Proxy/2.0',
      'Accept': '*/*',
    };
    if (rangeHeader) {
      upstreamHeaders['Range'] = rangeHeader;
    }

    try {
      const upstreamRes = await fetch(targetUrl, {
        method: request.method,
        headers: upstreamHeaders,
        redirect: 'follow',
        signal: request.signal,
      });

      if (!upstreamRes.ok && upstreamRes.status !== 206) {
        if (activeBase === baseUrl) {
          cachedStreamBaseUrl = '';
          cachedAtEpoch = 0;
        }
        lastStatus = upstreamRes.status >= 500 ? 503 : upstreamRes.status;
        continue;
      }

      const outHeaders = new Headers(corsHeaders);
      outHeaders.set('Content-Type', upstreamRes.headers.get('Content-Type') || 'video/mp4');
      outHeaders.set('Accept-Ranges', 'bytes');
      outHeaders.set('X-Accel-Buffering', 'no');  // prevent Cloudflare proxy buffering → lower stream latency
      outHeaders.set('X-Stream-Backend', activeBase);

      const contentRange = upstreamRes.headers.get('Content-Range');
      if (contentRange) {
        outHeaders.set('Content-Range', contentRange);
      }
      const contentLength = upstreamRes.headers.get('Content-Length');
      if (contentLength) {
        outHeaders.set('Content-Length', contentLength);
      }
      const cacheControl = upstreamRes.headers.get('Cache-Control');
      outHeaders.set(
        'Cache-Control',
        cacheControl || 'public, max-age=3600, stale-while-revalidate=86400'
      );

      return new Response(request.method === 'HEAD' ? null : upstreamRes.body, {
        status: upstreamRes.status,
        headers: outHeaders,
      });
    } catch (err) {
      if (activeBase === baseUrl) {
        cachedStreamBaseUrl = '';
        cachedAtEpoch = 0;
      }
      lastErr = err;
      continue;
    }
  }

  return new Response(
    JSON.stringify({
      error: 'Upstream stream server unavailable',
      upstream_status: lastStatus,
      detail: lastErr ? String((lastErr && lastErr.message) || lastErr) : undefined,
    }),
    {
      status: 503,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    }
  );
}
