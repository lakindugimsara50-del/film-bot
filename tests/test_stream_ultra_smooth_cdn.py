"""
tests/test_stream_ultra_smooth_cdn.py

End-to-end verification tests for the 4-layer CDN-grade Telegram streaming engine:
1. FastAPI stream_server: MP4 moov keyframe seek index, /stream/seekinfo, seek-aware /stream/warmup,
   2 MiB tail cache, and exact Range byte offsets.
2. StreamSessionPool: singleflight chunk coalescing, LRU 1 MiB chunk cache, dead DC media_session eviction,
   viewer activity priority tracking, and background range prewarming.
3. Cloudflare Pages Edge Functions: exact sub-8MiB Range alignment (regression test for byte-0 corruption),
   4 MiB aligned Edge Block Cache, /stream/seekinfo & /stream/warmup forwarding, byte-identical download proxy.
4. Frontend Player & CSS: zero-spinner seamless shadow handover CSS/JS, real throughput ABR selection,
   MP4 keyframe offset interpolation, and JS syntax validity.
"""

import asyncio
import json
import os
import struct
import subprocess
import sys
import types as py_types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_BOT_DIR = _ROOT / "bot"
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def _build_synthetic_mp4(duration_ms: int = 600_000, timescale: int = 1000, chunk_offsets=(2048, 5_000_000, 12_000_000, 20_000_000)):
    """Builds a minimal faststart MP4 with ftyp + moov(mvhd + trak(mdia(mdhd+minf(stbl(stts+stss+stsc+stco))))) + padding."""
    # mvhd v0 (timescale @ +12, duration @ +16)
    mvhd_payload = struct.pack(">IIII", 0, 0, 0, timescale) + struct.pack(">I", duration_ms) + b"\x00" * 80
    mvhd = _box(b"mvhd", mvhd_payload)

    # mdhd v0
    mdhd_payload = struct.pack(">IIIII", 0, 0, 0, timescale, duration_ms) + b"\x00" * 4
    mdhd = _box(b"mdhd", mdhd_payload)

    # hdlr: vide
    hdlr = _box(b"hdlr", struct.pack(">I4s4sIII", 0, b"mhlr", b"vide", 0, 0, 0) + b"VideoHandler\x00")

    # stts: 4 samples of 150s each (150_000 ticks)
    stts = _box(b"stts", struct.pack(">IIII", 0, 1, len(chunk_offsets), duration_ms // len(chunk_offsets)))
    # stss: sync samples 1..N
    stss_entries = b"".join(struct.pack(">I", i + 1) for i in range(len(chunk_offsets)))
    stss = _box(b"stss", struct.pack(">II", 0, len(chunk_offsets)) + stss_entries)
    # stsc: 1 sample per chunk starting from chunk 1
    stsc = _box(b"stsc", struct.pack(">IIIII", 0, 1, 1, 1, 1))
    # stco: 32-bit chunk offsets
    stco_entries = b"".join(struct.pack(">I", off) for off in chunk_offsets)
    stco = _box(b"stco", struct.pack(">II", 0, len(chunk_offsets)) + stco_entries)

    stbl = _box(b"stbl", stts + stss + stsc + stco)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", mdhd + hdlr + minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", mvhd + trak)
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isommp41")
    return ftyp + moov + b"\x00" * 256


# ---------------------------------------------------------------------------
# 1. Backend stream_server tests
# ---------------------------------------------------------------------------

def test_parse_mp4_seek_index_extracts_duration_and_anchors():
    from streaming import stream_server

    mp4_header = _build_synthetic_mp4(
        duration_ms=600_000,
        chunk_offsets=(4096, 10_000_000, 25_000_000, 40_000_000),
    )
    parsed = stream_server._parse_mp4_seek_index(mp4_header, file_size=50_000_000)
    assert parsed is not None
    assert parsed["file_size"] == 50_000_000
    assert parsed["duration"] == pytest.approx(600.0, rel=1e-2)
    anchors = parsed["anchors"]
    assert len(anchors) >= 3
    assert all(anchors[i]["t"] <= anchors[i + 1]["t"] for i in range(len(anchors) - 1))
    assert anchors[-1]["offset"] >= anchors[0]["offset"]


def test_tail_cache_roundtrip():
    from streaming import stream_server

    async def _run():
        stream_server._TAIL_CACHE.clear()
        tail_bytes = b"MOOV_TAIL_" + os.urandom(512)
        file_size = 100_000_000
        tail_start = file_size - len(tail_bytes)
        await stream_server._save_to_tail_cache("tail_key_1", tail_start, tail_bytes)

        hit = await stream_server._get_from_tail_cache("tail_key_1", file_size, tail_start + 10, tail_start + 109)
        assert hit == tail_bytes[10:110]
        miss = await stream_server._get_from_tail_cache("tail_key_1", file_size, 0, 100)
        assert miss is None

    asyncio.run(_run())


def test_seekinfo_and_warmup_and_range_endpoints(monkeypatch):
    from fastapi.testclient import TestClient
    from streaming import stream_server

    mp4_header = _build_synthetic_mp4(duration_ms=400_000, chunk_offsets=(2048, 4_000_000, 8_000_000, 12_000_000))
    total_size = 20_000_000
    chunk_block = mp4_header.ljust(1048576, b"A")

    async def fake_stream_chunks(media_source, start=0, end=0):
        want = (end - start + 1) if end >= start else 1048576
        if start == 0:
            yield chunk_block[: min(want, len(chunk_block))]
        else:
            remaining = want
            while remaining > 0:
                piece = min(remaining, 1048576)
                yield (b"X" * piece)
                remaining -= piece

    fake_pool = MagicMock()
    fake_pool.clients = [MagicMock()]
    fake_pool.get_media_info = AsyncMock(
        return_value={
            "message": MagicMock(id=999),
            "file_id": "test_file_id_12345678901234567890",
            "file_size": total_size,
            "mime_type": "video/mp4",
            "file_name": "movie.mp4",
        }
    )
    fake_pool.stream_media_chunks = fake_stream_chunks
    fake_pool.prewarm_chunk_range = AsyncMock(return_value=3)
    fake_pool.note_viewer_activity = MagicMock()

    mock_warmup = AsyncMock(return_value=True)
    monkeypatch.setattr(stream_server, "stream_pool", fake_pool)
    monkeypatch.setattr(stream_server, "warmup_channel_message", mock_warmup)
    stream_server._SEEK_INDEX_CACHE.clear()
    stream_server._HEADER_CACHE.clear()

    client = TestClient(stream_server.app)

    # 1. /stream/seekinfo/{chat_id}/{message_id}
    r_seek = client.get("/stream/seekinfo/-1004325759505/999")
    assert r_seek.status_code == 200
    js = r_seek.json()
    assert js["file_size"] == total_size
    assert js["duration"] == pytest.approx(400.0, rel=1e-2)
    assert len(js["anchors"]) >= 2

    # 2. /stream/warmup with seek start_byte forwards target_bytes & start_offset_req accurately
    r_warm_seek = client.post("/stream/warmup/-1004325759505/999?start_byte=5242880&bytes=4194304")
    assert r_warm_seek.status_code == 200
    assert r_warm_seek.json()["start_byte"] == 5242880
    mock_warmup.assert_awaited_once_with(
        "-1004325759505",
        999,
        target_bytes=4194304,
        start_offset_req=5242880,
    )

    # 3. /stream/channel mid-file Range preserves exact start byte in Content-Range
    r_range = client.get(
        "/stream/channel/-1004325759505/999?burst=2097152",
        headers={"Range": "bytes=1048576-2097151"},
    )
    assert r_range.status_code == 206
    assert r_range.headers["content-range"] == f"bytes 1048576-2097151/{total_size}"
    assert len(r_range.content) == 1048576


# ---------------------------------------------------------------------------
# 2. TelegramStreamPool concurrency, singleflight & cache tests
# ---------------------------------------------------------------------------

def test_session_pool_viewer_activity_lru_and_singleflight(monkeypatch):
    from pyrogram import types
    from streaming.session_pool import TelegramStreamPool

    async def _run():
        pool = TelegramStreamPool()
        pool.note_viewer_activity(hold_sec=60.0)
        # During pytest, PYTEST_CURRENT_TEST shields unrelated background warmups unless active stream > 0
        assert pool.has_recent_viewer() is False
        pool._active_streams_count = 1
        assert pool.has_recent_viewer() is True
        pool._active_streams_count = 0

        # Non-pytest mode respects _viewer_active_until window
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        pool.note_viewer_activity(hold_sec=60.0)
        assert pool.has_recent_viewer() is True
        pool._viewer_active_until = 0.0
        assert pool.has_recent_viewer() is False

        fetch_calls = 0

        async def slow_uncached(media_source, chunk_idx):
            nonlocal fetch_calls
            fetch_calls += 1
            await asyncio.sleep(0.05)
            return b"CHUNK_DATA_" + str(chunk_idx).encode("ascii") + b"\x00" * 64

        pool._fetch_chunk_uncached = slow_uncached

        msg = MagicMock(spec=types.Message)
        msg.chat = MagicMock(id=-1004325759505)
        msg.id = 999
        msg.video = MagicMock(file_id="test_file_id_very_long_unique_key_001", file_size=10485760)
        msg.document = None

        # Fire two concurrent requests for the same chunk -> singleflight executes uncached fetch ONLY ONCE
        c1, c2 = await asyncio.gather(
            pool._fetch_chunk(msg, chunk_idx=2),
            pool._fetch_chunk(msg, chunk_idx=2),
        )
        assert c1 == c2
        assert fetch_calls == 1

        # Third sequential call hits LRU _chunk_cache (still fetch_calls == 1!)
        c3 = await pool._fetch_chunk(msg, chunk_idx=2)
        assert c3 == c1
        assert fetch_calls == 1

        # Prewarm range populates consecutive chunks cleanly
        warmed = await pool.prewarm_chunk_range(msg, start_byte=0, total_bytes=2 * 1048576)
        assert warmed >= 1

    asyncio.run(_run())


def test_session_pool_evicts_broken_media_session(monkeypatch):
    from pyrogram import types
    from pyrogram.file_id import FileId, FileType
    import pyrogram.methods.messages.inline_session as inline_mod
    from streaming.session_pool import TelegramStreamPool

    async def _run():
        pool = TelegramStreamPool()
        client = MagicMock()
        client.name = "broken_dc_admin"
        client.is_connected = True
        client.storage = MagicMock()
        client.invoke = AsyncMock()
        dead_sess = MagicMock()
        dead_sess.invoke = AsyncMock(side_effect=ConnectionError("socket reset"))
        client.media_sessions = {5: dead_sess}
        pool.clients = [client]

        valid_file_id = FileId(
            file_type=FileType.VIDEO,
            dc_id=5,
            media_id=123456789,
            access_hash=987654321,
            file_reference=b"ref123",
        ).encode()

        monkeypatch.setattr(inline_mod, "get_session", AsyncMock(return_value=dead_sess))

        msg = MagicMock(spec=types.Message)
        msg.video = MagicMock(file_id=valid_file_id, file_size=10485760)
        msg.document = None

        with pytest.raises(ConnectionError):
            await pool._fetch_chunk_direct(client, msg, chunk_idx=0)
        assert 5 not in client.media_sessions

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3. Cloudflare Edge Functions & Player.js Verification (Node.js)
# ---------------------------------------------------------------------------

def test_cloudflare_edge_range_alignment_block_cache_and_identical_download():
    chan_path = _ROOT / "website" / "functions" / "stream" / "channel" / "[[path]].js"
    dl_path = _ROOT / "website" / "functions" / "stream" / "download" / "[[path]].js"
    warmup_path = _ROOT / "website" / "functions" / "stream" / "warmup" / "[[path]].js"
    seekinfo_path = _ROOT / "website" / "functions" / "stream" / "seekinfo" / "[[path]].js"
    ping_path = _ROOT / "website" / "functions" / "stream" / "ping.js"

    assert chan_path.read_text(encoding="utf-8") == dl_path.read_text(encoding="utf-8")
    for p in (chan_path, dl_path, warmup_path, seekinfo_path, ping_path):
        chk = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert chk.returncode == 0, f"Syntax error in {p}: {chk.stderr}"

    node_script = r"""
const assert = require('assert');
const fs = require('fs');
const path = require('path');

(async () => {
  // Build deterministic 16 MiB synthetic file on origin
  const TOTAL_SIZE = 16 * 1024 * 1024;
  const BLOCK_BYTES = 4 * 1024 * 1024;
  const originStore = new Map();
  let originFetchCalls = 0;

  globalThis.caches = {
    default: {
      async match(req) {
        const key = typeof req === 'string' ? req : req.url;
        const val = originStore.get(key);
        return val ? val.clone() : undefined;
      },
      async put(req, res) {
        const key = typeof req === 'string' ? req : req.url;
        originStore.set(key, res.clone());
      }
    }
  };

  function makeChunkPayload(start, end) {
    const len = end - start + 1;
    const buf = new Uint8Array(len);
    for (let i = 0; i < len; i++) buf[i] = (start + i) & 0xff;
    return buf;
  }

  globalThis.fetch = async (url, init = {}) => {
    const u = String(url);
    if (u.includes('stream_endpoint.json')) {
      return new Response(JSON.stringify({ stream_base_url: 'https://mock-tunnel.example' }), { status: 200 });
    }
    if (u.startsWith('https://mock-tunnel.example/stream/channel/')) {
      originFetchCalls++;
      const headers = new Headers(init.headers || {});
      const range = headers.get('Range') || 'bytes=0-1048575';
      const m = range.match(/bytes=(\d+)-(\d+)?/);
      const s = parseInt(m[1], 10);
      const e = m[2] !== undefined ? Math.min(TOTAL_SIZE - 1, parseInt(m[2], 10)) : Math.min(TOTAL_SIZE - 1, s + BLOCK_BYTES - 1);
      const body = makeChunkPayload(s, e);
      return new Response(body, {
        status: 206,
        headers: {
          'Content-Range': `bytes ${s}-${e}/${TOTAL_SIZE}`,
          'Content-Length': String(body.byteLength),
          'Content-Type': 'video/mp4',
          'Accept-Ranges': 'bytes'
        }
      });
    }
    if (u.startsWith('https://mock-tunnel.example/stream/warmup/')) {
      return new Response(JSON.stringify({ ok: true, echoed_url: u }), { status: 200 });
    }
    return new Response('Not Found', { status: 404 });
  };

  // Load ESM module via dynamic import
  const mod = await import('file://' + process.argv[1].replace(/\\/g, '/'));
  const waitPromises = [];
  const ctxBase = {
    waitUntil(p) { waitPromises.push(p); },
    env: {},
    request: null
  };

  // Regression check: requesting 1..2 MiB mid-header range MUST return EXACT start 1048576 (never bytes 0-...)
  const req1 = new Request('https://filmsub.pages.dev/stream/channel/-1004325759505/999', {
    headers: { 'Range': 'bytes=1048576-2097151' }
  });
  const res1 = await mod.onRequest({ ...ctxBase, request: req1 });
  assert.strictEqual(res1.status, 206);
  const cr1 = res1.headers.get('Content-Range');
  assert.ok(cr1.startsWith('bytes 1048576-2097151/'), `Expected exact Range start, got ${cr1}`);
  const buf1 = new Uint8Array(await res1.arrayBuffer());
  assert.strictEqual(buf1.byteLength, 1048576);
  assert.strictEqual(buf1[0], (1048576) & 0xff);

  // Mid-movie aligned block [8 MiB .. 12 MiB) -> misses, populates Edge Block Cache (v3 blk_8388608), then hits!
  const reqBlockMiss = new Request('https://filmsub.pages.dev/stream/channel/-1004325759505/999', {
    headers: { 'Range': 'bytes=8388608-12582911' }
  });
  const resBlockMiss = await mod.onRequest({ ...ctxBase, request: reqBlockMiss });
  assert.strictEqual(resBlockMiss.status, 206);
  assert.strictEqual(resBlockMiss.headers.get('X-Edge-Cache'), 'MISS-BLOCK-STREAMING');
  const bufBlk = new Uint8Array(await resBlockMiss.arrayBuffer());
  assert.strictEqual(bufBlk.byteLength, BLOCK_BYTES);
  await Promise.allSettled(waitPromises);
  const callsAfterBlkCache = originFetchCalls;

  // Sub-range inside cached 4 MiB block MUST serve HIT-BLOCK directly from Edge Cache with zero upstream call!
  const reqBlockHit = new Request('https://filmsub.pages.dev/stream/channel/-1004325759505/999', {
    headers: { 'Range': 'bytes=9437184-10485759' }
  });
  const resBlockHit = await mod.onRequest({ ...ctxBase, request: reqBlockHit });
  assert.strictEqual(resBlockHit.status, 206);
  assert.strictEqual(resBlockHit.headers.get('X-Edge-Cache'), 'HIT-BLOCK');
  assert.strictEqual(resBlockHit.headers.get('Content-Range'), `bytes 9437184-10485759/${TOTAL_SIZE}`);
  const bufHit = new Uint8Array(await resBlockHit.arrayBuffer());
  assert.strictEqual(bufHit.byteLength, 1048576);
  assert.strictEqual(bufHit[0], (9437184) & 0xff);
  assert.strictEqual(originFetchCalls, callsAfterBlkCache, 'Sub-range inside cached 4MB block must not hit origin!');

  // Warmup forwarding check with start_byte & bytes
  const warmMod = await import('file://' + process.argv[2].replace(/\\/g, '/'));
  const warmReq = new Request('https://filmsub.pages.dev/stream/warmup/-1004325759505/999?start_byte=5242880&bytes=4194304', { method: 'POST' });
  const warmRes = await warmMod.onRequest({ ...ctxBase, request: warmReq });
  assert.strictEqual(warmRes.status, 200);
  const warmJson = await warmRes.json();
  assert.ok(warmJson.echoed_url.includes('start_byte=5242880'));
  assert.ok(warmJson.echoed_url.includes('bytes=4194304'));
  console.log('EDGE_TESTS_OK');
})().catch(e => { console.error(e); process.exit(1); });
"""
    res = subprocess.run(
        ["node", "-e", node_script, str(chan_path), str(warmup_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Node Edge test failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}"
    assert "EDGE_TESTS_OK" in res.stdout


def test_player_js_syntax_helpers_and_contracts():
    player_path = _ROOT / "website" / "assets" / "js" / "player.js"
    css_path = _ROOT / "website" / "assets" / "css" / "style.css"
    movie_html_path = _ROOT / "website" / "movie.html"

    chk = subprocess.run(["node", "--check", str(player_path)], capture_output=True, text=True)
    assert chk.returncode == 0, f"player.js syntax error: {chk.stderr}"

    player_txt = player_path.read_text(encoding="utf-8")
    css_txt = css_path.read_text(encoding="utf-8")
    movie_txt = movie_html_path.read_text(encoding="utf-8")

    # Preserve mobile hardware guard E2E contract & new seamless engines
    assert "isMobileClient" in player_txt
    assert "applyQualitySwitch(targetQuality, { isAutoDowngrade: false });" in player_txt
    assert "attachSeekPrewarmEngine" in player_txt
    assert "fz-shadow-handover" in player_txt
    assert "vjs-fz-seamless-switching" in css_txt
    assert "v=20261125_cinema_v66" in movie_txt

    # Unit-test JS helper functions extracted into clean VM sandbox
    vm_test = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync(process.argv[1], 'utf8');
function extractFn(name) {
  const idx = src.indexOf(`function ${name}(`);
  if (idx === -1) throw new Error('Missing ' + name);
  let depth = 0, start = src.indexOf('{', idx);
  for (let i = start; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(idx, i + 1);
    }
  }
  throw new Error('Unclosed ' + name);
}

const sandbox = {
  window: { innerWidth: 1280 },
  navigator: { userAgent: 'Mozilla/5.0', connection: { downlink: 5, effectiveType: '4g', saveData: false } },
  _measuredMbps: 0,
  getEffectiveDownloadMbps() { return sandbox._measuredMbps || sandbox.navigator.connection.downlink; },
  Math, Number, Array, Object, Boolean, parseInt
};
vm.createContext(sandbox);
vm.runInContext(
  [
    extractFn('extractChannelIdsFromUrl'),
    extractFn('estimateByteOffsetForTime'),
    extractFn('detectNetworkSpeed'),
  ].join('\n\n'),
  sandbox
);

// 1. extractChannelIdsFromUrl
const ids = sandbox.extractChannelIdsFromUrl('/stream/channel/-1004325759505/280');
assert.strictEqual(ids.chatId, '-1004325759505');
assert.strictEqual(ids.messageId, '280');
assert.strictEqual(sandbox.extractChannelIdsFromUrl('https://cdn.example/video.mp4'), null);

// 2. estimateByteOffsetForTime (moov keyframe interpolation + 1 MiB alignment)
const seekInfo = {
  duration: 600,
  file_size: 600_000_000,
  anchors: [
    { t: 0, offset: 4096 },
    { t: 300, offset: 300_000_000 },
    { t: 600, offset: 599_000_000 },
  ]
};
const off0 = sandbox.estimateByteOffsetForTime(seekInfo, 0, 600);
assert.strictEqual(off0, 0);
const offMid = sandbox.estimateByteOffsetForTime(seekInfo, 300, 600);
assert.ok(offMid > 290_000_000 && offMid <= 300_000_000, `Unexpected mid offset ${offMid}`);
assert.strictEqual(offMid % 1048576, 0, 'Must align cleanly to 1 MiB backend chunk boundary');

// 3. detectNetworkSpeed respects real measured Mbps
sandbox._measuredMbps = 1.1;
assert.strictEqual(sandbox.detectNetworkSpeed().recommendedQuality, '480p');
sandbox._measuredMbps = 3.2;
assert.strictEqual(sandbox.detectNetworkSpeed().recommendedQuality, '720p');
sandbox._measuredMbps = 8.5;
assert.strictEqual(sandbox.detectNetworkSpeed().recommendedQuality, '1080p');
console.log('PLAYER_HELPERS_OK');
"""
    res = subprocess.run(["node", "-e", vm_test, str(player_path)], capture_output=True, text=True)
    assert res.returncode == 0, f"Player VM test failed:\nSTDOUT:{res.stdout}\nSTDERR:{res.stderr}"
    assert "PLAYER_HELPERS_OK" in res.stdout
