import os
import re
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from http.server import HTTPServer
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class LocalFilmSubHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        website_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'website')
        super().__init__(*args, directory=website_dir, **kwargs)

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/api/stream'):
            self._handle_stream_proxy()
            return
        super().do_GET()

    def _handle_stream_proxy(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        file_id = (qs.get('id') or [''])[0].strip()
        quality = (qs.get('q') or ['auto'])[0].strip().lower()
        if not file_id:
            self.send_response(400)
            self.end_headers()
            return

        sample_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'website', 'assets', 'sample_stream.mp4')
        with open(sample_path, 'rb') as f:
            full_bytes = f.read()
        total_len = len(full_bytes)

        raw_range = self.headers.get('Range') or ''
        m = re.match(r'^bytes=(\d+)-(\d*)$', raw_range)
        start = int(m.group(1)) if m else 0
        end = int(m.group(2)) if (m and m.group(2)) else (total_len - 1)
        end = min(end, total_len - 1)
        chunk = full_bytes[start:end + 1]

        self.send_response(206)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('X-Stream-Quality', quality)
        self.send_header('X-Drive-File-Id', file_id)
        self.send_header('Content-Range', f'bytes {start}-{end}/{total_len}')
        self.send_header('Content-Length', str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


def _ensure_server_on_8000():
    srv = ThreadingHTTPServer(('127.0.0.1', 8000), LocalFilmSubHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def run_tests():
    local_srv = _ensure_server_on_8000()
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--autoplay-policy=no-user-gesture-required')

    driver = webdriver.Chrome(options=options)
    try:
        for test_id in ['interstellar', 'interstellar-2014', 'irumudi-2026']:
            url = f'http://localhost:8000/movie.html?id={test_id}'
            print(f"\n==========================================")
            print(f"Testing URL: {url}")
            print(f"==========================================")
            driver.get(url)
            
            # Wait for Video.js player and movie details to be ready
            for _ in range(30):
                hero_elems = driver.find_elements('id', 'movie-detail-title')
                title_loaded = bool(hero_elems and hero_elems[0].text and 'Loading' not in hero_elems[0].text)
                ready = driver.execute_script("""
                    const p = typeof videojs !== 'undefined' && videojs.getPlayer('filmsubPlayer');
                    return !!(p && p.readyState && p.readyState() >= 1);
                """)
                if ready and title_loaded:
                    break
                time.sleep(0.5)

            # 1. Title and Hero details
            hero_title = driver.find_element('id', 'movie-detail-title').text
            poster_src = driver.find_element('id', 'movie-poster-img').get_attribute('src')
            print(f"[OK] Hero Title: {hero_title}")
            print(f"[OK] Poster Src: {poster_src}")
            expected_title = 'Irumudi' if 'irumudi' in test_id else 'Interstellar'
            assert expected_title in hero_title, f"Expected {expected_title} in hero title, got {hero_title}"

            # 2. Check Console logs for fatal errors
            logs = driver.get_log('browser')
            severe_logs = [
                l for l in logs
                if l['level'] == 'SEVERE'
                and 'favicon' not in l['message']
                and 'image.tmdb.org' not in l['message']
                and 'ERR_CONNECTION' not in l['message']
            ]
            print(f"[LOGS] Severe logs (excluding favicon): {len(severe_logs)}")
            for sl in severe_logs:
                print("  ", sl['message'])
            assert len(severe_logs) == 0, f"Found severe console errors: {severe_logs}"

            # 3. Video.js player & Subtitles verification
            player_info = driver.execute_script("""
                const p = videojs.getPlayer('filmsubPlayer');
                if (!p) return { error: 'Player not found' };
                const tt = p.textTracks();
                const tracks = [];
                for (let i = 0; i < tt.length; i++) {
                    tracks.push({
                        kind: tt[i].kind,
                        label: tt[i].label,
                        mode: tt[i].mode,
                        cues: tt[i].cues ? tt[i].cues.length : 0
                    });
                }
                return {
                    src: p.currentSrc(),
                    duration: p.duration(),
                    readyState: p.readyState(),
                    paused: p.paused(),
                    tracks: tracks
                };
            """)
            print(f"[OK] Player Info: src={player_info.get('src')}, duration={player_info.get('duration')}, readyState={player_info.get('readyState')}")
            print(f"[OK] Subtitle Tracks count: {len(player_info.get('tracks', []))}")
            for t in player_info.get('tracks', []):
                print(f"     Track: kind={t['kind']} mode={t['mode']} cues={t['cues']}")
            assert len(player_info.get('tracks', [])) > 0, "No subtitle tracks found"
            assert player_info['tracks'][0]['cues'] > 0, "Subtitle track has 0 cues"

            # 4. Playback advancement test (simulate user interaction or play)
            play_res = driver.execute_script("""
                const p = videojs.getPlayer('filmsubPlayer');
                return new Promise((resolve) => {
                    p.play().then(() => resolve({status: 'playing'}))
                            .catch(e => resolve({error: e.name + ': ' + e.message}));
                });
            """)
            print(f"[OK] Play invocation: {play_res}")
            time.sleep(3)
            cur_time = driver.execute_script("return videojs.getPlayer('filmsubPlayer').currentTime();")
            print(f"[OK] Video playback advanced to: {cur_time:.2f}s")
            assert cur_time > 0, f"Expected currentTime > 0, got {cur_time}"

            # 5. Download section verification
            downloads_count = driver.execute_script("return document.querySelectorAll('.download-card').length;")
            print(f"[OK] Download cards rendered: {downloads_count}")
            assert downloads_count > 0, "Expected at least 1 download card"

        # 6. Embed stream test (Inception)
        print(f"\n==========================================")
        print("Testing URL: http://localhost:8000/movie.html?id=inception-2010 (Embed Stream)")
        print(f"==========================================")
        driver.get('http://localhost:8000/movie.html?id=inception-2010')
        iframe_src = None
        for _ in range(20):
            iframe_src = driver.execute_script("return document.querySelector('#video-player-container iframe')?.src;")
            if iframe_src:
                break
            time.sleep(0.4)
        print(f"[OK] Inception embed iframe src: {iframe_src}")
        assert iframe_src and 'vidsrc.to' in iframe_src, f"Expected vidsrc.to in iframe src, got {iframe_src}"

        # 7. Real Google Drive Super Player test (Game of Thrones + One Last Shot - Chunk Stream + Quality Switch + VIP Servers + Zero White Screen)
        for gdrive_slug in ['game-of-thrones-2011-s01e01', 'one-last-shot-2026']:
            print(f"\n==========================================")
            print(f"Testing URL: http://localhost:8000/movie.html?id={gdrive_slug} (Google Drive Super Player)")
            print(f"==========================================")
            driver.get(f'http://localhost:8000/movie.html?id={gdrive_slug}')
            for _ in range(30):
                ready = driver.execute_script("""
                    const p = typeof videojs !== 'undefined' && videojs.getPlayer('filmsubPlayer');
                    return !!(p && p.readyState && p.readyState() >= 4);
                """)
                if ready:
                    break
                time.sleep(0.5)

            super_check = driver.execute_script("""
                const container = document.getElementById('video-player-container');
                const bg = window.getComputedStyle(container).backgroundColor;
                const serverTabs = Array.from(document.querySelectorAll('#server-tabs .server-tab')).map(b => b.textContent.trim());
                const p = videojs.getPlayer('filmsubPlayer');
                const inPlayerQualityBtn = !!document.querySelector('.vjs-super-quality-btn');
                const subOverlay = !!document.getElementById('fs-sub-overlay');
                return {
                    bg,
                    serverTabsCount: serverTabs.length,
                    serverTabs,
                    currentSrc: p ? p.currentSrc() : '',
                    readyState: p ? p.readyState() : 0,
                    duration: p ? p.duration() : 0,
                    inPlayerQualityBtn,
                    subOverlay
                };
            """)
            print(f"[OK] Zero-White-Screen BG: {super_check['bg']}")
            print(f"[OK] Server Tabs ({super_check['serverTabsCount']}): {super_check['serverTabs']}")
            print(f"[OK] Super Player Chunk Src: {super_check['currentSrc']}, Duration: {super_check['duration']}, ReadyState: {super_check['readyState']}")
            assert super_check['bg'] == 'rgb(0, 0, 0)', f"Expected pure black rgb(0, 0, 0) container background, got {super_check['bg']}"
            assert super_check['serverTabsCount'] >= 5, f"Expected at least 5 multi-server + VIP backup tabs, got {super_check['serverTabsCount']}"
            assert super_check['inPlayerQualityBtn'], "Expected in-player quality gear button inside Video.js control bar"
            assert super_check['subOverlay'], "Expected Sinhala subtitle overlay inside player"
            assert super_check['readyState'] == 4 and (super_check['duration'] or 0) > 0, f"Expected readyState == 4 and duration > 0 on /api/stream, got {super_check}"
            assert '/api/stream?id=' in super_check['currentSrc'], f"Expected /api/stream?id= in currentSrc, got {super_check['currentSrc']}"

            # Test quality switch to 480p (strictly verify /api/stream?id=...&q=480p, never sample_stream.mp4)
            driver.execute_script("document.querySelector('.q-pill[data-quality=\"480p\"]').click();")
            time.sleep(1)
            new_src = driver.execute_script("return videojs.getPlayer('filmsubPlayer').currentSrc();")
            print(f"[OK] Quality switch to 480p updated src: {new_src}")
            assert 'q=480p' in new_src and 'sample_stream' not in new_src, f"Expected strict 480p chunk query in src, got {new_src}"

            # Test UTF-16LE with BOM .SRT decoding, cue replacement in Video.js textTrack, and +0.5s sync offset
            sub_edge_check = driver.execute_script("""
                const srtText = "1\\r\\n00:00:02,000 --> 00:00:06,000\\r\\nසිංහල UTF-16 පරීක්ෂාව 1\\r\\n\\r\\n2\\r\\n00:00:07,000 --> 00:00:11,000\\r\\nසිංහල UTF-16 පරීක්ෂාව 2\\r\\n";
                const utf16Bytes = new Uint8Array(2 + srtText.length * 2);
                utf16Bytes[0] = 0xFF;
                utf16Bytes[1] = 0xFE;
                for (let i = 0; i < srtText.length; i++) {
                    const code = srtText.charCodeAt(i);
                    utf16Bytes[2 + i * 2] = code & 0xFF;
                    utf16Bytes[2 + i * 2 + 1] = (code >> 8) & 0xFF;
                }
                const decoded = decodeSubtitleBuffer(utf16Bytes.buffer);
                const vtt = convertSrtToVttText(decoded);
                const cues = parseVttToCues(vtt);
                parsedSubCues = cues;
                syncSubtitles();
                const p = videojs.getPlayer('filmsubPlayer');
                const tt = p.textTracks()[0];
                const initialStart = tt.cues[0].startTime;
                document.getElementById('btn-sub-sync-plus').click();
                const shiftedStart = tt.cues[0].startTime;
                return {
                    cuesCount: tt.cues.length,
                    firstText: tt.cues[0].text,
                    initialStart,
                    shiftedStart
                };
            """)
            print(f"[OK] UTF-16LE .SRT & Sync check: {sub_edge_check}")
            assert sub_edge_check['cuesCount'] == 2, f"Expected 2 replaced cues from UTF-16LE SRT, got {sub_edge_check}"
            assert 'සිංහල UTF-16' in sub_edge_check['firstText'], f"Expected Sinhala text in decoded UTF-16 cue, got {sub_edge_check}"
            assert abs((sub_edge_check['shiftedStart'] - sub_edge_check['initialStart']) - 0.5) < 0.05, f"Expected +0.5s shift on VTTCue, got {sub_edge_check}"

        print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")
    finally:
        driver.quit()
        if local_srv:
            local_srv.shutdown()

if __name__ == '__main__':
    run_tests()
