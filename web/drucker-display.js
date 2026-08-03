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

  const rows = pool.map(p => {
    const spooledList = Array.isArray(p.spooled_names) && p.spooled_names
      ? p.spooled_names
      : (p.spooled_name ? [p.spooled_name] : []);
    const printing = p.printing_name || null;
    const printed = p.printed_name || null;
    // Kategorie „Gedruckt" — zuletzt fertig, nur wenn noch innerhalb der TTL.
    const printedBlock = printed
      ? `<div class="dd-cat dd-printed" data-printed-for="${escapeHtml(p.id)}">
           <div class="dd-cat-label">Gedruckt</div>
           <div class="dd-line">${escapeHtml(printed)}</div>
         </div>`
      : '';
    // Kategorie „Wird gedruckt" — der aktuell druckende Auftrag.
    const printingBlock = printing
      ? `<div class="dd-cat dd-printing">
           <div class="dd-cat-label">Wird gedruckt</div>
           <div class="dd-line">${escapeHtml(printing)}</div>
         </div>`
      : '';
    // Kategorie „Nächster" — bereits an den Drucker gesendet, wartet auf Druck.
    const nextLines = spooledList.map(n => `<div class="dd-line">${escapeHtml(n)}</div>`).join('');
    const nextBlock = nextLines
      ? `<div class="dd-cat dd-next">
           <div class="dd-cat-label">Nächster</div>
           ${nextLines}
         </div>`
      : '';
    // Fehler-Hinweis (bleibt sichtbar, falls der Drucker hängt).
    const faultBlock = p.faulty
      ? `<div class="dd-fault"><span class="txt-danger">⚠ fehlerhaft</span>${p.load > 0 ? ` — ${p.load} blockiert` : ''}</div>`
      : '';
    return `<div class="printer-card" data-printer="${escapeHtml(p.id)}">
      <div class="printer-name">${escapeHtml(printerLabel(p))}</div>
      ${faultBlock}
      ${printedBlock}
      ${printingBlock}
      ${nextBlock}
    </div>`;
  }).join('');

  content.innerHTML = `<div class="grid" style="grid-template-columns:repeat(${pool.length},minmax(0,1fr))">${rows}</div>`;

  // Pro „Gedruckt"-Block einen Timer setzen, der ihn nach der Rest-TTL
  // ausblendet (falls kein neuer Snapshot vorher nachzieht).
  pool.forEach(p => {
    if (!p.printed_name || !p.printed_expires_in) return;
    const pid = p.id;
    const ms = Math.max(0, p.printed_expires_in) * 1000;
    const t = setTimeout(() => {
      const card = content.querySelector(`.printer-card[data-printer="${CSS.escape(pid)}"]`);
      card?.querySelector('.dd-printed')?.remove();
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