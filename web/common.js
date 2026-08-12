// web/common.js — Gemeinsame Helfer für die Ausleihe-Ausgabe-Frontends
// (host.js, scan.js, student.html, qr-display.html, drucker-display.js). Kein Build-Step: als
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
// nie WEM, s. process_scan). Unbekannter Code (`unknown_book`, kein Titel
// bekannt) → "<Buchcode> unbekannt" (ohne Bindestrich/Titel). `books` ist
// die aktuelle Bücherliste (student_info/currentBooks) der aufrufenden Seite.
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
  if (msg.status === 'unknown_book') {
    return `${msg.barcode} unbekannt`;
  }
  return `${msg.barcode} — ${msg.msg || msg.status}`;
}

// Gemeinsame Statuszeilen für den Leihschein-Druck im Helfer- und
// Schülerclient. Die Queue liefert dieselben Felder an beide Clients; die
// studentenspezifische Positionsberechnung passiert serverseitig.
function capitalizeSentenceStarts(text) {
  const value = String(text || '');
  return value.replace(
    /(^|[.!?]\s+)([a-zäöüß])/giu,
    (_match, prefix, first) => prefix + first.toLocaleUpperCase('de-DE'),
  );
}

function printProgressStatusText(msg) {
  if (msg.peer_error) {
    return capitalizeSentenceStarts(
      msg.msg || 'Es dauert ungewöhnlich lange, vielleicht liegt ein Fehler vor.',
    );
  }
  const printer = msg.printer_label;
  if (msg.status === 'printing') {
    return printer ? `Leihschein wird von ${printer} gedruckt…` : 'Leihschein wird gedruckt…';
  }
  if (typeof msg.position === 'number' && msg.position === 0) {
    return printer ? `Leihschein wartet an ${printer} auf Druck…` : 'Leihschein wartet auf Druck…';
  }
  if (typeof msg.position === 'number' && msg.position === 1) {
    return printer
      ? `Leihschein an 1. Druckerwarteschlangenposition von ${printer}`
      : 'Leihschein an 1. Druckerwarteschlangenposition';
  }
  if (typeof msg.position === 'number' && msg.position >= 2) {
    return `Leihschein an ${msg.position}. Druckerwarteschlangenposition`;
  }
  return 'Leihschein in Druckerwarteschlange…';
}

function printResultStatusText(msg) {
  if (msg.stalled) {
    return capitalizeSentenceStarts(msg.msg || 'Druck dauert ungewöhnlich lange');
  }
  if (msg.peer_error) {
    return capitalizeSentenceStarts(
      msg.msg || 'Es dauert ungewöhnlich lange, vielleicht liegt ein Fehler vor.',
    );
  }
  if (msg.ok) {
    return msg.printer_label
      ? `Leihschein von ${msg.printer_label} gedruckt.`
      : 'Leihschein gedruckt.';
  }
  return `Druck fehlgeschlagen: ${msg.msg || ''}`;
}

// ---- Beeper: Scan-Ton, kapselt AudioContext/-Buffer als Closure-State ----
// Gemeinsam für scan.js/student.html/drucker-display.js. Aufrufer prüfen weiterhin SELBST
// `soundEnabled`, AUSSERHALB von playBeep() — Beeper entscheidet nicht,
// ob geblept wird, nur wie.
const Beeper = (() => {
  let audioCtx = null, audioBuffer = null, audioInitPromise = null;

  async function resumeAudio() {
    if (!audioCtx || audioCtx.state === 'running') return;
    try { await audioCtx.resume(); } catch (_e) { /* Audio optional */ }
  }

  async function initAudio() {
    if (!audioCtx) {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (_e) { return; }
    }
    await resumeAudio();
    if (audioBuffer) return;
    if (!audioInitPromise) {
      audioInitPromise = (async () => {
        try {
          // Silent buffer to unlock iOS AudioContext during user gesture
          const silence = audioCtx.createBuffer(1, 1, audioCtx.sampleRate);
          const silSrc = audioCtx.createBufferSource();
          silSrc.buffer = silence; silSrc.connect(audioCtx.destination); silSrc.start(0);
          await audioCtx.resume();
          const response = await fetch('/beep.mp3');
          const arrayBuf = await response.arrayBuffer();
          audioBuffer = await audioCtx.decodeAudioData(arrayBuf);
        } catch (_e) { /* Audio optional — Ton entfällt, Rest der Seite bleibt nutzbar */ }
      })();
    }
    const pending = audioInitPromise;
    await pending;
    if (audioInitPromise === pending) audioInitPromise = null;
    // Ein erster Ladeversuch kann vom Browser noch als „suspended" behandelt
    // werden. Eine spätere Nutzergeste darf denselben AudioContext aufwecken.
    await resumeAudio();
  }
  function playBeep() {
    if (!audioCtx || audioCtx.state !== 'running' || !audioBuffer) return;
    const src = audioCtx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(audioCtx.destination);
    src.start(0);
  }
  return { initAudio, playBeep };
})();

