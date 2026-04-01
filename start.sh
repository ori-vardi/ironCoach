#!/usr/bin/env bash
# IronCoach — Start the server
# Kills any existing server on the port, then starts fresh.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Help ──────────────────────────────────────────
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: ./start.sh [options]"
    echo ""
    echo "Options:"
    echo "  --build    Build frontend before starting the server"
    echo "  -h, --help Show this help message"
    echo ""
    echo "Environment:"
    echo "  PORT       Server port (default: 8000)"
    exit 0
fi

# ── Optional frontend build ───────────────────────
if [ "$1" = "--build" ]; then
    echo "Building frontend..."
    cd "$SCRIPT_DIR/frontend"
    [ -d node_modules ] || npm install
    npm run build
    echo ""
fi

cd "$SCRIPT_DIR/backend"

# Default port (override with PORT env var or .env file)
PORT="${PORT:-8000}"

# Kill existing process on the port (if any)
EXISTING_PID=$(lsof -i :"$PORT" -t 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
    echo "Stopping existing server on port $PORT (PID: $EXISTING_PID)..."
    kill $EXISTING_PID 2>/dev/null || true
    sleep 1
fi

echo "Starting IronCoach on http://localhost:$PORT"
echo "Press Ctrl+C to stop."
echo ""

python3 server.py
