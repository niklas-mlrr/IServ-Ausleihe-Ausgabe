// web/scan-state.js — Modul-State, DOM-Element-Referenzen + reine Helfer
// Teil des scan.html-Frontends (siehe scan-state.js/scan-ws.js/scan-render.js,
// in dieser Reihenfolge nach html5-qrcode.min.js + common.js eingebunden).
// Kein Build-Step: alle drei Dateien teilen sich eine gemeinsame Top-Level-
// Scope (klassische <script>-Tags), zusätzlich exponiert auf window.__scan
// für Debug-/Introspektionszwecke.

window.__scan = window.__scan || {};

const statusEl = document.getElementById('status-text');
// Zentraler Setter: hält die Alert-Farbe strikt an den Alert-Text gebunden —
// jeder andere Statustext (z.B. "<Code> gesendet") setzt automatisch wieder
// die normale Schrift. `alertClass` ist eine der drei Farb-CSS-Klassen
// (`status-alert-red`/`status-alert-orange`/`status-book-issued`) oder
// `null` für normale Schrift — s. `statusAlertClass()` weiter unten, das sie
// aus DEMSELBEN `ALERT_META` ableitet, das auch die Fenster-Überschrift
// einfärbt (Statuszeile und Fenster können dadurch nicht mehr auseinanderlaufen).
// Nimmt PLAIN TEXT entgegen (kein HTML) — schreibt auf textContent, das
// Entities nicht interpretiert; escapeHtml()-te Strings hier wären falsch.
function setStatusText(text, alertClass = null) {
  statusEl.textContent = text;
  statusEl.classList.remove('status-alert-red', 'status-alert-orange', 'status-book-issued');
  if (alertClass) statusEl.classList.add(alertClass);
}
const dotEl = document.getElementById('dot');
const sNameEl = document.getElementById('s-name');
const sFormEl = document.getElementById('s-form');
const sPayEl = document.getElementById('s-pay');
const bookRowsEl = document.getElementById('book-rows');
let ws;
let studentActive = false;          // ist gerade ein Schüler zugewiesen?
let workerPending = false;          // Schüler zugewiesen, aber Worker noch nicht bereit
let queueSize = null;               // zuletzt gemeldete Warteschlangengröße
let queueList = [];                // wartende Schüler (Fallback, eigene Klasse)
let queueListAll = [];             // wie queueList, aber inkl. active/done (Fallback)
let loadingStudent = false;        // Schüler wird geladen (next/call gesendet,
                                  //  student_info steht noch aus) — Queue verbergen
let waitingMsg = 'Warte auf Schüler-Zuweisung';
let peeking = false;                // Menü-Toggle: Warteschlangen-Ansicht bei
let idleMenuOpen = false;           // Menü geöffnet OHNE Schüler (Idle): Kamera-
                                  //  zeile eingeklappt, Queue bleibt sichtbar.
// ---- Klassen-Reiter (Helfer-Menü): alle im Host offenen Klassen ----
// `contextsData` kommt vom Server (`contexts_update`): je Klasse id, form und
// ihre wartenden Schüler. `selectedCtxId` = gewählter Tab; `ownContextId` =
// Klasse, an die dieser Helfer gebunden (Vorauswahl beim Öffnen). Ist nichts
// geladen, fällt currentQueue() auf die eigene Klasse zurück (queueList).
let contextsData = [];
let selectedCtxId = null;
let ownContextId = null;
let queueView = false;             // .app.queue-view gesetzt? (Peek oder Idle)
                                  //  verbundenem Hintergrund-Schüler (kein Trennen)
