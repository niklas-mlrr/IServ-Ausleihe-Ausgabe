// web/host-state.js — Modul-State + reine Helfer/Konstanten
// Teil des host.html-Frontends (siehe host-state.js/host-ws.js/host-render.js,
// in dieser Reihenfolge nach common.js eingebunden). Kein Build-Step: alle drei
// Dateien teilen sich eine gemeinsame Top-Level-Scope (klassische <script>-Tags),
// zusätzlich exponiert auf window.__host für Debug-/Introspektionszwecke.

window.__host = window.__host || {};

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

  const AUTO_DONE_KEYS = ['not_enrolled', 'unpaid', 'remission_pending', 'exemption_pending'];
  const AUTO_DONE_STORAGE_KEY = 'autoDoneFilters';
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
