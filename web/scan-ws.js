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
    spectating = !!msg.spectator;  // Zuschauer: Status „Warten bis Schüler frei…"
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
    scanInFlight = false;
    printRequestId = null;
    printThenNext = false;
    printBtn.disabled = false;
    if (currentPrintJob) {
      currentPrintJob = null;
      if (printerChangeModal.classList.contains('show')) closePrinterChangeModal();
    }
    renderBooks(currentBooks);
    closeBookAlertModal();
    // Bücher sofort sichtbar; Scans+„Scanner bereit"-Status aber erst, sobald
    // der Worker bereit ist (`worker_ready`). Bis dahin „Warten…" + Scans ignor.
    workerPending = true;
    // Zuschauer (spectating=true via spectate_student, server-seitig): Schüler
    // ist bei einem ANDEREN Helfer aktiv, dieser Client bekommt nie ein
    // `worker_ready` — `workerPending` bleibt dauerhaft true (sperrt Scans
    // clientseitig bereits über den bestehenden Gate in onScanSuccess). Der
    // Statushinweis („Warten bis Schüler frei…") kommt über setReadyStatus,
    // das spectating auswertet — auch beim Schließen eines Peeks bleibt er so
    // erhalten, statt mit „Warten…" überschrieben zu werden.
    setReadyStatus();
  } else if (msg.type === 'worker_ready') {
    workerPending = false;
    spectating = false;       // eigener Schüler übernommen (Beförderung/Laden)
    setReadyStatus();
  } else if (msg.type === 'loading') {
    // Server beginnt, einen neuen Schüler für diesen Helfer zu laden („Weiter"/
    // „Nächster"/„Aufrufen"). Queue verbergen, „wird geladen …" zeigen — NICHT
    // die Warteschlange aufblitzen lassen, selbst wenn kurz vorher ein
    // Idle-`waiting` stand (Host-„Nächster" ohne vorherigen Schüler).
    studentActive = false;
    workerPending = true;
    loadingStudent = true;
    spectating = false;        // neuer Schüler für diesen Helfer — nicht mehr Zuschauer
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
    scanInFlight = false;
    bookRowsEl.innerHTML = '<div class="book-empty">Schüler wird geladen …</div>';
    closeBookAlertModal();
    currentStudent = null;
    lendingApproved = false;
    heldScanValue = null;
    closeLendModal();
    setStatusText('Warten…', null, 'wait');
    clearStatus('trans');
    clearStatus('print');
    if (menuWasOpen) animateMenu(false, true);
  } else if (msg.type === 'scan_result') {
    if (msg.spectator) {
      // Gespiegelter Scan des aktiven Helfers (s. spectate_student/Fan-out in
      // ws.py) — nur die Bücherliste mitziehen, kein eigener Scan-Vorgang
      // (pendingScans/drainScanWaiters) und keine Statuszeile/Alert-Modal:
      // die bleiben „Warten bis Schüler frei…".
      if ((msg.status === 'staged' || msg.status === 'booked') && msg.isbn) {
        scannedIsbns.add(msg.isbn);
        scanOrder.set(msg.isbn, ++scanSeq);
        renderBooks(currentBooks, true);
      }
      return;
    }
    if (pendingScans > 0) pendingScans--;
    // Jeder normale Status beendet die Bearbeitung dieses Scans. Bei einem
    // blockierenden Hinweis muss der Helfer zuerst bewusst schließen, damit
    // auch die Host-Meldung aufgeräumt wird (s. dismissBookAlert()).
    if (!BLOCKING_STATUSES.has(msg.status)) scanInFlight = false;
    // Erfolgreicher Scan → Buch in der Liste als „erledigt" markieren:
    // 'booked' = tatsächlich gebucht (ALLOW_BOOKING an), 'staged' = nur ins
    // Feld gefüllt (Gate aus / read-only Betrieb).
    if ((msg.status === 'staged' || msg.status === 'booked') && msg.isbn) {
      scannedIsbns.add(msg.isbn);
      scanOrder.set(msg.isbn, ++scanSeq);   // zuletzt gescanntes zuoberst in „erledigt"
      renderBooks(currentBooks, true);     // FLIP: Zeilen an neue Position fahren
      // Ein neuer Scan macht einen bereits gestarteten Leihschein fachlich
      // veraltet. Der alte Druck darf physisch fertig werden, aber die
      // Anzeige kehrt sofort zum Buchfortschritt zurück.
      clearStatus('print');
      // „Bestand leer" erneut gescannt: Buchung ist bereits abgeschlossen
      // (oben) — die Rückfrage ist rein informativ und blockiert nichts.
      if (msg.was_empty_stock) showEmptyStockPopup(msg.isbn);
    }
    drainScanWaiters();
    // Jeder nicht-verbuchbare Scan (alles außer staged/booked) → Statuszeile
    // deutlich + Hinweis-Modal am Gerät. Der Helfer schließt es selbst;
    // selbst schließbare Hinweise auch beim nächsten Scan. Bei ausgemustert /
    // verliehen-an-andere räumt dismissBookAlert zusätzlich die Host-Meldung
    // auf (server: clear_book_alert); bei den reinen Hinweisen (nicht
    // bestellt, unbekannt, noch nicht geladen, Prüf-Fehler, an sich selbst
    // verliehen) war der Host nie informiert → Clear ist dort ein No-op.
    const isAlert = !OK_STATUSES.has(msg.status);
    // Helfer scannt für den zugewiesenen Schüler — "den Schüler" statt "dich"
    // (Default in scanResultStatusText() ist "dich", für den Schüler-Client).
    setStatusText(scanResultStatusText(msg, currentBooks, 'den Schüler'), statusAlertClass(msg.status));
    if (isAlert) showBookAlertModal(msg);
  } else if (msg.type === 'settings') {
    slipSecondPageDefault = !!msg.slip_second_page;
    if (Array.isArray(msg.book_order)) {
      bookOrder = msg.book_order;
      if (studentActive) renderBooks(currentBooks);  // aktuelle Liste live umsortieren
    }
    // Drucker-Pool + Vorauswahl für den Druck-Dialog. Pooländerungen auch am
    // geöffneten Dialog nachführen (setPool kürzt die Auswahl auf existierende
    // Drucker); die Vorauswahl (print_default_ids) aber nur übernehmen, solange
    // der Dialog NICHT offen ist — sonst würde ein fremder settings-Push (z. B.
    // Dev-Toggle am Host) die laufende manuelle Auswahl des Helfers zurücksetzen.
    if (Array.isArray(msg.printers)) {
      printerPool = msg.printers.map(p => ({id: p.id, name: p.name, label: p.label, is_default: p.is_default, faulty: p.faulty}));
      if (printPicker) printPicker.setPool(printerPool);
    }
    if (Array.isArray(msg.print_default_ids)) {
      printDefaultIds = msg.print_default_ids;
      if (printPicker && !printModal.classList.contains('show')) {
        printPicker.setSelectedIds(printDefaultIds);
      }
    }
  } else if (msg.type === 'booklist_update') {
    // Live-Nachzug der Bücherliste nach einer Ausblendungs-/Reihenfolge-Änderung
    // im Einstellungen-Dialog. Ersetzt nur die Liste + Reihenfolge, lässt den
    // Scan-Fortschritt (scannedIsbns/scanOrder) unangetastet — ein ausgeblendetes
    // Buch fällt raus, ein wieder eingeblendetes taucht mit IServ-Status auf.
    if (Array.isArray(msg.book_order)) bookOrder = msg.book_order;
    if (Array.isArray(msg.books)) currentBooks = msg.books;
    if (studentActive) renderBooks(currentBooks);
  } else if (msg.type === 'print_progress') {
    if (msg.request_id && printRequestId && msg.request_id !== printRequestId) return;
    if (msg.stale) return;
    // Live-Status aus der internen Druckerwarteschlange (OS-getrieben):
    //   peer_error                  → „Es dauert ungewöhnlich lange … - <Label>"
    //                                (Auftrag am hängenden Drucker / kein
    //                                Ersatzdrucker — Text vom Server, kein
    //                                clientseitiger Positions-Zusatz)
    //   status printing             → „Leihschein wird von <pname> gedruckt…"
    //                                (OS druckt aktiv — erst jetzt, nicht
    //                                schon bei slot-Pos. 0)
    //   position 0 (spooled)        → „Leihschein wartet an <pname> auf Druck…"
    //   position 1 + pname (Slot)   → „Leihschein an 1. Druckerwarteschlangen-
    //                                position von <pname>"
    //   position 1 ohne pname        → Kurzform (zentraler Wartender, Drucker
    //                                noch nicht zugewiesen)
    //   position ≥ 2                → „Leihschein an X. Druckerwarteschlangen-
    //                                position" (kurz, ohne Drucker)
    // <pname> = Anzeige-Label „Label (Systemname)" (msg.printer_label); erst
    // gesetzt, wenn der Auftrag einem Drucker zugewiesen ist (Slot-Job).
    // Zentrale-Warteschlangen-Jobs ohne zugewiesenen Drucker → kein Zusatz.
    // Für peer_error übernimmt der Server den Text — kein Zusatz.
    if (msg.peer_error) {
      setStatusText(msg.msg || 'Es dauert ungewöhnlich lange, vielleicht liegt ein Fehler vor.', null, 'print');
    } else {
      const text = printProgressStatusText(msg);
      // Drucker-Fortschritt = aktive Meldung: aktualisiert nur die `print`-
      // Zeile. Aktive Meldungen in `trans`/`wait` (z.B. „Warten …") bleiben
      // sichtbar; terminale (z.B. „Scanner bereit") werden als End-Meldungen
      // von dieser neuen Meldung verdrängt.
      setStatusText(text, null, 'print');
    }
    // Live-Status des eigenen Auftrags für die Druckenbutton-Umschaltung +
    // das Drucker-Nachfrage-Fenster (s. scan-render.js). Solange der Job in
    // der zentralen Warteschlange steht, bleibt `status` 'queued'.
    currentPrintJob = {
      job_id: msg.job_id, status: msg.status,
      allowed: (msg.allowed_printers === undefined) ? null : msg.allowed_printers,
      peer_error: !!msg.peer_error,
    };
    const stuck = msg.status === 'queued' && msg.peer_error;
    if (stuck && !printerChangeModal.classList.contains('show')) {
      openPrinterChangeModal();
    } else if (msg.status !== 'queued' && printerChangeModal.classList.contains('show')) {
      // Auftrag wurde regulär einem Drucker zugewiesen, während das Fenster
      // offen war — die Warteschlange lief unverändert weiter (kein Pause-
      // Mechanismus ans offene Fenster geknüpft), das Fenster folgt nur nach.
      closePrinterChangeModal();
    }
  } else if (msg.type === 'print_printers_update_result') {
    if (msg.ok === false && printerChangeModal.classList.contains('show')) {
      printerChangeErrEl.textContent = msg.msg || 'Aktualisieren fehlgeschlagen';
      printerChangeErrEl.style.display = '';
    }
  } else if (msg.type === 'print_result') {
    if (msg.request_id && printRequestId && msg.request_id !== printRequestId) return;
    printBtn.disabled = false;
    const wasPrintThenNext = printThenNext;
    printThenNext = false;
    printRequestId = null;
    // Auftrag ist finalisiert (gedruckt/fehlgeschlagen) — Nachfrage-Fenster
    // (falls offen) gehört zu keinem lebenden Job mehr.
    if (currentPrintJob && currentPrintJob.job_id === msg.job_id) {
      currentPrintJob = null;
      if (printerChangeModal.classList.contains('show')) closePrinterChangeModal();
    }
    if (msg.stale) {
      // Ergebnis des alten, durch einen Scan entwerteten Jobs: keine
      // Drucker-/Fehlermeldung zurück in die Statuszeile schreiben.
      clearStatus('print');
      return;
    }
    if (msg.stalled) {
      // Noch andauerndes Problem (Drucker hängt) → aktiv, persistiert.
      setStatusText(msg.msg || 'Druck dauert ungewöhnlich lange', null, 'print');
    } else if (msg.peer_error) {
      setStatusText(msg.msg || 'Es dauert ungewöhnlich lange, vielleicht liegt ein Fehler vor.', null, 'print');
    } else if (msg.ok) {
      const base = printResultStatusText(msg);
      // „gedruckt." = terminal: überschreibt die vorige Druckmeldung im selben
      // Slot, ist aber danach von jeder neuen Meldung überschreibbar. Aktive
      // Meldungen anderer Slots (z.B. „Warten …") bleiben sichtbar; terminale
      // (z.B. „Scanner bereit") werden verdrängt.
      setStatusText(base, null, 'print', true);
      // „Drucken & nächster Schüler": bei erfolgreichem Druck nicht sofort
      // weiterschalten, sondern per Countdown (4 s, entspricht der Host-
      // Toast-Dauer). stalled/peer_error/Fehler → ok=false → kein Auto-
      // Advance (Schüler bleibt stehen, s. Plan Fehler-Verhalten).
      if (wasPrintThenNext) startNextCountdown(base);
    } else {
      // Druck endgültig gescheitert → terminal (End-Meldung).
      setStatusText(printResultStatusText(msg), null, 'print', true);
    }
  } else if (msg.type === 'print_for_student_result') {
    // Betreuerauslöser-Druck für einen FREMDEN Schüler aus der Klassenliste
    // (s. scan-render.js openPrintDialog/sendPrint mit printTargetStudentId).
    // Anders als 'print_result' NICHT die eigene `print`-Statuszeile
    // überschreiben — das wäre der Druckstatus eines anderen Schülers, nicht
    // des eigenen zugewiesenen. Ein Erfolg braucht keine eigene Meldung: der
    // Druckbutton verschwindet ohnehin fleet-weit, sobald der nächste
    // contexts_update-Broadcast (PrintQueue._notify_all) eintrifft. Nur ein
    // Fehlschlag (z. B. Wettlauf zweier Helfer) bekommt einen kurzen,
    // transienten Hinweis in der neutralen 'trans'-Zeile.
    if (!msg.ok) setStatusText(msg.msg || 'Leihschein konnte nicht gesendet werden', null, 'trans', true);
  } else if (msg.type === 'finish_signed_result') {
    // „Leihschein unterschreiben"-Button (s. scan-render.js): Erfolg braucht
    // keine eigene Meldung — der Schüler wandert fleet-weit in die
    // Fertig-Gruppe, sobald der nächste contexts_update-Broadcast eintrifft.
    // Nur ein Fehlschlag (z. B. Doppelklick zweier Helfer) bekommt einen
    // kurzen, transienten Hinweis.
    if (!msg.ok) setStatusText(msg.msg || 'Konnte nicht abgeschlossen werden', null, 'trans', true);
  } else if (msg.type === 'waiting') {
    studentActive = false;
    workerPending = false;
    loadingStudent = false;  // kein Schüler (mehr) geladen — Queue anzeigen
    spectating = false;       // Helfer frei — Zuschauer-Status hinfällig
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
    scanInFlight = false;
    printRequestId = null;
    printThenNext = false;
    printBtn.disabled = false;
    if (currentPrintJob) {
      currentPrintJob = null;
      if (printerChangeModal.classList.contains('show')) closePrinterChangeModal();
    }
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
    // und Dropdown aufbauen — letzte Klasse vorwählen (sessionStorage), sonst
    // Platzhalter „Klasse wählen". Sofort Schüler der gewählten Klasse nachladen.
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
    setStatusText('Fehler: ' + (msg.msg || ''));
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
