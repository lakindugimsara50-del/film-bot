/**
 * Cloudflare Pages Function: /api/stream
 * ============================================================
 * High-Speed Byte-Range Chunk Streaming Proxy for Google Drive
 * Supports:
 *  - True Quality-Adaptive Byte-Range Chunking (5MB - 10MB HTTP 206 chunks)
 *  - Instant MP4 header & tail moov-atom seeking for both +faststart and tail-moov MP4s
 *  - Automatic Virus-Scan Warning Bypass (drive.usercontent.google.com + confirm=t + uuid parsing)
 *  - Client AbortSignal propagation to prevent upstream Google Drive bandwidth leaks
 *  - Full CORS & Accept-Ranges headers for Video.js / HTML5 <video>
 */

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Type, X-Stream-Quality, X-Chunk-Size',
    'Access-Control-Max-Age': '86400',
  };
}

function extractDriveId(rawIdOrUrl) {
  if (!rawIdOrUrl) return '';
  const str = String(rawIdOrUrl).trim();
  if (/^[a-zA-Z0-9_-]{15,60}$/.test(str)) return str;
  const m1 = str.match(/\/d\/([a-zA-Z0-9_-]{15,60})/);
  if (m1) return m1[1];
  const m2 = str.match(/[?&]id=([a-zA-Z0-9_-]{15,60})/);
  if (m2) return m2[1];
  return '';
}

function getChunkSizeForQuality(quality) {
  const q = String(quality || 'auto').toLowerCase();
  // Minimum 5 MB chunk ensures even large 4.5 MB tail moov atoms (e.g. 1.5 GB MP4s)
  // are fetched in a single HTTP 206 round-trip during initial metadata probing.
  if (q === '360p') return 5 * 1024 * 1024;   // 5 MB
  if (q === '480p') return 6 * 1024 * 1024;   // 6 MB
  if (q === '720p') return 8 * 1024 * 1024;   // 8 MB
  return 10 * 1024 * 1024;                    // 10 MB for 1080p / auto
}

function normalizeRangeHeader(rawRange, quality, method) {
  if (method === 'HEAD' && !rawRange) {
    return { range: 'bytes=0-0', isHeadProbe: true, chunkSize: 1 };
  }
  const chunkSize = getChunkSizeForQuality(quality);
  const maxChunk = 16 * 1024 * 1024;

  if (!rawRange || typeof rawRange !== 'string' || !rawRange.startsWith('bytes=')) {
    return { range: `bytes=0-${chunkSize - 1}`, isHeadProbe: false, chunkSize };
  }

  const match = rawRange.match(/^bytes=(\d+)-(\d*)$/);
  if (!match) {
    return { range: rawRange, isHeadProbe: false, chunkSize };
  }

  const start = parseInt(match[1], 10);
  if (Number.isNaN(start) || start < 0) {
    return { range: `bytes=0-${chunkSize - 1}`, isHeadProbe: false, chunkSize };
  }

  if (match[2] !== '') {
    const requestedEnd = parseInt(match[2], 10);
    if (!Number.isNaN(requestedEnd) && requestedEnd >= start) {
      const cappedEnd = Math.min(requestedEnd, start + maxChunk - 1);
      return { range: `bytes=${start}-${cappedEnd}`, isHeadProbe: false, chunkSize: cappedEnd - start + 1 };
    }
  }

  const end = start + chunkSize - 1;
  return { range: `bytes=${start}-${end}`, isHeadProbe: false, chunkSize };
}

