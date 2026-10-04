/**
 * Cloudflare Pages Function: /stream/ping
 * Checks live backend health from Cloudflare edge proxy
 */

const GITHUB_ENDPOINT_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json';

let cachedStreamBaseUrl = '';
let cachedFallbackBaseUrl = '';
let cachedAtEpoch = 0;
const CACHE_TTL_MS = 15000;

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Type, X-Stream-Backend',
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
    const timer = setTimeout(() => ctrl.abort(), 2000);
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
  const { request, env } = context;
  const corsHeaders = buildCorsHeaders();

  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  const { primary, fallback } = await resolveLiveStreamBaseUrl(env);
  const targetBackend = primary || fallback || 'https://film-bot-2.onrender.com';

  let backendReady = false;
  if (targetBackend && targetBackend.startsWith('http')) {
    try {
      const probeCtrl = new AbortController();
      const probeTimer = setTimeout(() => probeCtrl.abort(), 1800);
      const probeResp = await fetch(`${targetBackend}/health?t=${Date.now()}`, {
        method: 'GET',
        headers: { 'User-Agent': 'FilmSub-Edge-Proxy/2.0' },
        signal: probeCtrl.signal,
      });
      clearTimeout(probeTimer);
      if (probeResp.ok) {
        backendReady = true;
      }
    } catch (e) {
      backendReady = false;
    }
  }

  return new Response(JSON.stringify({
    status: 'pong',
    service: 'filmsub_edge_stream',
    mode: 'telegram_cloud',
    ready: backendReady,
    backend: targetBackend
  }), {
    status: 200,
    headers: {
      ...corsHeaders,
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store, no-cache',
    },
  });
}
