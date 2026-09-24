/**
 * Cloudflare Pages Function: /api/download
 * ============================================================
 * One-Click Direct Video Download Proxy for FilmSub
 *
 * Eliminates Google Drive's "Google Drive can't scan this file for viruses / OK"
 * confirmation screen by resolving the confirmation token (`confirm=t`, `uuid`, `at`)
 * at the Cloudflare Edge and streaming the binary MP4 directly to the user's browser
 * with `Content-Disposition: attachment; filename="..."`.
 *
 * Supports:
 *  - Instant browser native file download with exact Content-Length progress bar
 *  - Full HTTP Range / 206 Partial Content support (pause/resume & IDM acceleration)
 *  - Distinct per-quality Drive IDs (1080p, 720p, 480p, 360p)
 *  - Automatic fallback for direct HTTPS video URLs
 */

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Type, Content-Disposition, X-Download-Quality',
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

function buildCleanFilename(title, quality, customFilename) {
  if (customFilename) {
    const cleanedCustom = String(customFilename).replace(/[<>:"/\\|?*\x00-\x1F]/g, '').trim();
    if (cleanedCustom) {
      return cleanedCustom.toLowerCase().endsWith('.mp4') ? cleanedCustom : `${cleanedCustom}.mp4`;
    }
  }
  const safeTitle = String(title || 'Movie')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '')
    .replace(/\s+/g, ' ')
    .trim() || 'Movie';
  const safeQuality = String(quality || '1080p')
    .replace(/[^a-zA-Z0-9]/g, '')
    .trim() || '1080p';
  return `${safeTitle} [${safeQuality}] - FilmSub.mp4`;
}

async function fetchDriveBinaryStream(fileId, rangeHeader, signal) {
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

  // If Google Drive returns the Virus Scan Warning HTML page, extract tokens & bypass automatically
  if (contentType.includes('text/html')) {
    const html = await res.text();
    const formActionMatch = html.match(/<form[^>]+id="download-form"[^>]+action="([^"]+)"/i) ||
                            html.match(/<form[^>]+action="(https:\/\/drive\.usercontent\.google\.com\/download[^"]*)"/i);
    const uuidMatch = html.match(/name="uuid"\s+value="([^"]+)"/i) || html.match(/[?&]uuid=([^&"']+)/i);
    const confirmMatch = html.match(/name="confirm"\s+value="([^"]+)"/i) || html.match(/[?&]confirm=([^&"']+)/i);
    const atMatch = html.match(/name="at"\s+value="([^"]+)"/i);

    const actionBase = formActionMatch
      ? formActionMatch[1].replace(/&amp;/g, '&')
      : 'https://drive.usercontent.google.com/download';

    const retryUrlObj = new URL(actionBase);
    retryUrlObj.searchParams.set('id', fileId);
    retryUrlObj.searchParams.set('export', 'download');
    retryUrlObj.searchParams.set('confirm', confirmMatch ? confirmMatch[1] : 't');
    if (uuidMatch) retryUrlObj.searchParams.set('uuid', uuidMatch[1]);
    if (atMatch) retryUrlObj.searchParams.set('at', atMatch[1]);

    const setCookie = res.headers.get('Set-Cookie');
    if (setCookie) reqHeaders['Cookie'] = setCookie;

    res = await fetch(retryUrlObj.toString(), fetchOpts);
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
  const rawIdOrUrl = url.searchParams.get('id') || url.searchParams.get('url') || '';
  const quality = url.searchParams.get('q') || '1080p';
  const title = url.searchParams.get('title') || 'Movie';
  const customFilename = url.searchParams.get('filename') || '';

  const fileId = extractDriveId(rawIdOrUrl);
  const filename = buildCleanFilename(title, quality, customFilename);
  const asciiFilename = filename.replace(/[^\x20-\x7E]/g, '');
  const encodedFilename = encodeURIComponent(filename);

  const clientRange = request.headers.get('Range');

  try {
    let upstream;
    if (fileId) {
      upstream = await fetchDriveBinaryStream(fileId, clientRange, request.signal);
    } else if (/^https?:\/\//i.test(rawIdOrUrl)) {
      const reqHeaders = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
      };
      if (clientRange) reqHeaders['Range'] = clientRange;
      upstream = await fetch(rawIdOrUrl, {
        method: 'GET',
        headers: reqHeaders,
        redirect: 'follow',
        signal: request.signal,
      });
    } else {
      return new Response(JSON.stringify({ error: 'Missing or invalid download ID or URL (?id=...)' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    }

    const upContentType = (upstream.headers.get('Content-Type') || '').toLowerCase();
    if (upContentType.includes('text/html') || (!upstream.ok && upstream.status !== 206)) {
      return new Response(JSON.stringify({
        error: 'Upstream storage returned non-binary response',
        status: upstream.status,
        contentType: upContentType,
      }), {
        status: upstream.status >= 400 ? upstream.status : 502,
        headers: { 'Content-Type': 'application/json', ...cors },
      });
    }

    const respHeaders = new Headers(cors);
    respHeaders.set('Content-Type', 'video/mp4');
    respHeaders.set(
      'Content-Disposition',
      `attachment; filename="${asciiFilename || 'FilmSub-Movie.mp4'}"; filename*=UTF-8''${encodedFilename}`
    );
    respHeaders.set('Accept-Ranges', 'bytes');
    respHeaders.set('X-Download-Quality', String(quality));
    respHeaders.set('X-Content-Type-Options', 'nosniff');

    const contentRange = upstream.headers.get('Content-Range');
    const contentLength = upstream.headers.get('Content-Length');
    if (contentRange) respHeaders.set('Content-Range', contentRange);
    if (contentLength) respHeaders.set('Content-Length', contentLength);

    if (method === 'HEAD') {
      if (upstream.body && typeof upstream.body.cancel === 'function') {
        try { await upstream.body.cancel(); } catch (e) {}
      }
      return new Response(null, {
        status: upstream.status === 206 ? 206 : 200,
        headers: respHeaders,
      });
    }

    return new Response(upstream.body, {
      status: upstream.status === 206 ? 206 : 200,
      headers: respHeaders,
    });
  } catch (err) {
    return new Response(JSON.stringify({
      error: 'Direct download proxy failed',
      detail: String(err && err.message ? err.message : err),
    }), {
      status: 502,
      headers: { 'Content-Type': 'application/json', ...cors },
    });
  }
}
