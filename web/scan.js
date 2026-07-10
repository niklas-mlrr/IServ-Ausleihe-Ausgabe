const statusEl = document.getElementById('status-text');
// Zentraler Setter: hält die Alert-Farbe (Ausgemustert/anderweitig verliehen)
// strikt an den Alert-Text gebunden — jeder andere Statustext (z.B. "<Code>
// gesendet") setzt automatisch wieder die normale Schrift.
function setStatusText(text, isAlert = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle('status-book-deleted', isAlert);
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

function renderWaitingStatus() {
  const n = currentQueueSize();
  setStatusText((n > 0 || contextsData.length || queueSize != null)
    ? `Warteschlange: ${n}`
    : waitingMsg);
}

// Peek-Statuszeile: Warteschlange angezeigt, aber der zugewiesene Schüler ist
// noch im Hintergrund verbunden. Name/Klasse stehen (weiterhin) in den
// s-name/s-form-Elementen — die Bücherliste wird durch die Queue ersetzt, der
// Schüler verschwindet nicht. Status zeigt ihn plus die aktuelle Queue-Größe.
function renderPeekStatus() {
  const name = sNameEl.textContent.trim();
  const form = sFormEl.textContent.trim();
  const who = name ? `${name}${form ? ` (${form})` : ''} im Hintergrund` : 'Schüler im Hintergrund';
  setStatusText(`${who} — Warteschlange: ${currentQueueSize()}`);
}

// Aktuell gewählte Klassen-Queue: aus den Klassen-Reitern (contextsData) oder
// Fallback auf die eigene Klasse (queueList, falls noch keine Kontext-Übersicht
// vom Server vorliegt). currentQueueSize() ist die Anzahl der wartenden Schüler
// des gewählten Tabs — für die Statuszeile und die Queue-Anzeige.
function currentQueue() {
  if (contextsData.length) {
    const c = contextsData.find(x => x.id === selectedCtxId) || contextsData[0];
    return (c && Array.isArray(c.queue)) ? c.queue : [];
  }
  return Array.isArray(queueList) ? queueList : [];
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
      const da = isBookDone(a[0], scannedIsbns) ? 1 : 0, db = isBookDone(b[0], scannedIsbns) ? 1 : 0;
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
    const done = isBookDone(b, scannedIsbns);
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

// Warteschlange anzeigen, solange kein Schüler zugewiesen ist — gleiche
// Zeilenform wie die Bücherliste, aber ohne Farbgebung (kein vorgemerkt/-
// ausgeliehen-Tint) und mit „Aufrufen"-Button pro Zeile statt des Status-Icons.
// Klick ruft genau diesen Schüler gezielt auf (WS `call`) — read-only gegen
// IServ/DB; nur die lokale Helfer-Zuweisung wird gesetzt. Bei Aufruf aus einer
// fremden Klasse bindet der Server den Helfer an diese Klasse (s. server/ws.py).
function renderQueue() {
  const list = currentQueue();
  if (!list.length) {
    // Keine Klasse offen: der Hinweis steht bereits an Stelle der Klassen-Reiter
    // (renderQueueTabs); darunter in der eigentlichen Warteschlange bleibt die
    // Liste leer, statt den Text zu wiederholen. Ist die eigene Klasse leer,
    // aber eine andere (rechts daneben) hat noch Wartende, wird die als
    // Vorschlag genannt — der Reiter selbst ist zusätzlich markiert (s.
    // renderQueueTabs, .qtab-suggested).
    if (!contextsData.length) { bookRowsEl.innerHTML = ''; return; }
    const suggested = selectedCtxId === ownContextId ? suggestedQueueContext() : null;
    bookRowsEl.innerHTML = suggested
      ? `<div class="book-empty">Warteschlange leer — weiter mit „${escapeHtml((suggested.form || 'Klasse').replace(/^Klasse\s+/i, ''))}"?</div>`
      : '<div class="book-empty">Warteschlange leer</div>';
    return;
  }
  bookRowsEl.innerHTML = list.map(s => {
    const form = (s.form || '').replace(/^Klasse\s+/i, '');
    const name = `${s.lastname}, ${s.firstname}`;
    return `<div class="book-row queue-row" data-student-id="${escapeHtml(String(s.student_id))}">`
      + `<div class="b-fach">${escapeHtml(form)}</div>`
      + `<div class="b-title">${escapeHtml(name)}</div>`
      + `<div class="b-call"><button class="call-btn">Aufrufen</button></div></div>`;
  }).join('');
}

// Weiter-Button: dieselbe Schaltfläche in beiden Modi, sie wandert nur —
// außerhalb des Menüs in der Statuszeile, im Menü an die Stelle, an der sonst
// die Lupe sitzt (rechts neben #status, s. .app.menu-open .top-section). Er
// bleibt dabei immer sichtbar, nur die Position wechselt. `first` muss VOR dem
// Umschalten der 'menu-open'-Klasse gemessen werden (wie firsts/flipTargets-
// ToPosition) — sonst hat sich das umgebende Grid (.top-section) schon auf die
// neue Spaltenaufteilung umgestellt und „first" ist in Wahrheit schon die neue
// Position, wodurch der Button ohne sichtbare Bewegung an seinen Zielort
// springt, statt mit der Statuszeile mitzuwandern. Reparenting + Start-
// Transform laufen darum synchron (wie bei flipTargetsToPosition), nicht erst
// im nächsten Frame.
function updateNextBtnPlacement(first) {
  const targetParent = appEl.classList.contains('menu-open') ? topSectionEl : statusbarEl;
  if (nextBtn.parentElement === targetParent) return;  // schon am Ziel
  targetParent.appendChild(nextBtn);
  const last = nextBtn.getBoundingClientRect();
  const dx = first.left - last.left;
  // dy nur setzen, wenn der neue Elternknoten NICHT ohnehin schon per FLIP
  // (flipTargetsToPosition) vertikal animiert wird — beim Zurückwandern in
  // die Statuszeile ist next-btn selbst Kind von .status-bar, das dort seine
  // EIGENE Y-FLIP-Bewegung bekommt. Ein zusätzlicher eigener dy-Transform
  // würde sich draufaddieren (doppelte, viel zu schnelle Bewegung). In
  // .top-section (Menü-Modus) bleibt next-btn dagegen der einzige bewegte
  // Teil dort, braucht also seine volle eigene Y-Bewegung.
  const dy = (targetParent === statusbarEl) ? 0 : (first.top - last.top);
  if (!dx && !dy) return;
  nextBtn.style.transition = 'none';
  nextBtn.style.transform = `translate(${dx}px, ${dy}px)`;
  nextBtn.offsetWidth;
  nextBtn.style.transition = reduceMotion ? 'transform .01ms' : `transform ${MENU_MS}ms ${MENU_EASE}`;
  nextBtn.style.transform = '';
  nextBtn.addEventListener('transitionend', () => {
    nextBtn.style.transition = ''; nextBtn.style.transform = '';
  }, { once: true });
}

// Klassen-Reiter aufbauen: ein Tab je offener Host-Klasse (contextsData), mit
// der Anzahl wartender Schüler als Badge. Aktiver Tab = selectedCtxId. Leer =
// Hinweis, dass keine Klasse offen ist.
function renderQueueTabs() {
  if (!contextsData.length) {
    queueTabsEl.innerHTML = '<span class="book-empty" style="padding:4px 0">Keine Klasse offen</span>';
    return;
  }
  const suggested = suggestedQueueContext();
  queueTabsEl.innerHTML = contextsData.map(c => {
    const n = (c.queue && c.queue.length) || 0;
    const badge = n ? ` <span class="qcount">${n}</span>` : '';
    const isSuggested = !!(suggested && c.id === suggested.id);
    return `<button class="qtab${c.id === selectedCtxId ? ' active' : ''}${isSuggested ? ' qtab-suggested' : ''}" data-ctx="${escapeHtml(c.id)}">${escapeHtml(c.form || 'Klasse')}${badge}</button>`;
  }).join('');
}

// Tab-Klick → Auswahl setzen, Queue + Status-Count aktualisieren. (Listener
// wird nach der queueTabsEl-Deklaration weiter unten angemeldet.)

// Aufrufen-Button in der Queue-Anzeige: gezielten Schüler anfordern.
bookRowsEl.addEventListener('click', (e) => {
  const btn = e.target.closest('.call-btn');
  if (!btn) return;
  const row = btn.closest('.queue-row');
  const sid = row ? row.dataset.studentId : null;
  if (sid == null) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  loadingStudent = true;
  // peeking bleibt bewusst true: scheitert der Aufruf (Schüler inzwischen
  // genommen), kehrt der Client automatisch in die Peek-Ansicht zurück. Im
  // Erfolgsfall setzt der `loading`-Handler peeking auf false.
  setStatusText('Schüler wird aufgerufen …');
  bookRowsEl.innerHTML = '<div class="book-empty">Schüler wird geladen …</div>';
  ws.send(JSON.stringify({ type: 'call', student_id: Number(sid) }));
});

function handleServerMessage(msg) {
  if (msg.type === 'student_info') {
    studentActive = true;
    loadingStudent = false;  // Schüler geladen — Bücherliste ersetzt die Queue
    peeking = false;          // (neuer) Schüler geladen → keine Queue-Ansicht
    idleMenuOpen = false;     // Schüler geladen → Idle-Menü hinfällig (gelöscht
                             //  bereits beim loading, hier nur defensiv)
    setMenuTitle();
    syncQueueView();
    const s = msg.student;
    currentStudent = s;          // Flags für den Freigabe-Dialog (s. unten)
    lendingApproved = false;     // neuer Schüler → Freigabe zurücksetzen
    heldScanValue = null;
    closeLendModal();
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
    closeBookAlertModal();
    // Bücher sofort sichtbar; Scans+„Scanner bereit"-Status aber erst, sobald
    // der Worker bereit ist (`worker_ready`). Bis dahin „Warten…" + Scans ignor.
    workerPending = true;
    setReadyStatus();
  } else if (msg.type === 'worker_ready') {
    workerPending = false;
    setReadyStatus();
  } else if (msg.type === 'loading') {
    // Server beginnt, einen neuen Schüler für diesen Helfer zu laden („Weiter"/
    // „Nächster"/„Aufrufen"). Queue verbergen, „wird geladen …" zeigen — NICHT
    // die Warteschlange aufblitzen lassen, selbst wenn kurz vorher ein
    // Idle-`waiting` stand (Host-„Nächster" ohne vorherigen Schüler).
    studentActive = false;
    workerPending = true;
    loadingStudent = true;
    // Ein Schüler wird aufgerufen (Aufrufen/Weiter/Nächster/Lupen-Suche) →
    // ein offenes Menü (Peek MIT Hintergrund-Schüler wie Idle OHNE) schließt
    // dabei immer, damit sofort scannbar ist, statt dass der Helfer manuell
    // zurückwechseln muss.
    const menuWasOpen = peeking || idleMenuOpen || searchOpen;
    peeking = false;          // Schülerwechsel beendet den Peek
    idleMenuOpen = false;     // ... und ein ggf. offenes Idle-Menü
    resetSearchPanel();       // ... und ein ggf. offenes Such-Panel (ohne Animation)
    setMenuTitle();
    syncQueueView();
    sNameEl.textContent = '';
    sFormEl.textContent = '';
    sPayEl.innerHTML = '';
    currentBooks = [];
    resetScannedState();
    bookRowsEl.innerHTML = '<div class="book-empty">Schüler wird geladen …</div>';
    closeBookAlertModal();
    currentStudent = null;
    lendingApproved = false;
    heldScanValue = null;
    closeLendModal();
    setStatusText('Warten…');
    if (menuWasOpen) animateMenu(false, true);
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
    // Jeder nicht-verbuchbare Scan (alles außer staged/booked) → Statuszeile
    // deutlich + Hinweis-Modal am Gerät. Der Helfer schließt es selbst
    // (Button/Klick-außerhalb/Escape/nächster Scan). Bei ausgemustert /
    // verliehen-an-andere räumt dismissBookAlert zusätzlich die Host-Meldung
    // auf (server: clear_book_alert); bei den reinen Hinweisen (nicht
    // bestellt, unbekannt, noch nicht geladen, Prüf-Fehler, an sich selbst
    // verliehen) war der Host nie informiert → Clear ist dort ein No-op.
    const isAlert = !OK_STATUSES.has(msg.status);
    setStatusText(`${escapeHtml(msg.barcode)} — ${escapeHtml(msg.msg || msg.status)}`, isAlert);
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
    setStatusText(detail);
    // „Drucken & nächster Schüler": nur bei erfolgreichem Druck weiterschalten.
    if (printThenNext) {
      printThenNext = false;
      if (msg.ok) advanceToNext();
    }
  } else if (msg.type === 'waiting') {
    studentActive = false;
    workerPending = false;
    loadingStudent = false;  // kein Schüler (mehr) geladen — Queue anzeigen
    peeking = false;          // kein Schüler → Peek hinfällig
    setMenuTitle();
    syncQueueView();
    sNameEl.textContent = '';
    sFormEl.textContent = '';
    sPayEl.innerHTML = '';
    bookRowsEl.innerHTML = '';
    currentBooks = [];
    resetScannedState();
    pendingScans = 0;
    drainScanWaiters();
    closeBookAlertModal();
    currentStudent = null;
    lendingApproved = false;
    heldScanValue = null;
    closeLendModal();
    if (typeof msg.queue_size === 'number') queueSize = msg.queue_size;
    if (Array.isArray(msg.queue)) queueList = msg.queue;
    if (msg.msg) waitingMsg = msg.msg;
    renderWaitingStatus();
    renderQueueTabs();
    renderQueue();
  } else if (msg.type === 'queue_update') {
    if (typeof msg.queue_size === 'number') queueSize = msg.queue_size;
    if (Array.isArray(msg.queue)) queueList = msg.queue;
    // Peek (Menü): Queue anzeigen, obwohl ein Schüler zugewiesen ist — dieser
    // bleibt im Hintergrund verbunden. Sonst nur anzeigen, wenn weder ein
    // Schüler geladen ist noch gerade einer geladen wird (next/call gesendet,
    // student_info steht aus). Die Klassen-Reiter selbst kommen via
    // `contexts_update`; hier nur die (Fallback-)Queue und der Count.
    if (peeking) { renderPeekStatus(); if (!contextsData.length) renderQueue(); }
    else if (!studentActive && !loadingStudent) { renderWaitingStatus(); if (!contextsData.length) renderQueue(); }
  } else if (msg.type === 'contexts_update') {
    // Alle im Host offenen Klassen + je ihre wartenden Schüler. Quelle für die
    // Klassen-Reiter im Helfer-Menü. own_context_id = Klasse, an die dieser
    // Helfer gebunden (Vorauswahl beim Öffnen). Live auf allen Zustandsänderungen
    // (open/close-class, Aufrufe, Abschlüsse) via broadcast_queue_size.
    contextsData = Array.isArray(msg.contexts) ? msg.contexts : [];
    ownContextId = msg.own_context_id || null;
    ensureSelectedCtx();
    renderQueueTabs();
    if (peeking) { renderPeekStatus(); renderQueue(); }
    else if (!studentActive && !loadingStudent) { renderWaitingStatus(); renderQueue(); }
  } else if (msg.type === 'search_classes') {
    // Lupen-Suche: alle Klassen des Schuljahrs (IServ). Session-Cache füllen
    // und Dropdown aufbauen — letzte Klasse vorwählen (localStorage), sonst
    // erste. Sofort Schüler der gewählten Klasse nachladen.
    searchClassCache = Array.isArray(msg.classes) ? msg.classes : [];
    renderSearchClasses();
  } else if (msg.type === 'search_students') {
    // Lupen-Suche: alle Schüler einer Klasse (IServ). Session-Cache füllen
    // und Dropdown aufbauen, falls die Klasse aktuell gewählt ist.
    const list = Array.isArray(msg.students) ? msg.students : [];
    searchStudentsCache.set(msg.form, list);
    if (searchOpen && searchClassSel.value === msg.form) renderSearchStudents(msg.form, list);
  } else if (msg.type === 'error') {
    loadingStudent = false;  // Laden gescheitert → Queue wieder freigeben
    // Lupe-Suche gescheitert (z. B. ungültige ID/IServ-Fehler): kein `loading`
    // gekommen → Menü/Panel offen lassen zum erneuten Versuch, Flag aber
    // zurücksetzen, damit ein späteres fremdes `loading` sie nicht doch schließt.
    searchSubmitted = false;
    setStatusText('Fehler: ' + (msg.msg || ''));
    dotEl.className = 'dot err';
    heldScanValue = null;
    closeLendModal();
    // Peek geöffnet (z. B. Aufruf aus der Queue gescheitert) → zurück in die
    // Peek-Ansicht; sonst nur bei freiem Helfer die Queue zeigen.
    if (peeking) renderQueue();
    else if (!studentActive) renderQueue();
  }
}

function connect() {
  if (!token) {
    setStatusText('Kein Token in der URL — vom Host QR-Code scannen');
    dotEl.className = 'dot err';
    return;
  }
  connectWebSocket(() => `wss://${location.host}/ws/scanner/${token}`, {
    onSocket: (s) => { ws = s; },
    onOpen: () => { dotEl.className = 'dot ok'; setReadyStatus(); },
    onClose: (e, reconnect) => {
      dotEl.className = 'dot err'; setStatusText('Getrennt — neu verbinden…');
      reconnect();
    },
    onError: () => { dotEl.className = 'dot err'; setStatusText('Verbindungsfehler'); },
    onMessage: e => { try { handleServerMessage(JSON.parse(e.data)); } catch (_) {} },
  });
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
const lendConfirmModal = document.getElementById('lend-confirm-modal');
const lendWarnEl = document.getElementById('lend-warn');
const modalLendYesBtn = document.getElementById('modal-lend-yes');
const modalLendNoBtn = document.getElementById('modal-lend-no');
let scanFlashTimeout = null;

// Nächster Schüler: aktuellen abschließen + nächsten aus der Queue laden.
// Alten Schüler sofort entfernen und "Warten…" zeigen, auch während
// der neue Schüler serverseitig noch geladen wird (Bücher folgen mit
// student_info, Scan-Freigabe mit worker_ready).
function advanceToNext() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  sNameEl.textContent = '';
  sFormEl.textContent = '';
  sPayEl.innerHTML = '';
  bookRowsEl.innerHTML = '';
  currentBooks = [];
  resetScannedState();
  workerPending = true;
  loadingStudent = true;  // nächste Schülerzugewiesen, wird gerade geladen → Queue verbergen
  setStatusText('Warten…');
  // Eigene Klasse leer, aber ein Reiter weiter hinten hat noch Wartende
  // (s. suggestedQueueContext): direkt dorthin springen, statt aus der
  // leeren eigenen Queue zu ziehen. Der Server bindet den Helfer dabei an
  // die neue Klasse um (s. advance_helper in sessions.py).
  const suggested = suggestedQueueContext();
  if (suggested) selectedCtxId = suggested.id;
  ws.send(JSON.stringify({ type: 'next', context_id: suggested ? suggested.id : undefined }));
}

function closeNextModal() { nextModal.classList.remove('show'); }

// Nächster-Schüler-Klick: bei noch offenen vorgemerkten Büchern erst einen
// Hinweis zeigen (Abbrechen / Nächster Schüler), sonst direkt weiterschalten.
async function requestNext() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  if (!studentActive) { advanceToNext(); return; }
  setStatusText('Prüfe Scans …');
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
// Status, die den Scan nicht verbuchen (kein staged/booked) → Hinweis-Modal
// am Gerät. Der Helfer schließt JEDES dieser Modal selbst (Button / Klick
// außerhalb / Escape / nächster Scan); clear_book_alert räumt ggfls. die
// Host-Meldung auf (No-op für Status ohne Host-Broadcast).
const OK_STATUSES = new Set(['staged', 'booked']);
// status → {title, color} für das Hinweis-Modal.
const ALERT_META = {
  book_deleted:        { title: 'Ausgemustertes Buch gescannt',  color: '#f44336' },
  not_in_stock:        { title: 'Buch noch verliehen',           color: '#f44336' },
  series_already_lent: { title: 'Buch bereits an dich verliehen', color: '#e69500' },
  not_enrolled:        { title: 'Buch nicht bestellt',           color: '#e69500' },
  unknown_book:        { title: 'Buch unbekannt',                color: '#f44336' },
  not_ready:           { title: 'Buchliste noch nicht geladen',   color: '#e69500' },
  error:               { title: 'Fehler bei der Prüfung',         color: '#f44336' },
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
  if (!studentActive) { setStatusText('Kein Schüler zugewiesen'); return; }
  setStatusText('Prüfe Scans …');
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
  setStatusText('Leihschein wird gedruckt …');
  ws.send(JSON.stringify({ type: 'print', second_page: slipCheck.checked }));
  closePrintModal();
}

printBtn.addEventListener('click', openPrintDialog);
modalPrintBtn.addEventListener('click', () => sendPrint(false));
modalPrintNextBtn.addEventListener('click', () => sendPrint(true));
modalCancelBtn.addEventListener('click', closePrintModal);
printModal.addEventListener('click', (e) => { if (e.target === printModal) closePrintModal(); });

// ---- Ausleih-Freigabe-Dialog (Unstimmigkeit: Nachweis fehlt / Rechnung offen) ----
// Hat der geladene Schüler eine Unstimmigkeit (und wurde noch nicht freigegeben),
// wird der Scan zurückgehalten und ein Dialog gezeigt. Erst nach „Ja" geht der
// Scan raus — die server-seitige Lager-/Anmeldeprüfung + Worker-Eintragung läuft
// danach wie gehabt. „Nein"/Escape/Click-außerhalb verwirft den Scan; da
// `lendingApproved` dann weiterhin false steht, fragt der nächste Scan erneut.
// „Ja" setzt `lendingApproved` → weitere Bücher werden nicht mehr angefragt,
// bis der Schüler neu geladen wird (Reset in student_info/loading/waiting).
function studentHasUnstimmigkeit() {
  const s = currentStudent;
  if (!s || !s.enrolled) return false;   // „nicht angemeldet" bleibt außen vor
  return !!(s.remission_pending || s.exemption_pending || !s.paid);
}

// Unstimmigkeit-Liste im Dialog rendern (gleiche Texte wie der s-pay-Block).
function renderLendWarning(el) {
  const s = currentStudent || {};
  const items = [];
  if (s.remission_pending)  items.push('Ermäßigungsnachweis fehlt');
  if (s.exemption_pending)  items.push('Befreiungsnachweis fehlt');
  if (!s.paid)              items.push(`Rechnung offen: ${escapeHtml(s.amount_open)} €`);
  el.innerHTML = `Für diese Person liegt eine Unstimmigkeit vor:<ul>`
    + items.map(t => `<li>${escapeHtml(t)}</li>`).join('')
    + `</ul>Trotzdem ausleihen?`;
  el.style.display = '';
}

function closeLendModal() { lendConfirmModal.classList.remove('show'); }

// Scan tatsächlich senden (aus onScanSuccess ausgelagert, damit der Freigabe-
// Pfad denselben Sendeweg nutzt). `pendingScans++` nur hier → zurückgehaltene
// Scans erzeugen keinen Drift in der Sequenzierung.
function sendScan(value) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  pendingScans++;
  ws.send(JSON.stringify({ type: 'scan', value }));
}

// „Ja, ausleihen": Freigabe merken, Modal schließen, gehaltenen Scan senden.
// Cooldown/lastValue erneut scharfstellen, damit die noch laufende Kamera
// denselben Barcode nicht sofort wieder feuert (Duplicate-Scan-Schutz).
modalLendYesBtn.addEventListener('click', () => {
  lendingApproved = true;
  closeLendModal();
  const v = heldScanValue;
  heldScanValue = null;
  if (v == null) return;
  lastValue = v; cooldown = true;
  setTimeout(() => { cooldown = false; lastValue = ''; }, 2000);
  setStatusText('Gesendet: ' + v);
  sendScan(v);
});

// „Nicht ausleihen": Scan verwerfen, nichts senden. Flag bleibt false → beim
// erneuten Einscannen wird erneut gefragt.
modalLendNoBtn.addEventListener('click', () => {
  closeLendModal();
  heldScanValue = null;
  setStatusText('Nicht ausgeliehen — Buch nicht eingegeben');
});
lendConfirmModal.addEventListener('click', (e) => {
  if (e.target === lendConfirmModal) {  // Click außerhalb der Box = verwerfen
    closeLendModal();
    heldScanValue = null;
    setStatusText('Nicht ausgeliehen — Buch nicht eingegeben');
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (printModal.classList.contains('show')) closePrintModal();
  if (nextModal.classList.contains('show')) closeNextModal();
  if (bookAlertModal.classList.contains('show')) dismissBookAlert();
  if (lendConfirmModal.classList.contains('show')) {
    closeLendModal();
    heldScanValue = null;
    setStatusText('Nicht ausgeliehen — Buch nicht eingegeben');
  }
});

// initAudio/playBeep: siehe common.js (Beeper).

soundBtn.addEventListener('click', async () => {
  soundEnabled = !soundEnabled;
  if (soundEnabled) {
    soundBtn.innerHTML = ICON_VOLUME_ON;
    soundBtn.classList.add('sound-on');
    await Beeper.initAudio();
    Beeper.playBeep();
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

// ---- Menü-Toggle: Schüler- ↔ Warteschlangen-Ansicht ----
// Der Schüler bleibt dabei verbunden (im Hintergrund); nur die Ansicht wird
// umgeschaltet. Erst das Aufrufen eines anderen Schülers („Aufrufen"-Button /
// „Nächster") trennt den alten — das ist das bestehende `call`/`next`-Verhalten.
// Statuszeile zeigt im Peek den Hintergrund-Schüler. Scans werden im Peek
// ignoriert (s. onScanSuccess), damit kein Buch für den abgelenkten Helfer
// gebucht/staged wird.
function setMenuTitle() {
  // Geöffnet: Peek → zurück zum Hintergrund-Schüler; Idle-Menü → Kamera wieder
  // einblenden. Geschlossen: Menü öffnet die Warteschlangen-Fokus-Ansicht.
  if (peeking) menuBtn.title = 'Schüler anzeigen';
  else if (idleMenuOpen) menuBtn.title = 'Scanner anzeigen';
  else menuBtn.title = 'Warteschlange anzeigen';
}
setMenuTitle();

// ---- Menü-Modus: Steuerleiste kollabieren, Statuszeile + Inhalt darunter
// fahren animiert nach oben (bzw. beim Schließen wieder zurück). Die Bewegung
// der Statuszeile/Name/Bücher läuft als FLIP: Position vor dem Klassen-Toggle
// messen, Klasse umschalten, dann die Differenz als inverse transform anwenden
// und nach 0 transitionen. So animiert auch der Wechsel des CSS-Grid
// (.top-section von zwei Zeilen auf eine), der selbst nicht transitionierbar ist.
//
// Die Steuer-Elemente, die im Menü-Modus per display:none verschwinden
// (gear-btn, reader, right-col, print), können nicht gefadet werden, solange
// sie display:none sind. Sie werden darum für die Dauer des Übergangs per
// position:absolute an ihrer alten Stelle gehalten (aus dem Fluss → Grid
// kollabiert weiter) und mit opacity synchron zur FLIP-Bewegung (.35s) ausge-
// blendet. Beim Schließen kehren sie in den Fluss zurück und faden entsprechend
// ein. Alles auf derselben Kurve (.35s cubic-bezier(.22,.61,.36,1)) → Status-
// zeilen-Bewegung und Ein-/Ausblenden der Elemente bewegen sich synchron.
//
// Next-btn und die Lupe (#search-btn) laufen NICHT über diesen Fade-Mechanismus:
// next-btn bleibt in beiden Modi dieselbe, immer sichtbare Schaltfläche — sie
// wandert nur per updateNextBtnPlacement() (eigene FLIP) zwischen Statuszeile
// und der Menü-Kopfzeile hin und her. Die Lupe sitzt fest in der Warteschlangen-
// Kopfzeile und blendet dort rein per CSS-Opacity (.app.menu-open) ein/aus —
// kein Reparenting nötig.
//
// Sonderfall print: es liegt in .status-bar, die der FLIP mit transform
// versieht. Ein transform-Vorfahr wird zum containing block für absolute
// Nachfahren — print würde auf der Statuszeilen-Bewegung reiten und dabei
// deren diskreten x-Sprung mitmachen (Statuszeile: full-width x=0 → Mittel-Spalte
// x=52). Es wird darum für den Übergang ins .top-section (nicht transformiert,
// positioniert) umgehängt und dort an alter Stelle festgepinnt. ----
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

function clearMenuInline(el) {
  MENU_INLINE_PROPS.forEach(p => { el.style[p] = ''; });
}

// Layout-relevanten Computed-Style snapshoten, damit ein per position:absolute
// aus dem Fluss genommenes Element seine innere Anordnung behält (z. B. right-col
// bleibt flex-direction:column, statt auf Row zurückzufallen).
const LAYOUT_PROPS = ['display','flexDirection','flexWrap','alignItems','justifyContent','gap'];
function snapshotLayout(el) {
  const cs = getComputedStyle(el);
  return Object.fromEntries(LAYOUT_PROPS.map(p => [p, cs[p]]));
}
function applyLayout(el, snap) {
  for (const [k, v] of Object.entries(snap)) el.style[k] = v;
}

// Element an seiner ersten Position (rect, viewport-Koordinaten) per
// position:absolute festpinnen. refRect ist der Positions-Anker (gemessen, bevor
// das Grid umstrukturiert wird) — das Element sitzt exakt dort, wo es im Fluss
// war. applyLayout stellt display + Flex-Verhalten wieder her und überschreibt
// damit das CSS display:none, damit das Element während des Fades rendert.
function pinAbsoluteAt(el, rect, refRect, snap) {
  el.style.position = 'absolute';
  el.style.left = (rect.left - refRect.left) + 'px';
  el.style.top  = (rect.top  - refRect.top ) + 'px';
  el.style.width  = rect.width + 'px';
  el.style.height = rect.height + 'px';
  el.style.margin = '0';
  el.style.boxSizing = 'border-box';
  applyLayout(el, snap);
}

// Positions-Anker für ein auszublendendes Element: print wird nach
// .top-section umgehängt → dort hin pinnen. Alle anderen an ihren aktuellen
// offsetParent (gear-wrap bzw. .top-section), der nicht transformiert ist.
function menuRefFor(el) {
  return (el === printBtn) ? topSectionEl : (el.offsetParent || topSectionEl);
}

// print in seine Heimat (.status-bar) zurückbringen, falls ein früherer
// Übergang es umgehängt hatte. next-btn wird bewusst NICHT hier zurückgeholt —
// es hat eine eigene Platzierung (updateNextBtnPlacement), die auch außerhalb
// der Statuszeile (Warteschlangen-Kopfzeile) gültig sein darf.
function restorePrint() {
  if (printBtn.parentElement !== statusbarEl) statusbarEl.insertBefore(printBtn, statusInnerEl);
}

function flipTargetsToPosition(firsts) {
  const targets = [statusbarEl, queueSlotEl, bookWrapEl];
  targets.forEach((t, i) => {
    const last = t.getBoundingClientRect();
    const dy = firsts[i].top - last.top;
    if (!dy) return;
    t.style.transition = 'none';
    t.style.transform = `translateY(${dy}px)`;
    // reflow erzwingen, damit die Start-Transform greift, bevor wir nach 0 gehen.
    t.offsetWidth;
    t.style.transition = reduceMotion ? 'transform .01ms' : `transform ${MENU_MS}ms ${MENU_EASE}`;
    t.style.transform = '';
    t.addEventListener('transitionend', () => {
      t.style.transition = ''; t.style.transform = '';
    }, { once: true });
  });
}

function animateMenu(open, keepQueueView = false) {
  // Jegliche noch laufende Menü-Animation abbrechen — schnelles Toggeln darf
  // keine halb gesetzten Inline-Styles hinterlassen. print in Heimat bringen.
  menuAnimGen++;
  menuHideEls.forEach(clearMenuInline);
  [statusbarEl, queueSlotEl, bookWrapEl].forEach(clearMenuInline);
  restorePrint();

  const firsts = [statusbarEl, queueSlotEl, bookWrapEl].map(t => t.getBoundingClientRect());
  const nextBtnFirst = nextBtn.getBoundingClientRect();

  if (open) {
    // Steuer-Elemente: Position + Anker + inneres Layout schon im Fluss messen.
    const hide = menuHideEls.map(el => {
      const ref = menuRefFor(el);
      return { el, rect: el.getBoundingClientRect(), refRect: ref.getBoundingClientRect(),
               snap: snapshotLayout(el), reparent: (el === printBtn) };
    });
    appEl.classList.add('menu-open');
    updateNextBtnPlacement(nextBtnFirst);   // wandert in die Statuszeilen-Nachbarschaft im Menü
    if (!keepQueueView) setQueueView(true);
    flipTargetsToPosition(firsts);
    // Steuer-Elemente an alter Stelle festpinnen und synchron ausfaden. Das
    // inline-display überschreibt das CSS display:none, damit sie rendern;
    // position:absolute nimmt sie aus dem Fluss, sodass das Grid kollabiert.
    hide.forEach(({ el, rect, refRect, snap, reparent }) => {
      if (reparent) topSectionEl.appendChild(el);   // aus .status-bar heraus
      pinAbsoluteAt(el, rect, refRect, snap);
      el.style.opacity = '1';
      el.style.transition = 'none';
      el.offsetWidth;
      el.style.transition = reduceMotion ? 'opacity .01ms' : `opacity ${MENU_MS}ms ${MENU_EASE}`;
      el.style.opacity = '0';
    });
    // Lupe: faded per CSS (opacity 0→1, .35s) ein, sobald menu-open sie in
    // der Warteschlangen-Kopfzeile auf opacity:1 setzt — kein JS nötig.
    const gen = menuAnimGen;
    setTimeout(() => { if (gen === menuAnimGen) menuHideEls.forEach(clearMenuInline); }, MENU_MS + 40);
  } else {
    // Schließen: Steuer-Elemente vorab unsichtbar machen (sind noch display:none
    // via CSS), damit sie beim Entfernen von menu-open nicht aufblitzen.
    menuHideEls.forEach(el => { el.style.opacity = '0'; el.style.transition = 'none'; });
    appEl.classList.remove('menu-open');
    updateNextBtnPlacement(nextBtnFirst);   // wandert zurück in die Statuszeile
    if (!keepQueueView) setQueueView(false);
    flipTargetsToPosition(firsts);
    // Steuer-Elemente sind nun zurück im Fluss (display:flex via CSS, menu-open
    // weg), aber per Inline opacity:0 → von 0→1 synchron einfaden.
    menuHideEls.forEach(el => {
      el.offsetWidth;
      el.style.transition = reduceMotion ? 'opacity .01ms' : `opacity ${MENU_MS}ms ${MENU_EASE}`;
      el.style.opacity = '1';
    });
    // Lupe faded per CSS (opacity 1→0) aus, sobald menu-open weg ist — kein JS nötig.
    const gen = menuAnimGen;
    setTimeout(() => { if (gen === menuAnimGen) menuHideEls.forEach(clearMenuInline); }, MENU_MS + 40);
  }
}

function openPeek() {
  peeking = true;
  resetSearchPanel();   // frisch starten — kein offenes Panel aus altem Peek
  setMenuTitle();
  // Bei jedem Öffnen die eigene Klasse (re-)selektieren — der man zugeordnet
  // ist. ensureSelectedCtx() zieht ownContextId (falls offen), sonst erste
  // offene Klasse. Das folgende peek_queue liefert ein frisches contexts_update;
  // dessen ensureSelectedCtx() behält die nun gesetzte eigene Auswahl bei.
  selectedCtxId = null;
  ensureSelectedCtx();
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'peek_queue' }));
  // Queue + Tabs sofort aus letzter bekannter Liste rendern; Antwort/Updates
  // (peek_queue liefert contexts_update) ziehen sie nach.
  renderPeekStatus();
  renderQueueTabs();
  renderQueue();
  // Zuletzt animieren: Erst hier messen, damit #status- und Queue-Inhalt bereits
  // final sind und die Ziel-Position stimmt. animateMenu toggelt .queue-view
  // synchron zum FLIP (Name-row → Queue-Header im Slot).
  animateMenu(true);
}

function closePeek() {
  peeking = false;
  resetSearchPanel();   // ggf. offenes Such-Panel einklappen, bevor das Menü schließt
  setMenuTitle();
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'peek_close' }));
  // Bücherliste des Hintergrund-Schülers wiederherstellen.
  renderBooks(currentBooks);
  setReadyStatus();
  animateMenu(false);
}