// ---- Lupen-Suche (Peek-Modus): Schnellsprung zu beliebigem Schüler ----
// Klassen + Schüler pro Klasse kommen vom Server (IServ, read-only) und werden
// clientseitig für die Session gecacht. `searchOpen` = Panel ausgeklappt;
// `searchSubmitted` markiert eine laufende search_call-Antwort (lädt der neue
// Schüler, schließt das Panel + Menü bewusst — `call`/`next` lassen das Menü
// sonst offen). Letzte Klasse in localStorage für die Vorauswahl beim Öffnen.
let searchOpen = false;
let searchSubmitted = false;
let searchClassCache = null;                 // string[] aller Klassen des Schuljahrs
const searchStudentsCache = new Map();        // form -> Schüler-Array (IServ)
const SEARCH_LASTCLASS_KEY = 'ausleihe-search-lastclass';

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

// ---- Ausleih-Freigabe bei Unstimmigkeit (Nachweis fehlt / Rechnung offen) ----
// Rein client-seitig: pupil-Flags kommen mit `student_info` (GET, s. server/
// iserv_client.py). Beim ersten Scan eines betroffenen Schülers wird der Scan
// zurückgehalten und ein Bestätigungsdialog gezeigt, bevor server-seitig die
// Lager-/Anmeldeprüfung + Worker-Eintragung laufen. „Ja" merkt die Freigabe
// bis zum Neuladen des Schülers; „Nein" verwirft den Scan (nächster Scan fragt
// erneut). Kein DB-/IServ-Schreibzugriff.
let currentStudent = null;          // Schüler-Objekt aus dem letzten student_info
let lendingApproved = false;        // Freigabe für den aktuellen Schüler erteilt?
let heldScanValue = null;           // Scan, der auf die Freigabe-Entscheidung wartet
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
function currentQueue() {
  if (contextsData.length) {
    const c = contextsData.find(x => x.id === selectedCtxId) || contextsData[0];
    return (c && Array.isArray(c.queue)) ? c.queue : [];
  }
  return Array.isArray(queueList) ? queueList : [];
}
// Wie currentQueue(), aber inkl. der bereits aufgerufenen (active) und
// abgeschlossenen (done) Schüler des gewählten Tabs — für die Gruppen-Boxen
// unter der eigentlichen (wartenden) Warteschlange. Fällt auf currentQueue()
// zurück, falls der Server (alte Session) noch kein `queue_all` liefert.
function currentFullQueue() {
  if (contextsData.length) {
    const c = contextsData.find(x => x.id === selectedCtxId) || contextsData[0];
    if (c && Array.isArray(c.queue_all)) return c.queue_all;
  } else if (Array.isArray(queueListAll) && queueListAll.length) {
    return queueListAll;
  }
  return currentQueue();
}
function currentQueueSize() {
  return currentQueue().length;
}

// Vorauswahl des aktiven Tabs sichern: eigene Klasse (ownContextId) falls offen,
// sonst erste offene Klasse. Nur setzen, wenn noch keiner gewählt oder der
// gewählte nicht mehr existiert (Klasse zwischenzeitlich geschlossen).
function ensureSelectedCtx() {
  const ids = contextsData.map(c => c.id);
  if (selectedCtxId && ids.includes(selectedCtxId)) return;
  selectedCtxId = (ownContextId && ids.includes(ownContextId))
    ? ownContextId
    : (ids[0] || null);
}

// Klassen-Vorschlag: ist die eigene Klasse (ownContextId) leer, aber ein
// Reiter WEITER RECHTS (später in contextsData, gleiche Reihenfolge wie im
// Host) hat noch Wartende, wird dieser als Vorschlag markiert (s.
// renderQueueTabs) — „Weiter" springt dann direkt dorthin, statt aus der
// leeren eigenen Klasse zu ziehen (advanceToNext), und bindet den Helfer
// serverseitig an diese Klasse um (s. advance_helper in sessions.py).
function suggestedQueueContext() {
  if (!contextsData.length || !ownContextId) return null;
  const idx = contextsData.findIndex(c => c.id === ownContextId);
  if (idx === -1) return null;
  const own = contextsData[idx];
  if (own.queue && own.queue.length) return null;  // eigene Queue nicht leer -> kein Vorschlag
  for (let i = idx + 1; i < contextsData.length; i++) {
    if (contextsData[i].queue && contextsData[i].queue.length) return contextsData[i];
  }
  return null;
}

