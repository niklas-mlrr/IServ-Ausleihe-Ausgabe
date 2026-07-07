const statusEl = document.getElementById('status-text');
const dotEl = document.getElementById('dot');
const sNameEl = document.getElementById('s-name');
const sFormEl = document.getElementById('s-form');
const sPayEl = document.getElementById('s-pay');
const bookRowsEl = document.getElementById('book-rows');
let ws, reconnectDelay = 2000;
let studentActive = false;          // ist gerade ein Schüler zugewiesen?
let queueSize = null;               // zuletzt gemeldete Warteschlangengröße
let waitingMsg = 'Warte auf Schüler-Zuweisung';

// ---- Druck-Dialog / Buch-Status ----
let currentBooks = [];              // Buchliste des aktuellen Schülers
const scannedIsbns = new Set();     // ISBNs erfolgreich gescannter (gestageter) Bücher
const scanOrder = new Map();        // ISBN -> Scan-Sequenz (für „zuletzt ausgegeben oben")
let scanSeq = 0;
function resetScannedState() { scannedIsbns.clear(); scanOrder.clear(); scanSeq = 0; }
let bookOrder = [];                 // klassenweite ISBN-Reihenfolge (vom Host konfiguriert)
let slipSecondPageDefault = false;  // Host-Default für „Schüler-Leihschein" (2. Seite)
let pendingScans = 0;               // noch nicht quittierte Scans (Sequenzierung)
const scanWaiters = [];             // Resolver, die auf pendingScans===0 warten
let printThenNext = false;          // „Drucken & nächster Schüler" angeklickt?

function drainScanWaiters() {
  if (pendingScans <= 0) {
    pendingScans = 0;
    while (scanWaiters.length) scanWaiters.shift()();
  }
}

// Auf den Abschluss aller laufenden Scans warten, bevor die Liste verglichen
// wird (mit Sicherheits-Timeout, falls eine Quittung ausbleibt).
function waitForScans(timeoutMs = 3000) {
  if (pendingScans <= 0) return Promise.resolve();
  return new Promise(resolve => {
    let done = false;
    const fin = () => { if (!done) { done = true; resolve(); } };
    scanWaiters.push(fin);
    setTimeout(fin, timeoutMs);
  });
}

function renderWaitingStatus() {
  statusEl.textContent = (typeof queueSize === 'number')
    ? `Warteschlange: ${queueSize}`
    : waitingMsg;
}

// Ruhezustand der Statuszeile: solange kein Schüler geladen ist (und keiner
// gerade geladen wird), zeigt die Statuszeile immer die Warteschlangenlänge.
function setReadyStatus() {
  if (studentActive) statusEl.textContent = 'Scanner bereit — Buch scannen';
  else renderWaitingStatus();
}

// IServ-Strings nie ungefiltert per innerHTML einsetzen.
function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const token = new URLSearchParams(location.search).get('token');

// „Erledigt" = bereits ausgeliehen (IServ) ODER in dieser Session gescannt.
function isBookDone(b) {
  return b.status === 'ausgeliehen' || !!(b.isbn && scannedIsbns.has(b.isbn));
}

