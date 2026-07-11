# Test- & Verifizierungsstand

> **Lebendes Dokument.** Hält fest, was bereits getestet/verifiziert ist und was
> noch zu testen bleibt. **Konvention:** Jede neue Funktion bzw. jedes neue
> Risiko hier unter „Offen / zu testen" eintragen; nach erfolgreichem Test in
> „Verifiziert" verschieben (mit Datum + Skript/Befund). Bezug: `docs/PLAN.md`.
>
> Stand: 2026-07-09 (Wartbarkeits-Refactoring, 187 Tests grün).
> Alle bisherigen Tests sind **read-only** gegen IServ
> (kein Submit, keine Buchung — PLAN §6).
>
> **Chronologie:** Die ausführliche Beschreibung, *wie* eine Funktion entstand
> (Bugfixes, Nutzer-Korrekturen, Commit-Historie), steht in `docs/CHANGELOG.md`.
> Hier steht nur, *was* aktuell noch zu testen ist.

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
| V13 | **Stale-Guards Modus A/B** — wird `helper.student_id` bzw. die Modus-B-Session während des `open_student()`-Awaits verändert, schließt der neu geöffnete Worker-Context sich selbst statt registriert zu werden (kein Leak) | `tests/test_stale_guards.py` (4 Tests: je Modus A/B ein Stale- und ein Happy-Path-Fall) | 2026-07-09 | grün; Mutationsprobe bestanden (Guard-Zeile auskommentiert → Test rot) |
| V14 | **WS-Message-Dispatch Scanner** — `call` mit pending-Re-Check, Umbinden an fremde Klasse (`rebind_helper_to_context`), `search_call` (Lupe) für queue-losen Schüler + fehlendes `form`, Peek-Toggle (`queue`/`close`), Scan ohne zugewiesenen Schüler, unbekanntes Token, malformed JSON-Frame beendet die Schleife nicht | `tests/test_ws_scanner.py` (8 Tests, echter `websocket_connect` ohne Lifespan, Fake-IServ, `worker_pool=None`) | 2026-07-09 | grün |
| V15 | **Pairing-TOCTOU** — ändert sich zwischen Code-Auflösung und `get_student_info`-Await der Zustand (Session revoked / Schüler bereits vergeben / Code neu belegt), antwortet `/api/student/pair` mit 409 statt eine stale Zuordnung zu committen | `tests/test_api_guards.py::test_student_pair_toctou_*` (3 Tests) + `test_student_pair_happy_path_binds_when_nothing_changed` | 2026-07-09 | grün |
| V16 | **Kontext-Lifecycle** — doppeltes Öffnen derselben Klasse reaktiviert statt dupliziert; `close_class` löst gebundene Helfer; Aktiv-Kontext-Wechsel bei unbekannter ID → 404 | `tests/test_api_guards.py::test_open_class_creates_context_and_populates_queue`, `test_close_class_switches_active_context_when_active_one_closed`, `test_close_class_unknown_context_404`, `test_set_active_context_*` | 2026-07-09 | grün |
| V17 | **`_read_booking_result`** gegen Fake-Locator — `has_not`-Filter (Schutz gegen Selektor-Drift, s. u.), sichtbarer/unsichtbarer Error-Alert, `nothing_found`, Locator-Exception schluckt statt zu propagieren | `tests/test_booking_result.py` (6 Tests) | 2026-07-09 | grün; ohne den `has_not`-Filter wird `test_barcode_only_in_typeahead_input_does_not_count_as_booked` rot (selbst nachgefahren). **Der getestete False-Positive kann im heutigen DOM nicht auftreten** (Feld liegt in keiner `<tr>`, `inner_text()` liefert keine Input-Werte) — der Test sichert den Filter gegen Selektor-Drift, s. „Offen" unten |

## Offen / zu testen

### Offen 2026-07-10 (Host: Sofort-fertig-Filter beim Klassen-Öffnen)

`OpenClassRequest.auto_done` + `_apply_auto_done()` (`server/routes/classes.py`)
setzen Schüler beim Laden einer neuen Klasse sofort auf `done`, wenn eine
gewählte Bedingung zutrifft (nicht angemeldet / nicht bezahlt / Ermäßigungs-
bzw. Befreiungsantrag ohne Nachweis). Nur ad-hoc mit einem temporären
Testfall verifiziert (Fake-IServ-Stub analog `_FakeIServForClasses`, danach
wieder entfernt) — **kein dauerhafter Unit-Test ergänzt**, Suite bleibt bei
187 Tests.

