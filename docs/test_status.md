# Test- & Verifizierungsstand

> **Lebendes Dokument.** Hält fest, was bereits getestet/verifiziert ist und was
> noch zu testen bleibt. **Konvention:** Jede neue Funktion bzw. jedes neue
> Risiko hier unter „Offen / zu testen" eintragen; nach erfolgreichem Test in
> „Verifiziert" verschieben (mit Datum + Skript/Befund). Bezug: `docs/PLAN.md`.
>
> Stand: 2026-07-05 (Unit-Test-Zahlen aktualisiert nach Review Tier 1–3).
> Alle bisherigen Tests sind **read-only** gegen IServ
> (kein Submit, keine Buchung — PLAN §6).

## Verifiziert (grün)

| # | Was | Wie / Skript | Datum | Befund |
|---|-----|--------------|-------|--------|
| V1 | Spike A — Counter-Seite headless bedienbar (Login, Schülersuche, Kartei) | `automation/spike_a_counter.py --explore` | 2026-06-12 | Selektoren stabil; Login ohne 2FA/Captcha; `docs/spikes/spike_a_protokoll.md` |
| V2 | Spike B — parallele Sessions desselben Accounts (O2) | `automation/spike_b_parallel.py` | 2026-06-12 | 3/3 Logins + 3/3 Cookie-Sharing, keine Invalidierung |
| V3 | Phase-2 E2E-Smoke Modus A (Host→Scanner→Worker→Kartei→staged) | `automation/e2e_smoke.py` | 2026-06-15 | bestanden; Bug in `scan.html` (Panel-Display) gefixt; `docs/phase2_e2e_2026-06-15.md` §1 |
| V4 | Worker-Recovery (Re-Login bei Session-Ablauf) | `automation/recovery_test.py` | 2026-06-15 | deterministisch via `clear_cookies()`, bestanden; `docs/phase2_e2e_2026-06-15.md` §2 |
| V5 | 2-Helfer-Paralleltest (zwei Schüler gleichzeitig, getrennte Karteien) | `automation/e2e_parallel.py` | 2026-06-15 | bestanden, keine Vermischung; `docs/phase2_e2e_2026-06-15.md` §3 |
| V6 | Pool-Härtung (fehlgeschlagene Logins werden nachgezogen, kein Context-Leak) | `WorkerPool.start()` | 2026-06-15 | im Paralleltest mitverifiziert; `docs/phase2_e2e_2026-06-15.md` §3 |
| V7 | Phase-4 E2E Modus B (Pairing-Flow + harte Token-Invalidierung) | `automation/e2e_modus_b.py` | 2026-06-15 | bestanden inkl. Reconnect mit totem Token (Close 4006); `docs/phase4_modus_b_2026-06-15.md` §5 |
| V8 | Druck-Backend-Logik `file`/`auto`-Resolution (ohne Drucker) | `server/printing.py` Smoke (py) | 2026-06-15 | auto→file auf Linux, PDF wird geschrieben (reiner Logik-Check, **kein** echter Druck) |
| V9 | Rate-Limit-Logik (sliding window 5/10 s, pro-IP) | `server/ratelimit.py` Smoke | 2026-06-15 | erste 5 erlaubt, 6. gedrosselt; andere IP unbetroffen |
| V10 | **Buchungs-Gate** — bei `ALLOW_BOOKING=false` wird der Worker (Enter) nie berührt | `handle_commit` Smoke (uv) | 2026-06-15 | Default→`blocked` ohne Worker-Zugriff; echter Config-Default `False`; Snapshot `False`. Beweist: kein Enter gegen Produktion |
| V11 | Härtung: `WorkerPool.stats()`, `worker_pool` im Snapshot, Limiter-`sweep()` | uv-Smoke | 2026-06-15 | stats total/available/in_use korrekt; sweep leert alte/leere Buckets; Snapshot enthält `worker_pool` |
| V12 | **Spike C / O4 — Silent-Print Windows** (echter Druck am Zielgerät) | `automation/test_printer.py "HP LaserJet Professional P1102"` | 2026-06-22 | rc=0, Seite ausgedruckt; SumatraPDF via winget nach `%LOCALAPPDATA%\SumatraPDF\`; `PRINTER_NAME=HP LaserJet Professional P1102` in `.env` setzen |

## Offen / zu testen

### Neu 2026-07-09 (Scanner: Reconnect stellt auch Lupe-Schüler wieder her + schneller Worker-Reload)

Wird die Helferclient-Seite neu geladen, stellt der Reconnect-Pfad
(`server/routes/ws.py` `ws_scanner`) den aktuell geladenen Schüler wieder her
und lädt die Kartei im Worker neu (`StudentSession.reload()` → `worker_ready`).
Zwei Lücken/Verbesserungen:

- **Lupe-Schüler (`search_call`)** ging bisher beim Reload verloren: er ist
  bewusst **nicht** in einer Queue eingetragen, also lief `state.find_student`
  None → der Reconnect sendete `waiting`, der Schüler war weg, der Worker
  wurde **nicht** neu geladen. Fix: `HelperSession.student_form` speichert die
  Klasse beim Zuweisen (`assign_student_to_helper`); der Reconnect nimmt die
  Form daraus, falls `find_student` None liefert, und durchläuft dann auch für
  den Lupe-Schüler den Wiederherstellungs-+Worker-Reload-Pfad. `end_student`
  räumt `student_form` in beiden Zweigen mit auf. (Hintergrund/Peek ist nur
  eine Ansicht — beim Reconnect kommt der Schüler ohnehin als aktiv zurück,
  `helper.peeking` wird auf False gesetzt.)
- **`StudentSession.reload()` beschleunigt**: Angular steht auf der bereits
  geöffneten Page → kein App-Root-Load (~4 s) mehr. Stattdessen Hop auf
  `#/counter` (erzwingt echten Re-Render — gleicher Hash allein wäre ein
  Angular-No-Op ohne frische Buchdaten) und zurück auf `#/counter/student/<id>`,
  beides In-App-Hashrouten via `_goto_authed` (inkl. Re-Login-Recovery). Sicherer
  Fallback auf vollständiges `load_card()` (Root + Schüler-Route), falls das
  Barcode-Feld nicht erscheint. `load_card` (frisches `open_student`) bleibt
  unverändert — dort muss Angular von der Root initialisiert werden (Spike B).
  Nur GET-Routen, kein `page.reload()` (kein Post-Re-Post-Risiko).

- [x] **Unit-Suite**: `uv run pytest` **149 grün**. `tests/test_scanner_reconnect.py`
      reload-Tests an neue Goto-Sequenz (`#/counter` → Schüler-Route, Fallback
      `load_card`) angepasst (Re-Login/Timeout/fehlendes-Re-Login/Schüler-Route-
      Redirect). `tests/test_queue_flow.py` +1 (`assign_student_to_helper` setzt
      `student_form` für Queue- wie Lupe-Schüler; Advance wechselt die Form
      mit) sowie `student_form`-Clear-Assertionen im transienten `end_student`-
      und `assign`-Test.
