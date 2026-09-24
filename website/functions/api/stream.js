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

// OAuth credentials are loaded exclusively from Cloudflare Pages environment variables.
// Set these in Cloudflare Dashboard → Pages → filmsub → Settings → Environment Variables:
//   GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN_1/2/3
const DEFAULT_OAUTH_CLIENT_ID = '';
const DEFAULT_OAUTH_CLIENT_SECRET = '';
const DEFAULT_REFRESH_TOKENS = [];

let _cachedAccessToken = '';
let _cachedTokenExpiry = 0;

async function getDriveAccessToken(env) {
  const now = Date.now();
  if (_cachedAccessToken && now < _cachedTokenExpiry - 60000) {
    return _cachedAccessToken;
  }
  const clientId = (env && env.GDRIVE_CLIENT_ID) || DEFAULT_OAUTH_CLIENT_ID;
  const clientSecret = (env && env.GDRIVE_CLIENT_SECRET) || DEFAULT_OAUTH_CLIENT_SECRET;
  const tokensToTry = [];
  for (const key of ['GDRIVE_REFRESH_TOKEN', 'GDRIVE_REFRESH_TOKEN_1', 'GDRIVE_REFRESH_TOKEN_2', 'GDRIVE_REFRESH_TOKEN_3']) {
    if (env && env[key] && !tokensToTry.includes(env[key])) tokensToTry.push(env[key]);
  }
  for (const rt of DEFAULT_REFRESH_TOKENS) {
    if (!tokensToTry.includes(rt)) tokensToTry.push(rt);
  }
  for (const refreshToken of tokensToTry) {
    try {
      const body = new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: refreshToken,
        grant_type: 'refresh_token',
      });
      const resp = await fetch('https://oauth2.googleapis.com/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data && data.access_token) {
          _cachedAccessToken = data.access_token;
          _cachedTokenExpiry = now + ((Number(data.expires_in) || 3500) * 1000);
          return _cachedAccessToken;
        }
      }
    } catch (e) {}
  }
  return '';
}

function parseFormUrlEncoded(str) {
  const out = {};
  if (!str) return out;
  for (const part of String(str).split('&')) {
    if (!part) continue;
    const eqIdx = part.indexOf('=');
    if (eqIdx === -1) {
      out[decodeURIComponent(part.replace(/\+/g, ' '))] = '';
    } else {
      const k = decodeURIComponent(part.slice(0, eqIdx).replace(/\+/g, ' '));
      const v = decodeURIComponent(part.slice(eqIdx + 1).replace(/\+/g, ' '));
      out[k] = v;
    }
  }
  return out;
}

async function fetchDriveTranscodeQualityStream(fileId, quality, rangeHeader, signal, env) {
  const qNorm = String(quality || '').toLowerCase().trim();
  if (!['720p', '480p', '360p'].includes(qNorm)) return null;

  const accessToken = await getDriveAccessToken(env);
  if (!accessToken) return null;

  const infoUrl = `https://drive.google.com/get_video_info?docid=${encodeURIComponent(fileId)}`;
  const infoRes = await fetch(infoUrl, {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${accessToken}`,
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    },
    signal,
  });
  if (!infoRes.ok) return null;

  const setCookie = infoRes.headers.get('Set-Cookie') || '';
  const driveStreamMatch = setCookie.match(/DRIVE_STREAM=[^;]+/);
  const cookieHeader = driveStreamMatch ? driveStreamMatch[0] : '';

  const text = await infoRes.text();
  const parsed = parseFormUrlEncoded(text);
  const fmtMap = parsed.url_encoded_fmt_stream_map || '';
  if (!fmtMap) return null;

  const streamsByItag = {};
  for (const entry of fmtMap.split(',')) {
    const ep = parseFormUrlEncoded(entry);
    if (ep.itag && ep.url) streamsByItag[String(ep.itag)] = ep.url;
  }

  let preferredItags = [];
  if (qNorm === '720p') preferredItags = ['22', '37', '59', '18'];
  else if (qNorm === '480p') preferredItags = streamsByItag['59'] ? ['59', '22', '18'] : ['22', '18', '37'];
  else if (qNorm === '360p') preferredItags = ['18', '59', '22'];

  let chosenUrl = '';
  for (const it of preferredItags) {
    if (streamsByItag[it]) {
      chosenUrl = streamsByItag[it];
      break;
    }
  }
  if (!chosenUrl) return null;

  const streamHeaders = {
    'Authorization': `Bearer ${accessToken}`,
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'Accept': '*/*',
  };
  if (cookieHeader) streamHeaders['Cookie'] = cookieHeader;
  if (rangeHeader) streamHeaders['Range'] = rangeHeader;

  const res = await fetch(chosenUrl, {
    method: 'GET',
    headers: streamHeaders,
    redirect: 'follow',
    signal,
  });
  if (res.ok || res.status === 206) return res;
  return null;
}

export async function onRequest(context) {
  const { request, env } = context;
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
  const isDownload = url.searchParams.get('download') === '1' || url.searchParams.get('dl') === '1';
  const isDedicated = url.searchParams.get('dedicated') === '1';
  const titleParam = url.searchParams.get('title') || 'Movie';
  const fileId = extractDriveId(rawId);

  if (!fileId) {
    return new Response(JSON.stringify({ error: 'Missing or invalid Google Drive file ID (?id=...)' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json', ...cors },
    });
  }

  const rawClientRange = request.headers.get('Range');
  const { range: effectiveRange, isHeadProbe, chunkSize } = isDownload
    ? { range: rawClientRange || '', isHeadProbe: false, chunkSize: 0 }
    : normalizeRangeHeader(rawClientRange, quality, method);

  try {
    let upstream = null;
    const qLower = String(quality).toLowerCase().trim();
    if (!isDedicated && ['720p', '480p', '360p'].includes(qLower)) {
      upstream = await fetchDriveTranscodeQualityStream(fileId, qLower, effectiveRange, request.signal, env);
    }
    if (!upstream) {
      upstream = await fetchDriveStream(fileId, effectiveRange, request.signal);
    }
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
    if (isDownload) {
      const safeTitle = String(titleParam).replace(/[<>:"/\\|?*\x00-\x1F]/g, '').trim() || 'Movie';
      const safeQ = String(quality === 'auto' ? '1080p' : quality).replace(/[^a-zA-Z0-9]/g, '') || '1080p';
      const dlName = `${safeTitle} [${safeQ}] - FilmSub.mp4`;
      respHeaders.set('Content-Disposition', `attachment; filename="${dlName.replace(/[^\x20-\x7E]/g, '')}"; filename*=UTF-8''${encodeURIComponent(dlName)}`);
    } else {
      respHeaders.set('X-Chunk-Size', String(chunkSize));
      respHeaders.set('Cache-Control', 'public, max-age=3600');
    }
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
