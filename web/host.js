  let ws = null;
  let state = { active_context_id: null, contexts: {}, queue: [], active_form: null, helpers: {}, modus_b: { open: false, pending: [], pending_count: 0, displays: [], join_url: null }, selected_schoolyear: null };
  let mbQrDataUrl = null;
  // QR-Modal-Beobachter: schließt das Popup automatisch, sobald der gezeigte QR gescannt wurde.
  // { kind: 'student'|'display', baseline: <Zählerstand beim Öffnen> }
  let qrWatch = null;
  let armedStudentId = null;  // Schüler, der per "Pairing"-Button scharfgestellt ist (Code-Klick ordnet zu)
  let studentAlerts = {};  // student_id -> {text} — ausgemustert/verliehen-Meldung fürs Now-Serving-Kästchen
  let prevPendingCount = null;  // letzter mb.pending_count — Anstieg => neuer Code (Beep+Blink)

  // ---- Tab-Modell ----
  // activeTab: 'host' | 'new' | <context_id> — rein pro Bediener/Browser
  // (welcher Reiter gerade fokussiert ist), NICHT global, nicht persistiert.
  // tabOrder: Reihenfolge der Klassen-Reiter (nur context_ids). Wird global aus
  // dem Server-State (`state.contexts`, Einfügereihenfolge) abgeleitet — die
  // offenen Klassen sind auf jedem angemeldeten Host-Rechner sichtbar. Inhalte
  // leben ohnehin serverseitig im Speicher.
  let tabOrder = [];
  let activeTab = 'host';
  let classList = [];                 // Klassen-Liste aus /api/classes (für Wähler + Single-Selects)
  let ctxSingleStudents = {};         // context_id -> [students] für den Einzelne-Schüler-Select
  // SVG-Icons für die Queue-Steuer-Buttons (pro Klassen-Tab neu gerendert).
  const ICO_RESET = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';
  const ICO_CLEAR = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  const ICO_DISC  = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/></svg>';
  const ICO_HELPER = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';

  // Schüler über alle Kontexte finden (student_id ist schulweit eindeutig).
  function findStudentInState(studentId) {
    for (const id of Object.keys(state.contexts || {})) {
      const s = (state.contexts[id].queue || []).find(q => q.student_id === studentId);
      if (s) return s;
    }
    return null;
  }
  function findCtxOfStudent(studentId) {
    for (const id of Object.keys(state.contexts || {})) {
      if ((state.contexts[id].queue || []).some(q => q.student_id === studentId)) return id;
    }
    return null;
  }

  // ---- Audio (Beep bei neuem Pairing-Code) — Muster aus web/scan.html ----
  let audioCtx = null, audioBuffer = null;
  async function initAudio() {
    if (audioBuffer) return;
    try {
      audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      // Stiller Buffer entsperrt den AudioContext während der Nutzergeste (iOS/Safari)
      const silence = audioCtx.createBuffer(1, 1, audioCtx.sampleRate);
      const silSrc = audioCtx.createBufferSource();
      silSrc.buffer = silence; silSrc.connect(audioCtx.destination); silSrc.start(0);
      await audioCtx.resume();
      const resp = await fetch('/beep.mp3');
      audioBuffer = await audioCtx.decodeAudioData(await resp.arrayBuffer());
    } catch (e) { /* Audio optional — Blink bleibt als visuelles Signal */ }
  }
  function playBeep() {
    if (!audioCtx || !audioBuffer) return;
    const src = audioCtx.createBufferSource();
    src.buffer = audioBuffer; src.connect(audioCtx.destination); src.start(0);
  }
  // Modus-B-Karte kurz aufblinken lassen (Klasse entfernt sich nach der Animation selbst)
  function flashModusB() {
    const card = document.getElementById('mb-status').closest('.card');
    if (!card) return;
    card.classList.remove('flash');
    void card.offsetWidth;  // Reflow erzwingen, damit die Animation neu startet
    card.classList.add('flash');
    card.addEventListener('animationend', () => card.classList.remove('flash'), { once: true });
  }

  // ---- Theme: System / Hell / Dunkel (manuelle Wahl überschreibt prefers-color-scheme) ----
  const THEME_CYCLE = { '': 'light', 'light': 'dark', 'dark': '' };
  const ICON_SUN = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>';
  const ICON_MOON = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>';
  const THEME_LABEL = { '': 'Auto', 'light': ICON_SUN + ' Hell', 'dark': ICON_MOON + ' Dunkel' };
  function applyTheme(t) {
    if (t) document.documentElement.setAttribute('data-theme', t);
    else document.documentElement.removeAttribute('data-theme');
    const btn = document.getElementById('theme-btn');
    if (btn) btn.innerHTML = THEME_LABEL[t] ?? 'Auto';
  }
  function cycleTheme() {
    const cur = localStorage.getItem('theme') || '';
    const next = THEME_CYCLE[cur] ?? '';
    if (next) localStorage.setItem('theme', next); else localStorage.removeItem('theme');
    applyTheme(next);
  }
  applyTheme(localStorage.getItem('theme') || '');

  // ---- Login ----
  document.getElementById('pw-input').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  async function doLogin() {
    const pw = document.getElementById('pw-input').value;
    const r = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ password: pw }) });
    if (r.ok) {
      initAudio();  // Login-Klick ist die Nutzergeste, die den AudioContext entsperrt
      document.getElementById('login-view').style.display = 'none';
      document.getElementById('main-view').style.display = '';
      loadSchoolyears();
      loadClasses();
      loadAutoDoneSelection();
      connectWs();
      // Dev-Toggles (PDF-lokal / Klasse-korrigieren / Schüler-Leihschein) werden
      // NICHT mehr vom Browser an den Server gepusht — der Server-State ist die
      // globale Quelle der Wahrheit und kommt via WS (`applyState` →
      // `renderStatusBar`). Ein Login überschreibt ihn nicht mehr.
    } else {
      document.getElementById('login-msg').textContent = 'Falsches Passwort';
    }
  }

  async function doLogout() {
    await fetch('/api/logout', { method: 'POST' });
    location.reload();
  }

  // ---- WebSocket ----
  // Bisher: fester 3000ms-Reconnect ohne Backoff (kein Cap nötig) — über
  // backoffFactor: 1 unverändert nachgebildet (siehe common.js).
  function connectWs() {
    const dot = document.getElementById('ws-dot');
    const label = document.getElementById('ws-label');
    const setConn = (ok) => { dot.className = 'ws-dot ' + (ok ? 'ok' : 'err'); label.textContent = ok ? 'Verbunden' : 'Getrennt'; };
    connectWebSocket(() => `wss://${location.host}/ws/host`, {
      onSocket: (s) => { ws = s; },
      initialDelay: 3000, backoffFactor: 1,
      onOpen: () => setConn(true),
      onClose: (e, reconnect) => { setConn(false); reconnect(); },
      onError: () => setConn(false),
      onMessage: e => {
        let msg;
        try { msg = JSON.parse(e.data); }
        catch (err) { console.warn('Bad WS frame', err); return; }
        if (msg.type === 'state') applyState(msg);
        else if (msg.type === 'book_alert') showBookAlert(msg);
        else if (msg.type === 'loan_slip_download') downloadLoanSlip(msg);
      },
    });
  }

  // „PDF lokal speichern": der Server schickt den Leihschein base64-kodiert über
  // die Host-WS; hier als Blob-Download im Browser des Host-Rechners auslösen
  // (Download-Prompt bzw. Ablage im Download-Ordner, je nach Browsereinstellung).
  function downloadLoanSlip(msg) {
    try {
      const bin = atob(msg.data_b64 || '');
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = msg.filename || 'leihschein.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      showMsg('Leihschein heruntergeladen: ' + (msg.filename || 'leihschein.pdf'));
    } catch (err) {
      console.error('Leihschein-Download fehlgeschlagen', err);
      showMsg('Leihschein-Download fehlgeschlagen');
    }
  }

  // escapeHtml: siehe common.js (vor host.js eingebunden).

  // ---- Tab-Leiste ----
  function renderTabBar() {
    const list = document.getElementById('tab-class-list');
    const ctxs = state.contexts || {};
    // Tabs, deren Kontext serverseitig nicht mehr existiert (Server-Restart),
    // aus der Reihenfolge droppen.
    tabOrder = tabOrder.filter(id => ctxs[id]);
    list.innerHTML = tabOrder.map(id => {
      const c = ctxs[id] || { form: 'Klasse', queue: [] };
      const pend = (c.queue || []).filter(s => s.status === 'pending').length;
      const lbl = escapeHtml(c.form || 'Klasse');
      const badge = pend ? ` <span class="tab-count">${pend}</span>` : '';
      return `<button class="tab-class${activeTab === id ? ' active' : ''}" data-tab="${id}">${lbl}${badge} <span class="tab-close" data-close="${id}" title="Reiter schließen">×</span></button>`;
    }).join('');
    document.getElementById('tab-host-btn').classList.toggle('active', activeTab === 'host');
    document.getElementById('tab-add-btn').classList.toggle('active', activeTab === 'new');
  }

  function switchTab(tab) {
    activeTab = tab;
    renderTabBar();
    renderPanels();
    // Server über den aktiven Klassen-Kontext informieren (Quelle fürs
    // Modus-B-Pairing-Fallback). Host/New brauchen keinen Kontext-Wechsel.
    if (tab !== 'host' && tab !== 'new') setActiveContext(tab);
  }
  function setActiveContext(ctxId) {
    fetch('/api/set-active-context', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: ctxId }),
    }).catch(() => {});
  }

  function showPanel(id) {
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('active', p.id === id));
  }

  // Panels: je Kontext eines (bei Bedarf neu erzeugt), Host/New sind statisch.
  function renderPanels() {
    const container = document.getElementById('class-panels');
    const ctxs = state.contexts || {};
    // Panels geschlossener Kontexte entfernen.
    container.querySelectorAll('.class-panel').forEach(p => { if (!ctxs[p.dataset.ctxId]) p.remove(); });
    // Neue Panels in tabOrder anlegen (bewahrt die Reihenfolge im DOM).
    for (const id of tabOrder) {
      if (!ctxs[id]) continue;
      if (!document.getElementById('panel-ctx-' + id)) {
        container.appendChild(buildClassPanel(id, ctxs[id]));
      }
    }
    showPanel(activeTab === 'host' ? 'panel-host'
      : activeTab === 'new' ? 'panel-new'
      : ('panel-ctx-' + activeTab));
    renderHostTab();
    for (const id of tabOrder) renderClassTab(id);
  }

  function classSelectOptions() {
    return '<option value="">-- Klasse wählen --</option>' +
      classList.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  }

  // Klassen-Panel pro Kontext aufbauen (Once, beim ersten Sichtbarwerden).
  function buildClassPanel(id, ctx) {
    const div = document.createElement('div');
    div.className = 'tab-panel class-panel';
    div.id = 'panel-ctx-' + id;
    div.dataset.ctxId = id;
    const form = escapeHtml(ctx.form || 'Klasse');
    div.innerHTML = `
      <div class="layout">
        <details class="setup-col" open>
          <summary>Schüler hinzufügen</summary>
          <div class="card">
            <div class="row" style="margin-bottom:8px">
              <select class="ctx-single-class" data-ctx-id="${id}">${classSelectOptions()}</select>
            </div>
            <div class="row">
              <select class="ctx-single-student" data-ctx-id="${id}" disabled><option value="">-- erst Klasse wählen --</option></select>
              <button class="success ctx-add-student" data-action="ctx-add-student" data-ctx-id="${id}" disabled>+ Hinzufügen</button>
            </div>
            <p class="hint">Hängt einzelne Schüler an <strong>${form}</strong> an — auch klassenübergreifend.</p>
          </div>
        </details>
        <div class="col">
          <div class="col-label">Betrieb — ${form}</div>
          <div class="card now-serving" data-ctx-ns="${id}"></div>
          <div class="card">
            <h2 style="margin:0 0 8px">Pairing (Modus B)</h2>
            <div class="ctx-arm-banner mb-arm-banner" data-ctx-id="${id}"></div>
            <div class="ctx-codes" data-ctx-id="${id}"></div>
          </div>
          <div class="card">
            <h2 style="margin:0 0 8px">Schüler-Queue <span class="queue-count" data-ctx-qc="${id}"></span></h2>
            <div class="row" style="align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px">
              <button class="ghost" data-action="ctx-reset" data-ctx-id="${id}"><span class="ghost-ico">${ICO_RESET}</span> Status zurücksetzen</button>
              <button class="ghost warn" data-action="ctx-clear" data-ctx-id="${id}"><span class="ghost-ico">${ICO_CLEAR}</span> Queue leeren</button>
              <button class="ghost warn apart" data-action="ctx-disconnect-all" data-ctx-id="${id}"><span class="ghost-ico">${ICO_DISC}</span> Alle Verbindungen trennen</button>
            </div>
            <div class="table-scroll">
              <table class="queue-table">
                <thead><tr><th>Name</th><th>Klasse</th><th>Status</th><th></th></tr></thead>
                <tbody data-ctx-queue="${id}"></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>`;
    return div;
  }

  // ---- Klassen öffnen/schließen ----
  const AUTO_DONE_KEYS = ['not_enrolled', 'unpaid', 'remission_pending', 'exemption_pending'];
  const AUTO_DONE_STORAGE_KEY = 'autoDoneFilters';

  function getAutoDoneSelection() {
    return AUTO_DONE_KEYS.filter(k => document.getElementById(`auto-done-${k}`)?.checked);
  }

  function loadAutoDoneSelection() {
    let saved = [];
    try { saved = JSON.parse(localStorage.getItem(AUTO_DONE_STORAGE_KEY) || '[]'); } catch { saved = []; }
    AUTO_DONE_KEYS.forEach(k => {
      const el = document.getElementById(`auto-done-${k}`);
      if (el) el.checked = saved.includes(k);
    });
  }

  async function openClass(force = false) {
    const form = document.getElementById('new-class-select').value;
    if (!form) return;
    const auto_done = getAutoDoneSelection();
    localStorage.setItem(AUTO_DONE_STORAGE_KEY, JSON.stringify(auto_done));
    showMsg('Lade Schüler…');
    const r = await fetch('/api/open-class', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ form, force, auto_done }) });
    const d = await r.json();
    if (r.status === 409 && d.detail && d.detail.reason === 'active_sessions') {
      if (await confirmDialog(`${d.detail.msg}\n\nTrotzdem öffnen?`, 'Öffnen')) return openClass(true);
      showMsg('Öffnen abgebrochen');
      return;
    }
    if (!r.ok) { showMsg(d.detail?.msg || d.detail || 'Fehler'); return; }
    const id = d.context_id;
    // Optimistisch lokal anzeigen, bevor der WS-Broadcast eintrifft (snappy
    // UX). applyState leitet tabOrder anschließend aus state.contexts ab und
    // rekonziliert diese Vorausnahme — global bleibt der Server der Truth.
    if (!tabOrder.includes(id)) tabOrder.push(id);
    showMsg(`${d.count} Schüler geladen — ${form}`);
    switchTab(id);
  }

  async function openTestConfig() {
    showMsg('Öffne Test Config…');
    const r = await fetch('/api/open-test-config', { method: 'POST' });
    const d = await r.json();
    if (!r.ok) { showMsg(d.detail?.msg || d.detail || 'Fehler'); return; }
    const id = d.context_id;
    if (!tabOrder.includes(id)) tabOrder.push(id);
    showMsg(`Test Config geöffnet — ${d.count} Testschüler`);
    switchTab(id);
  }

  async function closeClass(id) {
    const ctx = (state.contexts || {})[id];
    if (!ctx) { dropTab(id); return; }
    const active = (ctx.queue || []).filter(s => s.status === 'active').length;
    if (active) {
      if (!await confirmDialog(`${ctx.form}: ${active} Schüler aktiv.\n\nReiter wirklich schließen? Aktive Verbindungen werden getrennt.`, 'Schließen')) return;
    }
    const r = await fetch('/api/close-class', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context_id: id }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Schließen fehlgeschlagen'); return; }
    dropTab(id);
    showMsg(`${ctx.form} geschlossen`);
  }

  function dropTab(id) {
    if (activeTab === id) activeTab = 'host';
    // tabOrder wird beim nächsten applyState aus state.contexts neu abgeleitet;
    // der Kontext ist serverseitig bereits geschlossen.
    renderTabBar();
    renderPanels();
  }

  // ---- Schuljahr (lebt im Einstellungen-Dialog) ----
  async function loadSchoolyears() {
    const sel = document.getElementById('schoolyear-select');
    if (!sel) return;
    const r = await fetch('/api/schoolyears');
    if (!r.ok) { sel.innerHTML = '<option value="">-- Fehler beim Laden --</option>'; return; }
    const { schoolyears, selected } = await r.json();
    // value="" = Default-Schuljahr (selected===null im State; laufend bzw. nächstes).
    sel.innerHTML = schoolyears.map(y => {
      const tag = y.default ? ' (aktuell)' : '';
      const val = y.default ? '' : escapeHtml(y.id);
      const isSel = (selected === null && y.default) || selected === y.id;
      return `<option value="${val}"${isSel ? ' selected' : ''}>${escapeHtml(y.name)}${tag}</option>`;
    }).join('');
  }

  async function selectSchoolyear(force = false) {
    const schoolyear = document.getElementById('schoolyear-select').value || null;
    showMsg('Wechsle Schuljahr…');
    const r = await fetch('/api/select-schoolyear', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ schoolyear, force }) });
    const d = await r.json();
    if (r.status === 409 && d.detail && d.detail.reason === 'active_sessions') {
      if (await confirmDialog(`${d.detail.msg}\n\nTrotzdem Schuljahr wechseln?`, 'Wechseln')) return selectSchoolyear(true);
      showMsg('Schuljahreswechsel abgebrochen');
      loadSchoolyears();  // Dropdown auf tatsächlichen State zurücksetzen
      return;
    }
    if (r.ok) {
      showMsg('Schuljahr gewechselt');
      loadClasses();
    } else {
      showMsg(d.detail?.msg || d.detail || 'Fehler');
    }
  }

  // ---- Klassen-Liste (für Wähler + Einzelne-Schüler-Selects) ----
  async function loadClasses() {
    const r = await fetch('/api/classes');
    if (!r.ok) return;
    const { classes } = await r.json();
    classList = classes || [];
    const opts = classSelectOptions();
    const nc = document.getElementById('new-class-select');
    if (nc) nc.innerHTML = opts;
    document.querySelectorAll('.ctx-single-class').forEach(sel => sel.innerHTML = opts);
  }

  // ---- Einzelne Schüler (pro Klassen-Tab) ----
  async function ctxLoadStudents(id) {
    const form = document.querySelector(`.ctx-single-class[data-ctx-id="${id}"]`).value;
    const sel = document.querySelector(`.ctx-single-student[data-ctx-id="${id}"]`);
    const btn = document.querySelector(`.ctx-add-student[data-ctx-id="${id}"]`);
    btn.disabled = true;
    ctxSingleStudents[id] = [];
    if (!form) {
      sel.disabled = true;
      sel.innerHTML = '<option value="">-- erst Klasse wählen --</option>';
      return;
    }
    sel.disabled = true;
    sel.innerHTML = '<option value="">-- lädt… --</option>';
    const r = await fetch('/api/students-for-class?form=' + encodeURIComponent(form));
    if (!r.ok) { sel.innerHTML = '<option value="">-- Fehler beim Laden --</option>'; return; }
    const { students } = await r.json();
    ctxSingleStudents[id] = students;
    sel.innerHTML = '<option value="">-- Schüler wählen --</option>' +
      students.map((s, i) => `<option value="${i}">${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}</option>`).join('');
    sel.disabled = false;
  }

  function ctxOnStudentChange(id) {
    const sel = document.querySelector(`.ctx-single-student[data-ctx-id="${id}"]`);
    document.querySelector(`.ctx-add-student[data-ctx-id="${id}"]`).disabled = (sel.value === '');
  }

  async function ctxAddSingleStudent(id) {
    const sel = document.querySelector(`.ctx-single-student[data-ctx-id="${id}"]`);
    const idx = sel.value;
    if (idx === '') return;
    const s = (ctxSingleStudents[id] || [])[idx];
    if (!s) return;
    const r = await fetch('/api/add-student', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: s.student_id, lastname: s.lastname, firstname: s.firstname, form: s.form, context_id: id }) });
    const d = await r.json().catch(() => ({}));
    if (r.ok) {
      showMsg(`${s.lastname}, ${s.firstname} hinzugefügt (${d.count} in Queue)`);
      sel.value = '';
      ctxOnStudentChange(id);
    } else {
      showMsg(d.detail || 'Konnte nicht hinzufügen');
    }
  }

  // ---- Queue-Steuerung (pro Klassen-Tab) ----
  async function ctxResetQueue(id) {
    if (!await confirmDialog('Queue-Status wirklich zurücksetzen?\n\nAlle Schüler dieser Klasse kehren auf „Wartend" zurück (Verbindungen werden getrennt).', 'Zurücksetzen')) return;
    const r = await fetch('/api/reset-queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context_id: id }) });
    if (r.ok) { const d = await r.json().catch(() => ({})); showMsg(`Queue zurückgesetzt (${d.count || 0} geändert)`); }
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Zurücksetzen fehlgeschlagen'); }
  }

  async function ctxClearQueue(id) {
    const ctx = state.contexts[id];
    const n = (ctx?.queue || []).length;
    if (!n) { showMsg('Queue ist bereits leer'); return; }
    if (!await confirmDialog(`Wirklich die GESAMTE Queue von ${ctx.form} leeren?\n\nAlle ${n} Schüler werden entfernt.`, 'Weiter')) return;
    if (!await confirmDialog('Letzte Bestätigung: Queue endgültig leeren?\n\nLaufende Live-Sessions werden getrennt.', 'Queue leeren')) return;
    const r = await fetch('/api/clear-queue', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context_id: id }) });
    if (r.ok) { const d = await r.json().catch(() => ({})); showMsg(`Queue geleert (${d.count || 0} entfernt)`); }
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Leeren fehlgeschlagen'); }
  }

  async function ctxDisconnectAll(id) {
    if (!await confirmDialog('Wirklich ALLE Verbindungen dieser Klasse trennen?\n\nBetroffene Schüler kehren auf „Wartend" zurück.', 'Alle trennen')) return;
    const r = await fetch('/api/disconnect-all', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ context_id: id }) });
    if (r.ok) { const d = await r.json().catch(() => ({})); showMsg(`${d.count || 0} Verbindung(en) getrennt`); }
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Trennen fehlgeschlagen'); }
  }

  // ---- Helfer ----
  async function addHelper() {
    const name = document.getElementById('helper-name').value || 'Helfer';
    const r = await fetch('/api/add-helper', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ name }) });
    const d = await r.json();
    if (r.ok) {
      qrWatch = { kind: 'helper', token: d.token };
      showQr(d.qr, d.url);
    }
  }

  async function removeHelper(token) {
    await fetch(`/api/helper/${token}`, { method: 'DELETE' });
  }

  async function nextStudent(token) {
    const r = await fetch('/api/next-student', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ helper_token: token }) });
    const d = await r.json();
    if (!r.ok) showMsg(d.detail || 'Fehler');
  }

  // ---- Queue-Steuerung ----
  async function skipStudent(studentId) {
    await fetch('/api/skip', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId }) });
  }

  // Einzelnen Schüler trennen: Helfer-/Schüler-Verbindung lösen, zurück auf "Wartend".
  async function disconnectStudent(studentId) {
    const r = await fetch('/api/disconnect', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Trennen fehlgeschlagen'); }
  }

  async function finishStudent(studentId) {
    await fetch('/api/finish', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId }) });
  }

  // Öffnet den Druck-Dialog und gibt die gewählte „second_page"-Option zurück,
  // oder null wenn Abbrechen gedrückt wurde.
  function openPrintDialog() {
    return new Promise(resolve => {
      const modal = document.getElementById('print-dialog');
      const box = modal.querySelector('.modal-box');
      const slipCb = document.getElementById('print-dialog-slip');
      const okBtn = document.getElementById('print-dialog-ok');
      const cancelBtn = document.getElementById('print-dialog-cancel');
      const prevFocus = document.activeElement;
      slipCb.checked = !!document.getElementById('slip-second-page')?.checked;
      const onKey = (e) => {
        if (e.key === 'Escape') { e.preventDefault(); finish(null); }
        else trapFocus(box, e);
      };
      const finish = (val) => {
        modal.classList.remove('show');
        okBtn.onclick = cancelBtn.onclick = null;
        modal.removeEventListener('keydown', onKey);
        if (prevFocus) prevFocus.focus();
        resolve(val);
      };
      okBtn.onclick = () => finish(slipCb.checked);
      cancelBtn.onclick = () => finish(null);
      modal.addEventListener('keydown', onKey);
      modal.classList.add('show');
      okBtn.focus();
    });
  }

  async function printLoanSlip(studentId, btn) {
    const secondPage = await openPrintDialog();
    if (secondPage === null) return;
    await busy(btn, async () => {
      showMsg('Leihschein wird geholt …');
      const r = await fetch('/api/print-loan-slip', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId, second_page: secondPage }) });
      const d = await r.json().catch(() => ({}));
      if (r.ok) showMsg(`Leihschein: ${d.detail || 'gedruckt'} (${d.backend})`);
      else showMsg(d.detail || 'Druck fehlgeschlagen');
    });
  }

  // ---- Modus B ----
  async function openModusB() {
    const r = await fetch('/api/modus-b/open', { method: 'POST' });
    // QR nicht sofort anzeigen — der Host öffnet ihn bei Bedarf über die Buttons.
    if (r.ok) { const d = await r.json(); mbQrDataUrl = d.qr; }
  }
  async function closeModusB() {
    await fetch('/api/modus-b/close', { method: 'POST' });
    mbQrDataUrl = null;
  }
  async function showMbQr() {
    // Immer frisch holen — der Join-QR rotiert nach jeder Zuordnung.
    const r = await fetch('/api/modus-b/qr');
    if (r.ok) { const d = await r.json(); mbQrDataUrl = d.qr; }
    if (mbQrDataUrl) {
      // Beim nächsten Scan kommt ein neuer Pairing-Code rein -> pending_count steigt.
      qrWatch = { kind: 'student', baseline: (state.modus_b && state.modus_b.pending_count) || 0 };
      showQr(mbQrDataUrl, state.modus_b.join_url || '');
    }
  }
  // iPad-Display per Registrierungscode (vom iPad-Bildschirm) freischalten.
  async function authorizeDisplay() {
    const el = document.getElementById('mb-display-code');
    const code = (el.value || '').trim();
    if (!code) return;
    const r = await fetch('/api/display/authorize', { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ registration_code: code }) });
    if (r.ok) { el.value = ''; showMsg('iPad freigeschaltet'); }
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Freischalten fehlgeschlagen'); }
  }
  // QR, mit dem ein iPad die Display-Seite (/qr-display) öffnet.
  async function showDisplayQr() {
    const r = await fetch('/api/display/qr');
    if (!r.ok) { showMsg('QR für iPad konnte nicht geladen werden'); return; }
    const d = await r.json();
    // Scannt das iPad den QR, verbindet sich ein neues Display -> displays-Liste wächst.
    qrWatch = { kind: 'display', baseline: ((state.modus_b && state.modus_b.displays) || []).length };
    showQr(d.qr, d.url || '');
  }
  // Gemeinsame Pairing-Funktion: ordnet einen wartenden Code einem Schüler zu.
  async function doPair(studentId, code, btn) {
    if (!studentId || !code) return;
    armedStudentId = null;
    await busy(btn, async () => {
      let r = await fetch('/api/student/pair', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ student_id: studentId, pairing_code: code }) });
      if (r.status === 409) {
        const d = await r.json();
        const det = d.detail;
        // Blocker-Sammelfall („blocked"): nicht bezahlt und/oder ausstehender
        // Nachweis — alle Gründe in einem Dialog dem Host zur Freigabe vorlegen.
        if (det && det.reason === 'blocked' && Array.isArray(det.blockers) && det.blockers.length) {
          const lines = det.blockers.map(b => {
            if (b.kind === 'unpaid') return `Nicht bezahlt (offen: ${b.amount_open} €)`;
            if (b.kind === 'nachweis') {
              const parts = [];
              if (b.remission)  parts.push('Ermäßigungsnachweis fehlt');
              if (b.exemption) parts.push('Befreiungsnachweis fehlt');
              return parts.join(' + ');
            }
            return null;
          }).filter(Boolean);
          if (!lines.length) { showMsg(det.msg || 'Pairing fehlgeschlagen'); return; }
          if (!await confirmDialog(`${lines.join('\n')}\n\nTrotzdem freigeben?`, 'Trotzdem freigeben')) return;
          r = await fetch('/api/student/pair', { method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ student_id: studentId, pairing_code: code, override_payment: true }) });
        } else if (det && det.reason === 'unpaid') {
          // Fallback für eine abweichende Server-Antwortform: nur nicht bezahlt.
          if (!await confirmDialog(`Schüler nicht bezahlt (offen: ${det.amount_open} €).\n\nTrotzdem freigeben?`, 'Trotzdem freigeben')) return;
          r = await fetch('/api/student/pair', { method: 'POST', headers: {'Content-Type':'application/json'},
            body: JSON.stringify({ student_id: studentId, pairing_code: code, override_payment: true }) });
        } else { showMsg((det && det.msg) || d.detail || 'Pairing fehlgeschlagen'); return; }
      }
      if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Pairing fehlgeschlagen'); }
    });
  }

  // Schüler-zuerst: "Pairing"-Button stellt den Schüler scharf; danach Code in
  // der Liste klicken. Arming ist global, wirkt aber nur im Klassen-Tab des
  // bewaffneten Schülers (dessen Pairing-Card wird neu gerendert).
  function pairStudent(studentId) {
    const pending = (state.modus_b && state.modus_b.pending) || [];
    if (!pending.length) { showMsg('Kein wartender Code — Schüler muss erst den QR scannen'); return; }
    armedStudentId = (armedStudentId === studentId) ? null : studentId;
    const cid = findCtxOfStudent(studentId);
    if (cid) renderCtxPairing(cid);
  }

  function cancelArm() { armedStudentId = null; for (const id of tabOrder) renderCtxPairing(id); }

  // Modus-B-Kontrolle im Host-Tab (global): öffnen/schließen, iPad-Freischalt,
  // QR-Buttons. Pairing-Codes selbst leben im jeweiligen Klassen-Tab.
  function renderModusBControl() {
    const mb = state.modus_b || { open: false, pending: [], displays: [] };
    document.getElementById('mb-open-btn').style.display = mb.open ? 'none' : '';
    document.getElementById('mb-close-btn').style.display = mb.open ? '' : 'none';
    document.getElementById('mb-status').textContent = mb.open ? 'geöffnet' : 'geschlossen';
    document.getElementById('mb-info').style.display = mb.open ? '' : 'none';
    // Freischalt-Feld nur zeigen, wenn ein iPad verbunden, aber noch nicht autorisiert ist.
    const needsAuth = (mb.displays || []).some(d => d.connected && !d.authorized);
    document.getElementById('mb-display-auth').style.display = needsAuth ? '' : 'none';
  }

  // Pairing-Card eines Klassen-Tabs: Arm-Banner + wartende Codes, zugeordnet
  // zu den wartenden Schülern DIESER Klasse.
  function renderCtxPairing(id) {
    const ctx = (state.contexts || {})[id];
    if (!ctx) return;
    const queue = ctx.queue || [];
    const pendingStudents = queue.filter(q => q.status === 'pending');
    // Scharfgestellter Schüler muss noch wartend sein, sonst zurücksetzen.
    const armed = armedStudentId ? pendingStudents.find(q => q.student_id === armedStudentId) : null;
    if (armedStudentId && !armed) armedStudentId = null;

    const banner = document.querySelector(`.ctx-arm-banner[data-ctx-id="${id}"]`);
    if (banner) {
      if (armed) {
        banner.style.display = 'block';
        banner.innerHTML = `Code für <b>${escapeHtml(armed.lastname)}, ${escapeHtml(armed.firstname)}</b> wählen — `
          + `<a href="#" data-action="cancel-arm" style="color:#fff">Abbrechen</a>`;
      } else {
        banner.style.display = 'none';
        banner.innerHTML = '';
      }
    }

    const codesEl = document.querySelector(`.ctx-codes[data-ctx-id="${id}"]`);
    if (!codesEl) return;
    const pending = (state.modus_b && state.modus_b.pending) || [];
    if (!pending.length) {
      codesEl.innerHTML = '<div style="opacity:.4">Noch keine wartenden Codes</div>';
      return;
    }
    const studentOpts = pendingStudents
      .map(q => `<option value="${q.student_id}">${escapeHtml(q.lastname)}, ${escapeHtml(q.firstname)}</option>`)
      .join('');
    codesEl.innerHTML = pending.map(p => {
      const dot = p.connected ? '<span style="color:#30d158">●</span>' : '<span style="color:#888">○</span>';
      const meta = `<span class="code-meta">${dot} ${p.age_s}s</span>`;
      if (armed) {
        // Schüler-zuerst: Code-Chip direkt anklickbar.
        return `<div class="code-row">
          <button class="success code-chip" data-action="pair" data-student-id="${armed.student_id}" data-code="${p.pairing_code}">${p.pairing_code}</button>
          ${meta}
        </div>`;
      }
      // Code-zuerst: Schüler im Select wählen + Zuordnen.
      const selId = `mb-sel-${id}-${p.pairing_code}`;
      return `<div class="code-row">
        <span class="code-val">${p.pairing_code}</span>
        ${meta}
        <select id="${selId}" style="flex:1;min-width:140px">${studentOpts}</select>
        <button class="success" data-action="pair-select" data-sel-id="${selId}" data-code="${p.pairing_code}" ${pendingStudents.length ? '' : 'disabled'}>Zuordnen</button>
      </div>`;
    }).join('');
  }

  // Host-Tab (Helfer + Modus-B-Kontrolle) und Klassen-Tab (Now-Serving + Queue
  // + Pairing) getrennt rendern.
  function renderHostTab() { renderHelpers(); renderModusBControl(); }
  function renderClassTab(id) {
    if (!(state.contexts || {})[id]) return;
    if (!document.getElementById('panel-ctx-' + id)) return;
    renderCtxNowServing(id);
    renderCtxQueue(id);
    renderCtxPairing(id);
  }

  // ---- State rendering ----
  function applyState(s) {
    state = s;
    if (!state.modus_b) state.modus_b = { open: false, pending: [], pending_count: 0, displays: [] };
    if (!state.contexts) state.contexts = {};
    // Neuer Pairing-Code? -> Beep + Blink, damit der Host es ohne Hinschauen merkt.
    const pc = state.modus_b.pending_count || 0;
    if (state.modus_b.open && prevPendingCount !== null && pc > prevPendingCount) {
      playBeep();
      flashModusB();
    }
    prevPendingCount = pc;
    // Schuljahr-Select (im Einstellungen-Dialog) mit dem Server-State synchron
    // halten (z. B. wenn ein anderer Host das Schuljahr wechselt). '' = aktuelles Jahr.
    const syEl = document.getElementById('schoolyear-select');
    if (syEl && syEl.options.length) syEl.value = state.selected_schoolyear || '';
    // Tabs global aus dem Server-State ableiten (Einfügereihenfolge der
    // Kontext-Keys = Reihenfolge, in der Klassen serverseitig geöffnet wurden,
    // auf jedem Host-Rechner identisch). Aktiven Tab ggf. auf Host zurückfallen
    // lassen, falls sein Kontext nicht mehr existiert (Server-Restart / auf
    // anderem Rechner geschlossen). Schuljahr-Wechsel leert alle Kontexte →
    // alle Klassen-Reiter verschwinden.
    tabOrder = Object.keys(state.contexts);
    if (activeTab !== 'host' && activeTab !== 'new' && !state.contexts[activeTab]) activeTab = 'host';
    maybeCloseQrOnScan();
    renderTabBar();
    renderPanels();
    renderWorkerStatus();
    renderStatusBar();
  }

  // Now-Serving pro Klassen-Tab: die gerade bedienten (aktiven) Schüler dieser
  // Klasse groß und prominent — die eine Information, die der Host während der
  // Ausgabe ständig braucht. Wiederverwendete Aktions-Handler (finish/print).
  function renderCtxNowServing(id) {
    const el = document.querySelector(`[data-ctx-ns="${id}"]`);
    if (!el) return;
    const ctx = (state.contexts || {})[id];
    const queue = (ctx && ctx.queue) || [];
    const active = queue.filter(s => s.status === 'active');
    const next = queue.find(s => s.status === 'pending');
    const helpers = Object.values(state.helpers || {});

    // Alerts abgelaufener/nicht mehr aktiver Schüler wegräumen — sonst bliebe
    // ein Kästchen rot, obwohl der Schüler längst abgeschlossen/entfernt ist.
    const activeIds = new Set(active.map(s => s.student_id));
    for (const aid of Object.keys(studentAlerts)) {
      if (!activeIds.has(Number(aid))) delete studentAlerts[aid];
    }

    let body;
    if (!active.length) {
      body = '<div class="ns-empty">Niemand aktiv — Schüler per „Pairing" oder „Nächster" zuweisen.</div>';
    } else {
      body = '<div class="ns-grid">' + active.map(s => {
        const helper = helpers.find(h => h.student_id === s.student_id);
        const helperLbl = helper ? `<span class="ns-helper">${ICO_HELPER} ${escapeHtml(helper.name)}</span>` : '';
        const alert = studentAlerts[s.student_id];
        // Schließen-Button nur am Schüler-Client-Modal (Modus B): dort hat der
        // Client bewusst keinen eigenen, also muss der Host freigeben. Am Helfer
        // (Modus A) schließt der Helfer sein Modal selbst → kein Host-Button.
        const alertBtn = alert && alert.source !== 'helper'
          ? ` <button class="secondary" data-action="clear-book-alert" data-student-id="${s.student_id}">Schließen</button>`
          : '';
        const alertLbl = alert
          ? `<div class="ns-alert-wrap"><div class="ns-alert${alert.borrower ? ' ns-alert-muted' : ''}">${escapeHtml(alert.text)}${alertBtn}</div>` +
            (alert.borrower ? `<div class="ns-borrower">${alert.kind === 'book_deleted' ? 'Ersatzanspruch' : 'verliehen an'} ${escapeHtml(alert.borrower)}</div>` : '') +
            `</div>`
          : '';
        // Kästchen bleibt rot (wie gehabt); nur der Meldungstext ist beim
        // Verliehen-Alert normal — „verliehen an …" ist das einzige Rot im Text.
        return `<div class="ns-tile${alert ? ' ns-tile-alert' : ''}">
          <div class="ns-name">${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}</div>
          <div class="ns-meta"><span>${escapeHtml(s.form)}</span>${helperLbl}</div>
          ${alertLbl}
          <div class="ns-actions">
            <button class="success" data-action="finish" data-student-id="${s.student_id}">Abschließen</button>
            <button class="secondary" data-action="print" data-student-id="${s.student_id}">Leihschein</button>
          </div>
        </div>`;
      }).join('') + '</div>';
    }

    const nextLbl = next
      ? `<div class="ns-next">Als Nächstes: <strong>${escapeHtml(next.lastname)}, ${escapeHtml(next.firstname)}</strong> (${escapeHtml(next.form)})</div>`
      : '';

    el.innerHTML = `<div class="ns-head">Aktuell in Ausgabe</div>${body}${nextLbl}`;
  }

  // QR-Popup automatisch schließen, sobald der gezeigte Code gescannt wurde
  // (neuer Pairing-Code beim Schüler-QR, neues Display beim iPad-QR, bzw. der
  // Helfer sich erfolgreich verbunden hat).
  function maybeCloseQrOnScan() {
    if (!qrWatch) return;
    if (qrWatch.kind === 'helper') {
      if ((state.helpers || {})[qrWatch.token]?.connected) closeQr();
      return;
    }
    const mb = state.modus_b || {};
    const grown = qrWatch.kind === 'student'
      ? (mb.pending_count || 0) > qrWatch.baseline
      : ((mb.displays || []).length) > qrWatch.baseline;
    if (grown) closeQr();
  }

  // Konsolidierte Status-Bar: Modus B, Queue-Zähler und iPad-Stand.
  // (WS-Dot via connectWs, Worker via renderWorkerStatus — gleiche Elemente.)
  function renderStatusBar() {
    const mb = state.modus_b || { open: false, displays: [] };
    // Queue-Zähler über alle Klassen-Kontexte (globaler Überblick in der Leiste).
    let openQ = 0, totalQ = 0;
    for (const id of Object.keys(state.contexts || {})) {
      const q = (state.contexts[id].queue) || [];
      totalQ += q.length;
      openQ += q.filter(s => s.status === 'pending').length;
    }
    const codes = mb.pending_count || 0;
    document.getElementById('sb-modusb').innerHTML = mb.open
      ? `Modus B <b>offen</b> · <b>${codes}</b> Code${codes === 1 ? '' : 's'} offen`
      : 'Modus B <b>geschlossen</b>';
    document.getElementById('sb-queue').innerHTML = `Queue <b>${openQ} offen</b> / ${totalQ}`;
    const auth = (mb.displays || []).filter(d => d.authorized && d.connected).length;
    const total = (mb.displays || []).length;
    document.getElementById('sb-ipads').innerHTML = `iPads <b>${auth}/${total}</b>`;
    // Server-Toggles synchron halten (auch bei Reconnect / zweitem Host):
    // Tailscale-IP, PDF-lokal, Klasse-korrigieren, Schüler-Leihschein. Alle
    // leben im Server-State als Quelle der Wahrheit (global für alle Host-
    // Rechner), nicht in localStorage. Nicht anfassen, solange der
    // Einstellungen-Dialog offen ist — sonst würden ungespeicherte Änderungen
    // des Operators überschrieben. (Theme/Auto-Hell-Dunkel bleibt bewusst
    // pro Browser in localStorage und wird hier nicht angetastet.)
    if (!settingsOpen()) {
      const tsCb = document.getElementById('force-tailscale-ip');
      if (tsCb) tsCb.checked = !!state.force_tailscale_ip;
      const pdfCb = document.getElementById('save-pdf-locally');
      if (pdfCb) pdfCb.checked = !!state.save_pdf_locally;
      const fixCb = document.getElementById('fix-class-on-slip');
      if (fixCb) fixCb.checked = !!state.fix_class_on_slip;
      const slipCb = document.getElementById('slip-second-page');
      if (slipCb) slipCb.checked = !!state.slip_second_page_default;
    }
  }

  // Toggle „Tailscale-IP": erzwingt die Tailscale-IP in allen QR-Codes (Server-
  // State). Die offenen QR-Modals werden neu geladen, falls gerade sichtbar.
  async function setForceTailscaleIp(enabled) {
    const r = await fetch('/api/force-tailscale-ip', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled }),
    });
    if (r.ok) showMsg(enabled ? 'QR-Codes nutzen jetzt die Tailscale-IP' : 'QR-Codes nutzen wieder die Auto-IP');
  }

  // Entwickler-Toggle „PDF lokal speichern": erzwingt beim Drucken das
  // file-Backend (Leihschein wird gespeichert statt gedruckt). Server-State ist
  // globale Quelle der Wahrheit (broadcastet an alle Hosts); hier der stille
  // Server-Push beim Ändern im Einstellungen-Dialog. Toast kommt vom Aufrufer.
  async function pushSavePdfLocally(enabled) {
    try {
      await fetch('/api/settings/save-pdf-locally', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ enabled }),
      });
    } catch (_) {}
  }

  // Experimenteller Toggle „Klasse auf Leihschein korrigieren": ersetzt beim
  // Drucken den Klassen-Code auf dem Leihschein durch die echte Klasse. Wie oben:
  // Server-State als globale Quelle, hier der stille Server-Push im Dialog.
  async function pushFixClassOnSlip(enabled) {
    try {
      await fetch('/api/settings/fix-class-on-slip', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ enabled }),
      });
    } catch (_) {}
  }

  // Einstellungen: Leihschein-Drucker setzen (In-Memory-Override im Server-
  // State; leer = zurück auf .env/Systemstandard).
  async function setPrinter(name) {
    const r = await fetch('/api/printer', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ printer: name }),
    });
    if (r.ok) showMsg(name ? `Leihscheine gehen jetzt an „${name}"` : 'Leihscheine gehen an den Standard-Drucker');
    else showMsg('Drucker konnte nicht gesetzt werden');
  }

  function renderWorkerStatus() {
    const wp = state.worker_pool || { total: 0, available: 0, in_use: 0 };
    const el = document.getElementById('worker-status');
    // Ganze Status-Bar rot tönen, wenn gar keine Worker da sind (Buchung/Scan unmöglich).
    document.getElementById('status-bar').classList.toggle('alert', !wp.total);
    el.classList.remove('txt-warn', 'txt-danger');
    if (!wp.total) {
      el.innerHTML = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.46 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg> <b>keine Worker</b> (Buchung/Scan nicht möglich)';
      el.classList.add('txt-danger');
    } else {
      el.innerHTML = `Worker <b>${wp.available}/${wp.total}</b> frei`;
      if (wp.available === 0) el.classList.add('txt-warn');
    }
  }

  function renderHelpers() {
    const tbody = document.getElementById('helper-tbody');
    const helpers = Object.values(state.helpers || {}).filter(h => h.connected);
    if (!helpers.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="opacity:.4;text-align:center">Noch keine Helfer</td></tr>';
      return;
    }
    // Klassen-Auswahl pro Helfer: offene Kontexte + „(aktive Klasse)" = None.
    const ctxs = state.contexts || {};
    const classOpts = '<option value="">(aktive)</option>' +
      tabOrder.map(id => `<option value="${id}">${escapeHtml(ctxs[id]?.form || 'Klasse')}</option>`).join('');
    tbody.innerHTML = helpers.map(h => {
      const student = h.student_id ? findStudentInState(h.student_id) : null;
      const studentName = student ? `${escapeHtml(student.lastname)}, ${escapeHtml(student.firstname)}` : '–';
      const connDot = h.connected ? '<span style="color:#30d158">●</span>' : '<span style="color:#888">○</span>';
      const hasStudent = h.student_id !== null;
      return `<tr>
        <td>${escapeHtml(h.name)}</td>
        <td>${connDot} ${h.connected ? 'verbunden' : 'getrennt'}</td>
        <td><select class="helper-class-sel" data-token="${h.token}" title="Klasse, die dieser Helfer bedient („Nächster" zieht daraus)">${classOpts}</select></td>
        <td>${studentName}</td>
        <td>${hasStudent ? '' : `<button class="success" data-action="next-student" data-token="${h.token}">Nächster</button>`}</td>
        <td><button class="danger" aria-label="Helfer ${escapeHtml(h.name)} entfernen" data-action="remove-helper" data-token="${h.token}"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg></button></td>
      </tr>`;
    }).join('');
    // Select-Werte nach innerHTML auf den Server-Stand bringen.
    tbody.querySelectorAll('.helper-class-sel').forEach(sel => {
      const h = (state.helpers || {})[sel.dataset.token];
      sel.value = (h && h.context_id) || '';
    });
  }

  async function setHelperClass(token, ctxId) {
    const r = await fetch(`/api/helper/${token}/class`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: ctxId ? ctxId : null }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Umbindung fehlgeschlagen'); }
  }

  // Queue-Tabelle eines Klassen-Tabs.
  function renderCtxQueue(id) {
    const ctx = (state.contexts || {})[id];
    const queue = (ctx && ctx.queue) || [];
    const tbody = document.querySelector(`[data-ctx-queue="${id}"]`);
    const qc = document.querySelector(`[data-ctx-qc="${id}"]`);
    if (qc) qc.textContent = `(${queue.filter(q => q.status === 'pending').length} offen / ${queue.length} gesamt)`;
    if (!tbody) return;
    if (!queue.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="opacity:.4;text-align:center">Keine Schüler — Klasse hinzufügen</td></tr>';
      return;
    }
    tbody.innerHTML = queue.map(s => {
      const badgeClass = { pending: 'badge-pending', active: 'badge-active', done: 'badge-done', skipped: 'badge-skipped' }[s.status] || '';
      const statusLabel = { pending: 'Wartend', active: 'Aktiv', done: 'Fertig', skipped: 'Übersprungen' }[s.status] || s.status;
      const pairBtn = (state.modus_b && state.modus_b.open)
        ? `<button class="success" data-action="pair-student" data-student-id="${s.student_id}">Pairing</button> ` : '';
      const printBtn = `<button class="secondary" data-action="print" data-student-id="${s.student_id}">Leihschein</button>`;
      // Trennen: löst Helfer-/Schüler-Verbindung und setzt den Schüler zurück auf "Wartend".
      const disconnectBtn = `<button class="secondary" data-action="disconnect" data-student-id="${s.student_id}">Trennen</button>`;
      const actions = s.status === 'pending'
        ? `${pairBtn}<button class="secondary" data-action="skip" data-student-id="${s.student_id}">Überspringen</button> ${disconnectBtn}`
        : s.status === 'active'
          ? `<button class="success" data-action="finish" data-student-id="${s.student_id}">Abschließen</button> <button class="secondary" data-action="skip" data-student-id="${s.student_id}">Abbrechen</button> ${disconnectBtn} ${printBtn}`
          : s.status === 'done'
            ? printBtn
            : '';
      return `<tr class="${s.status === 'active' ? 'row-active' : ''}">
        <td>${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}</td>
        <td>${escapeHtml(s.form)}</td>
        <td><span class="badge ${badgeClass}">${statusLabel}</span></td>
        <td><div class="row-actions">${actions}</div></td>
      </tr>`;
    }).join('');
  }

  // ---- Modal-A11y: Fokus-Falle + Fokus-Rückgabe (für confirm + QR) ----
  // Hält Tab/Shift+Tab innerhalb des Containers; merkt vorigen Fokus.
  let _modalPrevFocus = null;
  const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
  function trapFocus(container, e) {
    if (e.key !== 'Tab') return;
    const items = [...container.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  // ---- QR-Modal ----
  function showQr(dataUrl, url) {
    _modalPrevFocus = document.activeElement;
    const _qrImg = document.getElementById('qr-img');
    if (typeof dataUrl === 'string' && dataUrl.startsWith('data:image/')) _qrImg.src = dataUrl;
    else _qrImg.src = '';
    document.getElementById('qr-url').textContent = url;
    document.getElementById('qr-modal').classList.add('show');
    document.getElementById('qr-close-btn').focus();
  }
  function closeQr() {
    document.getElementById('qr-modal').classList.remove('show');
    qrWatch = null;
    if (_modalPrevFocus) { _modalPrevFocus.focus(); _modalPrevFocus = null; }
  }

  // Ausgemustertes oder anderweitig verliehenes Buch wurde gescannt (Scanner
  // oder Schüler-Client) — als Toast melden UND im „Aktuell in Ausgabe"-
  // Kästchen der betreffenden Person festhalten (bis die Ausgabe abgeschlossen
  // wird bzw. state.queue diesen Schüler nicht mehr als aktiv führt).
  function showBookAlert(msg) {
    if (msg.student_id == null) return;
    const cid = findCtxOfStudent(msg.student_id);
    if (msg.cleared) {
      delete studentAlerts[msg.student_id];
      if (cid) renderCtxNowServing(cid);
      return;
    }
    const label = msg.kind === 'book_deleted' ? 'Ausgemustertes Buch gescannt' : 'Bereits verliehenes Buch gescannt';
    const who = msg.student ? ` (${escapeHtml(msg.student)})` : '';
    // „currently lent to someone else": aktuellen Ausleiher namentlich nennen
    // (nur bei not_in_stock belegt; read-only aus /books/:code, PLAN §3.7).
    // Toast bleibt als rotes Kästchen (warn) — nur im Now-Serving-Kästchen ist
    // der Meldungstext normal und „verliehen an …" rot. Bei book_deleted mit
    // loaned_to → „Ersatzanspruch …" (ausgemustert, aber noch Schüler-verknüpft).
    const loaned = msg.loaned_to
      ? (msg.kind === 'book_deleted'
          ? ` — Ersatzanspruch ${escapeHtml(msg.loaned_to)}`
          : ` — verliehen an ${escapeHtml(msg.loaned_to)}`)
      : '';
    showMsg(`${label}: ${escapeHtml(msg.title || msg.barcode)}${who}${loaned}`, 'warn');
    studentAlerts[msg.student_id] = {
      text: `${label}: ${msg.title || msg.barcode}`,
      borrower: msg.loaned_to || null,
      kind: msg.kind || null,
      source: msg.source || 'student',
    };
    if (cid) renderCtxNowServing(cid);
  }

  // Host gibt das blockierende Hinweis-Modal am Schüler-Client wieder frei
  // (der Client hat bewusst keinen eigenen Schließen-Button).
  async function clearBookAlert(studentId) {
    delete studentAlerts[studentId];
    const cid = findCtxOfStudent(studentId);
    if (cid) renderCtxNowServing(cid);
    await fetch('/api/clear-book-alert', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId }),
    });
  }

  // ---- Util: Toast-Stack (mehrere Meldungen gleichzeitig) ----
  function showMsg(text, variant) {
    const stack = document.getElementById('toast-stack');
    const t = document.createElement('div');
    t.className = 'toast' + (variant ? ` toast-${variant}` : '');
    t.textContent = text;
    stack.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    setTimeout(() => {
      t.classList.remove('show');
      const drop = () => t.remove();
      t.addEventListener('transitionend', drop, { once: true });
      setTimeout(drop, 400);  // Fallback, falls keine Transition feuert (reduced-motion)
    }, 4000);
  }

  // Eigener Bestätigungs-Dialog statt nativer confirm()-Ketten. Gibt ein Promise<bool>.
  function confirmDialog(message, okLabel = 'Bestätigen') {
    return new Promise(resolve => {
      const m = document.getElementById('confirm-modal');
      const box = m.querySelector('.modal-box');
      const ok = document.getElementById('confirm-ok');
      const cancel = document.getElementById('confirm-cancel');
      const prevFocus = document.activeElement;
      document.getElementById('confirm-text').textContent = message;
      ok.textContent = okLabel;
      const onKey = (e) => {
        if (e.key === 'Escape') { e.preventDefault(); finish(false); }
        else trapFocus(box, e);
      };
      const finish = (val) => {
        m.classList.remove('show');
        ok.onclick = cancel.onclick = null;
        m.removeEventListener('keydown', onKey);
        if (prevFocus) prevFocus.focus();
        resolve(val);
      };
      ok.onclick = () => finish(true);
      cancel.onclick = () => finish(false);
      m.addEventListener('keydown', onKey);
      m.classList.add('show');
      ok.focus();
    });
  }

  // Button während eines async-Calls sperren (+ „…") gegen Doppelklicks.
  async function busy(btn, fn) {
    if (!btn) return fn();
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = '…';
    try { return await fn(); }
    finally { btn.disabled = false; btn.textContent = label; }
  }

  // Leihschein-Toggle (2. Seite / Schüler-Leihschein an/aus): globaler Server-
  // State ist Quelle der Wahrheit (siehe renderStatusBar-Sync). pushSlipDefault
  // schreibt eine Änderung an den Server (broadcastet an alle Hosts + Helfer).
  const _slipCb = document.getElementById('slip-second-page');
  async function pushSlipDefault(checked) {
    try {
      await fetch('/api/settings/slip-default', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ second_page: checked }),
      });
    } catch (_) {}
  }
  // Checkbox-Werte kommen via applyState→renderStatusBar vom Server; keine
  // localStorage-Initialisierung mehr (Theme bleibt die einzige lokale Größe).

  // Einstellungen-Dialog: Tailscale-IP, PDF-lokal, Klasse-korrigieren,
  // Schüler-Leihschein + Drucker. Alle Werte kommen aus dem globalen Server-
  // State; Änderungen werden erst bei „Speichern" an den Server gepusht
  // (broadcastet an alle Host-Rechner). Abbrechen/Esc stellt die Checkboxen
  // auf den vorherigen Stand zurück. Theme/Auto-Hell-Dunkel bleibt pro Browser.
  function openSettingsDialog() {
    const modal = document.getElementById('settings-dialog');
    const box = modal.querySelector('.modal-box');
    const tsCb = document.getElementById('force-tailscale-ip');
    const pdfCb = document.getElementById('save-pdf-locally');
    const fixCb = document.getElementById('fix-class-on-slip');
    const slipCb = document.getElementById('slip-second-page');
    const printerSel = document.getElementById('printer-select');
    const saveBtn = document.getElementById('settings-dialog-save');
    const cancelBtn = document.getElementById('settings-dialog-cancel');
    const prevFocus = document.activeElement;
    // Immer mit dem Drucker-Reiter starten, statt den zuletzt gewählten Reiter zu behalten.
    document.querySelectorAll('#settings-tabs .tab').forEach(t => t.classList.toggle('active', t.id === 'settings-tab-drucker-btn'));
    document.querySelectorAll('.settings-tab-panel').forEach(p => p.classList.toggle('active', p.id === 'settings-tab-drucker'));
    // Ausgangswerte aus dem Server-State (globale Quelle der Wahrheit für alle
    // Host-Rechner). Theme/Auto-Hell-Dunkel bleibt die einzige lokale Größe
    // und taucht hier nicht auf.
    tsCb.checked = !!state.force_tailscale_ip;
    pdfCb.checked = !!state.save_pdf_locally;
    fixCb.checked = !!state.fix_class_on_slip;
    slipCb.checked = !!state.slip_second_page_default;
    const prev = { ts: tsCb.checked, pdf: pdfCb.checked, fix: fixCb.checked, slip: slipCb.checked, printer: state.printer_name || '' };
    // Druckerliste vom Server holen (rein lesend) und das Dropdown füllen.
    printerSel.innerHTML = '<option value="">wird geladen …</option>';
    printerSel.disabled = true;
    fetch('/api/printers').then(r => r.ok ? r.json() : null).then(info => {
      if (!modal.classList.contains('show')) return;  // Dialog inzwischen zu
      const names = (info && info.printers) || [];
      const current = (info && info.current) || prev.printer;
      const envDefault = (info && info.env_default) || null;
      const sysDefault = (info && info.default) || null;
      // Gar nichts gefunden (keine Geräteliste, kein System-/.env-Standard,
      // kein gesetzter Drucker): Auswahl deaktiviert lassen.
      if (!names.length && !envDefault && !sysDefault && !current) {
        printerSel.innerHTML = '<option value="">Kein Drucker gefunden</option>';
        return;
      }
      let defLabel = 'Standard';
      if (envDefault) defLabel += ` (${envDefault})`;
      else if (sysDefault) defLabel += ` (${sysDefault})`;
      printerSel.innerHTML = '';
      printerSel.add(new Option(defLabel, ''));
      for (const n of names) printerSel.add(new Option(n, n));
      // Aktuell gewählten Drucker anzeigen, auch wenn er (gerade) nicht in der Liste ist.
      if (current && !names.includes(current)) printerSel.add(new Option(`${current} (nicht gefunden)`, current));
      printerSel.value = prev.printer;
      printerSel.disabled = false;
    }).catch(() => {
      if (!modal.classList.contains('show')) return;
      printerSel.innerHTML = '<option value="">Standard (Liste nicht verfügbar)</option>';
      printerSel.disabled = false;
    });
    // Bücherlisten des Schuljahrs laden und als Reiter aufbauen (rein lesend).
    loadBooklistTabs();
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); finish(false); }
      else trapFocus(box, e);
    };
    const finish = (save) => {
      if (save) {
        if (tsCb.checked !== prev.ts) setForceTailscaleIp(tsCb.checked);
        if (pdfCb.checked !== prev.pdf) {
          pushSavePdfLocally(pdfCb.checked);  // Server-Truth setzen → Broadcast an alle Hosts
          showMsg(pdfCb.checked ? 'Drucke werden lokal als PDF gespeichert' : 'Drucke gehen wieder an den Drucker');
        }
        if (fixCb.checked !== prev.fix) {
          pushFixClassOnSlip(fixCb.checked);  // Server-Truth setzen → Broadcast an alle Hosts
          showMsg(fixCb.checked ? 'Klasse wird auf dem Leihschein korrigiert' : 'Leihschein wird wieder unverändert gedruckt');
        }
        if (slipCb.checked !== prev.slip) {
          pushSlipDefault(slipCb.checked);  // Server-Truth setzen → Broadcast an alle Hosts + Helfer
        }
        if (!printerSel.disabled && printerSel.value !== prev.printer) setPrinter(printerSel.value);
        saveChangedBooklistOrders();
      } else {
        tsCb.checked = prev.ts;
        pdfCb.checked = prev.pdf;
        fixCb.checked = prev.fix;
        slipCb.checked = prev.slip;
      }
      modal.classList.remove('show');
      saveBtn.onclick = cancelBtn.onclick = null;
      modal.removeEventListener('keydown', onKey);
      if (prevFocus) prevFocus.focus();
    };
    saveBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
    modal.addEventListener('keydown', onKey);
    modal.classList.add('show');
    saveBtn.focus();
  }

  // ---- Bücherlisten ordnen (Einstellungen-Dialog, Reiter je Jahrgang) ----
  // Analog zur Klassen-Bücher-Reihenfolge, aber jahrgangsweit und vorab: pro
  // Booklist ein Reiter, Katalog wird beim Anklicken lazy geladen. Änderungen
  // leben lokal bis „Speichern" (dann POST je geänderten Jahrgang).
  let blData = {};        // grade -> { catalog:{isbn:{title,subject}}, order:[isbn], saved:[isbn], loaded:bool }
  let blActiveGrade = null;
  let blDragIndex = null, blDropIndex = null, blDropPos = null;

  function settingsOpen() { return document.getElementById('settings-dialog').classList.contains('show'); }

  async function loadBooklistTabs() {
    const tabs = document.getElementById('bl-tabs');
    const list = document.getElementById('bl-list');
    blData = {}; blActiveGrade = null;
    tabs.innerHTML = '';
    list.innerHTML = '<div class="hint">Lade Bücherlisten…</div>';
    let info = null;
    try { const r = await fetch('/api/booklists'); if (r.ok) info = await r.json(); } catch (_) {}
    if (!settingsOpen()) return;
    const lists = (info && info.booklists) || [];
    if (!lists.length) {
      list.innerHTML = '<div class="hint">Keine Bücherlisten für das gewählte Schuljahr.</div>';
      return;
    }
    lists.forEach((bl, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'tab' + (i === 0 ? ' active' : '');
      btn.textContent = bl.title || ('Jahrgang ' + bl.grade);
      btn.dataset.grade = bl.grade;
      btn.onclick = () => selectBooklistTab(bl.grade);
      tabs.appendChild(btn);
    });
    selectBooklistTab(lists[0].grade);
  }

  async function selectBooklistTab(grade) {
    blActiveGrade = grade;
    document.querySelectorAll('#bl-tabs .tab').forEach(t =>
      t.classList.toggle('active', Number(t.dataset.grade) === grade));
    const list = document.getElementById('bl-list');
    if (!blData[grade] || !blData[grade].loaded) {
      list.innerHTML = '<div class="hint">Lade…</div>';
      let d = null;
      try {
        const r = await fetch('/api/booklist-order?grade=' + encodeURIComponent(grade));
        if (r.ok) d = await r.json();
      } catch (_) {}
      if (blActiveGrade !== grade || !settingsOpen()) return;  // Nutzer hat weitergeklickt
      const cat = {};
      ((d && d.catalog) || []).forEach(b => { cat[b.isbn] = b; });
      const order = ((d && d.order) || []).filter(isbn => cat[isbn]);
      const hidden = new Set(((d && d.hidden) || []).filter(isbn => cat[isbn]));
      blData[grade] = {
        catalog: cat, order, saved: order.slice(), loaded: true,
        hidden, savedHidden: new Set(hidden),
      };
    }
    renderBooklistList();
  }

  const ICON_EYE = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const ICON_EYE_OFF = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 11 7 11 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.53 13.53 0 0 0 1 12s4 7 11 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

  function renderBooklistList() {
    const list = document.getElementById('bl-list');
    const data = blData[blActiveGrade];
    if (!data) { list.innerHTML = ''; return; }
    if (!data.order.length) {
      list.innerHTML = '<div class="hint">Keine ausleihbaren Bücher in dieser Liste.</div>';
      return;
    }
    list.innerHTML = data.order.map((isbn, i) => {
      const b = data.catalog[isbn] || { title: isbn, subject: '' };
      const hidden = data.hidden.has(isbn);
      return `<div class="bo-row${hidden ? ' bo-hidden' : ''}" draggable="true" data-idx="${i}" data-isbn="${escapeHtml(isbn)}">`
        + `<span class="bo-grip">⠿</span>`
        + `<span class="bo-num">${i + 1}</span>`
        + `<span class="bo-fach">${escapeHtml(b.subject || '')}</span>`
        + `<span class="bo-title">${escapeHtml(b.title || isbn)}</span>`
        + `<button type="button" class="bo-hide-btn" title="${hidden ? 'Wieder einblenden' : 'Ausblenden'}" aria-label="${hidden ? 'Wieder einblenden' : 'Ausblenden'}">${hidden ? ICON_EYE_OFF : ICON_EYE}</button></div>`;
    }).join('');
    list.querySelectorAll('.bo-row').forEach(row => {
      row.addEventListener('dragstart', onBlDragStart);
      row.addEventListener('dragover', onBlDragOver);
      row.addEventListener('drop', onBlDrop);
      row.addEventListener('dragend', onBlDragEnd);
      row.querySelector('.bo-hide-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        onBlToggleHidden(row.dataset.isbn);
      });
    });
  }

  function onBlToggleHidden(isbn) {
    const data = blData[blActiveGrade];
    if (!data) return;
    if (data.hidden.has(isbn)) data.hidden.delete(isbn);
    else data.hidden.add(isbn);
    renderBooklistList();
  }

  function clearBlDropMarks() {
    document.querySelectorAll('#bl-list .bo-row').forEach(r => r.classList.remove('drop-before', 'drop-after'));
  }
  function onBlDragStart(e) {
    blDragIndex = Number(e.currentTarget.dataset.idx);
    e.currentTarget.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  }
  function onBlDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const row = e.currentTarget;
    const rect = row.getBoundingClientRect();
    const pos = (e.clientY < rect.top + rect.height / 2) ? 'before' : 'after';
    blDropIndex = Number(row.dataset.idx);
    blDropPos = pos;
    clearBlDropMarks();
    row.classList.add(pos === 'before' ? 'drop-before' : 'drop-after');
  }
  function onBlDrop(e) {
    e.preventDefault();
    const data = blData[blActiveGrade];
    if (!data || blDragIndex === null || blDropIndex === null) return;
    let target = blDropIndex + (blDropPos === 'after' ? 1 : 0);
    const from = blDragIndex;
    if (from < target) target--;
    if (target !== from) {
      const [moved] = data.order.splice(from, 1);
      data.order.splice(target, 0, moved);
    }
    blDragIndex = blDropIndex = blDropPos = null;
    renderBooklistList();
  }
  function onBlDragEnd() {
    blDragIndex = blDropIndex = blDropPos = null;
    clearBlDropMarks();
    document.querySelectorAll('#bl-list .bo-row').forEach(r => r.classList.remove('dragging'));
  }

  // Beim „Speichern" nur tatsächlich geänderte Jahrgänge an den Server schicken.
  function saveChangedBooklistOrders() {
    for (const g of Object.keys(blData)) {
      const d = blData[g];
      if (!d.loaded) continue;
      if (JSON.stringify(d.order) !== JSON.stringify(d.saved)) {
        d.saved = d.order.slice();
        saveBooklistOrder(Number(g), d.order);
      }
      const hiddenArr = [...d.hidden].sort();
      const savedHiddenArr = [...d.savedHidden].sort();
      if (JSON.stringify(hiddenArr) !== JSON.stringify(savedHiddenArr)) {
        d.savedHidden = new Set(d.hidden);
        saveBooklistHidden(Number(g), hiddenArr);
      }
    }
  }
  async function saveBooklistOrder(grade, order) {
    try {
      await fetch('/api/booklist-order', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grade, order }),
      });
    } catch (_) {}
  }
  async function saveBooklistHidden(grade, hidden) {
    try {
      await fetch('/api/booklist-hidden', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grade, hidden }),
      });
    } catch (_) {}
  }

  // ---- Tastatur-Shortcuts für den Operator (nur wenn eingeloggt) ----
  // Esc schließt das oberste Overlay; "c" fokussiert das iPad-Freischalt-Feld;
  // "n" gibt dem einzigen unbesetzten Helfer den nächsten Schüler. Nichts
  // Destruktives liegt auf einer Taste. Tipp-Eingaben werden nicht abgefangen.
  document.addEventListener('keydown', (e) => {
    const qr = document.getElementById('qr-modal');
    const confirmOpen = document.getElementById('confirm-modal').classList.contains('show');
    if (qr.classList.contains('show')) {        // QR-Modal: Esc schließt, Tab-Falle
      if (e.key === 'Escape') { e.preventDefault(); closeQr(); }
      else trapFocus(qr.querySelector('.qr-box'), e);
      return;
    }
    if (confirmOpen) return;                     // confirmDialog managt seine Tasten selbst
    if (document.getElementById('main-view').style.display === 'none') return;
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || '');
    if (typing) return;
    if (e.key === 'c') {
      const code = document.getElementById('mb-display-code');
      if (code && code.offsetParent !== null) { e.preventDefault(); code.focus(); }
    } else if (e.key === 'n') {
      const free = Object.values(state.helpers || {}).filter(h => h.student_id === null);
      if (free.length === 1) {
        const h = free[0];
        // „Nächster" zieht aus der Klasse des Helfers; ohne Bindung aus beliebigem
        // Kontext mit wartendem Schüler.
        const ctx = h.context_id ? (state.contexts || {})[h.context_id] : null;
        const hasPending = ctx
          ? (ctx.queue || []).some(s => s.status === 'pending')
          : Object.values(state.contexts || {}).some(c => (c.queue || []).some(s => s.status === 'pending'));
        if (hasPending) { e.preventDefault(); nextStudent(h.token); }
      }
    }
  });

  // ---- Event-Verkabelung ----
  // Statische Buttons/Selects: direkte addEventListener-Bindung an feste IDs.
  document.getElementById('login-btn').addEventListener('click', doLogin);
  document.getElementById('settings-btn').addEventListener('click', openSettingsDialog);
  document.getElementById('theme-btn').addEventListener('click', cycleTheme);
  document.getElementById('logout-btn').addEventListener('click', doLogout);
  document.getElementById('schoolyear-select').addEventListener('change', () => selectSchoolyear());
  document.getElementById('tab-host-btn').addEventListener('click', () => switchTab('host'));
  document.getElementById('tab-add-btn').addEventListener('click', () => switchTab('new'));
  // Klassen-Tab-Leiste: Klick auf einen Reiter wechselt, × schließt ihn.
  document.getElementById('tab-class-list').addEventListener('click', (e) => {
    const close = e.target.closest('[data-close]');
    if (close) { e.stopPropagation(); closeClass(close.dataset.close); return; }
    const tab = e.target.closest('[data-tab]');
    if (tab) switchTab(tab.dataset.tab);
  });
  // Einstellungen-Dialog: Reiter Drucker / Bücherliste / Entwicklung.
  document.getElementById('settings-tabs').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-settings-tab]');
    if (!btn) return;
    const name = btn.dataset.settingsTab;
    document.querySelectorAll('#settings-tabs .tab').forEach(t => t.classList.toggle('active', t === btn));
    document.querySelectorAll('.settings-tab-panel').forEach(p => p.classList.toggle('active', p.id === `settings-tab-${name}`));
  });
  document.getElementById('open-class-btn').addEventListener('click', () => openClass());
  document.getElementById('open-test-config-btn').addEventListener('click', () => openTestConfig());
  document.getElementById('mb-open-btn').addEventListener('click', openModusB);
  document.getElementById('mb-close-btn').addEventListener('click', closeModusB);
  document.getElementById('mb-display-code').addEventListener('keydown', (e) => { if (e.key === 'Enter') authorizeDisplay(); });
  document.getElementById('authorize-display-btn').addEventListener('click', authorizeDisplay);
  document.getElementById('show-mb-qr-btn').addEventListener('click', showMbQr);
  document.getElementById('show-display-qr-btn').addEventListener('click', showDisplayQr);
  document.getElementById('add-helper-btn').addEventListener('click', addHelper);
  document.getElementById('qr-modal').addEventListener('click', closeQr);
  document.getElementById('qr-box').addEventListener('click', (e) => e.stopPropagation());
  document.getElementById('qr-close-btn').addEventListener('click', closeQr);

  // Dynamisch per innerHTML gerenderte Buttons (Klassen-Panels + Helfer-Tabelle):
  // tragen statt onclick nur data-action/data-* Attribute. Ein delegierter
  // Click-Handler pro stabilem Container ersetzt den Inline-Aufruf 1:1.
  function handleDelegatedAction(e) {
    const el = e.target.closest('[data-action]');
    if (!el) return;
    const panel = el.closest('.class-panel');
    const id = el.dataset.ctxId || (panel && panel.dataset.ctxId);
    switch (el.dataset.action) {
      case 'cancel-arm': e.preventDefault(); cancelArm(); break;
      case 'pair': doPair(parseInt(el.dataset.studentId), el.dataset.code, el); break;
      case 'pair-select': doPair(parseInt(document.getElementById(el.dataset.selId).value), el.dataset.code, el); break;
      case 'finish': finishStudent(parseInt(el.dataset.studentId)); break;
      case 'print': printLoanSlip(parseInt(el.dataset.studentId), el); break;
      case 'next-student': nextStudent(el.dataset.token); break;
      case 'remove-helper': removeHelper(el.dataset.token); break;
      case 'pair-student': pairStudent(parseInt(el.dataset.studentId)); break;
      case 'skip': skipStudent(parseInt(el.dataset.studentId)); break;
      case 'disconnect': disconnectStudent(parseInt(el.dataset.studentId)); break;
      case 'clear-book-alert': clearBookAlert(parseInt(el.dataset.studentId)); break;
      case 'ctx-reset': ctxResetQueue(id); break;
      case 'ctx-clear': ctxClearQueue(id); break;
      case 'ctx-disconnect-all': ctxDisconnectAll(id); break;
      case 'ctx-add-student': ctxAddSingleStudent(id); break;
    }
  }
  document.getElementById('class-panels').addEventListener('click', handleDelegatedAction);
  document.getElementById('class-panels').addEventListener('change', (e) => {
    const el = e.target;
    if (el.classList.contains('ctx-single-class')) ctxLoadStudents(el.dataset.ctxId);
    else if (el.classList.contains('ctx-single-student')) ctxOnStudentChange(el.dataset.ctxId);
  });
  document.getElementById('helper-tbody').addEventListener('click', handleDelegatedAction);
  document.getElementById('helper-tbody').addEventListener('change', (e) => {
    const el = e.target;
    if (el.classList.contains('helper-class-sel')) setHelperClass(el.dataset.token, el.value);
  });

  // Beim Laden prüfen ob bereits eingeloggt
  fetch('/api/state').then(r => {
    if (r.ok) {
      document.getElementById('login-view').style.display = 'none';
      document.getElementById('main-view').style.display = '';
      r.json().then(s => { applyState(s); });
      loadSchoolyears();
      loadClasses();
      loadAutoDoneSelection();
      connectWs();
      // Dev-Toggles kommen via WS vom Server (globale Quelle), kein Browser-Push
      // beim Auto-Login mehr. Theme bleibt pro Browser (localStorage).
      // Auto-Login hat keine Login-Geste -> AudioContext beim ersten Klick entsperren
      document.addEventListener('pointerdown', () => initAudio(), { once: true });
    }
  });