// Queue-Ansicht (Name-row verborgen, Queue-Header mit Klassen-Reitern sichtbar)
// gilt im Peek (Hintergrund-Schüler) und im Idle (kein Schüler). Während ein
// Schüler geladen wird (loadingStudent), ist sie aus — dann steht „wird geladen"
// im Buchbereich. syncQueueView leitet sie aus dem Helfer-Zustand ab; der Menü-
// Übergang (animateMenu) toggelt .queue-view bewusst selbst synchron zum FLIP.
function setQueueView(on) {
  queueView = on;
  appEl.classList.toggle('queue-view', on);
}
function syncQueueView() {
  setQueueView(peeking || (!studentActive && !loadingStudent));
}

// Ruhezustand der Statuszeile: solange kein Schüler geladen ist (und keiner
// gerade geladen wird), zeigt die Statuszeile immer die Warteschlangenlänge.
// Ist ein Schüler zugewiesen, aber der Worker noch nicht bereit, steht hier
// „Warten…" — die Bücherliste ist zwar schon da, aber Scans buchen erst nach
// `worker_ready`.
function setReadyStatus() {
  if (studentActive) setStatusText(workerPending ? 'Warten…' : 'Scanner bereit — Buch scannen');
  else renderWaitingStatus();
}

// escapeHtml, isBookDone: siehe common.js (vor scan.js eingebunden).
const token = new URLSearchParams(location.search).get('token');
let lastValue = '', cooldown = false, html5QrCode = null, currentCameraId = null, isTorchOn = false, isCameraRunning = false, isRestarting = false, soundEnabled = false;
const ICON_VOLUME_ON = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const ICON_VOLUME_OFF = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
const cameraSelect = document.getElementById('camera-select');
const torchBtn = document.getElementById('torch-btn');
const soundBtn = document.getElementById('sound-btn');
const reloadBtn = document.getElementById('reload-btn');
const gearBtn = document.getElementById('gear-btn');
const menuBtn = document.getElementById('menu-btn');
const printBtn = document.getElementById('print-btn');
const nextBtn = document.getElementById('next-btn');
const camDropdown = document.getElementById('cam-dropdown');
const readerEl = document.getElementById('reader');
const searchBtn = document.getElementById('search-btn');
const searchPanel = document.getElementById('search-panel');
const searchClassSel = document.getElementById('search-class');
const searchStudentSel = document.getElementById('search-student');
const appEl = document.querySelector('.app');
const topSectionEl = document.querySelector('.top-section');
const statusbarEl = document.querySelector('.status-bar');
const statusInnerEl = document.getElementById('status');
const nameRowEl = document.querySelector('.name-row');
const queueSlotEl = document.getElementById('queue-slot');
const queueTabsEl = document.getElementById('queue-tabs');
const bookWrapEl = document.querySelector('.book-table-wrap');
const rightColEl = document.querySelector('.right-col');
const bookAlertModal = document.getElementById('book-alert-modal');
const bookAlertTitleEl = document.getElementById('book-alert-title');
const bookAlertTextEl = document.getElementById('book-alert-text');
const bookAlertNoteEl = document.getElementById('book-alert-note');
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
const lendConfirmModal = document.getElementById('lend-confirm-modal');
const lendWarnEl = document.getElementById('lend-warn');
const modalLendYesBtn = document.getElementById('modal-lend-yes');
const modalLendNoBtn = document.getElementById('modal-lend-no');
let scanFlashTimeout = null;
const OK_STATUSES = new Set(['staged', 'booked']);
// status → {title, color} für das Hinweis-Modal.
const ALERT_META = {
  book_deleted:        { title: 'Ausgemustertes Buch gescannt',  color: '#f44336' },
  not_in_stock:        { title: 'Buch bereits verliehen',        color: '#f44336' },
  book_already_lent:   { title: 'Buch bereits an den Schüler verliehen', color: '#e69500' },
  series_already_lent: { title: 'Buchreihe bereits an den Schüler verliehen', color: '#e69500' },
  not_enrolled:        { title: 'Buch nicht bestellt',           color: '#e69500' },
  unknown_book:        { title: 'Buch unbekannt',                color: '#e69500' },
  not_ready:           { title: 'Buchliste noch nicht geladen',   color: '#e69500' },
  error:               { title: 'Fehler bei der Prüfung',         color: '#f44336' },
};
// Statuszeilen-Farbklasse für einen scan_result-Status — abgeleitet aus
// ALERT_META.color, damit Statuszeile und Fenster-Überschrift IMMER
// dieselbe Farbe haben. Rot ist reserviert für Status, die am Schüler-
// Client ein Schließen durch den Host erfordern (book_deleted, not_in_stock)
// — sowie error (technisches Problem, keine sichere Aussage möglich); alle
// anderen Alert-Status (bereits-an-dich-verliehen, nicht bestellt,
// unbekannter Code, Buchliste noch nicht geladen) sind orange, selbst
// schließbar. 'booked' ist grün, 'staged'/OK normal (kein Klassenname).
function statusAlertClass(status) {
  if (status === 'booked') return 'status-book-issued';
  if (OK_STATUSES.has(status)) return null;
  const meta = ALERT_META[status];
  return meta && meta.color === '#e69500' ? 'status-alert-orange' : 'status-alert-red';
}
function computeOpenBooks() {
  const vorgemerkt = currentBooks.filter(b => b.status !== 'ausgeliehen');
  const offen = vorgemerkt.filter(b => !(b.isbn && scannedIsbns.has(b.isbn)));
  return { vorgemerkt, offen };
}

