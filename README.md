# Barcode Scanner

Scan barcodes on your phone, get them typed on your computer. A lightweight WebSocket bridge between a browser-based scanner and a desktop keyboard simulator.

## How it Works

1. **Server** (Node.js) – HTTPS + WebSocket server with self-signed certificate
2. **Phone** – Browser-based scanner using the camera (`html5-qrcode`)
3. **Desktop** – Python client that receives barcodes and types them as keyboard input

```
┌─────────────┐        ┌──────────────┐        ┌─────────────┐
│    Phone    │───────>│  Node Server │───────>│   Desktop   │
│   Camera    │  WS/WSS │   (HTTPS)    │  WS/WSS │ (simulates  │
│  Scanner UI │        │              │        │   typing)   │
└─────────────┘        └──────────────┘        └─────────────┘
```

## Setup

### Requirements
- Node.js (for server)
- Python 3 + pip (for desktop client)
- Phone with camera

### Install

```bash
cd server
npm install

cd ../client
pip install -r requirements.txt
```

## Usage

### Quick Start (macOS/Linux)

```bash
./start.sh
```

On first run, the server generates a self-signed certificate and prints a QR code. Scan it with your phone to open the scanner.

### Manual Start

**Terminal 1 – Server:**
```bash
cd server
npm start
```

**Terminal 2 – Desktop Client:**
```bash
cd client
python3 client.py
```

**Phone:**
- Scan the QR code shown in the server terminal, or
- Navigate to `https://<server-ip>:3443`
- Accept the self-signed certificate warning
- Grant camera permission

## Configuration

### Desktop Client Options

```bash
python3 client.py --port 3001    # Custom port
python3 client.py --no-enter     # Don't press Enter after typing
```

## Security

- Self-signed certificate auto-generated on first run
- Certificate includes all local IPs as SANs (including Tailscale)
- WebSocket connection between phone and desktop

## Project Structure

```
├── server/
│   ├── server.js       # HTTPS + WebSocket server
│   ├── package.json
│   └── public/
│       └── scanner.html  # Camera scanner UI
├── client/
│   ├── client.py       # Desktop keyboard simulator
│   └── requirements.txt
├── start.sh            # macOS/Linux launcher
└── start.bat           # Windows launcher
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Camera not working" | Use Chrome/Safari, not in-app browsers. Ensure HTTPS |
| "Can't connect" | Firewall: allow port 3443. Check IP hasn't changed |
| macOS: "permission denied" | Grant Accessibility permission to Terminal in System Settings |
| Certificate warning | Expected with self-signed certs; click "Advanced" → "Proceed" |

## License

MIT