function renderBooks(books, animate = false) {
  if (!books || !books.length) {
    bookRowsEl.innerHTML = '<div class="book-empty">Keine Bücher hinterlegt</div>';
    return;
  }
  // Sortierung: erledigte (gescannt/ausgeliehen) nach unten. Offene Bücher
  // nach der klassenweit konfigurierten Reihenfolge (bookOrder; ohne Eintrag
  // ans Ende). Erledigte nach Ausgabedatum (jüngstes oben); gerade gescannte
  // ohne Datum stehen oben in der Gruppe. Original-Index als stabiler Tiebreak.
  const orderIndex = isbn => {
    const i = bookOrder.indexOf(isbn);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  // „Erledigt"-Rang, höher = weiter oben: gerade in dieser Session gescannte
  // Bücher zuerst (nach Scan-Reihenfolge, zuletzt gescanntes oben), darunter die
  // schon vorher ausgeliehenen nach Ausgabedatum (jüngstes oben).
  const SCAN_BASE = 1e15;   // > jeder Epoch-ms-Wert
  const doneRank = b => {
    if (b.isbn && scanOrder.has(b.isbn)) return SCAN_BASE + scanOrder.get(b.isbn);
    const t = b.distributed_at ? Date.parse(b.distributed_at) : NaN;
    return Number.isNaN(t) ? -1 : t;
  };
  // FLIP-Vorbereitung: alte Positionen je Buch (Original-Index als stabiler
  // Schlüssel) merken, BEVOR innerHTML ausgetauscht wird. Nur bei animate=true
  // (erfolgreicher Scan) — nicht beim initialen Laden oder reiner Settings-Änderung.
  const oldRects = new Map();
  if (animate) {
    bookRowsEl.querySelectorAll('.book-row[data-book-idx]').forEach(row => {
      oldRects.set(row.dataset.bookIdx, row.getBoundingClientRect());
    });
  }
  const ordered = books
    .map((b, i) => [b, i])
    .sort((a, b) => {
      const da = isBookDone(a[0]) ? 1 : 0, db = isBookDone(b[0]) ? 1 : 0;
      if (da !== db) return da - db;                       // erledigte nach unten
      if (da === 1) {                                      // beide erledigt → nach Rang
        const diff = doneRank(b[0]) - doneRank(a[0]);      // absteigend (jüngstes oben)
        if (diff) return diff;
      } else {                                             // beide offen → Klassen-Reihenfolge
        const diff = orderIndex(a[0].isbn) - orderIndex(b[0].isbn);
        if (diff) return diff;
      }
      return a[1] - b[1];
    });
  bookRowsEl.innerHTML = ordered.map(([b, idx]) => {
    const done = isBookDone(b);
    const icon = done
      ? '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
      : '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    return `<div class="book-row row-${done ? 'ausgeliehen' : 'vorgemerkt'}" data-book-idx="${idx}">`
      + `<div class="b-fach">${escapeHtml(b.subject)}</div>`
      + `<div class="b-title">${escapeHtml(b.title)}</div>`
      + `<div class="b-icon">${icon}</div></div>`;
  }).join('');
  // FLIP-Animation: jede Zeile, die schon da war, startet an ihrer alten
  // Position (translate) und fährt zur neuen (translate→0). Neue Zeilen
  // (z. B. nach Schülerwechsel) haben keinen alten Eintrag und erscheinen sofort.
  if (animate && oldRects.size) {
    const rows = bookRowsEl.querySelectorAll('.book-row[data-book-idx]');
    rows.forEach(row => {
      const old = oldRects.get(row.dataset.bookIdx);
      if (!old) return;  // neue Zeile — keine alte Position
      const cur = row.getBoundingClientRect();
      const dx = old.left - cur.left;
      const dy = old.top - cur.top;
      if (!dx && !dy) return;
      row.style.transition = 'none';
      row.style.transform = `translate(${dx}px, ${dy}px)`;
      row.offsetWidth;  // Reflow erzwingen, damit die Startposition greift
      row.style.transition = '';
      row.style.transform = '';
    });
  }
}

function handleServerMessage(msg) {
  if (msg.type === 'student_info') {
    studentActive = true;
    const s = msg.student;
    sNameEl.textContent = `${s.lastname}, ${s.firstname}`;
    sFormEl.textContent = (s.form || '').replace(/^Klasse\s+/i, '');
    // Bezahlt-/Offen-Status, ergänzt um „Nachweis fehlt"-Hinweise (Ermäßigung
    // bzw. Befreiung): Antrag gestellt, aber noch unentschieden — gleiche Farbe
    // wie „Offen". Reihenfolge: erst die Nachweise, dann der Offene Betrag.
    const payParts = [];
    if (!s.enrolled) {
      payParts.push('<span class="wait">Nicht angemeldet</span>');
    } else {
      const nachweis = s.remission_pending || s.exemption_pending;
      if (s.remission_pending)  payParts.push('<span class="unpaid">Ermäßigungsnachweis fehlt</span>');
      if (s.exemption_pending) payParts.push('<span class="unpaid">Befreiungsnachweis fehlt</span>');
      // „Bezahlt" entfallen, wenn ein Nachweis fehlt — der Hinweis geht vor.
      if (s.paid && !nachweis) {
        payParts.push('<span class="paid">Bezahlt</span>');
      } else if (!s.paid) {
        payParts.push(`<span class="unpaid">Offen: ${escapeHtml(s.amount_open)} €</span>`);
      }
    }
    sPayEl.innerHTML = payParts.join(' · ');
    if (Array.isArray(s.book_order)) bookOrder = s.book_order;
    currentBooks = s.books || [];
    resetScannedState();
    renderBooks(currentBooks);
    statusEl.classList.remove('status-book-deleted');
    closeBookAlertModal();
    statusEl.textContent = 'Scanner bereit — Buch scannen';
  } else if (msg.type === 'scan_result') {
    if (pendingScans > 0) pendingScans--;
    // Erfolgreicher Scan → Buch in der Liste als „erledigt" markieren:
    // 'booked' = tatsächlich gebucht (ALLOW_BOOKING an), 'staged' = nur ins
    // Feld gefüllt (Gate aus / read-only Betrieb).
    if ((msg.status === 'staged' || msg.status === 'booked') && msg.isbn) {
      scannedIsbns.add(msg.isbn);
      scanOrder.set(msg.isbn, ++scanSeq);   // zuletzt gescanntes zuoberst in „erledigt"
      renderBooks(currentBooks, true);     // FLIP: Zeilen an neue Position fahren
    }
    drainScanWaiters();
    // Ausgemustert / verliehen-an-andere / verliehen-an-sich-selbst → Statuszeile
    // deutlich rot, Prüfung greift server-seitig noch vor der Anmeldeprüfung.
    const isAlert = ALERT_STATUSES.has(msg.status);
    statusEl.classList.toggle('status-book-deleted', isAlert);
    statusEl.textContent = `${escapeHtml(msg.barcode)} — ${escapeHtml(msg.msg || msg.status)}`;
    if (isAlert) showBookAlertModal(msg);
  } else if (msg.type === 'settings') {
    slipSecondPageDefault = !!msg.slip_second_page;
    if (Array.isArray(msg.book_order)) {
      bookOrder = msg.book_order;
      if (studentActive) renderBooks(currentBooks);  // aktuelle Liste live umsortieren
    }
  } else if (msg.type === 'print_result') {
    printBtn.disabled = false;
    const detail = msg.ok
      ? `Leihschein: ${escapeHtml(msg.detail || 'gedruckt')}`
      : `Druck fehlgeschlagen: ${escapeHtml(msg.msg || '')}`;
    statusEl.textContent = detail;
    // „Drucken & nächster Schüler": nur bei erfolgreichem Druck weiterschalten.
    if (printThenNext) {
      printThenNext = false;
      if (msg.ok) advanceToNext();
    }
  } else if (msg.type === 'waiting') {
    studentActive = false;
    sNameEl.textContent = '';
    sFormEl.textContent = '';
    sPayEl.innerHTML = '';
    bookRowsEl.innerHTML = '';
    currentBooks = [];
    resetScannedState();
    pendingScans = 0;
    drainScanWaiters();
    statusEl.classList.remove('status-book-deleted');
    closeBookAlertModal();
    if (typeof msg.queue_size === 'number') queueSize = msg.queue_size;
    if (msg.msg) waitingMsg = msg.msg;
    renderWaitingStatus();
  } else if (msg.type === 'queue_update') {
    if (typeof msg.queue_size === 'number') queueSize = msg.queue_size;
    if (!studentActive) renderWaitingStatus();
  } else if (msg.type === 'error') {
    statusEl.textContent = 'Fehler: ' + (msg.msg || '');
    dotEl.className = 'dot err';
  }
}

function connect() {
  if (!token) {
    statusEl.textContent = 'Kein Token in der URL — vom Host QR-Code scannen';
    dotEl.className = 'dot err';
    return;
  }
  ws = new WebSocket(`wss://${location.host}/ws/scanner/${token}`);
  ws.onopen  = () => { dotEl.className = 'dot ok'; setReadyStatus(); reconnectDelay = 2000; };
  ws.onclose = () => {
    dotEl.className = 'dot err'; statusEl.textContent = 'Getrennt — neu verbinden…';
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(Math.round(reconnectDelay * 1.6), 30000);  // Backoff, Cap 30 s
  };
  ws.onerror = () => { dotEl.className = 'dot err'; statusEl.textContent = 'Verbindungsfehler'; };
  ws.onmessage = e => { try { handleServerMessage(JSON.parse(e.data)); } catch (_) {} };
}
connect();

let lastValue = '', cooldown = false, html5QrCode = null, currentCameraId = null, isTorchOn = false, isCameraRunning = false, isRestarting = false, soundEnabled = false;
const ICON_VOLUME_ON = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const ICON_VOLUME_OFF = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
const cameraSelect = document.getElementById('camera-select');
const torchBtn = document.getElementById('torch-btn');
const soundBtn = document.getElementById('sound-btn');
const reloadBtn = document.getElementById('reload-btn');
const gearBtn = document.getElementById('gear-btn');
const printBtn = document.getElementById('print-btn');
const nextBtn = document.getElementById('next-btn');
const camDropdown = document.getElementById('cam-dropdown');
const readerEl = document.getElementById('reader');
const bookAlertModal = document.getElementById('book-alert-modal');
const bookAlertTitleEl = document.getElementById('book-alert-title');
const bookAlertTextEl = document.getElementById('book-alert-text');
const bookAlertBorrowerEl = document.getElementById('book-alert-borrower');
const bookAlertCloseBtn = document.getElementById('book-alert-close');
const printModal = document.getElementById('print-modal');
const printWarnEl = document.getElementById('print-warn');
const slipCheck = document.getElementById('slip-second-page');
const modalPrintBtn = document.getElementById('modal-print');
const modalPrintNextBtn = document.getElementById('modal-print-next');
const modalCancelBtn = document.getElementById('modal-cancel');
const nextModal = document.getElementById('next-modal');
const nextWarnEl = document.getElementById('next-warn');
const modalNextConfirmBtn = document.getElementById('modal-next-confirm');
const modalNextCancelBtn = document.getElementById('modal-next-cancel');
let scanFlashTimeout = null;

// Nächster Schüler: aktuellen abschließen + nächsten aus der Queue laden.
// Alten Schüler sofort entfernen und "Wird geladen…" zeigen, auch während
// der neue Schüler serverseitig noch geladen wird.
function advanceToNext() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  sNameEl.textContent = '';
  sFormEl.textContent = '';
  sPayEl.innerHTML = '';
  bookRowsEl.innerHTML = '';
  currentBooks = [];
  resetScannedState();
  statusEl.classList.remove('status-book-deleted');
  statusEl.textContent = 'Wird geladen…';
  ws.send(JSON.stringify({ type: 'next' }));
}

