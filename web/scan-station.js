// Scan-Station (`/scan-station`) — festes Scan-Gerät für Schüler ohne Handy.
//
// Der Scanmodus ist bewusst identisch zum Schülerclient (`student.js`,
// Ansicht „aktiv"): dieselbe obere Leiste (Hilfe/Zahnrad/Kamera/Torch/Ton),
// dieselbe Statuszeile mit denselben Farbregeln, dieselbe Bücher-Tabelle mit
// FLIP-Animation und dasselbe Buch-Hinweis-Modal. Aussehen: `scan-view.css`,
// gemeinsame Logik: `renderBookRows`/`renderBookAlert`/`statusAlertClass` in
// `common.js`. Hier steht nur, was die Station zusätzlich braucht:
//
//   1. Registrierung/Freischaltung durch den Host (wie beim Drucker-Display),
//   2. Anmeldung per vierstelligem Code vom gedruckten Zettel,
//   3. Rückfall auf „Zettel-Code scannen" nach 30 s Leerlauf.
//
// Nicht enthalten (bewusst): Leihschein-Druck und Abschließen — das bleibt
// bei Host/Helfer. Bei blockierenden Buch-Meldungen (ausgemustert/anderweitig
// verliehen) verhält sich die Station dagegen genau wie am Handy: kein
// eigener Schließen-Weg, nur der Host gibt per `/api/clear-book-alert` frei
// (`bookAlertOpen`); solange offen werden Kamera-Scan UND das manuelle
// Eingabefeld ignoriert (das Feld selbst bleibt bedienbar — weitere Codes
// eintippen/Enter geht, wirkt nur nicht, s. `onScanSuccess`). Der 30-s-
// Leerlauf-Timer (Punkt 3) wird für die Dauer einer solchen Meldung
// ausgesetzt und läuft erst mit der Host-Freigabe wieder frisch an
// (`freezeIdle`/`unfreezeIdle`) — bei allen anderen, selbst schließbaren
// Meldungen läuft er unverändert weiter. Jeder Scan
// während einer laufenden Anmeldung wird zuerst darauf geprüft, ob er
// eigentlich der Zettel-Code eines ANDEREN Schülers ist (Stationswechsel
// statt Buch-Scan) — s. `onScanSuccess`/server `routes/ws.py::ws_scan_station`.

const token = new URLSearchParams(location.search).get('token') || '';

// --- Zustand ---------------------------------------------------------------
let forbidden = false;        // vom Host per × gesperrt (kein Reconnect)
let authorized = false;       // vom Host freigeschaltet
let student = null;           // {lastname, firstname, form} solange angemeldet
let currentBooks = [];        // Bücherliste des angemeldeten Schülers
let bookOrder = [];           // klassenweite ISBN-Reihenfolge
const scannedIsbns = new Set();  // in dieser Anmeldung gescannte ISBNs
const scanOrder = new Map();     // ISBN -> Scan-Sequenz („zuletzt oben")
let scanSeq = 0;
let workerPending = false;    // Kartei lädt noch → Buch-Scans ignorieren
let scanInFlight = false, cooldown = false, lastValue = '';
let scanCooldownTimer = null;
// Ausgemustertes/anderweitig verliehenes Buch gescannt → blockierendes
// Hinweis-Modal wie am Handy: kein eigener Schließen-Weg, nur der Host gibt
// per `/api/clear-book-alert` frei. Solange offen wird jeder Scan/jede
// Eingabe ignoriert (s. `onScanSuccess`) — das Feld selbst bleibt aber
// bedienbar (weitere Codes eintippen + Enter drücken geht, wirkt nur nicht).
let bookAlertOpen = false;
let idleTtlS = 30;            // vom Server gemeldetes Leerlauf-TTL
let idleDeadline = 0, idleTimer = null;
// Leerlauf-Timer ausgesetzt, solange `bookAlertOpen` ein blockierendes Modal
// offen hält — s. `freezeIdle`/`unfreezeIdle`.
let idleFrozen = false;
// Eingabeart wie im Helferclient: 'camera' (Kamerabild) oder 'manual'
// (Tastatur-/Handscanner tippt in ein Feld). Lokal gemerkt, aber vom Host
// überschreibbar — genau wie das Theme.
const INPUT_MODE_KEY = 'scanStationInputMode';
let inputMode = 'camera';
try {
  if (sessionStorage.getItem(INPUT_MODE_KEY) === 'manual') inputMode = 'manual';
} catch (e) { /* privater Modus — Kamera-Default bleibt */ }
let inputModeApplied = null;  // zuletzt tatsächlich aufgebauter Modus
let manualInput = null;
let modeFromHost = false;
// ---- Druckermodus (analog student.js) --------------------------------
// `true`, sobald beim Laden der Bücherliste tatsächlich Bücher da waren —
// verhindert, dass eine (noch) leere Liste fälschlich als "alles erledigt"
// gilt.
let bookListHadBooks = false;
let druckmodusEntered = false;
// Fester 30-s-Abmelde-Timer nach Eintritt in den Druckermodus — kein Reset
// (die Station ist ein Gemeinschaftsgerät, kein persönliches Handy wie beim
// Schülerclient). Läuft unabhängig vom normalen 30-s-Leerlauftimer weiter.
let printLogoutTimer = null;

const $ = (id) => document.getElementById(id);

// --- Statuszeile (Helfer identisch zu student.js) ---------------------------
function setStatusText(text, alertClass = null) {
  const el = $('status-text');
  el.textContent = text;
  el.classList.remove('status-alert-red', 'status-alert-orange', 'status-book-issued');
  if (alertClass) el.classList.add(alertClass);
}

