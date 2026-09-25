/**
 * Cloudflare Pages Function: /api/download
 * ============================================================
 * One-Click Multi-Quality Direct Video Download Proxy for FilmSub
 *
 * 1. Eliminates Google Drive's "Google Drive can't scan this file for viruses / OK"
 *    confirmation screen by resolving confirmation tokens (`confirm=t`, `uuid`, `at`)
 *    at the Cloudflare Edge and streaming the binary MP4 directly to the browser
 *    with `Content-Disposition: attachment; filename="..."`.
 * 2. Resolves TRUE distinct multi-quality MP4 streams (`1080p`, `720p`, `480p`, `360p`)
 *    with distinct smaller byte sizes (`Content-Length`):
 *    - If a dedicated per-quality Google Drive file ID is provided (`dedicated=1`),
 *      streams that dedicated file directly.
 *    - If a single Google Drive file ID is used across quality buttons, queries
 *      Google Drive's authenticated `get_video_info` transcode endpoint (`itag=37`,
 *      `itag=59`, `itag=22`, `itag=18`) so `720p`, `480p`, and `360p` download genuine
 *      lower-resolution, smaller-byte-size MP4 files instead of the 1.4 GB 1080p master!
 */

// OAuth credentials are loaded exclusively from Cloudflare Pages environment variables.
// Set these in Cloudflare Dashboard → Pages → filmsub → Settings → Environment Variables:
//   GDRIVE_CLIENT_ID        = your Google OAuth2 client ID
//   GDRIVE_CLIENT_SECRET    = your Google OAuth2 client secret
//   GDRIVE_REFRESH_TOKEN_1  = refresh token #1 (required for authenticated transcode downloads)
//   GDRIVE_REFRESH_TOKEN_2  = refresh token #2 (optional extra account)
//   GDRIVE_REFRESH_TOKEN_3  = refresh token #3 (optional extra account)
// Without these, the proxy falls back to unauthenticated Google Drive download (no transcode).
const DEFAULT_OAUTH_CLIENT_ID = '';
const DEFAULT_OAUTH_CLIENT_SECRET = '';
const DEFAULT_REFRESH_TOKENS = [];

let _cachedAccessToken = '';
let _cachedTokenExpiry = 0;

function buildCorsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Range, Content-Type, Accept, Origin',
    'Access-Control-Expose-Headers': 'Content-Length, Content-Range, Accept-Ranges, Content-Type, Content-Disposition, X-Download-Quality, X-Download-Itag',
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

async function getDriveAccessToken(env) {
  const now = Date.now();
  if (_cachedAccessToken && now < _cachedTokenExpiry - 60000) {
    return _cachedAccessToken;
  }

  const clientId = (env && env.GDRIVE_CLIENT_ID) || DEFAULT_OAUTH_CLIENT_ID;
  const clientSecret = (env && env.GDRIVE_CLIENT_SECRET) || DEFAULT_OAUTH_CLIENT_SECRET;
  const tokensToTry = [];
  // Support GDRIVE_REFRESH_TOKEN (single) or GDRIVE_REFRESH_TOKEN_1/2/3 (multiple accounts)
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
    } catch (e) {
      // try next token
    }
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

async function fetchDriveTranscodeQualityStream(fileId, quality, rangeHeader, signal, env, requestedItag) {
  const qNorm = String(quality || '').toLowerCase().trim();
  if (!requestedItag && !['720p', '480p', '360p'].includes(qNorm)) {
    return null;
  }

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
    if (ep.itag && ep.url) {
      streamsByItag[String(ep.itag)] = ep.url;
    }
  }

  let preferredItags = [];
  if (requestedItag && streamsByItag[String(requestedItag)]) {
    preferredItags = [String(requestedItag)];
  } else if (qNorm === '720p') {
    preferredItags = ['22', '37', '59', '18'];
  } else if (qNorm === '480p') {
    preferredItags = streamsByItag['59'] ? ['59', '22', '18'] : ['22', '18', '37'];
  } else if (qNorm === '360p') {
    preferredItags = ['18', '59', '22'];
  }

  let chosenItag = '';
  let chosenUrl = '';
  for (const it of preferredItags) {
    if (streamsByItag[it]) {
      chosenItag = it;
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

  if (res.ok || res.status === 206) {
    return { response: res, itag: chosenItag };
  }
  return null;
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
  const rawIdOrUrl = url.searchParams.get('id') || url.searchParams.get('url') || '';
  const quality = url.searchParams.get('q') || '1080p';
  const title = url.searchParams.get('title') || 'Movie';
  const customFilename = url.searchParams.get('filename') || '';
  const isDedicatedVariant = url.searchParams.get('dedicated') === '1';
  const requestedItag = url.searchParams.get('itag') || '';

  const fileId = extractDriveId(rawIdOrUrl);
  const filename = buildCleanFilename(title, quality, customFilename);
  const asciiFilename = filename.replace(/[^\x20-\x7E]/g, '');
  const encodedFilename = encodeURIComponent(filename);

  const clientRange = request.headers.get('Range');

  try {
    let upstream = null;
    let resolvedItag = 'raw';

    if (fileId) {
      const qLower = String(quality).toLowerCase().trim();
      if (!isDedicatedVariant && (requestedItag || ['720p', '480p', '360p'].includes(qLower))) {
        const transcodeRes = await fetchDriveTranscodeQualityStream(
          fileId,
          qLower,
          clientRange,
          request.signal,
          env,
          requestedItag
        );
        if (transcodeRes && transcodeRes.response) {
          upstream = transcodeRes.response;
          resolvedItag = transcodeRes.itag;
        }
      }
      if (!upstream) {
        upstream = await fetchDriveBinaryStream(fileId, clientRange, request.signal);
      }
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
    respHeaders.set('X-Download-Itag', String(resolvedItag));
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