- [ ] **Am Gerät** (manuell, read-only, erst nach Freigabe — PLAN §6):
      (1) Helfer lädt Schüler (call/next) → Seite neu laden → Schüler sofort
      wieder da, Worker reloaded **direkt** auf die Schüler-Route (Log/Browser:
      `#/counter` → `#/counter/student/<id>`, **kein** Root-Load). (2) Gleiches
      mit **Lupe-Schüler** (`search_call`): Reload → Schüler wiederhergestellt,
      `worker_ready`, keine `waiting`. (3) Gleiches mit **Hintergrund-Schüler**
      (Peek offen): Reload → Schüler als aktiv zurück, Worker reloaded.
      (4) **Fallback**: Situation erzwingen, in der das Barcode-Feld nach
      direktem Reload nicht erscheint → `load_card()` läuft, Kartei steht am
      Ende. (5) Buchdaten nach Reload gegen die IServ-Kartei abgleichen
      (nicht veraltet — gleicher Hash dürfte dank `#/counter`-Hop kein No-Op
      sein). Jede Test-Ausleihe sofort zurücknehmen.

### Neu 2026-07-09 (Host: „Test Config" als eigener Tab statt Sub-Reiter)

Der „Test Config"-Sub-Reiter im „Schüler hinzufügen"-Bereich jedes Klassen-Tabs
entfällt; stattdessen bietet das „+"-Menü (`panel-new`) neben „Neue Klasse
öffnen" jetzt eine zweite Karte „Test Config öffnen". Klick öffnet einen
eigenen, dedizierten Tab (Pseudo-Klasse `Test Config`, kein echter IServ-Code,
kein Katalog-Abruf) und befüllt ihn **sofort** mit den festen Testschülern.
Erneutes Öffnen (weiterer Klick, oder Reload) reaktiviert denselben Kontext
statt eine zweite Queue anzulegen (Dedup über `ctx.form`, analog
`/api/open-class`). „Schüler hinzufügen" in normalen Klassen-Tabs bleibt
unverändert bei „Einzelne Schüler" (jetzt ohne Sub-Tab-Leiste, da nur noch ein
Inhalt).

- Backend: neue Route `POST /api/open-test-config` (`server/routes/api.py`,
  Konstante `TEST_CONFIG_FORM = "Test Config"`); nutzt weiterhin
  `TEST_STUDENTS`/`_load_test_students()`, aber ohne IServ-Roundtrip.
  Bestehende Route `POST /api/add-test-students` bleibt unverändert (weiter
  nutzbar, um Testschüler in **jeden** offenen Kontext nachzuziehen).
- Frontend (`web/host.html`): `panel-new` hat zweite Karte + `openTestConfig()`
  (spiegelt `openClass()`); `buildClassPanel()` ohne Sub-Tab-Leiste mehr, tote
  Funktionen `ctxAddTestStudents`/`ctxSwitchSubTab` + Dispatch-Cases entfernt.
- [x] **Unit-Test**: `tests/test_api_guards.py::test_open_test_config_populates_and_reuses`
      — erster Aufruf befüllt mit allen `TEST_STUDENTS`, zweiter Aufruf
      reaktiviert denselben Kontext (`reused: True`, kein zweiter Eintrag in
      `state.contexts`). Suite grün (148 passed).
- [x] **JS-Syntax**: `node --check` auf den extrahierten `<script>`-Block → OK.
- [ ] **Am Gerät** (manuell): „+" → „Test Config öffnen" klicken, Tab erscheint
      mit 3 Testschülern in der Queue; erneutes Öffnen wechselt nur den Fokus
      (keine doppelte Queue); normaler Klassen-Tab zeigt „Schüler hinzufügen"
      weiterhin korrekt ohne Reiter-Leiste.

### Neu 2026-07-09 (Scanner: Hinweis-Modal für JEDEN nicht-verbuchbaren Scan)

Bisher öffneten nur `book_deleted`/`not_in_stock`/`series_already_lent` ein
Hinweis-Modal; alle anderen nicht-OK Scans liefen nur als Statuszeilen-Text.
Jetzt öffnet **jeder** nicht-OK Scan ein Fenster (gleicher Modal-Baukasten).
Rein client-seitig (`web/scan.js` + `web/student.html`); Server-Pfad
(`evaluate_scan_for_booking`, `process_scan`, `book_alert`-Broadcast) und
IServ/DB unangetastet (read-only, kein Write, kein neues GET). Commit `eba6071`.

- Schüler-Client: `book_deleted` (ausgemustert, **mit und ohne**
  `loaned_to`-Ersatzanspruch) + `not_in_stock` (an andere Person verliehen)
  bleiben **Host-geschlossen** (blockierend, kein Schließen-Button,
  `book_alert_open`); alle übrigen nicht-OK Status (`series_already_lent`,
  `not_enrolled`, `unknown_book`, `not_ready`, `error`) schließt der Schüler
  **selbst** (Button / nächster Scan) und scannt weiter.
- Helfer-Client: **jedes** nicht-OK Modal am Gerät schließbar.
- [x] **Syntax**: `node --check web/scan.js` OK; extrahierter
  `<script>`-Block aus `web/student.html` OK.
- [ ] **Am Gerät** (manuell, read-only): pro Status einmal den Scan treiben
  und Modal-Öffnen + Schließen-Verhalten prüfen — Schüler: dismissible
  Status schließen sich per Button **und** beim Folge-Scan (Weiter-Scannen);
  blocking Status (`book_deleted` mit/ohne Ersatzanspruch, `not_in_stock`)
  nur via Host-Freigabe. Helfer: alle Status am Gerät schließbar.

### Neu 2026-07-09 (Host: Tabs & Einstellungen global — Server-State statt localStorage)

Offene Klassen-Reiter und Einstellungen sind jetzt auf jedem angemeldeten Host-
Rechner sichtbar/synchron. Quelle der Wahrheit = der bereits globale In-Memory-
Serverstate (`state_snapshot` + `broadcast_host`), nicht mehr pro-Browser
`localStorage`. `web/host.html`: `tabOrder` in `applyState` aus `state.contexts`
abgeleitet; `activeTab` rein pro Bediener (In-Memory, nicht persistiert — *Menge*
offen global, *Fokus* pro Browser); Dev-Toggles (PDF-lokal, Klasse-korrigieren,
Schüler-Leihschein) aus `state` spiegeln statt localStorage; Login pusht nicht
mehr lokal → Server. `server/routes/api.py`: `/api/slip-default` broadcastet
zusätzlich `broadcast_host`. **Theme (Auto/Hell/Dunkel) bleibt bewusst pro
Browser in localStorage.** Keine IServ-/DB-Writes; nur App-eigene In-Memory-
Endpunkte. Commit `0e39cd5`.