function show(view) {
  ['register', 'forbidden', 'print', 'scan'].forEach(v => {
    $('view-' + v).classList.toggle('show', v === view);
  });
}

// --- Druckermodus-Zentrierung (Port von student.js::positionScrollCenter) --
// Der Inhalt (.scroll-center) steht mittig bezogen auf die GESAMTE View-Höhe,
// außer das würde mit dem fix oben stehenden Namen (.print-name-row)
// kollidieren, dann direkt darunter. Ist der Inhalt selbst zu groß, scrollt
// nur dieser Bereich statt über den Bildschirmrand zu laufen.
function positionScrollCenter(view) {
  const nameRow = view.querySelector('.print-name-row');
  const wrap = view.querySelector('.scroll-center');
  const inner = wrap && wrap.firstElementChild;
  if (!wrap || !inner) return;
  const containerH = view.clientHeight;
  if (!containerH) return;  // View gerade nicht sichtbar (display:none)
  const minTop = nameRow ? nameRow.offsetTop + nameRow.offsetHeight : 0;
  const desiredTop = (containerH - inner.scrollHeight) / 2;
  const top = Math.max(desiredTop, minTop);
  wrap.style.top = `${top}px`;
  wrap.style.maxHeight = `${Math.max(0, containerH - top)}px`;
}

if (typeof ResizeObserver !== 'undefined') {
  const printView = $('view-print');
  const scrollCenterObserver = new ResizeObserver(() => positionScrollCenter(printView));
  scrollCenterObserver.observe(printView);
  const printBigMsg = printView.querySelector('.big-msg');
  if (printBigMsg) scrollCenterObserver.observe(printBigMsg);
} else {
  window.addEventListener('resize', () => positionScrollCenter($('view-print')));
}

// Schüler angemeldet ja/nein: Namenszeile + Tabelle gegen die Aufforderung
// „Zettel-Code scannen" tauschen. Die Kamera (#reader) bleibt in beiden
// Zuständen dasselbe Element und läuft durch.
function renderBinding() {
  const bound = !!student;
  $('ready-block').hidden = bound;
  $('student-row').hidden = !bound;
  $('book-wrap').hidden = !bound;
  if (bound) {
    $('s-name').textContent =
      [student.lastname, student.firstname].filter(Boolean).join(', ') || '–';
    $('s-form').textContent = (student.form || '').replace(/^Klasse\s+/i, '');
  }
}

function renderBooks(animate = false) {
  renderBookRows($('book-rows'), currentBooks,
                 { bookOrder, scannedIsbns, scanOrder, animate });
  // #book-wrap wird hier ggf. erstmals mit echtem Inhalt sichtbar (bzw.
  // seine Höhe ändert sich mit der neuen Zeilenzahl) — neu berechnen.
  updateBottomInsets();
}

// --- Druckermodus (Port von student.js::allVorgemerkteDone/maybeEnterDruckmodus) ---
// `true`, wenn alle aktuell sichtbaren Bücher erledigt sind (ausgeliehen oder
// in dieser Anmeldung gescannt). Ausgeblendete Reihen fallen aus
// `currentBooks` heraus und gelten daher nicht als offen.
function allVorgemerkteDone() {
  const relevantBooks = currentBooks.filter(
    b => b.status === 'vorgemerkt' || b.status === 'ausgeliehen',
  );
  return bookListHadBooks
    && relevantBooks.length === currentBooks.length
    && relevantBooks.every(b => isBookDone(b, scannedIsbns));
}

// Einmaliger Eintritt in den Druckermodus pro Anmeldung. Wird nach
// worker_ready, jedem erfolgreichen Scan und jeder booklist_update geprüft
// (dieselben drei Aufrufstellen wie in student.js).
function maybeEnterDruckmodus() {
  if (druckmodusEntered || !student) return;
  if (!allVorgemerkteDone()) return;
  druckmodusEntered = true;
  enterDruckmodus();
}

function enterDruckmodus() {
  stopIdle();  // ab hier zählt nur noch der feste 30-s-Abmelde-Timer unten
  $('print-name').textContent =
    [student.lastname, student.firstname].filter(Boolean).join(', ') || '–';
  $('print-form').textContent = (student.form || '').replace(/^Klasse\s+/i, '');
  $('print-title').textContent = 'Leihschein drucken';
  $('print-text').textContent = 'Wird geprüft…';
  $('print-countdown').textContent = '';
  show('print');
  updateFocusBanner();  // kein Eingabefeld im Druckermodus — Banner ggf. verbergen
  positionScrollCenter($('view-print'));
  send({ type: 'print_mode' });
}

// --- Buch-Hinweis-Modal (gemeinsame Implementierung, s. common.js) ---------
const bookAlertEls = {
  modal: $('book-alert-modal'), title: $('book-alert-title'), text: $('book-alert-text'),
  note: $('book-alert-note'), hint: $('book-alert-hint'), support: $('book-alert-support'),
  actions: $('book-alert-actions'),
};
// Ausgemustert/anderweitig verliehen (dismissible=false) → wie am Handy KEIN
// Schließen-Button, nur der Host gibt per `/api/clear-book-alert` frei. Alle
// anderen Meldungen (dismissible=true) schließt der Schüler selbst.
const showBookAlert = (msg, dismissible) => renderBookAlert(bookAlertEls, msg, dismissible);
const closeBookAlert = () => hideBookAlert(bookAlertEls);
$('book-alert-close').addEventListener('click', closeBookAlert);

$('help-btn').addEventListener('click', () => $('help-modal').classList.add('show'));
$('help-close').addEventListener('click', () => $('help-modal').classList.remove('show'));

