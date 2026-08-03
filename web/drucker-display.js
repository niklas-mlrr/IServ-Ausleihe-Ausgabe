// Drucker-Display (`/drucker-display`) — reine Anzeige für einen Bildschirm
// neben den Druckern. Verbindet sich unauthentifiziert via `/ws/drucker-display`
// (s. server/routes/ws.py). Vor dem Host-Pairing zeigt es NUR die Registrierungs-
// Nummer; danach die vom Host zugewiesenen Drucker + die gefilterte zentrale
// Warteschlange. Live via WebSocket-Push (kein Polling).

const viewRegister = document.getElementById('view-register');
const viewQueue = document.getElementById('view-queue');
const connDot = document.getElementById('conn-dot');
const connText = document.getElementById('conn-text');
const content = document.getElementById('dd-content');
// Laufende TTL-Timer, die „Gedruckt"-Kategorien nach 30s ausblenden.
let printedTimers = [];

function show(name) {
  viewRegister.classList.toggle('show', name === 'register');
  viewQueue.classList.toggle('show', name === 'queue');
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

// Ein Auftrag als Kästchen: [Klasse] [Name, Vorname] — Klasse in fester
// Spaltenbreite, damit die Namen über alle Kästchen bündig untereinander
// stehen. data-order-key (pro Drucker eindeutig) treibt die FLIP-Animation.
function orderBox(pid, raw, extraClass) {
  const { name, form } = parseOrder(raw);
  const key = `${pid}::${raw}`;
  const cls = extraClass ? ` ${extraClass}` : '';
  return `<div class="dd-order${cls}" data-order-key="${escapeHtml(key)}">`
    + `<span class="dd-form">${escapeHtml(form)}</span>`
    + `<span class="dd-stname">${escapeHtml(name)}</span>`
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
    const spooledList = Array.isArray(p.spooled_names) && p.spooled_names
      ? p.spooled_names
      : (p.spooled_name ? [p.spooled_name] : []);
    // Blockierte Aufträge (stalled/peer_error/failed) nur bei Fehler relevant:
    // sie wurden gesendet, der Schüler soll seinen Namen sehen und sich melden.
    const blockedList = p.faulty && Array.isArray(p.blocked_names) ? p.blocked_names : [];
    const printing = p.printing_name || null;
    const printed = p.printed_name || null;
    // Die drei Kategorien stehen immer (mit Label), auch ohne Eintrag — dann
    // halt leer. So bleibt das Layout pro Drucker stabil.
    const printedBox = printed ? orderBox(p.id, printed, 'dd-order-printed') : '';
    const printingBox = printing ? orderBox(p.id, printing) : '';
    const nextBoxes = spooledList.map(n => orderBox(p.id, n)).join('')
      + blockedList.map(n => orderBox(p.id, n, 'dd-order-blocked')).join('');
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

  content.innerHTML = `<div class="grid" style="grid-template-columns:repeat(${pool.length},minmax(0,1fr))">${rows}</div>`;

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

function applyTheme(theme) {
  // Theme vom Host ('light' | 'dark'); Default dark (bisheriges Aussehen).
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
}

function applyLabel(label) {
  // Display-Name unten links (leer = nichts anzeigen).
  const el = document.getElementById('dd-name');
  if (el) el.textContent = (label && label.trim()) ? label : '';
}

function handleServerMessage(msg) {
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

connectWebSocket(() => `wss://${location.host}/ws/drucker-display`, {
  onOpen: () => { connDot.style.background = '#30d158'; connText.textContent = 'verbunden'; },
  onClose: (e, reconnect) => {
    connDot.style.background = '#ff6b6b';
    connText.textContent = 'getrennt — neu verbinden…';
    reconnect();
  },
  onError: () => { connDot.style.background = '#ff6b6b'; connText.textContent = 'Verbindungsfehler'; },
  onMessage: e => {
    let msg; try { msg = JSON.parse(e.data); } catch (_) { return; }
    handleServerMessage(msg);
  },
});

show('register');