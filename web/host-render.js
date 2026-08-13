// web/host-render.js — DOM-Rendering + Event-Verkabelung
// Teil des host.html-Frontends (siehe host-state.js/host-ws.js/host-render.js,
// in dieser Reihenfolge nach common.js eingebunden). Kein Build-Step: alle drei
// Dateien teilen sich eine gemeinsame Top-Level-Scope (klassische <script>-Tags),
// zusätzlich exponiert auf window.__host für Debug-/Introspektionszwecke.

window.__host = window.__host || {};

  // ---- Audio (Beep bei neuem Pairing-Code) — initAudio/playBeep: siehe common.js (Beeper) ----
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
  // Druckersymbol für die Leihschein-Buttons — dasselbe SVG wie im Helfer-Client
  // (scan.html #print-btn), nur hier statt dem Wort „Leihschein".
  const ICON_PRINTER = '<svg class="ico ico-lg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/></svg>';
  // Zielflagge für „Abschließen": nur das große 4x4-Karomuster, ohne Mast.
  const ICON_ACTION_CHECK = '<svg class="ico ico-lg" viewBox="0 0 24 24" fill="currentColor"><rect x="2" y="2" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1"/><rect x="2" y="2" width="5" height="5"/><rect x="12" y="2" width="5" height="5"/><rect x="7" y="7" width="5" height="5"/><rect x="17" y="7" width="5" height="5"/><rect x="2" y="12" width="5" height="5"/><rect x="12" y="12" width="5" height="5"/><rect x="7" y="17" width="5" height="5"/><rect x="17" y="17" width="5" height="5"/></svg>';
  // WLAN-Symbol für „Trennen", durchgestrichen mit einer Diagonale von
  // links unten nach rechts oben.
  const ICON_DISCONNECT = '<svg class="ico ico-lg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/><line x1="3" y1="21" x2="21" y2="3" stroke-width="2.4"/></svg>';
  const ICON_SIGN = '<svg class="ico ico-lg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 3.5a2.1 2.1 0 0 1 3 3L8 16 4 17l1-4Z"/><line x1="12.5" y1="5.5" x2="15.5" y2="8.5"/><path d="M2 20.5c1.3-1.7 2.2-1.5 3-.3.7 1 .6 1.8 1.8 1.3 1.6-.6 1.8-2.2 3.5-1.7.9.3 1.1 1.1 2 1L20.5 17.2"/></svg>';
  // Zettel (Barcode + Bücherliste) erneut drucken: Blatt mit Eselsohr oben
  // links (Grundform wie `docLetterIcon` in scan-render.js, aber gespiegelt
  // — die Antrags-Icons dort knicken oben rechts), Barcode oben rechts
  // davon (fünf schmale Striche) und darunter zwei gleich lange
  // Listenzeilen mit sichtbarem Abstand zum Häkchen, das NUR die untere
  // trägt (Strich allein wirkt sonst wie zwei gleichrangige
  // „erledigt"-Zeilen) — spiegelt den echten Zettel (Barcode + Abhak-Liste,
  // s. `server/scan_station.py::build_sheet_pdf`). Höhe (y2–22 im 24er
  // viewBox) deckungsgleich mit `ICON_ACTION_CHECK`/`ICON_PRINTER`; die
  // Breite ist bewusst schmaler als die anderen Icons (x5–19 statt x2–22)
  // — Seitenverhältnis ~1:1,43, angelehnt an ein echtes A4-Blatt (1:√2).
  const ICON_SHEET = '<svg class="ico ico-lg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.8 2H8.5l-3.5 3.5V20.8a1.2 1.2 0 0 0 1.2 1.2H17.8a1.2 1.2 0 0 0 1.2-1.2V3.2a1.2 1.2 0 0 0-1.2-1.2Z"/><path d="M8.5 2v3.5h-3.5"/><g stroke-width="1.3"><line x1="10.4" y1="6.5" x2="10.4" y2="10"/><line x1="11.7" y1="6.5" x2="11.7" y2="10"/><line x1="13" y1="6.5" x2="13" y2="10"/><line x1="14.3" y1="6.5" x2="14.3" y2="10"/><line x1="15.6" y1="6.5" x2="15.6" y2="10"/></g><line x1="8" y1="14" x2="13" y2="14"/><line x1="8" y1="18.5" x2="13" y2="18.5"/><polyline points="14.7,18.8 15.8,20.0 17.7,17.6"/></svg>';
  // Schließen-X für den „Code verwerfen"-Button neben „Zuordnen" (Modus B).
  // Dasselbe X-Pfad-SVG wie beim Helfer-Entfernen-Button (s. renderHelpers).
  const ICON_CLOSE = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
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
      Beeper.initAudio();  // Login-Klick ist die Nutzergeste, die den AudioContext entsperrt
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
      const pend = (c.queue || []).filter(s => s.status === 'pending' || s.status === 'absent').length;
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
        <details class="setup-col">
          <summary>Klasseneinstellungen</summary>
          <div class="card">
            <h2 style="margin:0 0 8px">Drucker für ${form}</h2>
            <div data-ctx-printers="${id}" style="display:flex;flex-direction:column;gap:10px"></div>
            <p class="hint" style="margin-top:8px">Auf welche Drucker der Leihschein dieser Klasse gedruckt wird. Kein Haken = kein Drucker (Leihschein-Druck nur per manueller Auswahl).</p>
            <p class="ctx-coupling-warn" data-ctx-printer-warn="${id}" style="display:none"></p>
            <h2 style="margin:4px 0 8px;border-top:1px solid var(--border);padding-top:12px">Live-Ausgabe für ${form}</h2>
            <label class="switch" style="margin-top:10px" title="Modus B (Live-Ausgabe) für diese Klasse ein/aus. Aus = kein Modus-B-Kasten in dieser Klassenansicht, keine Pairing-Zuordnung.">
              <input type="checkbox" data-ctx-live="${id}">
              <span class="track"></span>
              Live-Ausgabe (Modus B) aktivieren
            </label>
            <p class="ctx-coupling-warn" data-ctx-live-warn="${id}" style="display:none"></p>
            <p class="hint" style="margin-top:8px">Schüler können sich per iPad selbst zum Scannen einreihen. Ausgeschaltet bleibt der Modus-B-Kasten in dieser Ansicht ausgeblendet.</p>
            <div data-ctx-done-opts="${id}" style="margin-top:14px;display:flex;flex-direction:column;gap:10px">
              <label class="slip-trigger-line" for="ctx-slip-trigger-${id}" title="Wann der Leihschein dieser Klasse gedruckt wird. Nur bei aktiver Live-Ausgabe.">
                <span>Leihschein Druck:</span>
                <select id="ctx-slip-trigger-${id}" data-ctx-slip-trigger="${id}">
                  <option value="auto">Automatisch</option>
                  <option value="student">Schülerauslöser</option>
                  <option value="helper">Betreuerauslöser</option>
                  <option value="barcode">Barcode</option>
                </select>
              </label>
              <label class="check-line" title="Schüler erst als fertig markieren, wenn der Leihschein unterschrieben ist — sonst bereits nach dem Drucken. Nur bei aktiver Live-Ausgabe.">
                <input type="checkbox" data-ctx-done-signed="${id}">
                <span>Leihschein unterschreiben</span>
              </label>
              <label class="check-line" title="Der unterschriebene Leihschein wird vom Lehrer eingesammelt. Nur relevant, wenn „Leihschein unterschreiben" angehakt ist.">
                <input type="checkbox" data-ctx-done-collected="${id}">
                <span>Leihschein wird vom Lehrer eingesammelt</span>
              </label>
            </div>
          </div>
          <div class="card">
            <h2 style="margin:0 0 8px">Schüler hinzufügen</h2>
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
          <div class="card now-serving" data-ctx-ns="${id}"></div>
          <div class="card" data-ctx-mb="${id}">
            <h2 style="margin:0 0 8px">Pairing (Modus B)</h2>
            <div class="ctx-arm-banner mb-arm-banner" data-ctx-id="${id}"></div>
            <div class="ctx-codes" data-ctx-id="${id}"></div>
            <div class="ctx-station" data-ctx-id="${id}"></div>
          </div>
          <div class="card">
            <h2 style="margin:0 0 8px">Lehrkraft-Ansicht</h2>
            <div class="ctx-teacher-body" data-ctx-id="${id}"></div>
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

  // Per-Klassen-Sperre während des Ladens: ein erneutes „Öffnen" für DIESELBE
  // Klasse wird ignoriert, bis ihr Request abgeschlossen ist — die Klasse soll
  // sich nicht vorzeitig (z. B. mit 0 Schülern) als „geladen" zeigen. Andere
  // Klassen lassen sich daneben weiter öffnen (kein globaler Button-Lock,
  // „Öffnen" bleibt für andere Klassen drückbar).
  const openingForms = new Set();

  async function openClass(force = false) {
    const form = document.getElementById('new-class-select').value;
    if (!form) return;
    if (openingForms.has(form)) return;  // diese Klasse lädt bereits
    openingForms.add(form);
    const release = () => openingForms.delete(form);
    const auto_done = getAutoDoneSelection();
    localStorage.setItem(AUTO_DONE_STORAGE_KEY, JSON.stringify(auto_done));
    const printers = getSelectedClassPrinters();
    saveClassPrintersSelection(getSelectedClassPrinterNames());
    const liveAusgabe = !!document.getElementById('new-class-live-ausgabe')?.checked;
    saveClassLiveAusgabe(liveAusgabe);
    const slipTrigger = document.getElementById('new-class-slip-trigger')?.value || 'auto';
    saveClassSlipTrigger(slipTrigger);
    const doneSigned = !!document.getElementById('new-class-done-signed')?.checked;
    saveClassDoneSigned(doneSigned);
    const doneCollected = !!document.getElementById('new-class-done-collected')?.checked;
    saveClassDoneCollected(doneCollected);
    // Ein einziger persistenter Toast: steht während des gesamten Ladens
    // („Lade Klasse …") und wird in-place zum Abschluss-Hinweis, sobald die
    // Klasse geladen ist. So gibt es nie zwei gleichzeitig sichtbare Toasts.
    const loadToast = showMsgPersistent(`Lade ${form}…`);
    let r, d;
    try {
      r = await fetch('/api/open-class', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ form, force, auto_done, printers, live_ausgabe: liveAusgabe, slip_trigger: slipTrigger, done_signed: doneSigned, done_collected: doneCollected }) });
      d = await r.json();
    } catch (err) {
      finalizeToast(loadToast, 'Fehler beim Laden der Klasse');
      release();
      return;
    }
    if (r.status === 409 && d.detail && d.detail.reason === 'active_sessions') {
      // Vor dem Bestätigungsdialog Sperre + Lade-Toast aufheben, damit der
      // rekursive Aufruf („Trotzdem öffnen") sauber neu starten kann.
      dismissToast(loadToast);
      release();
      if (await confirmDialog(`${d.detail.msg}\n\nTrotzdem öffnen?`, 'Öffnen')) return openClass(true);
      showMsg('Öffnen abgebrochen');
      return;
    }
    if (!r.ok) {
      // 409 reason 'loading' (Klasse noch am Laden) oder anderer Fehler: keine
      // „geladen"-Meldung, nur den Fehlertext zeigen.
      finalizeToast(loadToast, d.detail?.msg || d.detail || 'Fehler');
      release();
      return;
    }
    const id = d.context_id;
    // Optimistisch lokal anzeigen, bevor der WS-Broadcast eintrifft (snappy
    // UX). applyState leitet tabOrder anschließend aus state.contexts ab und
    // rekonziliert diese Vorausnahme — global bleibt der Server der Truth.
    if (!tabOrder.includes(id)) tabOrder.push(id);
    // Gleicher Toast-Element, neuer Text — kein zweiter „Klasse …"-Toast
    // daneben. Auto-dismissed nach 4 s wie ein normaler Hinweis.
    finalizeToast(loadToast, `${form} geladen — ${d.count} Schüler`);
    release();
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

  // Übersprungener Schüler: Einmal-QR erzeugen, mit dem ein Helfer die Bücher
  // des Schülers stellvertretend einscannt (QR-Modal wie beim Pairing).
  async function helperScan(studentId) {
    const r = await fetch('/api/helper-scan/start', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { showMsg(d.detail || 'QR-Erzeugung fehlgeschlagen'); return; }
    showQr(d.qr, d.url);
  }

  // Einzelnen Schüler trennen: Helfer-/Schüler-Verbindung lösen, zurück auf "Wartend".
  async function disconnectStudent(studentId) {
    const r = await fetch('/api/disconnect', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Trennen fehlgeschlagen'); }
  }

  async function finishStudent(studentId) {
    await fetch('/api/finish', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId }) });
  }

  async function finishSignedStudent(studentId) {
    const r = await fetch('/api/finish-signed', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ student_id: studentId }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showMsg(d.detail || 'Unterschrift konnte nicht bestätigt werden');
    }
  }

  // Drucker-Pool aus dem lokalen Snapshot, für `mountPrinterPicker` (Druck-
  // Dialog + Nachfrage-Toast bei feststeckenden Aufträgen) — inkl. `faulty`.
  function hostPrinterPool() {
    return (state.printers || []).map(
      p => ({id: p.id, name: p.name, label: p.label, is_default: p.is_default, faulty: p.faulty})
    );
  }

  // Öffnet den Druck-Dialog und gibt die gewählte „second_page"-Option + die
  // ausgewählten Drucker-IDs zurück (`{mode:'print', second_page, printers}`),
  // oder null wenn Abbrechen gedrückt wurde. Vorauswahl = erlaubte Drucker
  // der Klasse des Schülers (`None` = alle Pool-Drucker); kein
  // Klassen-Kontext → leer.
  // `opts.sheet` = Scan-Station-Zettel statt Leihschein: der Zettel ist
  // einseitig, deshalb entfällt die „2. Seite"-Option (`second_page` bleibt
  // dann immer false). Zwei Zettel-Varianten, je nach Aufrufweg:
  //   - `opts.reprint` falsch (Pairing-Kasten, `renderCtxStationPrint`, nur
  //     wartende Schüler): DREI Knöpfe — „Erstellen und Drucken" (OK),
  //     „Erstellen" (nur aktivieren, kein Druck, löst direkt
  //     `{mode:'activate'}` aus, braucht keine Druckerauswahl), „Abbrechen".
  //   - `opts.reprint` wahr (Nachdruck-Knopf im „Aktuell in
  //     Ausgabe"-Kästchen, immer schon aktive Schüler mit Code): NUR
  //     „Drucken" (OK) + „Abbrechen" — der Zettel-Code existiert ja schon,
  //     „Erstellen" ohne Druck ergibt hier keinen Sinn. Der Server behält in
  //     BEIDEN Fällen denselben Zettel-Code (`AppState.allocate_station_
  //     code` — stabil pro Schüler), holt aber bei jedem Aufruf die
  //     Bücherliste frisch von IServ, s. `sessions._load_and_activate_
  //     station_student`.
  function openPrintDialog(studentId, opts) {
    const sheet = !!(opts && opts.sheet);
    const reprint = sheet && !!(opts && opts.reprint);
    return new Promise(resolve => {
      const modal = document.getElementById('print-dialog');
      const box = modal.querySelector('.modal-box');
      const slipCb = document.getElementById('print-dialog-slip');
      const slipRow = document.getElementById('print-dialog-slip-row');
      const reactivateCb = document.getElementById('print-dialog-reactivate');
      const reactivateRow = document.getElementById('print-dialog-reactivate-row');
      const reactivateText = document.getElementById('print-dialog-reactivate-text');
      const titleEl = document.getElementById('print-dialog-title');
      titleEl.textContent = sheet
        ? (reprint ? 'Zettel für Scan-Station drucken' : 'Zettel für Scan-Station erstellen')
        : 'Leihschein drucken';
      slipRow.style.display = sheet ? 'none' : '';
      // Checkbox „Alten Code reaktivieren": nur beim ersten Erstellen nach
      // einem „Trennen" relevant — der Schüler hatte einen entwerteten
      // Zettel-Code (`station_reactivate_code`, s. `AppState.
      // station_reactivate_code`). Beim Nachdruck (reprint) ist der Code
      // ohnehin schon aktiv, dort erscheint die Checkbox nicht.
      const reactivateCode = sheet && !reprint
        ? (findStudentInState(studentId) || {}).station_reactivate_code
        : null;
      reactivateRow.style.display = reactivateCode ? '' : 'none';
      reactivateCb.checked = true;
      if (reactivateCode) reactivateText.textContent = `Alten Code (${reactivateCode}) reaktivieren`;
      const okBtn = document.getElementById('print-dialog-ok');
      const activateBtn = document.getElementById('print-dialog-activate-only');
      const cancelBtn = document.getElementById('print-dialog-cancel');
      const pickerErrEl = document.getElementById('print-dialog-picker-error');
      const pickerEl = document.getElementById('print-dialog-picker');
      const prevFocus = document.activeElement;
      slipCb.checked = !!document.getElementById('slip-second-page')?.checked;
      pickerErrEl.textContent = '';
      pickerErrEl.style.display = 'none';
      okBtn.textContent = reprint ? 'Drucken' : (sheet ? 'Erstellen und Drucken' : 'Drucken');
      activateBtn.hidden = !sheet || reprint;

      // Pool + Vorauswahl aus dem lokalen Snapshot.
      const pool = hostPrinterPool();
      const ctxId = findCtxOfStudent(studentId);
      const allowed = ctxId ? (state.contexts[ctxId] || {}).allowed_printers : undefined;
      // ctxId vorhanden, allowed === null (Klasse erlaubt alle) → alle Pool-
      // Drucker; allowed === [ids] → genau diese; kein ctx → leer.
      const selected = (ctxId && allowed === null) ? pool.map(p => p.id)
        : (ctxId && Array.isArray(allowed)) ? allowed : [];
      const picker = mountPrinterPicker(pickerEl, pool, selected);

      const onKey = (e) => {
        if (e.key === 'Escape') { e.preventDefault(); finish(null); }
        else trapFocus(box, e);
      };
      const finish = (val) => {
        modal.classList.remove('show');
        okBtn.onclick = activateBtn.onclick = cancelBtn.onclick = null;
        modal.removeEventListener('keydown', onKey);
        if (prevFocus) prevFocus.focus();
        resolve(val);
      };
      okBtn.onclick = () => {
        const ids = picker.getSelectedIds();
        if (!ids.length) {
          pickerErrEl.textContent = 'Bitte mindestens einen Drucker auswählen.';
          pickerErrEl.style.display = '';
          return;
        }
        finish({
          mode: 'print', second_page: !sheet && slipCb.checked, printers: ids,
          reactivate_old_code: reactivateCb.checked,
        });
      };
      activateBtn.onclick = () => finish({mode: 'activate', reactivate_old_code: reactivateCb.checked});
      cancelBtn.onclick = () => finish(null);
      modal.addEventListener('keydown', onKey);
      modal.classList.add('show');
      okBtn.focus();
    });
  }

  async function printLoanSlip(studentId, btn) {
    const choice = await openPrintDialog(studentId);
    if (choice === null) return;
    await busy(btn, async () => {
      // Der Druck geht durch die server-interne Druckerwarteschlange; der
      // Endpoint blockiert bis „gedruckt"/Fehler. Live-Popup (Position /
      // „wird gedruckt" / „gedruckt") kommt via WS (showPrintProgress/
      // showPrintResult) — nur hier am startenden Host. Die HTTP-Antwort ist
      // Rückversicherung für den Fall, dass der WS gerade nicht live ist.
      // Ob der Auftrag als Betreuerauslöser-Schüler-Auftrag zählt (kein
      // eigener host_sid, dafür student_token → Status/Originator wie beim
      // Schüler selbst), entscheidet ausschließlich der Server aus dem
      // State (s. `print_loan_slip`) — kein Client-Flag mehr nötig.
      const r = await fetch('/api/print-loan-slip', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ student_id: studentId, second_page: choice.second_page, printers: choice.printers }) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showMsg(d.detail || 'Druck fehlgeschlagen', 'warn');
      }
      // Bei r.ok liefert der WS das Popup; nichts weiter tun.
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
  async function toggleModusBPause(btn) {
    if (!state.modus_b || !state.modus_b.open || !btn) return;
    btn.disabled = true;
    btn.innerHTML = '…';
    try {
      const r = await fetch('/api/modus-b/pause', { method: 'POST' });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showMsg(d.detail || 'QR-Pause konnte nicht geändert werden');
        return;
      }
      const d = await r.json();
      // Das WS-Snapshot folgt ebenfalls; die direkte Aktualisierung sorgt
      // trotzdem für eine sofortige Button-Rückmeldung.
      state.modus_b.paused = !!d.paused;
    } finally {
      btn.disabled = false;
      renderModusBControl();
    }
  }
  async function allowThreeModusBScans(btn) {
    if (!state.modus_b || !state.modus_b.open || !state.modus_b.paused || !btn) return;
    await busy(btn, async () => {
      const r = await fetch('/api/modus-b/allow-scans', { method: 'POST' });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showMsg(d.detail || 'Scans konnten nicht freigeschaltet werden');
        return;
      }
      const d = await r.json();
      state.modus_b.paused = !!d.paused;
    });
    renderModusBControl();
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
  // iPad-Display per Klick auf einen Eintrag der Host-Freischalt-Liste
  // freischalten (kein Tippen des Registrierungscodes mehr — wie beim
  // Drucker-Display, s. enablePrinterDisplay). Der Code dient dort nur noch
  // dem visuellen Abgleich mit dem iPad-Bildschirm.
  async function authorizeDisplay(displayId, btn) {
    if (!displayId) return;
    const call = async () => {
      const r = await fetch('/api/display/authorize', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ display_id: displayId }) });
      if (r.ok) showMsg('iPad freigeschaltet');
      else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Freischalten fehlgeschlagen'); }
    };
    if (btn) await busy(btn, call); else await call();
  }
  // iPad-Display bewusst trennen. Der Server entfernt die flüchtige Session
  // und weist die QR-Seite an, nicht sofort mit einem neuen Code zu reconnecten.
  async function disconnectDisplay(displayId, btn) {
    if (!displayId) return;
    if (!await confirmDialog('iPad-Display wirklich trennen?', 'Trennen')) return;
    await busy(btn, async () => {
      const r = await fetch('/api/display/disconnect', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ display_id: displayId }),
      });
      if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Trennen fehlgeschlagen'); }
    });
  }
  // QR, mit dem ein iPad die Display-Seite (/qr-display) öffnet.
  async function showDisplayQr() {
    const r = await fetch('/api/display/qr');
    if (!r.ok) { showMsg('QR für QR-Display konnte nicht geladen werden'); return; }
    const d = await r.json();
    // Scannt das iPad den QR, verbindet sich ein neues Display -> displays-Liste wächst.
    qrWatch = { kind: 'display', baseline: ((state.modus_b && state.modus_b.displays) || []).length };
    showQr(d.qr, d.url || '');
  }
  // QR, mit dem ein Gerät die Drucker-Display-Seite (/drucker-display) öffnet
  // (Basis-URL ohne Token → frisches Display beim Scannen, „+"-Reiter).
  async function showPrinterDisplayQr() {
    const r = await fetch('/api/drucker-display/qr');
    if (!r.ok) { showMsg('QR für Druckerdisplay konnte nicht geladen werden'); return; }
    const d = await r.json();
    showQr(d.qr, d.url || '');
  }
  // QR für ein konkretes Display (URL inkl. ?token=…) — öffnet dasselbe Display
  // wieder, ein Reload wiederverwendet die Session (QR-Button im Panel).
  async function showPdTokenQr(displayId) {
    const r = await fetch(`/api/drucker-display/qr?display_id=${encodeURIComponent(displayId)}`);
    if (!r.ok) { showMsg('QR für Druckerdisplay konnte nicht geladen werden'); return; }
    const d = await r.json();
    showQr(d.qr, d.url || '');
  }
  // Drucker-Display durch Eingabe eines Namens freischalten (Einschalten-Button
  // im unautorisierten Panel). Der Registrierungs-Code wird nur noch auf dem
  // Display + im Reiter gezeigt (visuelle Zuordnung), nicht mehr am Host getippt.
  async function enablePrinterDisplay(displayId, label, btn) {
    if (!label || !label.trim()) return;
    if (btn) await busy(btn, async () => {
      const r = await fetch('/api/drucker-display/enable', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ display_id: displayId, label: label.trim() }),
      });
      if (r.ok) showMsg('Drucker-Display freigeschaltet');
      else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Freischaltung fehlgeschlagen'); }
    });
  }
  // Aktuelle geordnete Drucker-Liste eines Displays (aus dem State): bei
  // `assigned_printer_ids === null` (Default = alle) die Pool-Drucker in
  // Pool-Reihenfolge, sonst die gespeicherte Liste (verwaiste IDs heraus).
  function _pdCurrentIds(displayId) {
    const d = (state.printer_displays || []).find(x => x.display_id === displayId);
    if (!d) return [];
    const pool = state.printers || [];
    if (d.assigned_printer_ids === null) return pool.map(p => p.id);
    const byId = {};
    pool.forEach(p => { byId[p.id] = true; });
    return d.assigned_printer_ids.filter(pid => byId[pid]);
  }
  // Geordnete Drucker-Liste eines Displays setzen. Die volle Liste geht an
  // /assign; der Server übernimmt Reihenfolge + Dedup + Pool-Filter.
  async function assignPdPrinters(displayId, printerIds) {
    const r = await fetch('/api/drucker-display/assign', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_id: displayId, printer_ids: printerIds }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Zuweisung fehlgeschlagen'); }
  }
  // Drucker-Box hinzufügen: PID ans Ende der aktuellen Liste anhängen.
  async function addPdPrinter(displayId, pid) {
    const ids = _pdCurrentIds(displayId);
    if (ids.includes(pid)) return;  // Duplikat verhindern
    await assignPdPrinters(displayId, [...ids, pid]);
  }
  // Drucker-Box entfernen: PID aus der Liste streichen.
  async function removePdPrinter(displayId, pid) {
    await assignPdPrinters(displayId, _pdCurrentIds(displayId).filter(x => x !== pid));
  }
  // Scanner-Boxen eines Displays — Spiegel der Drucker-Varianten oben, gegen
  // `/api/drucker-display/assign-scanners` statt `/assign`.
  function _pdCurrentScannerIds(displayId) {
    const d = (state.printer_displays || []).find(x => x.display_id === displayId);
    if (!d) return [];
    const pool = state.printer_scanners || [];
    if (d.assigned_scanner_ids === null) return pool.map(s => s.scanner_id);
    const byId = {};
    pool.forEach(s => { byId[s.scanner_id] = true; });
    return d.assigned_scanner_ids.filter(sid => byId[sid]);
  }
  async function assignPdScanners(displayId, scannerIds) {
    const r = await fetch('/api/drucker-display/assign-scanners', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_id: displayId, scanner_ids: scannerIds }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Zuweisung fehlgeschlagen'); }
  }
  async function addPdScanner(displayId, sid) {
    const ids = _pdCurrentScannerIds(displayId);
    if (ids.includes(sid)) return;
    await assignPdScanners(displayId, [...ids, sid]);
  }
  async function removePdScanner(displayId, sid) {
    await assignPdScanners(displayId, _pdCurrentScannerIds(displayId).filter(x => x !== sid));
  }
  // Gemeinsame Drucker+Scanner-Reihenfolge setzen (`item_order`, eine Box-
  // Reihe statt zweier getrennter Abschnitte — bestimmt auch die Spalten-
  // reihenfolge am physischen Drucker-Display).
  async function assignPdItemOrder(displayId, itemOrder) {
    const r = await fetch('/api/drucker-display/reorder-items', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_id: displayId, item_order: itemOrder }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Reihenfolge konnte nicht gespeichert werden'); }
  }
  // Box (Drucker ODER Scanner) per Drag umsortieren: dragKey vor/unter
  // targetKey einsortieren — die aktuell im DOM sichtbare Reihenfolge (beide
  // Kinds gemeinsam) ist die Ausgangsbasis, damit ein Drag genau die
  // sichtbare Reihe verschiebt, egal ob Drucker oder Scanner beteiligt sind.
  function reorderPdBoxItems(displayId, dragKind, dragId, targetKind, targetId) {
    const host = document.querySelector(`.pdd-panel[data-display="${CSS.escape(displayId)}"]`);
    const keys = host
      ? Array.from(host.querySelectorAll('.pd-box[data-kind][data-pid]'))
        .map(b => `${b.dataset.kind}:${b.dataset.pid}`)
      : [];
    const dragKey = `${dragKind}:${dragId}`;
    const targetKey = `${targetKind}:${targetId}`;
    const withoutDrag = keys.filter(k => k !== dragKey);
    const to = withoutDrag.indexOf(targetKey);
    withoutDrag.splice(to + 1, 0, dragKey);
    assignPdItemOrder(displayId, withoutDrag);
  }
  // Anzeige-Label eines Drucker-Scanners: Name (falls gesetzt), sonst
  // Registrierungs-Code/Short-ID (Mirror printerLabel).
  function scannerLabel(s) {
    return (s.label && s.label.trim()) ? s.label.trim() : (s.registration_code || s.scanner_id.slice(0, 6));
  }
  // Display-Name setzen (Name-Feld + Speichern / Enter).
  async function setPdLabel(displayId, label) {
    const r = await fetch('/api/drucker-display/label', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_id: displayId, label: label || '' }),
    });
    if (r.ok) showMsg('Display-Name gespeichert');
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Name konnte nicht gespeichert werden'); }
  }
  // Theme-Schieberegler: Hell/Dunkel auf dem Display.
  async function setPdTheme(displayId, dark) {
    const r = await fetch('/api/drucker-display/theme', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_id: displayId, theme: dark ? 'dark' : 'light' }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Theme konnte nicht gesetzt werden'); }
  }
  // Drucker-Display verbieten (× am Reiter, endgültig): Token wird gebannt,
  // das Display zeigt „gesperrt" und ein Reload bleibt gesperrt. Bestätigungs-
  // dialog im Caller (nicht reaktivierbar).
  async function forgetPrinterDisplay(displayId) {
    const r = await fetch('/api/drucker-display/forget', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ display_id: displayId }),
    });
    if (r.ok) { activePdTab = null; showMsg('Display verboten'); }
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Verbieten fehlgeschlagen'); }
  }
  // ---- Scan-Station: API-Aufrufe ----
  // QR/URL für eine neue Station („+"-Reiter, Basis-URL ohne Token) bzw. für
  // eine konkrete Station (QR-Button im Panel, URL inkl. ?token=…).
  async function showScanStationQr(stationId) {
    const q = stationId ? `?station_id=${encodeURIComponent(stationId)}` : '';
    const r = await fetch(`/api/scan-station/qr${q}`);
    if (!r.ok) { showMsg('QR für Scan-Station konnte nicht geladen werden'); return; }
    const d = await r.json();
    showQr(d.qr, d.url || '');
  }
  async function ssPost(path, body, okMsg, failMsg) {
    const r = await fetch(`/api/scan-station/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (r.ok) { if (okMsg) showMsg(okMsg); return true; }
    const d = await r.json().catch(() => ({}));
    showMsg(d.detail || failMsg);
    return false;
  }
  async function enableScanStation(stationId, label, btn) {
    if (!label || !label.trim()) return;
    if (btn) await busy(btn, () =>
      ssPost('enable', { station_id: stationId, label: label.trim() },
        'Scan-Station freigeschaltet', 'Freischaltung fehlgeschlagen'));
  }
  const setSsLabel = (stationId, label) =>
    ssPost('label', { station_id: stationId, label: label || '' },
      'Stationsname gespeichert', 'Name konnte nicht gespeichert werden');
  const setSsTheme = (stationId, dark) =>
    ssPost('theme', { station_id: stationId, theme: dark ? 'dark' : 'light' },
      null, 'Theme konnte nicht gesetzt werden');
  // Eingabeart der Station (wie im Helferclient): Kamera oder Manuell.
  const setSsInputMode = (stationId, manual) =>
    ssPost('input-mode', { station_id: stationId, input_mode: manual ? 'manual' : 'camera' },
      null, 'Eingabeart konnte nicht gesetzt werden');
  const releaseSsStudent = (stationId) =>
    ssPost('release', { station_id: stationId }, 'Station freigegeben', 'Freigeben fehlgeschlagen');
  // Station verbieten (× am Reiter, endgültig — Bestätigungsdialog im Caller).
  async function forgetScanStation(stationId) {
    if (await ssPost('forget', { station_id: stationId }, 'Station verboten',
        'Verbieten fehlgeschlagen')) {
      activeSsTab = null;
    }
  }
  // Zettel erstellen/(nach-)drucken: gleicher Druck-Dialog wie beim
  // Leihschein (Druckerauswahl), nur ohne die „2. Seite"-Option (der Zettel
  // ist einseitig). `reprint = true` (Nachdruck-Knopf im „Aktuell in
  // Ausgabe"-Kästchen, immer schon aktiver Schüler mit Code) zeigt nur
  // „Drucken"/„Abbrechen" — dort ergibt ein separates „Erstellen" ohne
  // Druck keinen Sinn. `reprint = false` (Pairing-Kasten, wartender
  // Schüler) zeigt zusätzlich „Erstellen" (`choice.mode === 'activate'`):
  // aktiviert den Schüler (Zettel-Code/Fortschritt) OHNE zu drucken — der
  // physische Druck kann jederzeit später über den Nachdruck-Knopf
  // nachgeholt werden. In beiden Fällen behält der Zettel-Code über
  // beliebig viele Aufrufe hinweg denselben Wert (`AppState.
  // allocate_station_code` — stabil pro Schüler, ein älterer Zettel bleibt
  // gültig); nur die Bücherliste wird bei jedem Aufruf frisch geholt.
  async function printStationSheet(studentId, btn, { reprint = false } = {}) {
    const choice = await openPrintDialog(studentId, { sheet: true, reprint });
    if (choice === null) return;
    await busy(btn, async () => {
      if (choice.mode === 'activate') {
        const r = await fetch('/api/scan-station/activate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ student_id: studentId, reactivate_old_code: choice.reactivate_old_code !== false }),
        });
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          showMsg(d.detail || 'Erstellen fehlgeschlagen', 'warn');
        }
        return;
      }
      const r = await fetch('/api/scan-station/print-sheet', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          student_id: studentId, printers: choice.printers,
          reactivate_old_code: choice.reactivate_old_code !== false,
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showMsg(d.detail || 'Druck fehlgeschlagen', 'warn');
      }
      // Bei r.ok liefert der WS das Fortschritts-Popup (wie beim Leihschein).
    });
  }

  // „+"-Box der Drucker-/Scanner-Boxen: kleines Popover der noch nicht
  // zugewiesenen Pool-Drucker bzw. autorisierten Scanner (`kind`). Auswahl →
  // addPdPrinter/addPdScanner. Schließt bei Außenklick/Esc. Liegt auf
  // document.body (überlebt Re-Renders des Panels) und räumt sich selbst.
  function openPdAddMenu(displayId, plusBox, kind = 'printer') {
    // Alte Popovers entfernen (nur eines gleichzeitig).
    document.querySelectorAll('.pd-add-popover').forEach(el => el.remove());
    const isScanner = kind === 'scanner';
    const pool = isScanner ? (state.printer_scanners || []) : (state.printers || []);
    const idOf = isScanner ? (x => x.scanner_id) : (x => x.id);
    const labelOf = isScanner ? scannerLabel : printerLabel;
    const assigned = new Set(isScanner ? _pdCurrentScannerIds(displayId) : _pdCurrentIds(displayId));
    const available = pool.filter(x => !assigned.has(idOf(x)));
    if (!available.length) {
      showMsg(isScanner ? 'Alle Scanner bereits zugewiesen' : 'Alle Pool-Drucker bereits zugewiesen');
      return;
    }
    const pop = document.createElement('div');
    pop.className = 'pd-add-popover';
    pop.innerHTML = available.map(x =>
      `<button class="pd-add-item" data-id="${escapeHtml(idOf(x))}">${escapeHtml(labelOf(x))}</button>`).join('');
    pop.addEventListener('click', (e) => {
      const item = e.target.closest('.pd-add-item');
      if (!item) return;
      if (isScanner) addPdScanner(displayId, item.dataset.id);
      else addPdPrinter(displayId, item.dataset.id);
      pop.remove();
    });
    document.body.appendChild(pop);
    // Immer oberhalb der „+"-Box öffnen, damit das Popover nicht aus dem
    // Fenster ragt; Breite/Höhe erst nach dem Anhängen bekannt, daher hier
    // messen statt fest zu verdrahten. Horizontal an den rechten Rand geklemmt.
    const rect = plusBox.getBoundingClientRect();
    const popRect = pop.getBoundingClientRect();
    const margin = 4;
    pop.style.top = `${Math.max(margin, rect.top - popRect.height - margin)}px`;
    pop.style.left = `${Math.max(margin, Math.min(rect.left, window.innerWidth - popRect.width - margin))}px`;
    const close = () => pop.remove();
    setTimeout(() => {  // nächster Tick, damit der öffnende Klick nicht schließt
      document.addEventListener('click', close, { once: true });
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); }, { once: true });
    }, 0);
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

  // Code verwerfen (ohne Zuordnung): wartende pending-Session revokieren —
  // z. B. Schüler hat nach Abschluss die Seite neu geladen und per Re-Join
  // einen neuen Code ausgelöst. Der Schüler geht client-seitig auf den
  // Done-Screen (s. invalidate_session → „closed"-Frame vor Close 4006),
  // ein Re-Join-Loop entsteht nicht.
  async function dismissCode(code, btn) {
    if (!code) return;
    if (!await confirmDialog(`Pairing-Code ${code} verwerfen? Der Schüler wird getrennt.`, 'Verwerfen')) return;
    await busy(btn, async () => {
      const r = await fetch('/api/student/dismiss', { method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ pairing_code: code }) });
      if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Verwerfen fehlgeschlagen'); }
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
    const mb = state.modus_b || { open: false, paused: false, pending: [], displays: [] };
    const hasConnectedDisplay = (mb.displays || []).some(d => d.connected && d.authorized);
    document.getElementById('mb-open-btn').style.display = mb.open ? 'none' : '';
    const pauseBtn = document.getElementById('mb-pause-btn');
    pauseBtn.style.display = mb.open && hasConnectedDisplay ? '' : 'none';
    pauseBtn.innerHTML = mb.paused ? ICO_PLAY : ICO_PAUSE;
    pauseBtn.setAttribute('aria-label', mb.paused ? 'QR-Anzeige fortsetzen' : 'QR-Anzeige pausieren');
    pauseBtn.title = mb.paused
      ? 'QR-Anzeige auf den iPads fortsetzen'
      : 'QR-Anzeige auf den iPads pausieren';
    document.getElementById('mb-close-btn').style.display = mb.open ? '' : 'none';
    const allowScansBtn = document.getElementById('mb-allow-scans-btn');
    allowScansBtn.style.display = mb.open && mb.paused && hasConnectedDisplay ? '' : 'none';
    document.getElementById('mb-status').textContent = mb.open ? 'geöffnet' : 'geschlossen';
    document.getElementById('mb-info').style.display = mb.open ? '' : 'none';
    // Alle verbundenen iPads bleiben sichtbar. Bei unautorisierten Displays
    // dient der Code nur dem visuellen Abgleich mit dem iPad-Bildschirm;
    // autorisierte Displays zeigen zusätzlich ihren Status.
    const pendingHost = document.getElementById('mb-display-pending');
    const connectedDisplays = (mb.displays || []).filter(d =>
      d.connected && (d.authorized || !ignoredDisplayIds.has(d.display_id))
    );
    pendingHost.style.display = connectedDisplays.length ? 'flex' : 'none';
    pendingHost.innerHTML = connectedDisplays.map(d => {
      const did = escapeHtml(d.display_id);
      const code = escapeHtml(d.registration_code || d.display_id.slice(0, 6));
      const status = d.authorized
        ? '<span class="badge badge-active mb-display-status">freigeschaltet</span>'
        : '<span class="badge badge-pending mb-display-status">wartet</span>';
      const authorize = d.authorized ? ''
        : `<button class="success" data-action="authorize-display" data-display-id="${did}">Freischalten</button>`;
      const ignore = d.authorized ? ''
        : `<button class="danger" data-action="ignore-display" data-display-id="${did}" title="Code ignorieren" aria-label="iPad-Code ${code} ignorieren">${ICON_CLOSE}</button>`;
      const disconnectClass = d.authorized ? ' mb-display-disconnect-primary' : '';
      const disconnect = d.authorized
        ? `<button class="ghost warn icon-only${disconnectClass}" data-action="disconnect-display" data-display-id="${did}" title="QR-Display trennen" aria-label="QR-Display trennen">${ICON_CLOSE}</button>`
        : `<button class="secondary" data-action="disconnect-display" data-display-id="${did}">Trennen</button>`;
      return `<div class="code-row mb-display-row">
        <span class="code-meta mb-display-label"><span class="ws-dot ok" aria-hidden="true"></span>QR-Display verbunden</span>
        <span class="code-val" aria-label="Registrierungscode">${code}</span>
        ${status}
        ${authorize}
        ${disconnect}
        ${ignore}
      </div>`;
    }).join('');
  }

  // iPad-Registrierungscode nur in dieser Host-Ansicht ausblenden. Die
  // Display-Session bleibt absichtlich bestehen; ein Reload des Hosts zeigt
  // den Eintrag wieder, ohne das iPad durch einen Reconnect mit einem neuen
  // Code zu überraschen.
  function ignoreDisplay(displayId) {
    if (!displayId) return;
    ignoredDisplayIds.add(displayId);
    renderModusBControl();
  }

  // Pairing-Card eines Klassen-Tabs: Arm-Banner + wartende Codes, zugeordnet
  // zu den wartenden Schülern DIESER Klasse.
  function renderCtxPairing(id) {
    const ctx = (state.contexts || {})[id];
    if (!ctx) return;
    // Zuerst die Scan-Station-Druckzeile — `renderCtxPairing` hat weiter unten
    // frühe returns (kein Code wartend), die Zeile soll aber immer stehen.
    renderCtxStationPrint(id);
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
          <button class="danger" data-action="dismiss-code" data-code="${p.pairing_code}" title="Code verwerfen" aria-label="Code ${p.pairing_code} verwerfen">${ICON_CLOSE}</button>
        </div>`;
      }
      // Code-zuerst: Schüler im Select wählen + Zuordnen.
      const selId = `mb-sel-${id}-${p.pairing_code}`;
      return `<div class="code-row">
        <span class="code-val">${p.pairing_code}</span>
        ${meta}
        <select id="${selId}" style="flex:1;min-width:140px">${studentOpts}</select>
        <button class="success" data-action="pair-select" data-sel-id="${selId}" data-code="${p.pairing_code}" ${pendingStudents.length ? '' : 'disabled'}>Zuordnen</button>
        <button class="danger" data-action="dismiss-code" data-code="${p.pairing_code}" title="Code verwerfen" aria-label="Code ${p.pairing_code} verwerfen">${ICON_CLOSE}</button>
      </div>`;
    }).join('');
  }

  // Host-Tab (Helfer + Modus-B-Kontrolle) und Klassen-Tab (Now-Serving + Queue
  // + Pairing) getrennt rendern.
  function renderHostTab() { renderHelpers(); renderModusBControl(); renderPrinterMainTabs(); renderPrintQueue(); renderPrinterDisplays(); renderScanStations(); renderPrinterScanners(); }

  // Hauptreiter der „Drucker"-Karte: Warteschlange / Displays / Scanner
  // (Scanner ist aktuell ein Platzhalter ohne eigene Funktion — die
  // Scan-Stationen bleiben im Live-Ausgabe-Kasten, s. renderScanStations()).
  // Rein clientseitige Sichtbarkeit — die Panels haben keine serverseitigen
  // Daten, die das umschalten müssten.
  function renderPrinterMainTabs() {
    const tabs = document.getElementById('printer-main-tabs');
    if (!tabs) return;
    tabs.querySelectorAll('[data-pmt-tab]').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.pmtTab === activePrinterMainTab);
    });
    document.getElementById('pmt-panel-queue')?.classList.toggle('active', activePrinterMainTab === 'queue');
    document.getElementById('pmt-panel-displays')?.classList.toggle('active', activePrinterMainTab === 'displays');
    document.getElementById('pmt-panel-scanner')?.classList.toggle('active', activePrinterMainTab === 'scanner');
  }
  function renderClassTab(id) {
    if (!(state.contexts || {})[id]) return;
    if (!document.getElementById('panel-ctx-' + id)) return;
    renderCtxNowServing(id);
    renderCtxQueue(id);
    renderCtxPairing(id);
    renderCtxTeacher(id);
    renderCtxPrinters(id);
  }

  // Lehrkraft-Ansicht-Kachel eines Klassen-Tabs: kein Fokus-Schutz nötig
  // (keine Texteingabe hier — der Registrierungscode wird nur ANGEZEIGT, s.
  // PLAN „Host-Ablauf"). Drei Zustände aus `ctx.teacher`
  // (`null` | `{authorized:false,…}` | `{authorized:true,…}`):
  //   1. noch keine Session → Button „QR für Lehrkraft anzeigen".
  //   2. Session wartet auf Freischaltung → Code + „Bestätigen"/„Abbrechen".
  //   3. autorisiert → Verbindungsstatus + sichtbare „Lehrkraft trennen"-Aktion
  //      (verhindert, dass ein späterer QR-Klick eine laufende Ansicht killt).
  // Statistik „Leihschein entgegengenommen" — von der Lehrkraft in ihrer
  // eigenen Ansicht je Schüler angekreuzt (`slip_collected`), hier nur
  // read-only aus der ohnehin vorhandenen Klassen-Queue abgeleitet (der Host
  // steuert dieses Flag nicht selbst). Nur relevant, sobald die Klassenoption
  // aktiv ist und mindestens ein Schüler abgeschlossen ist.
  function teacherSlipStat(ctx) {
    if (ctx.done_collected !== true) return '';
    const queue = ctx.queue || [];
    // Auto-fertig (nicht angemeldet / nicht bezahlt / …) zählt NICHT als
    // abgeschlossen: solche Schüler wurden nie aufgerufen, es wurden keine
    // Bücher gescannt und kein Leihschein gedruckt (`status` ist nur wegen
    // des Auto-Fertig-Filters beim Klassen-Öffnen 'done', s. classes.py).
    // Der Zähler spiegelt nur echte Abschlüsse mit Buchausgabe.
    const done = queue.filter(s => s.status === 'done' && !s.auto_skipped);
    if (!done.length) return '';
    const collected = done.filter(s => s.slip_collected).length;
    return `<p class="hint" style="margin-top:8px">Leihschein entgegengenommen: <b>${collected} / ${done.length}</b> abgeschlossen</p>`;
  }

  function renderCtxTeacher(id) {
    const ctx = (state.contexts || {})[id];
    if (!ctx) return;
    const host = document.querySelector(`.ctx-teacher-body[data-ctx-id="${id}"]`);
    if (!host) return;
    const t = ctx.teacher;
    const slipStat = teacherSlipStat(ctx);
    if (!t) {
      host.innerHTML = `<button class="secondary" data-action="teacher-qr" data-ctx-id="${id}">QR für Lehrkraft anzeigen</button>
        <p class="hint" style="margin-top:8px">Zeigt einer Lehrkraft live nur diese Klasse (Status je Schüler) — mit eingeschränkter Steuerung (nur „Als abwesend"-Markierung und Leihschein-Eingang), ohne andere Klassen/Bücher/Zahldaten.</p>
        ${slipStat}`;
      return;
    }
    if (!t.authorized) {
      host.innerHTML = `<div class="row" style="align-items:center;gap:10px;flex-wrap:wrap">
          <span>Code: <b>${escapeHtml(t.registration_code)}</b></span>
          <button class="secondary" data-action="teacher-qr" data-ctx-id="${id}">QR erneut zeigen</button>
          <button class="success" data-action="teacher-authorize" data-ctx-id="${id}">Bestätigen</button>
          <button class="secondary" data-action="teacher-disconnect" data-ctx-id="${id}">Abbrechen</button>
        </div>
        <p class="hint" style="margin-top:8px">Wartet auf Bestätigung — das Lehrkraft-Gerät zeigt denselben Code.</p>
        ${slipStat}`;
      return;
    }
    const dot = t.connected ? '<span style="color:#30d158">●</span>' : '<span style="color:#888">○</span>';
    host.innerHTML = `<div class="row" style="align-items:center;gap:10px">
        <span>${dot} ${t.connected ? 'verbunden' : 'getrennt (Reconnect möglich)'}</span>
        <button class="ghost warn icon-only" data-action="teacher-disconnect" data-ctx-id="${id}" title="Lehrkraft trennen" aria-label="Lehrkraft trennen">${ICON_CLOSE}</button>
      </div>
      ${slipStat}`;
  }

  // QR für die Lehrkraft-Ansicht dieser Klasse holen (mintet bzw. ersetzt eine
  // noch nicht autorisierte Session serverseitig) und im gemeinsamen QR-Modal
  // zeigen.
  async function showTeacherQr(id) {
    const r = await fetch(`/api/teacher/qr?context_id=${encodeURIComponent(id)}`);
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'QR für Lehrkraft konnte nicht erzeugt werden'); return; }
    const d = await r.json();
    // Wie bei Helfer-/Schüler-/Display-QRs den nächsten State-Broadcast
    // beobachten: sobald die Lehrkraft den QR scannt und ihre WS verbunden ist,
    // schließt sich das gemeinsame QR-Modal automatisch.
    qrWatch = { kind: 'teacher', context_id: id };
    showQr(d.qr, d.url || '');
    // Falls die WS-Verbindung genau zwischen dem Server-Broadcast und dem
    // Öffnen des Modals entstanden ist, den bereits aktuellen Snapshot direkt
    // auswerten statt auf einen weiteren Broadcast zu warten.
    maybeCloseQrOnScan();
  }

  // Host bestätigt den (im eigenen State bereits bekannten) Registrierungscode.
  async function authorizeTeacher(id) {
    const ctx = (state.contexts || {})[id];
    const code = ctx && ctx.teacher && ctx.teacher.registration_code;
    if (!code) return;
    const r = await fetch('/api/teacher/authorize', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: id, registration_code: code }),
    });
    if (r.ok) showMsg('Lehrkraft freigeschaltet');
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Freischaltung fehlgeschlagen'); }
  }

  // Lehrkraft-Session dieser Klasse trennen — bei einer bereits autorisierten
  // Session (laufende Ansicht) erst nach Bestätigung.
  async function disconnectTeacher(id) {
    const ctx = (state.contexts || {})[id];
    const authorized = !!(ctx && ctx.teacher && ctx.teacher.authorized);
    if (authorized && !await confirmDialog('Lehrkraft-Ansicht dieser Klasse trennen?', 'Trennen')) return;
    const r = await fetch('/api/teacher/disconnect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: id }),
    });
    if (r.ok) showMsg('Lehrkraft-Session getrennt');
    else { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Trennen fehlgeschlagen'); }
  }

  // ---- Drucker-Allowlist pro Klasse (panel-new + Klassen-Tab) ------------
  // Checkbox-Liste der Pool-Drucker. Im panel-new wird die Auswahl mit
  // `/api/open-class` geschickt; im Klassen-Tab live per `/api/context-printers`
  // gesetzt. Leere Auswahl = `[]` = bewusst kein Drucker (Server speichert leere
  // Menge, nicht „alle"); `None` (Feld fehlt) = alle. S. _resolve_allowed_printers.

  function _printerCheckboxHTML(p, checked, dataCtxId) {
    const pid = escapeHtml(p.id);
    const pname = escapeHtml(printerStableKey(p));
    const lbl = escapeHtml(printerLabel(p));
    const ctxAttr = dataCtxId ? ` data-ctx-id="${escapeHtml(dataCtxId)}"` : '';
    return `<label class="check-line" title="Leihschein dieser Klasse auf „${lbl}" drucken${checked ? '' : ' (abgewählt)'}">
      <input type="checkbox" class="printer-check" data-pid="${pid}" data-pname="${pname}"${ctxAttr}${checked ? ' checked' : ''}>
      <span>${lbl}</span>
    </label>`;
  }

  // panel-new: Drucker-Checkboxen für die nächste zu öffnende Klasse. Default
  // alle angehakt (entspricht „alle"); gespeicherte Auswahl (eingeschränkt)
  // bleibt erhalten. Kein Pool → Hinweis, dass Druck nicht möglich ist.
  function renderNewClassPrinters() {
    const host = document.getElementById('new-class-printers');
    if (!host) return;
    const pool = state.printers || [];
    if (!pool.length) {
      host.innerHTML = '<p class="hint">Kein Drucker konfiguriert — Druck nicht möglich. In den Einstellungen Drucker hinzufügen.</p>';
      return;
    }
    const saved = loadClassPrintersSelection();  // Set | null (null = alle)
    const checkboxes = pool.map(p => _printerCheckboxHTML(p, saved === null || saved.has(printerStableKey(p)), null)).join('');
    // Bewusst leere Auswahl (gespeichertes leeres Set) ehrlich anzeigen: kein
    // Drucker für die nächste zu öffnende Klasse — Druck nur per manueller
    // Auswahl. Default (nichts gespeichert) = alle angehakt.
    const noneSelected = saved !== null && saved.size === 0;
    host.innerHTML = noneSelected
      ? checkboxes + '<p class="hint" style="margin-top:6px">Kein Drucker ausgewählt — Leihschein-Druck nur per manueller Auswahl im Druckdialog.</p>'
      : checkboxes;
    // Live-Ausgabe-Schalter für die nächste zu öffnende Klasse aus localStorage
    // vorbelegen (Default true).
    const liveCb = document.getElementById('new-class-live-ausgabe');
    if (liveCb) liveCb.checked = loadClassLiveAusgabe();
    // Leihschein-Druckmodus für die nächste zu öffnende Klasse aus localStorage
    // vorbelegen (Default "auto").
    const slipSel = document.getElementById('new-class-slip-trigger');
    if (slipSel) slipSel.value = loadClassSlipTrigger();
    // Fertig-Optionen für die nächste zu öffnende Klasse aus localStorage
    // vorbelegen (Default aus, s. ClassContext.done_signed/done_collected).
    const signedCb = document.getElementById('new-class-done-signed');
    if (signedCb) signedCb.checked = loadClassDoneSigned();
    const collectedCb = document.getElementById('new-class-done-collected');
    if (collectedCb) collectedCb.checked = loadClassDoneCollected();
    // Kopplung Drucker ↔ Live-Ausgabe aus dem gerenderten Zustand synchronisieren
    // (Schalter deaktiviert + roter Hinweis, wenn kein Drucker gewählt).
    updateNewClassLiveGate();
    updateNewClassDoneOpts();
  }

  // Kopplung panel-new: der „mindestens ein Drucker"-Hinweis erscheint NICHT
  // dauerhaft bei fehlendem Drucker, sondern erst beim Versuch, Live-Ausgabe zu
  // aktivieren (s. Live-Schalter-Listener). Hier nur Warnungen räumen, sobald
  // wieder ein Drucker gewählt ist.
  function newClassPrinterCheckedCount() {
    return document.querySelectorAll('#new-class-printers input[data-pid]:checked').length;
  }
  function updateNewClassLiveGate() {
    if (newClassPrinterCheckedCount() > 0) {
      for (const eid of ['new-class-live-warn', 'new-class-printer-warn']) {
        const el = document.getElementById(eid);
        if (el) { el.textContent = ''; el.style.display = 'none'; }
      }
    }
  }

  // Klassen-Tab: Checkboxen je Kontext aus `ctx.allowed_printers` (Snapshot).
  // `null` = alle Pool-Drucker (alle angehakt, Default bei Öffnen ohne Angabe);
  // `[]` = bewusst kein Drucker ausgewählt (nichts angehakt). Letzteres ist
  // keine „alle"-Falle mehr: der Helfer-Druckdialog bekommt keine Vorauswahl,
  // der Druck bleibt aber per manueller Auswahl möglich.
  function renderCtxPrinters(id) {
    const host = document.querySelector(`[data-ctx-printers="${id}"]`);
    if (!host) return;
    const ctx = (state.contexts || {})[id];
    if (!ctx) return;
    const pool = state.printers || [];
    const allowed = ctx.allowed_printers;  // null | string[]
    const allowedSet = allowed === null ? null : new Set(allowed);
    if (!pool.length) {
      host.innerHTML = '<p class="hint">Kein Drucker konfiguriert.</p>';
      return;
    }
    const checkboxes = pool.map(p => _printerCheckboxHTML(p, allowedSet === null || allowedSet.has(p.id), id)).join('');
    // Bewusst leere Auswahl (= `[]`, nicht `null`) ehrlich anzeigen: kein
    // Drucker für diese Klasse gewählt — Druck nur per manueller Auswahl.
    const noneSelected = allowedSet !== null && allowedSet.size === 0;
    host.innerHTML = noneSelected
      ? checkboxes + '<p class="hint" style="margin-top:6px">Kein Drucker für diese Klasse ausgewählt — Leihschein-Druck nur per manueller Auswahl im Druckdialog.</p>'
      : checkboxes;
    // Live-Ausgabe-Schalter dieser Klasse aus dem Snapshot setzen + Modus-B-
    // Kasten (Pairing) entsprechend ein/ausblenden. Default `true` (kompatibel
    // mit Kontexten, die das Feld noch nicht liefern — z. B. alte Snapshots).
    const liveOn = ctx.live_ausgabe !== false;
    const liveCb = document.querySelector(`input[data-ctx-live="${id}"]`);
    if (liveCb) liveCb.checked = liveOn;
    // Leihschein-Druckmodus dieser Klasse aus dem Snapshot setzen (Default
    // "auto", kompatibel mit Kontexten, die das Feld noch nicht liefern).
    const slipSel = document.querySelector(`select[data-ctx-slip-trigger="${id}"]`);
    if (slipSel) {
      slipSel.value = ctx.slip_trigger || 'auto';
      slipSel.dataset.prevValue = slipSel.value;
    }
    // Fertig-Optionen dieser Klasse aus dem Snapshot setzen (Default `false`,
    // kompatibel mit Kontexten, die die Felder noch nicht liefern).
    const signedCb = document.querySelector(`input[data-ctx-done-signed="${id}"]`);
    if (signedCb) signedCb.checked = !!ctx.done_signed;
    const collectedCb = document.querySelector(`input[data-ctx-done-collected="${id}"]`);
    if (collectedCb) collectedCb.checked = !!ctx.done_collected;
    const mbCard = document.querySelector(`[data-ctx-mb="${id}"]`);
    if (mbCard) mbCard.style.display = liveOn ? '' : 'none';
    // Kopplung Drucker ↔ Live-Ausgabe: der „mindestens ein Drucker"-Hinweis
    // erscheint NICHT dauerhaft bei fehlendem Drucker, sondern erst beim Versuch,
    // Live-Ausgabe zu aktivieren (s. setContextLiveAusgabe). Hier nur Warnungen
    // räumen, sobald wieder ein Drucker gewählt ist (Snapshot nach Änderung).
    updateCtxCouplingHints(id, noneSelected);
    // Fertig-Optionen („Leihschein unterschrieben" / „… eingesammelt")
    // an den Live-Schalter koppeln: beide ausgegraut bei Live aus, „eingesammelt"
    // zusätzlich ausgegraut, wenn „unterschrieben" nicht angehakt ist.
    updateCtxDoneOpts(id);
  }

  // Klassen-Tab: Fertig-Optionen unter der Live-Ausgabe. „Leihschein
  // eingesammelt" ist ausgegraut, wenn „Leihschein unterschrieben" nicht
  // angehakt ist (= fertig bereits bei gedruckt); beide ausgegraut, wenn
  // Live-Ausgabe aus. Persistiert serverseitig via `setContextDoneOptions`
  // (s. u.) — die eigentliche Fertig-Übergang-Funktion folgt später.
  function updateCtxDoneOpts(id) {
    const live = document.querySelector(`input[data-ctx-live="${id}"]`);
    const signed = document.querySelector(`input[data-ctx-done-signed="${id}"]`);
    const collected = document.querySelector(`input[data-ctx-done-collected="${id}"]`);
    const slipTrigger = document.querySelector(`select[data-ctx-slip-trigger="${id}"]`);
    if (!live || !signed || !collected) return;
    const liveOn = !!live.checked;
    signed.disabled = !liveOn;
    collected.disabled = !liveOn || !signed.checked;
    if (slipTrigger) slipTrigger.disabled = !liveOn;
  }

  // Kopplungs-Hinweise für eine Klasse nach einem Re-Render räumen. Die roten
  // Hinweise werden nur beim Versuch gezeigt (Live an ohne Drucker bzw. letzter
  // Drucker bei aktiver Live-Ausgabe) — s. setContextLiveAusgabe /
  // setContextPrinters. Ist wieder ≥1 Drucker gewählt, beide Warnungen löschen.
  function updateCtxCouplingHints(id, noneSelected) {
    if (noneSelected) return;
    for (const sel of [`[data-ctx-printer-warn="${id}"]`, `[data-ctx-live-warn="${id}"]`]) {
      const el = document.querySelector(sel);
      if (el) { el.textContent = ''; el.style.display = 'none'; }
    }
  }

  // Checkbox-Änderung im Klassen-Tab → Allowlist sofort ans Server senden.
  // Leere Auswahl = `[]` = bewusst kein Drucker (Server speichert leere Menge,
  // nicht „alle"); s. _resolve_allowed_printers. `changedEl` = die gerade
  // geänderte Checkbox — wird zurückgesetzt, wenn die Änderung blockiert wird.
  async function setContextPrinters(id, changedEl) {
    const host = document.querySelector(`[data-ctx-printers="${id}"]`);
    if (!host) return;
    const boxes = Array.from(host.querySelectorAll('input[data-pid]'));
    const ids = boxes.filter(el => el.checked).map(el => el.dataset.pid);
    const liveOn = !!document.querySelector(`input[data-ctx-live="${id}"]`)?.checked;
    // Letzten Drucker bei aktiver Live-Ausgabe nicht abwählen — Schalter erst
    // ausschalten. Checkbox revertieren, roten Hinweis zeigen, nicht senden.
    if (!ids.length && liveOn) {
      if (changedEl) changedEl.checked = true;
      const warn = document.querySelector(`[data-ctx-printer-warn="${id}"]`);
      if (warn) { warn.textContent = 'Zuerst Live-Ausgabe schließen'; warn.style.display = ''; }
      return;
    }
    // Drucker-Warnung räumen, sobald wieder ≥1 Drucker gewählt ist.
    const pWarn = document.querySelector(`[data-ctx-printer-warn="${id}"]`);
    if (pWarn) { pWarn.textContent = ''; pWarn.style.display = 'none'; }
    const r = await fetch('/api/context-printers', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: id, printers: ids }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showMsg(d.detail?.msg || d.detail || 'Drucker-Auswahl konnte nicht gespeichert werden');
      // Server hat abgewiesen (z. B. „Zuerst Live-Ausgabe schließen") → Checkbox
      // zurücksetzen, damit die UI zum Server-Snapshot passt (Broadcast folgt).
      if (changedEl) changedEl.checked = true;
    }
  }

  // Live-Ausgabe-Schalter im Klassen-Tab geändert → ans Server senden. Der
  // Server broadcastet den neuen Snapshot; daraufhin blendet renderCtxPrinters
  // den Modus-B-Kasten ein/aus und der Queue-Pairing-Button folgt nach.
  // `el` = der Schalter — wird zurückgesetzt, wenn das Aktivieren blockiert
  // wird (kein Drucker gewählt).
  async function setContextLiveAusgabe(id, on, el) {
    if (on) {
      const host = document.querySelector(`[data-ctx-printers="${id}"]`);
      const boxes = host ? Array.from(host.querySelectorAll('input[data-pid]')) : [];
      const hasPrinter = boxes.some(b => b.checked);
      // Mindestens eine Checkbox angehakt (keine angehakt = `[]` = kein Drucker).
      if (!hasPrinter) {
        if (el) el.checked = false;
        const warn = document.querySelector(`[data-ctx-live-warn="${id}"]`);
        if (warn) { warn.textContent = 'Es ist mindestens ein Drucker auszuwählen'; warn.style.display = ''; }
        updateCtxDoneOpts(id);
        return;
      }
    }
    const liveWarn = document.querySelector(`[data-ctx-live-warn="${id}"]`);
    if (liveWarn) { liveWarn.textContent = ''; liveWarn.style.display = 'none'; }
    const r = await fetch('/api/context-live-ausgabe', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: id, live_ausgabe: !!on }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showMsg(d.detail?.msg || d.detail || 'Live-Ausgabe konnte nicht gespeichert werden');
      if (el) el.checked = !on;  // revert
    }
    updateCtxDoneOpts(id);
  }

  // Klassen-Tab: Leihschein-Druckmodus (slip_trigger) dieser Klasse nachträglich
  // speichern (Dropdown unter „Leihschein Druck:"). Spiegel von
  // setContextLiveAusgabe — ohne Druckerkopplung (jeder Wert ist unabhängig von
  // der Druckerauswahl erlaubbar). Persistiert serverseitig via
  // /api/context-slip-trigger; bei Fehler revert + Toast.
  async function setContextSlipTrigger(id, value, el) {
    const prev = el && el.dataset.prevValue;
    const r = await fetch('/api/context-slip-trigger', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: id, slip_trigger: value }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showMsg(d.detail?.msg || d.detail || 'Druckmodus konnte nicht gespeichert werden');
      if (el && prev) el.value = prev;  // revert
    } else if (el) {
      el.dataset.prevValue = value;
    }
  }

  // Klassen-Tab: „Fertig"-Voraussetzungen (Leihschein unterschreiben/
  // einsammeln) dieser Klasse nachträglich speichern. Liest beide Checkboxen
  // aktuell aus dem DOM (statt nur der geänderten), damit z. B. „eingesammelt"
  // korrekt mitgeschickt wird, wenn „unterschreiben" gerade abgewählt wurde.
  // Persistiert serverseitig via /api/context-done-options; bei Fehler revert
  // (beide Checkboxen zurück auf den Server-Snapshot) + Toast.
  async function setContextDoneOptions(id) {
    const signed = document.querySelector(`input[data-ctx-done-signed="${id}"]`);
    const collected = document.querySelector(`input[data-ctx-done-collected="${id}"]`);
    if (!signed || !collected) return;
    const r = await fetch('/api/context-done-options', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ context_id: id, done_signed: !!signed.checked, done_collected: !!collected.checked }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      showMsg(d.detail?.msg || d.detail || 'Fertig-Optionen konnten nicht gespeichert werden');
      const ctx = (state.contexts || {})[id];
      if (ctx) {
        signed.checked = !!ctx.done_signed;
        collected.checked = !!ctx.done_collected;
      }
      updateCtxDoneOpts(id);
    }
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
    const next = queue.find(s => s.status === 'pending' || s.status === 'absent');
    const helpers = Object.values(state.helpers || {});

    // Alerts abgelaufener/nicht mehr aktiver Schüler wegräumen — sonst bliebe
    // ein Kästchen rot, obwohl der Schüler längst abgeschlossen/entfernt ist.
    const activeIds = new Set(active.map(s => s.student_id));
    for (const aid of Object.keys(studentAlerts)) {
      if (!activeIds.has(Number(aid))) delete studentAlerts[aid];
    }

    let body;
    if (!active.length) {
      // Live-Ausgabe aus → kein Pairing-Hinweis (Modus-B-Kasten ist ausgeblendet);
      // dann bleibt nur „Nächster" als Weg, einen Schüler zu aktivieren.
      const liveOn = ctx.live_ausgabe !== false;
      body = liveOn
        ? '<div class="ns-empty">Niemand aktiv — Schüler per „Pairing" oder „Nächster" zuweisen.</div>'
        : '<div class="ns-empty">Niemand aktiv — Schüler per „Nächster" zuweisen.</div>';
    } else {
      body = '<div class="ns-grid">' + active.map(s => {
        const helper = helpers.find(h => h.student_id === s.student_id);
        // Ein aktiver Schüler ohne Helfer-Zuordnung ist ein Schülerclient.
        // Erst im Druckmodus ist der Betreuerauslöser bereit; der Server
        // behandelt den ersten Auftrag dann automatisch wie einen Schüler-
        // Auftrag (s. `print_loan_slip` in routes/slips.py — kein Client-Flag
        // mehr). Der Druckbutton bleibt ab dem Druckmodus bis zum Abschluss
        // sichtbar — auch während/nach dem Druck und im Unterschriften-Modus
        // (links neben dem rechtsbündigen Unterschrift-Button), damit ein
        // verlorener/zerstörter Leihschein jederzeit nachgedruckt werden kann.
        // Ein Nachdruck (`slip_printed`) legt der Server bewusst als Host-
        // Auftrag an (s. `is_reprint` in routes/slips.py); ein laufender
        // Erstdruck wird serverseitig via `in_flight_student_ids()` blockiert.
        // `station_print_needs_host`: Scan-Station-Druckermodus (Automatisch/
        // Selbstauslöser) fand beim Eintritt keinen erlaubten, auf einem
        // Display sichtbaren Drucker und hat bewusst KEINEN Auftrag erzeugt
        // (s. server/routes/ws.py::ws_scan_station) — der Button muss hier
        // unabhängig vom Klassen-`slip_trigger` erscheinen, damit der Host
        // ganz normal drucken kann.
        const studentClientPrint = !helper
          && s.print_mode
          && (ctx.slip_trigger === 'helper' || s.station_print_needs_host);
        const studentClient = !helper && s.print_mode;
        const studentSignature = studentClient && ctx.done_signed === true && s.slip_signing;
        const printAction = studentClient
          ? (studentClientPrint
            ? `<button class="secondary icon-only" data-action="print" data-student-id="${s.student_id}" title="Leihschein drucken" aria-label="Leihschein drucken">${ICON_PRINTER}</button>`
            : '')
          : `<button class="secondary icon-only" data-action="print" data-student-id="${s.student_id}" title="Leihschein drucken" aria-label="Leihschein drucken">${ICON_PRINTER}</button>`;
        const helperLbl = helper ? `<span class="ns-helper">${ICO_HELPER} ${escapeHtml(helper.name)}</span>` : '';
        // Scan-Station-Zeile unter dem Namen: links Stationsname + Symbol (nur
        // während der Schüler dort angemeldet ist, inkl. eigenem Trennen-Knopf,
        // der NUR von der Station abmeldet — Spiegel von /api/scan-station/
        // release, aber vom Schüler statt der Station aus adressiert), rechts
        // der Zettel-Code (bleibt sichtbar, solange ein Zettel gedruckt wurde,
        // auch nach dem Abmelden — der Code steht ja weiter auf dem Papier).
        // Ein übernehmender Helfer hat beim Stationsnamen Vorrang (leer statt
        // veraltet); die Zeile selbst entfällt ganz ohne Code.
        const stationDisconnectBtn = (!helper && s.station_name)
          ? `<button class="ns-station-disconnect" data-action="station-disconnect" data-student-id="${s.student_id}" title="Von der Scan-Station abmelden" aria-label="Von der Scan-Station abmelden">${ICON_DISCONNECT}</button>`
          : '';
        const stationRight = (!helper && s.station_name)
          ? `<div class="ns-station-name">${ICO_HOST} ${escapeHtml(s.station_name)}${stationDisconnectBtn}</div>` : '';
        const codeLeft = s.station_code
          ? `<div class="ns-code">${escapeHtml(s.station_code)}</div>` : '';
        // Zettel (Barcode + Bücherliste) erneut drucken — nur sinnvoll, wenn
        // überhaupt schon einer gedruckt wurde (sonst gäbe es keinen Code).
        // Öffnet denselben Druck-Dialog wie beim ersten Zettel-Druck aus der
        // Klassen-Queue (`printStationSheet`), nur direkt für diesen bereits
        // aktiven Schüler statt über die Wartend-Auswahl.
        const reprintSheetBtn = s.station_code
          ? `<button class="secondary icon-only" data-action="reprint-station-sheet" data-student-id="${s.student_id}" title="Zettel (Barcode + Bücherliste) erneut drucken" aria-label="Zettel erneut drucken">${ICON_SHEET}</button>`
          : '';
        const statusLbl = renderActiveStatusText(s);
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
        // Scan-Station-Druckermodus-Gate (s. PrintQueue.station_gate_snapshot):
        // gelber Hinweis NUR, solange der Auftrag noch wartet (`status ===
        // 'queued'`, noch nicht an einen Drucker gesendet) UND kein erlaubter
        // Drucker gerade auf einem Display sichtbar ist. War zu Beginn schon
        // keiner sichtbar, wurde gar kein Auftrag erzeugt (s. ws_scan_station)
        // — dann gibt es hier auch keinen Hinweis, sondern nur den normalen
        // Druckbutton (s. `studentClientPrint`/`station_print_needs_host`
        // oben). Bereits dispatchte Aufträge zeigen ebenfalls nichts mehr
        // (der Job kann nicht mehr umgebucht werden, `host_adopt_station_job`
        // lässt nur wartende Aufträge zu).
        const gate = (s.station_gate && !s.station_gate.dispatched && s.station_gate.blocked)
          ? s.station_gate : null;
        const gateText = gate
          ? `${escapeHtml(s.form)}, ${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}: `
            + 'Kein erlaubter freigegebener Drucker verfügbar'
          : '';
        const gateLbl = gate
          ? `<div class="ns-station-gate">
              <div class="ns-station-gate-text">${gateText}</div>
              <div class="ns-station-gate-picker" id="ns-sg-picker-${s.student_id}"></div>
              <p class="ns-station-gate-err" id="ns-sg-err-${s.student_id}" style="display:none"></p>
              <button type="button" class="secondary ns-station-gate-btn" data-action="station-gate-adopt" data-student-id="${s.student_id}" data-job-id="${escapeHtml(gate.job_id)}">Druckauftrag aktualisieren</button>
            </div>`
          : '';
        // Hinweis, wenn der Scan-Station-Druckermodus (Automatisch/
        // Selbstauslöser) beim Eintritt weder Drucker noch Drucker-Scanner
        // erreichbar fand und deshalb bewusst KEINEN Auftrag erzeugt hat (s.
        // `station_print_needs_host` oben) — macht sichtbar, WARUM hier
        // (anders als sonst bei diesem Trigger) ein Druckbutton auftaucht.
        const needsHostLbl = s.station_print_needs_host
          ? `<div class="ns-station-gate ns-station-needs-host">
              <div class="ns-station-gate-text">Kein erlaubter Drucker/Scanner erreichbar — bitte hier drucken.</div>
            </div>`
          : '';
        // Kästchen bleibt rot (wie gehabt); nur der Meldungstext ist beim
        // Verliehen-Alert normal — „verliehen an …" ist das einzige Rot im Text.
        return `<div class="ns-tile${alert ? ' ns-tile-alert' : ''}">
          <div class="ns-name">${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}</div>
          <div class="ns-tile-mid">
            <div class="ns-info-grid">
              <div class="ns-info-left">
                ${codeLeft}
                <div class="ns-class">${escapeHtml(s.form)}</div>
              </div>
              <div class="ns-info-right">
                ${stationRight}
                <div class="ns-status">${statusLbl}${helperLbl}</div>
              </div>
            </div>
            ${alertLbl}
            ${gateLbl}
            ${needsHostLbl}
          </div>
          <div class="ns-actions">
            <div class="ns-actions-left">
              <button class="success icon-only" data-action="finish" data-student-id="${s.student_id}" title="Abschließen" aria-label="Abschließen">${ICON_ACTION_CHECK}</button>
              <button class="secondary icon-only" data-action="disconnect" data-student-id="${s.student_id}" title="Trennen" aria-label="Trennen">${ICON_DISCONNECT}</button>
              ${reprintSheetBtn}
            </div>
            <div class="ns-actions-right">
              ${printAction}
              ${studentSignature ? `<button class="secondary icon-only" data-action="finish-signed" data-student-id="${s.student_id}" title="Leihschein unterschrieben" aria-label="Leihschein unterschrieben">${ICON_SIGN}</button>` : ''}
            </div>
          </div>
        </div>`;
      }).join('') + '</div>';
    }

    const nextLbl = next
      ? `<div class="ns-next">Als Nächstes: <strong>${escapeHtml(next.lastname)}, ${escapeHtml(next.firstname)}</strong> (${escapeHtml(next.form)})</div>`
      : '';

    el.innerHTML = `<div class="ns-head">Aktuell in Ausgabe</div>${body}${nextLbl}`;

    // Drucker-Picker fürs "Druckauftrag aktualisieren"-Menü imperativ mounten
    // (mountPrinterPicker baut eigenes DOM + Event-Listener, kann nicht als
    // reiner innerHTML-String erzeugt werden). Nur für Schüler mit einem noch
    // wartenden, blockierten Gate-Auftrag (s. gateLbl oben).
    stationGatePickers = {};
    const pool = hostPrinterPool();
    for (const s of active) {
      const gate = s.station_gate;
      if (!gate || gate.dispatched || !gate.blocked) continue;
      const mount = document.getElementById(`ns-sg-picker-${s.student_id}`);
      if (!mount) continue;
      const pool_ids = pool.map(p => p.id);
      stationGatePickers[s.student_id] = mountPrinterPicker(mount, pool, pool_ids);
    }
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
      openQ += q.filter(s => s.status === 'pending' || s.status === 'absent').length;
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

  // ---- Drucker-Pool (Einstellungen-Dialog) ----------------------------
  // Reiter wie Klassen-Tabs: pro Drucker ein Reiter + Panel (Duplex-Dropdown,
  // Entfernen). Mutationen (add/remove/duplex/reorder) gehen sofort an den
  // Server (eigene Endpunkte), nicht über den „Speichern"-Fluss — nur der
  // Schüler-Leihschein-Toggle bleibt dort.
  const DUPLEX_OPTIONS = [
    {v: 'one_sided', l: 'Einseitig'},
    {v: 'two_sided_long', l: 'Doppelseitig (lange Seite)'},
    {v: 'two_sided_short', l: 'Doppelseitig (kurze Seite)'},
  ];
  let activePrinterTabId = null;
  let printerDeviceInfo = {printers: [], default: null, backend: null};

  function printerSystemLabel(p) {
    if (p.name === null || p.name === undefined) {
      const dev = printerDeviceInfo.default;
      return dev ? `Standarddrucker (${dev})` : 'Standarddrucker';
    }
    return p.name;
  }

  function printerLabel(p) {
    // Anzeigename (falls gesetzt) mit Systemnamen in Klammern — der
    // Systemname bleibt stets sichtbar zur eindeutigen Zuordnung. Beim
    // Standarddrucker steht nur „Standarddrucker" in der Klammer (ohne den
    // Gerätename) — sonst entstünden verschachtelte Klammern wie
    // „Raum 104 (Standarddrucker (HP-LJ))". Der Gerätename bleibt in der
    // Panel-Zeile „System:" sichtbar.
    if (p.label && p.label.trim()) {
      const sys = (p.name === null || p.name === undefined) ? 'Standarddrucker' : p.name;
      return `${p.label} (${sys})`;
    }
    return printerSystemLabel(p);
  }

  async function fetchPrinters() {
    try {
      const r = await fetch('/api/printers');
      if (r.ok) return await r.json();
    } catch (_) {}
    return null;
  }

  async function refreshPrinterDeviceInfo() {
    // Geräteliste + Default nur beim Öffnen des Einstellungs-Dialogs frisch
    // holen (OS-Drucker ändern sich selten). Die Live-Last der Pool-Drucker
    // kommt über den State-Snapshot, nicht über diesen (langsamen) Endpoint,
    // der serverseitig lpstat/Get-Printer ausführt.
    const info = await fetchPrinters();
    if (!info) return;
    printerDeviceInfo = {printers: info.printers || [], default: info.default || null, backend: info.backend || null};
    renderPrinterPool(info.pool || state.printers || []);
  }

  async function _applyPoolResponse(r) {
    // Nach einer Pool-Mutation: direkt aus der Endpoint-Antwort (`{ok, pool}`)
    // rendern — ohne erneuten /api/printers-Roundtrip (kein lpstat). Fallback
    // auf den Live-Snapshot, falls die Antwort keinen Pool liefert.
    let pool = null;
    try { if (r.ok) { const d = await r.json(); pool = d && d.pool; } } catch (_) {}
    renderPrinterPool(pool || state.printers || []);
  }

  function maybeRefreshPrinterPoolFromSnapshot() {
    // Live-Update des Einstellungs-Pools aus dem WS-Snapshot — dieselbe Quelle
    // wie die Druckerwarteschlange, damit add/remove ohne spürbare Verzögerung
    // auch im Einstellungs-Dialog auftauchen/verschwinden. Schutz: wird gerade
    // ein Eingabefeld (Anzeigename) oder das Duplex-Select im Panel fokussiert,
    // überspringen wir den Re-Render (würde die laufende Eingabe zurücksetzen);
    // die Mutation selbst rendert via _applyPoolResponse, der nächste Snapshot
    // ohne Fokus holt nach. Die Add-Row liegt außerhalb #printer-panels und
    // bleibt von Re-Renders unberührt.
    if (!settingsOpen()) return;
    const ae = document.activeElement;
    if (ae && ae.closest && ae.closest('#printer-panels')) return;
    renderPrinterPool(state.printers || []);
  }

  function renderPrinterPool(pool) {
    const tabs = document.getElementById('printer-tabs');
    const panels = document.getElementById('printer-panels');
    tabs.innerHTML = '';
    panels.innerHTML = '';
    if (!pool.length) {
      panels.innerHTML = '<p class="hint">Kein Drucker konfiguriert — Leihschein-Druck nicht möglich. Mit „+" einen hinzufügen.</p>';
      activePrinterTabId = null;
      return;
    }
    if (!pool.some(p => p.id === activePrinterTabId)) activePrinterTabId = pool[0].id;
    for (const p of pool) {
      const active = p.id === activePrinterTabId;
      const busy = p.load > 0;
      const btn = document.createElement('button');
      btn.className = 'ptab' + (active ? ' active' : '');
      btn.dataset.pid = p.id;
      btn.draggable = true;
      btn.title = p.faulty
        ? 'Drucker fehlerhaft (ziehen zum Umsortieren)'
        : 'Drucker-Reiter (ziehen zum Umsortieren)';
      // × hinter dem Namen (Spiegel der Klassen-Tab-Schließen-Markierung).
      const closeTitle = busy ? 'Drucker noch beschäftigt — Entfernen gesperrt' : 'Drucker entfernen';
      const closeCls = busy ? 'ptab-close is-busy' : 'ptab-close';
      const faultMark = p.faulty ? ' <span class="ptab-fault" title="Fehlerhaft">⚠</span>' : '';
      btn.innerHTML = `${escapeHtml(printerLabel(p))}${faultMark} <span class="${closeCls}" data-close="${p.id}" title="${closeTitle}">×</span>`;
      // Klick auf den Tab-Körper (nicht auf ×) aktiviert den Reiter.
      btn.onclick = (e) => {
        if (e.target.closest('[data-close]')) return;
        activePrinterTabId = p.id; renderPrinterPool(pool);
      };
      wirePrinterTabDrag(btn, pool);
      tabs.appendChild(btn);
      if (active) panels.appendChild(buildPrinterPanel(p, pool));
    }
    // × Klicks → Bestätigungsdialog (liegt jetzt über dem Settings-Dialog, s. CSS
    // #confirm-modal z-index) + Entfernen. Beschäftigte Drucker sind gesperrt.
    tabs.querySelectorAll('[data-close]').forEach(el => {
      el.onclick = async (e) => {
        e.stopPropagation();
        const id = el.dataset.close;
        if (el.classList.contains('is-busy')) { showMsg('Drucker noch beschäftigt — bitte warten'); return; }
        const p = pool.find(x => x.id === id);
        if (!p) return;
        if (!await confirmDialog(`Drucker „${printerLabel(p)}" entfernen?`, 'Entfernen')) return;
        await removePrinter(id);
      };
    });
  }

  function buildPrinterPanel(p, pool) {
    const panel = document.createElement('div');
    panel.className = 'printer-panel';
    const busy = p.load > 0;
    const faulty = !!p.faulty;
    const spooledList = Array.isArray(p.spooled_names) && p.spooled_names.length
      ? p.spooled_names
      : (p.spooled_name ? [p.spooled_name] : []);
    const statusLine = faulty
      ? `<p class="hint" style="color:var(--warn,#b00)">Fehlerhaft — keine neuen Aufträge. Drucker nach Inaktivität blockiert.</p>
         <button class="secondary" data-act="reactivate" style="margin-top:6px">Wieder aktivieren</button>`
      : (busy
        ? `<p class="hint">Belegt: ${p.load}/2 — ${p.printing_name ? 'druckt „' + escapeHtml(p.printing_name) + '"' : 'wartend'}${spooledList.length ? ' · gesendet, wartet: ' + spooledList.map(n => '„' + escapeHtml(n) + '"').join(', ') : ''}</p>`
        : '');
    const opts = DUPLEX_OPTIONS.map(o => `<option value="${o.v}"${o.v === p.duplex ? ' selected' : ''}>${o.l}</option>`).join('');
    panel.innerHTML = `
      <div class="printer-panel-name">${escapeHtml(printerLabel(p))}</div>
      <div class="printer-panel-sys">System: ${escapeHtml(printerSystemLabel(p))}</div>
      <label class="settings-row settings-field" title="Frei wählbarer Name, der überall (mit Systemname in Klammern) angezeigt wird. Leer = nur Systemname.">
        Name
        <div class="printer-label-row">
          <input type="text" data-act="label-input" value="${escapeHtml(p.label || '')}" placeholder="z. B. Drucker Raum 104" autocomplete="off">
          <button class="secondary" data-act="label-save">Speichern</button>
        </div>
      </label>
      <label class="settings-row settings-field" title="Wie gedruckt wird, falls ein Auftrag länger als zwei Seiten ist. Wird nur gespeichert (nicht ans Backend weitergereicht).">
        Duplex (bei &gt; 2 Seiten)
        <select data-act="duplex">${opts}</select>
      </label>
      ${statusLine}
    `;
    panel.querySelector('[data-act="duplex"]').onchange = (e) => setPrinterDuplex(p.id, e.target.value);
    const labelInput = panel.querySelector('[data-act="label-input"]');
    const labelSave = panel.querySelector('[data-act="label-save"]');
    const saveLabel = () => setPrinterLabel(p.id, labelInput.value);
    labelSave.onclick = saveLabel;
    labelInput.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); saveLabel(); } };
    const reactivateBtn = panel.querySelector('[data-act="reactivate"]');
    if (reactivateBtn) reactivateBtn.onclick = () => reactivatePrinter(p.id);
    return panel;
  }

  // Drag-to-reorder der Drucker-Reiter (HTML5 DnD), Spiegel der booklist-Logik.
  function wirePrinterTabDrag(btn, pool) {
    let dragId = null;
    btn.addEventListener('dragstart', (e) => { dragId = btn.dataset.pid; e.dataTransfer.effectAllowed = 'move'; });
    btn.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; });
    btn.addEventListener('drop', (e) => {
      e.preventDefault();
      const targetId = btn.dataset.pid;
      if (!dragId || dragId === targetId) return;
      const ids = pool.map(p => p.id);
      const from = ids.indexOf(dragId), to = ids.indexOf(targetId);
      ids.splice(from, 1); ids.splice(to, 0, dragId);
      reorderPrinters(ids);
    });
  }

  async function setPrinterDuplex(id, duplex) {
    const r = await fetch('/api/printers/duplex', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id, duplex}),
    });
    if (r.ok) showMsg('Duplex-Modus gespeichert');
    else showMsg('Duplex konnte nicht gespeichert werden');
    await _applyPoolResponse(r);
  }

  async function removePrinter(id) {
    const r = await fetch('/api/printers/remove', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id}),
    });
    if (r.ok) showMsg('Drucker entfernt');
    else if (r.status === 400) showMsg('Drucker noch beschäftigt — bitte warten');
    else showMsg('Drucker konnte nicht entfernt werden');
    await _applyPoolResponse(r);
  }

  async function reorderPrinters(ids) {
    const r = await fetch('/api/printers/reorder', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids}),
    });
    await _applyPoolResponse(r);
  }

  async function reactivatePrinter(id) {
    const r = await fetch('/api/printers/reactivate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id}),
    });
    if (r.ok) showMsg('Drucker wieder aktiv');
    else if (r.status === 400) showMsg('Drucker ist nicht fehlerhaft');
    else showMsg('Aktivieren fehlgeschlagen');
    await _applyPoolResponse(r);
  }

  async function addPrinter(name, label) {
    const body = {name};
    if (label && label.trim()) body.label = label.trim();
    const r = await fetch('/api/printers/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (r.ok) { showMsg('Drucker hinzugefügt'); await _applyPoolResponse(r); }
    else { const t = await r.text().catch(() => ''); showMsg('Hinzufügen fehlgeschlagen' + (t ? ` (${t})` : '')); }
  }

  async function setPrinterLabel(id, label) {
    const body = {id};
    if (label && label.trim()) body.label = label.trim();
    const r = await fetch('/api/printers/label', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    if (r.ok) showMsg('Anzeigename gespeichert');
    else showMsg('Anzeigename konnte nicht gespeichert werden');
    await _applyPoolResponse(r);
  }

  function openPrinterAddRow(pool) {
    const row = document.getElementById('printer-add-row');
    const sel = document.getElementById('printer-add-select');
    const nameInput = document.getElementById('printer-add-name');
    if (nameInput) nameInput.value = '';
    const inPool = new Set(pool.map(p => p.name));
    const device = (printerDeviceInfo.printers || []).filter(n => !inPool.has(n));
    const opts = [];
    if (!inPool.has(null)) opts.push('<option value="">Standarddrucker</option>');
    for (const n of device) opts.push(`<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`);
    if (!opts.length) {
      sel.innerHTML = '<option value="">— keine weiteren Drucker verfügbar —</option>';
    } else {
      sel.innerHTML = opts.join('');
    }
    row.style.display = '';
    sel.focus();
  }

  async function initPrinterPoolUI() {
    activePrinterTabId = null;
    document.getElementById('printer-add-row').style.display = 'none';
    await refreshPrinterDeviceInfo();
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

  // ---- Druckerwarteschlange (Host-Tab) ----
  // Live-Status des Drucker-Pools (Last, druckt/wartet je Drucker) plus die
  // zentrale Warteschlange (Aufträge ohne zugewiesenen Drucker) als Liste:
  // Schüler, Klasse, Auftraggeber. Daten aus dem State-Snapshot
  // (`state.printers`/`state.print_queue_summary`); der Server pusht auf jeden
  // Druck-Übergang einen frischen Snapshot (print_queue._notify_all), darum
  // folgt die Box ohne Polling.
  function renderPrintQueue() {
    const box = document.getElementById('print-queue-box');
    if (!box) return;
    const pool = state.printers || [];
    const summary = state.print_queue_summary || {};
    const waiting = summary.waiting || 0;
    const waitingList = summary.waiting_list || [];
    if (!pool.length) {
      box.innerHTML = '<p class="hint">Kein Drucker konfiguriert — Druck nicht möglich. In den Einstellungen Drucker hinzufügen.</p>';
      return;
    }
    const rows = pool.map(p => {
      const printing = p.printing_name;
      // Alle an OS gesendeten, aber (noch) nicht aktiv druckenden Jobs
      // (Spiegel von `_Slots.jobs` ohne den printing-Job). `spooled_names`
      // ist die vollständige Liste; `spooled_name` der älteste (Kompat).
      const spooledList = Array.isArray(p.spooled_names) && p.spooled_names.length
        ? p.spooled_names
        : (p.spooled_name ? [p.spooled_name] : []);
      const spooledNames = spooledList.map(n => `„${escapeHtml(n)}"`).join(', ');
      let dot, status;
      if (p.faulty) {
        // Hängender Drucker: blockierte Aufträge zählen über `load` mit, werden
        // aber nicht als druckend/wartend gelistet (serverseitig ausgeschlossen).
        dot = 'fault';
        status = `<span class="txt-danger">⚠ fehlerhaft</span>` +
          (p.load > 0 ? ` — ${p.load} blockiert` : '');
      } else if (printing && spooledNames) {
        dot = 'busy';
        status = `druckt „${escapeHtml(printing)}" · als nächstes ${spooledNames}`;
      } else if (printing) {
        dot = 'busy';
        status = `druckt „${escapeHtml(printing)}"`;
      } else if (spooledNames) {
        dot = 'busy';
        status = `gesendet, wartet auf Druck: ${spooledNames}`;
      } else if (p.load > 0) {
        // Kein aktiver Druck, aber Slot belegt → blockierte Aufträge, die bei
        // der Reaktivierung gerade vom Scheduler geräumt werden (Transient).
        dot = 'busy';
        status = `${p.load} blockiert (wird geräumt)`;
      } else {
        dot = 'idle';
        status = '<span style="opacity:.5">bereit</span>';
      }
      return `<tr>
        <td>${escapeHtml(printerLabel(p))}</td>
        <td><span class="pq-dot pq-${dot}" aria-hidden="true"></span> ${status}</td>
      </tr>`;
    }).join('');
    // Zentrale Warteschlange: jeder wartende Auftrag mit Schüler, Klasse,
    // Auftraggeber (Host / Helfer namentlich) + erlaubte Drucker (Allowlist
    // der Klasse zum Enqueue-Zeitpunkt; „alle" = kein Filter). Position =
    // Minimum über alle erlaubten Drucker, wie viele Aufträge dort noch vor
    // diesem liegen (0 = druckt, 1 = gesendet/wartet, 2 = erster zentraler
    // Wartender bei vollem Drucker). Rollen-gerecht geordnet (HOST>HELFER>SCHÜLER).
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
      ? `<table class="pq-wait-table">
          <thead><tr><th>#</th><th>Schüler</th><th>Klasse</th><th>Auftraggeber</th><th>Drucker</th></tr></thead>
          <tbody>${waitRows}</tbody>
        </table>`
      : '<p class="hint" style="margin:0">Zentrale Warteschlange leer.</p>';
    box.innerHTML = `
      <div class="pq-section">Drucker (${pool.length})</div>
      <table class="pq-table">
        <thead><tr><th>Drucker</th><th>Status</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="pq-section">Zentrale Warteschlange (${waiting})</div>
      ${waitBlock}`;
  }

  // Drucker-Displays (Bildschirme neben den Druckern) als Unter-Reiter im
  // „Displays"-Hauptreiter der „Drucker"-Karte: pro verbundenem Display ein
  // Reiter (Label = Display-Name, sonst Short-ID, plus Statuspunkt
  // grau=unautorisiert / grün=autorisiert und ein × zum Trennen). Beim ersten
  // Öffnen eines Display-Reiters steht dort die Code-Eingabe; nach
  // Autorisation: Name-Feld, Light/Dark-Schieberegler und eine Box-Ansicht der
  // zugewiesenen Drucker (Drag umsortieren, × entfernen, „+"-Box zum
  // Hinzufügen). Daten aus `state.printer_displays`; der Server pusht bei
  // jeder Änderung einen frischen Snapshot. `assigned_printer_ids: null` =
  // alle Pool-Drucker (Default), geordnete Liste = Teilmenge in dieser
  // Reihenfolge. Reiter kommen/verschwinden automatisch mit dem
  // Verbindungsstand.
  function renderPrinterDisplays() {
    const tabList = document.getElementById('pd-tab-list');
    const panelsHost = document.getElementById('pd-panels-displays');
    if (!tabList || !panelsHost) return;
    const displays = state.printer_displays || [];
    const pool = state.printers || [];
    // Aktiver Sub-Reiter aufräumen, falls sein Display verschwunden ist
    // (Spiegel der Klassen-Tab-Logik in applyState).
    if (activePdTab && !displays.some(d => d.display_id === activePdTab)) {
      activePdTab = null;
    }
    // Tab-Leiste: je Display ein Reiter — Label = Name (falls gesetzt), sonst
    // der Registrierungs-Code (visuelle Zuordnung vor der Freischaltung), plus
    // Statuspunkt und × zum Verbieten (Spiegel der Klassen-Tab-×).
    tabList.innerHTML = displays.map(d => {
      const short = d.display_id.slice(0, 6);
      const code = d.registration_code || short;
      const lbl = escapeHtml(d.label && d.label.trim() ? d.label : code);
      // Punkt = Verbindungsstatus: grün, wenn ein Display mit diesem Token
      // geöffnet ist (WS verbunden), sonst grau.
      const dotCls = d.connected ? 'pd-dot-green' : 'pd-dot-gray';
      const title = d.connected ? 'verbunden' : 'nicht verbunden';
      return `<button class="pd-tab${activePdTab === d.display_id ? ' active' : ''}" data-pd-tab="${escapeHtml(d.display_id)}" title="${title}"><span class="pd-tab-dot ${dotCls}" aria-hidden="true"></span>${lbl} <span class="pd-tab-close" data-pd-close="${escapeHtml(d.display_id)}" title="Display verbieten" aria-label="Display verbieten">×</span></button>`;
    }).join('');
    const d = displays.find(x => x.display_id === activePdTab);
    if (!d) { panelsHost.innerHTML = ''; return; }
    // Fokus-Schutz: wird gerade im Name-Feld getippt, das Panel nicht neu
    // aufbauen (sonst fliegt Fokus + Wert bei jedem eintreffenden Snapshot).
    // Tabs werden dennoch aktualisiert. Die nächste Änderung ohne Fokus holt nach.
    const ae = document.activeElement;
    if (ae && ae.classList && ae.classList.contains('pdd-name') && panelsHost.contains(ae)) {
      return;
    }
    const short = d.display_id.slice(0, 6);
    const code = d.registration_code || short;
    const did = escapeHtml(d.display_id);
    const ids = d.assigned_printer_ids; // null = alle, sonst geordnete Liste
    const byId = {};
    pool.forEach(p => { byId[p.id] = p; });
    const boxPrinters = ids === null ? pool : ids.map(pid => byId[pid]).filter(Boolean);
    const assignedIds = ids === null ? pool.map(p => p.id) : ids;
    const available = pool.filter(p => !assignedIds.includes(p.id));  // für „+ Drucker"

    // Scanner: gleiche Zuweisungs-Mechanik wie Drucker oben, gegen
    // `state.printer_scanners`/`assigned_scanner_ids`. Nur autorisierte
    // Scanner sind wählbar (unautorisierte tauchen erst nach Freischaltung
    // im eigenen „Scanner"-Reiter auf).
    const scannerPool = (state.printer_scanners || []).filter(s => s.authorized);
    const sByIdMap = {};
    scannerPool.forEach(s => { sByIdMap[s.scanner_id] = s; });
    const sids = d.assigned_scanner_ids; // null = alle, sonst geordnete Liste
    const boxScanners = sids === null ? scannerPool : sids.map(sid => sByIdMap[sid]).filter(Boolean);
    const assignedSids = sids === null ? scannerPool.map(s => s.scanner_id) : sids;
    const availableScanners = scannerPool.filter(s => !assignedSids.includes(s.scanner_id));

    // Eine gemeinsame Box-Reihe für Drucker UND Scanner, in der vom Host frei
    // wählbaren Reihenfolge (`item_order`, Mirror server-seitiger
    // AppState._ordered_display_items — dieselbe Reihenfolge bestimmt auch
    // die Spaltenreihenfolge am physischen Drucker-Display). Items ohne
    // Eintrag in `item_order` hängen stabil ans Ende (erst Drucker, dann
    // Scanner, `Array.prototype.sort` ist stabil).
    const order = d.item_order || [];
    const orderIndex = {};
    order.forEach((k, i) => { orderIndex[k] = i; });
    const combined = [
      ...boxPrinters.map(p => ({ kind: 'printer', id: p.id, label: printerLabel(p) })),
      ...boxScanners.map(s => ({ kind: 'scanner', id: s.scanner_id, label: scannerLabel(s) })),
    ];
    combined.sort((a, b) => {
      const ai = orderIndex[`${a.kind}:${a.id}`] ?? order.length;
      const bi = orderIndex[`${b.kind}:${b.id}`] ?? order.length;
      return ai - bi;
    });
    const boxes = combined.map(item => {
      const idEsc = escapeHtml(item.id);
      const removeLabel = item.kind === 'scanner' ? 'Scanner entfernen' : 'Drucker entfernen';
      return `<div class="pd-box" draggable="true" data-kind="${item.kind}" data-pid="${idEsc}" data-display="${did}">
        <span class="pd-box-name">${escapeHtml(item.label)}</span>
        <button class="pd-box-remove" data-kind="${item.kind}" data-pd-remove="${idEsc}" data-display="${did}" title="Entfernen" aria-label="${removeLabel}">×</button>
      </div>`;
    }).join('');
    const addPrinterBox = available.length
      ? `<div class="pd-box pd-box-add" data-kind="printer" data-display="${did}" title="Drucker hinzufügen">+ Drucker</div>`
      : '';
    const addScannerBox = availableScanners.length
      ? `<div class="pd-box pd-box-add" data-kind="scanner" data-display="${did}" title="Scanner hinzufügen">+ Scanner</div>`
      : '';
    const boxGrid = `<div class="pd-box-grid">${boxes}${addPrinterBox}${addScannerBox}</div>`
      + (combined.length || available.length || availableScanners.length
        ? '' : '<p class="hint" style="margin-top:6px">Kein Drucker im Pool, kein freigeschalteter Scanner.</p>');
    const combinedSection = `<div class="pdd-section-label">Drucker und Scanner (${combined.length})</div>${boxGrid}`;

    if (!d.authorized) {
      // Unautorisiert: Display zeigt den Code (visuelle Zuordnung). Freischaltung
      // per Namens-Eingabe (+ Einschalten). Drucker/Scanner lassen sich schon
      // vor dem Einschalten zuordnen (Boxen + „+"-Popover unten).
      panelsHost.innerHTML = `<div class="pdd-panel" data-display="${did}">
        <div class="pdd-row" data-display="${did}">
          <span class="pdd-id">Code: ${escapeHtml(code)}</span>
          <input class="pdd-name pdd-enable-name" type="text" placeholder="Name" autocomplete="off" data-display="${did}">
          <button class="secondary pdd-enable" data-display="${did}">Einschalten</button>
        </div>
        ${combinedSection}
      </div>`;
      panelsHost.querySelector('.pdd-enable-name')?.focus();
      wirePdBoxesDnD(panelsHost);
      return;
    }
    // Autorisiertes Panel: Name-Feld, Speichern, QR-Button (Token-URL dieses
    // Displays), Theme-Schieberegler, Drucker-/Scanner-Boxen.
    panelsHost.innerHTML = `<div class="pdd-panel" data-display="${did}">
      <div class="pdd-field-row">
        <span class="pdd-field-label">Name</span>
        <input class="pdd-name" type="text" value="${escapeHtml(d.label || '')}" placeholder="${escapeHtml(short)}" autocomplete="off" data-display="${did}">
        <button class="secondary pdd-name-save" data-display="${did}">Speichern</button>
        <button class="secondary pdd-qr" data-display="${did}" title="QR-Code für dieses Display (mit Token) anzeigen">QR</button>
        <label class="switch pdd-theme" title="Darstellung auf dem Display: Hell oder Dunkel">
          <input type="checkbox" class="pdd-theme-toggle" data-display="${did}"${d.theme === 'dark' ? ' checked' : ''}>
          <span class="track"></span>
          Dunkel
        </label>
      </div>
      ${combinedSection}
    </div>`;
    wirePdBoxesDnD(panelsHost);
  }

  // ---- Scan-Stationen (`/scan-station`) ----
  // Reiter unten im Live-Ausgabe-Kasten — je verbundene Station einer, plus
  // „+" für QR/URL. Spiegel von `renderPrinterDisplays()`, nur ohne die
  // Drucker-Zuweisung: eine Station braucht nur Name, Theme und (falls gerade
  // jemand angemeldet ist) den Freigeben-Knopf. Die Reiter sind Umschalter —
  // ein zweiter Klick klappt das Panel wieder zu.
  function renderScanStations() {
    const tabList = document.getElementById('ss-tab-list');
    const panelsHost = document.getElementById('ss-panels');
    if (!tabList || !panelsHost) return;
    const stations = state.scan_stations || [];
    // Aktives Panel aufräumen, wenn seine Station verschwunden ist.
    if (activeSsTab && !stations.some(s => s.station_id === activeSsTab)) activeSsTab = null;

    tabList.innerHTML = stations.map(s => {
      const code = s.registration_code || s.station_id.slice(0, 6);
      const lbl = escapeHtml(s.label && s.label.trim() ? s.label : code);
      const dotCls = s.connected ? 'pd-dot-green' : 'pd-dot-gray';
      const title = s.connected ? 'verbunden' : 'nicht verbunden';
      const active = activeSsTab === s.station_id ? ' active' : '';
      return `<button class="pd-tab${active}" data-ss-tab="${escapeHtml(s.station_id)}" title="${title}"><span class="pd-tab-dot ${dotCls}" aria-hidden="true"></span>${lbl} <span class="pd-tab-close" data-ss-close="${escapeHtml(s.station_id)}" title="Station verbieten" aria-label="Station verbieten">×</span></button>`;
    }).join('');

    const s = stations.find(x => x.station_id === activeSsTab);
    if (!s) { panelsHost.innerHTML = ''; return; }
    // Fokus-Schutz wie beim Drucker-Display: wird gerade im Namensfeld
    // getippt, das Panel nicht neu aufbauen (sonst fliegt Fokus + Wert bei
    // jedem eintreffenden Snapshot).
    const ae = document.activeElement;
    if (ae && ae.classList && ae.classList.contains('ssd-name') && panelsHost.contains(ae)) return;

    const sid = escapeHtml(s.station_id);
    const short = s.station_id.slice(0, 6);
    const code = s.registration_code || short;
    if (!s.authorized) {
      panelsHost.innerHTML = `<div class="pdd-panel" data-station="${sid}">
        <div class="pdd-row" data-station="${sid}">
          <span class="pdd-id">Code: ${escapeHtml(code)}</span>
          <input class="ssd-name ssd-enable-name" type="text" placeholder="Name" autocomplete="off" data-station="${sid}">
          <button class="secondary ssd-enable" data-station="${sid}">Einschalten</button>
        </div>
        <p class="hint">Der Code steht auf dem Bildschirm der Station — so lässt sich der richtige Reiter zuordnen.</p>
      </div>`;
      panelsHost.querySelector('.ssd-enable-name')?.focus();
      return;
    }
    // Belegt: wer gerade angemeldet ist + Knopf zum sofortigen Freigeben
    // (z. B. Schüler ist weggegangen, ohne „Fertig" zu drücken).
    const busyRow = s.student_id
      ? `<div class="ssd-busy">
           <span>Angemeldet: <b>${escapeHtml(s.student_name || '—')}</b>${s.worker_ready ? '' : ' <span class="hint">(lädt…)</span>'}</span>
           <button class="secondary ssd-release" data-station="${sid}">Freigeben</button>
         </div>`
      : '<div class="ssd-busy hint">Wartet auf einen Zettel-Code.</div>';
    panelsHost.innerHTML = `<div class="pdd-panel" data-station="${sid}">
      <div class="pdd-field-row">
        <span class="pdd-field-label">Name</span>
        <input class="ssd-name" type="text" value="${escapeHtml(s.label || '')}" placeholder="${escapeHtml(short)}" autocomplete="off" data-station="${sid}">
        <button class="secondary ssd-name-save" data-station="${sid}">Speichern</button>
        <button class="secondary ssd-qr" data-station="${sid}" title="QR-Code für diese Station (mit Token) anzeigen">QR</button>
        <label class="switch pdd-theme" title="Eingabeart auf der Station: Kamera-Scanner oder Tastatur-/Handscanner (wie im Helferclient)">
          <input type="checkbox" class="ssd-mode-toggle" data-station="${sid}"${s.input_mode === 'manual' ? ' checked' : ''}>
          <span class="track"></span>
          Manuell
        </label>
        <label class="switch pdd-theme" title="Darstellung auf der Station: Hell oder Dunkel">
          <input type="checkbox" class="ssd-theme-toggle" data-station="${sid}"${s.theme === 'dark' ? ' checked' : ''}>
          <span class="track"></span>
          Dunkel
        </label>
      </div>
      ${busyRow}
    </div>`;
  }

  // ---- Drucker-Scanner (`/drucker-scan`) ----
  // Reiter im „Scanner"-Hauptreiter der „Drucker"-Karte — je verbundener
  // Scanner einer, plus „+" für QR/URL. Spiegel von `renderScanStations()`,
  // nur ohne Schüler-Bindung: ein Scanner braucht nur Name, Theme und
  // Eingabeart (vom Host vorgegeben, wie bei der Scan-Station). Die Reiter
  // sind Umschalter — ein zweiter Klick klappt das Panel wieder zu.
  function renderPrinterScanners() {
    const tabList = document.getElementById('psc-tab-list');
    const panelsHost = document.getElementById('psc-panels');
    if (!tabList || !panelsHost) return;
    const scanners = state.printer_scanners || [];
    if (activePscTab && !scanners.some(s => s.scanner_id === activePscTab)) activePscTab = null;

    tabList.innerHTML = scanners.map(s => {
      const code = s.registration_code || s.scanner_id.slice(0, 6);
      const lbl = escapeHtml(s.label && s.label.trim() ? s.label : code);
      const dotCls = s.connected ? 'pd-dot-green' : 'pd-dot-gray';
      const title = s.connected ? 'verbunden' : 'nicht verbunden';
      const active = activePscTab === s.scanner_id ? ' active' : '';
      return `<button class="pd-tab${active}" data-psc-tab="${escapeHtml(s.scanner_id)}" title="${title}"><span class="pd-tab-dot ${dotCls}" aria-hidden="true"></span>${lbl} <span class="pd-tab-close" data-psc-close="${escapeHtml(s.scanner_id)}" title="Scanner verbieten" aria-label="Scanner verbieten">×</span></button>`;
    }).join('');

    const s = scanners.find(x => x.scanner_id === activePscTab);
    if (!s) { panelsHost.innerHTML = ''; return; }
    const ae = document.activeElement;
    if (ae && ae.classList && ae.classList.contains('pscd-name') && panelsHost.contains(ae)) return;

    const sid = escapeHtml(s.scanner_id);
    const short = s.scanner_id.slice(0, 6);
    const code = s.registration_code || short;
    if (!s.authorized) {
      panelsHost.innerHTML = `<div class="pdd-panel" data-scanner="${sid}">
        <div class="pdd-row" data-scanner="${sid}">
          <span class="pdd-id">Code: ${escapeHtml(code)}</span>
          <input class="pscd-name pscd-enable-name" type="text" placeholder="Name" autocomplete="off" data-scanner="${sid}">
          <button class="secondary pscd-enable" data-scanner="${sid}">Einschalten</button>
        </div>
        <p class="hint">Der Code steht auf dem Bildschirm des Scanners — so lässt sich der richtige Reiter zuordnen.</p>
      </div>`;
      panelsHost.querySelector('.pscd-enable-name')?.focus();
      return;
    }
    panelsHost.innerHTML = `<div class="pdd-panel" data-scanner="${sid}">
      <div class="pdd-field-row">
        <span class="pdd-field-label">Name</span>
        <input class="pscd-name" type="text" value="${escapeHtml(s.label || '')}" placeholder="${escapeHtml(short)}" autocomplete="off" data-scanner="${sid}">
        <button class="secondary pscd-name-save" data-scanner="${sid}">Speichern</button>
        <button class="secondary pscd-qr" data-scanner="${sid}" title="QR-Code für diesen Scanner (mit Token) anzeigen">QR</button>
        <label class="switch pdd-theme" title="Eingabeart am Scanner: Kamera oder manuelles Eingabefeld">
          <input type="checkbox" class="pscd-mode-toggle" data-scanner="${sid}"${s.input_mode === 'manual' ? ' checked' : ''}>
          <span class="track"></span>
          Manuell
        </label>
        <label class="switch pdd-theme" title="Darstellung am Scanner: Hell oder Dunkel">
          <input type="checkbox" class="pscd-theme-toggle" data-scanner="${sid}"${s.theme === 'dark' ? ' checked' : ''}>
          <span class="track"></span>
          Dunkel
        </label>
      </div>
      <p class="hint">Diesem Gerät wird bei den Drucker-Displays im Reiter „Displays" eine Drucker-Scanner-Box zugeordnet.</p>
    </div>`;
  }

  // ---- Drucker-Scanner: API-Aufrufe ----
  async function showPrinterScannerQr(scannerId) {
    const q = scannerId ? `?scanner_id=${encodeURIComponent(scannerId)}` : '';
    const r = await fetch(`/api/drucker-scan/qr${q}`);
    if (!r.ok) { showMsg('QR für Drucker-Scanner konnte nicht geladen werden'); return; }
    const d = await r.json();
    showQr(d.qr, d.url || '');
  }
  async function pscPost(path, body, okMsg, failMsg) {
    const r = await fetch(`/api/drucker-scan/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (r.ok) { if (okMsg) showMsg(okMsg); return true; }
    const d = await r.json().catch(() => ({}));
    showMsg(d.detail || failMsg);
    return false;
  }
  async function enablePrinterScanner(scannerId, label, btn) {
    if (!label || !label.trim()) return;
    if (btn) await busy(btn, () =>
      pscPost('enable', { scanner_id: scannerId, label: label.trim() },
        'Drucker-Scanner freigeschaltet', 'Freischaltung fehlgeschlagen'));
  }
  const setPscLabel = (scannerId, label) =>
    pscPost('label', { scanner_id: scannerId, label: label || '' },
      'Scanner-Name gespeichert', 'Name konnte nicht gespeichert werden');
  const setPscTheme = (scannerId, dark) =>
    pscPost('theme', { scanner_id: scannerId, theme: dark ? 'dark' : 'light' },
      null, 'Theme konnte nicht gesetzt werden');
  const setPscInputMode = (scannerId, manual) =>
    pscPost('input-mode', { scanner_id: scannerId, input_mode: manual ? 'manual' : 'camera' },
      null, 'Eingabeart konnte nicht gesetzt werden');
  async function forgetPrinterScanner(scannerId) {
    if (await pscPost('forget', { scanner_id: scannerId }, 'Scanner verboten', 'Verbieten fehlgeschlagen')) {
      activePscTab = null;
    }
  }

  // Druckzeile unten im Pairing-Kasten: „Scan-Station: [Schüler] [Erstellen]".
  // Öffnet den Druck-Dialog für den Zettel (Barcode + Bücherliste zum
  // Abhaken) eines wartenden Schülers dieser Klasse — die Handy-Alternative
  // zum Pairing-Code daneben. Knopf heißt bewusst „Erstellen" statt
  // „Drucken", weil der Dialog dahinter NICHT zwingend druckt (s.
  // `stationSheetDialog`): „Erstellen und Drucken" aktiviert den Schüler UND
  // druckt, das reine „Erstellen" nur aktiviert (Zettel-Code/Fortschritt),
  // der physische Druck kann jederzeit über den Nachdruck-Knopf im „Aktuell
  // in Ausgabe"-Kästchen nachgeholt werden.
  function renderCtxStationPrint(id) {
    const el = document.querySelector(`.ctx-station[data-ctx-id="${id}"]`);
    if (!el) return;
    const ctx = (state.contexts || {})[id];
    const pending = ((ctx && ctx.queue) || []).filter(q => q.status === 'pending');
    // Auswahl über den Re-Render hinweg halten (Snapshots treffen laufend ein).
    const prev = el.querySelector('select')?.value || '';
    const opts = pending
      .map(q => `<option value="${q.student_id}">${escapeHtml(q.lastname)}, ${escapeHtml(q.firstname)}</option>`)
      .join('');
    el.innerHTML = `<div class="ctx-station-row">
      <span class="ctx-station-label">Scan-Station:</span>
      <select class="ctx-station-sel" data-ctx-id="${id}" ${pending.length ? '' : 'disabled'}>${opts || '<option value="">keine wartenden Schüler</option>'}</select>
      <button class="secondary" data-action="print-station-sheet" data-ctx-id="${id}" ${pending.length ? '' : 'disabled'}>Erstellen</button>
    </div>`;
    const sel = el.querySelector('select');
    if (sel && prev && pending.some(q => String(q.student_id) === prev)) sel.value = prev;
  }

  // HTML5-Drag der Drucker-/Scanner-Boxen (Spiegel von wirePrinterTabDrag /
  // onBlDrag*): eine Box auf eine andere ziehen → gemeinsame Reihenfolge neu
  // festlegen und an /reorder-items schicken. `pdDragPid`/`pdDragKind` halten
  // den gezogenen Eintrag über dragstart→drop hinweg (Drucker- ODER Scanner-
  // Box, s. data-kind) — beide Kinds lassen sich gegeneinander umsortieren
  // (eine gemeinsame Box-Reihe, s. renderPrinterDisplays).
  let pdDragKind = 'printer';
  function wirePdBoxesDnD(host) {
    host.querySelectorAll('.pd-box[draggable="true"]').forEach(box => {
      box.addEventListener('dragstart', (e) => {
        pdDragPid = box.dataset.pid;
        pdDragKind = box.dataset.kind || 'printer';
        box.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
      });
      box.addEventListener('dragend', () => {
        box.classList.remove('dragging');
        pdDragPid = null;
      });
      box.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; });
      box.addEventListener('drop', (e) => {
        e.preventDefault();
        const targetPid = box.dataset.pid;
        const targetKind = box.dataset.kind || 'printer';
        if (!pdDragPid || (pdDragPid === targetPid && pdDragKind === targetKind)) return;
        reorderPdBoxItems(box.dataset.display, pdDragKind, pdDragPid, targetKind, targetPid);
      });
    });
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
      // Name: bevorzugt aus dem Queue-Eintrag (findStudentInState), Fallback
      // auf die am Helfer hinterlegten Namen — letzteres greift bes. bei
      // transienten Lupe-Schülern, die in KEINER Queue stehen und sonst als
      // „–" erschienen.
      const student = h.student_id ? findStudentInState(h.student_id) : null;
      const ln = student ? student.lastname : h.student_lastname;
      const fn = student ? student.firstname : h.student_firstname;
      const form = (student ? student.form : h.student_form || '').replace(/^Klasse\s+/i, '');
      const hasName = ln || fn;
      // Klasse nur bei Lupe-Zuweisung in Klammern zeigen — bei Queue-Aufrufen
      // impliziert der Klassen-Tab die Klasse, eine Wiederholung wäre Rauschen.
      // Präfix „Klasse " streichen (s. scan-render.js), sonst „(Klasse 10a)".
      const classTag = (h.student_via_search && form) ? ` <span class="helper-student-class">(${escapeHtml(form)})</span>` : '';
      const studentName = hasName ? `${escapeHtml(ln || '')}${ln ? ', ' : ''}${escapeHtml(fn || '')}${classTag}` : '–';
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

  // Rote Info-Hinweise zu einem Schüler (Anmelde-/Zahlstatus aus IServ) —
  // wandern aus der ehemaligen Info-Spalte in die Status-Spalte: ergänzend
  // hinter dem Status (Wartend/Aktiv/X/Y/Leihschein/Übersprungen), und bei
  // „fertig" ersetzt der Hinweis das „Fertig"-Badge. Immer rot (badge-info-warn).
  //   • `null` = noch nicht abgefragt → kein Badge, statt einen unbekannten
  //     Stand als „okay" darzustellen.
  //   • „Nicht angemeldet" steht allein: ohne Anmeldung liefert IServ zu Zahlung
  //     und Anträgen nichts Belastbares (s. QueueStudent.set_info_flags).
  // Liefert ein Array von Badge-HTML-Strings (leer = kein Hinweis bekannt).
  function hintBadges(s) {
    const out = [];
    if (s.enrolled === false) {
      out.push('<span class="badge badge-info-warn">Nicht angemeldet</span>');
    } else if (s.enrolled) {
      if (s.paid === false) {
        // Konkreter Restbetrag statt pauschalem Hinweis; fehlt `amount_open`
        // (IServ liefert ihn nicht immer), bleibt es beim allgemeinen Text.
        const open = typeof s.amount_open === 'number'
          ? `${s.amount_open.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} € offen`
          : 'Bezahlung ausstehend';
        out.push(`<span class="badge badge-info-warn">${open}</span>`);
      }
      if (s.remission_pending) out.push('<span class="badge badge-info-warn">Ermäßigungsantrag ausstehend</span>');
      if (s.exemption_pending) out.push('<span class="badge badge-info-warn">Befreiungsantrag ausstehend</span>');
    }
    return out;
  }

  // Gemeinsame Statusdarstellung für die Klassenliste und „Aktuell in Ausgabe“.
  // Die Now-Serving-Kachel übernimmt denselben Inhalt, verwendet aber bewusst
  // ihre kompakte Textgestaltung statt der Tabellen-Badges.
  function activeStatusDetails(s) {
    if (s.slip_signing) return { content: 'Unterschrift' };
    if (s.slip_printed) return { content: 'Leihschein gedruckt' };
    if (s.slip_status === 'printing') return { content: 'Leihschein druckt' };
    if (s.slip_status === 'waiting') return { content: 'Leihschein wartet' };
    if (s.books_total == null) return { content: 'Lädt' };
    // Zettel-/Stations-Fluss (Schüler ohne Handy, s. docs/PLAN.md §3.8):
    // „Bücher sammeln" statt der Zahl, solange NICHT an einer Station
    // angemeldet und noch nicht alles ausgeliehen — ein übernehmender Helfer
    // hat Vorrang (dessen Badge/Fortschritt zählt dann). Sobald alles
    // ausgeliehen ist, bleibt es bei der Zahl stehen (fällt unten durch).
    const atStation = !!s.station_name;
    const fullyLent = s.books_total > 0 && s.books_done >= s.books_total;
    if (s.station_zettel_printed && !s.assigned_helper_name && !atStation && !fullyLent) {
      return { content: 'Bücher sammeln' };
    }
    if (s.books_total) {
      const loaned = s.loaned_at_load || 0;
      const sessionX = s.books_done - loaned;
      const sessionY = s.books_total - loaned;
      // „Bestand leer": aus der AKTIVEN Y-Zählung raus (wie ausgeblendet),
      // aber die wahre Gesamtzahl bleibt in Klammern sichtbar, solange noch
      // offene Bestand-leer-Bücher da sind — verschwindet automatisch, sobald
      // das Buch tatsächlich gescannt wird (s. server/sessions.py::mark_book_done).
      const emptyOut = s.books_empty_outstanding || 0;
      const totalStr = emptyOut > 0 ? `${s.books_total - emptyOut} (${s.books_total})` : `${s.books_total}`;
      const sessionYStr = emptyOut > 0 ? `${sessionY - emptyOut} (${sessionY})` : `${sessionY}`;
      const sessionCombined = `${sessionX}/${sessionYStr}`;
      const totalCombined = `${s.books_done}/${totalStr}`;
      // Zweizeilig nur, wenn sich session- und gesamt-Zahlen tatsächlich
      // unterscheiden — bei Gleichstand (kein Vorbestand) reicht die
      // gesamt-Zeile allein.
      if (sessionCombined !== totalCombined && (atStation || (loaned > 0 && sessionY > 0))) {
        return {
          progress: true,
          title: `seit Aufrufen ${sessionX}/${sessionY} (offene vorgemerkte) · insgesamt ${s.books_done}/${s.books_total} (ausgeliehene/angemeldete)`,
          content: `<span class="q-progress-main">${sessionCombined} ohne Mjb</span><span class="q-progress-sub">${totalCombined} gesamt</span>`,
        };
      }
      return {
        title: 'ausgegebene / angemeldete Bücher',
        content: `${totalCombined} gesamt`,
      };
    }
    return { content: 'Aktiv' }; // geladen, aber ohne Bücher
  }

  function renderActiveStatusBadge(s) {
    const details = activeStatusDetails(s);
    const progress = details.progress ? ' q-progress' : '';
    const title = details.title ? ` title="${escapeHtml(details.title)}"` : '';
    return `<span class="badge badge-active${progress}"${title}>${details.content}</span>`;
  }

  function renderActiveStatusText(s) {
    const details = activeStatusDetails(s);
    return `<span class="ns-status${details.progress ? ' ns-status-progress' : ''}">${details.content}</span>`;
  }

  // Queue-Tabelle eines Klassen-Tabs.
  function renderCtxQueue(id) {
    const ctx = (state.contexts || {})[id];
    const queue = (ctx && ctx.queue) || [];
    const tbody = document.querySelector(`[data-ctx-queue="${id}"]`);
    const qc = document.querySelector(`[data-ctx-qc="${id}"]`);
    if (qc) qc.textContent = `(${queue.filter(q => q.status === 'pending' || q.status === 'absent').length} offen / ${queue.length} gesamt)`;
    if (!tbody) return;
    if (!queue.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="opacity:.4;text-align:center">Keine Schüler — Klasse hinzufügen</td></tr>';
      return;
    }
    tbody.innerHTML = queue.map(s => {
      // Status-Spalte eines aktiven Schülers zeigt den Fortschritt statt starr
      // „Aktiv": X/Y (ausgegebene/angemeldete Bücher) → „Leihschein wartet" →
      // „Leihschein druckt" → „Leihschein gedruckt" → ggf. „Unterschrift".
      //
      // X/Y ist zweizeilig, wenn beim Aufrufen schon Bücher ausgeliehen waren
      // (`loaned_at_load > 0`): oben die session-basierten Zahlen (seit Aufrufen
      // ausgeliehene / beim Aufrufen noch offene vorgemerkte — dieselben wie im
      // Druck- und Nächster-Schüler-Hinweis des Helfers), unten die gesamt
      // (ausgeliehene / angemeldete). Ohne Vorbestand sind beide identisch →
      // eine Zeile.
      //
      // Rote Info-Hinweise (Nicht angemeldet, Bezahlung ausstehend, …) hängen
      // ergänzend hinter dem Status; bei „fertig" ersetzt der Hinweis das
      // „Fertig"-Badge (s. hintBadges). Die ehemalige Info-Spalte ist entfallen.
      const hints = hintBadges(s);
      let statusBadge;
      if (s.status === 'active') {
        statusBadge = renderActiveStatusBadge(s);
      } else if (s.status === 'done') {
        // Fertig: statt „Fertig" der rote Hinweis, falls einer bekannt ist;
        // ohne Hinweis bleibt es beim grünen „Fertig". Ein abwesender Schüler,
        // dessen Bücher ein Helfer eingescant hat (`helper_scanned`), wird als
        // „Fertig (abwesend)" ausgewiesen — der physische Stapel muss separat
        // übergeben werden. Zusätzlich, sobald die Lehrkraft den Leihschein
        // entgegengenommen hat (read-only Anzeige, s. renderCtxTeacher/
        // teacherSlipStat — der Host setzt das Flag nicht selbst), ein kleines
        // grünes Häkchen mit Titel-Tooltip.
        const doneBadge = s.helper_scanned
          ? '<span class="badge badge-done">Fertig (abwesend)</span>'
          : '<span class="badge badge-done">Fertig</span>';
        statusBadge = hints.length ? hints.join('') : doneBadge;
        if (ctx.done_collected === true && s.slip_collected) {
          statusBadge += `<span class="badge badge-done" title="Leihschein von der Lehrkraft entgegengenommen">Leihschein ✓</span>`;
        }
      } else {
        const badgeClass = { pending: 'badge-pending', skipped: 'badge-skipped', absent: 'badge-absent' }[s.status] || '';
        const statusLabel = { pending: 'Wartend', skipped: 'Übersprungen', absent: 'Abwesend' }[s.status] || s.status;
        statusBadge = `<span class="badge ${badgeClass}">${statusLabel}</span>`;
      }
      // Hinweise ergänzend hinter den Status — außer bei „fertig", dort schon
      // als Ersatz für „Fertig" gesetzt.
      const trailingHints = s.status === 'done' ? '' : hints.join('');
      // Bei aktiver Bearbeitung die Status-Reihenfolge im selben Badge-Kasten
      // beibehalten: Status → Helfer → Hinweis. Der Name kommt aus dem
      // serverseitig aufgelösten Queue-Feld, nicht aus dem Bearer-Token.
      const helperBadge = s.status === 'active' && s.assigned_helper_name
        ? `<span class="badge badge-helper">${ICO_HELPER} ${escapeHtml(s.assigned_helper_name)}</span>`
        : '';
      // Scan-Station-Bearbeitung: gleiche Stelle/Aufbau wie der Helfer-Badge,
      // Host-Symbol statt Helfer-Symbol (s. ICO_HOST). Ein übernehmender
      // Helfer hat Vorrang (Stationsname würde ohnehin verschwinden, sobald
      // der Schüler dort abgemeldet wird).
      const stationBadge = s.status === 'active' && s.station_name && !s.assigned_helper_name
        ? `<span class="badge badge-helper">${ICO_HOST} ${escapeHtml(s.station_name)}</span>`
        : '';
      const statusCell = `<div class="q-status">${statusBadge}${helperBadge}${stationBadge}${trailingHints}</div>`;
      const pairBtn = (state.modus_b && state.modus_b.open && ctx.live_ausgabe !== false)
        ? `<button class="success" data-action="pair-student" data-student-id="${s.student_id}">Pairing</button> ` : '';
      // Übersprungener Schüler: Helfer scannt die Bücher stellvertretend über
      // einen Einmal-QR (POST /api/helper-scan/start → QR-Modal).
      const helperScanBtn = (state.modus_b && state.modus_b.open)
        ? `<button class="secondary" data-action="helper-scan" data-student-id="${s.student_id}">Bücher als Helfer einscannen</button> ` : '';
      // Trennen: löst Helfer-/Schüler-Verbindung und setzt den Schüler zurück auf "Wartend".
      const disconnectBtn = `<button class="secondary" data-action="disconnect" data-student-id="${s.student_id}">Trennen</button>`;
      const actions = s.status === 'pending'
        ? `${pairBtn}<button class="secondary" data-action="skip" data-student-id="${s.student_id}">Überspringen</button> ${disconnectBtn}`
        : s.status === 'absent'
          // Abwesend: kein Pairing (Schülerclient-Zuordnung blockiert), aber
          // Helfer-Scan-QR + Überspringen + Trennen wie bei einem Wartenden.
          ? `${helperScanBtn}<button class="secondary" data-action="skip" data-student-id="${s.student_id}">Überspringen</button> ${disconnectBtn}`
          : s.status === 'active'
            ? `<button class="success" data-action="finish" data-student-id="${s.student_id}">Abschließen</button> <button class="secondary" data-action="skip" data-student-id="${s.student_id}">Abbrechen</button> ${disconnectBtn}`
            : s.status === 'skipped'
              ? helperScanBtn
              : '';
      return `<tr class="${s.status === 'active' ? 'row-active' : ''}">
        <td>${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}</td>
        <td>${escapeHtml((s.form || '').replace(/^Klasse\s+/i, ''))}</td>
        <td>${statusCell}</td>
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

  // Schüler NUR von der Scan-Station abmelden (Station fällt sofort auf
  // „Zettel-Code scannen" zurück) — anders als der allgemeine „Trennen"-
  // Knopf bleibt der Schüler dabei aktiv/zugewiesen, nur die Stationsbindung
  // endet. Der Server pusht danach von selbst einen neuen Snapshot.
  async function disconnectFromStation(studentId) {
    const r = await fetch('/api/scan-station/release-student', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: studentId }),
    });
    if (!r.ok) { const d = await r.json().catch(() => ({})); showMsg(d.detail || 'Abmelden fehlgeschlagen'); }
  }

  // "Druckauftrag aktualisieren" im gelben Scan-Station-Gate-Hinweis (s.
  // renderCtxNowServing): der Host übernimmt einen pausierten
  // Druckermodus-Auftrag manuell (kein erlaubter Drucker mehr auf einem
  // Display sichtbar) und macht ihn zu einem regulären Host-Auftrag.
  async function adoptStationPrintJob(studentId, jobId, btn) {
    const errEl = document.getElementById(`ns-sg-err-${studentId}`);
    const picker = stationGatePickers[studentId];
    const ids = picker ? picker.getSelectedIds() : [];
    if (!ids.length) {
      if (errEl) { errEl.textContent = 'Bitte mindestens einen Drucker auswählen.'; errEl.style.display = ''; }
      return;
    }
    if (btn) btn.disabled = true;
    try {
      const r = await fetch(`/api/print-queue/${encodeURIComponent(jobId)}/adopt-station`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ printers: ids }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        if (errEl) { errEl.textContent = d.detail || 'Aktualisieren fehlgeschlagen'; errEl.style.display = ''; }
        if (btn) btn.disabled = false;
        return;
      }
      // Erfolg: der nächste state_snapshot (via print-queue-Notify) zeigt den
      // Auftrag als normalen Host-Auftrag ohne station_gate mehr an.
    } catch {
      if (errEl) { errEl.textContent = 'Aktualisieren fehlgeschlagen'; errEl.style.display = ''; }
      if (btn) btn.disabled = false;
    }
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

  // Wie showMsg, aber ohne Auto-Dismiss: der Toast bleibt stehen, bis der
  // Aufrufer ihn via dismissToast(el) wieder entfernt. Für Hinweise, die einen
  // Statusübergang begleiten (z. B. „Lade Klasse …" bis zum fertigen Laden).
  function showMsgPersistent(text, variant) {
    const stack = document.getElementById('toast-stack');
    const t = document.createElement('div');
    t.className = 'toast' + (variant ? ` toast-${variant}` : '');
    t.textContent = text;
    stack.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    return t;
  }

  function dismissToast(el) {
    if (!el) return;
    el.classList.remove('show');
    const drop = () => el.remove();
    el.addEventListener('transitionend', drop, { once: true });
    setTimeout(drop, 400);  // Fallback (reduced-motion)
  }

  // Macht einen per showMsgPersistent erzeugten Toast zum finalen Hinweis: Text
  // wird in-place ausgetauscht (kein zweiter Toast, der kurzzeitig neben dem
  // ersten sichtbar wäre) und danach wie showMsg nach 4 s auto-dismissed.
  function finalizeToast(el, text) {
    if (!el) { showMsg(text); return; }
    el.textContent = text;
    setTimeout(() => {
      el.classList.remove('show');
      const drop = () => el.remove();
      el.addEventListener('transitionend', drop, { once: true });
      setTimeout(drop, 400);  // Fallback (reduced-motion)
    }, 4000);
  }

  // ---- Persistenter Druck-Status-Toast (keyed per job_id) ----
  // Live aus der internen Druckerwarteschlange: Position / „wird gedruckt" /
  // „gedruckt". Bleibt stehen, solange der Auftrag wartet oder druckt, und
  // auto-dismissed 4 s nach „gedruckt"/Fehler (wie showMsg). Erscheint nur am
  // startenden Host — der Server adressiert per sid (state.host_ws_by_sid).
  const printToasts = {};  // job_id -> { el, timer }

  function printToastText(msg, finalOk) {
    // Host-Präfix: „Leihschein von <Name>, <Vorname> (<Klasse>)" — msg.name
    // ist bereits im Format „Nachname, Vorname (Form)" (server: slip_name).
    // Fällt kein Schülername an, nur „Leihschein ".
    const prefix = msg.name ? `Leihschein von ${msg.name} ` : 'Leihschein ';
    // Peer-Fehler (Auftrag am hängenden Drucker / kein Ersatzdrucker) —
    // bleibt stehen. Text vom Server („Es dauert ungewöhnlich lange … -
    // <Label>"), keine clientseitige +1-Hochzählung der Position mehr.
    if (msg.peer_error) {
      return msg.msg || 'Es dauert ungewöhnlich lange, vielleicht liegt ein Fehler vor.';
    }
    if (finalOk === false) {
      // Stall (Inaktivität): lange Hinweismeldung direkt anzeigen.
      if (msg.stalled) return msg.msg || 'Druck dauert ungewöhnlich lange';
      return `${prefix}— Druck fehlgeschlagen: ${msg.msg || ''}`;
    }
    // <pname> = Anzeige-Label „Label (Systemname)" (msg.printer_label); erst
    // gesetzt, wenn der Auftrag einem Drucker zugewiesen ist (Slot-Job). Der
    // Drucker rückt inline in den Satz („… von <pname> gedruckt."), kein
    // separates „ — Drucker …"-Suffix mehr. Zentrale-Warteschlangen-Jobs ohne
    // zugewiesenen Drucker → Kurzform ohne Drucker.
    const pname = msg.printer_label;
    // „Wird gedruckt" erst, wenn das OS aktiv druckt — nicht schon bei Slot-
    // Position 0 (dort: „wartet an <pname> auf Druck"). Position 1 zeigt die
    // Warteschlangenposition (nicht mehr „wartet auf Druck"). Ab Position 2
    // Kurzform ohne Drucker.
    if (finalOk === true) {
      return pname ? `${prefix}von ${pname} gedruckt.` : `${prefix}gedruckt.`;
    }
    if (msg.status === 'printing') {
      return pname ? `${prefix}wird von ${pname} gedruckt…` : `${prefix}wird gedruckt…`;
    }
    if (typeof msg.position === 'number' && msg.position === 0) {
      return pname ? `${prefix}wartet an ${pname} auf Druck…` : `${prefix}wartet auf Druck…`;
    }
    if (typeof msg.position === 'number' && msg.position === 1) {
      return pname
        ? `${prefix}an 1. Druckerwarteschlangenposition von ${pname}`
        : `${prefix}an 1. Druckerwarteschlangenposition`;
    }
    if (typeof msg.position === 'number' && msg.position >= 2) {
      return `${prefix}an ${msg.position}. Druckerwarteschlangenposition`;
    }
    return `${prefix}in Druckerwarteschlange…`;
  }

  function _printToastEl(jobId, warn) {
    let entry = printToasts[jobId];
    if (!entry) {
      const stack = document.getElementById('toast-stack');
      const t = document.createElement('div');
      t.className = 'toast' + (warn ? ' toast-warn' : '');
      stack.appendChild(t);
      requestAnimationFrame(() => t.classList.add('show'));
      // status/allowedPrinters/peerError/jobId: laufend aus print_progress/
      // print_result gepflegt (renderPrintToast). expanded = UI-Zustand des
      // eingebetteten Drucker-Nachfrage-Pickers; userToggled merkt sich, ob
      // der Host das schon manuell umgeschaltet hat (verhindert, dass ein
      // erneutes Auto-Öffnen eine bewusste Nutzer-Entscheidung überschreibt).
      entry = {
        el: t, timer: null, status: null, allowedPrinters: null,
        peerError: false, expanded: false, userToggled: false, jobId,
      };
      printToasts[jobId] = entry;
    } else if (warn) {
      entry.el.classList.add('toast-warn');
    }
    return entry;
  }

  // Baut den Toast-Inhalt aus einer Text-Zeile + (wenn der Auftrag noch
  // unzugewiesen in der Warteschlange steht, `status === 'queued'`) einer
  // klickbaren Umschaltfläche zum Drucker-Nachfrage-Picker. Sind ALLE
  // erlaubten Drucker fehlerhaft (`peer_error`), klappt der Picker beim
  // ersten Eintreffen automatisch auf; ansonsten öffnet ihn ein Klick auf den
  // Hinweistext — in beiden Fällen bleibt die Warteschlange unangetastet
  // (kein Pause-Mechanismus), ein zwischenzeitliches Dispatchen des Auftrags
  // schließt den Picker beim nächsten Aufruf automatisch wieder (status
  // wechselt weg von 'queued').
  function renderPrintToast(entry, msg, finalOk) {
    entry.status = msg.status;
    entry.allowedPrinters = (msg.allowed_printers === undefined) ? null : msg.allowed_printers;
    entry.peerError = !!msg.peer_error;
    entry.jobId = msg.job_id;
    const queued = entry.status === 'queued';
    if (!queued) {
      entry.expanded = false;
    } else if (entry.peerError && !entry.userToggled && !entry.expanded) {
      entry.expanded = true;
    }
    entry.el.replaceChildren();
    const line = document.createElement('div');
    line.className = 'toast-line';
    line.textContent = printToastText(msg, finalOk);
    if (queued) {
      line.classList.add('toast-line-clickable');
      line.addEventListener('click', () => {
        entry.userToggled = true;
        entry.expanded = !entry.expanded;
        renderPrintToast(entry, msg, finalOk);
      });
    }
    entry.el.appendChild(line);
    if (queued && entry.expanded) {
      const pool = hostPrinterPool();
      const preselect = entry.allowedPrinters === null
        ? pool.map(p => p.id) : entry.allowedPrinters;
      const pickerDiv = document.createElement('div');
      pickerDiv.className = 'toast-printer-picker';
      entry.el.appendChild(pickerDiv);
      const picker = mountPrinterPicker(pickerDiv, pool, preselect);
      const errEl = document.createElement('p');
      errEl.className = 'toast-printer-picker-error';
      errEl.style.display = 'none';
      entry.el.appendChild(errEl);
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'toast-printer-update-btn';
      btn.textContent = 'Druckauftrag aktualisieren';
      btn.addEventListener('click', async () => {
        const ids = picker.getSelectedIds();
        if (!ids.length) {
          errEl.textContent = 'Bitte mindestens einen Drucker auswählen.';
          errEl.style.display = '';
          return;
        }
        btn.disabled = true;
        try {
          const r = await fetch(`/api/print-queue/${encodeURIComponent(entry.jobId)}/printers`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ printers: ids }),
          });
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            errEl.textContent = d.detail || 'Aktualisieren fehlgeschlagen';
            errEl.style.display = '';
            btn.disabled = false;
            return;
          }
          // Erfolg: der nächste print_progress-Push baut den Toast anhand
          // des neuen Status neu auf (Position/Zuweisung).
        } catch {
          errEl.textContent = 'Aktualisieren fehlgeschlagen';
          errEl.style.display = '';
          btn.disabled = false;
        }
      });
      entry.el.appendChild(btn);
    }
  }

  function showPrintProgress(msg) {
    const entry = _printToastEl(msg.job_id, !!msg.peer_error);
    renderPrintToast(entry, msg, null);
  }

  function showPrintResult(msg) {
    const entry = _printToastEl(msg.job_id, !msg.ok);
    renderPrintToast(entry, msg, msg.ok);
    if (entry.timer) clearTimeout(entry.timer);
    // peer_error („Fehler bei vorigem Auftrag") und stalled (Inaktivität)
    // bleiben stehen, bis ein neuer Druck kommt oder die Seite neu lädt —
    // kein Auto-Dismiss wie bei „gedruckt"/generischem Fehler.
    if (msg.peer_error || msg.stalled) return;
    entry.timer = setTimeout(() => {
      entry.el.classList.remove('show');
      const drop = () => { entry.el.remove(); delete printToasts[msg.job_id]; };
      entry.el.addEventListener('transitionend', drop, { once: true });
      setTimeout(drop, 400);  // Fallback (reduced-motion)
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
    const saveBtn = document.getElementById('settings-dialog-save');
    const cancelBtn = document.getElementById('settings-dialog-cancel');
    const addBtn = document.getElementById('printer-add-btn');
    const addRow = document.getElementById('printer-add-row');
    const addSel = document.getElementById('printer-add-select');
    const addConfirm = document.getElementById('printer-add-confirm');
    const addCancel = document.getElementById('printer-add-cancel');
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
    const prev = { ts: tsCb.checked, pdf: pdfCb.checked, fix: fixCb.checked, slip: slipCb.checked };
    // Drucker-Pool-Reiter aufbauen (rein lesend vom Server; Mutationen gehen
    // sofort über eigene Endpunkte, nicht über den „Speichern"-Fluss).
    initPrinterPoolUI();
    addBtn.onclick = () => openPrinterAddRow((state.printers || []).map(p => p.name));
    addConfirm.onclick = async () => {
      const v = addSel.value;
      if (v === '' && !(addSel.options.length && addSel.options[0].value === '')) return;  // „keine verfügbar"-Platzhalter
      const labelEl = document.getElementById('printer-add-name');
      const label = labelEl ? labelEl.value : '';
      await addPrinter(v === '' ? null : v, label);
      addRow.style.display = 'none';
    };
    addCancel.onclick = () => { addRow.style.display = 'none'; };
    const addNameInput = document.getElementById('printer-add-name');
    if (addNameInput) addNameInput.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); addConfirm.click(); } };
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
      const empty = new Set(((d && d.empty) || []).filter(isbn => cat[isbn]));
      blData[grade] = {
        catalog: cat, order, saved: order.slice(), loaded: true,
        hidden, savedHidden: new Set(hidden),
        empty, savedEmpty: new Set(empty),
      };
    }
    renderBooklistList();
  }

  // Sichtbar-Indikator: grünes Kästchen mit Haken (geometrisch, aus Geraden).
  const ICON_CHECK = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="5,12 10,17 19,7"/></svg>';
  // Ausgeblendet-Indikator: rotes Kästchen mit Verbotssymbol (Kreis + 45°-Strich).
  const ICON_NO = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="8.2"/><line x1="6.2" y1="6.2" x2="17.8" y2="17.8"/></svg>';
  // Bestand-leer-Indikator: gelbes Kästchen mit „0" (ohne Kreis).
  const ICON_EMPTY = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><text x="12" y="12" text-anchor="middle" dominant-baseline="central" font-size="20" font-weight="700" fill="currentColor" stroke="none">0</text></svg>';

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
      const empty = !hidden && data.empty.has(isbn);
      const stateClass = hidden ? ' bo-hidden' : empty ? ' bo-empty' : '';
      const icon = hidden ? ICON_NO : empty ? ICON_EMPTY : ICON_CHECK;
      const title = hidden ? 'Wieder einblenden' : empty ? 'Als Bestand leer markiert — Klick: Ausblenden' : 'Klick: Als Bestand leer markieren';
      return `<div class="bo-row${stateClass}" draggable="true" data-idx="${i}" data-isbn="${escapeHtml(isbn)}">`
        + `<span class="bo-grip">⠿</span>`
        + `<span class="bo-num">${i + 1}</span>`
        + `<span class="bo-fach">${escapeHtml(b.subject || '')}</span>`
        + `<span class="bo-title">${escapeHtml(b.title || isbn)}</span>`
        + `<button type="button" class="bo-hide-btn" title="${title}" aria-label="${title}">${icon}</button></div>`;
    }).join('');
    list.querySelectorAll('.bo-row').forEach(row => {
      row.addEventListener('dragstart', onBlDragStart);
      row.addEventListener('dragover', onBlDragOver);
      row.addEventListener('drop', onBlDrop);
      row.addEventListener('dragend', onBlDragEnd);
      row.querySelector('.bo-hide-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        onBlCycleStatus(row.dataset.isbn);
      });
    });
  }

  // 3-Wege-Zyklus: da → Bestand leer → ausgeblendet → da. „Bestand leer" liegt
  // dabei nur einen Klick von „da" entfernt (häufigerer Fall — Bestand
  // vorübergehend leer, bleibt buchbar), „ausgeblendet" ist der drastischere,
  // zwei Klicks entfernte Schritt (Reihe nicht mehr vorgemerkt/buchbar).
  function onBlCycleStatus(isbn) {
    const data = blData[blActiveGrade];
    if (!data) return;
    if (data.hidden.has(isbn)) {
      data.hidden.delete(isbn);
    } else if (data.empty.has(isbn)) {
      data.empty.delete(isbn);
      data.hidden.add(isbn);
    } else {
      data.empty.add(isbn);
    }
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
      const emptyArr = [...d.empty].sort();
      const savedEmptyArr = [...d.savedEmpty].sort();
      if (JSON.stringify(emptyArr) !== JSON.stringify(savedEmptyArr)) {
        d.savedEmpty = new Set(d.empty);
        saveBooklistEmpty(Number(g), emptyArr);
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
  async function saveBooklistEmpty(grade, empty) {
    try {
      await fetch('/api/booklist-empty', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ grade, empty }),
      });
    } catch (_) {}
  }

  // ---- Tastatur-Shortcuts für den Operator (nur wenn eingeloggt) ----
  // Esc schließt das oberste Overlay; "n" gibt dem einzigen unbesetzten Helfer
  // den nächsten Schüler. Nichts Destruktives liegt auf einer Taste.
  // Tipp-Eingaben werden nicht abgefangen.
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
    if (e.key === 'n') {
      const free = Object.values(state.helpers || {}).filter(h => h.student_id === null);
      if (free.length === 1) {
        const h = free[0];
        // „Nächster" zieht aus der Klasse des Helfers; ohne Bindung aus beliebigem
        // Kontext mit wartendem Schüler.
        const ctx = h.context_id ? (state.contexts || {})[h.context_id] : null;
        const hasPending = ctx
          ? (ctx.queue || []).some(s => s.status === 'pending' || s.status === 'absent')
          : Object.values(state.contexts || {}).some(c => (c.queue || []).some(s => s.status === 'pending' || s.status === 'absent'));
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
  // panel-new: Drucker-Checkbox ↔ Live-Ausgabe-Schalter Kopplung. Letzten
  // Drucker bei aktiver Live-Ausgabe nicht abwählen (roter Hinweis, revert);
  // sonst Auswahl persistieren + Gate (Schalter disabled bei keinem Drucker).
  document.getElementById('new-class-printers').addEventListener('change', (e) => {
    const el = e.target;
    if (!el || !el.dataset || !el.dataset.pid) return;
    const liveCb = document.getElementById('new-class-live-ausgabe');
    if (newClassPrinterCheckedCount() === 0 && liveCb && liveCb.checked) {
      el.checked = true;
      const warn = document.getElementById('new-class-printer-warn');
      if (warn) { warn.textContent = 'Zuerst Live-Ausgabe schließen'; warn.style.display = ''; }
      return;
    }
    saveClassPrintersSelection(getSelectedClassPrinterNames());
    updateNewClassLiveGate();
  });
  document.getElementById('new-class-live-ausgabe').addEventListener('change', (e) => {
    const el = e.target;
    const on = !!el.checked;
    if (on && newClassPrinterCheckedCount() === 0) {
      el.checked = false;
      const warn = document.getElementById('new-class-live-warn');
      if (warn) { warn.textContent = 'Es ist mindestens ein Drucker auszuwählen'; warn.style.display = ''; }
      return;
    }
    saveClassLiveAusgabe(on);
    updateNewClassLiveGate();
    updateNewClassDoneOpts();
  });
  document.getElementById('new-class-slip-trigger').addEventListener('change', (e) => {
    saveClassSlipTrigger(e.target.value);
  });
  // panel-new: Fertig-Optionen unter der Live-Ausgabe. „Leihschein
  // eingesammelt" ausgegraut, wenn „Leihschein unterschrieben" nicht angehakt
  // ist; beide ausgegraut, wenn Live-Ausgabe aus. Persistiert für das nächste
  // Öffnen in localStorage (s. loadClassDoneSigned/-Collected); tatsächlich
  // an den Server geschickt wird die Auswahl erst mit `/api/open-class`
  // (s. `openClass`) — die eigentliche Fertig-Übergang-Funktion folgt später.
  function updateNewClassDoneOpts() {
    const live = document.getElementById('new-class-live-ausgabe');
    const signed = document.getElementById('new-class-done-signed');
    const collected = document.getElementById('new-class-done-collected');
    const slipTrigger = document.getElementById('new-class-slip-trigger');
    if (!live || !signed || !collected) return;
    const liveOn = !!live.checked;
    signed.disabled = !liveOn;
    collected.disabled = !liveOn || !signed.checked;
    if (slipTrigger) slipTrigger.disabled = !liveOn;
  }
  document.getElementById('new-class-done-signed').addEventListener('change', (e) => {
    saveClassDoneSigned(e.target.checked);
    updateNewClassDoneOpts();
  });
  document.getElementById('new-class-done-collected').addEventListener('change', (e) => {
    saveClassDoneCollected(e.target.checked);
  });
  document.getElementById('mb-open-btn').addEventListener('click', openModusB);
  document.getElementById('mb-pause-btn').addEventListener('click', (e) => toggleModusBPause(e.currentTarget));
  document.getElementById('mb-allow-scans-btn').addEventListener('click', (e) => allowThreeModusBScans(e.currentTarget));
  document.getElementById('mb-close-btn').addEventListener('click', closeModusB);
  // Delegiert (innerHTML wird bei jedem Snapshot neu aufgebaut, s.
  // renderModusBControl): Klick auf „Freischalten" eines gelisteten,
  // verbundenen-aber-unautorisierten iPads.
  document.getElementById('mb-display-pending').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="authorize-display"]');
    if (btn) authorizeDisplay(btn.dataset.displayId, btn);
    const ignoreBtn = e.target.closest('[data-action="ignore-display"]');
    if (ignoreBtn) ignoreDisplay(ignoreBtn.dataset.displayId);
    const disconnectBtn = e.target.closest('[data-action="disconnect-display"]');
    if (disconnectBtn) disconnectDisplay(disconnectBtn.dataset.displayId, disconnectBtn);
  });
  document.getElementById('show-mb-qr-btn').addEventListener('click', showMbQr);
  document.getElementById('show-display-qr-btn').addEventListener('click', showDisplayQr);
  // Hauptreiter der „Drucker"-Karte (Warteschlange / Displays / Scanner).
  document.getElementById('printer-main-tabs').addEventListener('click', (e) => {
    const tab = e.target.closest('[data-pmt-tab]');
    if (!tab) return;
    activePrinterMainTab = tab.dataset.pmtTab;
    renderPrinterMainTabs();
  });
  // Drucker-Display-Reiter (im „Displays"-Hauptreiter): „+" öffnet den QR zum
  // Verbinden eines neuen Displays; Klick auf einen Reiter klappt sein Panel
  // auf/zu, × verbietet das Display endgültig (mit Bestätigungsdialog).
  document.getElementById('pd-tab-add').addEventListener('click', showPrinterDisplayQr);
  document.getElementById('pd-tabs-bar').addEventListener('click', async (e) => {
    const closeEl = e.target.closest('[data-pd-close]');
    if (closeEl) {
      e.stopPropagation();
      const id = closeEl.dataset.pdClose;
      const d = (state.printer_displays || []).find(x => x.display_id === id);
      const name = d ? (d.label && d.label.trim() ? d.label : d.display_id.slice(0, 6)) : id;
      if (!await confirmDialog(
        `Drucker-Display „${name}" verbieten? Das Display wird gesperrt und kann nicht wieder aktiviert werden.`,
        'Verbieten'
      )) return;
      forgetPrinterDisplay(id);
      return;
    }
    const tab = e.target.closest('[data-pd-tab]');
    if (!tab) return;
    // Umschalter: derselbe Reiter erneut → Panel wieder zuklappen (Spiegel
    // der Scan-Stations-Reiter).
    activePdTab = activePdTab === tab.dataset.pdTab ? null : tab.dataset.pdTab;
    renderPrinterDisplays();
  });
  // Drucker-Displays: delegierte Handler für die per innerHTML gerenderten
  // Elemente im aktiven Display-Panel (Namen Einschalten, Name Speichern,
  // Drucker-Box entfernen/hinzufügen, Theme-Toggle).
  const pdBox = document.getElementById('pd-panels-displays');
  pdBox.addEventListener('click', (e) => {
    const enableBtn = e.target.closest('.pdd-enable');
    if (enableBtn) {
      const row = enableBtn.closest('.pdd-row');
      const name = row.querySelector('.pdd-enable-name').value;
      enablePrinterDisplay(row.dataset.display, name, enableBtn);
      return;
    }
    const removeBtn = e.target.closest('.pd-box-remove');
    if (removeBtn) {
      if (removeBtn.dataset.kind === 'scanner') {
        removePdScanner(removeBtn.dataset.display, removeBtn.dataset.pdRemove);
      } else {
        removePdPrinter(removeBtn.dataset.display, removeBtn.dataset.pdRemove);
      }
      return;
    }
    const addBox = e.target.closest('.pd-box-add');
    if (addBox) {
      openPdAddMenu(addBox.dataset.display, addBox, addBox.dataset.kind || 'printer');
      return;
    }
    const nameSave = e.target.closest('.pdd-name-save');
    if (nameSave) {
      const inp = nameSave.closest('.pdd-panel').querySelector('.pdd-name');
      inp.blur();  // Fokus raus, damit der nächste Snapshot das Panel + Reiter-Label aktualisiert
      setPdLabel(nameSave.dataset.display, inp.value);
      return;
    }
    const qrBtn = e.target.closest('.pdd-qr');
    if (qrBtn) {
      showPdTokenQr(qrBtn.dataset.display);
      return;
    }
  });
  // Theme-Schieberegler → sofort setzen.
  pdBox.addEventListener('change', (e) => {
    if (e.target.matches('.pdd-theme-toggle')) {
      setPdTheme(e.target.dataset.display, e.target.checked);
    }
  });
  // Namen-Eingabe per Enter abschicken (Freischalten im unautorisierten Panel
  // bzw. Speichern im autorisierten Panel).
  pdBox.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    if (e.target.matches('.pdd-enable-name')) {
      const row = e.target.closest('.pdd-row');
      enablePrinterDisplay(row.dataset.display, e.target.value, row.querySelector('.pdd-enable'));
    } else if (e.target.matches('.pdd-name')) {
      const save = e.target.closest('.pdd-panel').querySelector('.pdd-name-save');
      if (save) save.click();
    }
  });
  // Scan-Stations-Reiter im Live-Ausgabe-Kasten: „+" zeigt QR/URL zum Öffnen
  // einer neuen Station, ein Reiterklick klappt sein Panel auf/zu, × verbietet
  // die Station endgültig (mit Bestätigungsdialog).
  document.getElementById('ss-tab-add').addEventListener('click', () => showScanStationQr());
  document.getElementById('ss-tabs-bar').addEventListener('click', async (e) => {
    const closeEl = e.target.closest('[data-ss-close]');
    if (closeEl) {
      e.stopPropagation();
      const id = closeEl.dataset.ssClose;
      const s = (state.scan_stations || []).find(x => x.station_id === id);
      const name = s ? (s.label && s.label.trim() ? s.label : s.station_id.slice(0, 6)) : id;
      if (!await confirmDialog(
        `Scan-Station „${name}" verbieten? Die Station wird gesperrt und kann nicht wieder aktiviert werden.`,
        'Verbieten'
      )) return;
      forgetScanStation(id);
      return;
    }
    const tab = e.target.closest('[data-ss-tab]');
    if (!tab) return;
    // Umschalter: derselbe Reiter erneut → Panel wieder zuklappen.
    activeSsTab = activeSsTab === tab.dataset.ssTab ? null : tab.dataset.ssTab;
    renderScanStations();
  });
  // Delegierte Handler für die per innerHTML gerenderten Panel-Elemente
  // (Spiegel der Drucker-Display-Handler oben).
  const ssBox = document.getElementById('ss-panels');
  ssBox.addEventListener('click', (e) => {
    const enableBtn = e.target.closest('.ssd-enable');
    if (enableBtn) {
      const row = enableBtn.closest('.pdd-row');
      enableScanStation(row.dataset.station, row.querySelector('.ssd-enable-name').value, enableBtn);
      return;
    }
    const nameSave = e.target.closest('.ssd-name-save');
    if (nameSave) {
      const inp = nameSave.closest('.pdd-panel').querySelector('.ssd-name');
      inp.blur();  // Fokus raus, damit der nächste Snapshot Panel + Reiter aktualisiert
      setSsLabel(nameSave.dataset.station, inp.value);
      return;
    }
    const qrBtn = e.target.closest('.ssd-qr');
    if (qrBtn) { showScanStationQr(qrBtn.dataset.station); return; }
    const releaseBtn = e.target.closest('.ssd-release');
    if (releaseBtn) { releaseSsStudent(releaseBtn.dataset.station); }
  });
  ssBox.addEventListener('change', (e) => {
    if (e.target.matches('.ssd-theme-toggle')) setSsTheme(e.target.dataset.station, e.target.checked);
    else if (e.target.matches('.ssd-mode-toggle')) setSsInputMode(e.target.dataset.station, e.target.checked);
  });
  ssBox.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    if (e.target.matches('.ssd-enable-name')) {
      const row = e.target.closest('.pdd-row');
      enableScanStation(row.dataset.station, e.target.value, row.querySelector('.ssd-enable'));
    } else if (e.target.matches('.ssd-name')) {
      const save = e.target.closest('.pdd-panel').querySelector('.ssd-name-save');
      if (save) save.click();
    }
  });
  // Drucker-Scanner-Reiter im „Scanner"-Hauptreiter der „Drucker"-Karte:
  // Spiegel der Scan-Stations-Handler oben.
  document.getElementById('psc-tab-add').addEventListener('click', () => showPrinterScannerQr());
  document.getElementById('psc-tabs-bar').addEventListener('click', async (e) => {
    const closeEl = e.target.closest('[data-psc-close]');
    if (closeEl) {
      e.stopPropagation();
      const id = closeEl.dataset.pscClose;
      const s = (state.printer_scanners || []).find(x => x.scanner_id === id);
      const name = s ? (s.label && s.label.trim() ? s.label : s.scanner_id.slice(0, 6)) : id;
      if (!await confirmDialog(
        `Drucker-Scanner „${name}" verbieten? Das Gerät wird gesperrt und kann nicht wieder aktiviert werden.`,
        'Verbieten'
      )) return;
      forgetPrinterScanner(id);
      return;
    }
    const tab = e.target.closest('[data-psc-tab]');
    if (!tab) return;
    activePscTab = activePscTab === tab.dataset.pscTab ? null : tab.dataset.pscTab;
    renderPrinterScanners();
  });
  const pscBox = document.getElementById('psc-panels');
  pscBox.addEventListener('click', (e) => {
    const enableBtn = e.target.closest('.pscd-enable');
    if (enableBtn) {
      const row = enableBtn.closest('.pdd-row');
      enablePrinterScanner(row.dataset.scanner, row.querySelector('.pscd-enable-name').value, enableBtn);
      return;
    }
    const nameSave = e.target.closest('.pscd-name-save');
    if (nameSave) {
      const inp = nameSave.closest('.pdd-panel').querySelector('.pscd-name');
      inp.blur();
      setPscLabel(nameSave.dataset.scanner, inp.value);
      return;
    }
    const qrBtn = e.target.closest('.pscd-qr');
    if (qrBtn) { showPrinterScannerQr(qrBtn.dataset.scanner); return; }
  });
  pscBox.addEventListener('change', (e) => {
    if (e.target.matches('.pscd-theme-toggle')) setPscTheme(e.target.dataset.scanner, e.target.checked);
    else if (e.target.matches('.pscd-mode-toggle')) setPscInputMode(e.target.dataset.scanner, e.target.checked);
  });
  pscBox.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    if (e.target.matches('.pscd-enable-name')) {
      const row = e.target.closest('.pdd-row');
      enablePrinterScanner(row.dataset.scanner, e.target.value, row.querySelector('.pscd-enable'));
    } else if (e.target.matches('.pscd-name')) {
      const save = e.target.closest('.pdd-panel').querySelector('.pscd-name-save');
      if (save) save.click();
    }
  });
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
      case 'dismiss-code': dismissCode(el.dataset.code, el); break;
      case 'finish': finishStudent(parseInt(el.dataset.studentId)); break;
      case 'finish-signed': finishSignedStudent(parseInt(el.dataset.studentId)); break;
      case 'print': printLoanSlip(parseInt(el.dataset.studentId), el); break;
      case 'next-student': nextStudent(el.dataset.token); break;
      case 'remove-helper': removeHelper(el.dataset.token); break;
      case 'pair-student': pairStudent(parseInt(el.dataset.studentId)); break;
      case 'helper-scan': helperScan(parseInt(el.dataset.studentId)); break;
      case 'skip': skipStudent(parseInt(el.dataset.studentId)); break;
      case 'disconnect': disconnectStudent(parseInt(el.dataset.studentId)); break;
      case 'clear-book-alert': clearBookAlert(parseInt(el.dataset.studentId)); break;
      case 'station-disconnect': disconnectFromStation(parseInt(el.dataset.studentId)); break;
      case 'station-gate-adopt': adoptStationPrintJob(parseInt(el.dataset.studentId), el.dataset.jobId, el); break;
      case 'reprint-station-sheet': printStationSheet(parseInt(el.dataset.studentId), el, { reprint: true }); break;
      case 'ctx-reset': ctxResetQueue(id); break;
      case 'ctx-clear': ctxClearQueue(id); break;
      case 'ctx-disconnect-all': ctxDisconnectAll(id); break;
      case 'ctx-add-student': ctxAddSingleStudent(id); break;
      case 'teacher-qr': showTeacherQr(id); break;
      case 'teacher-authorize': authorizeTeacher(id); break;
      case 'teacher-disconnect': disconnectTeacher(id); break;
      case 'print-station-sheet': {
        // Schüler aus dem Select derselben Zeile — der Button trägt nur die
        // Klasse, damit die Auswahl über Re-Renders hinweg erhalten bleibt.
        const sel = el.closest('.ctx-station-row')?.querySelector('.ctx-station-sel');
        const sid = sel && sel.value ? parseInt(sel.value) : null;
        if (sid) printStationSheet(sid, el);
        break;
      }
    }
  }
  document.getElementById('class-panels').addEventListener('click', handleDelegatedAction);
  document.getElementById('class-panels').addEventListener('change', (e) => {
    const el = e.target;
    if (el.classList.contains('ctx-single-class')) ctxLoadStudents(el.dataset.ctxId);
    else if (el.classList.contains('ctx-single-student')) ctxOnStudentChange(el.dataset.ctxId);
    else if (el.classList.contains('printer-check')) setContextPrinters(el.dataset.ctxId, el);
    else if (el.hasAttribute('data-ctx-done-signed')) { updateCtxDoneOpts(el.dataset.ctxDoneSigned); setContextDoneOptions(el.dataset.ctxDoneSigned); }
    else if (el.hasAttribute('data-ctx-done-collected')) setContextDoneOptions(el.dataset.ctxDoneCollected);
    else if (el.hasAttribute('data-ctx-live')) setContextLiveAusgabe(el.dataset.ctxLive, el.checked, el);
    else if (el.hasAttribute('data-ctx-slip-trigger')) setContextSlipTrigger(el.dataset.ctxSlipTrigger, el.value, el);
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
      document.addEventListener('pointerdown', () => Beeper.initAudio(), { once: true });
    }
  });

