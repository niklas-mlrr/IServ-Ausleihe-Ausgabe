# Projektplan: IServ Ausleihe-Ausgabe

> Initialisierungsplan vom 2026-06-12, seither laufend fortgeschrieben
> (zuletzt 2026-07-05, Review Tier 1–3). Basiert auf der Projektskizze
> (Seminarfach) und den Klärungsfragen vom 2026-06-12.
> Dieses Dokument ist die Arbeitsgrundlage — Änderungen hier einpflegen.

## 1. Zielbild

**Eine App, zwei Modi**, gehostet auf dem Windows-Laptop der Schulbuchausleihe
im Schul-WLAN:

| Modus | Einsatz | Wer scannt | Kern-Ablauf |
|-------|---------|-----------|-------------|
| **A — Stapel** (Teil 1) | Sommerferien, Stapelerstellung | Helfer mit eigenem Handy | Laptop wählt Klasse → Schüler alphabetisch abarbeiten → Helfer scannt Bücher per Handykamera → Buchung → Leihschein-Druck |
| **B — Live-Ausgabe** (Teil 2, Pilot) | Schuljahresbeginn, Testklasse/-jahrgang ab Jg. 9 | Schüler mit eigenem Gerät | iPad zeigt allgemeinen QR → Schüler scannt → Handy zeigt 4-stelligen Code → Host ordnet Code einem Schüler zu und bestätigt → Schüler sieht bestellte Bücher, scannt sie selbst → Buchung |

**Leitplanken aus der Skizze (nicht verhandelbar):**

- Keine Schreiboperationen auf die Ausleihe-Datenbank durch selbstprogrammierten
  Code. Alle Writes laufen durch das **offizielle IServ-Frontend** (siehe
  Write-Pfad). Die `ausleihe-api` wird ausschließlich **read-only** genutzt.
- Bestehendes System (USB-Handscanner) bleibt jederzeit als Fallback nutzbar.
- Keine dauerhafte Speicherung von Schülerdaten; Website nur im Schul-WLAN,
  zugriffsgeschützt.
- Tests ausschließlich mit Niklas' Account und ausgemusterten Büchern.

## 2. Architektur

```text
Helfer-/Schüler-Handy (Browser: Kamera-Scanner)      iPad (QR-Anzeige)
        │  HTTPS + WebSocket                              │
        ▼                                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Python-Server (FastAPI) — Windows-Laptop, Schul-WLAN, Port 3443 │
│                                                                  │
│  ├─ Web-UI (statisch, vanilla JS):                               │
│  │    host.html   Laptop: Klasse/Schüler wählen, Pairing,   │
│  │                     Status aller Sessions, Skip-Funktion      │
│  │    scan.html        Handy: Scanner-UI (aus Fork übernommen)   │
│  │    qr-display.html  iPad: zeigt allgemeinen anonymen QR-Code  │
│  │                                                              │
│  ├─ ausleihe-api (read-only, Admin-Account):                     │
│  │    Klassen/Schüler, Anmeldungen, Bezahlstatus,               │
│  │    bereits ausgeliehene Bücher, Leihschein-PDF               │
│  │                                                              │
│  ├─ Playwright-Worker:                                           │
│  │    N Browser-Contexts auf der offiziellen IServ-Ausleihe-     │
│  │    Counter-Seite (eingeloggt). Pro aktivem Schüler ein        │
│  │    Context. Scan → Barcode-Feld füllen → Submit → Ergebnis    │
│  │    (Erfolg/Fehler) aus der UI zurücklesen.                    │
│  │    → Der Write geht durchs offizielle Frontend inkl. dessen   │
│  │      Validierung (bezahlt? richtige Serie? schon verliehen?)  │
│  │                                                              │
│  └─ Druck-Service:                                               │
│       get_loan_slip_pdf() → Drucker (Windows, silent print)      │
└──────────────────────────────────────────────────────────────────┘
```

**Stack-Entscheidungen** (geklärt 2026-06-12):

- **Backend:** Python (FastAPI + websockets), Neuaufbau. Die `ausleihe-api`
  wird als Dependency eingebunden (`pip install git+…` oder lokaler Pfad).
- **Write-Pfad:** Playwright-Browser-Automatisierung der offiziellen UI —
  **ein Mechanismus für beide Modi**. Begründung: skizzenkonform (offizielles
  Modul schreibt), parallelisierbar (ein Context pro Schüler), nutzt die
  eingebaute Validierung der offiziellen Website.
- **Frontend:** Vanilla HTML/JS ohne Build-Step (wie bisher);
  `html5-qrcode` (vendored), `beep.mp3` und die Scanner-UI-Basis werden aus
  dem Fork übernommen.
- **Alt-Code:** Node-Server und Python-Keyboard-Client werden entfernt
  (Git-Historie bewahrt sie); sauberes neues Projekt-Layout.
- **Accounts:** Ein Ausleihe-Admin-Account (Niklas) für API-Reads **und**
  Playwright-UI-Sessions. Credentials in `.env` (nicht im Repo).
- **Druck:** Server holt das offizielle Leihschein-PDF über die API und
  druckt direkt (SumatraPDF `-print-to` oder `win32print`).

## 3. Rollen- und Sicherheitsmodell

| Rolle | Gerät | Zugang | Darf |
|-------|-------|--------|------|
| Host | Laptop | Passwort-Login → Session-Cookie | alles: Klasse/Schüler wählen, Pairing bestätigen, Skip, Abbruch, Druck |
| Helfer-Scanner (Modus A) | Helfer-Handy | Join-Code vom Host (oder Passwort) | zugewiesenen Schüler sehen, Bücher scannen |
| QR-Anzeige (Modus B) | iPad | Registrierung am Host per Code; danach **nur** QR-Anzeige, keine Schülerdaten im Klartext | QR-Codes anzeigen |
| Schüler-Session (Modus B) | Schüler-Handy | allgemeiner **konstanter** Join-QR (festes Secret, kein Rotieren) → Server mintet pro Browser-Session `session_token` + 4-stelligen Pairing-Code, vom Host bestätigt | nur eigene Bestelldaten sehen, nur eigene Bücher scannen |

