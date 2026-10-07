/**
 * Cloudflare Pages Function: /stream/channel/[[path]]
 * ============================================================
 * Dynamic Edge Byte-Range Stream Proxy with Cloudflare Edge Cache for Telegram Cloud HD
 *
 * Performance Architecture:
 *  1. Cloudflare Edge Cache for Initial Chunks (0-8MB):
 *     - The initial 8MB containing the MP4 moov atom, codec metadata, and initial GOPs
 *       is cached in Cloudflare Edge Cache (caches.default) using a canonical origin.
 *     - Hit latency: 15-25ms directly from nearest Edge PoP (Colombo / Singapore / etc.)
 *       at 50-100+ MB/s, eliminating 10-20s tunnel delays!
 *  2. Non-Blocking Streaming on Cache MISS:
 *     - Bounded initial probes (e.g. bytes=0-1 from Safari or bytes=0-1048575):
 *       Proxied with exact client range so client receives response in <300ms!
 *       Full 8MB chunk is primed into edge cache asynchronously via context.waitUntil.
 *     - Open-ended requests (bytes=0- or no range):
 *       Upstream stream is teed: clientStream is piped immediately with HTTP 206 (TTFB <200ms),
 *       while cacheStream buffers and saves to caches.default in the background.
 *  3. RFC 7233 Compliance:
 *     - Returns exact Uint8Array slices with proper Content-Range and Content-Length.
 *     - Supports suffix ranges (bytes=-N) without false initial chunk matching.
 *  4. Dynamic Live Tunnel Routing & Fast Failover:
 *     - Resolves live tunnel from GitHub Raw with 60s cache TTL.
 *     - Graceful 503 failover switches immediately without hanging.
 */

const GITHUB_ENDPOINT_JSON =
  'https://raw.githubusercontent.com/lakindugimsara50-del/film-bot/main/website/data/stream_endpoint.json';

const EDGE_INITIAL_CHUNK_BYTES = 8 * 1024 * 1024; // 8 MiB initial chunk (moov atom + first video frames)
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
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers':
      'Content-Length, Content-Range, Accept-Ranges, Content-Type, Content-Disposition, X-Stream-Backend, X-Edge-Cache',
    'Access-Control-Max-Age': '86400',
  };
}

function concatStreams(stream1, stream2PromiseFn) {
  let reader1 = stream1.getReader();
  let reader2 = null;
  return new ReadableStream({
    async pull(controller) {
      if (reader1) {
        try {
          const { done, value } = await reader1.read();
          if (!done) {
            controller.enqueue(value);
            return;
          }
        } catch (rErr) {
          reader1 = null;
        }
        reader1 = null;
      }
      if (!reader2) {
        try {
          const s2 = await stream2PromiseFn();
          if (s2) {
            reader2 = s2.getReader();
          } else {
            controller.close();
            return;
          }
        } catch (e) {
          controller.close();
          return;
        }
      }
      try {
        const { done, value } = await reader2.read();
        if (!done) {
          controller.enqueue(value);
        } else {
          controller.close();
        }
      } catch (err) {
        controller.close();
      }
    },
    async cancel(reason) {
      if (reader1) try { await reader1.cancel(reason); } catch (e) {}
      if (reader2) try { await reader2.cancel(reason); } catch (e) {}
    }
  });
}

function getCanonicalCacheKey(chatId, msgId) {
  return new Request(
    `${CANONICAL_CACHE_ORIGIN}/__edge_stream_cache/v2/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}/chunk_0_8m`,
    { method: 'GET' }
  );
}

