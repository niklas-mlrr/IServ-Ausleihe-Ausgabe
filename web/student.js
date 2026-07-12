(function () {
const views = {
  pending: document.getElementById('view-pending'),
  active: document.getElementById('view-active'),
  done: document.getElementById('view-done'),
  error: document.getElementById('view-error'),
};
function show(name) {
  for (const k in views) views[k].classList.toggle('show', k === name);
}
// escapeHtml, isBookDone, initAudio/playBeep (Beeper): siehe common.js.

// Statuszeile IMMER über diesen Helper setzen (nie textContent/classList
// direkt), damit Text und Aussehen (Farbe) untrennbar zusammen wechseln —
// die Statuszeile behält ihr Aussehen, bis eine neue Meldung ihren Text
// ersetzt (z. B. bleibt eine „ausgemustert"-Meldung rot stehen, bis der
// nächste Scan eine neue Statuszeile setzt; das Freigeben durch den Host
// (`book_alert_clear`) ändert den Text NICHT und ruft diesen Helper daher
// bewusst nicht auf). `alertClass` s. `statusAlertClass()` weiter unten.
function setStatusText(text, alertClass = null) {
  const el = document.getElementById('status-text');
  el.textContent = text;
  el.classList.remove('status-alert-red', 'status-alert-orange', 'status-book-issued');
  if (alertClass) el.classList.add(alertClass);
}

const joinSecret = new URLSearchParams(location.search).get('j');
let token = sessionStorage.getItem('mb_token');
let ws = null, finished = false, scannerStarted = false;
let workerPending = false;         // Schüler zugewiesen, aber Worker noch nicht bereit
let currentBooks = [];              // Buchliste des Schülers
const scannedIsbns = new Set();     // in dieser Session gescannte ISBNs
const scanOrder = new Map();        // ISBN -> Scan-Sequenz (für „zuletzt ausgegeben oben")
let scanSeq = 0;
let bookOrder = [];                 // klassenweite ISBN-Reihenfolge (vom Host konfiguriert)

function showError(title, text) {
  if (title) document.getElementById('error-title').textContent = title;
  if (text) document.getElementById('error-text').textContent = text;
  show('error');
}
function clearToken() { token = null; sessionStorage.removeItem('mb_token'); }

async function join() {
  if (!joinSecret) { showError('Kein QR-Code', 'Bitte scanne den QR-Code am Display.'); return false; }
  try {
    const r = await fetch('/api/student/join', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ join_secret: joinSecret }),
    });
    if (!r.ok) {
      showError('Ausgabe geschlossen', 'Die Live-Ausgabe ist derzeit nicht geöffnet.');
      return false;
    }
    const d = await r.json();
    token = d.session_token;
    sessionStorage.setItem('mb_token', token);
    document.getElementById('pair-code').textContent = d.pairing_code;
    return true;
  } catch (_) {
    showError('Verbindungsfehler', 'Bitte erneut versuchen.');
    return false;
  }
}

// Reconnect-Sonderfall (nicht wegabstrahierbar, s. common.js-Kommentar zu
// connectWebSocket): Close-Code 4006 = entwerteter Token → Re-Join statt
// normalem Reconnect; `finished` (regulär abgeschlossen) → gar nicht
// reconnecten. Beides bleibt hier als eigener onClose-Handler.
function connect() {
  const wsHandle = connectWebSocket(() => `wss://${location.host}/ws/student/${token}`, {
    onSocket: (s) => { ws = s; },
    onOpen: () => { document.getElementById('dot').className = 'dot ok'; },
    onMessage: e => { let m; try { m = JSON.parse(e.data); } catch (_) { return; } handleServerMessage(m); },
    onError: () => { document.getElementById('dot').className = 'dot err'; },
    onClose: async (e, reconnect) => {
      document.getElementById('dot').className = 'dot err';
      if (finished) return;                       // regulär abgeschlossen
      if (e.code === 4006) {                       // Token ungültig/entwertet
        clearToken();
        if (joinSecret) { if (await join()) reconnect(0); }
        else show('done');
        return;
      }
      setStatusText('Getrennt — neu verbinden…');
      reconnect();                                 // Netzproblem → erneut (mit Backoff)
    },
  });
}