function closeNextModal() { nextModal.classList.remove('show'); }

// Nächster-Schüler-Klick: bei noch offenen vorgemerkten Büchern erst einen
// Hinweis zeigen (Abbrechen / Nächster Schüler), sonst direkt weiterschalten.
async function requestNext() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!studentActive) { advanceToNext(); return; }
  statusEl.textContent = 'Prüfe Scans …';
  await waitForScans();
  setReadyStatus();
  const { vorgemerkt, offen } = computeOpenBooks();
  if (offen.length === 0) { advanceToNext(); return; }
  renderOpenWarning(nextWarnEl, vorgemerkt, offen);
  nextModal.classList.add('show');
}
nextBtn.addEventListener('click', requestNext);
modalNextConfirmBtn.addEventListener('click', () => { closeNextModal(); advanceToNext(); });
modalNextCancelBtn.addEventListener('click', closeNextModal);
nextModal.addEventListener('click', (e) => { if (e.target === nextModal) closeNextModal(); });

// ---- Buch-Hinweis-Modal (ausgemustert / verliehen-an-andere / verliehen-an-
// sich-selbst). Der Helfer schließt es selbst (Button/Klick-außerhalb/Escape/
// nächster Scan). Bei ausgemustert/verliehen-an-andere räumt der Schließen-
// Button zusätzlich die Host-Meldung auf (server: clear_book_alert); bei
// „an sich selbst verliehen" wird der Host gar nicht informiert → das Clear
// ist dort ein No-op. ----
const ALERT_STATUSES = new Set(['book_deleted', 'not_in_stock', 'series_already_lent']);
// status → {title, color} für das Hinweis-Modal.
const ALERT_META = {
  book_deleted:        { title: 'Ausgemustertes Buch gescannt',  color: '#f44336' },
  not_in_stock:        { title: 'Buch noch verliehen',           color: '#f44336' },
  series_already_lent: { title: 'Buch bereits an dich verliehen', color: '#e69500' },
};
function showBookAlertModal(msg) {
  const meta = ALERT_META[msg.status] || { title: 'Buch-Hinweis', color: '#f44336' };
  bookAlertTitleEl.textContent = meta.title;
  bookAlertTitleEl.style.color = meta.color;
  bookAlertTextEl.textContent = `${escapeHtml(msg.barcode)} — ${escapeHtml(msg.msg || meta.title)}`;
  // „currently lent to someone else": Name des aktuellen Ausleihers als
  // eigene Zeile (nur bei not_in_stock belegt; read-only aus /books/:code).
  // Bei book_deleted mit loaned_to → Ersatzanspruch-Hinweis statt „verliehen".
  if (msg.loaned_to) {
    bookAlertBorrowerEl.textContent = msg.status === 'book_deleted'
      ? `Ersatzanspruch: ${msg.loaned_to}`
      : `Aktuell verliehen an: ${msg.loaned_to}`;
    bookAlertBorrowerEl.hidden = false;
  } else {
    bookAlertBorrowerEl.textContent = '';
    bookAlertBorrowerEl.hidden = true;
  }
  bookAlertModal.classList.add('show');
}
function closeBookAlertModal() { bookAlertModal.classList.remove('show'); }
// Bewusstes Schließen durch den Helfer → zusätzlich Host-Meldung aufräumen.
// Guard: nur senden, wenn das Modal wirklich offen war (vermeidet redundante
// Clears bei Kontextwechseln, die ohnehin die Queue aufräumt).
function dismissBookAlert() {
  const wasOpen = bookAlertModal.classList.contains('show');
  closeBookAlertModal();
  if (wasOpen && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'clear_book_alert' }));
  }
}
bookAlertCloseBtn.addEventListener('click', dismissBookAlert);
bookAlertModal.addEventListener('click', (e) => { if (e.target === bookAlertModal) dismissBookAlert(); });

