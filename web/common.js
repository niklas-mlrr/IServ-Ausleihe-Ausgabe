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

// Komplette Statuszeile für einen scan_result. Bei tatsächlicher Buchung
// ('booked', ALLOW_BOOKING an) nicht die technische DOM-Best-effort-Meldung
// des Workers, sondern "<Buchcode> ausgegeben — <Fach> — <Titel>" (ohne
// Bindestrich zwischen Buchcode und "ausgegeben", anders als bei den übrigen
// Status-Meldungen). Zwei „an dich selbst verliehen"-Fälle — jeweils NUR der
// Buchtitel hinterm Bindestrich (`msg.title`, vom Server durchgereicht; NICHT
// `msg.msg`, das ist die technische, längere Server-Meldung):
// `book_already_lent` (genau dieses Exemplar) →
// "<Buchcode> bereits an <targetLabel> verliehen — <Titel>";
// `series_already_lent` (ein ANDERES Exemplar derselben Reihe) →
// "<Buchcode> Buchreihe bereits an <targetLabel> verliehen — <Titel>".
// `targetLabel` ("dich" am Schüler-Client — Default, der Schüler scannt sein
// EIGENES Buch — bzw. "den Schüler" am Helfer-Client, s. `scan-ws.js`, wo
// der Helfer scannt und der Bezug immer der zugewiesene Schüler ist, nie
// „dich" der Helfer). Ausgemustert zerfällt in zwei Fälle (`msg.loaned_to` —
// am Schüler-Client ohnehin immer null, Privatheit, fällt dort also immer
// auf den ersten Fall zurück): OHNE Ersatzanspruch →
// "<Buchcode> ausgemustert — <Titel>"; MIT Ersatzanspruch →
// "<Buchcode> Ersatzanspruch an <Nachname>, <Vorname> (<Klasse>) — <Titel>"
// statt der technischen `msg`. „An jemand anderen verliehen" (`not_in_stock`)
// → "<Buchcode> bereits verliehen — <Titel>" (ohne Name — der Schüler sieht
// nie WEM, s. process_scan). `books` ist die aktuelle Bücherliste
// (student_info/currentBooks) der aufrufenden Seite.
function scanResultStatusText(msg, books, targetLabel = 'dich') {
  if (msg.status === 'booked') {
    const book = (books || []).find(b => b.isbn === msg.isbn);
    const detail = book ? `${book.subject} — ${book.title}` : '';
    return `${msg.barcode} ausgegeben${detail ? ' — ' + detail : ''}`;
  }
  if (msg.status === 'book_already_lent') {
    return `${msg.barcode} bereits an ${targetLabel} verliehen — ${msg.title || ''}`;
  }
  if (msg.status === 'series_already_lent') {
    return `${msg.barcode} Buchreihe bereits an ${targetLabel} verliehen — ${msg.title || ''}`;
  }
  if (msg.status === 'book_deleted' && msg.loaned_to) {
    const last = msg.loaned_to_lastname, first = msg.loaned_to_firstname;
    const form = (msg.loaned_to_form || '').replace(/^Klasse\s+/i, '');
    const name = (last || first) ? `${last || ''}, ${first || ''}${form ? ` (${form})` : ''}` : msg.loaned_to;
    return `${msg.barcode} Ersatzanspruch an ${name} — ${msg.title || ''}`;
  }
  if (msg.status === 'book_deleted' && !msg.loaned_to) {
    return `${msg.barcode} ausgemustert — ${msg.title || ''}`;
  }
  if (msg.status === 'not_in_stock') {
    return `${msg.barcode} bereits verliehen — ${msg.title || ''}`;
  }
  return `${msg.barcode} — ${msg.msg || msg.status}`;
}

// ---- Beeper: Scan-Ton, kapselt AudioContext/-Buffer als Closure-State ----
// Gemeinsam für scan.js/student.html. Aufrufer prüfen weiterhin SELBST
// `soundEnabled`, AUSSERHALB von playBeep() — Beeper entscheidet nicht,
// ob geblept wird, nur wie.
const Beeper = (() => {
  let audioCtx = null, audioBuffer = null;
  async function initAudio() {
    if (audioBuffer) return;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      // Silent buffer to unlock iOS AudioContext during user gesture
      const silence = audioCtx.createBuffer(1, 1, audioCtx.sampleRate);
      const silSrc = audioCtx.createBufferSource();
      silSrc.buffer = silence; silSrc.connect(audioCtx.destination); silSrc.start(0);
      await audioCtx.resume();
      const response = await fetch('/beep.mp3');
      const arrayBuf = await response.arrayBuffer();
      audioBuffer = await audioCtx.decodeAudioData(arrayBuf);
    } catch (e) { /* Audio optional — Ton entfällt, Rest der Seite bleibt nutzbar */ }
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