function handleServerMessage(msg) {
  if (msg.type === 'pending') {
    document.getElementById('pair-code').textContent = msg.pairing_code;
    show('pending');
  } else if (msg.type === 'student_info') {
    // Identität (Name/Klasse/Bezahlt) sofort; Bücherliste folgt mit
    // `worker_ready`, sobald der Worker bereit ist.
    renderStudent(msg.student, msg.payment_overridden);
    show('active');
    startScanner();
  } else if (msg.type === 'worker_ready') {
    // Worker buchungsbereit: Bücherliste rendern, Status flippen, Scans frei.
    workerPending = false;
    currentBooks = msg.books || [];
    renderBooks(currentBooks);
    setStatusText('Scanner bereit — Buch scannen');
  } else if (msg.type === 'scan_result') {
    // Jeder nicht-verbuchbare Scan → Hinweis-Modal (wie bisher bei
    // ausgemustert / verliehen / an-sich-selbst, jetzt auch für „nicht
    // bestellt", „unbekannt", „noch nicht geladen", Prüf-Fehler).
    // Host-geschlossen (blocking): ausgemustert (mit/ohne Ersatzanspruch)
    // + an andere Person verliehen → kein Schließen-Button, serverseitig
    // blockiert `book_alert_open` weitere Scans, nur der Betreuer gibt per
    // `book_alert_clear` frei. Alle anderen nicht-OK Meldungen schließt der
    // Schüler selbst (Button / nächster Scan) und scannt weiter.
    const ok = OK_STATUSES_STUDENT.has(msg.status);
    const blocking = BLOCKING_STATUSES_STUDENT.has(msg.status);
    const dismissible = !ok && !blocking;
    setStatusText(scanResultStatusText(msg, currentBooks), statusAlertClass(msg.status));
    if (blocking) { bookAlertOpen = true; showBookAlertModal(msg, false); }
    else if (dismissible) { showBookAlertModal(msg, true); }
    // Erfolgreicher Scan → Buch als „erledigt" markieren (sinkt nach unten).
    if ((msg.status === 'staged' || msg.status === 'booked') && msg.isbn) {
      scannedIsbns.add(msg.isbn);
      scanOrder.set(msg.isbn, ++scanSeq);
      renderBooks(currentBooks, true);   // FLIP: Zeilen an neue Position fahren
    }
  } else if (msg.type === 'book_alert_clear') {
    // Der Host gibt frei — nur das Modal schließt. Die Statuszeile behält
    // Text UND Aussehen (z. B. rot „ausgemustert") bis zur nächsten
    // scan_result-Meldung; sie ändert sich nur zusammen mit neuem Text.
    bookAlertOpen = false;
    closeBookAlertModal();
  } else if (msg.type === 'closed') {
    finished = true; clearToken(); show('done');
  } else if (msg.type === 'error') {
    setStatusText('Fehler: ' + (msg.msg || ''));
  }
}

