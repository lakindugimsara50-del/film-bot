#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# start.sh — VPS startup script
# Installs dependencies, then runs the streaming server and bot
# concurrently inside a single terminal / screen session.
# Usage:  chmod +x start.sh && ./start.sh
# ─────────────────────────────────────────────────────────────────

set -e  # Exit immediately on error

# ── 0. Load .env if present ────────────────────────────────────────
if [ -f .env ]; then
    # Export each non-comment line as a shell variable
    export $(grep -v '^#' .env | xargs)
fi

# ── 1. Install / upgrade Python dependencies ───────────────────────
echo "[start.sh] Installing Python dependencies..."
pip install --quiet --upgrade -r requirements.txt

# ── 2. Start the streaming FastAPI server in the background ────────
echo "[start.sh] Starting stream server on port 8080..."
uvicorn streaming.stream_server:app \
    --host 0.0.0.0 \
    --port 8080 \
    --log-level info &

STREAM_PID=$!
echo "[start.sh] Stream server PID: $STREAM_PID"

# Give the streaming server a moment to bind the port
sleep 2

# ── 3. Start the Telegram bot (foreground) ─────────────────────────
echo "[start.sh] Starting Telegram bot..."
python main.py

# ── 4. Cleanup: kill stream server when bot exits ──────────────────
echo "[start.sh] Bot exited. Stopping stream server (PID $STREAM_PID)..."
kill "$STREAM_PID" 2>/dev/null || true
