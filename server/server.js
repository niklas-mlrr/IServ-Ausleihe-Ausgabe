'use strict';
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const selfsigned = require('selfsigned');
const qrcode = require('qrcode-terminal');
const { WebSocketServer } = require('ws');

const PORT = 3443;

function getAllLocalIPs() {
  const ips = [];
  for (const iface of Object.values(os.networkInterfaces()).flat()) {
    if (iface.family === 'IPv4' && !iface.internal) ips.push(iface.address);
  }
  return ips.length ? ips : ['127.0.0.1'];
}

async function getCert() {
  const certPath = path.join(__dirname, 'cert.pem');
  const keyPath = path.join(__dirname, 'key.pem');
  if (fs.existsSync(certPath) && fs.existsSync(keyPath)) {
    return { cert: fs.readFileSync(certPath, 'utf8'), key: fs.readFileSync(keyPath, 'utf8') };
  }
  console.log('Generating self-signed certificate...');
  const ips = getAllLocalIPs();
  const pems = await selfsigned.generate([{ name: 'commonName', value: ips[0] }], {
    days: 3650,
    keySize: 2048,
    extensions: [{ name: 'subjectAltName', altNames: ips.map(ip => ({ type: 7, ip })) }],
  });
  fs.writeFileSync(certPath, pems.cert);
  fs.writeFileSync(keyPath, pems.private);
  return { cert: pems.cert, key: pems.private };
}

async function main() {
  const { cert, key } = await getCert();
  const desktopClients = new Set();

  const mimeTypes = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css' };

  const server = https.createServer({ cert, key }, (req, res) => {
    if (req.method !== 'GET') { res.writeHead(405); res.end(); return; }
    const filePath = path.join(__dirname, 'public', req.url === '/' ? 'scanner.html' : req.url);
    fs.readFile(filePath, (err, data) => {
      if (err) { res.writeHead(404); res.end('Not found'); return; }
      res.writeHead(200, { 'Content-Type': mimeTypes[path.extname(filePath)] || 'text/plain' });
      res.end(data);
    });
  });

  const wss = new WebSocketServer({ server });

  wss.on('connection', (ws, req) => {
    const ip = req.socket.remoteAddress;

    ws.on('message', (raw) => {
      let msg;
      try { msg = JSON.parse(raw); } catch { return; }

      if (msg.type === 'register' && msg.role === 'desktop') {
        desktopClients.add(ws);
        ws.send(JSON.stringify({ type: 'ack' }));
        return;
      }

      if (msg.type === 'scan') {
        const barcode = String(msg.value || '').trim();
        if (!barcode) return;
        console.log(`[scan] "${barcode}" from ${ip} -> ${desktopClients.size} desktop(s)`);
        const payload = JSON.stringify({ type: 'scan', value: barcode });
        for (const client of desktopClients) {
          if (client.readyState === 1) client.send(payload);
        }
      }
    });

    ws.on('close', () => desktopClients.delete(ws));
  });

  server.listen(PORT, () => {
    const ips = getAllLocalIPs();
    const url = `https://${ips[0]}:${PORT}`;
    console.log(`\nBarcode server running at ${url}`);
    console.log('\nScan this QR code with your phone to open the scanner:\n');
    qrcode.generate(url, { small: true });
    console.log(`\nNote: Accept the self-signed certificate warning in your browser.`);
    console.log(`Desktop client connects to: wss://localhost:${PORT}/\n`);
  });
}

main().catch(err => { console.error(err); process.exit(1); });