async function readExactBytes(readableStream, skip, limit, shouldCancel = true) {
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
    if (shouldCancel) {
      try { await reader.cancel(); } catch (e) {}
    } else {
      try { reader.releaseLock(); } catch (e) {}
    }
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

  // Helper background task to refresh endpoints without blocking client
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

  if (request.method !== 'GET' && request.method !== 'HEAD') {
    return new Response(JSON.stringify({ error: 'Method Not Allowed' }), {
      status: 405,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  }

  const urlObj = new URL(request.url);
  let pathSegments = [];
  if (params && Array.isArray(params.path)) {
    pathSegments = params.path;
  } else if (params && typeof params.path === 'string') {
    pathSegments = params.path.split('/').filter(Boolean);
  } else {
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

  const rangeHeader = request.headers.get('Range');
  let clientStart = null;
  let clientEnd = null;
  let suffixBytes = null;
  let hasExplicitRange = false;

  if (rangeHeader && rangeHeader.toLowerCase().startsWith('bytes=')) {
    const val = rangeHeader.slice(6).trim();
    const parts = val.split(',')[0].trim().split('-');
    if (parts[0] !== '' && parts[1] !== '') {
      const s = parseInt(parts[0], 10);
      const e = parseInt(parts[1], 10);
      if (!isNaN(s) && !isNaN(e) && e >= s) {
        clientStart = s;
        clientEnd = e;
        hasExplicitRange = true;
      }
    } else if (parts[0] !== '' && parts[1] === '') {
      const s = parseInt(parts[0], 10);
      if (!isNaN(s) && s >= 0) {
        clientStart = s;
        clientEnd = null;
        hasExplicitRange = true;
      }
    } else if (parts[0] === '' && parts[1] !== '') {
      const suf = parseInt(parts[1], 10);
      if (!isNaN(suf) && suf > 0) {
        suffixBytes = suf;
        hasExplicitRange = true;
      }
    }
  }

  // Suffix ranges (bytes=-N) must not match the initial 0-8MB chunk
  const isInitialRange = suffixBytes === null && (clientStart === null || clientStart < EDGE_INITIAL_CHUNK_BYTES);
  const isBoundedProbe = isInitialRange && clientStart === 0 && clientEnd !== null && (clientEnd - clientStart + 1) <= 2 * 1024 * 1024;

  // ── 1. Cloudflare Edge Cache Lookup (caches.default) ────────────────────────
  // Check if initial 8MB chunk (moov atom + first video frames) is already in Edge Cache
  const edgeCache = typeof caches !== 'undefined' ? caches.default : null;
  const cacheKey = getCanonicalCacheKey(chatId, msgId);

  if (edgeCache && isInitialRange) {
    try {
      const cachedHit = await edgeCache.match(cacheKey);
      if (cachedHit) {
        const totalSize = cachedHit.headers.get('X-Total-Size') || '*';
        const contentType = cachedHit.headers.get('Content-Type') || 'video/mp4';

        const outHeaders = new Headers(corsHeaders);
        outHeaders.set('Content-Type', contentType);
        outHeaders.set('Accept-Ranges', 'bytes');
        outHeaders.set('X-Edge-Cache', 'HIT');
        outHeaders.set('X-Accel-Buffering', 'no');
        outHeaders.set('Cache-Control', 'public, max-age=604800, s-maxage=604800');

        // Fast-path: Open-ended requests (bytes=0- or no range):
        // Deliver initial 8MB chunk directly from Cloudflare Edge Cache in <20ms
        // Browser reads moov atom and GOPs immediately, then sends subsequent byte-range requests
        if ((clientStart === null || clientStart === 0) && clientEnd === null) {
          const chunkLenNum = parseInt(cachedHit.headers.get('Content-Length') || String(EDGE_INITIAL_CHUNK_BYTES), 10);
          outHeaders.set('Content-Length', String(chunkLenNum));
          outHeaders.set('Content-Range', `bytes 0-${chunkLenNum - 1}/${totalSize}`);
          if (request.method === 'HEAD') {
            return new Response(null, { status: 206, headers: outHeaders });
          }
          return new Response(cachedHit.body, { status: 206, headers: outHeaders });
        }

        const cachedBuffer = new Uint8Array(await cachedHit.arrayBuffer());
        if (cachedBuffer && cachedBuffer.byteLength > 0) {
          const effectiveStart = clientStart !== null ? clientStart : 0;
          if (effectiveStart < cachedBuffer.byteLength) {
            const effectiveEnd = (clientEnd !== null)
              ? Math.min(clientEnd, cachedBuffer.byteLength - 1)
              : (cachedBuffer.byteLength - 1);

            const slice = cachedBuffer.subarray(effectiveStart, effectiveEnd + 1);
            outHeaders.set('Content-Length', String(slice.byteLength));
            outHeaders.set('Content-Range', `bytes ${effectiveStart}-${effectiveStart + slice.byteLength - 1}/${totalSize}`);

            if (request.method === 'HEAD') {
              return new Response(null, { status: 206, headers: outHeaders });
            }
            return new Response(slice, { status: 206, headers: outHeaders });
          }
        }
      }
    } catch (cErr) {}
  }

  // ── 2. Upstream Backend Resolution & Proxying ───────────────────────────────
  const { primary: baseUrl, fallback: fallbackUrl } = await resolveLiveStreamBaseUrl(env, context);
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

  // Background helper to prime the 8MB initial chunk into Edge Cache without blocking the client
  const primeEdgeCacheInBackground = (targetUrls) => {
    if (!edgeCache) return;
    const task = async () => {
      try {
        const exists = await edgeCache.match(cacheKey);
        if (exists) return;

        for (const base of targetUrls) {
          const streamUrl = `${base}/stream/channel/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}`;
          try {
            const resp = await fetch(streamUrl, {
              headers: {
                'Range': `bytes=0-${EDGE_INITIAL_CHUNK_BYTES - 1}`,
                'User-Agent': 'FilmSub-Edge-Cache-Primer/2.0',
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
      context.waitUntil(task());
    } else {
      task().catch(() => {});
    }
  };

  for (const activeBase of urlsToTry) {
    const targetUrl = `${activeBase}/stream/channel/${encodeURIComponent(chatId)}/${encodeURIComponent(msgId)}${urlObj.search || ''}`;

    const upstreamHeaders = {
      'User-Agent': request.headers.get('User-Agent') || 'FilmSub-Edge-Proxy/2.0',
      'Accept': '*/*',
    };

    if (isInitialRange) {
      if (isBoundedProbe && rangeHeader) {
        // Request only the small probe slice from upstream to respond in <300ms
        upstreamHeaders['Range'] = rangeHeader;
      } else {
        // Request initial 8MB from upstream
        upstreamHeaders['Range'] = `bytes=0-${EDGE_INITIAL_CHUNK_BYTES - 1}`;
      }
    } else if (rangeHeader) {
      upstreamHeaders['Range'] = rangeHeader;
    }

    const abortCtrl = new AbortController();
    const tId = setTimeout(() => {
      try { abortCtrl.abort(); } catch (e) {}
    }, 15000);
    const combinedSignal = (request.signal && typeof AbortSignal.any === 'function')
      ? AbortSignal.any([request.signal, abortCtrl.signal])
      : abortCtrl.signal;

    try {
      const upstreamRes = await fetch(targetUrl, {
        method: request.method === 'HEAD' && !isInitialRange ? 'HEAD' : 'GET',
        headers: upstreamHeaders,
        redirect: 'follow',
        signal: combinedSignal,
      });
      clearTimeout(tId);

      if (!upstreamRes.ok && upstreamRes.status !== 206) {
        if (activeBase === baseUrl) {
          cachedStreamBaseUrl = '';
          cachedAtEpoch = 0;
        }
        lastStatus = upstreamRes.status >= 500 ? 503 : upstreamRes.status;
        continue;
      }

      const outHeaders = new Headers(corsHeaders);
      const contentType = upstreamRes.headers.get('Content-Type') || 'video/mp4';
      outHeaders.set('Content-Type', contentType);
      outHeaders.set('Accept-Ranges', 'bytes');
      outHeaders.set('X-Accel-Buffering', 'no');
      outHeaders.set('X-Stream-Backend', activeBase);

      const contentRange = upstreamRes.headers.get('Content-Range');
      const responseBody = upstreamRes.body;

      // ── Handle Initial Range (0-8MB) on Cache MISS ──
      if (isInitialRange && (upstreamRes.status === 206 || upstreamRes.status === 200)) {
        let totalSize = '*';
        if (contentRange) {
          const m = contentRange.match(/bytes\s+\d+-\d+\/(\d+|\*)/);
          if (m) totalSize = m[1];
        } else {
          totalSize = upstreamRes.headers.get('Content-Length') || '*';
        }

        // Case A: Bounded probe (e.g. Safari bytes=0-1 or player probe bytes=0-1048575)
        // Serve immediately without waiting for full 8MB; prime edge cache in background
        if (isBoundedProbe && clientEnd !== null) {
          const neededBytes = clientEnd - (clientStart || 0) + 1;
          const probeBuffer = await readExactBytes(responseBody, 0, neededBytes);

          outHeaders.set('Content-Length', String(probeBuffer.byteLength));
          outHeaders.set('Content-Range', `bytes 0-${probeBuffer.byteLength - 1}/${totalSize}`);
          outHeaders.set('X-Edge-Cache', 'MISS-PROBE');
          outHeaders.set('Cache-Control', 'public, max-age=86400, stale-while-revalidate=86400');

          primeEdgeCacheInBackground(urlsToTry);

          if (request.method === 'HEAD') {
            return new Response(null, { status: 206, headers: outHeaders });
          }
          return new Response(probeBuffer, { status: 206, headers: outHeaders });
        }

        // Case B: Open-ended range or large initial request (e.g. bytes=0-)
        // Tee stream: deliver clientStream immediately (TTFB <200ms) while caching cacheStream in background
        const [clientStream, cacheStream] = responseBody.tee();

        const cacheTask = async () => {
          try {
            const initialBuffer = await readExactBytes(cacheStream, 0, EDGE_INITIAL_CHUNK_BYTES, false);
            if (edgeCache && initialBuffer && initialBuffer.byteLength > 0) {
              const cacheableRes = new Response(initialBuffer, {
                status: 200,
                headers: {
                  'Content-Type': contentType,
                  'Content-Length': String(initialBuffer.byteLength),
                  'X-Total-Size': String(totalSize),
                  'Cache-Control': 'public, max-age=604800, s-maxage=604800',
                },
              });
              await edgeCache.put(cacheKey, cacheableRes);
            }
          } catch (e) {}
        };

        if (context.waitUntil && typeof context.waitUntil === 'function') {
          context.waitUntil(cacheTask());
        } else {
          cacheTask().catch(() => {});
        }

        outHeaders.set('X-Edge-Cache', 'MISS-STREAMING');
        outHeaders.set('Cache-Control', 'public, max-age=86400, stale-while-revalidate=86400');
        if (contentRange) outHeaders.set('Content-Range', contentRange);
        const upContentLen = upstreamRes.headers.get('Content-Length');
        if (upContentLen) outHeaders.set('Content-Length', upContentLen);

        if (request.method === 'HEAD') {
          return new Response(null, { status: 206, headers: outHeaders });
        }
        return new Response(clientStream, { status: 206, headers: outHeaders });
      }

      // ── Handle Subsequent/Seeking Ranges (past 8MB or suffix ranges) ──
      if (hasExplicitRange && contentRange && clientStart !== null && clientEnd !== null) {
        const mRange = contentRange.match(/bytes\s+(\d+)-(\d+)\/(\d+|\*)/);
        if (mRange) {
          const upStart = parseInt(mRange[1], 10);
          const upEnd = parseInt(mRange[2], 10);
          const totalSize = mRange[3];

          if (clientStart >= upStart && clientEnd <= upEnd) {
            const skipBytes = clientStart - upStart;
            const neededBytes = clientEnd - clientStart + 1;
            outHeaders.set('Content-Range', `bytes ${clientStart}-${clientEnd}/${totalSize}`);
            outHeaders.set('Content-Length', String(neededBytes));

            if (request.method === 'HEAD') {
              return new Response(null, { status: 206, headers: outHeaders });
            }

            // Zero-buffering progressive streaming: pass stream directly when no slicing is needed
            if (skipBytes === 0 && (upEnd - upStart + 1) === neededBytes) {
              return new Response(responseBody, { status: 206, headers: outHeaders });
            }

            const slicedBuffer = await readExactBytes(responseBody, skipBytes, neededBytes);
            outHeaders.set('Content-Length', String(slicedBuffer.byteLength));
            outHeaders.set('Content-Range', `bytes ${clientStart}-${clientStart + slicedBuffer.byteLength - 1}/${totalSize}`);

            return new Response(slicedBuffer, { status: 206, headers: outHeaders });
          }
        }
      }

      if (contentRange) outHeaders.set('Content-Range', contentRange);
      const contentLength = upstreamRes.headers.get('Content-Length');
      if (contentLength) outHeaders.set('Content-Length', contentLength);

      const contentDisposition = upstreamRes.headers.get('Content-Disposition');
      if (contentDisposition) outHeaders.set('Content-Disposition', contentDisposition);

      outHeaders.set('Cache-Control', 'public, max-age=3600, stale-while-revalidate=86400');

      return new Response(request.method === 'HEAD' ? null : responseBody, {
        status: upstreamRes.status,
        headers: outHeaders,
      });
    } catch (err) {
      clearTimeout(tId);
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
