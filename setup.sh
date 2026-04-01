#!/usr/bin/env bash
# IronCoach — One-time setup script
# Installs Python dependencies and builds the React frontend.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== IronCoach Setup ==="
echo ""

# Check Python 3.11+
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.11+."
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MINOR" -lt 11 ]; then
    echo "ERROR: Python $PY_VERSION found, but 3.11+ is required."
    exit 1
fi

# Check Node/npm
if ! command -v npm &>/dev/null; then
    echo "ERROR: npm not found. Please install Node.js 18+ and npm."
    echo "       (needed only for building the frontend)"
    exit 1
fi

# Install Python dependencies
echo "[1/3] Installing Python dependencies..."
cd backend
pip3 install -r requirements.txt
cd ..

# Install frontend dependencies
echo "[2/3] Installing frontend dependencies..."
cd frontend
npm install

# Build frontend
echo "[3/3] Building frontend..."
npm run build
cd ..

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. (Optional) Install Claude CLI for AI features:"
echo "     npm install -g @anthropic-ai/claude-code && claude"
echo ""
echo "  2. Start the server:"
echo "     ./start.sh"
echo ""
echo "  3. Open http://localhost:8000 and create your admin account"