// ---- Buch-Hinweis-Modal. Blocking-Variante (ausgemustert / verliehen-an-
// andere): kein Schließen-Button, nur der Host gibt per `book_alert_clear`
// frei (book_alert_open blockiert weitere Scans). Hinweis-Variante (an sich
// selbst verliehen): Schließen-Button, vom Schüler lokal schließbar. ----
let bookAlertOpen = false;
const bookAlertModalEl = document.getElementById('book-alert-modal');
const bookAlertTitleEl = document.getElementById('book-alert-title');
const bookAlertTextEl = document.getElementById('book-alert-text');
const bookAlertNoteEl = document.getElementById('book-alert-note');
const bookAlertHintEl = document.getElementById('book-alert-hint');
const bookAlertSupportEl = document.getElementById('book-alert-support');
const bookAlertActionsEl = document.getElementById('book-alert-actions');
const bookAlertCloseBtn = document.getElementById('book-alert-close');
const ALERT_META_STUDENT = {
  book_deleted:        { title: 'Ausgemustertes Buch gescannt',   color: '#f44336' },
  not_in_stock:        { title: 'Buch bereits verliehen',         color: '#f44336' },
  book_already_lent:   { title: 'Buch bereits an dich verliehen', color: '#e69500' },
  series_already_lent: { title: 'Buchreihe bereits an dich verliehen', color: '#e69500' },
  not_enrolled:        { title: 'Buch nicht bestellt',            color: '#e69500' },
  unknown_book:        { title: 'Buch unbekannt',                 color: '#e69500' },
  not_ready:           { title: 'Buchliste noch nicht geladen',   color: '#e69500' },
  error:               { title: 'Fehler bei der Prüfung',         color: '#f44336' },
};
// Status, die NICHT verbucht werden können und die der Schüler selbst
// schließen darf (alle nicht-OK Status außer den Host-geschlossenen).
const OK_STATUSES_STUDENT = new Set(['staged', 'booked']);
// Host-geschlossen: ausgemustert (mit/ohne Ersatzanspruch) + an andere
// Person verliehen → blockierendes Modal, nur der Betreuer gibt frei.
const BLOCKING_STATUSES_STUDENT = new Set(['book_deleted', 'not_in_stock']);
// Statuszeilen-Farbklasse — abgeleitet aus ALERT_META_STUDENT.color, damit
// Statuszeile und Fenster-Überschrift IMMER dieselbe Farbe haben. Rot ist
// reserviert für Status, bei denen der Host schließen/freigeben muss
// (book_deleted, not_in_stock) sowie error; alle anderen Alert-Status
// (inkl. unbekannter Code) sind orange (selbst schließbar).
function statusAlertClass(status) {
  if (status === 'booked') return 'status-book-issued';
  if (OK_STATUSES_STUDENT.has(status)) return null;
  const meta = ALERT_META_STUDENT[status];
  return meta && meta.color === '#e69500' ? 'status-alert-orange' : 'status-alert-red';
}
function showBookAlertModal(msg, dismissible) {
  const meta = ALERT_META_STUDENT[msg.status] || { title: 'Buch-Hinweis', color: '#f44336' };
  // Ausgemustert OHNE Ersatzanspruch: eigene, kürzere Überschrift/Meldung.
  // loaned_to ist am Schüler-Client aus Privatheitsgründen ohnehin immer
  // null (s. process_scan) — dieser Fall greift hier also immer.
  const deletedNoReplacement = msg.status === 'book_deleted' && !msg.loaned_to;
  bookAlertTitleEl.textContent = deletedNoReplacement ? 'Buch ausgemustert' : meta.title;
  bookAlertTitleEl.style.color = meta.color;
  if (msg.status === 'book_already_lent') {
    bookAlertTextEl.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    bookAlertNoteEl.textContent = 'Dieses Buch ist bereits an dich verliehen. Du musstest es nicht noch einmal scannen.';
    bookAlertNoteEl.hidden = false;
  } else if (msg.status === 'series_already_lent') {
    bookAlertTextEl.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    bookAlertNoteEl.textContent = 'Ein Buch dieser Buchreihe ist bereits an dich verliehen. Leg es einfach wieder zurück.';
    bookAlertNoteEl.hidden = false;
  } else if (deletedNoReplacement) {
    bookAlertTextEl.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    bookAlertNoteEl.textContent = 'Dieses Buch ist ausgemustert. Es kann nicht mehr verliehen werden.';
    bookAlertNoteEl.hidden = false;
  } else if (msg.status === 'not_in_stock') {
    bookAlertTextEl.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    bookAlertNoteEl.textContent = 'Dieses Buch ist bereits an jemand anders verliehen. Es kann derzeit nicht an dich verliehen werden.';
    bookAlertNoteEl.hidden = false;
  } else if (msg.status === 'unknown_book') {
    // Kein Titel bekannt (Buch existiert laut API nicht) — nur der
    // gescannte Code, kein Bindestrich/Titel dahinter.
    bookAlertTextEl.textContent = `${msg.barcode || ''}`;
    bookAlertNoteEl.textContent = 'Dieser Code ist unbekannt. Bitte nochmal scannen.';
    bookAlertNoteEl.hidden = false;
  } else {
    bookAlertTextEl.textContent = `${msg.barcode || ''} — ${msg.msg || meta.title}`;
    bookAlertNoteEl.textContent = '';
    bookAlertNoteEl.hidden = true;
  }
  // Gedämpfte Notiz-Schrift NUR bei blockierenden Meldungen (dort steht
  // darunter die „Bitte warte…"-Hinweiszeile) — bei selbst schließbaren
  // Meldungen gibt es keine Hinweiszeile mehr, die Notiz bleibt normal.
  bookAlertNoteEl.classList.toggle('book-alert-dim', !dismissible);
  if (dismissible) {
    // „Du kannst diese Meldung selbst schließen." existiert bewusst nicht
    // mehr — der Schließen-Button spricht für sich.
    bookAlertHintEl.textContent = '';
    bookAlertHintEl.hidden = true;
    bookAlertActionsEl.style.display = '';
    // Zusätzlich, in unscheinbarer Schrift (wie Code/Titel oben), ein
    // Hinweis auf den Betreuer, falls der Fehler unerwartet wiederholt auftritt.
    bookAlertSupportEl.textContent = 'Falls dieser Fehler unerwartet weiterhin auftritt, melde dich bitte beim Betreuer.';
    bookAlertSupportEl.hidden = false;
  } else {
    bookAlertHintEl.textContent = 'Bitte warte, bis ein Helfer dieses Buch einsammelt und dich freigibt.';
    bookAlertHintEl.hidden = false;
    bookAlertActionsEl.style.display = 'none';
    bookAlertSupportEl.textContent = '';
    bookAlertSupportEl.hidden = true;
  }
  bookAlertModalEl.classList.add('show');
}
function closeBookAlertModal() { bookAlertModalEl.classList.remove('show'); }
// „An sich selbst verliehen"-Hinweis: lokal schließbar (kein Host-Bezug).
bookAlertCloseBtn.addEventListener('click', closeBookAlertModal);

