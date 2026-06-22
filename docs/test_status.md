# Test- & Verifizierungsstand

> **Lebendes Dokument.** Hält fest, was bereits getestet/verifiziert ist und was
> noch zu testen bleibt. **Konvention:** Jede neue Funktion bzw. jedes neue
> Risiko hier unter „Offen / zu testen" eintragen; nach erfolgreichem Test in
> „Verifiziert" verschieben (mit Datum + Skript/Befund). Bezug: `docs/PLAN.md`.
>
> Stand: 2026-06-15. Alle bisherigen Tests sind **read-only** gegen IServ
> (kein Submit, keine Buchung — PLAN §6).

## Verifiziert (grün)

| # | Was | Wie / Skript | Datum | Befund |
|---|-----|--------------|-------|--------|
| V1 | Spike A — Counter-Seite headless bedienbar (Login, Schülersuche, Kartei) | `automation/spike_a_counter.py --explore` | 2026-06-12 | Selektoren stabil; Login ohne 2FA/Captcha; `docs/spikes/spike_a_protokoll.md` |
| V2 | Spike B — parallele Sessions desselben Accounts (O2) | `automation/spike_b_parallel.py` | 2026-06-12 | 3/3 Logins + 3/3 Cookie-Sharing, keine Invalidierung |
| V3 | Phase-2 E2E-Smoke Modus A (Host→Scanner→Worker→Kartei→staged) | `automation/e2e_smoke.py` | 2026-06-15 | bestanden; Bug in `scan.html` (Panel-Display) gefixt |
| V4 | Worker-Recovery (Re-Login bei Session-Ablauf) | `automation/recovery_test.py` | 2026-06-15 | deterministisch via `clear_cookies()`, bestanden |
| V5 | 2-Helfer-Paralleltest (zwei Schüler gleichzeitig, getrennte Karteien) | `automation/e2e_parallel.py` | 2026-06-15 | bestanden, keine Vermischung |
| V6 | Pool-Härtung (fehlgeschlagene Logins werden nachgezogen, kein Context-Leak) | `WorkerPool.start()` | 2026-06-15 | im Paralleltest mitverifiziert |
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
Regressions-Netz und QS-Beleg. **50 Tests, grün (2026-06-18).** Coverage
(`uv run pytest`, jetzt mit `pytest-cov` als dev-dep + `--cov=server` in
`addopts`): **39 %** gesamt (vorher 37 % / initial 20 %); Kernlogik deutlich
höher — `hub.py` 93 %, `state.py` 92 %, `sessions.py` 49 %, `routes/api.py`
34 %, `config.py` 98 %.
Bewusst niedrig bleiben IServ-/Playwright-/Wiring-Module (`iserv_client.py`,
`routes/ws.py`, `app.py`, `main.py`) — die decken die E2E-Skripte V3–V7 ab.

| Datei | Deckt ab |
|-------|----------|
| `tests/test_hub.py` | WS-Verteiler: `broadcast_host` (Auslieferung + tote Host-Sockets entfernt), `queue_update` nur an unzugewiesene Scanner, `broadcast_queue_size`/`send_scanner` lösen tote Scanner-Sockets (`ws=None`, kein Leak), no-op bei unbekanntem Token |
| `tests/test_ratelimit.py` | Drossel (allow/throttle, Fenster-Ablauf, pro-IP, sweep) |
| `tests/test_booking_gate.py` | Buchungs-Gate: ohne Flag kein Worker-/Enter-Zugriff |
| `tests/test_sessions.py` | Session-Lebenszyklus, Token/Code-Eindeutigkeit, harte Invalidierung |
| `tests/test_queue_flow.py` | Queue-Übergänge: `gen_pairing_code` (skip/Erschöpfung), `end_student` (Status/Helfer-Lösung/Worker-Release), `advance_helper` (leer + nächster), harte Worker-Freigabe |
| `tests/test_api_guards.py` | Endpunkt-Logik: Auth-Guard (`_require_host`), Login, `add-student` (Validierung/Duplikat 409), `add-test-students`-Idempotenz, skip/finish-Validierung, Buchungs-Gate HTTP-Ebene (403), `_base_url`/`_last_scan_for` |
| `tests/test_printing.py` | Backend-Resolution (auto je Plattform) + `file`-Backend |
| `tests/test_worker_pool.py` | `WorkerPool.stats()` (total/available/in_use) |
| `tests/test_tls.py` | Cert hat SAN (localhost/127.0.0.1/cn), idempotent |

## Hinweise zum Testen (wenn es so weit ist)

- Server für E2E-Skripte muss laufen (`uv run python -m server.main`); die
  `automation/e2e_*`-Skripte treiben die echten Web-Seiten per Playwright.
- Druck-Tests gefahrlos mit `PRINT_BACKEND=file` beginnen (kein physischer Druck).
- Buchende Tests **nie** unbeaufsichtigt/automatisch; nur Einzelfall-Freigabe.