// Hinweis auf offene Bücher in das Ziel-Element rendern oder ausblenden.
function studentHasUnstimmigkeit() {
  const s = currentStudent;
  if (!s || !s.enrolled) return false;   // „nicht angemeldet" bleibt außen vor
  return !!(s.remission_pending || s.exemption_pending || !s.paid);
}

const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const MENU_MS = reduceMotion ? 1 : 350;
const MENU_EASE = 'cubic-bezier(.22,.61,.36,1)';
// Steuer-Elemente, die im Menü-Modus verschwinden (Menü-Button + #status + Lupe + Weiter bleiben sichtbar).
const menuHideEls = [gearBtn, readerEl, rightColEl, printBtn];
// Inline-Props, die animateMenu temporär setzt → für Cleanup/Abbbruch zurücksetzen.
const MENU_INLINE_PROPS = ['position','left','top','width','height','margin','boxSizing',
  'display','flexDirection','flexWrap','alignItems','justifyContent','gap',
  'opacity','transition','transform'];
let menuAnimGen = 0;   // schützt vor Cleanup-Kollisionen bei schnellem Toggeln
const LAYOUT_PROPS = ['display','flexDirection','flexWrap','alignItems','justifyContent','gap'];

// Zur Introspektion/Debugging zusätzlich auf window.__scan verfügbar
// machen (rein additiv — der Code oben referenziert weiterhin die
// bare Bezeichner aus der gemeinsamen Skript-Scope, keine funktionale
// Abhängigkeit von window.__scan).
window.__scan.setStatusText = setStatusText;
window.__scan.statusAlertClass = statusAlertClass;
window.__scan.resetScannedState = resetScannedState;
window.__scan.drainScanWaiters = drainScanWaiters;
window.__scan.waitForScans = waitForScans;
window.__scan.currentQueue = currentQueue;
window.__scan.currentFullQueue = currentFullQueue;
window.__scan.currentQueueSize = currentQueueSize;
window.__scan.ensureSelectedCtx = ensureSelectedCtx;
window.__scan.suggestedQueueContext = suggestedQueueContext;
window.__scan.setQueueView = setQueueView;
window.__scan.syncQueueView = syncQueueView;
window.__scan.setReadyStatus = setReadyStatus;
window.__scan.computeOpenBooks = computeOpenBooks;
window.__scan.studentHasUnstimmigkeit = studentHasUnstimmigkeit;