// ---- Druck-Dialog ----
function closePrintModal() { printModal.classList.remove('show'); }

// Dialog öffnen: erst auf Abschluss laufender Scans warten, dann Warnung
// (vorgemerkte, noch nicht gescannte Bücher) berechnen und Default setzen.
async function openPrintDialog() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!studentActive) { statusEl.textContent = 'Kein Schüler zugewiesen'; return; }
  statusEl.textContent = 'Prüfe Scans …';
  await waitForScans();
  setReadyStatus();
  const { vorgemerkt, offen } = computeOpenBooks();
  renderOpenWarning(printWarnEl, vorgemerkt, offen);
  slipCheck.checked = slipSecondPageDefault;
  printModal.classList.add('show');
}

// Vorgemerkte (status !== 'ausgeliehen'), in dieser Session noch nicht
// gescannte Bücher ermitteln. Rein lokal — kein IServ-/DB-Zugriff.
function computeOpenBooks() {
  const vorgemerkt = currentBooks.filter(b => b.status !== 'ausgeliehen');
  const offen = vorgemerkt.filter(b => !(b.isbn && scannedIsbns.has(b.isbn)));
  return { vorgemerkt, offen };
}

// Hinweis auf offene Bücher in das Ziel-Element rendern oder ausblenden.
function renderOpenWarning(el, vorgemerkt, offen) {
  if (offen.length > 0) {
    const items = offen.map(b =>
      `<li>${escapeHtml(b.subject ? b.subject + ' – ' : '')}${escapeHtml(b.title)}</li>`).join('');
    el.innerHTML =
      `Achtung: Erst ${vorgemerkt.length - offen.length} von ${vorgemerkt.length} `
      + `vorgemerkten Büchern gescannt.<ul>${items}</ul>`;
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}

// Leihschein drucken. Der Server holt das PDF read-only und druckt lokal
// (kein IServ-Submit); Ergebnis kommt als 'print_result' zurück.
function sendPrint(thenNext) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  printThenNext = thenNext;
  printBtn.disabled = true;
  statusEl.textContent = 'Leihschein wird gedruckt …';
  ws.send(JSON.stringify({ type: 'print', second_page: slipCheck.checked }));
  closePrintModal();
}