// ---- PDF-Download aus base64 (Host-Leihschein-Download + Schülereigenabruf) ----
// Server schickt ein PDF base64-kodiert über eine WebSocket; hier als
// Blob-Download im Browser des Empfängers auslösen (Download-Prompt bzw.
// Ablage im Download-Ordner, je nach Browsereinstellung). Gemeinsam für
// host-ws.js (Leihschein-PDF-lokal-speichern) und student.js (eigener
// Leihschein, s. `own_slip_download`).
function downloadBase64Pdf(filename, dataB64) {
  const bin = atob(dataB64 || '');
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || 'leihschein.pdf';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

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

// ---- Druckerauswahl-Dropdown (Host- + Helfer-Druck-Dialog) ----
// Gemeinsame Komponente: geschlossener Trigger zeigt die gewählten Drucker als
// kommaseparierte „Label (Systemname)", aufgeklappt eine Checkbox-Liste aller
// Pool-Drucker. Aufrufer hält `pool`/`selectedIds` selbst (common.js speichert
// keinen modulweiten State); Änderungen via setPool/setSelectedIds.
//
// pool: [{id, name, label, is_default, faulty}] — name/label optional (None →
//   Standarddrucker); faulty optional (fehlerhaft/deaktiviert — Zeile wird
//   ausgegraut mit „(deaktiviert)"-Hinweis, bleibt aber anwählbar);
//   selectedIds: id[] (Vorauswahl).
// Rückgabe: {getSelectedIds, setPool, setSelectedIds, setEnabled, close}.
function mountPrinterPicker(mountEl, pool, selectedIds) {
  const ppool = Array.isArray(pool) ? pool : [];
  const selected = new Set(Array.isArray(selectedIds) ? selectedIds : []);

  // Anzeigename „Label (Systemname)" — Label wenn gesetzt, sonst Systemname,
  // sonst „Standarddrucker"; Systemname in Klammern nur, wenn vorhanden und
  // vom Anzeigenamen verschieden.
  function display(p) {
    const disp = p.label || p.name || 'Standarddrucker';
    return (p.name && p.name !== disp) ? `${disp} (${p.name})` : disp;
  }

  mountEl.innerHTML =
    '<div class="printer-picker">'
    + '<button type="button" class="pp-trigger" aria-expanded="false"><span class="pp-label"></span><span class="pp-caret">▾</span></button>'
    + '<div class="pp-panel" hidden></div>'
    + '</div>';
  const root = mountEl.querySelector('.printer-picker');
  const trigger = root.querySelector('.pp-trigger');
  const labelEl = root.querySelector('.pp-label');
  const panel = root.querySelector('.pp-panel');

  function renderPanel() {
    panel.innerHTML = ppool.length
      ? ppool.map(p => {
          const chk = selected.has(p.id) ? ' checked' : '';
          const faultyCls = p.faulty ? ' pp-row-faulty' : '';
          const hint = p.faulty ? ' <span class="pp-faulty-hint">(deaktiviert)</span>' : '';
          return `<label class="pp-row${faultyCls}"><input type="checkbox" data-pid="${escapeHtml(p.id)}"${chk}><span>${escapeHtml(display(p))}${hint}</span></label>`;
        }).join('')
      : '<div class="pp-empty">Kein Drucker konfiguriert</div>';
    panel.querySelectorAll('input[data-pid]').forEach(cb => {
      cb.addEventListener('change', () => {
        if (cb.checked) selected.add(cb.dataset.pid);
        else selected.delete(cb.dataset.pid);
        renderLabel();
      });
    });
  }

  function renderLabel() {
    const chosen = ppool.filter(p => selected.has(p.id));
    labelEl.textContent = chosen.length
      ? chosen.map(display).join(', ')
      : 'Kein Drucker ausgewählt';
    labelEl.classList.toggle('pp-placeholder', !chosen.length);
  }

  // Öffnet das Panel immer nach oben (statt nach unten), damit es nicht aus
  // dem Screen ragt; max-height wird auf den tatsächlich verfügbaren Platz
  // oberhalb des Triggers begrenzt.
  function positionPanel() {
    const margin = 8;
    const preferredMax = 220;
    const rect = trigger.getBoundingClientRect();
    const spaceAbove = rect.top - margin;
    panel.classList.add('pp-panel-up');
    panel.style.maxHeight = Math.max(120, Math.min(preferredMax, spaceAbove)) + 'px';
  }

  let open = false;
  function setOpen(v) {
    open = v;
    panel.hidden = !v;
    trigger.setAttribute('aria-expanded', v ? 'true' : 'false');
    if (v) {
      positionPanel();
      // Schließen bei Klick außerhalb (ein Doc-Listener pro Öffnung; wird beim
      // Schließen wieder entfernt, sodass geschlossene Picker keine Listener
      // akkumulieren).
      setTimeout(() => document.addEventListener('click', onDocClick), 0);
    } else {
      document.removeEventListener('click', onDocClick);
    }
  }
  function onDocClick(e) { if (!root.contains(e.target)) setOpen(false); }

  trigger.addEventListener('click', (e) => { e.stopPropagation(); setOpen(!open); });

  renderPanel();
  renderLabel();

  return {
    getSelectedIds() { return ppool.filter(p => selected.has(p.id)).map(p => p.id); },
    setPool(newPool) {
      const ids = new Set((Array.isArray(newPool) ? newPool : []).map(p => p.id));
      // Auswahl auf noch existierende Drucker kürzen.
      for (const id of [...selected]) if (!ids.has(id)) selected.delete(id);
      ppool.length = 0;
      ppool.push(...(Array.isArray(newPool) ? newPool : []));
      renderPanel();
      renderLabel();
    },
    setSelectedIds(ids) {
      selected.clear();
      (Array.isArray(ids) ? ids : []).forEach(id => selected.add(id));
      renderPanel();
      renderLabel();
    },
    setEnabled(b) {
      trigger.disabled = !b;
      panel.querySelectorAll('input').forEach(cb => { cb.disabled = !b; });
    },
    close() { setOpen(false); },
  };
}


// ---------------------------------------------------------------------------
// Scan-Ansicht: Bücher-Tabelle, Statusfarben und Buch-Hinweis-Modal.
//
// Gemeinsam vom Schülerclient (`student.js`) und der Scan-Station
// (`scan-station.js`) genutzt — beide zeigen denselben Scanmodus, also darf es
// dafür nur EINE Implementierung geben (Aussehen: `web/scan-view.css`).
// ---------------------------------------------------------------------------

const ALERT_META = {
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
const OK_SCAN_STATUSES = new Set(['staged', 'booked']);
// Host-geschlossen: ausgemustert (mit/ohne Ersatzanspruch) + an andere
// Person verliehen → blockierendes Modal, nur der Betreuer gibt frei.
const BLOCKING_SCAN_STATUSES = new Set(['book_deleted', 'not_in_stock']);
// Statuszeilen-Farbklasse — abgeleitet aus ALERT_META.color, damit
// Statuszeile und Fenster-Überschrift IMMER dieselbe Farbe haben. Rot ist
// reserviert für Status, bei denen der Host schließen/freigeben muss
// (book_deleted, not_in_stock) sowie error; alle anderen Alert-Status
// (inkl. unbekannter Code) sind orange (selbst schließbar).
function statusAlertClass(status) {
  if (status === 'booked') return 'status-book-issued';
  if (OK_SCAN_STATUSES.has(status)) return null;
  const meta = ALERT_META[status];
  return meta && meta.color === '#e69500' ? 'status-alert-orange' : 'status-alert-red';
}
function renderBookAlert(els, msg, dismissible) {
  const meta = ALERT_META[msg.status] || { title: 'Buch-Hinweis', color: '#f44336' };
  // Ausgemustert OHNE Ersatzanspruch: eigene, kürzere Überschrift/Meldung.
  // loaned_to ist am Schüler-Client aus Privatheitsgründen ohnehin immer
  // null (s. process_scan) — dieser Fall greift hier also immer.
  const deletedNoReplacement = msg.status === 'book_deleted' && !msg.loaned_to;
  els.title.textContent = deletedNoReplacement ? 'Buch ausgemustert' : meta.title;
  els.title.style.color = meta.color;
  if (msg.status === 'book_already_lent') {
    els.text.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    els.note.textContent = 'Dieses Buch ist bereits an dich verliehen. Du musstest es nicht noch einmal scannen.';
    els.note.hidden = false;
  } else if (msg.status === 'series_already_lent') {
    els.text.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    els.note.textContent = 'Ein Buch dieser Buchreihe ist bereits an dich verliehen. Leg es einfach wieder zurück.';
    els.note.hidden = false;
  } else if (deletedNoReplacement) {
    els.text.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    els.note.textContent = 'Dieses Buch ist ausgemustert. Es kann nicht mehr verliehen werden.';
    els.note.hidden = false;
  } else if (msg.status === 'not_in_stock') {
    els.text.textContent = `${msg.barcode || ''} — ${msg.title || meta.title}`;
    els.note.textContent = 'Dieses Buch ist bereits an jemand anders verliehen. Es kann derzeit nicht an dich verliehen werden.';
    els.note.hidden = false;
  } else if (msg.status === 'unknown_book') {
    // Kein Titel bekannt (Buch existiert laut API nicht) — nur der
    // gescannte Code, kein Bindestrich/Titel dahinter.
    els.text.textContent = `${msg.barcode || ''}`;
    els.note.textContent = 'Dieser Code ist unbekannt. Bitte nochmal scannen.';
    els.note.hidden = false;
  } else {
    els.text.textContent = `${msg.barcode || ''} — ${msg.msg || meta.title}`;
    els.note.textContent = '';
    els.note.hidden = true;
  }
  // Gedämpfte Notiz-Schrift NUR bei blockierenden Meldungen (dort steht
  // darunter die „Bitte warte…"-Hinweiszeile) — bei selbst schließbaren
  // Meldungen gibt es keine Hinweiszeile mehr, die Notiz bleibt normal.
  els.note.classList.toggle('book-alert-dim', !dismissible);
  if (dismissible) {
    // „Du kannst diese Meldung selbst schließen." existiert bewusst nicht
    // mehr — der Schließen-Button spricht für sich.
    els.hint.textContent = '';
    els.hint.hidden = true;
    els.actions.style.display = '';
    // Zusätzlich, in unscheinbarer Schrift (wie Code/Titel oben), ein
    // Hinweis auf den Betreuer, falls der Fehler unerwartet wiederholt auftritt.
    els.support.textContent = 'Falls dieser Fehler unerwartet weiterhin auftritt, melde dich bitte beim Betreuer.';
    els.support.hidden = false;
  } else {
    els.hint.textContent = 'Bitte warte, bis ein Helfer dieses Buch einsammelt und dich freigibt.';
    els.hint.hidden = false;
    els.actions.style.display = 'none';
    els.support.textContent = '';
    els.support.hidden = true;
  }
  els.modal.classList.add('show');
}

// Buch-Hinweis-Modal schließen (Gegenstück zu `renderBookAlert`).
function hideBookAlert(els) { els.modal.classList.remove('show'); }

// Bücher-Tabelle in `rows` rendern. `opts`:
//   bookOrder     — klassenweite ISBN-Reihenfolge für die offenen Bücher
//   scannedIsbns  — in dieser Sitzung erfolgreich gescannte ISBNs (Set)
//   scanOrder     — ISBN -> laufende Scan-Nummer (Map), für die Sortierung
//                   der erledigten Zeilen (zuletzt gescanntes oben)
//   animate       — FLIP-Animation nach einem erfolgreichen Scan
function renderBookRows(rows, books, opts) {
  const { bookOrder = [], scannedIsbns = new Set(), scanOrder = new Map(), animate = false } = opts || {};
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