// ---- Idle-Menü: Menü-Button auch ohne zugewiesenen Schüler nutzen ----
// Im Idle ist die Warteschlange ohnehin sichtbar (syncQueueView hält queue-view
// an). Das Menü klappt hier lediglich die Kamera-Zeile ein (Fokus auf die
// Queue) und fährt sie wieder aus — kein Hintergrund-Schüler, kein Server-
// Roundtrip (peek_queue/peek_close entfallen). queue-view bleibt dabei an
// (keepQueueView beim animateMenu), die Queue steht durchgehend. Die Lupe ist
// hier ebenfalls nutzbar: gezielt einen beliebigen IServ-Schüler aufrufen
// (search_call funktioniert serverseitig auch ohne aktuellen Schüler).
function openIdleMenu() {
  idleMenuOpen = true;
  resetSearchPanel();
  setMenuTitle();
  // Queue + Tabs aus letztem Stand rendern; Live-Updates (contexts_update)
  // kommen wie immer ungefragt für Idle-Helfer mit.
  renderWaitingStatus();
  renderQueueTabs();
  renderQueue();
  // queue-view bleibt an → Kamera-Zeile kollabiert nur via menu-open.
  animateMenu(true, true);
}

function closeIdleMenu() {
  idleMenuOpen = false;
  resetSearchPanel();
  setMenuTitle();
  animateMenu(false, true);
  // Status zurück auf Idle-Warteschlangengröße (Kamera wieder eingeblendet).
  renderWaitingStatus();
}

menuBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  if (peeking) { closePeek(); return; }
  if (idleMenuOpen) { closeIdleMenu(); return; }
  // Im Lade-Zustand nicht umschalten (Schüler steht kurz bevor). Sonst:
  // Schüler aktiv → Peek (Hintergrund); ohne Schüler → Idle-Menü.
  if (loadingStudent) return;
  if (studentActive) openPeek();
  else openIdleMenu();
});

// ---- Lupen-Suche (Peek-Modus): Schnellsprung zu beliebigem Schüler ----
// Lupe klicken → Warteliste fährt nach unten (FLIP), zwei Dropdowns blenden
// synchron ein: oben Klasse (alle des Schuljahrs), unten Schüler der gewählten
// Klasse. Schüler wählen → wird geladen (search_call, ersetzt den Hintergrund-
// Schüler). Letzte Klasse wird beim erneuten Öffnen vorausgewählt (localStorage),
// bleibt aber änderbar. Alles read-only (IServ-GETs über den Server).

// FLIP für die Panel-Bewegung: gleiche Technik wie animateMenu, nur dass die
// Statuszeile stehen bleibt und nur name-row + book-table-wrap nach unten
// fahren (das Panel schiebt sich dazwischen).
function flipSearchTargets(firsts) {
  const targets = [queueSlotEl, bookWrapEl];
  targets.forEach((t, i) => {
    const last = t.getBoundingClientRect();
    const dy = firsts[i].top - last.top;
    if (!dy) return;
    t.style.transition = 'none';
    t.style.transform = `translateY(${dy}px)`;
    t.offsetWidth;
    t.style.transition = reduceMotion ? 'transform .01ms' : `transform ${MENU_MS}ms ${MENU_EASE}`;
    t.style.transform = '';
    t.addEventListener('transitionend', () => {
      t.style.transition = ''; t.style.transform = '';
    }, { once: true });
  });
}

