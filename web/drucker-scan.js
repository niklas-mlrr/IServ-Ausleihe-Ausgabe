// Drucker-Scanner (`/drucker-scan`) — Scan-Gerät neben einem oder mehreren
// Druckern, mit dem ein Scan-Station-Schüler (Schülerauslöser) seinen
// Leihschein-Druckauftrag selbst auslöst. Verbindet sich unauthentifiziert
// via `/ws/drucker-scan` (s. server/routes/ws.py::ws_drucker_scan). Zeigt
// bewusst KEIN Scan-Ergebnis an — das steht ausschließlich am zugeordneten
// Drucker-Display (s. web/drucker-display.js). Kamera/Eingabeart wird vom
// Host vorgegeben (kein lokaler Umschalter am Gerät, s. `input_mode`).

const viewRegister = document.getElementById('view-register');
const viewScan = document.getElementById('view-scan');
const viewForbidden = document.getElementById('view-forbidden');
const connText = document.getElementById('conn-text');
const readerEl = document.getElementById('reader');

function show(name) {
  viewRegister.classList.toggle('show', name === 'register');
  viewScan.classList.toggle('show', name === 'scan');
  viewForbidden.classList.toggle('show', name === 'forbidden');
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
}

function applySystemTheme() {
  const light = matchMedia('(prefers-color-scheme: light)').matches;
  document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
}

function applyLabel(label) {
  const el = document.getElementById('dd-name');
  if (el) el.textContent = (label && label.trim()) ? label : '';
}

// Läuft oft ohne weitere Interaktion. Der Ladeversuch ist deshalb best
// effort; die erste Pointer-/Tastaturgeste kann einen vom Browser gesperrten
// AudioContext nachträglich freischalten (Autoplay-Regel) — Mirror
// drucker-display.js::primePrinterAudio.
function primeScanAudio() { void Beeper.initAudio(); }
primeScanAudio();
document.addEventListener('pointerdown', primeScanAudio, { once: true, passive: true });
document.addEventListener('keydown', primeScanAudio, { once: true });

// --- Scan-Eingabe: eine Kamera ODER ein manuelles Feld, nie beides. ---------
let html5QrCode = null;
let manualInput = null;
let inputModeApplied = null;
let currentSocket = null;
let cooldown = false;
let lastValue = '';
let scanCooldownTimer = null;

function send(msg) {
  if (currentSocket && currentSocket.readyState === WebSocket.OPEN) {
    currentSocket.send(JSON.stringify(msg));
  }
}

// Anzeigedauer eines Scan-Ergebnisses am zugeordneten Drucker-Display
// (`_SCANNER_RESULT_TTL_S` in server/routes/ws.py) — dieselbe Frist pausiert
// hier die Kamera: solange am Display noch das letzte Ergebnis steht, soll
// der nächste Schüler nicht schon versehentlich seinen Code hineinscannen.
const SCAN_COOLDOWN_MS = 10000;

// Ein Scan pro Cooldown-Fenster; derselbe Code erst nach Ablauf erneut
// (Spiegel des Dublettenschutzes in scan-station.js). Kein Warten auf eine
// Server-Antwort — der Scanner selbst zeigt kein Ergebnis (s. Modul-Kommentar).
// Während des Cooldowns pausiert die Kamera komplett (kein Decoding, kein
// Videobild) statt nur erkannte Codes zu ignorieren — Mirror-Verhalten für
// manuelle Eingabe: das Feld bleibt zwar bedienbar, `cooldown` verwirft den
// Wert aber genauso.
function onScanSuccess(value) {
  const code = String(value || '').trim().replace(/\*/g, '');
  if (!code || cooldown || code === lastValue) return;
  lastValue = code;
  cooldown = true;
  clearTimeout(scanCooldownTimer);
  if (html5QrCode) {
    try { html5QrCode.pause(true); } catch (e) { /* Kamera lief nicht/nicht pausierbar */ }
  }
  scanCooldownTimer = setTimeout(() => {
    cooldown = false;
    lastValue = '';
    if (html5QrCode) {
      try { html5QrCode.resume(); } catch (e) { /* Kamera inzwischen gestoppt/umgeschaltet */ }
    }
  }, SCAN_COOLDOWN_MS);
  // Sofortiges akustisches Feedback, sobald ein Code erkannt wurde — der
  // Scanner zeigt sonst nichts an (s. Modul-Kommentar), der Ton bestätigt
  // dem Schüler, dass der Scan angekommen ist.
  void Beeper.initAudio().then(() => Beeper.playBeep());
  send({ type: 'scan', code });
}

