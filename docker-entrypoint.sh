#!/bin/bash
set -e

PORT="${PORT:-7860}"

echo "=========================================="
echo " Starting Film Sub Bot & Stream Server    "
echo " Port: $PORT                              "
echo "=========================================="

# Start FastAPI Streaming Server in background on HF Spaces port
echo "[Entrypoint] Starting stream proxy on port $PORT..."
python -m uvicorn streaming.stream_server:app --host 0.0.0.0 --port "$PORT" --log-level info &
STREAM_PID=$!

# Wait for stream server to bind port
sleep 2

# Start Telegram Bot in foreground
echo "[Entrypoint] Starting Film Bot polling..."
python main.py &
BOT_PID=$!

# Trap signals for graceful shutdown
trap "kill -TERM $STREAM_PID $BOT_PID 2>/dev/null || true; exit 0" SIGINT SIGTERM

# Wait for any process to exit
wait -n $STREAM_PID $BOT_PID

exit $?