// --- Leerlauf-Timer --------------------------------------------------------
// Der Server hat denselben 30-s-Timer als Sicherheitsnetz (Sweeper). Lokal
// läuft er zusätzlich, damit der Rückfall ohne Netz-Latenz sichtbar wird und
// die Restzeit angezeigt werden kann. Startet erst mit `worker_ready` —
// solange auf einen freien Platz gewartet wird, zählt nichts.
function touchIdle() {
  if (!student || workerPending) return;
  idleDeadline = Date.now() + idleTtlS * 1000;
}

function stopIdle() {
  clearInterval(idleTimer);
  idleTimer = null;
  idleDeadline = 0;
  idleFrozen = false;
  $('countdown').textContent = '';
}

function startIdle() {
  touchIdle();
  clearInterval(idleTimer);
  idleTimer = setInterval(() => {
    if (!student || !idleDeadline) return;
    const left = Math.ceil((idleDeadline - Date.now()) / 1000);
    $('countdown').textContent = left > 0 ? `Abmeldung in ${left}s` : '';
    if (left <= 0) release();
  }, 500);
}

// Blockierendes Buch-Hinweis-Modal (ausgemustert/anderweitig verliehen) offen
// → Leerlauf-Timer aussetzen, bis der Host per `/api/clear-book-alert`
// freigibt (`book_alert_clear`). Sonst würde die Station während der auf
// Betreuer-Entscheidung wartenden Meldung abgemeldet, obwohl der Schüler
// nichts falsch macht. Bei allen anderen (selbst schließbaren) Meldungen
// läuft der Timer unverändert weiter.
function freezeIdle() {
  if (idleFrozen) return;
  idleFrozen = true;
  clearInterval(idleTimer);
  idleTimer = null;
  $('countdown').textContent = '';
}

function unfreezeIdle() {
  if (!idleFrozen) return;
  idleFrozen = false;
  if (student && !workerPending) startIdle();
}

function release() {
  stopIdle();
  send({ type: 'release' });
}

// --- Server-Kommunikation --------------------------------------------------
let currentSocket = null;
function send(msg) {
  if (currentSocket && currentSocket.readyState === WebSocket.OPEN) {
    currentSocket.send(JSON.stringify(msg));
  }
}

// Statuszeile im Leerlauf (kein Schüler angemeldet) — eine Konstante, damit
// alle Rückfall-Wege denselben Text zeigen.
const READY_STATUS = 'Bitte Schülerbarcode scannen';

function resetToReady(msg, alertClass) {
  student = null;
  currentBooks = [];
  bookOrder = [];
  scannedIsbns.clear();
  scanOrder.clear();
  workerPending = false;
  scanInFlight = false;
  bookAlertOpen = false;
  bookListHadBooks = false;
  druckmodusEntered = false;
  clearInterval(printLogoutTimer);
  lastValue = '';
  stopIdle();
  closeBookAlert();
  renderBinding();
  show('scan');
  // Nach der Abmeldung (Timer/„Fertig"/Trennen) soll im manuellen Modus
  // sofort wieder ins Eingabefeld getippt werden können, ohne erst per Klick
  // fokussieren zu müssen — ein Handscanner tippt sonst ins Leere.
  if (inputMode === 'manual' && manualInput) manualInput.focus();
  updateFocusBanner();
  setStatusText(msg || READY_STATUS, alertClass);
}