function submitManualInput() {
  if (!manualInput) return;
  const v = manualInput.value.trim();
  if (v) { manualInput.value = ''; onScanSuccess(v); }
  manualInput.focus();
}

async function stopCamera() {
  if (html5QrCode) {
    try { await html5QrCode.stop(); } catch (e) { /* Kamera lief nicht */ }
    try { html5QrCode.clear(); } catch (e) { /* Container schon leer */ }
    html5QrCode = null;
  }
}

async function startCamera() {
  readerEl.innerHTML = '';
  let cameras = [];
  try { cameras = await Html5Qrcode.getCameras(); } catch (e) { /* keine Kamera-Berechtigung */ }
  if (!cameras.length) return;
  const preferred = cameras.find(c => /back|rück/i.test(c.label)) || cameras[cameras.length - 1];
  html5QrCode = new Html5Qrcode('reader');
  try {
    await html5QrCode.start(preferred.id, { fps: 15, aspectRatio: 1.5 }, onScanSuccess, () => {});
  } catch (e) { /* Kamera-Start fehlgeschlagen — Gerät bleibt ohne Eingabe */ }
}

function startManual() {
  readerEl.innerHTML = '';
  const inp = document.createElement('input');
  inp.id = 'manual-input';
  inp.type = 'text';
  inp.autocomplete = 'off';
  inp.placeholder = 'Barcode scannen …';
  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      submitManualInput();
    }
  });
  readerEl.appendChild(inp);
  manualInput = inp;
  inp.focus();
}

async function setInputMode(mode) {
  const m = mode === 'manual' ? 'manual' : 'camera';
  if (m === inputModeApplied) return;
  inputModeApplied = m;
  document.body.classList.toggle('manual-mode', m === 'manual');
  if (m === 'manual') {
    await stopCamera();
    manualInput = null;
    startManual();
  } else {
    manualInput = null;
    await startCamera();
  }
}

// --- WebSocket ---------------------------------------------------------
const token = new URLSearchParams(location.search).get('token') || '';

applySystemTheme();

let unloading = false;
let unloadNotified = false;
function onUnload() {
  unloading = true;
  try {
    if (currentSocket && currentSocket.readyState === WebSocket.OPEN) {
      currentSocket.close(1001, 'page unload');
    }
  } catch (_) { /* no-op — Seite wird ohnehin entladen */ }
  if (!unloadNotified && navigator.sendBeacon) {
    unloadNotified = true;
    try {
      navigator.sendBeacon(`/api/drucker-scan/departed?token=${encodeURIComponent(token)}`);
    } catch (_) { /* no-op */ }
  }
}
window.addEventListener('pagehide', onUnload);
window.addEventListener('beforeunload', onUnload);

let forbidden = false;

function handleServerMessage(msg) {
  if (msg.type === 'forbidden') {
    forbidden = true;
    show('forbidden');
    return;
  }
  if ('theme' in msg) applyTheme(msg.theme);
  if (msg.type === 'registration') {
    document.getElementById('reg-code').textContent = msg.code || '····';
    show('register');
    if (msg.input_mode) setInputMode(msg.input_mode);
  } else if (msg.type === 'ready') {
    applyLabel(msg.label);
    show('scan');
    setInputMode(msg.input_mode);
  }
}

connectWebSocket(() => `wss://${location.host}/ws/drucker-scan?token=${encodeURIComponent(token)}`, {
  onSocket: (ws) => { currentSocket = ws; },
  onOpen: () => { connText.textContent = 'verbunden'; },
  onClose: (e, reconnect) => {
    connText.textContent = 'getrennt — neu verbinden…';
    if (!forbidden && !unloading) reconnect();
  },
  onError: () => { connText.textContent = 'Verbindungsfehler'; },
  onMessage: e => {
    let msg; try { msg = JSON.parse(e.data); } catch (_) { return; }
    handleServerMessage(msg);
  },
});

show('register');
