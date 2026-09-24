import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def run_tests():
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
            
            # Wait for Video.js player to be ready
            for _ in range(20):
                ready = driver.execute_script("""
                    const p = typeof videojs !== 'undefined' && videojs.getPlayer('filmsubPlayer');
                    return !!(p && p.readyState && p.readyState() >= 1);
                """)
                if ready:
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
            severe_logs = [l for l in logs if l['level'] == 'SEVERE' and 'favicon' not in l['message']]
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
            for _ in range(25):
                ready = driver.execute_script("""
                    const p = typeof videojs !== 'undefined' && videojs.getPlayer('filmsubPlayer');
                    return !!(p && p.readyState && p.readyState() >= 1);
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

            # Test quality switch to 480p
            driver.execute_script("document.querySelector('.q-pill[data-quality=\"480p\"]').click();")
            time.sleep(1)
            new_src = driver.execute_script("return videojs.getPlayer('filmsubPlayer').currentSrc();")
            print(f"[OK] Quality switch to 480p updated src: {new_src}")
            assert 'q=480p' in new_src or 'sample_stream' in new_src, f"Expected 480p chunk query in src, got {new_src}"

        print("\n>>> ALL TESTS PASSED SUCCESSFULLY! <<<")
    finally:
        driver.quit()

if __name__ == '__main__':
    run_tests()