function handleServerMessage(msg) {
  if (msg.theme) { themeFromHost = true; applyTheme(msg.theme); }
  // Host-Vorgabe der Eingabeart schlägt die lokale Wahl (wie beim Theme).
  if (msg.input_mode) { modeFromHost = true; setInputMode(msg.input_mode); }

  if (msg.type === 'forbidden') {
    forbidden = true;
    show('forbidden');
  } else if (msg.type === 'registration') {
    authorized = false;
    $('reg-code').textContent = msg.code || '····';
    // Vor der Freischaltung trägt die Station noch keinen Namen.
    $('station-name').textContent = '';
    show('register');
    // (Größenänderung durch den geleerten Namen fängt der ResizeObserver ab.)
  } else if (msg.type === 'ready') {
    authorized = true;
    if (typeof msg.idle_ttl_s === 'number') idleTtlS = msg.idle_ttl_s;
    $('station-name').textContent = (msg.label || '').trim();
    show('scan');
    resetToReady();
  } else if (msg.type === 'code_error') {
    scanInFlight = false;
    // `kind === 'book'`: ein Buch-Barcode wurde vor der Anmeldung gescannt
    // (egal welches Buch, der Server prüft nur, ob IServ den Code überhaupt
    // kennt) — gelb, der Schüler soll zuerst seinen Schülercode scannen.
    // Alles andere (unbekannter/ungültiger Code, abgelehnter Wechselversuch
    // während einer laufenden Sitzung) bleibt rot wie bisher.
    const alertClass = msg.kind === 'book' ? 'status-alert-orange' : 'status-alert-red';
    if (!student) {
      // Ungültiger/abgelehnter Zettel-Code vor der Anmeldung — zurück auf
      // „Zettel-Code scannen".
      resetToReady(msg.msg || 'Code ungültig.', alertClass);
    } else {
      // Fehlgeschlagener Wechselversuch (Code eines anderen Schülers während
      // einer laufenden Sitzung, z. B. der Zielschüler ist inzwischen fertig)
      // — der aktuell angemeldete Schüler bleibt unangetastet.
      setStatusText(msg.msg || 'Code ungültig.', alertClass);
    }
  } else if (msg.type === 'student_info') {
    student = msg.student || {};
    currentBooks = [];
    bookOrder = Array.isArray(msg.book_order) ? msg.book_order : [];
    scannedIsbns.clear();
    scanOrder.clear();
    workerPending = true;
    scanInFlight = false;
    bookAlertOpen = false;
    bookListHadBooks = false;
    druckmodusEntered = false;
    clearInterval(printLogoutTimer);
    closeBookAlert();
    renderBinding();
    $('book-rows').innerHTML = '<div class="book-empty">Bücher werden geladen…</div>';
    setStatusText('Wird geladen…');
  } else if (msg.type === 'worker_waiting') {
    setStatusText(`Warten auf freien Platz … (Position ${msg.position})`, 'status-alert-orange');
  } else if (msg.type === 'worker_ready') {
    workerPending = false;
    currentBooks = msg.books || [];
    bookListHadBooks = currentBooks.length > 0;
    if (Array.isArray(msg.book_order)) bookOrder = msg.book_order;
    renderBooks();
    setStatusText('Scanner bereit — Buch scannen');
    startIdle();
    maybeEnterDruckmodus();
  } else if (msg.type === 'booklist_update') {
    // Live-Nachzug der Bücherliste nach einer Ausblendungs-/Bestand-leer-
    // Änderung im Einstellungen-Dialog (Gegenstück zum Schülerclient, s.
    // `student.js`) — ersetzt nur die Liste + Reihenfolge, lässt den
    // Scan-Fortschritt (scannedIsbns/scanOrder) unangetastet.
    if (Array.isArray(msg.book_order)) bookOrder = msg.book_order;
    if (Array.isArray(msg.books)) currentBooks = msg.books;
    renderBooks();
    maybeEnterDruckmodus();
  } else if (msg.type === 'scan_result') {
    // Blockierende Meldungen (ausgemustert/anderweitig verliehen) halten
    // `scanInFlight` bewusst offen — wie am Handy endet die Sperre erst mit
    // der Host-Freigabe (`book_alert_clear`), nicht schon mit dieser Antwort.
    const blocking = BLOCKING_SCAN_STATUSES.has(msg.status);
    if (!blocking) scanInFlight = false;
    touchIdle();
    setStatusText(scanResultStatusText(msg, currentBooks), statusAlertClass(msg.status));
    if (blocking) { bookAlertOpen = true; showBookAlert(msg, false); freezeIdle(); }
    else if (!OK_SCAN_STATUSES.has(msg.status)) { showBookAlert(msg, true); }
    if (OK_SCAN_STATUSES.has(msg.status) && msg.isbn) {
      scannedIsbns.add(msg.isbn);
      scanOrder.set(msg.isbn, ++scanSeq);
      renderBooks(true);   // FLIP: Zeilen an neue Position fahren
      maybeEnterDruckmodus();
    }
  } else if (msg.type === 'print_mode_result') {
    // Antwort auf `{type:'print_mode'}` — s. `enterDruckmodus()`. Die Station
    // zeigt nur einen einmaligen Hinweis, keinen laufenden Fortschritt (die
    // Sichtbarkeit danach läuft ausschließlich über den Host). "Barcode"
    // bleibt ein eigener Platzhalter; alle anderen Fälle, in denen der
    // Auftrag NICHT sauber in der Warteschlange wartet (Betreuerauslöser
    // ODER kein erlaubter Drucker auf einem Display sichtbar — dann wurde gar
    // kein Auftrag erzeugt, s. server/routes/ws.py), zeigen denselben
    // Betreuer-Hinweis: der Host druckt in diesem Fall ganz normal über den
    // Druckbutton in "Aktuell in Ausgabe" (`station_print_needs_host`).
    let text;
    if (msg.trigger === 'barcode') {
      text = 'Druckmodus (Barcode) — folgt.';
    } else if (Array.isArray(msg.scanner_names) && msg.scanner_names.length) {
      // Schülerauslöser MIT erreichbarem Drucker-Scanner: kein Auftrag jetzt
      // — der Schüler muss dort seinen Schülercode scannen (s.
      // server/routes/ws.py::ws_drucker_scan). Namen kommasepariert, letzter
      // Name mit „oder" statt Komma.
      const names = msg.scanner_names;
      const list = names.length === 1
        ? names[0]
        : `${names.slice(0, -1).join(', ')} oder ${names[names.length - 1]}`;
      text = `Bitte scanne deinen Schülercode an ${list}, um deinen Leihschein zu drucken.`;
    } else if (msg.printer_available) {
      // Singular/Plural je nachdem, wie viele Drucker-Displays gerade
      // mindestens einen für diese Klasse erlaubten Drucker zeigen.
      const label = msg.display_count === 1 ? 'Druckeranzeige' : 'Druckeranzeigen';
      text = `Der Leihschein wartet in der Druckerwarteschlange. Bitte achte auf die ${label}.`;
    } else {
      text = 'Bitte wende dich an einen Betreuer, damit dein Leihschein gedruckt werden kann.';
    }
    // "Leihschein unterschreiben" ist eine Klassenoption, unabhängig davon,
    // wer letztlich druckt — nach einer Leerzeile angehängt (nicht beim
    // Barcode-Platzhalter, der ohnehin noch kein echter Ablauf ist).
    // #print-text nutzt white-space:pre-line, damit die Leerzeile wirkt.
    if (msg.done_signed && msg.trigger !== 'barcode') {
      const who = msg.recipient === 'teacher' ? 'Lehrer' : 'Betreuer';
      text += `\n\nNach dem Druck den Leihschein bitte unterschreiben und beim ${who} abgeben.`;
    }
    $('print-text').textContent = text;
    // Fester Abmelde-Timer, KEIN Reset — unabhängig davon, wie es mit dem
    // Druckauftrag (falls einer läuft) weitergeht; der bleibt am Host
    // sichtbar (s. PrintQueue.station_gate_snapshot).
    clearTimeout(printLogoutTimer);
    let left = 30;
    $('print-countdown').textContent = `Abmeldung in ${left}s`;
    printLogoutTimer = setInterval(() => {
      left -= 1;
      $('print-countdown').textContent = left > 0 ? `Abmeldung in ${left}s` : '';
      if (left <= 0) { clearInterval(printLogoutTimer); release(); }
    }, 1000);
  } else if (msg.type === 'book_alert_clear') {
    // Der Host gibt frei — nur das Modal schließt, die Statuszeile behält
    // Text UND Farbe bis zum nächsten Scan (wie im Schülerclient).
    bookAlertOpen = false;
    scanInFlight = false;
    closeBookAlert();
    unfreezeIdle();
  } else if (msg.type === 'released') {
    resetToReady();
  } else if (msg.type === 'error') {
    setStatusText(msg.msg || 'Fehler', 'status-alert-red');
  }
}