function renderStudent(s, overridden) {
  bookAlertOpen = false;
  closeBookAlertModal();
  document.getElementById('s-name').textContent = `${s.lastname}, ${s.firstname}`;
  document.getElementById('s-form').textContent = (s.form || '').replace(/^Klasse\s+/i, '');
  const pay = document.getElementById('s-pay');
  if (!s.enrolled) {
    pay.innerHTML = '<span class="pay-badge wait">Nicht angemeldet</span>';
  } else {
    // „Nachweis fehlt" (Ermäßigung/Befreiung): Antrag gestellt, aber noch
    // unentschieden — gleiche Farbe wie „Offen". Reihenfolge: erst die
    // Nachweise, dann der Bezahlstatus. Beim Override bleibt der
    // Betreuer-Freigabe-Badge erhalten, der Offen-Betrag folgt dahinter.
    const parts = [];
    const nachweis = s.remission_pending || s.exemption_pending;
    if (s.remission_pending)  parts.push('<span class="pay-badge unpaid">Ermäßigungsnachweis fehlt</span>');
    if (s.exemption_pending) parts.push('<span class="pay-badge unpaid">Befreiungsnachweis fehlt</span>');
    // „Bezahlt" entfallen, wenn ein Nachweis fehlt — der Hinweis geht vor.
    if (s.paid && !nachweis) {
      parts.push('<span class="pay-badge paid">Bezahlt</span>');
    } else if (!s.paid) {
      if (overridden) {
        parts.push('<span class="pay-badge override">Vom Betreuer freigegeben</span>');
        parts.push(`<span class="pay-badge unpaid">offen: ${escapeHtml(s.amount_open)} €</span>`);
      } else {
        parts.push(`<span class="pay-badge unpaid">Nicht bezahlt — offen: ${escapeHtml(s.amount_open)} €</span>`);
      }
    }
    pay.innerHTML = parts.join(' · ');
  }
  if (Array.isArray(s.book_order)) bookOrder = s.book_order;
  // Bücherliste bleibt ausgeblendet, bis der Worker bereit ist (`worker_ready`):
  // Statuszeile „Wird geladen…", Placeholder im Bücher-Bereich, Scans ignoriert.
  workerPending = true;
  currentBooks = [];
  scannedIsbns.clear(); scanOrder.clear(); scanSeq = 0;
  document.getElementById('book-rows').innerHTML =
    '<div class="book-empty">Bücher werden geladen…</div>';
  setStatusText('Wird geladen…');
}

