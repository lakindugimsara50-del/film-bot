# Google Drive Streaming & Multi-Quality Download Setup — Archive & 1-Click Restore Guide

This directory preserves the 100% complete Google Drive streaming and downloading architecture developed for FilmSub.
If you ever want to re-enable Google Drive uploads with a disposable/secondary Google account, all the code and instructions are right here.

---

## 📦 What is in this Archive?

1. **`stream.js`**:
   - Cloudflare Pages Function `/api/stream?id=<drive_file_id>&q=<quality>`.
   - Quality-adaptive HTTP 206 byte-range chunking (5MB - 10MB chunks).
   - Google Drive itag transcode engine (`itag=37` 1080p, `itag=59` 720p, `itag=22` 480p, `itag=18` 360p) for genuine distinct file sizes.
   - Client `AbortSignal` propagation to avoid bandwidth leaks.
   - Head probe optimization (`bytes=0-0` returns exact total `Content-Length` without downloading whole file).

2. **`download.js`**:
   - Cloudflare Pages Function `/api/download?id=<drive_file_id>&q=<quality>&title=<title>`.
   - Bypasses Google Drive's "Google Drive can't scan this file for viruses / OK" screen automatically by parsing `confirm=t`, `uuid`, and `at` tokens.
   - Multi-account OAuth2 token pool (supports `GDRIVE_REFRESH_TOKEN_1`, `_2`, `_3`).
   - Serves files with `Content-Disposition: attachment; filename="<Title> [<Quality>] - FilmSub.mp4"`.

3. **`cloud_drive/`**:
   - Python client library (`drive_manager.py`, `rclone_client.py`, `gdrive_client.py`, `onedrive_client.py`).
   - Supports 3 Google Drive accounts simultaneously with automatic load-balancing and quota inspection.

---

## 🔄 1-Click Restoration Steps

Whenever you wish to re-enable Google Drive storage:
1. In `bot/config.py`, set:
   ```python
   ENABLE_GDRIVE_UPLOAD = True
   ```
2. In Cloudflare Pages (`filmsub` project), ensure the secrets are set:
   - `GDRIVE_CLIENT_ID`
   - `GDRIVE_CLIENT_SECRET`
   - `GDRIVE_REFRESH_TOKEN_1` (or `GDRIVE_REFRESH_TOKEN`)
3. Copy `stream.js` and `download.js` back to `website/functions/api/`:
   ```powershell
   Copy-Item "backups/gdrive_setup_complete/stream.js" "website/functions/api/stream.js" -Force
   Copy-Item "backups/gdrive_setup_complete/download.js" "website/functions/api/download.js" -Force
   ```
4. Deploy to Cloudflare Pages:
   ```powershell
   npx wrangler pages deploy website --project-name=filmsub --branch=main
   ```