function animateSearchPanel(open) {
  const firsts = [queueSlotEl, bookWrapEl].map(t => t.getBoundingClientRect());
  if (open) {
    // Natürliche Höhe messen (scrollHeight liefert sie auch bei max-height:0),
    // dann von 0 auf diese Höhe transitionieren — die Warteliste darunter per
    // FLIP synchron nach unten fahren.
    const h = searchPanel.scrollHeight;
    searchPanel.classList.add('open');
    searchPanel.style.transition =
      `max-height ${MENU_MS}ms ${MENU_EASE}, opacity ${MENU_MS}ms ${MENU_EASE}`;
    searchPanel.style.maxHeight = h + 'px';
    flipSearchTargets(firsts);
    const onEnd = (e) => {
      if (e.propertyName !== 'max-height') return;   // erst wenn die Höhe fertig
      searchPanel.style.maxHeight = 'none';   // danach frei wachsen
      searchPanel.removeEventListener('transitionend', onEnd);
    };
    searchPanel.addEventListener('transitionend', onEnd);
  } else {
    searchPanel.style.maxHeight = searchPanel.scrollHeight + 'px';
    searchPanel.offsetWidth;   // Reflow: Start-Height greift, bevor zu 0
    searchPanel.style.transition =
      `max-height ${MENU_MS}ms ${MENU_EASE}, opacity ${MENU_MS}ms ${MENU_EASE}`;
    searchPanel.style.maxHeight = '0px';
    searchPanel.classList.remove('open');
    flipSearchTargets(firsts);
  }
}