// Zur Introspektion/Debugging zusätzlich auf window.__host verfügbar
// machen (rein additiv — der Code oben referenziert weiterhin die
// bare Bezeichner aus der gemeinsamen Skript-Scope, keine funktionale
// Abhängigkeit von window.__host).
window.__host.flashModusB = flashModusB;
window.__host.applyTheme = applyTheme;
window.__host.cycleTheme = cycleTheme;
window.__host.doLogin = doLogin;
window.__host.doLogout = doLogout;
window.__host.renderTabBar = renderTabBar;
window.__host.switchTab = switchTab;
window.__host.setActiveContext = setActiveContext;
window.__host.showPanel = showPanel;
window.__host.renderPanels = renderPanels;
window.__host.classSelectOptions = classSelectOptions;
window.__host.buildClassPanel = buildClassPanel;
window.__host.getAutoDoneSelection = getAutoDoneSelection;
window.__host.loadAutoDoneSelection = loadAutoDoneSelection;
window.__host.openClass = openClass;
window.__host.openTestConfig = openTestConfig;
window.__host.closeClass = closeClass;
window.__host.dropTab = dropTab;
window.__host.loadSchoolyears = loadSchoolyears;
window.__host.selectSchoolyear = selectSchoolyear;
window.__host.loadClasses = loadClasses;
window.__host.ctxLoadStudents = ctxLoadStudents;
window.__host.ctxOnStudentChange = ctxOnStudentChange;
window.__host.ctxAddSingleStudent = ctxAddSingleStudent;
window.__host.ctxResetQueue = ctxResetQueue;
window.__host.ctxClearQueue = ctxClearQueue;
window.__host.ctxDisconnectAll = ctxDisconnectAll;
window.__host.addHelper = addHelper;
window.__host.removeHelper = removeHelper;
window.__host.nextStudent = nextStudent;
window.__host.skipStudent = skipStudent;
window.__host.disconnectStudent = disconnectStudent;
window.__host.finishStudent = finishStudent;
window.__host.finishSignedStudent = finishSignedStudent;
window.__host.openPrintDialog = openPrintDialog;
window.__host.printLoanSlip = printLoanSlip;
window.__host.openModusB = openModusB;
window.__host.closeModusB = closeModusB;
window.__host.toggleModusBPause = toggleModusBPause;
window.__host.allowThreeModusBScans = allowThreeModusBScans;
window.__host.showMbQr = showMbQr;
window.__host.authorizeDisplay = authorizeDisplay;
window.__host.disconnectDisplay = disconnectDisplay;
window.__host.showDisplayQr = showDisplayQr;
window.__host.ignoreDisplay = ignoreDisplay;
window.__host.doPair = doPair;
window.__host.pairStudent = pairStudent;
window.__host.cancelArm = cancelArm;
window.__host.renderModusBControl = renderModusBControl;
window.__host.renderCtxPairing = renderCtxPairing;
window.__host.renderHostTab = renderHostTab;
window.__host.renderPrintQueue = renderPrintQueue;
window.__host.renderClassTab = renderClassTab;
window.__host.renderCtxNowServing = renderCtxNowServing;
window.__host.renderStatusBar = renderStatusBar;
window.__host.setForceTailscaleIp = setForceTailscaleIp;
window.__host.pushSavePdfLocally = pushSavePdfLocally;
window.__host.pushFixClassOnSlip = pushFixClassOnSlip;
window.__host.renderWorkerStatus = renderWorkerStatus;
window.__host.renderHelpers = renderHelpers;
window.__host.setHelperClass = setHelperClass;
window.__host.renderCtxQueue = renderCtxQueue;
window.__host.trapFocus = trapFocus;
window.__host.showQr = showQr;
window.__host.closeQr = closeQr;
window.__host.clearBookAlert = clearBookAlert;
window.__host.showMsg = showMsg;
window.__host.confirmDialog = confirmDialog;
window.__host.busy = busy;
window.__host.pushSlipDefault = pushSlipDefault;
window.__host.openSettingsDialog = openSettingsDialog;
window.__host.settingsOpen = settingsOpen;
window.__host.loadBooklistTabs = loadBooklistTabs;
window.__host.selectBooklistTab = selectBooklistTab;
window.__host.renderBooklistList = renderBooklistList;
window.__host.onBlCycleStatus = onBlCycleStatus;
window.__host.clearBlDropMarks = clearBlDropMarks;
window.__host.onBlDragStart = onBlDragStart;
window.__host.onBlDragOver = onBlDragOver;
window.__host.onBlDrop = onBlDrop;
window.__host.onBlDragEnd = onBlDragEnd;
window.__host.saveChangedBooklistOrders = saveChangedBooklistOrders;
window.__host.saveBooklistOrder = saveBooklistOrder;
window.__host.saveBooklistHidden = saveBooklistHidden;
window.__host.saveBooklistEmpty = saveBooklistEmpty;
window.__host.handleDelegatedAction = handleDelegatedAction;
window.__host.showPrintProgress = showPrintProgress;
window.__host.showPrintResult = showPrintResult;
