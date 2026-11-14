import sys

# Read style.css
with open('website/assets/css/style.css', 'r', encoding='utf-8') as f:
    style_css = f.read()

# Locate the marker for MODERN CINEMA PLAYER TOOLBAR
marker = '/* ============================================================\n   MODERN CINEMA PLAYER TOOLBAR & QUICK DOWNLOAD STRIP\n   ============================================================ */'
if marker not in style_css:
    # Try with CRLF
    marker = '/* ============================================================\r\n   MODERN CINEMA PLAYER TOOLBAR & QUICK DOWNLOAD STRIP\r\n   ============================================================ */'

if marker not in style_css:
    print("ERROR: marker not found in style.css!")
    sys.exit(1)

parts = style_css.split(marker)
base_css = parts[0]

new_player_and_mobile_css = """/* ============================================================
   ULTRA-SLEEK CINEMA PLAYER & QUICK DOWNLOAD STRIP (DESKTOP)
   ============================================================ */
.cs-player-card {
  background: #000000 !important;
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
  border-radius: 14px !important;
  overflow: hidden !important;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.95), 0 0 50px rgba(229, 9, 20, 0.16) !important;
  transition: box-shadow 0.3s ease !important;
}
.cs-player-card:hover {
  box-shadow: 0 24px 70px rgba(0, 0, 0, 1), 0 0 60px rgba(229, 9, 20, 0.24) !important;
}

.player-toolbar {
  background: linear-gradient(180deg, rgba(22, 24, 32, 0.98) 0%, rgba(12, 13, 18, 0.98) 100%) !important;
  padding: 10px 18px !important;
  border-top-left-radius: 14px !important;
  border-top-right-radius: 14px !important;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
  backdrop-filter: blur(16px) !important;
  -webkit-backdrop-filter: blur(16px) !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 12px !important;
}

.server-tabs {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  flex-wrap: wrap !important;
}

.server-tab {
  background: rgba(255, 255, 255, 0.06) !important;
  color: #e2e8f0 !important;
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  padding: 6px 15px !important;
  border-radius: 8px !important;
  font-size: 12.5px !important;
  font-weight: 700 !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 7px !important;
  transition: all 0.22s cubic-bezier(0.25, 1, 0.5, 1) !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3) !important;
  cursor: pointer !important;
}
.server-tab:hover {
  background: rgba(255, 255, 255, 0.14) !important;
  color: #ffffff !important;
  border-color: rgba(255, 255, 255, 0.25) !important;
  transform: translateY(-1px) !important;
}
.server-tab.active {
  background: linear-gradient(135deg, #e50914 0%, #b81d24 100%) !important;
  color: #ffffff !important;
  border-color: rgba(255, 255, 255, 0.3) !important;
  box-shadow: 0 3px 14px rgba(229, 9, 20, 0.5) !important;
}

.server-tab.btn-trailer-pill {
  background: rgba(255, 193, 7, 0.1) !important;
  color: #ffc107 !important;
  border-color: rgba(255, 193, 7, 0.3) !important;
}
.server-tab.btn-trailer-pill:hover {
  background: rgba(255, 193, 7, 0.2) !important;
  color: #ffffff !important;
  border-color: #ffc107 !important;
}
.server-tab.btn-trailer-pill.active {
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%) !important;
  color: #ffffff !important;
  border-color: rgba(255, 255, 255, 0.3) !important;
  box-shadow: 0 3px 14px rgba(245, 158, 11, 0.5) !important;
}

.player-status-badge {
  background: rgba(34, 197, 94, 0.1) !important;
  border: 1px solid rgba(34, 197, 94, 0.3) !important;
  color: #4ade80 !important;
  font-size: 11.5px !important;
  font-weight: 700 !important;
  padding: 4px 10px !important;
  border-radius: 20px !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 6px !important;
}
.player-status-badge .pulse-indicator {
  width: 7px !important;
  height: 7px !important;
  border-radius: 50% !important;
  background: #22c55e !important;
  box-shadow: 0 0 8px #22c55e !important;
  animation: pulse-ring 1.8s infinite !important;
}

.btn-quality-trigger {
  background: rgba(255, 255, 255, 0.06) !important;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
  color: #ffffff !important;
  padding: 6px 14px !important;
  border-radius: 8px !important;
  font-size: 12.5px !important;
  font-weight: 700 !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 8px !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3) !important;
  transition: all 0.22s ease !important;
  cursor: pointer !important;
}
.btn-quality-trigger:hover {
  background: rgba(255, 255, 255, 0.12) !important;
  border-color: rgba(255, 255, 255, 0.3) !important;
  transform: translateY(-1px) !important;
}

.quick-dl-strip {
  background: linear-gradient(180deg, rgba(14, 15, 20, 0.98) 0%, rgba(10, 11, 15, 0.98) 100%) !important;
  border-top: 1px solid rgba(255, 255, 255, 0.08) !important;
  padding: 10px 18px !important;
  border-bottom-left-radius: 14px !important;
  border-bottom-right-radius: 14px !important;
  backdrop-filter: blur(16px) !important;
  -webkit-backdrop-filter: blur(16px) !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  flex-wrap: wrap !important;
  gap: 10px !important;
}
.quick-dl-label {
  font-size: 12.5px !important;
  font-weight: 800 !important;
  letter-spacing: 0.4px !important;
  display: inline-flex !important;
  align-items: center !important;
  gap: 7px !important;
  color: rgba(255, 255, 255, 0.85) !important;
}
.quick-dl-buttons {
  display: flex !important;
  align-items: center !important;
  gap: 8px !important;
  flex-wrap: wrap !important;
}
.quick-dl-pill {
  background: rgba(255, 255, 255, 0.06) !important;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
  color: #ffffff !important;
  padding: 6px 14px !important;
  border-radius: 8px !important;
  font-size: 12px !important;
  font-weight: 700 !important;
  gap: 8px !important;
  display: inline-flex !important;
  align-items: center !important;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35) !important;
  transition: all 0.22s cubic-bezier(0.25, 1, 0.5, 1) !important;
  text-decoration: none !important;
}
.quick-dl-pill:hover {
  background: linear-gradient(135deg, #e50914 0%, #ff2036 100%) !important;
  border-color: rgba(255, 255, 255, 0.3) !important;
  color: #ffffff !important;
  transform: translateY(-2px) !important;
  box-shadow: 0 6px 20px rgba(229, 9, 20, 0.6) !important;
}
.quick-dl-icon {
  color: #ff3b49 !important;
  font-size: 12px !important;
  transition: color 0.2s ease !important;
}
.quick-dl-pill:hover .quick-dl-icon {
  color: #ffffff !important;
}
.quick-dl-size {
  background: rgba(255, 255, 255, 0.12) !important;
  color: #ffc107 !important;
  padding: 2px 6px !important;
  border-radius: 5px !important;
  font-size: 10.5px !important;
  font-weight: 700 !important;
}
.quick-dl-pill:hover .quick-dl-size {
  background: rgba(0, 0, 0, 0.3) !important;
  color: #ffffff !important;
}

/* ============================================================
   RESPONSIVE MOBILE ENGINE: PHONES & TABLETS (<= 768px)
   Provides perfectly proportioned, beautiful, compact UI.
   Eliminates all oversized ("lokui wage") banners, headers & cards!
   ============================================================ */
@media (max-width: 768px) {
  /* 1. Global Viewport & Container Reset */
  html, body {
    overflow-x: hidden !important;
    width: 100% !important;
    max-width: 100vw !important;
  }
  :root {
    --header-h: 52px !important;
  }
  .cs-container, .container {
    padding: 0 12px !important;
    max-width: 100% !important;
  }
  .site-header {
    height: 52px !important;
    padding: 0 12px !important;
  }
  .logo-brand-img {
    height: 26px !important;
    max-width: 140px !important;
  }
  .header-actions .tg-btn {
    display: none !important;
  }

  /* 2. Compact Proportional Mobile Hero Banner */
  .hero,
  .hero.compact-hero {
    min-height: 44vh !important;
    max-height: 48vh !important;
    height: auto !important;
    padding: 0 14px 14px !important;
    margin-top: var(--header-h) !important;
    align-items: flex-end !important;
    justify-content: flex-start !important;
  }
  .hero .hero-backdrop,
  .hero.compact-hero .hero-backdrop {
    background-position: center 20% !important;
    background-size: cover !important;
  }
  .hero-content {
    gap: 5px !important;
    padding: 0 !important;
    max-width: 100% !important;
  }
  .hero-badge {
    font-size: 8.5px !important;
    padding: 2px 6px !important;
    letter-spacing: 0.5px !important;
  }
  .hero-title {
    font-size: clamp(17px, 5.2vw, 22px) !important;
    line-height: 1.2 !important;
    margin-bottom: 2px !important;
    display: -webkit-box !important;
    -webkit-line-clamp: 2 !important;
    -webkit-box-orient: vertical !important;
    overflow: hidden !important;
  }
  .hero-title-si {
    font-size: 11.5px !important;
    margin-bottom: 2px !important;
    display: -webkit-box !important;
    -webkit-line-clamp: 1 !important;
    -webkit-box-orient: vertical !important;
    overflow: hidden !important;
  }
  .hero-description {
    font-size: 11px !important;
    line-height: 1.35 !important;
    color: rgba(255, 255, 255, 0.72) !important;
    margin-bottom: 6px !important;
    display: -webkit-box !important;
    -webkit-line-clamp: 2 !important;
    -webkit-box-orient: vertical !important;
    overflow: hidden !important;
  }
  .hero-meta {
    gap: 4px !important;
    margin-bottom: 4px !important;
  }
  .hero-meta .badge {
    font-size: 8.5px !important;
    padding: 2px 5px !important;
  }
  .hero-btn-trailer,
  .hero-btn-download,
  .hero-sound-btn,
  .hero-edge-nav {
    display: none !important;
  }
  .hero-actions {
    margin-top: 2px !important;
    width: 100% !important;
  }
  .hero-btn-watch {
    display: inline-flex !important;
    width: auto !important;
    max-width: 190px !important;
    padding: 8px 18px !important;
    font-size: 13px !important;
    font-weight: 700 !important;
    border-radius: 6px !important;
    background: #e50914 !important;
    color: #ffffff !important;
    box-shadow: 0 4px 16px rgba(229, 9, 20, 0.5) !important;
    gap: 7px !important;
  }
  .hero-apple-pagination {
    bottom: 6px !important;
    gap: 5px !important;
  }
  .hero-apple-dot {
    width: 5px !important;
    height: 5px !important;
  }
  .hero-apple-dot.active {
    width: 15px !important;
  }

  /* 3. Sleek Sections & Horizontal Touch Carousels */
  .section {
    padding: 14px 0 6px !important;
  }
  .trending-section:first-of-type,
  body > .section:first-of-type,
  main > .section:first-of-type {
    padding-top: calc(var(--header-h) + 8px) !important;
  }
  .section-header {
    padding: 0 12px !important;
    margin-bottom: 8px !important;
  }
  .section-title {
    font-size: 15px !important;
    gap: 6px !important;
  }
  .section-title::before {
    width: 3px !important;
    height: 16px !important;
  }
  .section-link {
    font-size: 11.5px !important;
  }
  .carousel-wrap {
    overflow: hidden !important;
  }
  .carousel-track {
    padding: 4px 12px 14px !important;
    gap: 8px !important;
    -webkit-overflow-scrolling: touch !important;
    scroll-snap-type: x mandatory !important;
  }
  .carousel-track .movie-card {
    scroll-snap-align: start !important;
  }
  .carousel-arrow,
  .carousel-btn {
    display: none !important; /* Hide desktop arrow buttons on mobile */
  }

  /* 4. Proportionate Card Sizes on Mobile Carousels */
  .carousel-track.trending-track .movie-card {
    width: 124px !important;
    min-width: 124px !important;
    max-width: 124px !important;
  }
  .carousel-track.compact-track .movie-card,
  .carousel-track .movie-card {
    width: 104px !important;
    min-width: 104px !important;
    max-width: 104px !important;
  }
  .card-poster {
    border-radius: 6px !important;
  }
  .card-badges-left {
    top: 5px !important;
    left: 5px !important;
    gap: 3px !important;
  }
  .badge-top-left {
    font-size: 8px !important;
    padding: 2px 4px !important;
    border-radius: 4px !important;
    letter-spacing: 0.4px !important;
  }
  .badge-card-new {
    font-size: 7.5px !important;
    padding: 2px 4px !important;
    border-radius: 4px !important;
  }
  .badge-card-type {
    font-size: 7.5px !important;
    padding: 2px 4px !important;
    border-radius: 4px !important;
  }
  .badge-top-right {
    top: 5px !important;
    right: 5px !important;
    font-size: 9px !important;
    padding: 2px 5px !important;
    border-radius: 4px !important;
    gap: 3px !important;
  }
  .badge-top-right i {
    font-size: 8px !important;
  }
  .card-bottom-bar {
    padding: 6px 6px !important;
  }
  .card-title-text {
    font-size: 10.5px !important;
    line-height: 1.25 !important;
  }
  .card-sub-tag {
    font-size: 8.5px !important;
    gap: 3px !important;
  }
  .card-rank-num {
    font-size: 28px !important;
    bottom: 18px !important;
    left: 3px !important;
  }

  /* 5. Clean 3-Column Grid for Search, Categories & Related */
  .movies-grid,
  .movie-grid,
  #movie-grid,
  #movies-grid,
  #results-grid,
  #related-grid {
    grid-template-columns: repeat(3, 1fr) !important;
    gap: 8px !important;
    padding: 8px 12px !important;
  }
  .movie-grid .movie-card {
    width: auto !important;
    min-width: 0 !important;
  }

  /* 6. Mobile Movie Page & Player Toolbar */
  .cs-breadcrumb-wrap,
  .cs-breadcrumb {
    display: none !important;
  }
  .cs-movie-header {
    padding: 8px 0 6px !important;
  }
  .cs-main-title {
    font-size: 16px !important;
    line-height: 1.25 !important;
    margin-bottom: 3px !important;
  }
  .cs-title-sub {
    gap: 4px !important;
  }
  .cs-title-sub .cs-sub-badge,
  .cs-title-sub .cs-header-quality,
  .cs-title-sub .cs-header-year,
  .cs-title-sub .badge {
    font-size: 9px !important;
    padding: 2px 5px !important;
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
    display: none !important; /* Quality is inside Video.js bar on mobile */
  }
  .server-tabs {
    width: 100% !important;
    overflow-x: auto !important;
    flex-wrap: nowrap !important;
    gap: 6px !important;
    scrollbar-width: none !important;
    padding-bottom: 1px !important;
  }
  .server-tabs::-webkit-scrollbar {
    display: none !important;
  }
  .server-tab {
    padding: 5px 12px !important;
    font-size: 11.5px !important;
    border-radius: 6px !important;
    flex-shrink: 0 !important;
  }

  /* 7. Mobile Center Overlay Controls */
  .cs-center-playpause {
    width: 52px !important;
    height: 52px !important;
    font-size: 18px !important;
    box-shadow: 0 0 24px rgba(229, 9, 20, 0.7) !important;
  }
  .cs-center-rewind,
  .cs-center-forward {
    width: 42px !important;
    height: 42px !important;
  }
  .cs-center-rewind svg,
  .cs-center-forward svg {
    width: 22px !important;
    height: 22px !important;
  }
  .cs-player-center-overlay {
    gap: 14px !important;
  }

  /* 8. Mobile Video.js Bottom Control Bar */
  .video-js .vjs-control-bar {
    height: 44px !important;
    padding: 0 8px !important;
  }
  .vjs-control-brand-wrap,
  .video-js .vjs-volume-panel,
  .video-js .vjs-playback-rate,
  .vjs-custom-seek-btn {
    display: none !important;
  }
  .vjs-custom-time-display {
    display: inline-flex !important;
    align-items: center !important;
    font-size: 10.5px !important;
    font-weight: 700 !important;
    padding: 0 2px !important;
    white-space: nowrap !important;
    margin-right: auto !important;
  }
  .vjs-super-quality-btn {
    padding: 0 3px !important;
    min-width: 36px !important;
  }
  .vjs-super-quality-badge {
    padding: 2px 5px !important;
    font-size: 9px !important;
    border-radius: 4px !important;
  }

  /* 9. Mobile Quick Download Strip */
  .quick-dl-strip {
    padding: 8px 10px !important;
    border-bottom-left-radius: 10px !important;
    border-bottom-right-radius: 10px !important;
    gap: 5px !important;
  }
  .quick-dl-label {
    font-size: 11px !important;
    width: 100% !important;
    margin-bottom: 2px !important;
  }
  .quick-dl-buttons {
    width: 100% !important;
    overflow-x: auto !important;
    flex-wrap: nowrap !important;
    gap: 5px !important;
    scrollbar-width: none !important;
    padding-bottom: 2px !important;
  }
  .quick-dl-buttons::-webkit-scrollbar {
    display: none !important;
  }
  .quick-dl-pill {
    padding: 5px 10px !important;
    font-size: 11px !important;
    border-radius: 6px !important;
    flex-shrink: 0 !important;
  }
  .quick-dl-size {
    font-size: 9.5px !important;
    padding: 1px 4px !important;
  }

  /* 10. Mobile Movie Details Card */
  .cs-movie-details-section {
    padding: 18px 0 24px !important;
  }
  .cs-details-card {
    display: grid !important;
    grid-template-columns: 88px 1fr !important;
    gap: 10px !important;
    padding: 12px !important;
    border-radius: 10px !important;
    align-items: start !important;
  }
  .cs-poster-col {
    width: 88px !important;
    max-width: 88px !important;
    margin: 0 !important;
  }
  .cs-poster-img {
    border-radius: 6px !important;
  }
  .cs-poster-actions .cs-poster-play-btn,
  .cs-poster-actions .cs-poster-dl-btn {
    display: none !important; /* Already right next to player on mobile */
  }
  #movie-share-btn {
    display: flex !important;
    font-size: 10px !important;
    padding: 5px !important;
    border-radius: 6px !important;
    width: 100% !important;
    margin-top: 5px !important;
    justify-content: center !important;
  }
  .cs-details-title {
    font-size: 15.5px !important;
    margin-bottom: 2px !important;
    line-height: 1.25 !important;
  }
  .cs-details-title-si {
    font-size: 12px !important;
    margin-bottom: 4px !important;
  }
  .cs-badges-rail {
    gap: 4px !important;
    margin-bottom: 6px !important;
    flex-wrap: wrap !important;
  }
  .cs-badge-pill {
    font-size: 9.5px !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
  }
  .cs-genre-pills {
    gap: 4px !important;
    margin-bottom: 6px !important;
    flex-wrap: wrap !important;
  }
  .cs-genre-pill {
    font-size: 9.5px !important;
    padding: 2px 6px !important;
    border-radius: 4px !important;
  }
  .cs-synopsis-card {
    grid-column: 1 / -1 !important;
    margin-top: 6px !important;
    padding: 10px !important;
    border-radius: 8px !important;
  }
  .cs-synopsis-tab {
    font-size: 11px !important;
    padding: 4px 8px !important;
  }
  .cs-synopsis-text {
    font-size: 11.5px !important;
    line-height: 1.4 !important;
  }
  .cs-meta-grid {
    grid-template-columns: repeat(2, 1fr) !important;
    gap: 6px !important;
    margin-top: 6px !important;
  }
  .cs-micro-card {
    padding: 7px 8px !important;
    gap: 6px !important;
    border-radius: 7px !important;
  }
  .cs-micro-icon {
    width: 26px !important;
    height: 26px !important;
    font-size: 10.5px !important;
    border-radius: 6px !important;
  }
  .cs-micro-label {
    font-size: 8.5px !important;
  }
  .cs-micro-val {
    font-size: 10px !important;
  }

  /* 11. Mobile Downloads Section */
  .cs-downloads-section {
    padding: 16px 0 20px !important;
  }
  .cs-section-header {
    margin-bottom: 10px !important;
  }
  .cs-section-title {
    font-size: 15px !important;
  }
  .cs-section-desc {
    font-size: 11px !important;
  }
  .cs-dl-grid {
    grid-template-columns: 1fr !important;
    gap: 8px !important;
    margin-bottom: 14px !important;
  }
  .cs-dl-card {
    padding: 12px !important;
    gap: 8px !important;
    border-radius: 8px !important;
  }
  .cs-dl-quality {
    font-size: 14px !important;
  }
  .cs-dl-actions {
    grid-template-columns: 1fr 1fr !important;
    gap: 6px !important;
  }
  .btn-download-primary,
  .btn-download-secondary {
    padding: 7px 10px !important;
    font-size: 11.5px !important;
    border-radius: 6px !important;
    justify-content: center !important;
  }

  /* 12. Mobile Telegram Banner */
  .cs-tg-banner {
    padding: 10px 12px !important;
    gap: 10px !important;
    border-radius: 8px !important;
    flex-direction: row !important;
    align-items: center !important;
  }
  .cs-tg-icon {
    font-size: 24px !important;
  }
  .cs-tg-text h3 {
    font-size: 12.5px !important;
    margin: 0 !important;
  }
  .cs-tg-text p {
    display: none !important;
  }
  .cs-tg-join-btn {
    padding: 6px 12px !important;
    font-size: 11px !important;
    white-space: nowrap !important;
    border-radius: 6px !important;
  }
}
"""

updated_style_css = base_css + marker + "\n" + new_player_and_mobile_css

with open('website/assets/css/style.css', 'w', encoding='utf-8', newline='') as f:
    f.write(updated_style_css)

print("SUCCESS: website/assets/css/style.css enhanced successfully!")
