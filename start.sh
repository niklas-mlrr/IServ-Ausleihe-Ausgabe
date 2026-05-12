#!/bin/bash
# macOS/Linux launcher
cd "$(dirname "$0")"

# Check dependencies
if ! command -v node &>/dev/null; then echo "Error: Node.js not installed."; exit 1; fi
if ! command -v python3 &>/dev/null; then echo "Error: Python 3 not installed."; exit 1; fi
python3 -c "import websocket, pyautogui" 2>/dev/null || {
    echo "Installing Python dependencies..."
    pip3 install -r client/requirements.txt
}

# Start server in background
node server/server.js &
SERVER_PID=$!
sleep 1

# Start desktop client (foreground — Ctrl+C stops both)
trap "kill $SERVER_PID 2>/dev/null" EXIT
python3 client/client.py "$@"
