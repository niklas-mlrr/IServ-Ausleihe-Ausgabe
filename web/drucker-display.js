// Drucker-Display (`/drucker-display`) — reine Anzeige für einen Bildschirm
// neben den Druckern. Verbindet sich unauthentifiziert via `/ws/drucker-display`
// (s. server/routes/ws.py). Vor dem Host-Pairing zeigt es NUR die Registrierungs-
// Nummer; danach die vom Host zugewiesenen Drucker + die gefilterte zentrale
// Warteschlange. Live via WebSocket-Push (kein Polling).

const viewRegister = document.getElementById('view-register');
const viewQueue = document.getElementById('view-queue');
const viewForbidden = document.getElementById('view-forbidden');
const connDot = document.getElementById('conn-dot');
const connText = document.getElementById('conn-text');
const content = document.getElementById('dd-content');
// Laufende TTL-Timer, die „Gedruckt"-Kategorien nach 30s ausblenden.
let printedTimers = [];
// Feedback für den Übergang „Wird gedruckt" → „Gedruckt". Der Server hält den
// zuletzt fertigen Auftrag pro Drucker 30s; der Client merkt sich zusätzlich,
// wann dieser Auftrag neu eingetroffen ist, damit ein erneuter WebSocket-
// Snapshot die 5s-Animation nicht wieder auf volle Grünintensität zurücksetzt.
const FINISHED_FEEDBACK_MS = 5000;
const printedJobByPrinter = new Map();
const finishedHighlights = new Map();
let hasQueueSnapshot = false;

function printedJobKey(printer) {
  if (printer.printed_job_id != null) return `id:${printer.printed_job_id}`;
  return printer.printed_name ? `name:${printer.printed_name}` : null;
}

function markFinishedJob(printerId, jobKey) {
  const highlightKey = `${printerId}::${jobKey}`;
  const startedAt = Date.now();
  finishedHighlights.set(highlightKey, startedAt);
  setTimeout(() => {
    if (finishedHighlights.get(highlightKey) === startedAt) finishedHighlights.delete(highlightKey);
  }, FINISHED_FEEDBACK_MS + 100);
}

function detectFinishedJobs(pool) {
  let finishedCount = 0;
  for (const printer of pool) {
    const printerId = String(printer.id);
    const currentJob = printedJobKey(printer);
    const previousJob = printedJobByPrinter.get(printerId);
    if (hasQueueSnapshot && currentJob && currentJob !== previousJob) {
      markFinishedJob(printerId, currentJob);
      finishedCount += 1;
    }
    printedJobByPrinter.set(printerId, currentJob);
  }
  hasQueueSnapshot = true;
  return finishedCount;
}

function finishedFeedbackFor(printer) {
  const jobKey = printedJobKey(printer);
  const highlightKey = jobKey ? `${printer.id}::${jobKey}` : '';
  const startedAt = highlightKey ? finishedHighlights.get(highlightKey) : undefined;
  if (startedAt == null) return { className: '', inlineStyle: '' };
  const age = Date.now() - startedAt;
  if (age >= FINISHED_FEEDBACK_MS) {
    finishedHighlights.delete(highlightKey);
    return { className: '', inlineStyle: '' };
  }
  return {
    className: 'dd-order-finished',
    inlineStyle: `animation-delay: -${Math.max(0, age)}ms;`,
  };
}

// Doppel-Scan am Drucker-Scanner ("already"): dasselbe, bereits existierende
// Kästchen in Warteschlange/Drucker-Karte einmalig gelb umranden statt ein
// zweites zu zeigen — Mirror von markFinishedJob/finishedFeedbackFor, nur
// direkt nach job_id (nicht printerId+jobKey) geschlüsselt, da der Auftrag in
// der Warteschlange ODER auf einem Drucker stehen kann.
const flaggedHighlights = new Map();

function markFlaggedJob(jobId) {
  if (!jobId) return;
  const startedAt = Date.now();
  flaggedHighlights.set(jobId, startedAt);
  setTimeout(() => {
    if (flaggedHighlights.get(jobId) === startedAt) flaggedHighlights.delete(jobId);
  }, FINISHED_FEEDBACK_MS + 100);
}

function flaggedFeedbackFor(jobId) {
  const startedAt = jobId ? flaggedHighlights.get(jobId) : undefined;
  if (startedAt == null) return { className: '', inlineStyle: '' };
  const age = Date.now() - startedAt;
  if (age >= FINISHED_FEEDBACK_MS) {
    flaggedHighlights.delete(jobId);
    return { className: '', inlineStyle: '' };
  }
  return {
    className: 'dd-order-flagged',
    inlineStyle: `animation-delay: -${Math.max(0, age)}ms;`,
  };
}

// Drucker-Displays laufen oft ohne weitere Interaktion. Der Ladeversuch ist
// deshalb best effort; die erste Pointer-/Tastaturgeste kann einen vom Browser
// gesperrten AudioContext nachträglich freischalten (Autoplay-Regel).
function primePrinterAudio() { void Beeper.initAudio(); }
primePrinterAudio();
document.addEventListener('pointerdown', primePrinterAudio, { once: true, passive: true });
document.addEventListener('keydown', primePrinterAudio, { once: true });

function playFinishedSound() {
  void Beeper.initAudio().then(() => Beeper.playBeep());
}

