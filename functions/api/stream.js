/**
 * Cloudflare Pages Function: /api/stream
 * ============================================================
 * High-Speed Byte-Range Chunk Streaming Proxy for Google Drive
 * Supports:
 *  - Native HTTP 206 Partial Content passthrough (zero-copy ReadableStream)
 *  - Instant MP4 header & tail moov-atom seeking for both +faststart and tail-moov MP4s
 *  - Automatic Virus-Scan Warning Bypass (drive.usercontent.google.com + confirm=t + uuid parsing)
 *  - Full CORS & Accept-Ranges headers for Video.js / HTML5 <video>
 */

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Type, X-Stream-Quality',
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

async function fetchDriveStream(fileId, rangeHeader, method) {
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

  let res = await fetch(baseDownloadUrl, {
    method: method === 'HEAD' ? 'GET' : method,
    headers: reqHeaders,
    redirect: 'follow',
  });

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

    res = await fetch(retryUrl, {
      method: method === 'HEAD' ? 'GET' : method,
      headers: reqHeaders,
      redirect: 'follow',
    });
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

  const clientRange = request.headers.get('Range') || 'bytes=0-';

  try {
    const upstream = await fetchDriveStream(fileId, clientRange, method);
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
    respHeaders.set('Cache-Control', 'public, max-age=3600');
    respHeaders.set('X-Content-Type-Options', 'nosniff');

    const contentRange = upstream.headers.get('Content-Range');
    if (contentRange) respHeaders.set('Content-Range', contentRange);

    const contentLength = upstream.headers.get('Content-Length');
    if (contentLength) respHeaders.set('Content-Length', contentLength);

    return new Response(method === 'HEAD' ? null : upstream.body, {
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