- [ ] Dauerhaften Test in `tests/test_api_guards.py` ergänzen:
      `_FakeIServForClasses` um eine `get_student_info`-Methode erweitern,
      die je nach `student_id` `enrolled`/`paid`/`remission_pending`/
      `exemption_pending` variiert, dann `open-class` mit `auto_done`
      aufrufen und die resultierenden Queue-Status prüfen.
- [ ] Am echten Gerät/Klasse verifizieren, dass die Filter-Checkbox-Auswahl
      korrekt aus `localStorage` vorbelegt wird und die IServ-Parallelabfrage
      (`asyncio.gather` über alle Klassenmitglieder) bei einer echten,
      größeren Klasse (~30 Schüler) performant bleibt.

### Offen 2026-07-09 (Worker: Robustheit der Erfolgs-Erkennung in `_read_booking_result`)

**Vorgeschichte, geklärt:** Ein früherer Eintrag hier vermutete einen
False-Positive — der `has_not`-Filter würde nichts filtern, falls das
Typeahead-Feld ein Geschwister statt ein Nachfahre der Bücherzeilen sei, und
eine fehlgeschlagene Buchung könnte als `booked` durchgehen. Die Auswertung des
DOM-Dumps `automation/out/06b_kartei_geladen.html` beantwortet das:

- `input.tt-input` liegt in einem `<form>` **oberhalb** der Tabellen
  (`form > div.form-group > div.input-group > span.twitter-typeahead`), keine
  der 16 `<tr>` im Dokument enthält ein `<input>`. Der Filter ist also ein No-op.
- Der False-Positive kann trotzdem **nicht** eintreten: der Erfolgs-Check liest
  `inner_text()` einer Zeile, und der *Wert* eines `<input>` ist kein Textknoten.
  Der Filter stammt aus einer früheren Implementierung, die `get_by_text(barcode)`
  über die ganze Seite laufen ließ. Er bleibt als Schutz gegen Selektor-Drift.
- Die Bücherzeilen sind `<tr ng-repeat="book in bl.books">`; der Buchcode steht
  als siebenstellige, nullgepolsterte Zahl in einer eigenen `<td>`-Spalte
  (z. B. `0017798`). Die Selektoren `.books-list`, `.lent-books` und
  `.student-books` kamen im DOM nicht vor und wurden entfernt.

**Was offen bleibt** — beides zeigt in Richtung `unknown`, nie in Richtung
`booked` (und `/api/commit-book` wertet nur `booked` als Erfolg), ist also
sicherheitsseitig unkritisch, aber unbequem im Betrieb:

- [ ] **Substring statt Spalten-Vergleich:** `barcode in row_text` prüft gegen
      den *gesamten* Zeilentext (Titel + Code + Zeitstempel) statt gegen die
      Code-Spalte. Bei durchgängig siebenstelligen Codes praktisch kollisionsfrei,
      aber unpräzise. Ein Wechsel auf einen exakten Spalten-Vergleich ist ein
      Eingriff im scharfen Buchungspfad → **nur mit Freigabe Niklas + Lukas und
      an einem ausgemusterten Buch** (PLAN §6).
- [ ] **Festes Zeitfenster:** `commit_barcode` wartet pauschal
      `wait_for_timeout(1500)` vor dem Auslesen. Rendert Angular die neue Zeile
      langsamer, meldet die Methode `unknown` obwohl gebucht wurde. Beim ersten
      scharfen Lauf beobachten, ob 1500 ms reichen; sonst auf ein
      `wait_for`-Prädikat auf die neue Zeile umstellen.
