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
| **B — Live-Ausgabe** (Teil 2, Pilot) | Schuljahresbeginn, Testklasse/-jahrgang ab Jg. 9 | Schüler mit eigenem Gerät | iPad zeigt personalisierten QR → Schüler scannt QR → 4-stelliger Pairing-Code wird am Laptop bestätigt → Schüler sieht bestellte Bücher, scannt sie selbst → Buchung |

**Leitplanken aus der Skizze (nicht verhandelbar):**

- Keine Schreiboperationen auf die Ausleihe-Datenbank durch selbstprogrammierten
  Code. Alle Writes laufen durch das **offizielle IServ-Frontend** (siehe
  Write-Pfad). Die `ausleihe-api` wird ausschließlich **read-only** genutzt.
- Bestehendes System (USB-Handscanner) bleibt jederzeit als Fallback nutzbar.
- Keine dauerhafte Speicherung von Schülerdaten; Website nur im Schul-WLAN,
  zugriffsgeschützt.
- Tests ausschließlich mit Niklas' Account und ausgemusterten Büchern.

## 2. Architektur

```
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
│  │    qr-display.html  iPad: zeigt personalisierte QR-Codes      │
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

1. **Einmal-Tokens:** Der QR enthält ein serverseitig generiertes, kurzlebiges,
   an genau einen Schüler gebundenes Token (kryptographisch zufällig, kein
   erratbares Muster). Server-seitiger Zustand entscheidet über Gültigkeit.
2. **Harter Zugriffsentzug:** Nach erfolgreichem Abschluss des Ausgabe-Prozesses
   (oder Timeout/Abbruch durch Leitstand) wird das Token serverseitig
   invalidiert und die WebSocket-Session beendet. Erneuter Aufruf der URL →
   neutrale „Vorgang abgeschlossen"-Seite, keine Daten.
3. **Doppelte Bestätigung:** Token allein reicht nicht — die Session wird erst
   aktiv, wenn der Leitstand den 4-stelligen Pairing-Code dem Schüler zuordnet
   und bestätigt.
4. **iPad-Absicherung:** Das QR-Display zeigt ausschließlich QR-Codes (+ ggf.
   Vorname/Initialen zur Orientierung — zu entscheiden). Die Display-Session
   ist eine eigene Rolle ohne Datenzugriff; Registrierung nur über den
   Leitstand. iPads zusätzlich im geführten Zugriff (iOS Kiosk-Modus).
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
| O2 | Erlaubt IServ mehrere parallele Sessions desselben Accounts? | Spike B klärt das. Plan B: ein Context, N Tabs mit geteilten Cookies. |
| O3 | Exaktes Verhalten der offiziellen Counter-Seite (DOM, Fehlerfälle, Schüler-Wechsel) | Spike A erkundet das mit Test-Account + ausgemustertem Buch. |
| O4 | Welcher Drucker (USB am Laptop? Netzwerk? Treiberlage unter Windows)? | Vor Ort prüfen; Spike C testet Silent-Print generisch. |
| O5 | Bezahlstatus-Anzeige: genaue Quelle (`enrollments`/`payments` via Admin-API) und Sonderfälle (Befreiung/Ermäßigung) | In Phase 2 gegen echte Daten read-only verifizieren. |
| O6 | Modus B: Was passiert bei „nicht bezahlt"? (Buch zurücklegen, Helfer rufen?) | Mit Hr. Pühn abstimmen; UI braucht definierten Fehlerzustand. |
| O7 | Deployment-Packaging für den Windows-Laptop (Python-Installation? portable venv? `start.bat`?) | Phase 3; Kandidat: `uv` + Lockfile + Start-Skript, alternativ portable Python. |
| O8 | Zeigt das QR-Display Namen/Initialen zur Orientierung oder nur anonyme QRs? | Datenschutz-Abwägung, Entscheidung vor Phase 4. |
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
- [ ] **Spike B:** 2–3 parallele Contexts mit demselben Account (→ O2)
- [ ] **Spike C:** Silent-Print eines PDFs unter Windows (→ O4)
- [ ] **Spike D:** Reichweitentest im Schul-WLAN: Handy ↔ Laptop (→ O9)

### Phase 2 — Kern Modus A (KW 26–28)

- [ ] FastAPI-Server: HTTPS, WebSocket-Hub, Session-/Rollenmodell
- [ ] Leitstand-UI: Login, Klasse wählen, Schülerliste (alphabetische Queue
      + Einzelauswahl für Tests), Live-Status aller Helfer-Sessions
- [ ] Helfer-Scanner-UI: Schüleranzeige (angemeldet / bezahlt / bereits
      vorhanden), Buch-Scan mit Sofort-Feedback (beep + visuell), Fehlerfälle
      (falsches Buch, nicht angemeldet, schon ausgeliehen, nicht bezahlt)
- [ ] Playwright-Worker produktionsreif: Context-Pool, Fehler-Mapping,
      Recovery (Re-Login bei Session-Ablauf)
- [ ] Leihschein-Druck nach Abschluss eines Schülers
- [ ] End-to-End-Test mit ausgemusterten Büchern

### Phase 3 — Generalprobe Teil 1 (vor Ferienbeginn, Anfang Juli)

- [ ] Deployment auf dem Ausleihe-Laptop (→ O7), `start`-Skript
- [ ] Probelauf im Schul-WLAN mit echtem Drucker
- [ ] Helfer-Kurzanleitung (1 Seite) + dokumentierter Fallback auf USB-Scanner
- [ ] **Meilenstein: Einsatz bei der Stapelerstellung**

### Phase 4 — Modus B: Live-Ausgabe-Pilot (Juli–August)

- [ ] QR-Display-Rolle (iPad): Registrierung, vom Leitstand gesteuerte Anzeige
- [ ] Einmal-Token-System + Pairing-Flow (4-stelliger Code, Leitstand-Bestätigung)
- [ ] Schüler-UI: reduziert und selbsterklärend (Bestellliste, Scan, Abschluss)
- [ ] Harter Zugriffsentzug + Skip-Funktion (Sicherheitsmodell §3 vollständig)
- [ ] Sicherheits-Review (Token-Lebenszyklus, Rollen-Grenzen, iPad-Härtung)
- [ ] Lasttest: 5 parallele Schüler-Sessions
- [ ] Generalprobe vor Schuljahresbeginn
- [ ] **Meilenstein: Pilot mit Testklasse/-jahrgang**

### Begleitend (Seminarfach)

- Entscheidungen und Messergebnisse (Zeitersparnis!) fortlaufend in `docs/`
  festhalten — Spike-Ergebnisse, Architekturentscheidungen, Probelauf-Protokolle
  sind direkt verwertbares Material für die Seminarfacharbeit.

## 6. Test- und Produktionsschutz

- Die `ausleihe-api` läuft hier **ausnahmslos read-only** (`allow_writes=False`,
  Default). Es gibt keinen Grund, das in diesem Projekt je zu ändern.
- Playwright-Tests nur mit Niklas' Account und ausgemusterten Büchern;
  Test-Ausleihen werden unmittelbar zurückgenommen.
- Vor jedem Probelauf: Rückbau-Plan (welche Test-Buchungen müssen rückgängig
  gemacht werden) schriftlich festhalten.
