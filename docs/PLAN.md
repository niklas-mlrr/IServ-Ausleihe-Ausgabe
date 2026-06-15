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
| **B — Live-Ausgabe** (Teil 2, Pilot) | Schuljahresbeginn, Testklasse/-jahrgang ab Jg. 9 | Schüler mit eigenem Gerät | iPad zeigt allgemeinen QR → Schüler scannt → Handy zeigt 4-stelligen Code → Leitstand ordnet Code einem Schüler zu und bestätigt → Schüler sieht bestellte Bücher, scannt sie selbst → Buchung |

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
│  │    leitstand.html   Laptop: Klasse/Schüler wählen, Pairing,   │
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
| Leitstand | Laptop | Passwort-Login → Session-Cookie | alles: Klasse/Schüler wählen, Pairing bestätigen, Skip, Abbruch, Druck |
| Helfer-Scanner (Modus A) | Helfer-Handy | Join-Code vom Leitstand (oder Passwort) | zugewiesenen Schüler sehen, Bücher scannen |
| QR-Anzeige (Modus B) | iPad | Registrierung am Leitstand per Code; danach **nur** QR-Anzeige, keine Schülerdaten im Klartext | QR-Codes anzeigen |
| Schüler-Session (Modus B) | Schüler-Handy | personalisierter QR mit **Einmal-Token** + 4-stelliger Pairing-Code, vom Leitstand bestätigt | nur eigene Bestelldaten sehen, nur eigene Bücher scannen |