- [ ] **Erfolgs-/Fehler-Selektoren unverifiziert** (Code-TODO
      `automation/worker.py::commit_barcode`, ~Z. 205, verweist auf
      `_read_booking_result`): die Buchungs-Erfolgs- und Fehler-Selektoren sind
      nach Spike-A-Doku nur best-effort und bis zum ersten freigegebenen
      Realtest **nicht am scharfen DOM bestätigt**. Sicherheitsseitig unkritisch
      (nur ein eindeutiges `booked` im DOM gilt als Erfolg; bei Unsicherheit
      `unknown`, das `/api/commit-book` nicht als Buchung wertet), aber im
      Produktions-Schreibpfad → jede Änderung/Verifikation **nur mit Freigabe
      Niklas + Lukas an einem ausgemusterten Buch** (PLAN §6). TODO im Code
      bewusst stehengelassen, bis der Realtest die Selektoren bestätigt.

### Offen 2026-07-09 (Scanner: Reconnect stellt auch Lupe-Schüler wieder her + schneller Worker-Reload)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09).

- [x] **Unit-Suite**: `uv run pytest` **149 grün** (`tests/test_scanner_reconnect.py`,
      `tests/test_queue_flow.py`).
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

### Offen 2026-07-09 (Host: „Test Config" als eigener Tab statt Sub-Reiter)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09).

- [x] **Unit-Test**: `tests/test_api_guards.py::test_open_test_config_populates_and_reuses`
      — erster Aufruf befüllt mit allen `TEST_STUDENTS`, zweiter Aufruf
      reaktiviert denselben Kontext (`reused: True`, kein zweiter Eintrag in
      `state.contexts`). Suite grün (148 passed).
- [x] **JS-Syntax**: `node --check` auf den extrahierten `<script>`-Block → OK.
- [ ] **Am Gerät** (manuell): „+" → „Test Config öffnen" klicken, Tab erscheint
      mit 3 Testschülern in der Queue; erneutes Öffnen wechselt nur den Fokus
      (keine doppelte Queue); normaler Klassen-Tab zeigt „Schüler hinzufügen"
      weiterhin korrekt ohne Reiter-Leiste.

### Offen 2026-07-09 (Scanner: Hinweis-Modal für JEDEN nicht-verbuchbaren Scan)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Rein client-seitig
(`web/scan.js` + `web/student.html`); Server-Pfad und IServ/DB unangetastet.

- [x] **Syntax**: `node --check web/scan.js` OK; extrahierter
  `<script>`-Block aus `web/student.html` OK.
- [ ] **Am Gerät** (manuell, read-only): pro Status einmal den Scan treiben
  und Modal-Öffnen + Schließen-Verhalten prüfen — Schüler: dismissible
  Status schließen sich per Button **und** beim Folge-Scan (Weiter-Scannen);
  blocking Status (`book_deleted` mit/ohne Ersatzanspruch, `not_in_stock`)
  nur via Host-Freigabe. Helfer: alle Status am Gerät schließbar.

### Offen 2026-07-09 (Host: Tabs & Einstellungen global — Server-State statt localStorage)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Theme bleibt bewusst pro
Browser in localStorage.

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

### Offen 2026-07-09 (Scanner: Lupen-Suche — Schnellsprung zu beliebigem Schüler)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). **Read-only** (nur IServ-GETs).

- [x] **Backend**: neue WS-Nachrichten `search_classes`/`search_students`/
      `search_call`. **Unit-Suite grün** (145 passed; +2 Tests in
      `tests/test_queue_flow.py`).
- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; Server-Imports OK.
- [ ] **Am Gerät** (manuell, read-only): Peek öffnen → Lupe → Panel + FLIP;
      Klassen-Liste = alle des Schuljahrs; Schüler-Dropdown pro Klasse;
      Vorauswahl der letzten Klasse beim erneuten Öffnen; Schüler laden →
      Peek endet, Scanner-Ansicht kehrt zurück; danach „Nächster"/Trennen
      räumt den transienten Schüler sauber auf (kein Worker-Leak, Helfer frei).
      *Gegen Produktion nur nach Freigabe; keine Buchung.*

### Offen 2026-07-09 (Helfer-Menü: Klassen-Reiter für alle offenen Host-Klassen)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Commit `8bf6c08`.

- [x] **Backend**: `state.real_contexts_summary()` + `contexts_update`-Broadcast +
      `rebind_helper_to_context`. **Unit-Suite grün** (147 passed; +1 in
      `tests/test_hub.py`, +1 in `tests/test_queue_flow.py`).
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