// Such-Panel sofort (ohne Animation) zurücksetzen — beim Peek-Öffnen/Schließen,
// damit kein eingeklapptes/offenes Panel aus einem früheren Peek stehen bleibt.
function resetSearchPanel() {
  searchOpen = false;
  searchSubmitted = false;
  searchPanel.classList.remove('open');
  searchPanel.style.maxHeight = '';
  searchPanel.style.transition = '';
}

// Klassen-Dropdown aufbauen: letzte gewählte Klasse (localStorage) vorwählen,
// falls vorhanden, sonst erste. Sofort Schüler der gewählten Klasse laden.
function renderSearchClasses() {
  const classes = searchClassCache || [];
  if (!classes.length) {
    searchClassSel.disabled = true;
    searchClassSel.innerHTML = '<option>Keine Klassen gefunden</option>';
    searchStudentSel.disabled = true;
    searchStudentSel.innerHTML = '<option>—</option>';
    return;
  }
  searchClassSel.disabled = false;
  const last = localStorage.getItem(SEARCH_LASTCLASS_KEY);
  const preselect = (last && classes.includes(last)) ? last : classes[0];
  searchClassSel.innerHTML = classes.map(c =>
    `<option value="${escapeHtml(c)}"${c === preselect ? ' selected' : ''}>${escapeHtml(c)}</option>`).join('');
  loadSearchStudents(preselect);
}