printBtn.addEventListener('click', openPrintDialog);
modalPrintBtn.addEventListener('click', () => sendPrint(false));
modalPrintNextBtn.addEventListener('click', () => sendPrint(true));
modalCancelBtn.addEventListener('click', closePrintModal);
printModal.addEventListener('click', (e) => { if (e.target === printModal) closePrintModal(); });
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (printModal.classList.contains('show')) closePrintModal();
  if (nextModal.classList.contains('show')) closeNextModal();
  if (bookAlertModal.classList.contains('show')) dismissBookAlert();
});

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

soundBtn.addEventListener('click', async () => {
  soundEnabled = !soundEnabled;
  if (soundEnabled) {
    soundBtn.innerHTML = ICON_VOLUME_ON;
    soundBtn.classList.add('sound-on');
    await initAudio();
    playBeep();
  } else {
    soundBtn.innerHTML = ICON_VOLUME_OFF;
    soundBtn.classList.remove('sound-on');
  }
});

// Zahnrad: Kamera-Dropdown auf/zu
gearBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  camDropdown.classList.toggle('open');
});
camDropdown.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => camDropdown.classList.remove('open'));

function onScanSuccess(value) {
  if (cooldown || value === lastValue) return;
  // Nächster Scan → evtl. offenes Hinweis-Modal bewusst schließen (auch Host
  // aufräumen); war keins offen, ist dismissBookAlert ein No-op.
  dismissBookAlert();
  if (soundEnabled) playBeep();
  lastValue = value; cooldown = true;
  setTimeout(() => { cooldown = false; lastValue = ''; }, 2000);
  statusEl.textContent = 'Gesendet: ' + value;
  if (ws && ws.readyState === WebSocket.OPEN) {
    pendingScans++;
    ws.send(JSON.stringify({ type: 'scan', value }));
  }
  if (navigator.vibrate) navigator.vibrate(80);
  readerEl.classList.add('scan-success');
  clearTimeout(scanFlashTimeout);
  scanFlashTimeout = setTimeout(() => readerEl.classList.remove('scan-success'), 1200);
}

