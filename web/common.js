// web/common.js — Gemeinsame Helfer für die Ausleihe-Ausgabe-Frontends
// (host.js, scan.js, student.html, qr-display.html). Kein Build-Step: als
// globale Funktionen/Objekte auf window verfügbar. MUSS per <script src>
// VOR den Skripten eingebunden werden, die diese Funktionen nutzen.

// IServ-Strings (Namen, Buchtitel, …) nie ungefiltert per innerHTML einsetzen.
function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// „Erledigt" = bereits ausgeliehen (IServ) ODER in dieser Session gescannt.
// scannedIsbns (Set) kommt als Parameter von der aufrufenden Seite — dort
// bleibt sie seitenweiter State, common.js hält keinen eigenen.
function isBookDone(b, scannedIsbns) {
  return b.status === 'ausgeliehen' || !!(b.isbn && scannedIsbns.has(b.isbn));
}

// ---- Beeper: Scan-Ton, kapselt AudioContext/-Buffer als Closure-State ----
// Gemeinsam für scan.js/student.html. Aufrufer prüfen weiterhin SELBST
// `soundEnabled`, AUSSERHALB von playBeep() — Beeper entscheidet nicht,
// ob geblept wird, nur wie.
const Beeper = (() => {
  let audioCtx = null, audioBuffer = null;
  async function initAudio() {
    if (audioBuffer) return;
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    // Silent buffer to unlock iOS AudioContext during user gesture
    const silence = audioCtx.createBuffer(1, 1, audioCtx.sampleRate);
    const silSrc = audioCtx.createBufferSource();
    silSrc.buffer = silence; silSrc.connect(audioCtx.destination); silSrc.start(0);
    await audioCtx.resume();
    const response = await fetch('/beep.mp3');
    const arrayBuf = await response.arrayBuffer();
    audioBuffer = await audioCtx.decodeAudioData(arrayBuf);
  }
  function playBeep() {
    if (!audioCtx || !audioBuffer) return;
    const src = audioCtx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(audioCtx.destination);
    src.start(0);
  }
  return { initAudio, playBeep };
})();

// ---- WebSocket-Reconnect mit Backoff ----
// Vereinheitlicht die (bis auf Callbacks/Delay identischen) connect()-Varianten
// aus scan.js/student.html/qr-display.html sowie connectWs() aus host.js.
//
// urlOrFn: WS-URL als String ODER als Funktion () => string (bei Bedarf pro
//   Reconnect neu ausgewertet — wichtig für student.html, wo sich der Token
//   nach einem Re-Join ändert).
// opts:
//   onSocket(ws)   — wird bei JEDER neuen Verbindung synchron mit der neuen
//                    WebSocket-Instanz aufgerufen (Aufrufer setzt hier i.d.R.
//                    seine eigene modulweite `ws`-Variable).
//   onOpen()       — wie ws.onopen, ohne den Delay-Reset (den übernimmt diese
//                    Funktion selbst).
//   onMessage(e)   — wie ws.onmessage.
//   onError()      — wie ws.onerror.
//   onClose(e, reconnect) — wie ws.onclose, bekommt zusätzlich `reconnect`
//                    (Funktion, optional mit ms-Override) zum gezielten
//                    Auslösen des nächsten Verbindungsversuchs. OHNE eigenen
//                    onClose-Handler wird automatisch reconnect() aufgerufen.
//                    Damit bleibt Raum für Sonderfälle (z. B. student.html:
//                    Close-Code 4006 = entwerteter Token → Re-Join statt
//                    normalem Reconnect; `finished` → gar nicht reconnecten).
//   initialDelay, maxDelay, backoffFactor — Backoff-Parameter (Default:
//     2000ms Start, 30000ms Deckel, ×1.6 je Versuch). Für einen festen
//     Delay ohne Backoff: backoffFactor: 1.
//
// Rückgabe: { reconnectNow() } — löst sofort einen neuen Verbindungsversuch
// aus (delay-Override 0), z. B. nach einem erfolgreichen Re-Join.
function connectWebSocket(urlOrFn, opts) {
  const {
    onSocket, onOpen, onMessage, onError, onClose,
    initialDelay = 2000, maxDelay = 30000, backoffFactor = 1.6,
  } = opts || {};
  let delay = initialDelay;

  function connect() {
    const url = typeof urlOrFn === 'function' ? urlOrFn() : urlOrFn;
    const socket = new WebSocket(url);
    if (onSocket) onSocket(socket);
    socket.onopen = () => { delay = initialDelay; if (onOpen) onOpen(); };
    socket.onmessage = e => { if (onMessage) onMessage(e); };
    socket.onerror = () => { if (onError) onError(); };
    socket.onclose = (e) => {
      const reconnect = (ms) => {
        setTimeout(connect, ms != null ? ms : delay);
        delay = Math.min(Math.round(delay * backoffFactor), maxDelay);
      };
      if (onClose) onClose(e, reconnect); else reconnect();
    };
  }
  connect();
  return { reconnectNow: () => connect() };
}
