# Projektplan: IServ Ausleihe-Ausgabe

> Initialisierungsplan, Stand 2026-06-12. Basiert auf der Projektskizze
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

### Phase 0 — Projekt-Setup (KW 24/25, jetzt)

- [ ] Repo umstrukturieren: Alt-Code raus, Python-Projektgerüst
      (`server/`, `web/`, `automation/`, `docs/`, `pyproject.toml`)
- [ ] Scanner-Assets übernehmen (`html5-qrcode.min.js`, `beep.mp3`,
      Scan-Logik aus `scanner.html`)
- [ ] `.env`-Handling + `CLAUDE.md` mit Read-only-/Produktions-Schutzregeln
      (analog `ausleihe-api`)
- [ ] Dieses Plandokument committen; README neu schreiben

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
- [x] Erledigt-Gruppe nach **Ausgabedatum** sortiert (jüngstes oben) — 2026-07-02d:
      In der Erledigt-Gruppe ersetzt `distributed_at` (desc) die Klassen-Reihenfolge;
      gerade gescannte Bücher ohne Datum stehen oben. `web/scan.html` + `web/student.html`.
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
- [ ] End-to-End-Test mit ausgemusterten Büchern **inkl. Buchung** (wartet auf Buchungstest-Freigabe Niklas + Lukas)

### Phase 3 — Generalprobe Teil 1 (vor Ferienbeginn, Anfang Juli)

- [x] Deployment-Packaging (→ O7): `setup.bat`/`start.bat`/`start.sh` +
      `docs/deployment.md` (Windows + Macbook, USB-Drucker) — 2026-06-15.
      Lauf am echten Ausleihe-Laptop noch offen (`docs/test_status.md`).
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
