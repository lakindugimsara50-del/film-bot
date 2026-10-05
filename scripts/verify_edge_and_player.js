/**
 * Verification test harness for Cloudflare Pages Function edge caching & player streaming.
 * Tests RFC 7233 byte-range slicing, edge caching, and non-blocking streaming.
 */

const http = require('http');
const path = require('path');

// Mock Cloudflare Cache Storage API
class MockCache {
  constructor() {
    this.store = new Map();
  }
  async match(request) {
    const key = typeof request === 'string' ? request : request.url;
    const entry = this.store.get(key);
    if (!entry) return null;
    return new Response(entry.buffer.slice(0), {
      status: entry.status,
      headers: new Headers(entry.headers),
    });
  }
  async put(request, response) {
    const key = typeof request === 'string' ? request : request.url;
    const buf = new Uint8Array(await response.arrayBuffer());
    const headers = {};
    for (const [k, v] of response.headers.entries()) {
      headers[k] = v;
    }
    this.store.set(key, {
      buffer: buf,
      status: response.status,
      headers: headers,
    });
  }
}

globalThis.caches = {
  default: new MockCache(),
};

// Create a realistic mock upstream stream server
const TOTAL_FILE_SIZE = 1865585576; // 1.86 GB (same as hi-2026)
const MOOV_SIZE = 5951133;

function startMockUpstreamServer(port) {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Access-Control-Allow-Methods', 'GET, HEAD, POST, OPTIONS');
      res.setHeader('Access-Control-Allow-Headers', 'Range, Content-Type');
      res.setHeader('Access-Control-Expose-Headers', 'Content-Length, Content-Range, Accept-Ranges, X-Stream-Cached');

      if (req.method === 'OPTIONS') {
        res.statusCode = 204;
        res.end();
        return;
      }

      if (req.url.startsWith('/health')) {
        res.setHeader('Content-Type', 'application/json');
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'pong', service: 'stream', ready: true }));
        return;
      }

      if (req.url.startsWith('/stream/warmup/')) {
        res.setHeader('Content-Type', 'application/json');
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'warming', ready: true }));
        return;
      }

      if (req.url.startsWith('/stream/channel/')) {
        const rangeHeader = req.headers['range'];
        let start = 0;
        let end = TOTAL_FILE_SIZE - 1;
        let isPartial = false;

        if (rangeHeader && rangeHeader.startsWith('bytes=')) {
          isPartial = true;
          const rangeVal = rangeHeader.slice(6).trim();
          const [sStr, eStr] = rangeVal.split('-');
          if (sStr && eStr) {
            start = parseInt(sStr, 10);
            end = Math.min(parseInt(eStr, 10), TOTAL_FILE_SIZE - 1);
          } else if (sStr && !eStr) {
            start = parseInt(sStr, 10);
            // Open-ended range returns 8MB slice (like Python backend)
            end = Math.min(start + (8 * 1024 * 1024) - 1, TOTAL_FILE_SIZE - 1);
          } else if (!sStr && eStr) {
            // Suffix range
            const suffix = parseInt(eStr, 10);
            start = Math.max(0, TOTAL_FILE_SIZE - suffix);
            end = TOTAL_FILE_SIZE - 1;
          }
        } else {
          // Unspecified range: serve first 8MB slice
          end = Math.min((8 * 1024 * 1024) - 1, TOTAL_FILE_SIZE - 1);
          isPartial = true;
        }

        const len = end - start + 1;
        res.statusCode = isPartial ? 206 : 200;
        res.setHeader('Content-Type', 'video/mp4');
        res.setHeader('Accept-Ranges', 'bytes');
        res.setHeader('Content-Length', String(len));
        if (isPartial) {
          res.setHeader('Content-Range', `bytes ${start}-${end}/${TOTAL_FILE_SIZE}`);
        }
        res.setHeader('X-Stream-Cached', 'HIT');

        if (req.method === 'HEAD') {
          res.end();
          return;
        }

        // Generate synthetic bytes with moov atom signature at byte 32
        const chunk = Buffer.alloc(Math.min(len, 64 * 1024));
        chunk.fill(0xAA);
        // Write ftyp box if at offset 0
        if (start === 0 && len >= 32) {
          chunk.writeUInt32BE(32, 0);
          chunk.write('ftyp', 4);
          chunk.writeUInt32BE(MOOV_SIZE, 32);
          chunk.write('moov', 36);
        }

        let sent = 0;
        const sendNext = () => {
          while (sent < len) {
            const toSend = Math.min(chunk.length, len - sent);
            sent += toSend;
            const canContinue = res.write(chunk.subarray(0, toSend));
            if (!canContinue) {
              res.once('drain', sendNext);
              return;
            }
          }
          res.end();
        };
        sendNext();
        return;
      }

      res.statusCode = 404;
      res.end('Not found');
    });

    server.listen(port, () => {
      resolve(server);
    });
  });
}