Sicherheitsanforderungen (aus Klärung 2026-06-12, „keine Sicherheitslücken"):

1. **Einmal-Tokens:** _(Mechanismus geändert 2026-06-15, siehe
   `docs/phase4_modus_b_2026-06-15.md`.)_ Das iPad zeigt einen **allgemeinen,
   anonymen** QR. Beim Scan mintet der Server pro Browser-Session einen langen,
   kryptographisch zufälligen `session_token` (~256 bit) — den **einzigen**
   Daten-Zugang — und einen 4-stelligen `pairing_code`. Der Code dient nur der
   menschlich vermittelten Zuordnung am Host und gewährt **nie** selbst
   Datenzugriff. Server-seitiger Zustand entscheidet über Gültigkeit.
2. **Harter Zugriffsentzug:** Nach erfolgreichem Abschluss des Ausgabe-Prozesses
   (oder Timeout/Abbruch durch Host) wird das Token serverseitig
   invalidiert und die WebSocket-Session beendet. Erneuter Aufruf der URL →
   neutrale „Vorgang abgeschlossen"-Seite, keine Daten.
3. **Doppelte Bestätigung:** Token allein reicht nicht — die Session wird erst
   aktiv, wenn der Host den 4-stelligen Pairing-Code dem Schüler zuordnet
   und bestätigt.
4. **iPad-Absicherung:** Das QR-Display zeigt **ausschließlich** den allgemeinen
   QR-Code — **keine** Namen/Initialen (O8 geklärt: anonym, 2026-06-15). Die
   Display-Session ist eine eigene Rolle ohne Datenzugriff; Registrierung nur
   über den Host. iPads zusätzlich im geführten Zugriff (iOS Kiosk-Modus).
5. **Skip-Funktion:** Host kann Schüler überspringen (krank/abwesend);
   deren Tokens werden nie ausgegeben bzw. sofort invalidiert.
6. **Transport:** HTTPS mit selbstsigniertem Zertifikat (Logik aus dem Fork
   nach Python portieren), nur im Schul-WLAN erreichbar.
7. **Keine Persistenz:** Schülerdaten nur im RAM der Session; Logs ohne
   personenbezogene Daten (Buch-Codes ja, Namen nein).

## 4. Offene Punkte

| # | Frage | Vorschlag / nächster Schritt |
|---|-------|------------------------------|
| O1 | Modus A: Wie kommt ein Schüler auf ein bestimmtes Helfer-Handy? | **Umgesetzt (2026-06-17):** Helfer tippt den „Weiter"-Button (⏭) im Scanner → WS `{type:"next"}` → `sessions.advance_helper`: schließt den aktuellen Schüler ab (`end_student`, **kein** Browser-Submit) und vergibt den nächsten Pending aus der Queue. Host kann weiterhin via „Nächster Schüler" zuweisen. |
| O2 | Erlaubt IServ mehrere parallele Sessions desselben Accounts? | **Geklärt (Spike B, 2026-06-12):** Ja — 3/3 parallele unabhängige Logins + 3/3 Cookie-Sharing-Contexts, keine Invalidierung. Context-Pool mit unabhängigen Contexts. |
| O3 | Exaktes Verhalten der offiziellen Counter-Seite (DOM, Fehlerfälle, Schüler-Wechsel) | Spike A erkundet das mit Test-Account + ausgemustertem Buch. |
| O4 | Welcher Drucker (USB am Laptop? Netzwerk? Treiberlage unter Windows)? | **Teil-adressiert (2026-06-15):** Druck-Service gebaut (`server/printing.py`, Backends `file`/`lp`/`sumatra`/`win-default`/`auto`), read-only PDF-Abruf via `get_loan_slip_pdf`. Echter Silent-Print am Zielgerät (= Spike C) noch offen → `docs/test_status.md`, `docs/deployment.md`. |
| O5 | Bezahlstatus-Anzeige: genaue Quelle (`enrollments`/`payments` via Admin-API) und Sonderfälle (Befreiung/Ermäßigung) | In Phase 2 gegen echte Daten read-only verifizieren. |
| O6 | Modus B: Was passiert bei „nicht bezahlt"? (Buch zurücklegen, Helfer rufen?) | **Teil-geklärt (2026-06-15):** UI zeigt Bücher + „nicht bezahlt"-Banner; Host kann beim Pairing per `override_payment` freigeben (Befreiung/Ermäßigung). Fachlicher Wortlaut/Workflow noch mit Hr. Pühn final. |
| O7 | Deployment-Packaging für den Windows-Laptop (Python-Installation? portable venv? `start.bat`?) | Phase 3; Kandidat: `uv` + Lockfile + Start-Skript, alternativ portable Python. |
| O8 | Zeigt das QR-Display Namen/Initialen zur Orientierung oder nur anonyme QRs? | **Geklärt (2026-06-15): anonym.** Ein allgemeiner QR, keine Schülerdaten auf dem iPad (Mechanismus geändert → `docs/phase4_modus_b_2026-06-15.md`). |
| O9 | Schul-WLAN: Client-Isolation zwischen Handy und Laptop? | Spike D; Erfahrung mit dem bisherigen Barcode-Scanner spricht dagegen, trotzdem vor Ort verifizieren. |

## 5. Phasenplan

Timeline-Anker: **Teil 1 muss zum Ferienbeginn (Anfang/Mitte Juli 2026)
einsatzbereit sein.** Teil 2 zum Schuljahresbeginn (Ende August 2026).

### Phase 0 — Projekt-Setup (KW 24/25) — abgeschlossen

- [x] Repo umstrukturiert: Alt-Code raus, Python-Projektgerüst
      (`server/`, `web/`, `automation/`, `docs/`, `pyproject.toml`)
- [x] Scanner-Assets übernommen (`html5-qrcode.min.js`, `beep.mp3`,
      Scan-Logik aus `scanner.html` → `web/scan.html`/`web/scan.js`)
- [x] `.env`-Handling + `CLAUDE.md` mit Read-only-/Produktions-Schutzregeln
      (analog `ausleihe-api`)
- [x] Dieses Plandokument committen; README neu geschrieben

### Phase 1 — Spikes: Risiken zuerst (KW 25/26)

> Erst wenn Spike A funktioniert, lohnt der Rest. Scheitert er, müssen wir
> den Write-Pfad neu diskutieren (→ `processBook`-API wäre die Alternative,
> erfordert aber eine Skizzen-/Policy-Entscheidung).

- [ ] **Spike A (kritisch):** Playwright gegen die offizielle Counter-Seite —
      Login, Schüler öffnen, Barcode eintragen, Submit, Erfolg/Fehler aus dem
      DOM auslesen. Test: ausgemustertes Buch auf Niklas' Account ausleihen
      **und zurücknehmen**.
- [x] **Spike B:** 2–3 parallele Contexts mit demselben Account (→ O2) — erledigt 2026-06-12
- [ ] **Spike C:** Silent-Print eines PDFs unter Windows (→ O4)
- [ ] **Spike D:** Reichweitentest im Schul-WLAN: Handy ↔ Laptop (→ O9)

### Phase 2 — Kern Modus A (KW 26–28)

- [x] FastAPI-Server: HTTPS (selbstsigniert), WebSocket-Hub, Session-/Rollenmodell — 2026-06-12
- [x] Host-UI: Login, Klasse wählen, alphabetische Queue, Live-Status Helfer-Sessions — 2026-06-12
- [x] Host-UI: Schuljahr auswählbar (`GET /api/schoolyears` + `POST /api/select-schoolyear`,
      read-only) — 2026-06-17. Default = laufendes Jahr, sonst das nächste
      (deterministisch aus `begin`/`end`, nicht blind `/schoolyears/current`);
      Wechsel resettet Queue/Klasse mit Active-Session-Guard. Schuljahr wird durch
      Klassen-/Schüler-/Karteiabrufe durchgereicht.
- [x] Helfer-Scanner-UI: Token-basiert, Schüleranzeige (angemeldet/bezahlt/Bücher), Scan-Feedback — 2026-06-12
- [x] Scanner-„Weiter"-Button (⏭): Helfer schließt aktuellen Schüler ab + lädt
      nächsten aus der Queue selbst (O1) — WS `{type:"next"}` → `advance_helper`;
      **kein** Browser-Submit (`end_student`→`release_worker`→`page.close()`).
      Schüler verschwindet sofort, Statuszeile „Wird geladen…" — 2026-06-17.
      Status-Push jetzt **vor** Worker-Aufbau (sofort sichtbar statt erst nach
      Reload); Modus-A-Laden zentral in `sessions.load_and_push_helper_student`.
      Scanner-Statuszeile auf Kamerafeld-Breite, flankiert von Drucker-Button
      (Platzhalter) + Weiter-Button; Status-Punkt entfernt.
- [x] Playwright-Worker: Context-Pool (N unabhängige Logins), Schülerkartei laden, Barcode staged (kein Submit) — 2026-06-12
      - Kartei seit 2026-06-17 **direkt per Schüler-ID-Route**
        (`#/counter/student/<id>` via `_goto_authed`) statt Nachnamen-Typeahead —
        eindeutig pro Schüler, keine Namensgleichheit/Tippfehler (`38c5094`).
      - Debug: `.env` `HEADLESS=false` (sichtbarer Browser) + `SLOW_MO_MS`
        (verlangsamt jede Aktion) — nur auf Geräten mit Display (`c77436c`).
- [x] Recovery (Re-Login bei Session-Ablauf) — 2026-06-15 (`automation/worker.py`, deterministisch getestet via `automation/recovery_test.py`)
- [x] E2E-Smoke headless (read-only): voller Modus-A-Flow Host→Scanner→Worker→Kartei→staged — 2026-06-15 (`automation/e2e_smoke.py`)
- [x] 2-Helfer-Paralleltest: zwei Schüler gleichzeitig aktiv, beide Karteien parallel, unabhängiges Staging — 2026-06-15 (`automation/e2e_parallel.py`)
- [x] Pool-Härtung: fehlgeschlagene Worker-Logins werden in `start()` einmal nachgezogen, geleakte Contexts geschlossen — 2026-06-15
      - **Leak-Fix bei schnellem „Weiter"-Klicken 2026-07-05:** Wahrer Grund
        war ein permanenter Context-Leak, nicht nur eine Race.
        `load_and_push_helper_student` läuft als `create_task`; `open_student`
        pop'd einen Context und lief in `load_card()` (~5 s), aber erst
        **nach** Return registrierte `set_worker_session` den Worker in
        `student_worker_sessions[id]`. „Weiter" vor `load_card`-Ende →
        `end_student(id)` → `pop(id)` → None → nichts freigegeben → Context
        geleakt. Bei `WORKER_CONTEXTS=2` und zwei schnellen Klicks Pool
        dauerhaft leer (jeder weitere Schüler: 12 s Timeout). Fix (gekoppelt):
        (a) `open_student`: `except Exception` → `except BaseException` —
        `CancelledError` ist seit Py3.8 `BaseException`, der alte Code ließ
        den Context beim Cancel durchrutschen; Handler gibt Context +
        `notify_all()` zurück. (b) `load_task`-Feld an `HelperSession`/
        `StudentSessionB`; `end_student`/`invalidate_session` canceln den
        laufenden Lade-Task → Context kommt zurück. Zusätzlich (mildere Race)
        `WorkerPool._lock` → `asyncio.Condition`, `open_student` wartet bis
        12 s statt sofort zu werfen. Regressionstests in
        `tests/test_worker_pool.py` + `tests/test_queue_flow.py`.
        Siehe `_logs/2026-07-05_sba_worker_pool_release_race.md`.
      - **Root-Cause-Fix 2026-07-05b (Commit `d3a75bd`, Review-Tier 1):** Der
        obige Fix war symptomatisch; vier strukturelle Lücken blieben:
        (a) `release_worker` feuer-te `asyncio.create_task(pool.release(...))`
        ohne Strong-Ref → Task konnte mid-Release geGC'd werden (asyncio
        hält Tasks nur schwach) → Context-leak. Fix: modullevel
        `_release_tasks`-Set + `add_done_callback(discard)`.
        (b) `load_task.cancel()` wurde **nicht awaited** — war der Task
        bereits nach `await open_student` im **synchronen** `set_worker_session`,
        traf `CancelledError` erst am nächsten `await` (keines mehr) → Task
        registriert Worker für bereits abgebrochenen Schüler → orphaned.
        Fix: jedes `cancel()` jetzt
        `with contextlib.suppress(asyncio.CancelledError): await task`;
        plus Stale-Guard in `load_and_push_*` (`assigned_student_id` capturen,
        nach `open_student` re-checken, sonst Worker schließen ohne Registrierung).
        (c) `remove_helper` (api.py) + `ws_scanner`-finally (ws.py) clear'ten
        nur die WS — Schüler blieb `active`, Worker orphaned (Modus A hatte
        keine TTL-Recovery wie Modus B). Fix: beide rufen jetzt
        `end_student(..., pending, revoked)` + cancel/await `load_task`.
        (d) `sweep_expired_sessions` ohne try/except → eine Exception tötet
        den Sweeper dauerhaft. Fix: try/except pro Iteration (CancelledError
        re-raise, Rest log+continue) + Batch-Broadcast.
        **Privacy im gleichen Commit:** `TEST_STUDENTS` (echte Schülernamen)
        aus `server/routes/api.py` in gitignored `tests/test_students.local.json`
        ausgelagert (Default nur Niklas); `session_token[:6]`-Logging →
        `sha256[:8]`-Handle. Suite grün (85). Siehe
        `_logs/2026-07-05_sba_pool_leak_root_causes.md` +
        `wiki/40_experience_logs/lessons_learned.md` („Await task.cancel()").
- [x] Buchender Submit-Pfad als Code vorhanden, **dreifach gated** — 2026-06-15:
      `commit_barcode()` (Enter+Result-Parse) + `handle_commit()` + Endpoint
      `POST /api/commit-book`. Gates: `ALLOW_BOOKING=false` (Default) + Host-Auth
      + `confirm:true`. Feuert ohne Freigabe **nie** gegen Produktion (verifiziert:
      bei Default wird der Worker nicht berührt). Enter/Selektoren unverifiziert bis
      zum freigegebenen Test.
- [ ] Fehlerfälle Scanner: falsches Buch, nicht angemeldet, schon ausgeliehen (braucht freigegebenen Buchungstest)
- [x] Leihschein-Druck — Code fertig: read-only PDF-Abruf + Druck-Abstraktion
      (`server/printing.py`, Endpoint `POST /api/print-loan-slip`, Host-Button) —
      2026-06-15. Echter Druck am Zielgerät noch zu verifizieren (`docs/test_status.md`).
- [x] Helfer-Druck-Dialog (`web/scan.html`) statt Sofortdruck — 2026-06-23:
      Klick auf den Drucker-Button öffnet ein Modal mit (a) Warnung „Erst X von Y
      vorgemerkten Büchern gescannt" inkl. Liste der offenen Titel, (b) Checkbox
      „Schüler-Leihschein (2. Seite)", (c) Buttons **Abbrechen / Drucken / Drucken
      & nächster Schüler** (letzterer schaltet nur bei `print_result.ok` weiter).
      - Checkbox-Default = Host-Toggle, server-gesynct: Host pusht seinen
        `slip-second-page`-Stand via `POST /api/slip-default` → `state.slip_second_page_default`
        → `Hub.broadcast_settings` → Helfer (`{type:"settings"}`); Helfer bekommt
        den Wert auch beim WS-Connect. Reines UI-Setting, **kein IServ-/DB-Zugriff**.
      - WS `print` nimmt jetzt `second_page` entgegen → `pages = None|"1"`.
      - Buchliste aktualisiert sich live nach jedem Scan: `scan_result` trägt die
        `isbn`, der Client markiert das Buch „erledigt" (rein visuell; Scans
        bleiben `staged`, kein Submit). Dialog wartet vor dem Vergleich via
        `pendingScans`-Zähler auf den Abschluss laufender Scans.