function renderBooks(books, animate = false) {
  const rows = document.getElementById('book-rows');
  if (!books || !books.length) {
    rows.innerHTML = '<div class="book-empty">Keine Bücher hinterlegt</div>';
    return;
  }
  // Erledigte (gescannt/ausgeliehen) nach unten. Offene nach der klassenweit
  // konfigurierten Reihenfolge (bookOrder; Rest ans Ende). Erledigte nach
  // Ausgabedatum (jüngstes oben); ohne Datum oben. Original-Index als Tiebreak.
  const orderIndex = isbn => {
    const i = bookOrder.indexOf(isbn);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  // „Erledigt"-Rang: gerade gescannte zuerst (Scan-Reihenfolge, zuletzt oben),
  // darunter schon ausgeliehene nach Ausgabedatum (jüngstes oben).
  const SCAN_BASE = 1e15;
  const doneRank = b => {
    if (b.isbn && scanOrder.has(b.isbn)) return SCAN_BASE + scanOrder.get(b.isbn);
    const t = b.distributed_at ? Date.parse(b.distributed_at) : NaN;
    return Number.isNaN(t) ? -1 : t;
  };
  // FLIP-Vorbereitung: alte Positionen je Buch (Original-Index als stabiler
  // Schlüssel) merken, BEVOR innerHTML ausgetauscht wird. Nur bei
  // animate=true (erfolgreicher Scan) — nicht beim initialen Laden.
  const oldRects = new Map();
  if (animate) {
    rows.querySelectorAll('.book-row[data-book-idx]').forEach(row => {
      oldRects.set(row.dataset.bookIdx, row.getBoundingClientRect());
    });
  }
  const ordered = books
    .map((b, i) => [b, i])
    .sort((a, b) => {
      const da = isBookDone(a[0], scannedIsbns) ? 1 : 0, db = isBookDone(b[0], scannedIsbns) ? 1 : 0;
      if (da !== db) return da - db;
      if (da === 1) {
        const diff = doneRank(b[0]) - doneRank(a[0]);
        if (diff) return diff;
      } else {
        const diff = orderIndex(a[0].isbn) - orderIndex(b[0].isbn);
        if (diff) return diff;
      }
      return a[1] - b[1];
    });
  rows.innerHTML = ordered.map(([b, idx]) => {
    const done = isBookDone(b, scannedIsbns);
    const icon = done
      ? '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
      : '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    const cls = done ? 'ausgeliehen' : 'vorgemerkt';
    return `<div class="book-row row-${cls}" data-book-idx="${idx}">`
      + `<div class="b-fach">${escapeHtml(b.subject)}</div>`
      + `<div class="b-title">${escapeHtml(b.title)}</div>`
      + `<div class="b-icon">${icon}</div></div>`;
  }).join('');
  // FLIP-Animation: jede Zeile, die schon da war, startet an ihrer alten
  // Position und fährt zur neuen. Neue Zeilen erscheinen sofort.
  if (animate && oldRects.size) {
    rows.querySelectorAll('.book-row[data-book-idx]').forEach(row => {
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

// ---- Finish ----
document.getElementById('finish-btn').addEventListener('click', () => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'finish' }));
});

// ================= Scanner (aus scan.html übernommen) =================
let lastValue = '', cooldown = false, html5QrCode = null, currentCameraId = null,
    isTorchOn = false, isRestarting = false, soundEnabled = false;
const cameraSelect = document.getElementById('camera-select');
const ICON_VOLUME_ON = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>';
const ICON_VOLUME_OFF = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>';
const torchBtn = document.getElementById('torch-btn');
const soundBtn = document.getElementById('sound-btn');
const reloadBtn = document.getElementById('reload-btn');
const gearBtn = document.getElementById('gear-btn');
const camDropdown = document.getElementById('cam-dropdown');
const readerEl = document.getElementById('reader');
let scanFlashTimeout = null;

// Zahnrad: Kamera-Dropdown auf/zu
gearBtn.addEventListener('click', (e) => { e.stopPropagation(); camDropdown.classList.toggle('open'); });
camDropdown.addEventListener('click', (e) => e.stopPropagation());
document.addEventListener('click', () => camDropdown.classList.remove('open'));

soundBtn.addEventListener('click', async () => {
  soundEnabled = !soundEnabled;
  soundBtn.innerHTML = soundEnabled ? ICON_VOLUME_ON : ICON_VOLUME_OFF;
  soundBtn.classList.toggle('sound-on', soundEnabled);
  if (soundEnabled) { await Beeper.initAudio(); Beeper.playBeep(); }
});

function onScanSuccess(value) {
  if (cooldown || value === lastValue) return;
  // Worker noch nicht bereit (Schüler gerade gepaart, open_student läuft) —
  // Scan ignorieren, nicht senden (wie beim ausgemusterten-Buch-Block).
  if (workerPending) return;
  // Blockierendes Hinweis-Modal offen → Barcode ignorieren, bis der Host
  // freigibt (kein eigener Schließen-Button am Client, s. book_alert_clear).
  if (bookAlertOpen) return;
  // Dismissibler Hinweis („an sich selbst verliehen") offen → beim nächsten
  // Scan selbst schließen (nicht-blockierend, kein Host-Bezug).
  if (bookAlertModalEl.classList.contains('show')) closeBookAlertModal();
  if (soundEnabled) Beeper.playBeep();
  lastValue = value; cooldown = true;
  setTimeout(() => { cooldown = false; lastValue = ''; }, 2000);
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'scan', value }));
  if (navigator.vibrate) navigator.vibrate(80);
  readerEl.classList.add('scan-success');
  clearTimeout(scanFlashTimeout);
  scanFlashTimeout = setTimeout(() => readerEl.classList.remove('scan-success'), 1200);
}

