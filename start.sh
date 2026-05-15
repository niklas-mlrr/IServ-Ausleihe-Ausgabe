#!/bin/bash
# macOS/Linux launcher
cd "$(dirname "$0")"
DIR="$(pwd)"

# Check dependencies
if ! command -v node &>/dev/null; then echo "Error: Node.js not installed."; exit 1; fi
if ! command -v python3 &>/dev/null; then echo "Error: Python 3 not installed."; exit 1; fi
python3 -c "import websocket, pyautogui" 2>/dev/null || {
    echo "Installing Python dependencies..."
    pip3 install -r client/requirements.txt
}

if [[ "$(uname)" == "Darwin" ]]; then
    # Open server and client in separate Terminal windows
    osascript -e "tell app \"Terminal\" to do script \"cd '$DIR' && node server/server.js\""
    sleep 1
    osascript -e "tell app \"Terminal\" to do script \"cd '$DIR' && python3 client/client.py $*\""
else
    # Start server in background
    node server/server.js &
    SERVER_PID=$!
    sleep 1

    # Start desktop client (foreground — Ctrl+C stops both)
    trap "kill $SERVER_PID 2>/dev/null" EXIT
    python3 client/client.py "$@"
fi