- [x] Scanner-Buchliste: erledigte (gescannt/ausgeliehen) sinken nach unten —
      2026-07-02 (`web/scan.html`, `isBookDone()` + stabile Sortierung).
- [x] Erledigt-Gruppe nach **Ausgabe-Aktualität** sortiert (jüngstes oben) — 2026-07-02d:
      In der Erledigt-Gruppe ersetzt der „doneRank" die Klassen-Reihenfolge —
      **gerade in dieser Session gescannte/ausgegebene Bücher zuerst** (nach
      Scan-Reihenfolge, zuletzt oben; `scanOrder`-Map, da staged/gebuchte Bücher im
      Client-Payload noch kein `distributed_at` tragen), darunter die schon vorher
      ausgeliehenen nach `distributed_at` (desc). `web/scan.html` + `web/student.html`.
- [x] Konfigurierbare **klassenweite Bücher-Reihenfolge** für den Scanner —
      2026-07-02: Host legt per Drag & Drop die Anzeige-Reihenfolge fest, gilt für
      die ganze Klasse und bleibt über Schülerwechsel (Reset nur bei Klassen-/
      Schuljahreswechsel, Queue-leeren). Karte „Bücher-Reihenfolge (Scanner)" in
      `web/host.html` zeigt die **ausleihbaren Bücher des Jahrgangs** aus der
      offiziellen **Jahrgangs-Bücherliste** (`GET /schoolyears/:sy/booklists/:id`,
      Klassenstufe = `form["grade"]`) — Basis geändert 2026-07-02b: nicht mehr die
      Vereinigung der Einzelanmeldungen, sondern die vollständige Jahrgangsliste
      (unabhängig davon, welche Schüler gerade angemeldet sind). Nur `borrowable=True`
      (keine Kauf-/Arbeitshefte), dedupliziert, `series_data` liefert Titel/Fach
      direkt. Zugriff über `GET /api/class-book-order` (on-demand,
      `iserv_client.get_class_book_catalog`, read-only, 2 GETs statt N).
      **Mehrjahresbände sind enthalten** (2026-07-02d): die komplette ausleihbare
      Jahrgangsliste wird gezeigt — der frühere `min(gradesFlat)`-Filter (nur
      unterster Jahrgang) wurde auf Wunsch entfernt. Drag & Drop mit
      **horizontaler Einfügemarke** (kein Zeilen-Highlight). Speichern via
      `POST /api/class-book-order` (`normalize_book_order` beschränkt auf Katalog +
      hängt fehlende an). `state.book_order` reist in `student_info`/`settings`
      mit; Scanner (`web/scan.html`, Modus A) **und** Schülerseite
      (`web/student.html`, Modus B) sortieren nach `[erledigt, Klassen-Reihenfolge,
      Original]`. Jeder Schüler sieht weiterhin nur seine eigenen Bücher.
      Tests: `tests/test_class_book_order.py`.