async function runTests() {
  console.log('====================================================');
  console.log('Starting Edge Proxy & TTFF Verification Tests...');
  console.log('====================================================\n');

  const mockPort = 8999;
  const mockServer = await startMockUpstreamServer(mockPort);
  console.log(`Mock upstream stream server running on http://127.0.0.1:${mockPort}`);

  try {
    // Load Cloudflare Pages functions
    const channelFuncPath = path.resolve(__dirname, '../functions/stream/channel/[[path]].js');
    const warmupFuncPath = path.resolve(__dirname, '../functions/stream/warmup/[[path]].js');

    const channelModule = await import('file://' + channelFuncPath.replace(/\\/g, '/'));
    const warmupModule = await import('file://' + warmupFuncPath.replace(/\\/g, '/'));

    const env = {
      STREAM_BACKEND_URL: `http://127.0.0.1:${mockPort}`,
    };

    const chatId = '-1004325759505';
    const msgId720p = '281'; // Hi! 720p
    const msgId1080p = '280'; // Hi! 1080p

    let waitPromises = [];
    const makeContext = (url, range, pathParams = [chatId, msgId720p]) => ({
      request: new Request(url, {
        method: 'GET',
        headers: range ? { 'Range': range } : {},
      }),
      env,
      params: { path: pathParams },
      waitUntil(promise) {
        waitPromises.push(promise);
      },
    });

    // ── TEST 1: Cross-Origin Canonical Edge Cache Key Consistency ──
    console.log('\n--- TEST 1: Cross-Origin Canonical Edge Cache Key Consistency ---');
    const warmupReq1 = makeContext(`https://yoursite.lk/stream/warmup/${chatId}/${msgId720p}`);
    const tWarm0 = Date.now();
    const warmupRes1 = await warmupModule.onRequest(warmupReq1);
    const tWarm1 = Date.now();
    console.log(`Warmup API response time: ${tWarm1 - tWarm0}ms, status: ${warmupRes1.status}`);

    if (waitPromises.length > 0) {
      console.log(`Awaiting ${waitPromises.length} background edge cache task(s)...`);
      await Promise.all(waitPromises);
      waitPromises = [];
    }

    // ── TEST 2: Instant Cache HIT on different domain (filmsub.pages.dev) ──
    console.log('\n--- TEST 2: Instant Cache HIT Verification (moov atom + first GOPs) ---');
    const hitContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId720p}`, 'bytes=0-1');
    const tHit0 = Date.now();
    const hitRes = await channelModule.onRequest(hitContext);
    const tHit1 = Date.now();
    const hitDuration = tHit1 - tHit0;
    const hitBytes = new Uint8Array(await hitRes.arrayBuffer());

    console.log(`Status: ${hitRes.status}`);
    console.log(`X-Edge-Cache: ${hitRes.headers.get('X-Edge-Cache')}`);
    console.log(`Content-Length: ${hitRes.headers.get('Content-Length')}`);
    console.log(`Content-Range: ${hitRes.headers.get('Content-Range')}`);
    console.log(`Slice size: ${hitBytes.byteLength} bytes`);
    console.log(`Time-To-First-Chunk (TTFB): ${hitDuration}ms`);

    if (hitRes.status !== 206) throw new Error(`Expected 206, got ${hitRes.status}`);
    if (hitRes.headers.get('X-Edge-Cache') !== 'HIT') throw new Error(`Expected X-Edge-Cache: HIT, got ${hitRes.headers.get('X-Edge-Cache')}`);
    if (hitBytes.byteLength !== 2) throw new Error(`Expected 2 bytes, got ${hitBytes.byteLength}`);
    if (hitDuration > 50) throw new Error(`Expected <50ms cache hit, got ${hitDuration}ms`);
    console.log('✅ TEST 2 PASSED: 2-byte probe served from Edge Cache in <50ms!');

    // ── TEST 3: Full 8MB moov atom retrieval from Edge Cache ──
    console.log('\n--- TEST 3: Edge Cache HIT for open-ended range bytes=0- ---');
    const fullContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId720p}`, 'bytes=0-');
    const tFull0 = Date.now();
    const fullRes = await channelModule.onRequest(fullContext);
    const tFull1 = Date.now();
    const fullDuration = tFull1 - tFull0;
    const fullBytes = new Uint8Array(await fullRes.arrayBuffer());

    console.log(`Status: ${fullRes.status}`);
    console.log(`X-Edge-Cache: ${fullRes.headers.get('X-Edge-Cache')}`);
    console.log(`Content-Length: ${fullRes.headers.get('Content-Length')}`);
    console.log(`Content-Range: ${fullRes.headers.get('Content-Range')}`);
    console.log(`Payload size: ${fullBytes.byteLength} bytes`);
    console.log(`Full 8MB Delivery Time: ${fullDuration}ms`);

    if (fullBytes.byteLength !== 8 * 1024 * 1024) throw new Error(`Expected 8MB, got ${fullBytes.byteLength}`);
    if (fullDuration > 100) throw new Error(`Expected <100ms for in-memory 8MB delivery, got ${fullDuration}ms`);
    console.log('✅ TEST 3 PASSED: Full 8MB moov atom served in <100ms directly from Edge Cache!');

    // ── TEST 4: Suffix Range Request bytes=-500 (Bug 2 Regression Test) ──
    console.log('\n--- TEST 4: RFC 7233 Suffix Range Request (bytes=-500) ---');
    const suffixContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId720p}`, 'bytes=-500');
    const tSuf0 = Date.now();
    const sufRes = await channelModule.onRequest(suffixContext);
    const tSuf1 = Date.now();
    const sufBytes = new Uint8Array(await sufRes.arrayBuffer());

    console.log(`Status: ${sufRes.status}`);
    console.log(`Content-Range: ${sufRes.headers.get('Content-Range')}`);
    console.log(`Content-Length: ${sufRes.headers.get('Content-Length')}`);
    console.log(`Payload size: ${sufBytes.byteLength} bytes`);
    console.log(`Duration: ${tSuf1 - tSuf0}ms`);

    if (sufRes.status !== 206) throw new Error(`Expected 206, got ${sufRes.status}`);
    if (sufBytes.byteLength !== 500) throw new Error(`Expected 500 bytes suffix, got ${sufBytes.byteLength}`);
    const cr = sufRes.headers.get('Content-Range');
    if (!cr || cr.startsWith('bytes 0-')) throw new Error(`Suffix range returned beginning of file! Content-Range: ${cr}`);
    console.log('✅ TEST 4 PASSED: Suffix range served correctly from end of file without false cache hit!');

    // ── TEST 5: Seeking Past 8MB (e.g. byte 50,000,000) ──
    console.log('\n--- TEST 5: Mid-Video Seek Past 8MB (bytes=50000000-50100000) ---');
    const seekContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId720p}`, 'bytes=50000000-50100000');
    const tSeek0 = Date.now();
    const seekRes = await channelModule.onRequest(seekContext);
    const tSeek1 = Date.now();
    const seekBytes = new Uint8Array(await seekRes.arrayBuffer());

    console.log(`Status: ${seekRes.status}`);
    console.log(`Content-Range: ${seekRes.headers.get('Content-Range')}`);
    console.log(`Content-Length: ${seekRes.headers.get('Content-Length')}`);
    console.log(`Payload size: ${seekBytes.byteLength} bytes`);
    console.log(`Duration: ${tSeek1 - tSeek0}ms`);

    if (seekRes.status !== 206) throw new Error(`Expected 206, got ${seekRes.status}`);
    if (seekBytes.byteLength !== 100001) throw new Error(`Expected 100001 bytes, got ${seekBytes.byteLength}`);
    console.log('✅ TEST 5 PASSED: Mid-video seeking works flawlessly with exact byte slicing!');

    // ── TEST 6: Cold MISS Bounded Probe Does NOT Block for 8MB (Bug 1 Fix Verification) ──
    console.log('\n--- TEST 6: Cold MISS Small Probe Non-Blocking Response (Message 1080p: 280) ---');
    // Message 280 is not in edgeCache yet
    const coldContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId1080p}`, 'bytes=0-1', [chatId, msgId1080p]);
    const tCold0 = Date.now();
    const coldRes = await channelModule.onRequest(coldContext);
    const tCold1 = Date.now();
    const coldDuration = tCold1 - tCold0;
    const coldBytes = new Uint8Array(await coldRes.arrayBuffer());

    console.log(`Status: ${coldRes.status}`);
    console.log(`X-Edge-Cache: ${coldRes.headers.get('X-Edge-Cache')}`);
    console.log(`Content-Length: ${coldRes.headers.get('Content-Length')}`);
    console.log(`Payload size: ${coldBytes.byteLength} bytes`);
    console.log(`Cold Probe TTFB Duration: ${coldDuration}ms`);

    if (coldRes.status !== 206) throw new Error(`Expected 206, got ${coldRes.status}`);
    if (coldRes.headers.get('X-Edge-Cache') !== 'MISS-PROBE') throw new Error(`Expected MISS-PROBE, got ${coldRes.headers.get('X-Edge-Cache')}`);
    if (coldBytes.byteLength !== 2) throw new Error(`Expected 2 bytes, got ${coldBytes.byteLength}`);
    if (coldDuration > 500) throw new Error(`Cold probe blocked for ${coldDuration}ms! Expected < 500ms`);
    console.log(`✅ TEST 6 PASSED: Cold probe returned in ${coldDuration}ms without blocking for 8MB!`);

    // Wait for background priming of 280
    if (waitPromises.length > 0) {
      console.log(`Awaiting background edge cache priming for message ${msgId1080p}...`);
      await Promise.all(waitPromises);
    }

    // Verify that 280 is now cached
    const postPrimeContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId1080p}`, 'bytes=0-1024', [chatId, msgId1080p]);
    const postPrimeRes = await channelModule.onRequest(postPrimeContext);
    console.log(`Post-prime X-Edge-Cache: ${postPrimeRes.headers.get('X-Edge-Cache')}`);
    if (postPrimeRes.headers.get('X-Edge-Cache') !== 'HIT') {
      throw new Error('Expected post-prime request to be a HIT!');
    }
    console.log('✅ TEST 6b PASSED: Background priming successfully cached full 8MB chunk for message 280!');

    // ── TEST 7: Cold MISS Open-Ended Streaming (Message 480p: 279) ──
    console.log('\n--- TEST 7: Cold MISS Open-Ended Range bytes=0- (Message 480p: 279) ---');
    const msgId480p = '279';
    const coldOpenContext = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId480p}`, 'bytes=0-', [chatId, msgId480p]);
    const tOpen0 = Date.now();
    const coldOpenRes = await channelModule.onRequest(coldOpenContext);
    const tOpen1 = Date.now();
    const openDuration = tOpen1 - tOpen0;

    console.log(`Status: ${coldOpenRes.status}`);
    console.log(`X-Edge-Cache: ${coldOpenRes.headers.get('X-Edge-Cache')}`);
    console.log(`Content-Length: ${coldOpenRes.headers.get('Content-Length')}`);
    console.log(`Content-Range: ${coldOpenRes.headers.get('Content-Range')}`);
    console.log(`TTFB Duration: ${openDuration}ms`);

    if (coldOpenRes.status !== 206) throw new Error(`Expected 206, got ${coldOpenRes.status}`);
    if (coldOpenRes.headers.get('X-Edge-Cache') !== 'MISS-STREAMING') throw new Error(`Expected MISS-STREAMING, got ${coldOpenRes.headers.get('X-Edge-Cache')}`);
    if (openDuration > 500) throw new Error(`TTFB took too long: ${openDuration}ms`);

    // Stream reader to receive chunks
    const reader = coldOpenRes.body.getReader();
    let totalReceived = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalReceived += value.length;
    }
    console.log(`Stream consumed: ${totalReceived} bytes`);
    if (totalReceived !== 8 * 1024 * 1024) throw new Error(`Expected 8MB streamed, got ${totalReceived}`);
    console.log(`✅ TEST 7 PASSED: Cold miss streamed immediately with TTFB ${openDuration}ms!`);

    // Await background priming task
    if (waitPromises.length > 0) {
      console.log(`Awaiting background edge cache priming for message ${msgId480p}...`);
      await Promise.all(waitPromises);
    }

    // Verify 279 is now cached
    const postPrime279Context = makeContext(`https://filmsub.pages.dev/stream/channel/${chatId}/${msgId480p}`, 'bytes=0-1024', [chatId, msgId480p]);
    const postPrime279Res = await channelModule.onRequest(postPrime279Context);
    console.log(`Post-prime X-Edge-Cache: ${postPrime279Res.headers.get('X-Edge-Cache')}`);
    if (postPrime279Res.headers.get('X-Edge-Cache') !== 'HIT') {
      throw new Error('Expected post-prime 279 request to be a HIT!');
    }
    console.log('✅ TEST 7b PASSED: Background priming cached 279, subsequent request is HIT!');

    console.log('\n====================================================');
    console.log('🎉 ALL EDGE PROXY VERIFICATION TESTS PASSED!');
    console.log('====================================================');
  } finally {
    mockServer.close();
  }
}

runTests().catch(err => {
  console.error('\n❌ TEST FAILURE:', err);
  process.exit(1);
});