// Load available cameras
Html5Qrcode.getCameras().then(cameras => {
  // Bevorzugt die hintere Ultra-Weitwinkel-Kamera (iOS: "Back Ultra Wide
  // Camera") — damit lassen sich auch dicke Bücher/stapelig liegende Codes
  // gut erfassen. Sonst beliebige Rückkamera, sonst erste Kamera.
  const preferred =
    cameras.find(c => /back ultra wide/i.test(c.label)) ||
    cameras.find(c => /back dual wide/i.test(c.label)) ||
    cameras.find(c => /back/i.test(c.label)) ||
    cameras[0];
  cameraSelect.innerHTML = cameras.map(c => `<option value="${escapeHtml(c.id)}" ${c === preferred ? 'selected' : ''}>${escapeHtml(c.label)}</option>`).join('');
  currentCameraId = preferred?.id;
  initScanner(currentCameraId);
}).catch(err => {
  cameraSelect.innerHTML = '<option>Keine Kamera gefunden</option>';
  cameraSelect.disabled = true;
});

async function initScanner(cameraId) {
  if (isRestarting) return;
  isRestarting = true;
  isCameraRunning = false;
  reloadBtn.disabled = true;
  reloadBtn.textContent = '...';
  statusEl.textContent = 'Kamera startet…';

  if (html5QrCode) {
    try { await html5QrCode.stop(); } catch (e) {}
    try { html5QrCode.clear(); } catch (e) {}
    html5QrCode = null;
  }

  html5QrCode = new Html5Qrcode('reader');
  try {
    await html5QrCode.start(
      cameraId,
      { fps: 15, aspectRatio: 2.0 },
      onScanSuccess,
      () => {}
    );
    isCameraRunning = true;
    currentCameraId = cameraId;
    isTorchOn = false;
    torchBtn.classList.remove('torch-on');
    if (ws && ws.readyState === WebSocket.OPEN) setReadyStatus();
    else statusEl.textContent = 'Neu verbinden…';
    const video = document.querySelector('#reader video');
    if (video && video.srcObject) {
      const track = video.srcObject.getVideoTracks()[0];
      const capabilities = track.getCapabilities?.();
      torchBtn.disabled = !(capabilities && capabilities.torch);
    }
  } catch (err) {
    console.error('Camera error:', err);
    statusEl.textContent = 'Kamerafehler — neu laden';
    dotEl.className = 'dot err';
  }

  reloadBtn.innerHTML = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';
  reloadBtn.disabled = false;
  isRestarting = false;
}