// --- Theme -----------------------------------------------------------------
// Ohne Host-Vorgabe folgt die Station der System-Einstellung des Geräts (wie
// der Schülerclient); sobald der Host ein Theme schickt, gewinnt dieses.
let themeFromHost = false;
function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
}

// --- Scanner (Aufbau aus student.js übernommen) ----------------------------
let html5QrCode = null, currentCameraId = null,
    isTorchOn = false, isRestarting = false, soundEnabled = false;
const cameraSelect = $('camera-select');
const ICON_VOLUME_ON = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const ICON_VOLUME_OFF = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
const torchBtn = $('torch-btn');
const soundBtn = $('sound-btn');
const reloadBtn = $('reload-btn');
const gearBtn = $('gear-btn');
const camDropdown = $('cam-dropdown');
const readerEl = $('reader');
let scanFlashTimeout = null;

gearBtn.addEventListener('click', (e) => { e.stopPropagation(); camDropdown.classList.toggle('open'); });
camDropdown.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => camDropdown.classList.remove('open'));

soundBtn.addEventListener('click', async () => {
  soundEnabled = !soundEnabled;
  soundBtn.innerHTML = soundEnabled ? ICON_VOLUME_ON : ICON_VOLUME_OFF;
  soundBtn.classList.toggle('sound-on', soundEnabled);
  if (soundEnabled) { await Beeper.initAudio(); Beeper.playBeep(); }
});

function flashReader() {
  readerEl.classList.add('scan-success');
  clearTimeout(scanFlashTimeout);
  scanFlashTimeout = setTimeout(() => readerEl.classList.remove('scan-success'), 1200);
}

// Ein Kameralauf für beide Zustände: vier Ziffern gelten als Zettel-Code,
// alles andere als Buch-Barcode. Die Buch-Barcodes im Bestand sind länger
// (s. Code.PNG), eine Verwechslung ist damit ausgeschlossen. Vor der Anmeldung
// (`!student`) entscheidet NICHT mehr die Form allein — jeder gescannte Wert
// geht an den Server, der zwischen bekanntem Zettel-Code, Buch-Barcode
// (`code_error.kind === 'book'`, egal welches Buch) und wirklich unbekanntem
// Code (`kind === 'invalid'`) unterscheidet (s. `code_error`-Handler unten).
function onScanSuccess(value) {
  const code = String(value || '').trim().replace(/\*/g, '');
  if (!code || !authorized || forbidden) return;
  // Dublettenschutz wie im Schülerclient: während ein Scan läuft gar nichts,
  // derselbe Code erst nach dem Cooldown erneut.
  if (scanInFlight || cooldown || code === lastValue) return;
  if (workerPending) return;  // Kartei lädt noch — Scan verwerfen
  // Blockierendes Hinweis-Modal offen (ausgemustert/anderweitig verliehen) —
  // wie am Handy erst der Host per `/api/clear-book-alert` freigeben lassen.
  // Kein Auto-Schließen durch den nächsten Scan (anders als bei den unten
  // dismissiblen Meldungen), der Scan wird komplett ignoriert.
  if (bookAlertOpen) return;
  if (!currentSocket || currentSocket.readyState !== WebSocket.OPEN) return;
  // Ein noch offener, selbst schließbarer Hinweis schließt sich beim
  // nächsten Scan selbst (blockierende sind oben bereits abgefangen).
  if (bookAlertEls.modal.classList.contains('show')) closeBookAlert();
  if (soundEnabled) Beeper.playBeep();
  lastValue = code;
  cooldown = true;
  clearTimeout(scanCooldownTimer);
  scanCooldownTimer = setTimeout(() => { cooldown = false; lastValue = ''; }, 2000);
  flashReader();
  if (navigator.vibrate) navigator.vibrate(80);
  touchIdle();

  if (!student) {
    // Auch während der Code-Prüfung: kein weiterer Scan/keine Eingabe, bis
    // die Antwort da ist (`student_info`/`code_error` löscht das wieder).
    scanInFlight = true;
    setStatusText(`${code} wird geprüft`);
    send({ type: 'student_code', value: code });
    return;
  }
  scanInFlight = true;
  setStatusText(`${code} wird geprüft`);
  send({ type: 'scan', value: code });
}

const ICON_ENTER = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>';
const ICON_TORCH = torchBtn.innerHTML;
const focusBanner = $('focus-banner');
const connEl = $('conn');
const stationNameEl = $('station-name');
const bookWrap = $('book-wrap');
const modeCameraBtn = $('mode-camera-btn');
const modeManualBtn = $('mode-manual-btn');