- [x] **Unit-Suite**: `uv run pytest` **145 grün** (keine Logik auf Server-
      Modellebene geändert; `state_snapshot` unverändert; 1-Zeilen-Broadcast-
      Zusatz in `/api/slip-default` wird von keiner Bestands-Assertion getroffen).
- [x] **localStorage-Check**: `grep localStorage web/host.html` → nur noch
      `theme` (cycleTheme/applyTheme).
- [ ] **Am Gerät — zwei Browser/Profile gegen denselben Server** (manuell,
      read-only): A öffnet Klasse „5a" → Reiter erscheint bei B ohne Reload;
      A schließt „5a" → Reiter verschwindet bei B; A aktiviert/deaktiviert in
      Einstellungen „PDF lokal speichern" / „Klasse korrigieren" / „Schüler-
      Leihschein" → Haken bei B erscheint/verschwindet; Theme umschalten auf A
      → B ändert sein Theme **nicht** (bleibt lokal); B Seite neu laden →
      offene Klassen weiterhin sichtbar, eigener Tab-Fokus fällt auf Host
      (nicht auf A's Fokus).

### Neu 2026-07-09 (Scanner: Lupen-Suche — Schnellsprung zu beliebigem Schüler)

Peek-Modus (`scan.js`): die **Lupe** öffnet ein Such-Panel unter der Statuszeile
— Warteliste fährt per FLIP nach unten, zwei Dropdowns blenden synchron ein
(oben Klasse, unten Schüler der gewählten Klasse). Schüler wählen → `search_call`
lädt ihn (ersetzt den Hintergrund-Schüler). Letzte Klasse wird beim erneuten
Öffnen vorausgewählt (`localStorage`), änderbar. **Read-only** (nur IServ-GETs).

- [x] **Backend**: neue WS-Nachrichten `search_classes`/`search_students`
      (IServ `get_class_names`/`get_students_for_form`, schuljahrbezogen im
      `state.class_names_cache`/`form_students_cache` gecacht, geleert im
      Schuljahreswechsel) + `search_call` (transienter `QueueStudent`, **nicht**
      in einer Queue, laden via `assign_student_to_helper`). `end_student`
      aufräumt auch nicht-gequeuete Schüler (neuer `else`-Zweig via
      `find_helper_for_student`). **Unit-Suite grün** (145 passed; +2 Tests in
      `tests/test_queue_flow.py`: transienter `end_student` + transienter
      `assign_student_to_helper`).
- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; Server-Imports OK.
- [ ] **Am Gerät** (manuell, read-only): Peek öffnen → Lupe → Panel + FLIP;
      Klassen-Liste = alle des Schuljahrs; Schüler-Dropdown pro Klasse;
      Vorauswahl der letzten Klasse beim erneuten Öffnen; Schüler laden →
      Peek endet, Scanner-Ansicht kehrt zurück; danach „Nächster"/Trennen
      räumt den transienten Schüler sauber auf (kein Worker-Leak, Helfer frei).
      *Gegen Produktion nur nach Freigabe; keine Buchung.*

### Neu 2026-07-09 (Helfer-Menü: Klassen-Reiter für alle offenen Host-Klassen)

Im Peek-Modus (`web/scan.js`/`scan.html` + Server-WS) zeigt das Helfermenü jetzt
**Reiter für alle offenen Host-Klassen** (alle nicht-impliziten `state.contexts`),
horizontal scrollbar; eigene Klasse vorausgewählt, sonst erste offene. Pro Reiter
darunter die Warteschlange dieser Klasse mit „Aufrufen"-Button (wie bisher). Der
im Hintergrund verbundene Schüler steht im Peek **nur in der Statuszeile**, die
große `.name-row` ist verborgen. Aufrufen aus einer **fremden** Klasse rebindet
den Helfer an diese Klasse (`helper.context_id` wechselt; danach zieht „Nächster"
aus der neuen Klasse) statt abzuweisen. Die Lupe bleibt unverhalten zusätzlich.
Commit `8bf6c08`.

- [x] **Backend**: `state.real_contexts_summary()` (alle offenen Klassen + je
      wartende Schüler); `hub.broadcast_queue_size` sendet zusätzlich
      `contexts_update` (`{contexts, own_context_id}`, pro Helfer) an denselben
      Kreis (`student_id is None or peeking`), `queue_update` bleibt bestehen;
      `routes/ws.py`: `contexts_update` bei Connect + `peek_queue`; `call` aus
      fremder Klasse rebindet statt Fehler (`rebind_helper_to_context` in
      `sessions.py`). **Unit-Suite grün** (147 passed; +1 in `tests/test_hub.py`
      `contexts_update`-Broadcast, +1 in `tests/test_queue_flow.py` Rebind).
- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; Server-Imports OK.
- [ ] **Am Gerät** (manuell, read-only): Host öffnet zwei Klassen-Tabs, Helfer
      verbinden; Menü öffnen → Reiter für beide Klassen, eigene (re-)selektiert,
      eigene Queue mit Aufrufen-Buttons; fremden Reiter wählen → dessen Queue;
      Menü schließen+erneut öffnen → wieder eigene Klasse selektiert; aktiver
      Reiter „nach unten offen" (verbindet optisch mit der Queue darunter);
      Aufrufen eines fremden Schülers → Schüler geladen, Helfer an fremde Klasse
      gebunden (Host-Helfer-Tabelle zeigt neue Klasse); im Peek Hintergrund-Schüler
      **nur** in Statuszeile (`.name-row` verborgen); viele Klassen → Reiter
      horizontal scrollbar; Lupe weiterhin verfügbar.

### Neu 2026-07-09 (Helfer-Menü: Menü-Button im Idle nutzbar)

Das Hamburger-Menü ist jetzt auch **ohne zugewiesenen Schüler** (Idle)
funktionsfähig. Es klappt im Idle lediglich die Kamera-Zeile ein (Fokus auf die
ohnehin sichtbare Warteschlange) und fährt sie wieder aus — **kein
Server-Roundtrip** (`peek_queue`/`peek_close` entfallen), `queue-view` bleibt
durchgehend an (`keepQueueView`-Flag an `animateMenu`). Die Lupe ist im
Idle-Menü ebenfalls nutzbar (`search_call` funktioniert serverseitig auch ohne
aktuellen Schüler). Rein client-seitig; keine neuen WS-Typen, kein
Server-/DB-/IServ-Zugriff. Commit `9d5f413`.

- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; keine Server-Änderung.
- [ ] **Am Gerät** (manuell, read-only): Helfer ohne Schüler → Menü-Button
      klappt Kamera-Zeile ein, Queue bleibt sichtbar; Titel „Scanner anzeigen";
      erneut tippen → Kamera wieder eingeblendet; Lupe öffnet Suche, Aufruf
      eines IServ-Schülers lädt ihn (serverseitig `search_call` ohne aktuellen
      Schüler); Schüler wird geladen → Idle-Menü hinfällig, Peek-Verhalten ab
      dann wie gehabt; Burger-Icon morphet synchron mit dem Menü-FLIP zu ←.
- [x] **Menü-Icon-Animation**: drei Balken → Linkspfeil (←) beim Öffnen, auf
      derselben `.35s cubic-bezier`-Kurve wie der Menü-FLIP; `prefers-reduced-
      motion` respektiert (.01ms). CSS-only, kein JS.
- [x] **Warteschlangen-Überschrift**: `qh-title` übernimmt Schrift des
      Schülernamens (`.s-name`: 1.5rem/700/line-height 1.2) — keine
      Kapitälchen/Sperrung/Transparenz mehr.

### Neu 2026-07-08 (Host-Überarbeitung: Settings + Tab-System)

Multi-Kontext-Refactor des Hosts (`web/host.html`) + Backend
(`server/state.py`, `routes/api.py`, `ws.py`, `sessions.py`, `hub.py`).
Zielbild + Phasen: `docs/PLAN.md` bzw. Plan in dieser Session.

- [x] **Backend-Kontext-Modell** (`state.py`): `ClassContext`, `contexts`-Dict,
      `active_context_id`, Kompat-Properties (`queue`/`active_form`/`book_order`
      delegieren an aktiven Kontext), `find_student`/`find_student_with_ctx`
      suchen über alle Kontexte, `next_pending`/`pending_count`/… nehmen
      `context_id`. `HelperSession.context_id` neu. **Unit-Suite grün**
      (143 passed) — bestehende Tests laufen über die Kompat-Properties weiter.
- [x] **Routen-Migration**: `/api/open-class`, `/api/close-class`,
      `/api/set-active-context`, `/api/helper/{token}/class` neu;
      `add-student`/`add-test-students`/`disconnect-all`/`reset-queue`/
      `clear-queue` nehmen `context_id` im Body; `next-student` zieht aus
      `helper.context_id`; Scanner-WS-Handler (`peek_queue`, Waiting-Msg,
      `call`-Guard) kontextbewusst. **Suite grün** (143 passed).
- [ ] **Frontend Tab-Chrome** (manuell am Gerät): Tab-Leiste unter Status-Bar
      `[Host] [Klasse … ×] … [+]`; Schuljahr-Auswahl aus `#setup-col` in den
      Einstellungen-Dialog (erster Block, inkl. 409-Confirm + Reset aller
      Klassen-Reiter); `hostTabs`-Persistenz in localStorage (Reihenfolge +
      aktiver Tab). Reload stellt Tabs aus Snapshot + localStorage wieder her
      (fehlende `context_id`s nach Server-Restart droppen).
- [ ] **Klassen-Tab pro Kontext**: eigene Queue + eigenes Now-Serving +
      Pairing-Card (Codes zugeordnet zu den wartenden Schülern DIESER Klasse);
      „Schüler hinzufügen" (Einzelne + Test Config) hängt pro Kontext an.
      Host-Tab: Helfer-Tabelle mit neuer **Klassen-Spalte** (Select zum
      Umbinden) + Modus-B-Kontrolle (öffnen/schließen, QR, iPad-Freischalt),
      keine eigene Queue. × pro Tab → `POST /api/close-class` (Confirm bei
      aktiven Schülern).
- [ ] **Helfer-Klassen-Bindung**: „Nächster" zieht aus `helper.context_id`;
      `call`-Guard weist klassenfremde Aufrufe ab („Schüler nicht in deiner
      Klasse"). Am Gerät mit ≥2 Klassen + ≥1 Helfer prüfen.
- [ ] **E2E-Skripte migrieren** (`automation/e2e_smoke.py`,
      `e2e_parallel.py`, `e2e_modus_b.py`): selektoren auf `#queue-tbody` /
      `#now-serving` / `#setup-col` sind obsolet — die Queue lebt jetzt pro
      Klassen-Tab (`[data-ctx-queue="<id>"]`, `[data-ctx-ns="<id>"]`). Skripte
      müssen erst einen Tab öffnen (`POST /api/open-class` → Kontext-Panel)
      und gegen dessen Selektoren prüfen. **Nur read-only / nach Freigabe**
      (PLAN §6, Produktionsschutz).
- [x] **JS-Syntax**: `node --check` auf den extrahierten `<script>`-Block → OK.
- [x] **Server-Imports**: `server.main`/`routes.api`/`routes.ws`/`hub`/
      `sessions`/`state` importieren sauber.

### Neu 2026-06-17 (Host: Reiter „Test Config")

> **Überholt (2026-07-09):** Der Sub-Reiter „Test Config" innerhalb eines
> Klassen-Tabs wurde entfernt und durch einen eigenen Top-Level-Tab ersetzt —
> siehe „Neu 2026-07-09 (Host: „Test Config" als eigener Tab statt Sub-Reiter)"
> oben. `TEST_STUDENTS`/`add-test-students` (IDs, Idempotenz-Test) bleiben
> unverändert gültig.

- [ ] **Reiter „Test Config"** (`host.html`): Auswahl des Reiters fügt die festen
      Testschüler automatisch an die Queue an (`switchTab('test')` →
      `addTestStudents()`); Button als manueller Re-Trigger. IDs fest verdrahtet
      in `TEST_STUDENTS` (`server/routes/api.py`), **keine** IServ-Abfrage:
      Niklas Müller (2159), Lukas Podleschny (2164), Lucas Stolpe (2167).
      Idempotent (Duplikate übersprungen) — **Unit-getestet** in
      `tests/test_api_guards.py::test_add_test_students_idempotent` (2026-06-18);
      bestehende Queue/Sessions unangetastet (am Gerät noch zu sichten).
      *Server nach Route-Änderung neu starten — `reload=False`; ein POST auf eine
      noch nicht registrierte Route liefert 405 (StaticFiles-Catch-all), nicht 404.*

### Neu 2026-06-17 (Scanner: Weiter-Button + Statuszeilen-Layout)

- [ ] **Weiter-Button (⏭) am Gerät** (`scan.html`, nur Helfer): schließt den
      aktuellen Schüler ab und lädt den nächsten aus der Queue; alter Schüler
      verschwindet **sofort**, Statuszeile zeigt „Wird geladen…", neuer Schüler
      erscheint nach kurzem Laden. Leere Queue → „Warteschlange leer".
      **Wichtig:** dabei darf im simulierten Browser nichts gebucht werden
      (Worker-Page wird nur geschlossen, kein Submit).
- [ ] **Statuszeilen-Layout:** Statuszeile nur so breit wie das Kamerafeld,
      links Drucker-Button (Druck-Funktion implementiert 2026-06-22 — WS
      `print` → `print_loan_slip_for`, am Gerät zu prüfen), rechts Weiter-Button; Name vertikal
      mittig zu Klasse/Bezahlt; farbiger Status-Punkt in beiden Clients entfernt.

### Neu 2026-06-17 (Scanner: Dark/Light + Klasse + transparente Zeilen)

- [ ] **Dark-/Light-Mode am Gerät** (`scan.html` + `student.html`): folgt
      `prefers-color-scheme` (erst Browser-Override, dann System); kein Toggle.
      In DevTools (*Rendering → Emulate prefers-color-scheme*) und am echten
      Handy beide Themes auf Lesbarkeit prüfen (Kontraste, native Controls).
- [ ] **Buchzeilen transparent:** Tint + Rand statt Vollfläche — vorgemerkt
      (orange) / ausgeliehen (grün) in beiden Themes noch erkennbar.
- [ ] **Klasse über Bezahlstatus:** zeigt z. B. „10c" **ohne** „Klasse"-Präfix;
      erscheint erst nach Schüler-Zuweisung/Pairing (kommt aus der Queue).

### Neu 2026-06-16 (Scanner-UI-Redesign + Buch-Daten-Anreicherung)

- [ ] **Scanner-Layout am Gerät** (`scan.html` + `student.html`): obere Leiste
      Zahnrad/Kamera-Streifen/Taschenlampe+Ton, volle Statuszeile, großer Name mit
      Bezahlstatus rechtsbündig, scrollbare Bücher-Tabelle — auf dem Handy prüfen
      (Querformat-Kamerastreifen + Scan funktioniert, nur Tabelle scrollt).
- [ ] **Bücher-Tabelle mit echten Daten:** Spalten Fach | Titel | Status-Icon;
      vorgemerkt (gelb/orange, ⏳) oben, ausgeliehen (hellgrün/dunkelgrün, ✓) unten;
      Titel + Fach korrekt aus `client.series` aufgelöst (Niklas' Test-Schüler).
- [ ] **Serien-Katalog-Cache** (`IsServClient._get_series_map`, read-only
      `GET /series`): erste Schülerauswahl lädt den Katalog einmalig; Titel/Fach
      auch für bereits ausgeliehene Bücher (nur `code`+`isbn` im Roh-Payload) gefüllt.

### Neu in dieser Session (Druck + Packaging)

- [ ] **Leihschein-Druck `file`-Backend** end-to-end über den Endpoint
      `POST /api/print-loan-slip` (Server laufend, read-only PDF-Abruf gegen
      IServ → PDF in `automation/out/loan_slips/`). Mit Niklas' Test-Schüler.
- [ ] **Druck `lp` (macOS, USB-Drucker)** — echter Ausdruck auf dem Macbook.
- [x] **Druck `sumatra` (Windows)** — Silent-Print am Ausleihe-Laptop mit
      HP LaserJet Professional P1102 (= Spike C / O4) — 2026-06-22 (→ V12).
- [ ] **Host-Button „Leihschein"** (UI) löst Druck korrekt aus, Statusmeldung.
      Code vollständig verdrahtet (2026-06-22): `printLoanSlip` → `POST
      /api/print-loan-slip` → `print_loan_slip_for`; am Gerät zu prüfen.
- [ ] **Seitenwahl-Toggle „Schüler-Leihschein"** (host.html, 2026-06-22): es
      wird stets der 2-seitige Beleg geholt; Seite 1 immer gedruckt, Seite 2 nur
      bei aktivem Toggle (`second_page` → `pages=None`, sonst `pages="1"`).
      Seitenbereich via `lp -o page-ranges=` bzw. SumatraPDF `-print-settings`;
      `win-default` kann **nicht** einschränken (druckt alle Seiten — WARN-Log).
      **Am echten Drucker prüfen**, dass `-print-settings "1"` wirklich nur
      Seite 1 druckt (Sumatra-Pfad ist der Produktivweg).
- [ ] **Scanner-Button „Leihschein" (🖨)** (`scan.html`, nur Helfer) löst Druck
      des aktuell zugewiesenen Schülers aus: WS `{type:'print'}` →
      `print_loan_slip_for(helper.student_id)` → `print_result`. Button während
      Druck deaktiviert, Statuszeile zeigt Backend/Detail bzw. Fehler.
      Unit: `tests/test_printing.py::test_print_loan_slip_for_reads_and_prints`
      (2026-06-22); am Gerät mit echtem Drucker zu prüfen.
- [ ] **`setup.bat` / `start.bat`** am echten Windows-Laptop (uv vorhanden,
      `uv sync`, Playwright-Install, Start).
- [ ] **`start.sh`** auf dem Macbook.
- [ ] Leihschein `variant="student-always_school-auto"` (2-Seiten-Beleg) prüfen,
      falls Schul-Beleg gewünscht.

### Härtung 2026-06-15 (gegen IServ / im Betrieb zu prüfen)

- [ ] **Selektor-Drift-Canary** (`WorkerPool.check_selectors`, read-only) beim
      Server-Start: bestätigen, dass er `input.tt-input[name="input"]` findet und
      bei DOM-Änderung WARN loggt (echter IServ-Read, kein Submit).
- [ ] **Worker-Status im Host** (`Worker: x/y frei`) live prüfen, inkl.
      Warnfarbe bei 0 Workern.
- [ ] **`secure`-Cookie** + **Logfile-Rotation** (`logs/server.log`) im echten Lauf
      gegenchecken (kein Schülername im Log — PLAN §3.7).

### Härtung 2026-06-15 (Frontend/TLS/Robustheit) — am Gerät zu prüfen

- [ ] **TLS-Cert am Zielgerät:** Handy verbindet über `https://<Laptop-IP>:3443`
      ohne CN/Host-Mismatch (SAN greift); Cert-Erzeugung ohne openssl-Binary
      (jetzt via `cryptography`).
- [ ] **`select-class`-Guard:** Klassenwechsel bei aktiven Sessions → 409 +
      Host-Confirm → Force räumt Sessions sauber ab (keine Waisen).
- [ ] **Einzelschüler-Reiter (2026-06-16):** Reiter *Einzelne Schüler* →
      `GET /api/students-for-class` lädt Liste, `POST /api/add-student` hängt
      einzeln an die Queue an (auch klassenübergreifend); Duplikat → 409;
      bestehende Queue/Sessions bleiben unangetastet.
- [ ] **Reconnect-Backoff** (scan/student/qr-display): Trennung → exponentieller
      Backoff bis 30 s, Reset bei Verbindung.

### Neu 2026-07-05 (Einstellungen: Buchreihen ausblenden)

- [ ] **„Ausblenden"-Button je Buch** im Einstellungen-Dialog (`host.html`,
      Reiter „Bücherlisten ordnen"): ausgeblendete Buchreihen eines Jahrgangs
      (`state.hidden_isbns_by_grade`, In-Memory, kein DB-/IServ-Write) gelten
      beim Scannen nicht mehr als „vorgemerkt" — weder in der Scanner- noch
      der Schüler-Anzeige — und sind damit auch nicht buchbar (gefiltert via
      `apply_hidden_books()` in `sessions.py`/`routes/ws.py`, direkt nach
      jedem `get_student_info`-Aufruf). Logik unit-getestet
      (`tests/test_class_book_order.py`: `apply_hidden_books`,
      `get_hidden_isbns_for_form`, State-Reset); **UI-Interaktion am Gerät
      noch zu sichten** (Toggle-Button, Persistenz über „Speichern",
      Live-Effekt bei bereits geladenem Schüler bewusst nicht sofort — analog
      zur bestehenden Bücher-Reihenfolge, erst beim nächsten Laden/Reconnect).

### Neu 2026-07-08 (Serverseitige Persistenz der Buchreihenfolge/Ausblendung)

- [x] **Persistenz `data/booklist_settings.json`** (`server/booklist_store.py`):
      `book_orders_by_grade` + `hidden_isbns_by_grade` werden beim Start geladen
      (`app.py` lifespan) und bei jeder Änderung (`POST /api/booklist-order`,
      `POST /api/booklist-hidden`) atomar weggeschrieben. Einziger globaler Satz
      (nicht pro Schuljahr) — beim Schuljahreswechsel bleibt die Konfiguration
      erhalten, nur `form_catalog_cache` wird geleert (ISBNs jahresspezifisch).
      ISBN-Drift fängt `normalize_book_order` + `hidden & catalog` ab: neue
      Katalog-Bücher sichtbar ans Ende, weggefallene gedroppt (Anforderung).
      Unit-getestet (`tests/test_booklist_store.py`: Round-Trip, fehlende/
      korrupte Datei, data-Dir-Anlage, deterministische Serialisierung,
      neue-ISBNs-ans-Ende, Nicht-String-Einträge gedroppt). Schreib-/Ladefehler
      non-fatal (In-Memory-State bleibt Leading). **Manueller Smoke am Gerät
      noch offen** (Serverneustart → Konfiguration wieder da).

### Aus Review Tier 2 (2026-07-05, PLAN §5 Phase 2)

- [x] **`current_books`-Jahrgangsfilter entfernt (2026-07-06).** Der
      konservative `distributed_at`-Schuljahresfilter in `get_student_info`
      ist raus; `?books=true` liefert zuverlässig nur aktuell ausgeliehene
      Bücher (API-Referenz), so dass alle aktuell ausgeliehenen Exemplare —
      auch noch nicht zurückgegebene Vorjahres-Bücher — als „ausgeliehen"
      ausgewiesen werden. Siehe PLAN §5 Phase 2 (2026-07-06).

### Neu 2026-07-06 (Ermäßigungs-/Befreiungsnachweis + Modus-B-Host-Freigabe)

- [x] **Nachweis-Feldsemantik read-only verifiziert.** Enrollment-Payload
      trägt `remission_request`/`remission_accepted`/`remission_judged_*`
      (Ermäßigung) bzw. `exemption_*` (Befreiung); `*_accepted` ist tri-state
      (`null`=unentschieden, `true`=akzeptiert, `false`=abgelehnt).
      „Nachweis fehlt" = `*_request is True and *_accepted is None`.
      Verifiziert am Testschüler 2159 (kein Antrag → beide Pending=False).
      Gebaut: `get_student_info` liefert `remission_pending`/`exemption_pending`;
      `web/scan.js` + `web/student.html` zeigen den Hinweis in Offen-Farbe
      vor dem Betrag; „Bezahlt" bei Nachweis unterdrückt; „Nicht angemeldet"
      im Schülerclient grau. Suite 92 grün.
- [ ] **Nachweis-Hinweis am Gerät mit echtem Pending-Fall.** Bislang nur
      gegen „kein Antrag" verifiziert — ein Schüler mit unentschiedenem
      Ermäßigungs-/Befreiungsantrag ist auf Prod nicht bekannt. Visueller
      Check der Hinweis-Anzeige + der kombinierte Host-Freigabe-Dialog
      (Modus B, `POST /api/student/pair` → `reason:"blocked"`-409 +
      `blockers`-Liste) steht aus, sobald ein solcher Fall vorliegt.
- [x] **Nicht-angemeldet paaren ohne Nachfrage.** Blocker-Prüfung auf
      `enrolled` gegated — kein False-Positive-Dialog „Nicht bezahlt
      (offen: None €)" mehr. Logik-Review (kein echter Nicht-angemeldet-
      Schüler auf Prod verfügbar).

### Neu 2026-07-07 (Lade-State bis Worker bereit — `worker_ready`)

- [ ] **Modus B live (`web/student.html`):** nach Pairing Statuszeile „Wird
      geladen…", Name/Klasse/Bezahlt sichtbar, Bücher-Bereich zeigt Placeholder
      „Bücher werden geladen…", Scans tun nichts. Sobald Worker ready
      (`worker_ready`-Nachricht): Bücherliste erscheint, Status → „Scanner
      bereit — Buch scannen", Scans funktionieren. **Unit:** Assertion in
      `tests/test_queue_flow.py::test_advance_helper_picks_next_and_completes_previous`
      (Modus A: `student_info` mit `books==[]` + `worker_ready`). Live am
      Testschüler offen (read-only, kein Enter — Niklas+Lukas-Freigabe).
- [ ] **Modus A live (`web/scan.js`):** nach Aufruf Bücherliste sofort sichtbar,
      Status „Warten…", Scans ignoriert. Sobald Worker ready: Status →
      „Scanner bereit — Buch scannen", Scans funktionieren.
- [x] **Reconnect (Seite neu laden) in aktiver Session — Modus A** (`server/routes/ws.py`):
      Helfer lädt die Seite neu, während ein Schüler geladen/wird geladen →
      Schüler wird sofort wieder geladen (`student_info` via GET), und wenn der
      Worker bereits bereit stand, wird die in ihm geöffnete Kartei-Seite per
      `StudentSession.reload()` (Re-Navigation über GET-Routen, kein
      `page.reload()` — kein Post-Re-Post-Risiko) auf dem **bestehenden**
      Context neu geladen; dann `worker_ready`.
      **Update 2026-07-09:** `reload()` springt auf initialisierter Page direkt
      die Schüler-Route an (Hop `#/counter` → `#/counter/student/<id>`, kein
      App-Root-Load mehr) und stellt zudem auch Lupe-Schüler wieder her — siehe
      Sektion oben. Läuft der Lade-Task noch, liefert
      dieser `worker_ready` selbst an den neuen WS. Mechanismus: das `finally`
      des alten Scanner-WS stößt den Teardown verzögert als Task an
      (`_deferred_end`, Grace `_RECONNECT_GRACE_S=3.0`) statt inline; ein
      Reconnect cancelt den Task und übernimmt. Echte Trennung → Teardown nach
      der Frist (Schüler `pending`, Worker zu). Nur GET, kein DB-/IServ-Write.
      **Unit:** `tests/test_scanner_reconnect.py` (14 Tests — Grace-Re-Checks,
      Cancel, In-Flight-Cancel, Exception-Robustheit, Worker-Release ohne
      Queue-Eintrag, reload Normal/Re-Login/Timeout/fehlendes-Re-Login/
      Schüler-Route-Redirect). Live am Gerät noch offen (read-only).
- [ ] **Reconnect (Seite neu laden) — Modus B** (`ws_student`): Worker bereits
      offen → `worker_ready` + Bücher sofort wiederhergestellt (nicht
      „Warten…"/„Wird geladen…" hängen bleiben).

### Neu 2026-07-08 (Helferclient: Menü-Toggle / Peek zwischen Schüler- und Warteschlangen-Ansicht)

- [ ] **Menü-Toggle (Peek) am Gerät** (`web/scan.js`/`scan.html` + Server-Peek-
      Protokoll): Hamburger-Menü (≡) schaltet bei zugewiesenem Schüler auf die
      Warteschlangen-Ansicht, **ohne** ihn zu trennen — er bleibt im Hintergrund
      verbunden, Statuszeile zeigt ihn (`renderPeekStatus`), Name/Zeile bleibt
      sichtbar. Nochmal Drücken kehrt zur Bücherliste zurück. Im Peek werden
      Scans ignoriert. WS `{type:'peek_queue'}`/`{type:'peek_close'}` + transient
      `helper.peeking` (Server) steuern Live-`queue_update`s
      (`broadcast_queue_size`: `student_id is None or peeking`).
      - **Aufrufen eines anderen Schülers aus der Peek-Ansicht** legt den alten
        als **`pending`** (wartend) zurück in die Warteschlange, **nicht** als
        `done` — `call`-Handler `end_student(queue_status="pending",
        session_state="revoked")` (analog Disconnect-Teardown `_deferred_end`).
        „Weiter" (`next`/`advance_helper`) schließt den alten weiter als `done`.
      - Scheitert der Aufruf (Schüler inzwischen von anderem Helfer genommen),
        kehrt der Client automatisch in die Peek-Ansicht zurück (kein
        „Schüler wird geladen …"-Stuck).
      Unit: `tests/test_hub.py` +1 (Peek-Helfer erhält `queue_update`),
      `tests/test_queue_flow.py` +2 (`end_student`/`assign_student_to_helper`
      resetten `peeking`); Suite **133 grün**; `node --check` OK. Live am Gerät
      offen (read-only, kein Enter — Niklas+Lukas-Freigabe).
      - **Animations-Sync (2026-07-09):** Beim Öffnen/Schließen faden die
        ausgeblendeten Steuer-Elemente (gear/reader/right-col/print/next) und die
        Lupe (#search-btn) jetzt synchron mit der Statuszeilen-FLIP-Bewegung
        aus/ein — alles `.35s cubic-bezier(.22,.61,.36,1)`, beide Richtungen.
        `flipAnimate` → `animateMenu(open)`: Steuer-Elemente werden per
        `position:absolute` an alter Stelle festgepinnt (aus dem Fluss → Grid
        kollabiert weiter) und per `opacity` gefadet; Lupe öffnet per CSS-Opacity,
        schließt per Pin. `print`/`next` (in `.status-bar`, das der FLIP per
        `transform` versieht) werden für den Übergang ins nicht-transformierte
        `.top-section` umgehängt — sonst reiten sie auf dem Transform und machen
        dessen diskreten x-Sprung (full-width→Mittel-Spalte) mit. Generation-Guard
        + Reset fangen schnelles Toggeln ab. Headless verifiziert (Playwright):
        kein JS-Fehler, Layout-Kollaps real (Statuszeile 125→7 px), print/next
        nach Zyklus wieder in `.status-bar` in Reihenfolge, keine Inline-Reste.
        Live am Gerät offen.

### Neu 2026-07-07 (Helferclient: Ausleih-Freigabe-Dialog bei Unstimmigkeit)

- [ ] **Freigabe-Dialog bei Unstimmigkeit (`web/scan.js`/`scan.html`, PLAN O10):**
      Schüler mit `remission_pending`/`exemption_pending`/`!paid` (nur bei
      `enrolled`) laden → erstes Buch scannen → `lend-confirm-modal` erscheint
      mit gelisteter Unstimmigkeit, Scan geht **nicht** raus (Buch bleibt
      vorgemerkt, `pendingScans` unverändert).
      - **„Ja, ausleihen"**: Scan wird gesendet (`scan_result` wie gehabt —
        `staged` bei `ALLOW_BOOKING=false`); zweites Buch scannen → **kein**
        Modal (Flag `lendingApproved` gesetzt).
      - **„Nicht ausleihen"** / Escape / Click außerhalb: Status „Nicht
        ausgeliehen — Buch nicht eingegeben", Scan verworfen; selben Barcode
        neu scannen → Modal fragt **erneut**.
      - **Neuladen** („Nächster"/„Aufrufen"/Reconnect): Flag resetted → Modal
        fragt wieder beim ersten Scan.
      Rein client-seitig, nur GET (`student_info`-Flags), kein DB-/IServ-Schreib-
      zugriff. Kein automatisierter Test (UI-Gate); live am Testschüler mit
      künstlicher Unstimmung offen (read-only — Niklas+Lukas-Freigabe).

### Aus dem bisherigen Plan (Phase 3/4)

- [ ] **Lasttest: 5 parallele Schüler-Sessions** (Modus B) — `WORKER_CONTEXTS`
      erhöhen, Pool-Verhalten unter Last prüfen (PLAN §5 Phase 4).
- [ ] **Rate-Limit `/api/student/join` end-to-end** — Drosselung (429) im echten
      Server unter Flut prüfen (Logik ist V9; HTTP-Pfad noch offen, ggf. im Lasttest).
- [ ] **Generalprobe Modus A** im Schul-WLAN mit echtem Drucker (Phase 3).
- [ ] **Spike D — Schul-WLAN-Reichweite / Client-Isolation** (O9), vor Ort.
- [ ] iPad im geführten Zugriff (iOS-Kiosk) — organisatorisch (PLAN §3.4).

### Gesperrt — erst nach Buchungstest-Freigabe (Niklas + Lukas, PLAN §6)

- [ ] **Buchender Submit-Pfad** — Code **gebaut + gated** (`commit_barcode` mit
      Enter, `/api/commit-book`, `ALLOW_BOOKING=false` default; Gate verifiziert = V10).
      Noch zu testen (nur mit Freigabe Niklas + Lukas): `ALLOW_BOOKING=true`, echtes
      Enter, **Erfolgs-/Fehler-Selektoren in `_read_booking_result()` bestätigen**
      (bisher unverifiziert), Ausgabe + sofortige Rücknahme eines ausgemusterten
      Buchs; Rückbau-Plan vorher ausfüllen (`docs/rueckbau_plan_VORLAGE.md`).
- [ ] **Scanner-Fehlerfälle** aus dem DOM (falsche Serie, nicht angemeldet, schon
      verliehen, unbekannter Code) — beobachtbar erst im freigegebenen Buchungstest.
- [ ] **End-to-End inkl. echter Buchung** (Modus A und B).

## Unit-Tests (pytest, `uv run pytest`)

Reine Logik, kein IServ/Playwright/Server — schnell + produktionsneutral, als
Regressions-Netz und QS-Beleg. **149 Tests, grün (2026-07-09; +2 für
Reconnect-stellt-auch-Lupe-Schüler-wieder-her + schnelleren
`StudentSession.reload()` — `HelperSession.student_form`-Setzen/Clear + neue
reload-Goto-Sequenz — siehe `tests/test_queue_flow.py`/
`tests/test_scanner_reconnect.py`; 2026-07-09: +2 für
Helfer-Menü-Klassen-Reiter — `contexts_update`-Broadcast + Rebind
`rebind_helper_to_context` — siehe `tests/test_hub.py`/`tests/test_queue_flow.py`;
2026-07-08: +3 für Menü-Peek — `helper.peeking`-Reset in
`end_student`/`assign_student_to_helper` + `broadcast_queue_size` an peekende
zugewiesene Helfer; 2026-07-07: +14 für
Scanner-Reconnect/Disconnect-Grace + `StudentSession.reload()`, +2 für `lent`-
Menge aus `current_books` bei ausgeblendeten Reihen + ISBN-Umhängung
`vormerk→lent` nach Buchung in derselben Session — siehe PLAN §6.1).** Coverage
(`--cov=server` in `addopts`): **45 %** gesamt
(vorher 39 %/2026-06-18, 37 %, initial 20 %); Kernlogik deutlich höher —
`hub.py` 82 %, `state.py` 93 %, `sessions.py` 60 %, `config.py` 93 %,
`ratelimit.py` 100 %, `tls.py` 69 %, `book_order.py` 76 %.
Bewusst niedrig bleiben IServ-/Playwright-/Wiring-Module (`iserv_client.py`
31 %, `routes/api.py` 31 %, `routes/ws.py`, `app.py`, `main.py`) — die decken
die E2E-Skripte V3–V7 ab.

| Datei | Deckt ab |
|-------|----------|
| `tests/test_hub.py` | WS-Verteiler: `broadcast_host` (Auslieferung + tote Host-Sockets entfernt), `queue_update` (mit `queue`-Liste) nur an unzugewiesene Scanner, `broadcast_queue_size`/`send_scanner` lösen tote Scanner-Sockets (`ws=None`, kein Leak), no-op bei unbekanntem Token; **2026-07-08:** `broadcast_queue_size` erreicht zugewiesene Helfer mit `peeking=True` (Menü-Peek) |
| `tests/test_ratelimit.py` | Drossel (allow/throttle, Fenster-Ablauf, pro-IP, sweep) |
| `tests/test_booking_gate.py` | Buchungs-Gate: ohne Flag kein Worker-/Enter-Zugriff |
| `tests/test_sessions.py` | Session-Lebenszyklus, Token/Code-Eindeutigkeit, harte Invalidierung |
| `tests/test_queue_flow.py` | Queue-Übergänge: `gen_pairing_code` (skip/Erschöpfung), `end_student` (Status/Helfer-Lösung/Worker-Release), `advance_helper` (leer + nächster; sendet `loading`, kein Idle-`waiting`), `assign_student_to_helper` (gezielter Aufruf aus der Warteschlange — ältester Wartender bleibt unangetastet; `loading`-WS-Push), `pending_queue_as_list` (nur status='pending'), harte Worker-Freigabe; **2026-07-09:** `assign_student_to_helper` setzt `helper.student_form` (Queue- wie Lupe-Schüler; Advance wechselt die Form mit), `end_student` räumt `student_form` (auch transienter Zweig); **2026-07-08:** `end_student`/`assign_student_to_helper` resetten `helper.peeking` (Menü-Peek) |
| `tests/test_api_guards.py` | Endpunkt-Logik: Auth-Guard (`_require_host`), Login, `add-student` (Validierung/Duplikat 409), `add-test-students`-Idempotenz, skip/finish-Validierung, Buchungs-Gate HTTP-Ebene (403), `_base_url`/`_last_scan_for` |
| `tests/test_printing.py` | Backend-Resolution (auto je Plattform) + `file`-Backend |
| `tests/test_worker_pool.py` | `WorkerPool.stats()` (total/available/in_use) |
| `tests/test_tls.py` | Cert hat SAN (localhost/127.0.0.1/cn), idempotent |
| `tests/test_booking_precheck.py` | Buchungs-Vorabprüfung (`evaluate_scan_for_booking`: Prüf-Reihenfolge `deleted → series_already_lent → not_in_stock → not_enrolled`; `book_deleted`-Vorrang; `not_in_stock`-vor-`not_enrolled`; `series_already_lent`-vor-`not_in_stock` auch bei lagerndem Exemplar; `unknown_book`, `not_ready`, Lookup-Fehler) + `process_scan`-Gate-Verhalten (Buchen/Stagen/kein Feldkontakt) + Alert-Broadcast (`not_in_stock`/`book_deleted`→Alert mit `source`; `series_already_lent`→kein Alert) + `loaned_to`-Durchreichung (Name-Feld getrennt von `msg`; Helfer-`scan_result`+Host-`book_alert` carry `loaned_to`, Schüler-Source strippt es auf `None`; msg bleibt name-frei) — auch für `book_deleted` mit `student_id` (Ersatzanspruch) + `lent` autoritativ aus `current_books` (ungefiltert, ignoriert `apply_hidden_books` — ausgeblendete Reihe die der Schüler hat bleibt `series_already_lent`) + ISBN-Umhängung `vormerk→lent` nach `booked` in derselben Session (Session-Mutables passed-by-reference, kein Neuladen) |
| `tests/test_scanner_reconnect.py` | Scanner-Reconnect/Disconnect-Grace (Modus A): `_deferred_end`-Re-Checks (Reconnect/`student_id`-Wechsel/neuer Schüler → No-op; echte Trennung → Teardown; Cancel → No-op), In-Flight-`load_task`-Cancel, Exception-Robustheit (`end_student`-/`broadcast_host`-Fehler schlucken), Worker-Release ohne Queue-Eintrag; `StudentSession.reload()` **(2026-07-09: direkter Schüler-Route-Hop `#/counter`→`#/counter/student/<id>` auf initialisierter Page statt App-Root+Schüler-Route, mit `load_card`-Fallback; Re-Login bei Login-Redirect, RuntimeError ohne Re-Login, Barcode-Timeout → Fallback)** — RAM-State/Fake-Pages, kein Browser/IServ |

## Hinweise zum Testen (wenn es so weit ist)

- Server für E2E-Skripte muss laufen (`uv run python -m server.main`); die
  `automation/e2e_*`-Skripte treiben die echten Web-Seiten per Playwright.
- Druck-Tests gefahrlos mit `PRINT_BACKEND=file` beginnen (kein physischer Druck).
- Buchende Tests **nie** unbeaufsichtigt/automatisch; nur Einzelfall-Freigabe.