// Camera selector change
cameraSelect.addEventListener('change', () => {
  initScanner(cameraSelect.value);
  isTorchOn = false;
  torchBtn.classList.remove('torch-on');
  camDropdown.classList.remove('open');
});

// Torch toggle
torchBtn.addEventListener('click', async () => {
  const video = document.querySelector('#reader video');
  if (!video || !video.srcObject) return;
  const track = video.srcObject.getVideoTracks()[0];
  if (!track) return;
  try {
    await track.applyConstraints({ advanced: [{ torch: !isTorchOn }] });
    isTorchOn = !isTorchOn;
    torchBtn.classList.toggle('torch-on', isTorchOn);
  } catch (e) {
    console.log('Torch not supported');
  }
});

// Reload camera button (always visible)
reloadBtn.addEventListener('click', () => {
  if (currentCameraId) initScanner(currentCameraId);
});

// Auto-restart camera when phone wakes from standby
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentCameraId) {
    setTimeout(() => initScanner(currentCameraId), 300);
  }
});

// Fallback: detect frozen/black video and auto-restart
setInterval(() => {
  if (document.hidden || isRestarting || !currentCameraId) return;
  const video = document.querySelector('#reader video');
  if (video && (video.paused || video.ended || video.readyState < 2)) {
    initScanner(currentCameraId);
  }
}, 3000);