// Warnbanner, solange im manuellen Modus das Eingabefeld NICHT den Fokus hat —
// ein Handscanner tippt sonst ins Leere. Klick fokussiert es wieder. Im
// Druckermodus (view-print) gibt es kein Eingabefeld zu fokussieren (das
// manuelle Feld steckt in #reader, das dort verborgen ist) — der Banner
// bliebe sonst fälschlich stehen, bis die Station sich abmeldet.
function updateFocusBanner() {
  const show = inputMode === 'manual' && !!manualInput
    && document.activeElement !== manualInput
    && !document.querySelector('.modal-overlay.show')
    && !$('view-print').classList.contains('show');
  focusBanner.classList.toggle('show', show);
  // (Größenänderung des Banners fängt der ResizeObserver ab.)
}

// Abstand vom Fensterrand zum jeweiligen Beschriftungstext — muss zu
// #station-name{left:18px}/#conn{right:16px} in scan-view.css passen.
const STATION_NAME_EDGE_GAP = 18;
const CONN_EDGE_GAP = 16;
// Ab welcher Fensterbreite die Eck-Beschriftungen NEBEN statt ÜBER der (auf
// 728,5 px begrenzten, zentrierten) Liste stehen:
//   listMaxWidth + 2 × (2 × Abstand-Rand-zu-Beschriftung + Beschriftungsbreite)
// Da beide Seitenränder gleich groß sind (Liste ist zentriert), zählt nur die
// Beschriftung, die (Abstand + eigene Breite) am meisten braucht — die
// andere passt dann zwangsläufig auch.
function wideBreakpoint() {
  const bodyStyle = getComputedStyle(document.body);
  const listMaxWidth = parseFloat(bodyStyle.maxWidth)
    - parseFloat(bodyStyle.paddingLeft) - parseFloat(bodyStyle.paddingRight);
  const nameW = stationNameEl.offsetWidth;
  const connW = connEl.offsetWidth;
  const [gap, labelW] = nameW >= connW
    ? [STATION_NAME_EDGE_GAP, nameW]
    : [CONN_EDGE_GAP, connW];
  // listMaxWidth + 2 × (2 × Abstand + Beschriftung).
  return listMaxWidth + 2 * (2 * gap + labelW);
}

// Die beiden CSS-Variablen für den unteren Bildschirmrand setzen:
//   --focus-banner-h  Höhe des roten Fokus-Banners (0px, wenn aus). Schiebt
//                     die Eck-Beschriftungen nach oben, damit sie sichtbar
//                     bleiben.
//   --station-bar-h   Polster unter der Bücherliste, s. unten.
// Gemessen statt fest verdrahtet, weil Banner und Beschriftungen auf schmalen
// Displays umbrechen können. Zusätzlich bekommt die Bücherliste einen
// Ausblende-Verlauf zum unteren Rand (wie die Warteschlangen-Karte im
// Drucker-Display, s. dort `applyWaitingOverflow`): Bücher werden beim
// Herunterscrollen in Richtung Beschriftungen transparent, statt hart mit
// deren Text zu kollidieren.
function updateBottomInsets() {
  const root = document.documentElement.style;
  const bannerH = focusBanner.classList.contains('show') ? focusBanner.offsetHeight : 0;
  root.setProperty('--focus-banner-h', bannerH + 'px');

  // Fenster schmaler als der Umschlagpunkt → die Beschriftungen liegen über
  // der Liste und brauchen Platz; das Banner geht ohnehin über die volle
  // Breite und zählt immer.
  const labelsOverlapList = window.innerWidth < wideBreakpoint();
  let inset = bannerH;
  if (labelsOverlapList) {
    // Vom oberen Rand der jeweiligen Beschriftung bis zur Fensterunterkante —
    // schließt ihren 14-px-Abstand und ein evtl. eingeblendetes Banner mit ein.
    for (const el of [stationNameEl, connEl]) {
      inset = Math.max(inset, window.innerHeight - el.getBoundingClientRect().top + 6);
    }
  }
  inset = Math.round(Math.max(0, inset));
  root.setProperty('--station-bar-h', inset + 'px');

  // Ausblende-Verlauf (`.has-inset` in scan-station.html): NICHT mehr über
  // eine hier gemessene Pixelhöhe von #book-wrap berechnet — genau das war
  // die Ursache der „kurz richtig, dann falsch"-Serie (Flex-Layout/`100dvh`
  // erreichen ihre endgültige Höhe teils erst nach dem ersten Messzeitpunkt,
  // jede Momentaufnahme konnte also im nächsten Moment schon wieder veraltet
  // sein). Der Verlauf ist stattdessen rein CSS-relativ zur jeweils AKTUELLEN
  // Höhe der Liste (100%) — hier wird nur noch EIN Boolean geschaltet, ob er
  // überhaupt aktiv ist.
  bookWrap.classList.toggle('has-inset', inset > 0);
}

focusBanner.addEventListener('click', () => manualInput && manualInput.focus());
document.addEventListener('focusin', updateFocusBanner);
document.addEventListener('focusout', updateFocusBanner);

