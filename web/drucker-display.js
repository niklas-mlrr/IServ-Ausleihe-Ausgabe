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

function show(name) {
  viewRegister.classList.toggle('show', name === 'register');
  viewQueue.classList.toggle('show', name === 'queue');
}

// Anzeige-Label eines Druckers: „<Label> (<Systemname>)" bzw. Systemname oder
// „Standarddrucker" — konsistent mit dem Host (host-render.js printerLabel /
// print_queue._printer_display).
function printerLabel(p) {
  const sys = p.is_default || !p.name ? 'Standarddrucker' : p.name;
  return p.label && p.label.trim() ? `${p.label} (${sys})` : sys;
}

function renderQueue(msg) {
  const pool = Array.isArray(msg.printers) ? msg.printers : [];
  const waitingList = Array.isArray(msg.waiting_list) ? msg.waiting_list : [];
  const waiting = msg.waiting || 0;

  if (!pool.length) {
    content.innerHTML =
      '<p class="hint">Kein Drucker zugewiesen — am Host Druckerkapazitäten für dieses Display einstellen.</p>';
    return;
  }

  const rows = pool.map(p => {
    const printing = p.printing_name;
    const spooledList = Array.isArray(p.spooled_names) && p.spooled_names.length
      ? p.spooled_names
      : (p.spooled_name ? [p.spooled_name] : []);
    const spooledNames = spooledList.map(n => `„${escapeHtml(n)}"`).join(', ');
    let dot, status;
    if (p.faulty) {
      dot = 'fault';
      status = `<span class="txt-danger">⚠ fehlerhaft</span>` + (p.load > 0 ? ` — ${p.load} blockiert` : '');
    } else if (printing && spooledNames) {
      dot = 'busy'; status = `druckt „${escapeHtml(printing)}" · als nächstes ${spooledNames}`;
    } else if (printing) {
      dot = 'busy'; status = `druckt „${escapeHtml(printing)}"`;
    } else if (spooledNames) {
      dot = 'busy'; status = `gesendet, wartet auf Druck: ${spooledNames}`;
    } else if (p.load > 0) {
      dot = 'busy'; status = `${p.load} blockiert (wird geräumt)`;
    } else {
      dot = 'idle'; status = 'bereit';
    }
    return `<div class="printer-card">
      <div class="printer-name">${escapeHtml(printerLabel(p))}</div>
      <div class="printer-status"><span class="dot ${dot}"></span>${status}</div>
    </div>`;
  }).join('');

  const waitRows = waitingList.map(w => {
    const printers = w.all_allowed
      ? 'alle'
      : (w.allowed_printers && w.allowed_printers.length
          ? w.allowed_printers.map(escapeHtml).join(', ')
          : '<span class="txt-danger">kein Drucker im Pool</span>');
    return `<tr>
      <td class="pq-pos">${w.position}</td>
      <td>${escapeHtml(w.student || '–')}</td>
      <td>${w.form ? escapeHtml(w.form) : '–'}</td>
      <td>${escapeHtml(w.originator || '–')}</td>
      <td>${printers}</td>
    </tr>`;
  }).join('');

  const waitBlock = waitingList.length
    ? `<div class="section-label">Zentrale Warteschlange (${waiting})</div>
       <table>
         <thead><tr><th>#</th><th>Schüler</th><th>Klasse</th><th>Auftraggeber</th><th>Drucker</th></tr></thead>
         <tbody>${waitRows}</tbody>
       </table>`
    : '<div class="section-label">Zentrale Warteschlange</div><p class="hint">Zentrale Warteschlange leer.</p>';

  content.innerHTML = `<div class="section-label">Drucker (${pool.length})</div><div class="grid" style="grid-template-columns:repeat(${pool.length},minmax(0,1fr))">${rows}</div>${waitBlock}`;
}

function applyTheme(theme) {
  // Theme vom Host ('light' | 'dark'); Default dark (bisheriges Aussehen).
  document.documentElement.setAttribute('data-theme', theme === 'light' ? 'light' : 'dark');
}

function applyLabel(label) {
  // Display-Name als Queue-Überschrift (leer = Default „Druckerwarteschlange").
  const h1 = document.querySelector('#view-queue h1');
  if (h1) h1.textContent = (label && label.trim()) ? label : 'Druckerwarteschlange';
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