@echo off
cd /d "%~dp0"

where node >nul 2>&1 || (echo Error: Node.js not installed. && pause && exit /b 1)
where python >nul 2>&1 || (echo Error: Python not installed. && pause && exit /b 1)

python -c "import websocket, pyautogui" 2>nul || (
    echo Installing Python dependencies...
    pip install -r client\requirements.txt
)

start "Barcode Server" node server\server.js
timeout /t 2 /nobreak >nul
python client\client.py %*