### Offen 2026-07-09 (Helfer-Menü: Menü-Button im Idle nutzbar)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Commit `9d5f413`. Rein
client-seitig; keine neuen WS-Typen, kein Server-/DB-/IServ-Zugriff.

- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; keine Server-Änderung.
- [ ] **Am Gerät** (manuell, read-only): Helfer ohne Schüler → Menü-Button
      klappt Kamera-Zeile ein, Queue bleibt sichtbar; Titel „Scanner anzeigen";
      erneut tippen → Kamera wieder eingeblendet; Lupe öffnet Suche, Aufruf
      eines IServ-Schülers lädt ihn (serverseitig `search_call` ohne aktuellen
      Schüler); Schüler wird geladen → Idle-Menü hinfällig, Peek-Verhalten ab
      dann wie gehabt; Burger-Icon morphet synchron mit dem Menü-FLIP zu ←.
- [x] **Menü-Icon-Animation** + **Warteschlangen-Überschrift**: CSS-only,
      erledigt (siehe `docs/CHANGELOG.md`).

### Offen 2026-07-08 (Host-Überarbeitung: Settings + Tab-System)

Multi-Kontext-Refactor des Hosts (`web/host.html`) + Backend
(`server/state.py`, `routes/api.py`, `ws.py`, `sessions.py`, `hub.py`).
Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-08).

- [x] **Backend-Kontext-Modell** + **Routen-Migration**: siehe CHANGELOG.
      **Unit-Suite grün** (143 passed) — bestehende Tests laufen über die
      Kompat-Properties weiter.
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

### Überholt 2026-06-17 (Host: Reiter „Test Config")

