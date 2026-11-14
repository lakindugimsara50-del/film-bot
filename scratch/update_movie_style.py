with open('website/movie.html', 'r', encoding='utf-8') as f:
    content = f.read()

target = '''    @media (max-width: 600px) {
      .cs-breadcrumb-wrap,
      .cs-breadcrumb {
        display: none !important;
      }
      .player-toolbar {
        padding: 4px 0 6px !important;
        gap: 6px !important;
        background: transparent !important;
        border: none !important;
      }
      .player-quality-picker,
      .player-status-badge {
        display: none !important;
      }
      .server-tabs {
        width: 100% !important;
        gap: 6px !important;
      }
      .server-tab {
        padding: 6px 14px !important;
        font-size: 12px !important;
        border-radius: 6px !important;
      }
      .cs-center-rewind, .cs-center-forward { width: 44px; height: 44px; }
      .cs-center-playpause { width: 56px; height: 56px; font-size: 20px; }
      .cs-player-center-overlay { gap: 16px; }
      .vjs-control-brand-wrap { display: none !important; }
      .video-js .vjs-volume-panel { display: none !important; }
      .video-js .vjs-playback-rate { display: none !important; }
      .vjs-custom-seek-btn { display: none !important; }
      .vjs-custom-time-display {
        display: inline-flex !important;
        align-items: center !important;
        font-size: 11px !important;
        font-weight: 600 !important;
        letter-spacing: 0.3px !important;
        padding: 0 4px !important;
        white-space: nowrap !important;
        min-width: 76px !important;
        flex-shrink: 0 !important;
        margin-right: auto !important;
      }
      .vjs-custom-time-display .vjs-cur-time,
      .vjs-custom-time-display .vjs-dur-time {
        font-variant-numeric: tabular-nums !important;
        color: #ffffff !important;
        font-size: 11px !important;
      }
      .vjs-custom-time-display .vjs-time-slash {
        margin: 0 2px !important;
        color: rgba(255, 255, 255, 0.45) !important;
      }
      .vjs-super-quality-btn {
        padding: 0 3px !important;
        min-width: 38px !important;
        flex-shrink: 0 !important;
      }
      .vjs-super-quality-badge {
        padding: 2px 5px !important;
        font-size: 9.5px !important;
        border-radius: 4px !important;
      }
    }'''

replacement = '''    @media (max-width: 768px) {
      .cs-breadcrumb-wrap,
      .cs-breadcrumb {
        display: none !important;
      }
      .player-toolbar {
        padding: 6px 10px !important;
        gap: 6px !important;
        background: rgba(14, 15, 20, 0.96) !important;
        border-top-left-radius: 10px !important;
        border-top-right-radius: 10px !important;
        border-bottom: 1px solid rgba(255, 255, 255, 0.06) !important;
      }
      .player-quality-picker,
      .player-status-badge {
        display: none !important;
      }
      .server-tabs {
        width: 100% !important;
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
        gap: 6px !important;
        scrollbar-width: none !important;
        padding-bottom: 1px !important;
      }
      .server-tabs::-webkit-scrollbar { display: none !important; }
      .server-tab {
        padding: 5px 12px !important;
        font-size: 11.5px !important;
        border-radius: 6px !important;
        flex-shrink: 0 !important;
      }
      .cs-center-rewind, .cs-center-forward { width: 42px !important; height: 42px !important; }
      .cs-center-rewind svg, .cs-center-forward svg { width: 22px !important; height: 22px !important; }
      .cs-center-playpause { width: 52px !important; height: 52px !important; font-size: 18px !important; }
      .cs-player-center-overlay { gap: 14px !important; }
      .vjs-control-brand-wrap { display: none !important; }
      .video-js .vjs-control-bar { height: 44px !important; padding: 0 8px !important; }
      .video-js .vjs-volume-panel { display: none !important; }
      .video-js .vjs-playback-rate { display: none !important; }
      .vjs-custom-seek-btn { display: none !important; }
      .vjs-custom-time-display {
        display: inline-flex !important;
        align-items: center !important;
        font-size: 10.5px !important;
        font-weight: 700 !important;
        padding: 0 2px !important;
        white-space: nowrap !important;
        margin-right: auto !important;
      }
      .vjs-custom-time-display .vjs-cur-time,
      .vjs-custom-time-display .vjs-dur-time {
        font-variant-numeric: tabular-nums !important;
        color: #ffffff !important;
        font-size: 10.5px !important;
      }
      .vjs-custom-time-display .vjs-time-slash {
        margin: 0 2px !important;
        color: rgba(255, 255, 255, 0.45) !important;
      }
      .vjs-super-quality-btn {
        padding: 0 3px !important;
        min-width: 36px !important;
        flex-shrink: 0 !important;
      }
      .vjs-super-quality-badge {
        padding: 2px 5px !important;
        font-size: 9px !important;
        border-radius: 4px !important;
      }
    }'''

if target in content:
    content = content.replace(target, replacement, 1)
    with open('website/movie.html', 'w', encoding='utf-8', newline='') as f:
        f.write(content)
    print("SUCCESS: movie.html updated!")
else:
    print("ERROR: target not found in movie.html")