async function fetchDriveStream(fileId, rangeHeader, signal) {
  const baseDownloadUrl = `https://drive.usercontent.google.com/download?id=${encodeURIComponent(fileId)}&export=download&confirm=t&authuser=0`;
  const reqHeaders = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://drive.google.com/',
  };
  if (rangeHeader) {
    reqHeaders['Range'] = rangeHeader;
  }

  const fetchOpts = {
    method: 'GET',
    headers: reqHeaders,
    redirect: 'follow',
  };
  if (signal) fetchOpts.signal = signal;

  let res = await fetch(baseDownloadUrl, fetchOpts);

  const contentType = (res.headers.get('Content-Type') || '').toLowerCase();
  if (contentType.includes('text/html')) {
    const html = await res.text();
    const uuidMatch = html.match(/name="uuid"\s+value="([^"]+)"/i) || html.match(/[?&]uuid=([^&"']+)/i);
    const confirmMatch = html.match(/name="confirm"\s+value="([^"]+)"/i) || html.match(/[?&]confirm=([^&"']+)/i);
    const atMatch = html.match(/name="at"\s+value="([^"]+)"/i);

    const confirmVal = confirmMatch ? confirmMatch[1] : 't';
    let retryUrl = `https://drive.usercontent.google.com/download?id=${encodeURIComponent(fileId)}&export=download&confirm=${encodeURIComponent(confirmVal)}`;
    if (uuidMatch) retryUrl += `&uuid=${encodeURIComponent(uuidMatch[1])}`;
    if (atMatch) retryUrl += `&at=${encodeURIComponent(atMatch[1])}`;

    const setCookie = res.headers.get('Set-Cookie');
    if (setCookie) reqHeaders['Cookie'] = setCookie;

    res = await fetch(retryUrl, fetchOpts);
  }

  return res;
}

export async function onRequest(context) {
  const { request } = context;
  const cors = buildCorsHeaders();
  const method = request.method.toUpperCase();

  if (method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: cors });
  }

  if (method !== 'GET' && method !== 'HEAD') {
    return new Response(JSON.stringify({ error: 'Method not allowed' }), {
      status: 405,
      headers: { 'Content-Type': 'application/json', ...cors },
    });
  }

  const url = new URL(request.url);
  const rawId = url.searchParams.get('id') || url.searchParams.get('url') || '';
  const quality = url.searchParams.get('q') || 'auto';
  const fileId = extractDriveId(rawId);

  if (!fileId) {
    return new Response(JSON.stringify({ error: 'Missing or invalid Google Drive file ID (?id=...)' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', ...cors },
    });
  }

  const rawClientRange = request.headers.get('Range');
  const { range: effectiveRange, isHeadProbe, chunkSize } = normalizeRangeHeader(rawClientRange, quality, method);

  try {
    const upstream = await fetchDriveStream(fileId, effectiveRange, request.signal);
    const upContentType = (upstream.headers.get('Content-Type') || '').toLowerCase();

    if (upContentType.includes('text/html') || (!upstream.ok && upstream.status !== 206)) {
      return new Response(JSON.stringify({
        error: 'Upstream Google Drive stream returned non-video response',
        status: upstream.status,
        contentType: upContentType,
      }), {
        status: upstream.status >= 400 ? upstream.status : 502,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    }

    const respHeaders = new Headers(cors);
    respHeaders.set('Content-Type', 'video/mp4');
    respHeaders.set('Accept-Ranges', 'bytes');
    respHeaders.set('X-Stream-Quality', String(quality));
    respHeaders.set('X-Chunk-Size', String(chunkSize));
    respHeaders.set('Cache-Control', 'public, max-age=3600');
    respHeaders.set('X-Content-Type-Options', 'nosniff');

    const contentRange = upstream.headers.get('Content-Range');
    const contentLength = upstream.headers.get('Content-Length');

    if (isHeadProbe) {
      if (upstream.body && typeof upstream.body.cancel === 'function') {
        try { await upstream.body.cancel(); } catch (e) {}
      }
      const totalMatch = contentRange && contentRange.match(/\/(\d+)$/);
      if (totalMatch) {
        respHeaders.set('Content-Length', totalMatch[1]);
      } else if (contentLength) {
        respHeaders.set('Content-Length', contentLength);
      }
      return new Response(null, { status: 200, headers: respHeaders });
    }

    if (contentRange) respHeaders.set('Content-Range', contentRange);
    if (contentLength) respHeaders.set('Content-Length', contentLength);

    if (method === 'HEAD') {
      if (upstream.body && typeof upstream.body.cancel === 'function') {
        try { await upstream.body.cancel(); } catch (e) {}
      }
      return new Response(null, {
        status: upstream.status === 206 || contentRange ? 206 : 200,
        headers: respHeaders,
      });
    }

    return new Response(upstream.body, {
      status: upstream.status === 206 || contentRange ? 206 : 200,
      headers: respHeaders,
    });
  } catch (err) {
    return new Response(JSON.stringify({
      error: 'Stream proxy failure',
      detail: String(err && err.message ? err.message : err),
    }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', ...cors },
    });
  }
}
