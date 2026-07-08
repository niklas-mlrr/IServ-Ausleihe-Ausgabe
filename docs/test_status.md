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

### Neu 2026-06-17 (Host: Reiter „Test Config")

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
      Context neu geladen; dann `worker_ready`. Läuft der Lade-Task noch, liefert
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
Regressions-Netz und QS-Beleg. **133 Tests, grün (2026-07-08; +3 für
Menü-Peek — `helper.peeking`-Reset in `end_student`/`assign_student_to_helper`
+ `broadcast_queue_size` an peekende zugewiesene Helfer — siehe
`tests/test_hub.py`/`tests/test_queue_flow.py`; 2026-07-07: +14 für
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
| `tests/test_queue_flow.py` | Queue-Übergänge: `gen_pairing_code` (skip/Erschöpfung), `end_student` (Status/Helfer-Lösung/Worker-Release), `advance_helper` (leer + nächster; sendet `loading`, kein Idle-`waiting`), `assign_student_to_helper` (gezielter Aufruf aus der Warteschlange — ältester Wartender bleibt unangetastet; `loading`-WS-Push), `pending_queue_as_list` (nur status='pending'), harte Worker-Freigabe; **2026-07-08:** `end_student`/`assign_student_to_helper` resetten `helper.peeking` (Menü-Peek) |
| `tests/test_api_guards.py` | Endpunkt-Logik: Auth-Guard (`_require_host`), Login, `add-student` (Validierung/Duplikat 409), `add-test-students`-Idempotenz, skip/finish-Validierung, Buchungs-Gate HTTP-Ebene (403), `_base_url`/`_last_scan_for` |
| `tests/test_printing.py` | Backend-Resolution (auto je Plattform) + `file`-Backend |
| `tests/test_worker_pool.py` | `WorkerPool.stats()` (total/available/in_use) |
| `tests/test_tls.py` | Cert hat SAN (localhost/127.0.0.1/cn), idempotent |
| `tests/test_booking_precheck.py` | Buchungs-Vorabprüfung (`evaluate_scan_for_booking`: Prüf-Reihenfolge `deleted → series_already_lent → not_in_stock → not_enrolled`; `book_deleted`-Vorrang; `not_in_stock`-vor-`not_enrolled`; `series_already_lent`-vor-`not_in_stock` auch bei lagerndem Exemplar; `unknown_book`, `not_ready`, Lookup-Fehler) + `process_scan`-Gate-Verhalten (Buchen/Stagen/kein Feldkontakt) + Alert-Broadcast (`not_in_stock`/`book_deleted`→Alert mit `source`; `series_already_lent`→kein Alert) + `loaned_to`-Durchreichung (Name-Feld getrennt von `msg`; Helfer-`scan_result`+Host-`book_alert` carry `loaned_to`, Schüler-Source strippt es auf `None`; msg bleibt name-frei) — auch für `book_deleted` mit `student_id` (Ersatzanspruch) + `lent` autoritativ aus `current_books` (ungefiltert, ignoriert `apply_hidden_books` — ausgeblendete Reihe die der Schüler hat bleibt `series_already_lent`) + ISBN-Umhängung `vormerk→lent` nach `booked` in derselben Session (Session-Mutables passed-by-reference, kein Neuladen) |
| `tests/test_scanner_reconnect.py` | Scanner-Reconnect/Disconnect-Grace (Modus A): `_deferred_end`-Re-Checks (Reconnect/`student_id`-Wechsel/neuer Schüler → No-op; echte Trennung → Teardown; Cancel → No-op), In-Flight-`load_task`-Cancel, Exception-Robustheit (`end_student`-/`broadcast_host`-Fehler schlucken), Worker-Release ohne Queue-Eintrag; `StudentSession.reload()` (App-Root+Schüler-Route re-navigieren, Re-Login bei Login-Redirect auf App-Root UND Schüler-Route, RuntimeError ohne Re-Login, Barcode-Timeout nicht fatal) — RAM-State/Fake-Pages, kein Browser/IServ |

## Hinweise zum Testen (wenn es so weit ist)

- Server für E2E-Skripte muss laufen (`uv run python -m server.main`); die
  `automation/e2e_*`-Skripte treiben die echten Web-Seiten per Playwright.
- Druck-Tests gefahrlos mit `PRINT_BACKEND=file` beginnen (kein physischer Druck).
- Buchende Tests **nie** unbeaufsichtigt/automatisch; nur Einzelfall-Freigabe.