Sicherheitsanforderungen (aus Klärung 2026-06-12, „keine Sicherheitslücken"):

1. **Einmal-Tokens:** _(Mechanismus geändert 2026-06-15, siehe
   `docs/phase4_modus_b_2026-06-15.md`.)_ Das iPad zeigt einen **allgemeinen,
   anonymen** QR. Beim Scan mintet der Server pro Browser-Session einen langen,
   kryptographisch zufälligen `session_token` (~256 bit) — den **einzigen**
   Daten-Zugang — und einen 4-stelligen `pairing_code`. Der Code dient nur der
   menschlich vermittelten Zuordnung am Leitstand und gewährt **nie** selbst
   Datenzugriff. Server-seitiger Zustand entscheidet über Gültigkeit.
2. **Harter Zugriffsentzug:** Nach erfolgreichem Abschluss des Ausgabe-Prozesses
   (oder Timeout/Abbruch durch Leitstand) wird das Token serverseitig
   invalidiert und die WebSocket-Session beendet. Erneuter Aufruf der URL →
   neutrale „Vorgang abgeschlossen"-Seite, keine Daten.
3. **Doppelte Bestätigung:** Token allein reicht nicht — die Session wird erst
   aktiv, wenn der Leitstand den 4-stelligen Pairing-Code dem Schüler zuordnet
   und bestätigt.
4. **iPad-Absicherung:** Das QR-Display zeigt **ausschließlich** den allgemeinen
   QR-Code — **keine** Namen/Initialen (O8 geklärt: anonym, 2026-06-15). Die
   Display-Session ist eine eigene Rolle ohne Datenzugriff; Registrierung nur
   über den Leitstand. iPads zusätzlich im geführten Zugriff (iOS Kiosk-Modus).
5. **Skip-Funktion:** Leitstand kann Schüler überspringen (krank/abwesend);
   deren Tokens werden nie ausgegeben bzw. sofort invalidiert.
6. **Transport:** HTTPS mit selbstsigniertem Zertifikat (Logik aus dem Fork
   nach Python portieren), nur im Schul-WLAN erreichbar.
7. **Keine Persistenz:** Schülerdaten nur im RAM der Session; Logs ohne
   personenbezogene Daten (Buch-Codes ja, Namen nein).

## 4. Offene Punkte

| # | Frage | Vorschlag / nächster Schritt |
|---|-------|------------------------------|
| O1 | Modus A: Wie kommt ein Schüler auf ein bestimmtes Helfer-Handy? | Vorschlag: Helfer tippt „Nächster Schüler" → Server vergibt den nächsten aus der alphabetischen Queue; Leitstand kann manuell zuweisen/überschreiben. |
| O2 | Erlaubt IServ mehrere parallele Sessions desselben Accounts? | **Geklärt (Spike B, 2026-06-12):** Ja — 3/3 parallele unabhängige Logins + 3/3 Cookie-Sharing-Contexts, keine Invalidierung. Context-Pool mit unabhängigen Contexts. |
| O3 | Exaktes Verhalten der offiziellen Counter-Seite (DOM, Fehlerfälle, Schüler-Wechsel) | Spike A erkundet das mit Test-Account + ausgemustertem Buch. |
| O4 | Welcher Drucker (USB am Laptop? Netzwerk? Treiberlage unter Windows)? | **Teil-adressiert (2026-06-15):** Druck-Service gebaut (`server/printing.py`, Backends `file`/`lp`/`sumatra`/`win-default`/`auto`), read-only PDF-Abruf via `get_loan_slip_pdf`. Echter Silent-Print am Zielgerät (= Spike C) noch offen → `docs/test_status.md`, `docs/deployment.md`. |
| O5 | Bezahlstatus-Anzeige: genaue Quelle (`enrollments`/`payments` via Admin-API) und Sonderfälle (Befreiung/Ermäßigung) | In Phase 2 gegen echte Daten read-only verifizieren. |
| O6 | Modus B: Was passiert bei „nicht bezahlt"? (Buch zurücklegen, Helfer rufen?) | **Teil-geklärt (2026-06-15):** UI zeigt Bücher + „nicht bezahlt"-Banner; Leitstand kann beim Pairing per `override_payment` freigeben (Befreiung/Ermäßigung). Fachlicher Wortlaut/Workflow noch mit Hr. Pühn final. |
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
- [x] Leitstand-UI: Login, Klasse wählen, alphabetische Queue, Live-Status Helfer-Sessions — 2026-06-12
- [x] Helfer-Scanner-UI: Token-basiert, Schüleranzeige (angemeldet/bezahlt/Bücher), Scan-Feedback — 2026-06-12
- [x] Playwright-Worker: Context-Pool (N unabhängige Logins), Schülerkartei laden, Barcode staged (kein Submit) — 2026-06-12
- [x] Recovery (Re-Login bei Session-Ablauf) — 2026-06-15 (`automation/worker.py`, deterministisch getestet via `automation/recovery_test.py`)
- [x] E2E-Smoke headless (read-only): voller Modus-A-Flow Leitstand→Scanner→Worker→Kartei→staged — 2026-06-15 (`automation/e2e_smoke.py`)
- [x] 2-Helfer-Paralleltest: zwei Schüler gleichzeitig aktiv, beide Karteien parallel, unabhängiges Staging — 2026-06-15 (`automation/e2e_parallel.py`)
- [x] Pool-Härtung: fehlgeschlagene Worker-Logins werden in `start()` einmal nachgezogen, geleakte Contexts geschlossen — 2026-06-15
- [ ] Fehlerfälle Scanner: falsches Buch, nicht angemeldet, schon ausgeliehen (braucht freigegebenen Buchungstest)
- [x] Leihschein-Druck — Code fertig: read-only PDF-Abruf + Druck-Abstraktion
      (`server/printing.py`, Endpoint `POST /api/print-loan-slip`, Leitstand-Button) —
      2026-06-15. Echter Druck am Zielgerät noch zu verifizieren (`docs/test_status.md`).
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

- [x] QR-Display-Rolle (iPad): Registrierung, vom Leitstand gesteuerte Anzeige
      (`web/qr-display.html`, allgemeiner anonymer QR) — 2026-06-15
- [x] Einmal-Token-System + Pairing-Flow (langer `session_token` + 4-stelliger
      Code, Leitstand-Bestätigung; Mechanismus geändert, s. Doku) — 2026-06-15
- [x] Schüler-UI: reduziert und selbsterklärend (`web/student.html`:
      Bestellliste, Scan, Abschluss) — 2026-06-15
- [x] Harter Zugriffsentzug (Token-Invalidierung + WS-Close + Worker zu) —
      2026-06-15; Skip-Funktion deckt Modus B mit ab
- [x] Sicherheits-Review Token-Lebenszyklus (initial, E2E-verifiziert) —
      2026-06-15; iPad-Härtung (iOS-Kiosk) bleibt organisatorisch
- [ ] Lasttest: 5 parallele Schüler-Sessions
- [ ] Rate-Limit `/api/student/join` vor dem Piloten (DoS)
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
  Default). Es gibt keinen Grund, das in diesem Projekt je zu ändern.
- Playwright-Tests nur mit Niklas' Account und ausgemusterten Büchern;
  Test-Ausleihen werden unmittelbar zurückgenommen.
- Vor jedem Probelauf: Rückbau-Plan (welche Test-Buchungen müssen rückgängig
  gemacht werden) schriftlich festhalten.
