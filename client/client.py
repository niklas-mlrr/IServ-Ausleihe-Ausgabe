#!/usr/bin/env python3
"""
Barcode desktop client — connects to the local server and types
each received barcode as keyboard input into the focused window.

macOS: grant Accessibility permission to Terminal (System Settings ->
       Privacy & Security -> Accessibility) the first time you run this.
Windows: run as normal user, no extra permissions needed.

Usage:
    pip install websocket-client pyautogui
    python client.py
    python client.py --no-enter   # don't press Enter after barcode
    python client.py --port 3001  # if you changed the server port
"""
import argparse
import json
import ssl
import sys
import time
import websocket
import pyautogui

pyautogui.PAUSE = 0.02
pyautogui.FAILSAFE = False

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=3443)
parser.add_argument('--no-enter', dest='enter', action='store_false', default=True,
                    help='Do not press Enter after typing the barcode')
args = parser.parse_args()

URL = f'wss://localhost:{args.port}/'


def on_message(ws, raw):
    try:
        msg = json.loads(raw)
    except Exception:
        return
    if msg.get('type') == 'scan':
        value = str(msg.get('value', '')).strip()
        if not value:
            return
        print(f'[scan] {value}')
        time.sleep(0.05)
        pyautogui.typewrite(value, interval=0.02)
        if args.enter:
            pyautogui.press('enter')


def on_open(ws):
    print(f'[connected] {URL}')
    ws.send(json.dumps({'type': 'register', 'role': 'desktop'}))


def on_error(ws, error):
    print(f'[error] {error}', file=sys.stderr)


def on_close(ws, code, msg):
    print('[disconnected] retrying in 3s...')


# Skip certificate verification since we use a self-signed cert
ssl_opts = {'cert_reqs': ssl.CERT_NONE}

print(f'Connecting to {URL} (waiting for scans...)')
while True:
    try:
        ws = websocket.WebSocketApp(URL, on_open=on_open, on_message=on_message,
                                    on_error=on_error, on_close=on_close)
        ws.run_forever(sslopt=ssl_opts, reconnect=3)
    except KeyboardInterrupt:
        print('Exiting.')
        sys.exit(0)
    except Exception as e:
        print(f'[fatal] {e} — retrying in 5s', file=sys.stderr)
        time.sleep(5)
