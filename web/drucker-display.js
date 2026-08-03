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
// in fester Spaltenbreite (bündige Namen), Auftraggeber rechts. data-order-key
// (pro Drucker eindeutig) treibt die FLIP-Animation.
function orderBox(pid, raw, extraClass, originator) {
  const { name, form } = parseOrder(raw);
  const key = `${pid}::${raw}`;
  const cls = extraClass ? ` ${extraClass}` : '';
  return `<div class="dd-order${cls}" data-order-key="${escapeHtml(key)}">`
    + `<span class="dd-form">${escapeHtml(form)}</span>`
    + `<span class="dd-stname">${escapeHtml(name)}</span>`
    + `<span class="dd-origin">${originatorHtml(originator)}</span>`
    + `</div>`;
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

  // FLIP-Vorbereitung: alte Positionen je Auftrag merken, BEVOR innerHTML
  // ausgetauscht wird — so fahren Aufträge, die die Kategorie wechseln (z. B.
  // Nächster → Wird gedruckt → Gedruckt), an ihre neue Position, statt zu
  // springen. Spiegel der Bücherliste im Helferclient (scan-render.js).
  const oldRects = new Map();
  content.querySelectorAll('.dd-order[data-order-key]').forEach(el => {
    oldRects.set(el.dataset.orderKey, el.getBoundingClientRect());
  });

  const rows = pool.map(p => {
    // Strukturierte Aufträge (mit Auftraggeber) aus p.orders; Status gruppiert
    // in die drei Kategorien. Fallback auf flache Namen-Felder, falls `orders`
    // fehlt (sollte nicht vorkommen — display_view reicht es immer durch).
    const orders = Array.isArray(p.orders) ? p.orders : [];
    const printingOrd = orders.find(o => o.status === 'printing') || null;
    const spooledOrds = orders.filter(o => o.status === 'spooled');
    // Blockierte Aufträge (stalled/peer_error/failed) nur bei Fehler relevant:
    // sie wurden gesendet, der Schüler soll seinen Namen sehen und sich melden.
    const blockedOrds = p.faulty ? orders.filter(o => o.status === 'blocked') : [];
    const printed = p.printed_name || null;
    // Die drei Kategorien stehen immer (mit Label), auch ohne Eintrag — dann
    // halt leer. So bleibt das Layout pro Drucker stabil.
    const printedBox = printed ? orderBox(p.id, printed, '', p.printed_originator) : '';
    const printingBox = printingOrd ? orderBox(p.id, printingOrd.name, '', printingOrd.originator) : '';
    const nextBoxes = spooledOrds.map(o => orderBox(p.id, o.name, '', o.originator)).join('')
      + blockedOrds.map(o => orderBox(p.id, o.name, 'dd-order-blocked', o.originator)).join('');
    // Bei Fehler: Name + „ - Fehler" in rot, gleicher Schriftgröße wie der Name;
    // darunter der Betreuer-Hinweis. Die Kategorien (Aufträge) bleiben sichtbar.
    const faulty = !!p.faulty;
    const nameSuffix = faulty ? ' - Fehler' : '';
    const faultMsg = faulty
      ? `<div class="dd-fault-msg">Es scheint ein Fehler vorzuliegen. Bitte melde dich beim Betreuer.</div>`
      : '';
    return `<div class="printer-card" data-printer="${escapeHtml(p.id)}">
      <div class="printer-name${faulty ? ' dd-fault-name' : ''}">${escapeHtml(printerLabel(p))}${nameSuffix}</div>
      ${faultMsg}
      <div class="dd-cat dd-printed" data-printed-for="${escapeHtml(p.id)}">
        <div class="dd-cat-label">Gedruckt</div>
        ${printedBox}
      </div>
      <div class="dd-cat dd-printing">
        <div class="dd-cat-label">Wird gedruckt</div>
        ${printingBox}
      </div>
      <div class="dd-cat dd-next">
        <div class="dd-cat-label">Nächster</div>
        ${nextBoxes}
      </div>
    </div>`;
  }).join('');

  // Allgemeine Warteschlange (zentrale Queue) unter den Druckern: nur Aufträge,
  // die für die oben gezeigten Drucker freigegeben sind (serverseitig via
  // display_view gefiltert). Einträge als Klasse + Name-Kästchen wie die
  // Druckeraufträge (gleicher FLIP-Schlüssel-Raum, Präfix „queue::").
  const waiting = Array.isArray(msg.waiting_list) ? msg.waiting_list : [];
  const waitingRows = waiting.map(w => orderBox('queue', w.student, '', w.originator_info)).join('');
  const waitingCard = `<div class="dd-waiting-card">
    <div class="dd-cat-label">Warteschlange (${waiting.length})</div>
    ${waitingRows || '<p class="hint">Keine Aufträge in der Warteschlange.</p>'}
  </div>`;

  content.innerHTML = `<div class="dd-layout">
    <div class="grid" style="grid-template-columns:repeat(${pool.length},minmax(0,1fr))">${rows}</div>
    ${waitingCard}
  </div>`;

  // FLIP-Animation: jedes Kästchen, das schon da war, startet an seiner alten
  // Position (translate) und fährt zur neuen (translate→0). Neue Kästchen
  // (kein alter Eintrag) erscheinen sofort.
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  content.querySelectorAll('.dd-order[data-order-key]').forEach(el => {
    const old = oldRects.get(el.dataset.orderKey);
    if (!old) return;
    const cur = el.getBoundingClientRect();
    const dx = old.left - cur.left;
    const dy = old.top - cur.top;
    if (!dx && !dy) return;
    el.style.transition = 'none';
    el.style.transform = `translate(${dx}px, ${dy}px)`;
    el.offsetWidth;  // Reflow erzwingen, damit die Startposition greift
    el.style.transition = reduceMotion ? 'transform .01ms' : '';
    el.style.transform = '';
  });

  // Pro „Gedruckt"-Block einen Timer setzen, der ihn nach der Rest-TTL
  // ausblendet (falls kein neuer Snapshot vorher nachzieht).
  pool.forEach(p => {
    if (!p.printed_name || !p.printed_expires_in) return;
    const pid = p.id;
    const ms = Math.max(0, p.printed_expires_in) * 1000;
    const t = setTimeout(() => {
      const card = content.querySelector(`.printer-card[data-printer="${CSS.escape(pid)}"]`);
      // Nur das Auftrags-Kästchen entfernen — das Label „Gedruckt" bleibt stehen.
      card?.querySelector('.dd-printed .dd-order')?.remove();
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

function handleServerMessage(msg) {
  if (msg.type === 'forbidden') {
    // Vom Betreuer gesperrt (× am Host). Kein Reconnect — der Token bleibt
    // verboten, auch ein Reload liefert wieder „gesperrt".
    forbidden = true;
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
    renderQueue(msg);
    show('queue');
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