Der Sub-Reiter „Test Config" innerhalb eines Klassen-Tabs wurde 2026-07-09
entfernt und durch einen eigenen Top-Level-Tab ersetzt — siehe „Offen
2026-07-09 (Host: „Test Config" als eigener Tab statt Sub-Reiter)" oben.
`TEST_STUDENTS`/`add-test-students` (IDs, Idempotenz-Test, Unit-getestet
2026-06-18) bleiben unverändert gültig; kein offener Testpunkt mehr.

### Offen 2026-06-17 (Scanner: Weiter-Button + Statuszeilen-Layout)

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

### Offen 2026-06-17 (Scanner: Dark/Light + Klasse + transparente Zeilen)

- [ ] **Dark-/Light-Mode am Gerät** (`scan.html` + `student.html`): folgt
      `prefers-color-scheme` (erst Browser-Override, dann System); kein Toggle.
      In DevTools (*Rendering → Emulate prefers-color-scheme*) und am echten
      Handy beide Themes auf Lesbarkeit prüfen (Kontraste, native Controls).
- [ ] **Buchzeilen transparent:** Tint + Rand statt Vollfläche — vorgemerkt
      (orange) / ausgeliehen (grün) in beiden Themes noch erkennbar.
- [ ] **Klasse über Bezahlstatus:** zeigt z. B. „10c" **ohne** „Klasse"-Präfix;
      erscheint erst nach Schüler-Zuweisung/Pairing (kommt aus der Queue).

### Offen 2026-06-16 (Scanner-UI-Redesign + Buch-Daten-Anreicherung)

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

### Offen in dieser Session (Druck + Packaging)

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

### Offen 2026-07-05 (Einstellungen: Buchreihen ausblenden)

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

### Offen 2026-07-08 (Serverseitige Persistenz der Buchreihenfolge/Ausblendung)

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

### Offen 2026-07-06 (Ermäßigungs-/Befreiungsnachweis + Modus-B-Host-Freigabe)

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

### Offen 2026-07-07 (Lade-State bis Worker bereit — `worker_ready`)

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
- [x] **Reconnect (Seite neu laden) in aktiver Session — Modus A** (`server/routes/ws.py`,
      Mechanismus + 2026-07-09-Update: `docs/CHANGELOG.md`). **Unit:**
      `tests/test_scanner_reconnect.py` (14 Tests). Live am Gerät noch offen
      (read-only).
- [ ] **Reconnect (Seite neu laden) — Modus B** (`ws_student`): Worker bereits
      offen → `worker_ready` + Bücher sofort wiederhergestellt (nicht
      „Warten…"/„Wird geladen…" hängen bleiben).

### Offen 2026-07-08 (Helferclient: Menü-Toggle / Peek zwischen Schüler- und Warteschlangen-Ansicht)

Details/Umsetzung (Protokoll, Animations-Sync 2026-07-09): `docs/CHANGELOG.md`.

- [ ] **Menü-Toggle (Peek) am Gerät** (`web/scan.js`/`scan.html`): Hamburger-Menü
      (≡) schaltet bei zugewiesenem Schüler auf die Warteschlangen-Ansicht,
      ohne ihn zu trennen; nochmal Drücken kehrt zurück; Aufrufen eines
      anderen Schülers aus der Peek-Ansicht legt den alten als „pending"
      zurück; Scheitert der Aufruf, kehrt der Client automatisch in die
      Peek-Ansicht zurück. Unit: `tests/test_hub.py` +1,
      `tests/test_queue_flow.py` +2; Suite **133 grün**; `node --check` OK.
      Live am Gerät offen (read-only, kein Enter — Niklas+Lukas-Freigabe).
- [x] **Animations-Sync** (Steuer-Elemente/Lupe faden synchron mit der
      Statuszeilen-FLIP-Bewegung): Headless verifiziert (Playwright), kein
      JS-Fehler, Layout-Kollaps real. Live am Gerät offen.

### Offen 2026-07-07 (Helferclient: Ausleih-Freigabe-Dialog bei Unstimmigkeit)

Details/Umsetzung: `docs/CHANGELOG.md` (PLAN O10).

- [ ] **Freigabe-Dialog bei Unstimmigkeit** (`web/scan.js`/`scan.html`):
      Schüler mit `remission_pending`/`exemption_pending`/`!paid` (nur bei
      `enrolled`) laden → erstes Buch scannen → Dialog erscheint, Scan geht
      erst nach „Ja, ausleihen" raus; „Nicht ausleihen"/Escape verwirft;
      Neuladen resettet die Freigabe. Rein client-seitig, nur GET, kein
      Schreibzugriff. Kein automatisierter Test (UI-Gate); live am
      Testschüler mit künstlicher Unstimmigkeit offen (read-only —
      Niklas+Lukas-Freigabe).

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
      (Listen-Selektoren gegen einen DOM-Dump verifiziert, Filter-Logik
      unit-getestet = V17; offen bleiben Substring-statt-Spalten-Vergleich und das
      feste 1500-ms-Fenster — siehe „Offen 2026-07-09 (Worker: Robustheit …)" oben),
      Ausgabe + sofortige Rücknahme eines ausgemusterten Buchs; Rückbau-Plan vorher
      ausfüllen (`docs/rueckbau_plan_VORLAGE.md`).
- [ ] **Scanner-Fehlerfälle** aus dem DOM (falsche Serie, nicht angemeldet, schon
      verliehen, unbekannter Code) — beobachtbar erst im freigegebenen Buchungstest.
- [ ] **End-to-End inkl. echter Buchung** (Modus A und B).

## Unit-Tests (pytest, `uv run pytest`)

Reine Logik, kein IServ/Playwright/Server — schnell + produktionsneutral, als
Regressions-Netz und QS-Beleg. **187 Tests, grün (2026-07-09; +29 aus dem
Wartbarkeits-Refactoring — neu `tests/test_stale_guards.py` (4),
`tests/test_ws_scanner.py` (8), `tests/test_booking_result.py` (6) sowie
Kontext-Lifecycle- und Pairing-TOCTOU-Ergänzungen in `tests/test_api_guards.py`;
siehe V13–V17 oben und `docs/CHANGELOG.md` für die Details.
Vorherige Chronologie (2026-06-16 – 2026-07-08) steht in `docs/CHANGELOG.md`,
nicht mehr hier ausgeschrieben).** Coverage
(`--cov=server` in `addopts`): **59 %** gesamt
(vorher 47 %/2026-07-08, 45 %/2026-07-05, 39 %/2026-06-18, 37 %, initial 20 %);
`routes/ws.py` 13 % → 38 %, `sessions.py` 65 % → 74 %, `modus_b.py` 21 % → 47 %,
`classes.py` 52 % → 75 %. Kernlogik deutlich höher —
`hub.py` 82 %, `state.py` 93 %, `sessions.py` 60 %, `config.py` 93 %,
`ratelimit.py` 100 %, `tls.py` 69 %, `book_order.py` 76 %.
Bewusst niedrig bleiben IServ-/Playwright-/Wiring-Module (`iserv_client.py`
31 %, `routes/ws.py` 38 %, `app.py`, `main.py`) — die decken die E2E-Skripte
V3–V7 ab. `routes/api.py` selbst ist nur noch der Aggregator (100 %, 8
Anweisungen); die Endpunkt-Logik sitzt jetzt in den neun `routes/`-Modulen,
siehe Coverage-Tabelle oben.

| Datei | Deckt ab |
|-------|----------|
| `tests/test_hub.py` | WS-Verteiler: `broadcast_host` (Auslieferung + tote Host-Sockets entfernt), `queue_update` (mit `queue`-Liste) nur an unzugewiesene Scanner, `broadcast_queue_size`/`send_scanner` lösen tote Scanner-Sockets (`ws=None`, kein Leak), no-op bei unbekanntem Token; **2026-07-08:** `broadcast_queue_size` erreicht zugewiesene Helfer mit `peeking=True` (Menü-Peek) |
| `tests/test_ratelimit.py` | Drossel (allow/throttle, Fenster-Ablauf, pro-IP, sweep) |
| `tests/test_booking_gate.py` | Buchungs-Gate: ohne Flag kein Worker-/Enter-Zugriff |
| `tests/test_sessions.py` | Session-Lebenszyklus, Token/Code-Eindeutigkeit, harte Invalidierung |
| `tests/test_queue_flow.py` | Queue-Übergänge: `gen_pairing_code` (skip/Erschöpfung), `end_student` (Status/Helfer-Lösung/Worker-Release), `advance_helper` (leer + nächster; sendet `loading`, kein Idle-`waiting`), `assign_student_to_helper` (gezielter Aufruf aus der Warteschlange — ältester Wartender bleibt unangetastet; `loading`-WS-Push), `pending_queue_as_list` (nur status='pending'), harte Worker-Freigabe; **2026-07-09:** `assign_student_to_helper` setzt `helper.student_form` (Queue- wie Lupe-Schüler; Advance wechselt die Form mit), `end_student` räumt `student_form` (auch transienter Zweig); **2026-07-08:** `end_student`/`assign_student_to_helper` resetten `helper.peeking` (Menü-Peek) |
| `tests/test_api_guards.py` | Endpunkt-Logik: Auth-Guard (`_require_host`), Login, `add-student` (Validierung/Duplikat 409), `add-test-students`-Idempotenz, skip/finish-Validierung, Buchungs-Gate HTTP-Ebene (403), `_base_url`/`_last_scan_for`; **2026-07-09 (V15/V16):** Kontext-Lifecycle (Doppel-Öffnen reaktiviert, `close_class` löst Helfer, unbekannte Kontext-ID → 404) + drei Pairing-TOCTOU-Fälle (Session revoked / Schüler vergeben / Code neu belegt während des `get_student_info`-Awaits → 409) |
| `tests/test_stale_guards.py` (V13) | `open_student()`-Re-Check nach dem Await: ändert sich `helper.student_id` (Modus A) bzw. die Modus-B-Session während des Worker-Aufbaus, wird der neue Context sofort wieder geschlossen statt registriert (kein Leak); Happy-Path registriert korrekt |
| `tests/test_ws_scanner.py` (V14) | Scanner-WS-Dispatch über echten `websocket_connect` (ohne Lifespan, Fake-IServ, `worker_pool=None`): unbekanntes Token, Scan ohne Schüler, malformed JSON-Frame tötet die Schleife nicht, Peek-Toggle, `call` auf nicht-pending Schüler, `call` bindet Helfer an fremde Klasse um, `search_call` für queue-losen Schüler, `search_call` ohne `form` → Fehler |
| `tests/test_booking_result.py` (V17) | `_read_booking_result` gegen Fake-Locator: sichtbarer Error-Alert (gekürzt) → `error`, unsichtbarer Alert ignoriert, Barcode in Buchzeile → `booked`, Barcode **nur** im Typeahead-Feld → `unknown` (der `has_not`-Filter, der genau das verhindert), nichts gefunden → `unknown`, Locator-Exception wird geschluckt |
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
