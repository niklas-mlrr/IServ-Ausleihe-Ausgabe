// web/host-ws.js — WebSocket-Verbindung + Nachrichten-Dispatch
// Teil des host.html-Frontends (siehe host-state.js/host-ws.js/host-render.js,
// in dieser Reihenfolge nach common.js eingebunden). Kein Build-Step: alle drei
// Dateien teilen sich eine gemeinsame Top-Level-Scope (klassische <script>-Tags),
// zusätzlich exponiert auf window.__host für Debug-/Introspektionszwecke.

window.__host = window.__host || {};

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
        else if (msg.type === 'print_progress') showPrintProgress(msg);
        else if (msg.type === 'print_result') showPrintResult(msg);
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
  // ---- State rendering ----
  function applyState(s) {
    state = s;
    if (!state.modus_b) state.modus_b = { open: false, pending: [], pending_count: 0, displays: [] };
    if (!state.contexts) state.contexts = {};
    // Neuer Pairing-Code? -> Beep + Blink, damit der Host es ohne Hinschauen merkt.
    const pc = state.modus_b.pending_count || 0;
    if (state.modus_b.open && prevPendingCount !== null && pc > prevPendingCount) {
      Beeper.playBeep();
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

// Zur Introspektion/Debugging zusätzlich auf window.__host verfügbar
// machen (rein additiv — der Code oben referenziert weiterhin die
// bare Bezeichner aus der gemeinsamen Skript-Scope, keine funktionale
// Abhängigkeit von window.__host).
window.__host.connectWs = connectWs;
window.__host.downloadLoanSlip = downloadLoanSlip;
window.__host.applyState = applyState;
window.__host.maybeCloseQrOnScan = maybeCloseQrOnScan;
window.__host.showBookAlert = showBookAlert;