// Wurde das Display vom Betreuer gesperrt? Dann KEIN automatischer Reconnect
// (der Token bleibt verboten — erneute Verbindungsversuche sind sinnlos und
// würden nur „gesperrt"-Meldungen flackern lassen).
let forbidden = false;
// Snapshot-Koalescing für die FLIP-Animation: trifft während einer laufenden
// Bewegung (FLIP_MS) ein neuer Snapshot ein, wird er gehalten und erst
// angewendet, wenn die Bewegung abgelaufen ist. So startet keine neue
// Bewegung, die eine noch laufende abbricht (Nutzer-Vorgabe: alle Bewegungen
// sichtbar, keine darf eine andere mittendrin abreißen). Eintreffende
// Snapshots zwischenzeitlich werden zum letzten gepoolt (nur der aktuellste
// zählt). Erscheinen/Verschwinden (neuer/entfernter Schlüssel) animiert nicht
// — nur Positions-/Größenänderungen bewegen sich.
const FLIP_MS = 500;
let flipAnimating = false;
let pendingQueueMsg = null;
// Seed-Rect für einen aufgeschobenen Scanner-„Reise"-Übergang (s.
// revertScannerAndFlip) — zieht mit pendingQueueMsg mit, damit ein während
// einer laufenden Animation ausgelöster Revert nicht verloren geht.
let pendingQueueSeed = null;
// Aufgeschobene „Gedruckt"-TTL-Entfernung: trifft der TTL-Timer während einer
// laufenden FLIP-Animation, wird die Entfernung zurückgestellt und nach
// Ablauf von flushPendingQueue nachgezogen (s. Nutzer-Vorgabe: beim
// Verschwinden soll der Rest gleiten, nicht springen — und keine Bewegung
// darf eine andere abbrechen).
let pendingTtlPid = null;
// Letzter vollständiger Server-Snapshot — Grundlage für den erzwungenen
// Re-Render, wenn eine Scanner-Karte lokal nach 10s abläuft, ohne dass der
// Server von sich aus einen neuen Snapshot pusht (s. revertScannerAndFlip).
let lastQueueMsg = null;
// Ein Scanner, dessen lokaler 10s-Timer abgelaufen ist, wird clientseitig auf
// „leer" erzwungen (der Server berechnet `expires_in` zwar korrekt nach,
// pusht aber nicht von sich aus einen Snapshot GENAU beim Ablauf) — bis ein
// GENUIN NEUER Scan-Event vom Server eintrifft (anderer status/code als beim
// letzten Mal verarbeitet, s. resolveScanners).
let locallyExpiredScanners = new Set();
let scannerEventSeen = new Map();

function show(name) {
  viewRegister.classList.toggle('show', name === 'register');
  viewQueue.classList.toggle('show', name === 'queue');
  viewForbidden.classList.toggle('show', name === 'forbidden');
}

// Anzeige-Label eines Druckers: nur der Name (Label), nicht der Systemname.
// Fallback auf Systemname bzw. „Standarddrucker", wenn kein Label gesetzt.
function printerLabel(p) {
  if (p.label && p.label.trim()) return p.label.trim();
  return p.is_default || !p.name ? 'Standarddrucker' : p.name;
}

// Druckauftrags-String „Nachname, Vorname (Form)" → Klasse (ohne Klammern)
// + Name. Form fehlt → leerstring (Name rückt trotzdem bündig, s. Grid).
function parseOrder(s) {
  const m = s.match(/^(.*?)\s*\(([^)]*)\)\s*$/);
  return m ? { name: m[1].trim(), form: m[2].trim() } : { name: s.trim(), form: '' };
}

// Helfer-Symbol (Person: Kopf + Schultern) — dasselbe SVG wie im Host
// (host-state.js ICO_HELPER, „aktuelle Ausgabe"-Now-Serving-Helferlabel).
const ICO_HELPER = '<svg class="dd-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
// Host-Symbol (Laptop aus geometrischen Figuren: Display-Rechteck + Basis).
const ICO_LAPTOP = '<svg class="dd-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="11" rx="1"/><path d="M2 20h20"/></svg>';

// Auftraggeber rechts im Kästchen: Helfer → Person-Symbol + Name; Host →
// Laptop-Symbol (ohne Name); Schüler/sonstige → nichts (derzeit nicht enqueueiert).
function originatorHtml(o) {
  if (!o) return '';
  if (o.type === 'helper') {
    return `<span class="dd-origin-kind">${ICO_HELPER}</span><span class="dd-origin-name">${escapeHtml(o.name || '')}</span>`;
  }
  if (o.type === 'host') {
    return `<span class="dd-origin-kind">${ICO_LAPTOP}</span>`;
  }
  return '';
}

// Ein Auftrag als Kästchen: [Klasse] [Name, Vorname] [Auftraggeber] — Klasse
// in fester Spaltenbreite (bündige Namen), Auftraggeber rechts. `key` ist der
// FLIP-Schlüssel = job_id (stabil über alle Behälter: Warteschlange ↔ Drucker-
// kategorie). So fährt ein Auftrag beim Wechseln des Behälters (z. B.
// Warteschlange → Nächster → Wird gedruckt → Gedruckt) von seiner alten an
// seine neue Position + Größe, statt zu springen.
// `name`/`form` kommen direkt (Warteschlange: w.student/w.form) oder werden
// aus dem kombinierten String „Name (Form)" gesplittet (Druckeraufträge:
// j.name, printed_name) — letzteres via parseOrder.
function orderBox(key, name, form, extraClass, originator, inlineStyle = '', extraAttrs = '') {
  const cls = extraClass ? ` ${extraClass}` : '';
  const style = inlineStyle ? ` style="${escapeHtml(inlineStyle)}"` : '';
  return `<div class="dd-order${cls}"${style} data-order-key="${escapeHtml(key)}"${extraAttrs}>`
    + `<span class="dd-form">${escapeHtml(form || '')}</span>`
    + `<span class="dd-stname">${escapeHtml(name)}</span>`
    + `<span class="dd-origin">${originatorHtml(originator)}</span>`
    + `</div>`;
}
// Variante für kombinierte Strings „Name (Form)" (Druckeraufträge, gedruckt):
// splittet per parseOrder und reicht die Teile an orderBox weiter.
function orderBoxFromRaw(key, raw, extraClass, originator, inlineStyle = '') {
  const { name, form } = parseOrder(raw);
  return orderBox(key, name, form, extraClass, originator, inlineStyle);
}