// Fensterbreite/-höhe ändert sich (Drehung, Größenänderung, virtuelle
// Tastatur) → direkt über das native `resize`-Event, nicht über einen auf
// `document.body` beobachtenden ResizeObserver: Body wechselt seine Breite
// zwar mit dem Fenster, aber ob/wann das zuverlässig als eigene
// ResizeObserver-Benachrichtigung ankommt, ist geräte-/browserabhängig
// (auf dem Tablet der Station kam dadurch nur noch der Fokus-Banner-Trigger
// zuverlässig an). `resize` ist dagegen der direkte, garantierte Weg zu genau
// dem Wert (`window.innerWidth`), den `wideBreakpoint()` ohnehin vergleicht.
window.addEventListener('resize', updateBottomInsets);
// `body { height: 100dvh }` — die dynamische Viewport-Einheit ändert sich auf
// Mobil-/Tablet-Browsern noch NACH dem Erst-Aufbau (Adressleiste/Werkzeugleiste
// klappt kurz nach dem Laden ein/aus), wodurch #book-wrap seine Höhe erst mit
// Verzögerung erreicht — genau das war der „Verlauf am Anfang zu weit unten"-
// Fall: die erste Berechnung traf noch die kleinere/größere Zwischenhöhe.
// `visualViewport`s eigenes `resize` reagiert darauf gezielter/zuverlässiger
// als das normale `window`-Resize.
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', updateBottomInsets);
}

// Die restlichen Größeneinflüsse (Beschriftungstext ändert sich, Fokus-Banner
// wird ein-/ausgeblendet) über einen ResizeObserver — das sind echte
// Element-Größenänderungen, dafür ist ResizeObserver das richtige Werkzeug.
// BEWUSST NICHT #book-wrap: `updateBottomInsets` setzt darauf selbst
// `padding-bottom` (--station-bar-h), was seine beobachtete Content-Box-Größe
// verändert — ein selbstausgelöster Resize. Der Browser erkennt das als
// Beobachtungs-Schleife und unterdrückt danach weitere Benachrichtigungen für
// den Observer (sichtbar als „Fenster bleibt immer schmal", war das Fenster
// beim Laden schmal). Der Moment, in dem #book-wrap erstmals mit echtem
// Inhalt sichtbar wird, ist ohnehin durch den expliziten Aufruf am Ende von
// `renderBooks()` abgedeckt.
if (typeof ResizeObserver !== 'undefined') {
  const insetObserver = new ResizeObserver(updateBottomInsets);
  [stationNameEl, connEl, focusBanner].forEach(el => insetObserver.observe(el));
}

// Manuell getippten Wert senden (Enter-Taste oder Enter-Button). Feld leeren
// und fokussiert halten — `onScanSuccess` übernimmt Dublettenschutz/Status.
function submitManualInput() {
  if (!manualInput) return;
  const v = manualInput.value.trim();
  if (v) { manualInput.value = ''; onScanSuccess(v); }
  manualInput.focus();
}

// Eingabeart umschalten — Aufbau 1:1 wie im Helferclient
// (`scan-render.js::setInputMode`): im manuellen Modus wird die Kamera
// gestoppt und ein Eingabefeld in `#reader` injiziert, der Taschenlampen-
// Knopf wird zum Enter-Knopf.
async function setInputMode(mode, { remember = true } = {}) {
  if (mode !== 'camera' && mode !== 'manual') return;
  if (mode === inputModeApplied) return;  // schon aufgebaut — nicht neu bauen
  inputMode = mode;
  inputModeApplied = mode;
  if (remember) { try { sessionStorage.setItem(INPUT_MODE_KEY, mode); } catch (e) { /* privater Modus */ } }
  document.body.classList.toggle('manual-mode', mode === 'manual');
  modeCameraBtn.classList.toggle('active', mode === 'camera');
  modeManualBtn.classList.toggle('active', mode === 'manual');
  camDropdown.classList.remove('open');

  if (mode === 'manual') {
    if (html5QrCode) {
      try { await html5QrCode.stop(); } catch (e) { /* Kamera lief nicht */ }
      try { html5QrCode.clear(); } catch (e) { /* Container schon leer */ }
      html5QrCode = null;
    }
    isTorchOn = false;
    torchBtn.classList.remove('torch-on');
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
    inp.addEventListener('focus', updateFocusBanner);
    inp.addEventListener('blur', updateFocusBanner);
    readerEl.appendChild(inp);
    manualInput = inp;
    torchBtn.innerHTML = ICON_ENTER;
    torchBtn.title = 'Eingabe senden (Enter)';
    inp.focus();
  } else {
    readerEl.innerHTML = '';
    manualInput = null;
    torchBtn.innerHTML = ICON_TORCH;
    torchBtn.title = 'Taschenlampe';
    torchBtn.disabled = false;
    if (currentCameraId) await initScanner(currentCameraId);
  }
  updateFocusBanner();
}

modeCameraBtn.addEventListener('click', () => setInputMode('camera'));
modeManualBtn.addEventListener('click', () => setInputMode('manual'));

async function initScanner(cameraId) {
  if (isRestarting) return;
  isRestarting = true;
  reloadBtn.disabled = true;
  if (html5QrCode) {
    try { await html5QrCode.stop(); } catch (e) { /* Kamera war nicht aktiv */ }
    try { html5QrCode.clear(); } catch (e) { /* Container schon leer */ }
    html5QrCode = null;
  }
  html5QrCode = new Html5Qrcode('reader');
  try {
    await html5QrCode.start(cameraId, { fps: 15, aspectRatio: 2.0 }, onScanSuccess, () => {});
    currentCameraId = cameraId;
    isTorchOn = false;
    torchBtn.classList.remove('torch-on');
    const video = document.querySelector('#reader video');
    if (video && video.srcObject) {
      const caps = video.srcObject.getVideoTracks()[0].getCapabilities?.();
      torchBtn.disabled = !(caps && caps.torch);
    }
  } catch (err) {
    setStatusText('Kamerafehler — Code bitte eintippen.', 'status-alert-red');
  }
  reloadBtn.disabled = false;
  isRestarting = false;
}

