// web/scan-ws.js — WebSocket-Verbindung + Nachrichten-Dispatch
// Teil des scan.html-Frontends (siehe scan-state.js/scan-ws.js/scan-render.js,
// in dieser Reihenfolge nach html5-qrcode.min.js + common.js eingebunden).
// Kein Build-Step: alle drei Dateien teilen sich eine gemeinsame Top-Level-
// Scope (klassische <script>-Tags), zusätzlich exponiert auf window.__scan
// für Debug-/Introspektionszwecke.

window.__scan = window.__scan || {};

function handleServerMessage(msg) {
  if (msg.type === 'student_info') {
    studentActive = true;
    loadingStudent = false;  // Schüler geladen — Bücherliste ersetzt die Queue
    peeking = false;          // (neuer) Schüler geladen → keine Queue-Ansicht
    idleMenuOpen = false;     // Schüler geladen → Idle-Menü hinfällig (gelöscht
                             //  bereits beim loading, hier nur defensiv)
    setMenuTitle();
    syncQueueView();
    const s = msg.student;
    currentStudent = s;          // Flags für den Freigabe-Dialog (s. unten)
    lendingApproved = false;     // neuer Schüler → Freigabe zurücksetzen
    heldScanValue = null;
    closeLendModal();
    sNameEl.textContent = `${s.lastname}, ${s.firstname}`;
    sFormEl.textContent = (s.form || '').replace(/^Klasse\s+/i, '');
    // Bezahlt-/Offen-Status, ergänzt um „Nachweis fehlt"-Hinweise (Ermäßigung
    // bzw. Befreiung): Antrag gestellt, aber noch unentschieden — gleiche Farbe
    // wie „Offen". Reihenfolge: erst die Nachweise, dann der Offene Betrag.
    const payParts = [];
    if (!s.enrolled) {
      payParts.push('<span class="wait">Nicht angemeldet</span>');
    } else {
      const nachweis = s.remission_pending || s.exemption_pending;
      if (s.remission_pending)  payParts.push('<span class="unpaid">Ermäßigungsnachweis fehlt</span>');
      if (s.exemption_pending) payParts.push('<span class="unpaid">Befreiungsnachweis fehlt</span>');
      // „Bezahlt" entfallen, wenn ein Nachweis fehlt — der Hinweis geht vor.
      if (s.paid && !nachweis) {
        payParts.push('<span class="paid">Bezahlt</span>');
      } else if (!s.paid) {
        payParts.push(`<span class="unpaid">Offen: ${escapeHtml(s.amount_open)} €</span>`);
      }
    }
    sPayEl.innerHTML = payParts.join(' · ');
    if (Array.isArray(s.book_order)) bookOrder = s.book_order;
    currentBooks = s.books || [];
    resetScannedState();
    renderBooks(currentBooks);
    closeBookAlertModal();
    // Bücher sofort sichtbar; Scans+„Scanner bereit"-Status aber erst, sobald
    // der Worker bereit ist (`worker_ready`). Bis dahin „Warten…" + Scans ignor.
    workerPending = true;
    setReadyStatus();
  } else if (msg.type === 'worker_ready') {
    workerPending = false;
    setReadyStatus();
  } else if (msg.type === 'loading') {
    // Server beginnt, einen neuen Schüler für diesen Helfer zu laden („Weiter"/
    // „Nächster"/„Aufrufen"). Queue verbergen, „wird geladen …" zeigen — NICHT
    // die Warteschlange aufblitzen lassen, selbst wenn kurz vorher ein
    // Idle-`waiting` stand (Host-„Nächster" ohne vorherigen Schüler).
    studentActive = false;
    workerPending = true;
    loadingStudent = true;
    // Ein Schüler wird aufgerufen (Aufrufen/Weiter/Nächster/Lupen-Suche) →
    // ein offenes Menü (Peek MIT Hintergrund-Schüler wie Idle OHNE) schließt
    // dabei immer, damit sofort scannbar ist, statt dass der Helfer manuell
    // zurückwechseln muss.
    const menuWasOpen = peeking || idleMenuOpen || searchOpen;
    peeking = false;          // Schülerwechsel beendet den Peek
    idleMenuOpen = false;     // ... und ein ggf. offenes Idle-Menü
    resetSearchPanel();       // ... und ein ggf. offenes Such-Panel (ohne Animation)
    setMenuTitle();
    syncQueueView();
    sNameEl.textContent = '';
    sFormEl.textContent = '';
    sPayEl.innerHTML = '';
    currentBooks = [];
    resetScannedState();
    bookRowsEl.innerHTML = '<div class="book-empty">Schüler wird geladen …</div>';
    closeBookAlertModal();
    currentStudent = null;
    lendingApproved = false;
    heldScanValue = null;
    closeLendModal();
    setStatusText('Warten…');
    if (menuWasOpen) animateMenu(false, true);
  } else if (msg.type === 'scan_result') {
    if (pendingScans > 0) pendingScans--;
    // Erfolgreicher Scan → Buch in der Liste als „erledigt" markieren:
    // 'booked' = tatsächlich gebucht (ALLOW_BOOKING an), 'staged' = nur ins
    // Feld gefüllt (Gate aus / read-only Betrieb).
    if ((msg.status === 'staged' || msg.status === 'booked') && msg.isbn) {
      scannedIsbns.add(msg.isbn);
      scanOrder.set(msg.isbn, ++scanSeq);   // zuletzt gescanntes zuoberst in „erledigt"
      renderBooks(currentBooks, true);     // FLIP: Zeilen an neue Position fahren
    }
    drainScanWaiters();
    // Jeder nicht-verbuchbare Scan (alles außer staged/booked) → Statuszeile
    // deutlich + Hinweis-Modal am Gerät. Der Helfer schließt es selbst
    // (Button/Klick-außerhalb/Escape/nächster Scan). Bei ausgemustert /
    // verliehen-an-andere räumt dismissBookAlert zusätzlich die Host-Meldung
    // auf (server: clear_book_alert); bei den reinen Hinweisen (nicht
    // bestellt, unbekannt, noch nicht geladen, Prüf-Fehler, an sich selbst
    // verliehen) war der Host nie informiert → Clear ist dort ein No-op.
    const isAlert = !OK_STATUSES.has(msg.status);
    setStatusText(`${msg.barcode} — ${msg.msg || msg.status}`, isAlert);
    if (isAlert) showBookAlertModal(msg);
  } else if (msg.type === 'settings') {
    slipSecondPageDefault = !!msg.slip_second_page;
    if (Array.isArray(msg.book_order)) {
      bookOrder = msg.book_order;
      if (studentActive) renderBooks(currentBooks);  // aktuelle Liste live umsortieren
    }
  } else if (msg.type === 'print_result') {
    printBtn.disabled = false;
    const detail = msg.ok
      ? `Leihschein: ${msg.detail || 'gedruckt'}`
      : `Druck fehlgeschlagen: ${msg.msg || ''}`;
    setStatusText(detail);
    // „Drucken & nächster Schüler": nur bei erfolgreichem Druck weiterschalten.
    if (printThenNext) {
      printThenNext = false;
      if (msg.ok) advanceToNext();
    }
  } else if (msg.type === 'waiting') {
    studentActive = false;
    workerPending = false;
    loadingStudent = false;  // kein Schüler (mehr) geladen — Queue anzeigen
    peeking = false;          // kein Schüler → Peek hinfällig
    setMenuTitle();
    syncQueueView();
    sNameEl.textContent = '';
    sFormEl.textContent = '';
    sPayEl.innerHTML = '';
    bookRowsEl.innerHTML = '';
    currentBooks = [];
    resetScannedState();
    pendingScans = 0;
    drainScanWaiters();
    closeBookAlertModal();
    currentStudent = null;
    lendingApproved = false;
    heldScanValue = null;
    closeLendModal();
    if (typeof msg.queue_size === 'number') queueSize = msg.queue_size;
    if (Array.isArray(msg.queue)) queueList = msg.queue;
    if (Array.isArray(msg.queue_all)) queueListAll = msg.queue_all;
    if (msg.msg) waitingMsg = msg.msg;
    renderWaitingStatus();
    renderQueueTabs();
    renderQueue();
  } else if (msg.type === 'queue_update') {
    if (typeof msg.queue_size === 'number') queueSize = msg.queue_size;
    if (Array.isArray(msg.queue)) queueList = msg.queue;
    if (Array.isArray(msg.queue_all)) queueListAll = msg.queue_all;
    // Peek (Menü): Queue anzeigen, obwohl ein Schüler zugewiesen ist — dieser
    // bleibt im Hintergrund verbunden. Sonst nur anzeigen, wenn weder ein
    // Schüler geladen ist noch gerade einer geladen wird (next/call gesendet,
    // student_info steht aus). Die Klassen-Reiter selbst kommen via
    // `contexts_update`; hier nur die (Fallback-)Queue und der Count.
    if (peeking) { renderPeekStatus(); if (!contextsData.length) renderQueue(); }
    else if (!studentActive && !loadingStudent) { renderWaitingStatus(); if (!contextsData.length) renderQueue(); }
  } else if (msg.type === 'contexts_update') {
    // Alle im Host offenen Klassen + je ihre wartenden Schüler. Quelle für die
    // Klassen-Reiter im Helfer-Menü. own_context_id = Klasse, an die dieser
    // Helfer gebunden (Vorauswahl beim Öffnen). Live auf allen Zustandsänderungen
    // (open/close-class, Aufrufe, Abschlüsse) via broadcast_queue_size.
    contextsData = Array.isArray(msg.contexts) ? msg.contexts : [];
    ownContextId = msg.own_context_id || null;
    ensureSelectedCtx();
    renderQueueTabs();
    if (peeking) { renderPeekStatus(); renderQueue(); }
    else if (!studentActive && !loadingStudent) { renderWaitingStatus(); renderQueue(); }
  } else if (msg.type === 'search_classes') {
    // Lupen-Suche: alle Klassen des Schuljahrs (IServ). Session-Cache füllen
    // und Dropdown aufbauen — letzte Klasse vorwählen (localStorage), sonst
    // erste. Sofort Schüler der gewählten Klasse nachladen.
    searchClassCache = Array.isArray(msg.classes) ? msg.classes : [];
    renderSearchClasses();
  } else if (msg.type === 'search_students') {
    // Lupen-Suche: alle Schüler einer Klasse (IServ). Session-Cache füllen
    // und Dropdown aufbauen, falls die Klasse aktuell gewählt ist.
    const list = Array.isArray(msg.students) ? msg.students : [];
    searchStudentsCache.set(msg.form, list);
    if (searchOpen && searchClassSel.value === msg.form) renderSearchStudents(msg.form, list);
  } else if (msg.type === 'error') {
    loadingStudent = false;  // Laden gescheitert → Queue wieder freigeben
    // Lupe-Suche gescheitert (z. B. ungültige ID/IServ-Fehler): kein `loading`
    // gekommen → Menü/Panel offen lassen zum erneuten Versuch, Flag aber
    // zurücksetzen, damit ein späteres fremdes `loading` sie nicht doch schließt.
    searchSubmitted = false;
    // Lupe-Suche auf einen Schüler, der gerade auf einem ANDEREN Client aktiv
    // ist (Queue-`call` oder ebenfalls Lupe): server sendet `busy` statt eines
    // echten Fehlers — Statuszeile ohne „Fehler:"-Prefix, Panel bleibt offen
    // (s. u.) zum erneuten Versuch, sobald der Schüler frei ist.
    setStatusText(msg.busy ? (msg.msg || '') : 'Fehler: ' + (msg.msg || ''));
    dotEl.className = 'dot err';
    heldScanValue = null;
    closeLendModal();
    // Peek geöffnet (z. B. Aufruf aus der Queue gescheitert) → zurück in die
    // Peek-Ansicht; sonst nur bei freiem Helfer die Queue zeigen.
    if (peeking) renderQueue();
    else if (!studentActive) renderQueue();
  }
}

function connect() {
  if (!token) {
    setStatusText('Kein Token in der URL — vom Host QR-Code scannen');
    dotEl.className = 'dot err';
    return;
  }
  connectWebSocket(() => `wss://${location.host}/ws/scanner/${token}`, {
    onSocket: (s) => { ws = s; },
    onOpen: () => { dotEl.className = 'dot ok'; setReadyStatus(); },
    onClose: (e, reconnect) => {
      dotEl.className = 'dot err'; setStatusText('Getrennt — neu verbinden…');
      reconnect();
    },
    onError: () => { dotEl.className = 'dot err'; setStatusText('Verbindungsfehler'); },
    onMessage: e => { try { handleServerMessage(JSON.parse(e.data)); } catch (_) {} },
  });
}
connect();

// Zur Introspektion/Debugging zusätzlich auf window.__scan verfügbar
// machen (rein additiv — der Code oben referenziert weiterhin die
// bare Bezeichner aus der gemeinsamen Skript-Scope, keine funktionale
// Abhängigkeit von window.__scan).
window.__scan.handleServerMessage = handleServerMessage;
window.__scan.connect = connect;