// FLIP nach einer DOM-Änderung: Karten, Auftrags-Kästchen UND Kategorie-Labels
// von ihren alten an ihre neuen Positionen gleiten lassen. Die Bewegung der
// inneren Elemente (Kästchen, Labels) wird RELATIV zur jeweiligen Karte
// gerechnet (Karten-Delta abgezogen), damit der Karten-FLIP nicht doppelt
// greift: ein Kästchen, dessen Karte nach unten wandert (z. B. weil das Grid
// darüber wächst), bekommt nur die Bewegung INNERHALB seiner Karte als eigene
// transform — das Mitwandern mit der Karte übernimmt deren FLIP. So springt
// nichts, und keine Bewegung bricht eine andere ab (Snapshots werden in
// scheduleQueueRender koalesziert). Spiegel der Bücherliste (scan-render.js),
// hier aber mit Karten als zusätzlicher Behälter-Ebene.
function flipFromOldRects(oldCardRects, oldOrderRects, oldLabelRects) {
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // Karten-Deltas (Bewegung je Karte) — Grundlage für den relativen FLIP der
  // inneren Elemente. Vor allen transform-Zuweisungen messen, damit die Karten
  // noch im untransformierten Zustand sind.
  const cardDeltas = new Map();
  content.querySelectorAll('.printer-card, .dd-waiting-card').forEach(el => {
    const old = oldCardRects.get(el.dataset.flipId);
    const cur = el.getBoundingClientRect();
    cardDeltas.set(el, old ? { dx: old.left - cur.left, dy: old.top - cur.top } : { dx: 0, dy: 0 });
  });
  // Auftrags-Kästchen: translate + scale, relativ zur Karte. Behälterwechsel
  // (Warteschlange → Nächster → Wird gedruckt → Gedruckt) verbindet der stabile
  // job_id-Schlüssel; das Kästchen wandert UND schrumpft (Drucker-Spalte ist
  // schmaler als die vollbreite Warteschlange). transform-origin 0 0, damit
  // scale + translate die alte Box exakt treffen. Neue Kästchen (kein alter
  // Eintrag) erscheinen sofort — Erscheinen wird nicht animiert.
  content.querySelectorAll('.dd-order[data-order-key]').forEach(el => {
    const old = oldOrderRects.get(el.dataset.orderKey);
    if (!old) return;
    const cur = el.getBoundingClientRect();
    const card = el.closest('.printer-card, .dd-waiting-card');
    const cd = cardDeltas.get(card) || { dx: 0, dy: 0 };
    const dx = (old.left - cur.left) - cd.dx;
    const dy = (old.top - cur.top) - cd.dy;
    const sx = cur.width ? old.width / cur.width : 1;
    const sy = cur.height ? old.height / cur.height : 1;
    if (!dx && !dy && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return;
    el.style.transition = 'none';
    el.style.transformOrigin = '0 0';
    el.style.transform = `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`;
    el.offsetWidth;  // Reflow erzwingen, damit die Startposition greift
    el.style.transition = reduceMotion ? 'transform .01ms' : '';
    el.style.transform = '';
  });
  // Kategorie-Labels („Gedruckt"/„Wird gedruckt"/„Nächster"/„Warteschlange"):
  // gleiten nach, wenn eine Kategorie kollabiert — z. B. „Gedruckt"-Kästchen
  // nach TTL verschwindet → „Wird gedruckt"/„Nächster" rücken nach oben, oder
  // ein Auftrag verlässt „Wird gedruckt"/„Nächster" → der Rest darunter gleitet
  // hoch. translate, relativ zur Karte; kein scale (Label behält Breite).
  content.querySelectorAll('.dd-cat-label[data-flip-id]').forEach(el => {
    const old = oldLabelRects.get(el.dataset.flipId);
    if (!old) return;
    const cur = el.getBoundingClientRect();
    const card = el.closest('.printer-card, .dd-waiting-card');
    const cd = cardDeltas.get(card) || { dx: 0, dy: 0 };
    const dx = (old.left - cur.left) - cd.dx;
    const dy = (old.top - cur.top) - cd.dy;
    if (!dx && !dy) return;
    el.style.transition = 'none';
    el.style.transform = `translate(${dx}px, ${dy}px)`;
    el.offsetWidth;
    el.style.transition = reduceMotion ? 'transform .01ms' : '';
    el.style.transform = '';
  });
  // Karten selbst: translate (kein scale — Karten behalten ihre Breite). Als
  // Letztes, damit die inneren Elemente vorher im untransformierten Rahmen der
  // Karte gemessen wurden.
  content.querySelectorAll('.printer-card, .dd-waiting-card').forEach(el => {
    const old = oldCardRects.get(el.dataset.flipId);
    if (!old) return;
    const cur = el.getBoundingClientRect();
    const dx = old.left - cur.left;
    const dy = old.top - cur.top;
    if (!dx && !dy) return;
    el.style.transition = 'none';
    el.style.transform = `translate(${dx}px, ${dy}px)`;
    el.offsetWidth;
    el.style.transition = reduceMotion ? 'transform .01ms' : '';
    el.style.transform = '';
  });
}

// Überlauf der Warteschlangen-Karte: ALLE Namen bleiben stets gerendert (kein
// Cap, kein Clip) — die Karte behält ihre natürliche Höhe. Eine Mask-Gradient
// blendet ab der vorletzten sichtbaren Zeile nach unten aus: der unterste
// Bereich (eine Zeile hoch) geht von deckend (oben) zu vollständig transparent
// (unten) über, alles darunter ist vollständig transparent — weiterhin
// gerendert, nur unsichtbar. Beim Resize neu berechnet. Nur die Warteschlangen-
// Karte (Drucker-Karten unberührt). Die Seite selbst klebt fix im Viewport
// (body overflow:hidden) — die transparente Überlappung wird unten abgeschnitten,
// was unsichtbar bleibt, da sie ohnehin transparent ist. Aufgerufen nach jedem
// Render (vor dem FLIP, dann stimmt die Maske für die neue Layoutposition),
// nach Ablauf einer FLIP-Animation (flushPendingQueue) und bei Resize.
let waitingOverflowTimer = null;
function applyWaitingOverflow() {
  const card = content.querySelector('.dd-waiting-card');
  if (!card) return;
  const top = card.getBoundingClientRect().top;
  const avail = window.innerHeight - top - 24;  // body padding unten
  if (avail <= 0) {
    card.style.maskImage = ''; card.style.webkitMaskImage = '';
    return;
  }
  // Eine Zeile hoch für den Ausblende-Bereich (Schriftgröße + Padding).
  const row = card.querySelector('.dd-order');
  const rowH = row ? row.getBoundingClientRect().height : 60;
  if (card.scrollHeight > avail) {
    const opaque = Math.max(0, avail - rowH);
    const fade = `linear-gradient(to bottom, black 0px, black ${opaque}px, transparent ${avail}px)`;
    card.style.maskImage = fade;
    card.style.webkitMaskImage = fade;
  } else {
    card.style.maskImage = ''; card.style.webkitMaskImage = '';
  }
}

// ---- Drucker-Scanner-Karten -------------------------------------------
// Zustände (s. server/routes/ws.py::_handle_drucker_scan):
//   null/abgelaufen → „Schülercode scannen", keine Bewegung, kein Label.
//   'checking'      → „<code> wird geprüft", gleiche Position.
//   'ready'         → Schritt-Label „Wartet jetzt auf Druck", Kästchen
//                      (Klasse+Name) fährt nach unten, ggf. Unterschreiben-
//                      Hinweis + „Zettel entsorgen"-Hinweis darunter. Nach
//                      Ablauf der 10s „reist" dasselbe Kästchen (kein neues)
//                      weiter an seine echte Position in der Warteschlange
//                      oder auf einem Drucker — s. revertScannerAndFlip.
//   'already'       → Schritt-Label je nach Job-Status, Kästchen fährt nach
//                      unten, Hinweis „bereits aufgegeben" darunter. Das
//                      ECHTE, bereits existierende Kästchen in Warteschlange/
//                      Drucker-Karte wird zusätzlich einmalig gelb umrandet
//                      (s. markFlaggedJob) — hier entsteht KEIN Reise-Effekt.
//   'pending_books' → keine Bewegung/Label, Kästchen zeigt Klasse+Name,
//                      Hinweis „noch nicht alle Bücher" darunter.
//   'unknown'       → keine Bewegung/Label, Kästchen zeigt „Code unbekannt",
//                      Hinweis „beim Betreuer melden" darunter.
// Die Positionsänderung („Kästchen fährt nach unten") innerhalb der Scanner-
// Karte entsteht rein aus der DOM-Reihenfolge (Kästchen steht bei 'ready'/
// 'already' NACH dem Schritt-Label statt direkt nach dem Hinweistext) — der
// bestehende FLIP-Mechanismus (gleiche Klassen/Datenattribute wie bei den
// Druckerkarten) übernimmt die Animation automatisch, ohne eigenen Code.
function scannerStepLabel(s) {
  if (s.status === 'ready') return 'Wartet jetzt auf Druck';
  if (s.status === 'already') {
    const js = (s.payload || {}).job_status;
    if (js === 'waiting') return 'Wartet bereits';
    if (js === 'printing') return 'Wird bereits gedruckt';
    return 'Ist bereits gedruckt';
  }
  return null;
}

function scannerBoxLine(s) {
  if (s.status === 'checking') return { name: `${s.code || ''} wird geprüft`, form: '' };
  if (s.status === 'ready' || s.status === 'already' || s.status === 'pending_books') {
    const p = s.payload || {};
    const name = [p.lastname, p.firstname].filter(Boolean).join(', ') || '–';
    return { name, form: p.form || '' };
  }
  if (s.status === 'unknown') return { name: 'Code unbekannt', form: '' };
  return { name: 'Schülercode scannen', form: '' };
}

// Mehrzeiliger Hinweistext unter dem Kästchen — jede Zeile ein eigenes
// `.dd-scan-below`-Element (kein `<br>`, damit jede Zeile für sich lesbar
// bleibt, Mirror der Bücherliste).
function scannerBelowLines(s) {
  if (s.status === 'ready') {
    const p = s.payload || {};
    const lines = [];
    if (p.done_signed) {
      const who = p.recipient === 'teacher' ? 'Lehrer' : 'Betreuer';
      lines.push(`Bitte den Leihschein anschließend unterschreiben und beim ${who} abgeben.`);
    }
    lines.push('Du kannst den Zettel mit dem Schülercode jetzt entsorgen.');
    return lines;
  }
  if (s.status === 'already') {
    return ['Der Druckauftrag für diesen Leihschein ist bereits aufgegeben worden.'];
  }
  if (s.status === 'pending_books') {
    return ['Du hast noch nicht alle Bücher ausgeliehen. Bitte schaue nochmal an einer Scan-Station nach und hole es nach.'];
  }
  if (s.status === 'unknown') {
    return ['Falls dies eigentlich doch ein gültiger Code sein sollte, bitte beim Betreuer melden.'];
  }
  return [];
}

function scanCardHtml(s) {
  const sid = escapeHtml(s.scanner_id);
  const stepLabel = scannerStepLabel(s);
  const line = scannerBoxLine(s);
  const belowLines = scannerBelowLines(s);
  const stepHtml = stepLabel
    ? `<div class="dd-cat-label" data-flip-id="scan-step::${sid}">${escapeHtml(stepLabel)}</div>`
    : '';
  // `data-travel-job-id`: nur im 'ready'-Fall gesetzt — der Reise-Übergang
  // (s. revertScannerAndFlip) betrifft ausschließlich frisch erzeugte
  // Aufträge, nicht den 'already'-Fall (der bekommt stattdessen den gelben
  // Umrandungs-Blitzer am bereits existierenden Kästchen).
  const travelJobId = s.status === 'ready' && s.payload && s.payload.job_id ? s.payload.job_id : null;
  const travelAttr = travelJobId ? ` data-travel-job-id="${escapeHtml(travelJobId)}"` : '';
  const boxHtml = orderBox(`scan-box::${s.scanner_id}`, line.name, line.form, '', null, '', travelAttr);
  const belowHtml = belowLines.map(t => `<div class="dd-scan-below">${escapeHtml(t)}</div>`).join('');
  const name = escapeHtml(s.label && s.label.trim() ? s.label : 'Scanner');
  return `<div class="printer-card dd-scan-card" data-flip-id="scan::${sid}" data-scanner="${sid}">
    <div class="printer-name">${name}</div>
    <div class="dd-scan-hint">Bitte scanne deinen Schülercode ein, um deinen Leihschein zu drucken.</div>
    ${stepHtml}
    ${boxHtml}
    ${belowHtml}
  </div>`;
}

// Scan-Ergebnis-Events je Scanner tracken: der Client erzwingt den Rückfall
// auf den Default-Zustand exakt nach Ablauf der lokalen 10s (s.
// revertScannerAndFlip), unabhängig davon, ob der Server von sich aus einen
// neuen Snapshot pusht (er berechnet `expires_in` zwar korrekt nach, pusht
// aber nicht proaktiv bei reinem Ablauf). `locallyExpiredScanners`
// überschreibt den Status dieses einen Scanners auf „leer", bis ein GENUIN
// NEUER Scan-Event vom Server eintrifft (anderer status/code als beim
// letzten Mal verarbeitet) — der hebt die lokale Sperre wieder auf. Ein
// frischer 'already'-Event löst hier zusätzlich den gelben Umrandungs-
// Blitzer am echten Kästchen aus (s. markFlaggedJob).
function resolveScanners(rawScanners) {
  return rawScanners.map(s => {
    const eventKey = `${s.status || ''}::${s.code || ''}`;
    const prevKey = scannerEventSeen.get(s.scanner_id);
    if (s.status != null && eventKey !== prevKey) {
      locallyExpiredScanners.delete(s.scanner_id);
      if (s.status === 'already' && s.payload && s.payload.job_id) {
        markFlaggedJob(s.payload.job_id);
      }
    }
    scannerEventSeen.set(s.scanner_id, eventKey);
    if (locallyExpiredScanners.has(s.scanner_id)) {
      return { ...s, status: null, code: null, payload: null, expires_in: null };
    }
    return s;
  });
}

// Scanner-Karte nach Ablauf der 10s lokal auf den Default-Zustand zurückfallen
// lassen. War der Zustand 'ready' (Kästchen trägt `data-travel-job-id`), wird
// dessen AKTUELLE Position gesichert und als Startpunkt für den FLIP an die
// renderQueue()-Neuberechnung übergeben (`seed`) — dasselbe Kästchen „reist"
// dadurch sichtbar von der Scanner-Karte an seine echte Position in der
// Warteschlange oder auf einem Drucker (kein neues Kästchen, s. Modul-
// Kommentar oben). Ein voller Re-Render (statt einer gezielten DOM-Mutation
// wie bei removePrintedAndFlip) ist hier nötig, weil das Ziel — wo genau der
// Auftrag inzwischen steht — nur der Server kennt.
function revertScannerAndFlip(scannerId) {
  const card = content.querySelector(`.dd-scan-card[data-scanner="${CSS.escape(scannerId)}"]`);
  const box = card ? card.querySelector('.dd-order[data-travel-job-id]') : null;
  const seed = box
    ? { jobId: box.dataset.travelJobId, rect: box.getBoundingClientRect() }
    : null;
  locallyExpiredScanners.add(scannerId);
  if (!lastQueueMsg) return;
  scheduleQueueRender(lastQueueMsg, seed);
}

function renderQueue(msg, seed) {
  lastQueueMsg = msg;
  const pool = Array.isArray(msg.printers) ? msg.printers : [];
  // Nur aktuell verbundene Scanner rendern — ein zugewiesener, aber gerade
  // getrennter Scanner soll keine „scanne hier"-Karte zeigen, an der gerade
  // nichts ankommt.
  const scanners = resolveScanners(
    (Array.isArray(msg.scanners) ? msg.scanners : []).filter(s => s.connected)
  );
  // Aufträge, die GERADE als 'ready' an einer Scanner-Karte gezeigt werden
  // (Kästchen „reist" erst nach Ablauf der 10s dorthin, s. revertScannerAndFlip):
  // an ihrer echten Position (Warteschlange/Drucker) so lange unterdrückt,
  // damit dasselbe Kästchen nicht doppelt erscheint.
  const readyTravelJobIds = new Set(
    scanners
      .filter(s => s.status === 'ready' && s.payload && s.payload.job_id)
      .map(s => s.payload.job_id)
  );

  if (!pool.length && !scanners.length) {
    content.innerHTML =
      '<p class="hint">Kein Drucker zugewiesen — am Host Druckerkapazitäten für dieses Display einstellen.</p>';
    return;
  }

  if (detectFinishedJobs(pool)) playFinishedSound();

  // Alte TTL-Timer („Gedruckt" nach 30s ausblenden) abräumen — der neue
  // Snapshot baut sie neu auf.
  for (const t of printedTimers) clearTimeout(t);
  printedTimers = [];

  // FLIP-Vorbereitung: alte Positionen + Größen je Auftrag UND je Karte
  // merken, BEVOR innerHTML ausgetauscht wird. Aufträge, die den Behälter
  // wechseln (Warteschlange → Nächster → Wird gedruckt → Gedruckt), fahren an
  // ihre neue Position + Größe (gleicher job_id-Schlüssel verbindet die
  // Behälter) — sie schrumpfen dabei, weil die Drucker-Spalte schmaler ist als
  // die vollbreite Warteschlange. Die Karten selbst gleiten nach, wenn sich
  // die Reihenhöhe verschiebt. Spiegel der Bücherliste (scan-render.js).
  const oldRects = new Map();
  content.querySelectorAll('.dd-order[data-order-key]').forEach(el => {
    oldRects.set(el.dataset.orderKey, el.getBoundingClientRect());
  });
  const oldCardRects = new Map();
  content.querySelectorAll('.printer-card, .dd-waiting-card').forEach(el => {
    oldCardRects.set(el.dataset.flipId, el.getBoundingClientRect());
  });
  // Labels („Gedruckt"/„Wird gedruckt"/„Nächster"/„Warteschlange") — eigener
  // FLIP-Schlüssel, damit sie nachgleiten, wenn eine Kategorie kollabiert
  // (z. B. „Gedruckt"-Kästchen verschwindet → darunter rückt alles nach oben).
  const oldLabelRects = new Map();
  content.querySelectorAll('.dd-cat-label[data-flip-id]').forEach(el => {
    oldLabelRects.set(el.dataset.flipId, el.getBoundingClientRect());
  });

  // Je eine Karten-HTML pro Drucker/Scanner, zunächst nach ID gemappt (nicht
  // direkt zusammengefügt) — die tatsächliche Spaltenreihenfolge kommt erst
  // unten aus `msg.card_order` (gemeinsame Drucker+Scanner-Reihenfolge, s.
  // AppState._ordered_display_items), damit Drucker- und Scanner-Karten
  // beliebig nebeneinander stehen können.
  const printerCardHtml = new Map();
  pool.forEach(p => {
    // Strukturierte Aufträge (mit Auftraggeber) aus p.orders; Status gruppiert
    // in die drei Kategorien. Fallback auf flache Namen-Felder, falls `orders`
    // fehlt (sollte nicht vorkommen — display_view reicht es immer durch).
    const orders = (Array.isArray(p.orders) ? p.orders : []).filter(
      o => !readyTravelJobIds.has(o.id)
    );
    // Beim Druckwechsel überlappt sich der Status kurz: der vorige Job ist im
    // OS-Poll noch „printing" (erst beim nächsten Poll „absent"/finalisiert),
    // während der nächste schon physisch druckt und ebenfalls „printing" wird.
    // Nur den ERSTEN „printing"-Job als „Wird gedruckt" zeigen; weitere
    // gleichzeitig „printing"-Jobs bleiben als „Nächster" sichtbar (s. nextOrds),
    // sodass ihr FLIP-Schlüssel (job_id) erhalten bleibt und sie bei der
    // Finalisierung des Vorgängers fließend von „Nächster" nach „Wird gedruckt"
    // gleiten — statt zu springen, weil sie zwischendrin aus dem DOM verschwunden
    // wären (kein alter Rect → kein FLIP → Sprung).
    const printingOrds = orders.filter(o => o.status === 'printing');
    const printingOrd = printingOrds[0] || null;
    // „Nächster": spooled + überlappende weitere printing-Jobs, in Slot-Reihen-
    // folge (orders ist FIFO nach Dispatch-Reihenfolge), damit die Positionen
    // und FLIP-Schlüssel stabil bleiben.
    const nextOrds = orders.filter(
      o => o.status === 'spooled' || (o.status === 'printing' && o !== printingOrd)
    );
    // Blockierte Aufträge (stalled/peer_error/failed) nur bei Fehler relevant:
    // sie wurden gesendet, der Schüler soll seinen Namen sehen und sich melden.
    const blockedOrds = p.faulty ? orders.filter(o => o.status === 'blocked') : [];
    const printed = p.printed_name || null;
    // Die drei Kategorien stehen immer (mit Label), auch ohne Eintrag — dann
    // halt leer. So bleibt das Layout pro Drucker stabil. FLIP-Schlüssel je
    // Box = job_id (stabil über Behälterwechsel).
    const finishedFeedback = finishedFeedbackFor(p);
    const printedBox = printed
      ? orderBoxFromRaw(
        p.printed_job_id || `printed::${p.id}::${printed}`,
        printed,
        finishedFeedback.className,
        p.printed_originator,
        finishedFeedback.inlineStyle,
      )
      : '';
    // Gelber Doppel-Scan-Blitzer (s. markFlaggedJob) kann auf jede der drei
    // Kategorien treffen, je nachdem wo der Auftrag gerade steht.
    const printingFlag = printingOrd ? flaggedFeedbackFor(printingOrd.id) : null;
    const printingBox = printingOrd
      ? orderBoxFromRaw(
        printingOrd.id, printingOrd.name, printingFlag.className, printingOrd.originator,
        printingFlag.inlineStyle,
      )
      : '';
    const nextBoxes = nextOrds.map(o => {
      const flag = flaggedFeedbackFor(o.id);
      return orderBoxFromRaw(o.id, o.name, flag.className, o.originator, flag.inlineStyle);
    }).join('')
      + blockedOrds.map(o => orderBoxFromRaw(o.id, o.name, 'dd-order-blocked', o.originator)).join('');
    // Bei Fehler: Name + „ - Fehler" in rot, gleicher Schriftgröße wie der Name;
    // darunter der Betreuer-Hinweis. Die Kategorien (Aufträge) bleiben sichtbar.
    const faulty = !!p.faulty;
    // Vorrang-Hinweis: im Schülerauftrag-Modus (msg.students_only, s.
    // `AppState._printer_display_students_only`) steckt in der „Nächster"-
    // Kategorie ein Host-/Helferauftrag — der ist in der zentralen
    // Warteschlange oben ausgeblendet, druckt aber real vorgezogen. Fehler
    // hat Vorrang vor diesem Hinweis (beides gleichzeitig zeigen wäre
    // widersprüchlich).
    const vorrang = !faulty && !!msg.students_only && nextOrds.some(
      o => o.originator && (o.originator.type === 'host' || o.originator.type === 'helper')
    );
    const nameSuffix = faulty ? ' - Fehler' : (vorrang ? ' - Vorrang' : '');
    const faultMsg = faulty
      ? `<div class="dd-fault-msg">Es scheint ein Fehler vorzuliegen. Bitte melde dich beim Betreuer.</div>`
      : vorrang
        ? `<div class="dd-priority-msg">Ein Betreuer druckt etwas. Dieser Druckauftrag hat Vorrang.</div>`
        : '';
    const nameClass = faulty ? ' dd-fault-name' : (vorrang ? ' dd-priority-name' : '');
    printerCardHtml.set(p.id, `<div class="printer-card" data-flip-id="${escapeHtml(p.id)}" data-printer="${escapeHtml(p.id)}">
      <div class="printer-name${nameClass}">${escapeHtml(printerLabel(p))}${nameSuffix}</div>
      ${faultMsg}
      <div class="dd-cat dd-printed" data-printed-for="${escapeHtml(p.id)}">
        <div class="dd-cat-label" data-flip-id="${escapeHtml(p.id)}::printed">Gedruckt</div>
        ${printedBox}
      </div>
      <div class="dd-cat dd-printing">
        <div class="dd-cat-label" data-flip-id="${escapeHtml(p.id)}::printing">Wird gedruckt</div>
        ${printingBox}
      </div>
      <div class="dd-cat dd-next">
        <div class="dd-cat-label" data-flip-id="${escapeHtml(p.id)}::next">Nächster</div>
        ${nextBoxes}
      </div>
    </div>`);
  });

  const scannerCardHtml = new Map();
  scanners.forEach(s => { scannerCardHtml.set(s.scanner_id, scanCardHtml(s)); });

  // Kartenreihenfolge: die vom Host gewählte gemeinsame Drucker+Scanner-
  // Reihenfolge (`msg.card_order`, s. AppState._ordered_display_items) —
  // Drucker- und Scanner-Karten können darin beliebig nebeneinander stehen.
  // Fällt (ältere/gecachte Nachrichten ohne das Feld) auf die natürliche
  // Reihenfolge zurück: erst alle Drucker, dann alle Scanner.
  const cardOrder = Array.isArray(msg.card_order) && msg.card_order.length
    ? msg.card_order
    : [...pool.map(p => `printer:${p.id}`), ...scanners.map(s => `scanner:${s.scanner_id}`)];
  const rows = cardOrder.map(key => {
    const sep = key.indexOf(':');
    const kind = key.slice(0, sep);
    const id = key.slice(sep + 1);
    if (kind === 'printer') return printerCardHtml.get(id) || '';
    if (kind === 'scanner') return scannerCardHtml.get(id) || '';
    return '';
  }).join('');

  // Allgemeine Warteschlange (zentrale Queue) unter den Druckern: nur Aufträge,
  // die für die oben gezeigten Drucker freigegeben sind (serverseitig via
  // display_view gefiltert). Einträge als Klasse + Name-Kästchen wie die
  // Druckeraufträge; FLIP-Schlüssel = job_id (gleich wie in den Druckerkarten),
  // sodass ein Auftrag beim Dispatch fließend von der Warteschlange in den
  // Drucker fährt (und dabei schrumpft, weil die Drucker-Spalte schmaler ist).
  // `w.student` (ohne Klasse, slip_name bekommt form=None) + `w.form` getrennt
  // an orderBox — die Klasse steht damit zuverlässig in der eigenen Spalte.
  const waiting = (Array.isArray(msg.waiting_list) ? msg.waiting_list : []).filter(
    w => !readyTravelJobIds.has(w.job_id)
  );
  const waitingRows = waiting.map(w => {
    const flag = flaggedFeedbackFor(w.job_id);
    return orderBox(
      w.job_id || `queue::${w.student}`, w.student || '', w.form || '', flag.className,
      w.originator_info, flag.inlineStyle,
    );
  }).join('');
  // Leere Warteschlange: kein „(0)" im Label und kein Hinweistext darunter —
  // die Karte zeigt nur das Label. Nicht leer: „Warteschlange (N)" + Namen.
  const waitingLabel = waiting.length ? `Warteschlange (${waiting.length})` : 'Warteschlange';
  const waitingCard = `<div class="dd-waiting-card" data-flip-id="__queue__">
    <div class="dd-cat-label" data-flip-id="__queue__::label">${waitingLabel}</div>
    ${waitingRows}
  </div>`;

  const colCount = pool.length + scanners.length;

  content.innerHTML = `<div class="dd-layout">
    <div class="grid" style="grid-template-columns:repeat(${colCount},minmax(0,1fr))">${rows}</div>
    ${waitingCard}
  </div>`;

  // Überlauf der Warteschlangen-Karte: zu viele Namen → der weiße Kasten endet
  // rechtzeitig, überlappende Namen werden transparent ausgeblendet (nur die
  // Warteschlangen-Karte, nicht die Drucker-Karten). Vor dem FLIP anwenden,
  // damit die Höhenkappe während der Animation bereits steht.
  applyWaitingOverflow();

  // Reise-Seed (s. revertScannerAndFlip): die zuletzt gemessene Position des
  // Namens-Kästchens an der Scanner-Karte wird als „alte" Position desselben
  // Auftrags (job_id) eingetragen — dieser Auftrag existiert jetzt zum ersten
  // Mal an seiner echten Stelle (Warteschlange/Drucker) im DOM, aber
  // `flipFromOldRects` sieht dank des Seeds trotzdem eine Bewegung statt eines
  // Neu-Erscheinens (kein neues Kästchen, s. Modul-Kommentar oben).
  if (seed && seed.jobId && seed.rect) {
    oldRects.set(seed.jobId, seed.rect);
  }

  // FLIP: Karten, Auftrags-Kästchen UND Kategorie-Labels von ihren alten an
  // ihre neuen Positionen gleiten lassen. Innere Elemente (Kästchen, Labels)
  // werden relativ zur jeweiligen Karte gerechnet (Karten-Delta abgezogen),
  // damit der Karten-FLIP nicht doppelt greift (s. flipFromOldRects).
  flipFromOldRects(oldCardRects, oldRects, oldLabelRects);

  // Pro „Gedruckt"-Block einen Timer setzen, der ihn nach der Rest-TTL
  // ausblendet (falls kein neuer Snapshot vorher nachzieht). Das Kästchen
  // verschwindet ohne Animation, der Rest gleitet per FLIP nach — läuft der
  // Timer während einer Animation, wird die Entfernung aufgeschoben
  // (s. removePrintedAndFlip / flushPendingQueue).
  pool.forEach(p => {
    if (!p.printed_name || !p.printed_expires_in) return;
    const pid = p.id;
    const ms = Math.max(0, p.printed_expires_in) * 1000;
    const t = setTimeout(() => {
      if (flipAnimating) { pendingTtlPid = pid; return; }
      removePrintedAndFlip(pid);
    }, ms);
    printedTimers.push(t);
  });

  // Ebenso pro Scanner-Karte mit aktivem (nicht-Default-)Status: nach Ablauf
  // der 10s zurückfallen (s. revertScannerAndFlip) bzw. „reisen", falls kein
  // neuer Scan vorher nachzieht. `revertScannerAndFlip` geht selbst über
  // `scheduleQueueRender` — eine laufende Animation wird darüber bereits
  // koalesziert, kein eigener flipAnimating-Check hier nötig.
  scanners.forEach(s => {
    if (s.status == null || s.expires_in == null) return;
    const sid = s.scanner_id;
    const ms = Math.max(0, s.expires_in) * 1000;
    const t = setTimeout(() => revertScannerAndFlip(sid), ms);
    printedTimers.push(t);
  });
}

// Initial-Theme folgt der System-/Browser-Einstellung des Geräts, auf dem das
// Display geöffnet wird — bis der Host es explizit überschreibt (applyTheme).
function applySystemTheme() {
  const light = matchMedia('(prefers-color-scheme: light)').matches;
  document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
}

function applyTheme(theme) {
  // Theme vom Host ('light' | 'dark') überschreibt die System-Einstellung.
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
}

function applyLabel(label) {
  // Display-Name unten links (leer = nichts anzeigen).
  const el = document.getElementById('dd-name');
  if (el) el.textContent = (label && label.trim()) ? label : '';
}

// Queue-Snapshot geordnet anwenden: während einer FLIP-Animation (FLIP_MS)
// eintreffende Snapshots poolen und nach Ablauf einmalig (als aktuellster)
// nachziehen. Siehe Block am Dateianfang (Snapshot-Koalescing). `seed` (s.
// revertScannerAndFlip) reicht eine zusätzliche „alte Position" für einen
// Reise-Übergang durch, der sonst kein passendes altes Rect im DOM hätte.
function scheduleQueueRender(msg, seed) {
  show('queue');
  if (flipAnimating) {
    pendingQueueMsg = msg;
    if (seed) pendingQueueSeed = seed;
    return;
  }
  renderQueue(msg, seed);
  flipAnimating = true;
  // Bei reduzierter Bewegung ist die FLIP-Animation instant (.01ms) — dann
  // nur kurzes Pooling gegen Flackern, kein 500ms-Lag.
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  setTimeout(flushPendingQueue, reduceMotion ? 80 : FLIP_MS);
}

// „Gedruckt"-Kästchen nach TTL verschwinden lassen: das Kästchen selbst ohne
// Animation entfernen, aber den Rest (z. B. den Warteschlangen-Kasten
// darunter) per FLIP gleiten lassen — nicht springen. Gezielt (ohne vollen
// Re-Render), damit andere Drucker-TTL-Timer nicht zurückgesetzt werden.
function removePrintedAndFlip(pid) {
  const card = content.querySelector(`.printer-card[data-printer="${CSS.escape(pid)}"]`);
  if (!card) return;
  // Alte Positionen JE Element merken (Karten, Auftrags-Kästchen, Labels),
  // BEVOR das „Gedruckt"-Kästchen entfernt wird — so gleitet danach alles
  // (Label „Wird gedruckt"/„Nächster", weitere Kästchen, die Warteschlangen-
  // Karte) an seine neue Position statt zu springen.
  const oldCardRects = new Map();
  content.querySelectorAll('.printer-card, .dd-waiting-card').forEach(el => {
    oldCardRects.set(el.dataset.flipId, el.getBoundingClientRect());
  });
  const oldOrderRects = new Map();
  content.querySelectorAll('.dd-order[data-order-key]').forEach(el => {
    oldOrderRects.set(el.dataset.orderKey, el.getBoundingClientRect());
  });
  const oldLabelRects = new Map();
  content.querySelectorAll('.dd-cat-label[data-flip-id]').forEach(el => {
    oldLabelRects.set(el.dataset.flipId, el.getBoundingClientRect());
  });
  // Nur das Auftrags-Kästchen entfernen — das Label „Gedruckt" bleibt stehen.
  // Das Verschwinden selbst wird nicht animiert (Nutzer-Vorgabe), der Rest
  // gleitet per flipFromOldRects nach.
  card.querySelector('.dd-printed .dd-order')?.remove();
  flipFromOldRects(oldCardRects, oldOrderRects, oldLabelRects);
  flipAnimating = true;
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  setTimeout(flushPendingQueue, reduceMotion ? 80 : FLIP_MS);
}

function flushPendingQueue() {
  flipAnimating = false;
  // Nach Ablauf der FLIP-Animation liegt die Warteschlangen-Karte an ihrer
  // neuen Layoutposition (transform wieder 0) — Maske neu berechnen, falls
  // sich z. B. das Grid darüber verkleinert/größert hat (s. removePrintedAndFlip).
  applyWaitingOverflow();
  if (pendingQueueMsg) {
    // Ein frischer Snapshot bringt aktuellen Stand — eine aufgeschobene TTL-
    // Entfernung ist damit hinfällig (das Kästchen ist im Snapshot eh weg).
    pendingTtlPid = null;
    const m = pendingQueueMsg; pendingQueueMsg = null;
    const s = pendingQueueSeed; pendingQueueSeed = null;
    scheduleQueueRender(m, s);
  } else if (pendingTtlPid) {
    const pid = pendingTtlPid; pendingTtlPid = null;
    removePrintedAndFlip(pid);
  }
}

function handleServerMessage(msg) {
  if (msg.type === 'forbidden') {
    // Vom Betreuer gesperrt (× am Host). Kein Reconnect — der Token bleibt
    // verboten, auch ein Reload liefert wieder „gesperrt". Eine eventuell
    // wartende Queue-Animation verwerfen (das Display zeigt nicht mehr queue).
    forbidden = true;
    pendingQueueMsg = null;
    show('forbidden');
    return;
  }
  // Theme nur anwenden, wenn der Host es explizit gesetzt hat (Override der
  // System-Einstellung); ohne theme-Key folgt das Display weiterhin dem Gerät.
  if ('theme' in msg) applyTheme(msg.theme);
  if ('label' in msg) applyLabel(msg.label);
  if (msg.type === 'registration') {
    document.getElementById('reg-code').textContent = msg.code || '····';
    show('register');
  } else if (msg.type === 'queue') {
    scheduleQueueRender(msg);
  }
}

// Token aus der URL (vom Server per Redirect zugewiesen). Ohne Token würde die
// Seite gar nicht erst ausgeliefert (der Server leitet immer auf ?token=… weiter).
const token = new URLSearchParams(location.search).get('token') || '';

applySystemTheme();

// Beim Entladen der Seite (Navigation auf einen neuen Token via /drucker-display
// oder Tab-Schließen) den Server AKTIV benachrichtigen, damit die alte Session
// sofort als getrennt erkannt wird (grauer Punkt, falls autorisiert; entfernt,
// falls nicht) — wie beim Tab-Schließen, nur dass der Close-Frame bei Navigation
// unzuverlässig ankommt. Zwei Hebel:
//  (1) ``navigator.sendBeacon`` auf /api/drucker-display/departed — sendBeacon ist
//      genau für „Server beim Entladen zuverlässig benachrichtigen" gemacht und
//      kommt auch beim Navigieren/Redirect verlässlich an. Der Server schließt
//      daraufhin die WS und räumt auf.
//  (2) zusätzlich die WS sauber mit Code 1001 schließen (Best-Effort) + das
//      ``unloading``-Flag unterdrückt den Auto-Reconnect, damit die alte Session
//      nicht wiederbelebt wird.
// Andere Tabs auf demselben Gerät (weitere Displays) sind unberührt — jeweils nur
// die geschlossene/navigierte Seite gibt ihre WS auf.
let unloading = false;
let unloadNotified = false;
let currentSocket = null;
function onUnload() {
  unloading = true;
  try {
    if (currentSocket && currentSocket.readyState === WebSocket.OPEN) {
      currentSocket.close(1001, 'page unload');
    }
  } catch (_) { /* no-op — Seite wird ohnehin entladen */ }
  // Beacon nur einmal senden (pagehide + beforeunload können beide feuern).
  if (!unloadNotified && navigator.sendBeacon) {
    unloadNotified = true;
    try {
      navigator.sendBeacon(
        `/api/drucker-display/departed?token=${encodeURIComponent(token)}`,
      );
    } catch (_) { /* no-op — Fallback ist der uvicorn-Ping */ }
  }
}
window.addEventListener('pagehide', onUnload);
window.addEventListener('beforeunload', onUnload);

connectWebSocket(() => `wss://${location.host}/ws/drucker-display?token=${encodeURIComponent(token)}`, {
  onSocket: (ws) => { currentSocket = ws; },
  onOpen: () => { connDot.style.background = '#30d158'; connText.textContent = 'verbunden'; },
  onClose: (e, reconnect) => {
    connDot.style.background = '#ff6b6b';
    connText.textContent = 'getrennt — neu verbinden…';
    // Gesperrte (forbidden) und entladene (unloading) Displays versuchen keinen
    // Reconnect — sonst würde die alte Session wiederbelebt und bliebe am Host
    // fälschlich grün, obwohl die Seite längst weg/navigiert ist.
    if (!forbidden && !unloading) reconnect();
  },
  onError: () => { connDot.style.background = '#ff6b6b'; connText.textContent = 'Verbindungsfehler'; },
  onMessage: e => {
    let msg; try { msg = JSON.parse(e.data); } catch (_) { return; }
    handleServerMessage(msg);
  },
});

show('register');

// Bei Resize die Überlauf-Ausblendung der Warteschlangen-Karte neu berechnen
// (debounced — nicht pro Resize-Event feuren).
window.addEventListener('resize', () => {
  if (waitingOverflowTimer) clearTimeout(waitingOverflowTimer);
  waitingOverflowTimer = setTimeout(applyWaitingOverflow, 100);
});
