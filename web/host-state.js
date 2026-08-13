// web/host-state.js — Modul-State + reine Helfer/Konstanten
// Teil des host.html-Frontends (siehe host-state.js/host-ws.js/host-render.js,
// in dieser Reihenfolge nach common.js eingebunden). Kein Build-Step: alle drei
// Dateien teilen sich eine gemeinsame Top-Level-Scope (klassische <script>-Tags),
// zusätzlich exponiert auf window.__host für Debug-/Introspektionszwecke.

window.__host = window.__host || {};

  let ws = null;
  let state = { active_context_id: null, contexts: {}, queue: [], active_form: null, helpers: {}, modus_b: { open: false, paused: false, pending: [], pending_count: 0, displays: [], join_url: null }, selected_schoolyear: null };
  let mbQrDataUrl = null;
  // QR-Modal-Beobachter: schließt das Popup automatisch, sobald der gezeigte QR gescannt wurde.
  // { kind: 'student'|'display', baseline: <Zählerstand beim Öffnen> }
  let qrWatch = null;
  // iPad-Registrierungscodes, die dieser Host vorübergehend ausblendet. Das
  // Display bleibt dabei verbunden und kann durch einen Reload der Host-Seite
  // wieder eingeblendet werden; „Ignorieren" ist bewusst keine Trennung.
  let ignoredDisplayIds = new Set();
  let armedStudentId = null;  // Schüler, der per "Pairing"-Button scharfgestellt ist (Code-Klick ordnet zu)
  let studentAlerts = {};  // student_id -> {text} — ausgemustert/verliehen-Meldung fürs Now-Serving-Kästchen
  // student_id -> mountPrinterPicker()-Instanz des "Druckauftrag aktualisieren"-
  // Menüs im gelben Scan-Station-Gate-Hinweis (s. renderCtxNowServing) — wird
  // bei jedem Rendern neu gemountet, da die Kachel per innerHTML neu aufgebaut wird.
  let stationGatePickers = {};
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
  // Aktiver Hauptreiter der „Drucker"-Karte: 'queue' | 'displays' | 'scanner'.
  // Rein pro Browser, nicht persistiert.
  let activePrinterMainTab = 'queue';
  // Aktiver Unter-Reiter im „Displays"-Hauptreiter: <display_id> oder null
  // (kein Panel offen). Rein pro Browser (wie activeTab), nicht persistiert.
  // Fällt auf null zurück, wenn das Display verschwindet.
  let activePdTab = null;
  // Drag-Zustand für die Drucker-Boxen eines Display-Reiters (HTML5 DnD),
  // analog blDragIndex für die Bücherlisten. PID des gezogenen Druckers.
  let pdDragPid = null;
  // Aufgeklappter Scan-Stations-Reiter im Live-Ausgabe-Kasten (<station_id>)
  // oder null = kein Panel offen. Anders als bei den Drucker-Displays gibt es
  // hier keinen statischen ersten Reiter — die Reiter sind Umschalter.
  let activeSsTab = null;
  // Aufgeklappter Drucker-Scanner-Reiter im „Scanner"-Hauptreiter der
  // „Drucker"-Karte (<scanner_id>) oder null = kein Panel offen. Umschalter
  // wie bei den Scan-Stationen (activeSsTab).
  let activePscTab = null;
  let classList = [];                 // Klassen-Liste aus /api/classes (für Wähler + Single-Selects)
  let ctxSingleStudents = {};         // context_id -> [students] für den Einzelne-Schüler-Select
  // SVG-Icons für die Queue-Steuer-Buttons (pro Klassen-Tab neu gerendert).
  const ICO_RESET = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>';
  const ICO_CLEAR = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  const ICO_DISC  = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/></svg>';
  const ICO_HELPER = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
  // Host-Symbol (Laptop) — dasselbe SVG wie im Drucker-Display (dort
  // `ICO_LAPTOP` in drucker-display.js) für den Auftraggeber „Host". Hier für
  // die Scan-Station-Kennzeichnung wiederverwendet (s. Anforderung: „wo ein
  // Symbol benötigt wird, das des Hostes im Druckerdisplay").
  const ICO_HOST = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="11" rx="1"/><path d="M2 20h20"/></svg>';
  const ICO_PAUSE = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>';
  const ICO_PLAY = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m8 5 11 7-11 7V5Z"/></svg>';

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

  const AUTO_DONE_KEYS = ['not_enrolled', 'unpaid', 'remission_pending', 'exemption_pending', 'all_lent'];
  const AUTO_DONE_STORAGE_KEY = 'autoDoneFilters';

  // Druck-Allowlist für neu zu öffnende Klassen (panel-new): Menge der
  // angehakten Drucker, die mit `/api/open-class` als `printers` (IDs)
  // geschickt wird. `null` (nichts gespeichert) = alle angehakt (Default).
  // Eine explizit leere Auswahl = `[]` = bewusst kein Drucker für die Klasse
  // (keine Vorauswahl; Druck bleibt per manueller Auswahl im Druckdialog
  // möglich). Wird für das nächste Öffnen gemerkt.
  //
  // Persistiert wird NICHT die `id` (die ist laut `printer_store.py` bewusst
  // nur zur Laufzeit stabil und wird bei jedem Server-Neustart neu vergeben —
  // gespeicherte IDs wären nach einem Neustart verwaist, nichts würde mehr
  // matchen und keine Checkbox bliebe angehakt), sondern der stabile
  // `name` (technische Identität, `null` bleibt `null` = Standarddrucker).
  const CLASS_PRINTERS_STORAGE_KEY = 'classPrinters';

  // `p.name` selbst ist der stabile Schlüssel; für den Standarddrucker
  // (`name === null`) wird `''` verwendet — dieselbe Normalisierung, die ein
  // `data-pname=""`-Attribut beim Auslesen über `el.dataset.pname` ohnehin
  // liefert (kein `null` im DOM darstellbar), damit Speichern/Lesen konsistent
  // denselben Wert vergleichen.
  function printerStableKey(p) {
    return p.name || '';
  }

  // Angehakte Drucker-IDs aus dem panel-new (`#new-class-printers`) lesen —
  // für den `/api/open-class`-Request (der Server erwartet IDs des aktuellen
  // Pools).
  function getSelectedClassPrinters() {
    const out = [];
    document.querySelectorAll('#new-class-printers input[data-pid]').forEach(el => {
      if (el.checked) out.push(el.dataset.pid);
    });
    return out;
  }

  // Angehakte Drucker als stabile Namen (für die localStorage-Persistenz,
  // s. `printerStableKey`).
  function getSelectedClassPrinterNames() {
    const out = [];
    document.querySelectorAll('#new-class-printers input[data-pid]').forEach(el => {
      if (el.checked) out.push(el.dataset.pname);
    });
    return out;
  }

  // Gespeicherte Auswahl laden (Set stabiler Namen; null = „alle", Default
  // bei nichts Gespeichertem). Eine leere Menge = bewusst kein Drucker. Beim
  // Render werden nur Pool-Drucker gecheckt, deren `printerStableKey` im
  // gespeicherten Set steht (oder alle, wenn gespeichert null ist).
  function loadClassPrintersSelection() {
    try {
      const raw = JSON.parse(localStorage.getItem(CLASS_PRINTERS_STORAGE_KEY) || 'null');
      if (raw === null) return null;
      return new Set(Array.isArray(raw) ? raw : []);
    } catch { return null; }
  }

  function saveClassPrintersSelection(ids) {
    localStorage.setItem(CLASS_PRINTERS_STORAGE_KEY, JSON.stringify(ids || []));
  }

  // Live-Ausgabe (Modus B) für neu zu öffnende Klassen (panel-new): Schalter
  // `#new-class-live-ausgabe`, der mit `/api/open-class` als `live_ausgabe`
  // geschickt wird. Default `true` (Modus B sichtbar, kompatibel mit
  // bestehendem Verhalten). Wird für das nächste Öffnen gemerkt.
  const CLASS_LIVE_AUSGABE_STORAGE_KEY = 'classLiveAusgabe';

  function loadClassLiveAusgabe() {
    const raw = localStorage.getItem(CLASS_LIVE_AUSGABE_STORAGE_KEY);
    return raw === null ? true : raw === 'true';
  }

  function saveClassLiveAusgabe(on) {
    localStorage.setItem(CLASS_LIVE_AUSGABE_STORAGE_KEY, on ? 'true' : 'false');
  }

  // Leihschein-Druckmodus (slip_trigger) für neu zu öffnende Klassen (panel-new):
  // Dropdown `#new-class-slip-trigger`, das mit `/api/open-class` als
  // `slip_trigger` geschickt wird. Bestimmt, wer am Schülerclient den Druck
  // auslöst, sobald alle vorgemerkten Bücher gescannt sind. Default "auto".
  // Wird für das nächste Öffnen gemerkt. S. ClassContext.slip_trigger.
  const CLASS_SLIP_TRIGGER_STORAGE_KEY = 'classSlipTrigger';
  const SLIP_TRIGGER_VALUES = ['auto', 'student', 'helper', 'barcode'];

  function loadClassSlipTrigger() {
    const raw = localStorage.getItem(CLASS_SLIP_TRIGGER_STORAGE_KEY);
    return SLIP_TRIGGER_VALUES.includes(raw) ? raw : 'auto';
  }

  function saveClassSlipTrigger(value) {
    if (SLIP_TRIGGER_VALUES.includes(value)) {
      localStorage.setItem(CLASS_SLIP_TRIGGER_STORAGE_KEY, value);
    }
  }

  // „Fertig"-Voraussetzungen (Klasseneinstellungen „Leihschein unterschreiben"/
  // „…wird vom Lehrer eingesammelt") für neu zu öffnende Klassen (panel-new):
  // Checkboxen `#new-class-done-signed`/`#new-class-done-collected`, die mit
  // `/api/open-class` als `done_signed`/`done_collected` geschickt werden.
  // Default `false` (kompatibel mit bestehendem Verhalten). Wird für das
  // nächste Öffnen gemerkt. S. ClassContext.done_signed/done_collected.
  const CLASS_DONE_SIGNED_STORAGE_KEY = 'classDoneSigned';
  const CLASS_DONE_COLLECTED_STORAGE_KEY = 'classDoneCollected';

  function loadClassDoneSigned() {
    return localStorage.getItem(CLASS_DONE_SIGNED_STORAGE_KEY) === 'true';
  }
  function saveClassDoneSigned(on) {
    localStorage.setItem(CLASS_DONE_SIGNED_STORAGE_KEY, on ? 'true' : 'false');
  }
  function loadClassDoneCollected() {
    return localStorage.getItem(CLASS_DONE_COLLECTED_STORAGE_KEY) === 'true';
  }
  function saveClassDoneCollected(on) {
    localStorage.setItem(CLASS_DONE_COLLECTED_STORAGE_KEY, on ? 'true' : 'false');
  }
  // ---- Bücherlisten ordnen (Einstellungen-Dialog, Reiter je Jahrgang) ----
  // Analog zur Klassen-Bücher-Reihenfolge, aber jahrgangsweit und vorab: pro
  // Booklist ein Reiter, Katalog wird beim Anklicken lazy geladen. Änderungen
  // leben lokal bis „Speichern" (dann POST je geänderten Jahrgang).
  let blData = {};        // grade -> { catalog:{isbn:{title,subject}}, order:[isbn], saved:[isbn], loaded:bool }
  let blActiveGrade = null;
  let blDragIndex = null, blDropIndex = null, blDropPos = null;

// Zur Introspektion/Debugging zusätzlich auf window.__host verfügbar
// machen (rein additiv — der Code oben referenziert weiterhin die
// bare Bezeichner aus der gemeinsamen Skript-Scope, keine funktionale
// Abhängigkeit von window.__host).
window.__host.findStudentInState = findStudentInState;
window.__host.findCtxOfStudent = findCtxOfStudent;
