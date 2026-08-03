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
// Aufgeschobene „Gedruckt"-TTL-Entfernung: trifft der TTL-Timer während einer
// laufenden FLIP-Animation, wird die Entfernung zurückgestellt und nach
// Ablauf von flushPendingQueue nachgezogen (s. Nutzer-Vorgabe: beim
// Verschwinden soll der Rest gleiten, nicht springen — und keine Bewegung
// darf eine andere abbrechen).
let pendingTtlPid = null;

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
function orderBox(key, name, form, extraClass, originator) {
  const cls = extraClass ? ` ${extraClass}` : '';
  return `<div class="dd-order${cls}" data-order-key="${escapeHtml(key)}">`
    + `<span class="dd-form">${escapeHtml(form || '')}</span>`
    + `<span class="dd-stname">${escapeHtml(name)}</span>`
    + `<span class="dd-origin">${originatorHtml(originator)}</span>`
    + `</div>`;
}
// Variante für kombinierte Strings „Name (Form)" (Druckeraufträge, gedruckt):
// splittet per parseOrder und reicht die Teile an orderBox weiter.
function orderBoxFromRaw(key, raw, extraClass, originator) {
  const { name, form } = parseOrder(raw);
  return orderBox(key, name, form, extraClass, originator);
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

function renderQueue(msg) {
  const pool = Array.isArray(msg.printers) ? msg.printers : [];

  if (!pool.length) {
    content.innerHTML =
      '<p class="hint">Kein Drucker zugewiesen — am Host Druckerkapazitäten für dieses Display einstellen.</p>';
    return;
  }

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

  const rows = pool.map(p => {
    // Strukturierte Aufträge (mit Auftraggeber) aus p.orders; Status gruppiert
    // in die drei Kategorien. Fallback auf flache Namen-Felder, falls `orders`
    // fehlt (sollte nicht vorkommen — display_view reicht es immer durch).
    const orders = Array.isArray(p.orders) ? p.orders : [];
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
    const printedBox = printed
      ? orderBoxFromRaw(p.printed_job_id || `printed::${p.id}::${printed}`, printed, '', p.printed_originator)
      : '';
    const printingBox = printingOrd
      ? orderBoxFromRaw(printingOrd.id, printingOrd.name, '', printingOrd.originator) : '';
    const nextBoxes = nextOrds.map(o => orderBoxFromRaw(o.id, o.name, '', o.originator)).join('')
      + blockedOrds.map(o => orderBoxFromRaw(o.id, o.name, 'dd-order-blocked', o.originator)).join('');
    // Bei Fehler: Name + „ - Fehler" in rot, gleicher Schriftgröße wie der Name;
    // darunter der Betreuer-Hinweis. Die Kategorien (Aufträge) bleiben sichtbar.
    const faulty = !!p.faulty;
    const nameSuffix = faulty ? ' - Fehler' : '';
    const faultMsg = faulty
      ? `<div class="dd-fault-msg">Es scheint ein Fehler vorzuliegen. Bitte melde dich beim Betreuer.</div>`
      : '';
    return `<div class="printer-card" data-flip-id="${escapeHtml(p.id)}" data-printer="${escapeHtml(p.id)}">
      <div class="printer-name${faulty ? ' dd-fault-name' : ''}">${escapeHtml(printerLabel(p))}${nameSuffix}</div>
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
    </div>`;
  }).join('');

  // Allgemeine Warteschlange (zentrale Queue) unter den Druckern: nur Aufträge,
  // die für die oben gezeigten Drucker freigegeben sind (serverseitig via
  // display_view gefiltert). Einträge als Klasse + Name-Kästchen wie die
  // Druckeraufträge; FLIP-Schlüssel = job_id (gleich wie in den Druckerkarten),
  // sodass ein Auftrag beim Dispatch fließend von der Warteschlange in den
  // Drucker fährt (und dabei schrumpft, weil die Drucker-Spalte schmaler ist).
  // `w.student` (ohne Klasse, slip_name bekommt form=None) + `w.form` getrennt
  // an orderBox — die Klasse steht damit zuverlässig in der eigenen Spalte.
  const waiting = Array.isArray(msg.waiting_list) ? msg.waiting_list : [];
  const waitingRows = waiting.map(w =>
    orderBox(w.job_id || `queue::${w.student}`, w.student || '', w.form || '', '', w.originator_info)
  ).join('');
  // Leere Warteschlange: kein „(0)" im Label und kein Hinweistext darunter —
  // die Karte zeigt nur das Label. Nicht leer: „Warteschlange (N)" + Namen.
  const waitingLabel = waiting.length ? `Warteschlange (${waiting.length})` : 'Warteschlange';
  const waitingCard = `<div class="dd-waiting-card" data-flip-id="__queue__">
    <div class="dd-cat-label" data-flip-id="__queue__::label">${waitingLabel}</div>
    ${waitingRows}
  </div>`;

  content.innerHTML = `<div class="dd-layout">
    <div class="grid" style="grid-template-columns:repeat(${pool.length},minmax(0,1fr))">${rows}</div>
    ${waitingCard}
  </div>`;

  // Überlauf der Warteschlangen-Karte: zu viele Namen → der weiße Kasten endet
  // rechtzeitig, überlappende Namen werden transparent ausgeblendet (nur die
  // Warteschlangen-Karte, nicht die Drucker-Karten). Vor dem FLIP anwenden,
  // damit die Höhenkappe während der Animation bereits steht.
  applyWaitingOverflow();

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
// nachziehen. Siehe Block am Dateianfang (Snapshot-Koalescing).
function scheduleQueueRender(msg) {
  show('queue');
  if (flipAnimating) { pendingQueueMsg = msg; return; }
  renderQueue(msg);
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
    scheduleQueueRender(m);
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
connectWebSocket(() => `wss://${location.host}/ws/drucker-display?token=${encodeURIComponent(token)}`, {
  onOpen: () => { connDot.style.background = '#30d158'; connText.textContent = 'verbunden'; },
  onClose: (e, reconnect) => {
    connDot.style.background = '#ff6b6b';
    connText.textContent = 'getrennt — neu verbinden…';
    // Gesperrte Displays versuchen keinen Reconnect (Token bleibt verboten).
    if (!forbidden) reconnect();
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