// Schüler-Dropdown für eine Klasse aufbauen (aus Cache oder Server-Anfrage).
function renderSearchStudents(form, list) {
  if (!form) {
    searchStudentSel.disabled = true;
    searchStudentSel.innerHTML = '<option>Zuerst Klasse wählen</option>';
    return;
  }
  const students = list != null ? list : (searchStudentsCache.get(form) || null);
  if (students == null) {
    searchStudentSel.disabled = true;
    searchStudentSel.innerHTML = '<option>Schüler laden …</option>';
    return;
  }
  if (!students.length) {
    searchStudentSel.disabled = true;
    searchStudentSel.innerHTML = '<option>Keine Schüler</option>';
    return;
  }
  searchStudentSel.disabled = false;
  searchStudentSel.innerHTML =
    '<option value="" disabled selected>— Schüler wählen —</option>'
    + students.map(s => {
      const name = `${s.lastname}, ${s.firstname}`;
      const data = `data-id="${escapeHtml(String(s.student_id))}" data-form="${escapeHtml(form)}" data-last="${escapeHtml(s.lastname)}" data-first="${escapeHtml(s.firstname)}"`;
      return `<option value="${escapeHtml(String(s.student_id))}" ${data}>${escapeHtml(name)}</option>`;
    }).join('');
}

