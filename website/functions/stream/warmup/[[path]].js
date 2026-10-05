/**
 * Cloudflare Pages Function: /stream/warmup/[[path]]
 * ============================================================
 * Edge Warmup Proxy for Telegram Cloud HD
 * Proxies warmup trigger calls to the live stream server backend
 */

const GITHUB_ENDPOINT_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json';

let cachedStreamBaseUrl = '';
let cachedFallbackBaseUrl = '';
let cachedAtEpoch = 0;
const CACHE_TTL_MS = 60000;

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Type, X-Stream-Backend',
    'Access-Control-Max-Age': '86400',
  };
}

async function resolveLiveStreamBaseUrl(env) {
  const now = Date.now();
  if (cachedStreamBaseUrl && now - cachedAtEpoch < CACHE_TTL_MS) {
    return { primary: cachedStreamBaseUrl, fallback: cachedFallbackBaseUrl };
  }

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
  } catch (err) {}

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

  const { primary: baseUrl, fallback: fallbackUrl } = await resolveLiveStreamBaseUrl(env);
  const urlsToTry = [baseUrl, fallbackUrl].filter(Boolean);

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
          headers: { ...corsHeaders, 'Content-Type': 'application/json', 'X-Stream-Backend': activeBase },
        });
      }
    } catch (err) {}
  }

  // Graceful response even if backend is offline so frontend never errors
  return new Response(
    JSON.stringify({ status: 'warming_fallback', chat_id: chatId, message_id: Number(msgId) }),
    {
      status: 200,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    }
  );
}