function startScanner() {
  if (scannerStarted) return;
  scannerStarted = true;
  Html5Qrcode.getCameras().then(cameras => {
    const preferred = cameras.find(c => /back dual wide/i.test(c.label))
      || cameras.find(c => /back/i.test(c.label)) || cameras[0];
    cameraSelect.innerHTML = cameras.map(c => `<option value="${escapeHtml(c.id)}" ${c === preferred ? 'selected' : ''}>${escapeHtml(c.label)}</option>`).join('');
    currentCameraId = preferred?.id;
    initScanner(currentCameraId);
  }).catch(() => { cameraSelect.innerHTML = '<option>Keine Kamera</option>'; cameraSelect.disabled = true; });
}

async function initScanner(cameraId) {
  if (isRestarting) return;
  isRestarting = true; reloadBtn.disabled = true; reloadBtn.textContent = '…';
  // Während des Ladens („Wird geladen…") Status nicht vom Kamera-Start
  // überschreiben lassen — `worker_ready` setzt danach den Ready-Status.
  if (!workerPending) setStatusText('Kamera startet…');
  if (html5QrCode) { try { await html5QrCode.stop(); } catch (e) {} try { html5QrCode.clear(); } catch (e) {} html5QrCode = null; }
  html5QrCode = new Html5Qrcode('reader');
  try {
    await html5QrCode.start(cameraId, { fps: 15, aspectRatio: 2.0 }, onScanSuccess, () => {});
    currentCameraId = cameraId; isTorchOn = false;
    torchBtn.classList.remove('torch-on');
    if (!workerPending) setStatusText('Scanner bereit — Buch scannen');
    const video = document.querySelector('#reader video');
    if (video && video.srcObject) {
      const caps = video.srcObject.getVideoTracks()[0].getCapabilities?.();
      torchBtn.disabled = !(caps && caps.torch);
    }
  } catch (err) { if (!workerPending) setStatusText('Kamerafehler — neu laden'); document.getElementById('dot').className = 'dot err'; }
  reloadBtn.innerHTML = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>'; reloadBtn.disabled = false; isRestarting = false;
}

cameraSelect.addEventListener('change', () => { initScanner(cameraSelect.value); camDropdown.classList.remove('open'); });
reloadBtn.addEventListener('click', () => { if (currentCameraId) initScanner(currentCameraId); });
torchBtn.addEventListener('click', async () => {
  const video = document.querySelector('#reader video');
  if (!video || !video.srcObject) return;
  const track = video.srcObject.getVideoTracks()[0];
  try { await track.applyConstraints({ advanced: [{ torch: !isTorchOn }] }); isTorchOn = !isTorchOn;
    torchBtn.classList.toggle('torch-on', isTorchOn); } catch (e) {}
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && currentCameraId && scannerStarted) setTimeout(() => initScanner(currentCameraId), 300);
});
setInterval(() => {
  if (document.hidden || isRestarting || !currentCameraId || !scannerStarted) return;
  const video = document.querySelector('#reader video');
  if (video && (video.paused || video.ended || video.readyState < 2)) initScanner(currentCameraId);
}, 3000);

// ================= Bootstrap =================
(async function boot() {
  if (token) { connect(); }            // bestehender Token → verbinden (Server prüft Gültigkeit)
  else if (await join()) { connect(); } // sonst per QR-Secret neue Session
})();
})();