function loadSearchStudents(form) {
  if (!form) { renderSearchStudents(''); return; }
  const cached = searchStudentsCache.get(form);
  if (cached != null) { renderSearchStudents(form, cached); return; }
  renderSearchStudents(form, null);   // „Schüler laden …"
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'search_students', form }));
  }
}

function openSearch() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  searchOpen = true;
  // Klassen laden, falls noch nicht gecacht; sonst direkt rendern.
  if (searchClassCache == null) {
    searchClassSel.disabled = true;
    searchClassSel.innerHTML = '<option>Klassen laden …</option>';
    searchStudentSel.disabled = true;
    searchStudentSel.innerHTML = '<option>—</option>';
    ws.send(JSON.stringify({ type: 'search_classes' }));
  } else {
    renderSearchClasses();
  }
  animateSearchPanel(true);
}

function closeSearch() {
  searchOpen = false;
  animateSearchPanel(false);
}

// Schüler ausgewählt → laden (search_call). Panel + Menü schließen bewusst
// erst, wenn der Server `loading` schickt (s. handleServerMessage), damit bei
// einem Fehler die Suche offen bleibt zum erneuten Versuch.
function submitSearchStudent() {
  const opt = searchStudentSel.selectedOptions[0];
  const sid = opt && opt.value;
  if (!sid) return;
  const form = opt && opt.dataset.form;
  const lastname = opt && opt.dataset.last;
  const firstname = opt && opt.dataset.first;
  if (!form) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  searchSubmitted = true;
  ws.send(JSON.stringify({
    type: 'search_call', student_id: Number(sid),
    form, lastname: lastname || '', firstname: firstname || '',
  }));
}

searchBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  if (!peeking && !idleMenuOpen) return;   // Lupe nur im Menü-Modus (Peek o. Idle)
  if (searchOpen) closeSearch(); else openSearch();
});

searchClassSel.addEventListener('change', () => {
  const form = searchClassSel.value;
  if (form) localStorage.setItem(SEARCH_LASTCLASS_KEY, form);
  loadSearchStudents(form);
});

searchStudentSel.addEventListener('change', submitSearchStudent);
// Klicks im Panel nicht ins Dokument durchblasen (cam-dropdown-Schließlogik).
searchPanel.addEventListener('click', (e) => e.stopPropagation());

// Klassen-Reiter-Klick: gewählten Tab setzen, Queue + Status-Count aktualisieren.
queueTabsEl.addEventListener('click', (e) => {
  const tab = e.target.closest('.qtab');
  if (!tab) return;
  selectedCtxId = tab.dataset.ctx;
  renderQueueTabs();
  renderQueue();
  if (peeking) renderPeekStatus(); else renderWaitingStatus();
});

function onScanSuccess(value) {
  // Worker noch nicht bereit (Schüler gerade zugewiesen, open_student läuft) —
  // Scan ignorieren, nicht senden (wie beim ausgemusterten-Buch-Block).
  if (workerPending) return;
  // Peek (Menü): Warteschlange sichtbar, Helfer schaut nicht auf die Bücher —
  // Scan ignorieren, damit kein Buch für den Hintergrund-Schüler gebucht/staged
  // wird und die Queue-Ansicht nicht überschrieben wird.
  if (peeking) return;
  // Freigabe-Dialog noch offen → Helfer entscheidet gerade; Scan nicht erneut
  // feuern (kein Doppelt-Beep, kein Überschreiben des gehaltenen Werts).
  if (lendConfirmModal.classList.contains('show')) return;
  if (cooldown || value === lastValue) return;
  // Nächster Scan → evtl. offenes Hinweis-Modal bewusst schließen (auch Host
  // aufräumen); war keins offen, ist dismissBookAlert ein No-op.
  dismissBookAlert();
  if (soundEnabled) Beeper.playBeep();
  lastValue = value; cooldown = true;
  setTimeout(() => { cooldown = false; lastValue = ''; }, 2000);
  // Unstimmigkeit (Nachweis fehlt / Rechnung offen) und noch nicht freigegeben:
  // Scan zurückhalten und Freigabe-Dialog zeigen — erst nach „Ja" geht der Scan
  // raus, dann läuft die server-seitige Lager-/Anmeldeprüfung + Worker wie gehabt.
  if (studentHasUnstimmigkeit() && !lendingApproved) {
    heldScanValue = value;
    renderLendWarning(lendWarnEl);
    lendConfirmModal.classList.add('show');
    setStatusText('Freigabe erforderlich — Buch zurückgehalten');
  } else {
    setStatusText('Gesendet: ' + value);
    sendScan(value);
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
  if (!workerPending) setStatusText('Kamera startet…');

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
    else setStatusText('Neu verbinden…');
    const video = document.querySelector('#reader video');
    if (video && video.srcObject) {
      const track = video.srcObject.getVideoTracks()[0];
      const capabilities = track.getCapabilities?.();
      torchBtn.disabled = !(capabilities && capabilities.torch);
    }
  } catch (err) {
    console.error('Camera error:', err);
    setStatusText('Kamerafehler — neu laden');
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