- [x] **Host-Einstellungen-Dialog** (2026-07-04) — die zwei Inline-Umschalter der
      Status-Bar (Tailscale-IP, Schüler-Leihschein) in einen Modal-Dialog
      („Einstellungen"-Button, Stil wie Druck-Dialog) ausgelagert. Speichern
      übernimmt nur Änderungen, Abbrechen/Esc verwirft. Enthält zusätzlich:
      - **Drucker-Auswahl:** Dropdown der dem Gerät bekannten Drucker.
        `list_printers()` in `server/printing.py` (rein lesend: Windows
        `Get-Printer`/`Win32_Printer Default=TRUE`, macOS/Linux `lpstat -e/-d`).
        `GET /api/printers`, `POST /api/printer` → In-Memory
        `state.printer_name_override` (None = `PRINTER_NAME` aus `.env` bzw.
        Systemstandard). `print_loan_slip_for` nutzt Override vor `cfg.printer_name`
        (Host + Helfer). „Kein Drucker gefunden", wenn nichts verfügbar.
      - **Bücherlisten ordnen (jahrgangsweit):** verallgemeinert die
        klassenweite Reihenfolge auf **alle Jahrgänge** des Schuljahrs, vorab
        konfigurierbar — ein **Reiter je Booklist** (Jahrgang), Katalog lazy
        geladen, per Drag & Drop sortierbar. `state.book_orders_by_grade`
        (dict grade→ISBN-Liste, In-Memory; Reset nur bei Schuljahreswechsel via
        `reset_booklist_orders`). `GET /api/booklists` (`get_booklists_overview`
        → `[{id,grade,title}]`), `GET|POST /api/booklist-order?grade=`
        (`get_booklist_catalog_by_grade`). `get_class_book_catalog` liefert jetzt
        `(grade, catalog)`; `_ensure_class_catalog` seedet `book_order` aus der
        jahrgangsweiten Reihenfolge, `POST /api/class-book-order` schreibt in
        dieselbe Map — Klassen- und Jahrgangs-Ordnung teilen sich `grade` als
        Key. Speichern für den Jahrgang der geladenen Klasse zieht `book_order`
        live nach (`broadcast_settings`). Alles nur GET/In-Memory, kein DB-Write.
        Tests: `tests/test_class_book_order.py` erweitert (Suite 79 grün).
- [x] **Karte „Bücher-Reihenfolge (Scanner)" entfernt** (2026-07-05) — mit dem
      Einstellungen-Dialog (s.o.) war sie funktional komplett redundant (gleicher
      Katalog, gleiche `book_orders_by_grade`-Ablage), zeigte aber zwei Bugs:
      (1) `POST /api/booklist-order` pushte nur per `broadcast_settings` an die
      Scanner-Helfer-Sessions, nie per `broadcast_host` an den Host selbst — eine
      im Einstellungen-Dialog gespeicherte Reihenfolge aktualisierte weder die
      (jetzt entfernte) Klassen-Karte noch `state.book_order` am Host live, bevor
      man neu geladen hat. Fix: beide Bücher-Reihenfolge-POST-Endpunkte rufen
      jetzt zusätzlich `broadcast_host(state.state_snapshot())`.
      (2) `_ensure_class_catalog` (seedet `book_order` aus `book_orders_by_grade`)
      wurde bisher nur durch den Klick auf „Bücher laden & anordnen" ausgelöst —
      ohne den Klick blieb `book_order` leer, auch wenn im Einstellungen-Dialog
      längst eine Reihenfolge vorkonfiguriert war. Fix: `select_class` ruft
      `_ensure_class_catalog` jetzt automatisch auf, Fehler dabei sind nicht
      fatal (Klasse bleibt geladen, `book_order` bleibt leer wie bisher ohne
      Klick). Damit greift eine vorab im Einstellungen-Dialog gesetzte
      Reihenfolge sofort beim Klassenwechsel, ganz ohne Zusatzklick.
      `GET|POST /api/class-book-order` + zugehöriges Frontend (`web/host.html`:
      `boOrder`/`loadBookOrder`/`renderBookOrderList`/Drag-Handler/`saveBookOrder`/
      `syncBookOrderCard`) entfernt; `normalize_book_order`/`_ensure_class_catalog`
      bleiben (jetzt einzig von `select_class` genutzt). Bestehende Tests
      (`tests/test_class_book_order.py`) testen nur die Katalog-/Normalisierungs-
      Logik, nicht die entfernten Endpunkte — unverändert grün (Suite 92).
- [x] **Bücher-Reihenfolge pro Schüler-Jahrgang statt globaler Klassen-Order**
      (2026-07-05) — bis hierhin hing die Helfer-Anzeige an **einer** globalen
      `state.book_order` für „die aktive Klasse". Für klassenübergreifende
      Warteschlangen (einzeln hinzugefügte Schüler, „Test Config"-Tab) mit
      Schülern aus verschiedenen Jahrgängen war das falsch: alle Helfer
      bekamen dieselbe (meist leere oder zum falschen Jahrgang passende)
      Reihenfolge. Fix: neues Modul `server/book_order.py` mit
      `get_book_order_for_form(state, form)` — ermittelt den Jahrgang **des
      jeweils zugewiesenen Schülers** (über `IsServClient.get_class_book_catalog`)
      und liefert dessen `book_orders_by_grade`-Konfiguration, mit
      `state.form_catalog_cache` (form → (grade, catalog_isbns)) gegen
      wiederholte IServ-Roundtrips. `hub.broadcast_settings()` berechnet die
      Reihenfolge jetzt **pro verbundenem Helfer** anhand seines eigenen
      Schülers, statt einen globalen Wert an alle zu pushen; alle vier
      `student_info`-Baustellen (`sessions.py` ×2, `routes/ws.py` ×2 —
      Scanner-Reconnect + Modus-B-Reconnect) nutzen dieselbe Funktion. Live
      per Playwright-freiem WS-Test verifiziert: zwei Helfer mit Schülern aus
      Jahrgang 10 und 12 (ohne geladene Klasse, reiner Test-Config-Betrieb)
      bekamen nach einer Jahrgangs-Umsortierung im Einstellungen-Dialog sofort
      ihre jeweils eigene, unterschiedliche Reihenfolge gepusht.
      `get_book_order_for_form` fängt IServ-Fehler intern ab (Fallback
      `state.book_order`) — ein Fehler dort darf `student_info` nie
      verhindern, da der Aufruf in `load_and_push_helper_student` außerhalb
      des einzigen Try/Except-Blocks liegt. Suite weiter grün (85).
- [x] **Review-Tier-2-Hardening** (2026-07-05, gebündelt in Commit `63a4cb3`)
      — Edge-Case-Bugs + Härtung aus dem Codebase-Review (4 Review-Agenten,
      Tier 2). Dateibegrenzt parallel umgesetzt, Suite grün (85):
      (a) `automation/worker.py`: `new_page()` an beiden Stellen im
      try/except (Context wird bei Fehlschlag zurück in den Pool gelegt);
      `release()` Double-Release-Guard (`session._context = None`);
      `start()`-Cancel schließt aufgebaute Contexts; `_read_booking_result`
      scoped auf Bücher-Liste (exkl. Eingabefeld), bleibt `unknown`-Default.
      (b) `server/iserv_client.py`: `(b.get("BookView") or {})` (null-safe);
      `threading.Lock` um Lazy-Init von `_client`/`_resolve_sy`/
      `_get_series_map` (Lock hält nicht während API-Calls);
      konservativer `current_books`-Jahrgangsfilter via `distributed_at`
      (keep-when-unknown — sicher gegen falsche Enter; **validierungsbedürftig**
      gegen echtes `?books=true`-Payload, falls Vorjahres-Bücher kommen).
      (c) `web/`: `escapeHtml` auf Kamera-id/-label (scan+student);
      `host.html` `JSON.parse` try/catch; `pushSlipDefault` erst post-Login;
      `qr-img.src` nur bei `data:image/`-Prefix.
      (d) `server/routes/api.py`: 7× `int(student_id)`→400; `secrets.compare_digest`
      für Host-Passwort + `join_secret` + neues `login_limiter` (5/15s);
      `request.client is None`→400; `_base_url` vertraut **nicht mehr** dem
      `Host`-Header-Hostnamen (IP aus `cfg.host_ip`/Auto-Erkennung, nur Port
      aus Host — sonst Host-Header-Injection ins QR-URL mit `join_secret`).
      `ws.py`: `receive_json` fängt `json.JSONDecodeError`. `ratelimit.py`:
      Dead-Pop-then-recreate entfernt (leere Deques werden jetzt echt
      evicted). `config.py`: `req_int`-Helper (klare `SystemExit`-Fehler).
      (e) `server/printing.py`: PDF-Dateiname µs+`token_hex` (keine
      Sekunden-Kollision); PowerShell UTF-8-Console-Prefix; `_print_win_default`
      via `asyncio.to_thread` (blockiert nicht den Event-Loop); `pages`-Regex-
      Validierung. `server/tls.py`: Zertifikat-Expiry-Check beim Start
      (regeneriert <30d); Key via `os.open(0o600)` (kein world-readable-Fenster).
      (f) `automation/`: Spike-Login-Check `and`→`or` (wie `worker.py`);
      `test_printer.py` Single-Quote-Escaping; e2e `HOST_PASSWORD` in `main()`
      mit klarem `SystemExit`. Test `test_base_url_keeps_routable_host` →
      `test_base_url_ignores_spoofed_host_header_uses_config_ip` (asserted
      jetzt die neue Security-Eigenschaft). Siehe
      `_logs/2026-07-05_sba_tier2_hardening.md` +
      `wiki/40_experience_logs/lessons_learned.md` („Host-Header nicht für
      URL-Hostnamen vertrauen").
- [x] **Review-Tier-3 (UI-Architektur + Server-Robustheit)** (2026-07-05,
      Commit folgt) — 5 dateibegrenzt parallele Agenten + 1 Polish-Agent
      danach, Suite grün (85):
      (a) `web/scan.html`: großer Inline-`<script>`-Block mechanisch nach
      `web/scan.js` extrahiert (493 Zeilen), `scan.html` auf 234 Zeilen
      (Markup + `<script src="scan.js">`) reduziert. Ladereihenfolge
      (`html5-qrcode.min.js` vor `scan.js`) erhalten, `node --check` grün.
      (b) `web/host.html`: alle 34 inline `onclick=`/`onchange=`/`onkeydown=`
      entfernt → `addEventListener` (direkt für statische Elemente,
      delegiert via `data-action`/`data-student-id`/`data-token`/`data-code`
      für dynamisch gerenderte Zeilen/Buttons). Grep bestätigt: keine
      `on*=`-Attribute mehr im Markup oder in Template-Literal-`innerHTML`.
      (c) `server/sessions.py`: `advance_helper` in zwei klare Schritte
      gesplittet — ruft `end_student` und delegiert dann an neues
      `assign_next_pending_to_helper` (Zuweisung + Broadcast + Hintergrund-
      Task für `load_and_push_helper_student`), analog zur Cleanup-Reihenfolge
      bei `/api/helper/{token}` DELETE. Tier-1-Stale-Task-Guards unangetastet.
      (d) `server/hub.py`: Broadcast-Race behoben — `broadcast_host`,
      `broadcast_queue_size`, `broadcast_settings` und `send_scanner` liefen
      als unabhängige Tasks und konnten dieselbe WebSocket-Verbindung
      gleichzeitig treffen (Interleaving/Reihenfolge-Risiko bei parallelen
      Sends). Neuer `Hub._safe_send()` mit Pro-Verbindung-`asyncio.Lock`
      (in `WeakKeyDictionary`, damit Locks toter Verbindungen nicht leaken).
      `server/sessions.py`: `print_loan_slip_for` bekommt expliziten
      `state.iserv is None`-Guard mit klarer `RuntimeError`-Meldung (statt
      unklarem `AttributeError` auf `None.get_loan_slip_pdf`, wird von den
      Aufrufern ohnehin generisch abgefangen).
      (e) `server/tls.py`: dreifach duplizierte `ipaddress.ip_address`/
      `ValueError`-Blöcke zu `_parsed_ip()`-Helper zusammengeführt;
      `_hostname_ipv4s` vor Verwendung in `_candidate_ipv4s` einsortiert.
      `server/printing.py`: toter `import subprocess` entfernt (nur
      `asyncio.subprocess.PIPE`/`STDOUT` in Gebrauch). `automation/e2e_*.py`
      bereits konsistent aus Tier 2, unverändert gelassen.
      (f) Polish-Pass (nach a+b, gleiche Dateien): `host.html`
      `renderStatusBar()` nutzt jetzt `settingsOpen()` statt eigener
      DOM-Query-Duplikation; kein Dead-Code/`window.*`-Exposure-Rest aus den
      onclick→addEventListener- bzw. Inline-Script-Extraktions-Refactors
      gefunden (bereits sauber). Token-Rotation-Kommentare in `showMbQr()`
      bereits ausreichend (WHY-only, keine Ergänzung nötig).
      Verifiziert: `uv run pytest` 85/85, `node --check` auf `scan.js` +
      extrahiertem `host.html`-Inline-Script grün, Grep bestätigt 0
      verbleibende `on*=`-Attribute in `web/`. Kein Verhaltensunterschied im
      Buchungspfad, `ALLOW_BOOKING`-Gate unangetastet.
- [x] **Buchreihen ausblenden** (2026-07-05, Einstellungen-Dialog) — jedes Buch
      im Reiter „Bücherlisten ordnen" (`host.html`) hat einen 👁/🚫-Button;
      ausgeblendete Reihen (`state.hidden_isbns_by_grade: dict[grade→set[isbn]]`,
      reiner In-Memory-State, kein DB-/IServ-Write) gelten beim Scannen nicht
      mehr als „vorgemerkt" (weder Scanner- noch Schüler-Anzeige) und sind
      damit nicht buchbar. Neue Funktionen `get_hidden_isbns_for_form()`
      (`server/book_order.py`, spiegelt `get_book_order_for_form()`) und
      `apply_hidden_books()` (`server/sessions.py`), gefiltert direkt nach
      jedem `get_student_info`-Aufruf (4 Call-Sites: Modus A/B je Zuweisung +
      Reconnect in `sessions.py`/`routes/ws.py`). Neuer Endpoint
      `POST /api/booklist-hidden` (mirrort `/api/booklist-order`);
      `GET /api/booklist-order` liefert zusätzlich `hidden: [isbn...]`. Tests:
      `tests/test_class_book_order.py` +5, Suite 90 grün. **Live-Effekt bei
      bereits geladenem Schüler bewusst nicht sofort** — analog zur
      bestehenden Bücher-Reihenfolge greift eine Änderung erst beim nächsten
      Laden/Reconnect, nicht rückwirkend auf eine schon offene
      Scanner-Session. **Gotcha:** direkt nach dem Deploy meldete der Nutzer
      „anwählbar, aber nicht speicherbar" — Ursache war kein Code-Bug, sondern
      ein laufender Server-Prozess (`reload=False`, kein systemd), der vor dem
      Code-Edit gestartet war und die neue Route noch nicht kannte, während
      das statische `host.html` sofort die neue UI zeigte. Diagnostiziert via
      `ps -o lstart` vs. `stat -c %y`; Neustart bewusst dem Nutzer überlassen
      (aktive Helfer-/Queue-Sessions wären sonst verloren gegangen). Details:
      `docs/test_status.md`,
      `~/cc/_logs/2026-07-05_sba_hide_book_series_and_reload_gotcha.md`,
      `~/cc/wiki/40_experience_logs/lessons_learned.md`.
- [ ] End-to-End-Test mit ausgemusterten Büchern **inkl. Buchung** (wartet auf Buchungstest-Freigabe Niklas + Lukas)

### Phase 3 — Generalprobe Teil 1 (vor Ferienbeginn, Anfang Juli)

- [x] Deployment-Packaging (→ O7): `setup.bat`/`start.bat`/`start.sh` +
      `docs/deployment.md` (Windows + Macbook, USB-Drucker) — 2026-06-15.
      Lauf am echten Ausleihe-Laptop noch offen (`docs/test_status.md`).
      2026-07-05: `setup.bat` installiert **`uv` jetzt automatisch** (offizieller
      Installer via `powershell -Command "irm https://astral.sh/uv/install.ps1 | iex"`,
      PATH für die laufende Sitzung ergänzt), falls es fehlt — vorher brach das
      Skript mit einer reinen Anleitung ab. `uv sync` lädt bei Bedarf selbst eine
      passende Python-Version; Node.js wird im Projekt nirgends gebraucht (kein
      `package.json`) — einzige externe Abhängigkeit war/ist `uv` selbst.
- [ ] Probelauf im Schul-WLAN mit echtem Drucker
- [ ] Helfer-Kurzanleitung (1 Seite) + dokumentierter Fallback auf USB-Scanner
- [ ] **Meilenstein: Einsatz bei der Stapelerstellung**

### Phase 4 — Modus B: Live-Ausgabe-Pilot (Juli–August)

> Initialer Aufbau erledigt 2026-06-15 (reiner Server-/Web-Code, keine Buchung).
> Details + Sicherheits-Review: `docs/phase4_modus_b_2026-06-15.md`.

- [x] QR-Display-Rolle (iPad): Registrierung, vom Host gesteuerte Anzeige
      (`web/qr-display.html`, allgemeiner anonymer QR) — 2026-06-15
- [x] Einmal-Token-System + Pairing-Flow (langer `session_token` + 4-stelliger
      Code, Host-Bestätigung; Mechanismus geändert, s. Doku) — 2026-06-15
- [x] Host-Pairing-UI ohne Tippen (2026-06-17): wartende Codes werden am Host
      **angezeigt** und per Klick zugeordnet (`web/host.html`, rein Frontend).
      Zwei Wege: *Code-zuerst* (Codes-Liste in der Modus-B-Karte mit
      Schüler-`<select>` + „Zuordnen") und *Schüler-zuerst* (Pairing-Button
      stellt Schüler scharf → Code-Chip klicken). Gemeinsame `doPair()` inkl.
      O6-Override. `prompt()` entfällt. Daten kamen schon aus
      `modus_b_snapshot().pending` + `/api/student/pair` — kein Server-Change.
- [x] Pairing-Latenz-Fix (2026-06-17): `student_info` wird in
      `load_and_push_paired_student` **vor** dem Worker-Open ans Handy gepusht
      (Worker-`load_card` lief vorher davor und blockierte die Anzeige ~7 s).
      Sicher, weil `handle_scan` „Worker nicht bereit" sauber meldet.
- [x] iPad-Display am Host bedienbar (2026-06-17b): Button „QR für iPad anzeigen"
      (`GET /api/display/qr` → QR auf `/qr-display`, host-auth) + Freischalt-Feld
      für den iPad-Registrierungscode (`POST /api/display/authorize`, erscheint
      nur bei verbundenem, unautorisiertem iPad). Bestehender Button → „QR für
      Schüler anzeigen"; Karte „Live-Ausgabe (Modus B)" → „Schüler".
- [x] Join-QR rotierte nach jeder Zuordnung (2026-06-17b). **Pro-Zuordnung-
      Rotation entfernt (2026-06-18):** Das Join-Secret wird jetzt **bei jedem
      Öffnen der Ausgabe** neu erzeugt (`gen_join_secret()` in `/api/modus-b/open`)
      und bleibt **innerhalb** einer Ausgabe konstant — der Schüler-QR ändert sich
      nicht mehr mitten in der Ausgabe. `_rotate_join_secret` ist entfallen.
      Schutz liegt weiter auf `modus_b_open`-Gate + Per-IP-Ratelimit + **manueller
      Host-Zuordnung** (Pairing). Trade-off: ein Screenshot des QR bleibt gültig,
      solange dieselbe Ausgabe offen ist — neue Joins erzeugen aber nur ungepairte
      pending-Sessions (verfallen per TTL). Alte QRs aus einer früheren Ausgabe
      werden mit dem nächsten Öffnen ungültig. „Ausgabe öffnen" zeigt den QR nicht
      automatisch. Auch der QR-Anzeige-Text (`#qr-url`) zeigt die aktuelle Join-URL.
- [x] Queue-Steuerung erweitert (2026-06-17b): pro Schüler „Trennen"
      (`/api/disconnect` → zurück auf „Wartend", trennt Helfer/Session), global
      „Alle Verbindungen … trennen" (`/api/disconnect-all`) und „Queue Status
      zurücksetzen" (`/api/reset-queue`, alle → pending). Beide global mit
      doppelter Bestätigung, dezenter Link-Stil. Alle bauen auf `end_student`.
- [x] Schüler-UI: reduziert und selbsterklärend (`web/student.html`:
      Bestellliste, Scan, Abschluss) — 2026-06-15
- [x] Scan-Vorabprüfung (2026-06-22): Bevor ein gescannter Barcode an den
      Worker gestaged wird, prüft der Server read-only (`GET /books/{code}` →
      ISBN), ob das Buch zur Anmelde-Buchliste des Schülers gehört
      (`check_scanned_book` in `server/sessions.py`). ISBN-Set `expected_isbns`
      wird je Session gehalten — **Modus B** auf `StudentSessionB` (befüllt beim
      Pairing/Reconnect), **Modus A** auf `HelperSession` (befüllt beim Laden des
      Schülers/Reconnect, geleert beim Schülerwechsel). Treffer → wie bisher;
      „not_enrolled"/„unknown_book" → sofortiges `scan_result`, **kein**
      Worker-Kontakt. Leeres Set (Buchliste noch nicht geladen) oder API-Fehler
      blockieren nicht (der offizielle Frontend-Submit validiert ohnehin).
      Reiner Read-Pfad, in Scanner- (`/ws/scanner`) und Schüler-WS
      (`/ws/student`) verdrahtet.
- [x] Harter Zugriffsentzug (Token-Invalidierung + WS-Close + Worker zu) —
      2026-06-15; Skip-Funktion deckt Modus B mit ab
- [x] Sicherheits-Review Token-Lebenszyklus (initial, E2E-verifiziert) —
      2026-06-15; iPad-Härtung (iOS-Kiosk) bleibt organisatorisch
- [ ] Lasttest: 5 parallele Schüler-Sessions
- [x] Rate-Limit `/api/student/join` (pro-IP, 5/10 s, `server/ratelimit.py`) —
      2026-06-15; Logik verifiziert, End-to-End-Drosselung noch im Lasttest zu prüfen.
- [x] Hardening-Pass aus Code-Review (2026-06-18): Worker-Context-Leak (Pool-
      Erschöpfung), WS-Reconnect-Leak, Host-Login-TTL (`HOST_SESSION_TTL_S`),
      QR-IP-Override (`HOST_IP`), Pairing-TOCTOU, `commit-book`-ok-nur-bei-booked
      u. a. Write-Pfad-Gating unangetastet. Details: `docs/hardening_2026-06-18.md`.
- [ ] O6 fachlich mit Hr. Pühn finalisieren
- [ ] Generalprobe vor Schuljahresbeginn
- [ ] **Meilenstein: Pilot mit Testklasse/-jahrgang**

### Begleitend (Seminarfach)

- Entscheidungen und Messergebnisse (Zeitersparnis!) fortlaufend in `docs/`
  festhalten — Spike-Ergebnisse, Architekturentscheidungen, Probelauf-Protokolle
  sind direkt verwertbares Material für die Seminarfacharbeit.
- **`docs/test_status.md`** (lebend) führt Verifiziertes vs. Offenes; neue zu
  testende Dinge dort eintragen.

## 6. Test- und Produktionsschutz

- Die `ausleihe-api` läuft hier **ausnahmslos read-only** (`allow_writes=False`,
  Default). Es gibt keinen Grund, das in diesem Projekt je zu ändern. Buchungen
  laufen **ausschließlich** über den Playwright-Write-Pfad (offizielles Frontend,
  Enter auf der Counter-Seite), **nie** per API-Write.
- Playwright-Tests nur mit Niklas' Account und ausgemusterten Büchern;
  Test-Ausleihen werden unmittelbar zurückgenommen.
- Vor jedem Probelauf: Rückbau-Plan (welche Test-Buchungen müssen rückgängig
  gemacht werden) schriftlich festhalten.

### 6.1 Buchungs-Freigabe (2026-07-02) — Auto-Buchung mit Vorabprüfung

Niklas hat das Klicken auf **Enter** (Buchung gegen die Produktion) freigegeben —
aber **nur**, wenn eine gescannte Buchung **beide** Bedingungen erfüllt. Sind sie
nicht erfüllt, wird der Barcode **gar nicht erst ins Eingabefeld getippt**:

1. **Buch im Lager** — `book.available and not book.distributed and not book.deleted`
   (Lager-Status aus `GET /books/{code}`).
2. **Bestellt & Reihe noch nicht ausgeliehen** — die ISBN gehört zur Anmelde-
   Buchliste des Schülers **und** von der Reihe ist noch kein Exemplar auf ihn
   ausgeliehen (= ISBN im Status „vorgemerkt" der Schülerinfo).

Umsetzung:

- `server/sessions.py::evaluate_scan_for_booking()` — read-only Vorabprüfung.
  **Streng bei Unsicherheit** (kein Client / Buchliste noch nicht geladen /
  Lookup-Fehler → nicht buchen), weil bei Erfolg automatisch Enter folgt.
- `server/sessions.py::process_scan()` — gemeinsame Scan-Verarbeitung für
  Scanner (Modus A) und Schüler (Modus B): Prüfung → bei Erfolg buchen
  (`handle_commit`, Enter) **falls `ALLOW_BOOKING=true`**, sonst nur stagen
  (`handle_scan`, fill ohne Enter). Bedingungen nicht erfüllt → **kein**
  Feldkontakt.
- **`ALLOW_BOOKING` bleibt Master-Gate** (Default `false` = kompletter read-only-
  Betrieb, Scan bleibt staged). Erst auf `true` feuert die Auto-Buchung.
- **Manueller „Buchen"-Button entfernt (2026-07-02):** Der Host-UI-Button
  (`web/host.html`, Kachel- + Queue-Ansicht) plus die `commitBook`-JS-Funktion
  sind raus — er wurde nur bei `allow_booking=true` gerendert, also genau dann,
  wenn die Auto-Buchung ohnehin läuft (redundant). Der Endpoint
  `POST /api/commit-book` (+ `handle_commit`) **bleibt** als dreifach gegateter
  Fallback bestehen, nur ohne UI-Fläche.
- Getrennte ISBN-Mengen pro Session: `vormerk_isbns` (buchbar) / `lent_isbns`
  (für die Meldung „Reihe schon ausgeliehen") in `HelperSession`/`StudentSessionB`.
- Tests: `tests/test_booking_precheck.py` (Bedingungslogik + Gate-Verhalten),
  `tests/test_booking_gate.py` (Enter-Gate unverändert).

⚠️ Die Erfolgs-/Fehler-Selektoren in `worker.commit_barcode()` /
`_read_booking_result()` sind bis zum ersten freigegebenen Realtest **unverifiziert**
(nur ein „booked" aus dem DOM gilt als Erfolg; „unknown" täuscht keine Buchung vor).
Vor Scharfschalten: ausgemustertes Buch + Rückbau-Plan.

**Update (2026-07-05) — Ausgemustert-Prüfung vorgezogen:** `book["deleted"]`
wird jetzt als **erste** Bedingung geprüft, noch vor „bestellt & Reihe nicht
ausgeliehen" — eigener Status `"book_deleted"`, unabhängig davon, ob der
Schüler das Buch überhaupt bestellt hat. Grund: ein ausgemustertes Buch soll
sofort als solches erkennbar sein, statt hinter „nicht bestellt" versteckt zu
werden. Die Bedingung „im Lager" (`not_in_stock`) prüft jetzt nur noch
`distributed`/`available`, `deleted` läuft separat vorher. Sichtbarkeit:
`process_scan()` broadcastet bei `book_deleted` UND `not_in_stock` (bereits
verliehen) einen `{"type": "book_alert", "kind", "student_id", ...}` an alle
Host-WS-Verbindungen (roter Toast + rot markiertes Kästchen der betreffenden
Person unter „Aktuell in Ausgabe" in `web/host.html`, inkl. eigenem
„Schließen"-Button pro Kästchen). Scanner (`web/scan.html`) und Schüler-Client
(`web/student.html`) färben bei `book_deleted` die Statuszeile rot
(`status-book-deleted`) und zeigen ein Hinweis-Modal ohne eigenen
Schließen-Button.

- Scanner (Modus A, Helfer bedient): schließt per Klick außerhalb der Box
  oder automatisch beim nächsten Scan — der Helfer steuert das selbst.
- Schüler-Client (Modus B, Schüler scannt selbst): das Modal ist **blockierend**
  — kein Klick-außerhalb, kein Auto-Close. `StudentSessionB.book_alert_open`
  wird server-seitig gesetzt; jeder weitere Scan wird ignoriert
  (`ws.py`/`ws_student`, vor `process_scan`), bis der Host über
  `POST /api/clear-book-alert` (Button im Now-Serving-Kästchen) freigibt —
  das schickt `{"type": "book_alert_clear"}` an die Schüler-WS und löscht das
  Kästchen bei allen Host-Verbindungen. Überlebt Reconnect (`book_alert_payload`
  wird erneut gesendet).

Tests: `tests/test_booking_precheck.py`
(`test_reject_deleted_before_not_enrolled`,
`test_reject_deleted_before_not_in_stock`).

**Bugfix (2026-07-05) — Scanner reagiert nicht auf Host-Trennung:**
`end_student()` löste die Helfer-Zuordnung serverseitig, informierte aber nie
den Scanner-WebSocket selbst — `web/scan.html` hat keinen Host-State-Feed und
reagiert nur auf gezielt gepushte Nachrichten. Betraf „Trennen" **und** „Alle
Verbindungen trennen". Fix: `end_student()` schickt jetzt zusätzlich
`hub.send_scanner(old_helper, {"type": "waiting", ...})` an den betroffenen
Helfer. **Lesson:** jede neue serverseitige Aktion, die einen Helfer-Zustand
ändert, braucht einen expliziten `send_scanner`-Push — ein
`broadcast_host`-Aufruf allein erreicht den Scanner nicht.