Html5Qrcode.getCameras().then(cameras => {
  const preferred = cameras.find(c => /back ultra wide/i.test(c.label))
    || cameras.find(c => /back dual wide/i.test(c.label))
    || cameras.find(c => /back/i.test(c.label))
    || cameras[0];
  cameraSelect.innerHTML = cameras
    .map(c => `<option value="${escapeHtml(c.id)}" ${c === preferred ? 'selected' : ''}>${escapeHtml(c.label)}</option>`)
    .join('');
  currentCameraId = preferred?.id;
  // Gemerkte/vorgegebene Eingabeart gewinnt; sonst Kamera starten.
  if (inputMode === 'manual') setInputMode('manual', { remember: false });
  else if (preferred) initScanner(preferred.id);
  else setStatusText('Keine Kamera gefunden — auf „Manuell" umschalten.', 'status-alert-red');
}).catch(() => {
  cameraSelect.innerHTML = '<option>Keine Kamera</option>';
  cameraSelect.disabled = true;
  // Kamera unbrauchbar — die manuelle Eingabe bleibt nutzbar.
  setInputMode('manual', { remember: false });
  setStatusText('Keine Kamera gefunden — Code bitte eintippen.', 'status-alert-red');
});

cameraSelect.addEventListener('change', () => {
  initScanner(cameraSelect.value);
  camDropdown.classList.remove('open');
});
reloadBtn.addEventListener('click', () => { if (currentCameraId) initScanner(currentCameraId); });
torchBtn.addEventListener('click', async () => {
  if (inputMode === 'manual') { submitManualInput(); return; }
  const video = document.querySelector('#reader video');
  if (!video || !video.srcObject) return;
  const track = video.srcObject.getVideoTracks()[0];
  try {
    await track.applyConstraints({ advanced: [{ torch: !isTorchOn }] });
    isTorchOn = !isTorchOn;
    torchBtn.classList.toggle('torch-on', isTorchOn);
  } catch (e) { /* Gerät kann kein Torch — Button bleibt wie er ist */ }
});
// Kamera nach Tab-Rückkehr neu starten (iOS pausiert den Stream im Hintergrund).
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentCameraId && inputMode === 'camera') {
    setTimeout(() => initScanner(currentCameraId), 300);
  }
});

// --- Manuelle Code-Eingabe --------------------------------------------------
$('manual-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const code = $('manual-code').value.trim();
  $('manual-code').value = '';
  if (!/^\d{4}$/.test(code)) {
    setStatusText('Bitte die vierstellige Nummer vom Zettel eingeben.', 'status-alert-orange');
    return;
  }
  setStatusText(`${code} wird geprüft`);
  send({ type: 'student_code', value: code });
});

// Jede Berührung/Eingabe hält die Sitzung offen — der Server bekommt das als
// `ping`, damit sein Sicherheitsnetz-Timer nicht vor dem lokalen zuschlägt.
['pointerdown', 'keydown'].forEach(evt => {
  document.addEventListener(evt, () => {
    if (!student || workerPending) return;
    touchIdle();
    send({ type: 'ping' });
  });
});

// --- Verbindung ------------------------------------------------------------
// Beim Entladen (Navigation/Tab schließen) den Server aktiv benachrichtigen —
// wie beim Drucker-Display, weil der Close-Frame dabei unzuverlässig ankommt.
let unloading = false, unloadNotified = false;
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
      navigator.sendBeacon(`/api/scan-station/departed?token=${encodeURIComponent(token)}`);
    } catch (_) { /* no-op — Fallback ist der uvicorn-Ping */ }
  }
}
window.addEventListener('pagehide', onUnload);
window.addEventListener('beforeunload', onUnload);

const connText = $('conn-text');
// Verbindungsstatus als reiner Text — wie beim Drucker-Display gibt es hier
// bewusst keinen farbigen Punkt.
function setConn(text) {
  connText.textContent = text;
  updateBottomInsets();
}

connectWebSocket(() => `wss://${location.host}/ws/scan-station?token=${encodeURIComponent(token)}`, {
  onSocket: (ws) => { currentSocket = ws; },
  onOpen: () => setConn('verbunden'),
  onClose: (e, reconnect) => {
    setConn('getrennt — neu verbinden…');
    if (!forbidden && !unloading) {
      setStatusText('Verbindung getrennt — neu verbinden…', 'status-alert-orange');
      reconnect();
    }
  },
  onError: () => setConn('Verbindungsfehler'),
  onMessage: e => {
    let msg;
    try { msg = JSON.parse(e.data); } catch (_) { return; }
    handleServerMessage(msg);
  },
});

show('register');
// Erste Messung explizit anstoßen — auf den garantierten Erst-Callback des
// ResizeObservers allein war kein Verlass (auf dem Stationsgerät blieb der
// Zustand bis zum nächsten Resize/Fokuswechsel falsch stehen). Elemente und
// CSS sind an dieser Stelle (Skript am Ende von <body>) bereits vollständig
// geparst/angewendet, ein synchroner Aufruf liefert also schon korrekte
// Maße — ResizeObserver + resize-Listener übernehmen danach alle Änderungen.
updateBottomInsets();
// Sicherheitsnetz gegen `100dvh`, das auf Mobil-/Tablet-Browsern erst mit
// Verzögerung seinen endgültigen Wert erreicht (Adress-/Werkzeugleiste klappt
// kurz nach dem Laden ein/aus, ohne dass zuverlässig ein Resize-Event dafür
// ankommt) — zwei zusätzliche Nachmessungen kurz nach dem Start.
setTimeout(updateBottomInsets, 300);
setTimeout(updateBottomInsets, 1200);
