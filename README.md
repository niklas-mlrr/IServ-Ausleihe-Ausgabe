# IServ Ausleihe-Ausgabe

Seminarfachprojekt „Optimierung der Schulbuchausleihe" — eine Web-App, die die
Buchausgabe der Schulbuchausleihe (IServ-Modul) ergänzt, nicht ersetzt.
**Eine App, zwei Modi:**

| Modus | Einsatz | Ablauf |
|-------|---------|--------|
| **A — Stapel** | Sommerferien, Stapelerstellung | Host (Laptop) wählt Klasse, Schüler werden alphabetisch abgearbeitet; Helfer scannen Bücher per Handykamera; Buchung; Leihschein-Druck |
| **B — Live-Ausgabe** (Pilot) | Schuljahresbeginn, Testklasse ab Jg. 9 | iPad zeigt **allgemeinen anonymen** QR → Schüler scannt → Handy zeigt 4-stelligen Code → Host ordnet Code einem Schüler zu und bestätigt → Schüler scannt eigene Bücher |

Der vollständige Projektplan (Architektur, Sicherheitsmodell, Phasen, offene
Punkte) steht in **[`docs/PLAN.md`](docs/PLAN.md)**.

## Kurzüberblick

Dieses Seminarfachprojekt unterstützt die Schulbuchausleihe der Schule. Statt
Bücher und Ausleihvorgänge manuell zu erfassen, verbindet die Anwendung einen
Host-Rechner mit browserbasierten Handyscannern und dem offiziellen
IServ-Frontend.

**Zwei Einsatzmodi:**

- **Stapelmodus:** Helfer scannen Bücher per Handykamera; die Anwendung führt
  durch die Ausgabe, bucht über das IServ-Frontend und druckt Leihscheine.
- **Live-Ausgabe-Pilot:** Schülerinnen und Schüler verbinden sich selbst über
  einen QR-Code; der Host ordnet die Sitzung zu und gibt die Ausgabe frei.

**Technik:** Python, FastAPI, WebSockets, Playwright, browserbasierter
Barcode-Scanner und lokaler Leihschein-Druck.

**Sicherheitsprinzip:** Die Anwendung liest Ausleihdaten über die separate
[ausleihe-api](https://github.com/niklas-mlrr/ausleihe-api) und
führt Buchungen ausschließlich über das offizielle IServ-Frontend aus. Der
bestehende USB-Scanner bleibt jederzeit als Fallback nutzbar.

**Projektstand (August 2026):** Der Stapelmodus ist funktional fertig.
Der Live-Ausgabe-Pilot ist implementiert; die Generalprobe im Schul-WLAN und
der Buchungstest gegen das echte System stehen noch aus. Die Logik ist mit
394 automatisierten Tests abgesichert.

## Architektur (Kurzfassung)

```
Helfer-/Schüler-Handy (Kamera-Scanner)        iPad (QR-Anzeige)
        │ HTTPS + WebSocket                        │
        ▼                                          ▼
  Python-Server (FastAPI) — Windows-Laptop, Schul-WLAN, Port 3443
  ├─ web/        statische UI (Host, Scanner, QR-Display)
  ├─ ausleihe-api  read-only: Klassen, Schüler, Anmeldungen,
  │                Bezahlstatus, Leihschein-PDF
  ├─ automation/ Playwright: Buchungen durch das OFFIZIELLE
  │              IServ-Frontend (ein Browser-Context pro Schüler)
  └─ Druck       Leihschein-PDF → Silent-Print (Windows)
```

Leitplanken: **keine selbstprogrammierten Schreibzugriffe auf die
Ausleihe-Datenbank** — alle Buchungen laufen durch das offizielle IServ-Frontend
(Playwright) inklusive dessen Validierung. Die
[ausleihe-api](https://github.com/niklas-mlrr/ausleihe-api) wird
ausschließlich lesend genutzt. Das bestehende System (USB-Handscanner) bleibt
jederzeit als Fallback nutzbar.

## Für Nachfolger — hier beginnen

Wenn du das Projekt **ohne die ursprünglichen Entwickler (Niklas & Lukas)
weiterbetreiben** willst, liegt die komplette Schritt-für-Schritt-Anleitung unter
**[`docs/nachfolge-anleitung.md`](docs/nachfolge-anleitung.md)** (als PDF-Handout
ebenfalls im Schulbuchausleihe-Team). Sie erklärt Ersteinrichtung, Normalbetrieb
(Modus A), das jährliche Bestand-Excel-Skript und typische Fehler — ohne
Technikvorkenntnisse.

**Kurzform:**
- Einmalig: `setup.bat` ausführen, `.env` ausfüllen (IServ-Zugangsdaten +
  `HOST_PASSWORD`), USB-Drucker als Standarddrucker setzen.
- Jeder Einsatz: `start.bat` doppelklicken, Host unter
  `https://localhost:3443/host` öffnen, Helfer per QR verbinden.
- Fällt die App einmal (z. B. nach einer IServ-Aktualisierung), arbeitet die
  Schulbuchausleihe weiter wie vorher mit dem USB-Handscanner am offiziellen
  IServ-Frontend — das ist kein Scheitern, sondern der dauerhafte Notnagel.

Modus B (Live-Ausgabe-Pilot) ist **nicht** Teil der Nachfolge-Dokumentation; er
benötigt technische Betreuung, die nach dem Abgang der Entwickler nicht mehr
sichergestellt ist.

## Setup

Voraussetzungen: [uv](https://docs.astral.sh/uv/) (bringt Python ≥ 3.12 bei
Bedarf selbst mit) und das Schwesterprojekt `ausleihe-api` als Checkout
**direkt neben** diesem Repo unter `../ausleihe-api`.

> **Verzeichnis-Layout (zwingend):** `uv sync` installiert `ausleihe-api` als
> editable Dependency aus `../ausleihe-api`. Beide Repos müssen nebeneinander
> liegen:
> ```
> projects/
> ├── ausleihe-ausgabe/   (dieses Repo)
> └── ausleihe-api/       (Schwester-Repo)
> ```

### macOS

```bash
# 1) uv installieren (falls noch nicht vorhanden), danach neues Terminal öffnen
curl -LsSf https://astral.sh/uv/install.sh | sh        # oder: brew install uv

# 2) Schwester-Repo daneben klonen
cd <ordner-über-ausleihe-ausgabe>
git clone https://github.com/niklas-mlrr/ausleihe-api.git ausleihe-api

# 3) Projekt einrichten
cd ausleihe-ausgabe
uv sync                              # Umgebung + Dependencies (zieht Python ≥3.12)
uv run playwright install chromium   # Browser für den Write-Pfad
cp .env.example .env                 # dann ISERV_* + HOST_PASSWORD eintragen (nano .env)

# 4) Starten (Vordergrund, Strg+C beendet)
./start.sh
```

Druck auf macOS: `PRINT_BACKEND=auto` nutzt automatisch `lp`/CUPS (vorinstalliert);
`PRINTER_NAME` leer = Standarddrucker.

### Windows (Ausleihe-Laptop)
```powershell
# 1) uv installieren (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

```
-> Neues Terminal öffnen

```powershell
# 2) Dieses GitHub Repo klonen
git clone https://github.com/niklas-mlrr/ausleihe-ausgabe.git ausleihe-ausgabe

# 3) API-Repo daneben klonen
git clone https://github.com/niklas-mlrr/ausleihe-api.git ausleihe-api

# 4) In den geklonten Haupt-Ordner navigieren
cd ausleihe-ausgabe

# 5) Setup Skript ausführen - Erledigt `uv sync`, Playwright-Chromium und legt `.env` aus der Vorlage an.
setup.bat

```

Dann im Ordner `ausleihe-ausgabe`:
1. **`.env`** öffnen und `ISERV_*` + `HOST_PASSWORD` eintragen.
2. **`start.bat`** doppelklicken — startet den Server (Beenden mit Strg+C).

Silent-Print (Leihschein): USB-Drucker als Standarddrucker setzen, ggf.
SumatraPDF installieren. Details inkl. Drucker-Setup in
**[`docs/deployment.md`](docs/deployment.md)**.

### Aufrufen

Nach dem Start ist die Host-UI erreichbar unter **`https://localhost:3443/host`**
(im Schul-WLAN auch über die LAN-IP des Laptops, z. B. `https://192.168.x.y:3443/host`).
Immer **https** — das selbstsignierte Zertifikat einmal bestätigen
(„Erweitert → trotzdem fortfahren"). `ALLOW_BOOKING` im Normalbetrieb auf `false` lassen.

Unit-Tests (reine Logik, kein IServ/Playwright):

```bash
uv run pytest
```

Smoke-Test (read-only):

```bash
uv run python -c "
from dotenv import load_dotenv; load_dotenv()
from ausleihe import AusleiheClient
print(AusleiheClient().get('/schoolyears/current')['id'])"
```

## Projektstruktur

```
├── server/        FastAPI-App: HTTPS, WebSocket-Hub, Rollen/Sessions, Druck
│   ├── sessions.py          Session-/Queue-Logik (Modus A + B)
│   ├── iserv_client.py      Read-only IServ-Wrapper
│   ├── book_order.py        Bücher-Reihenfolge pro Schüler-Jahrgang
│   ├── printing.py          Leihschein-Druck (file/lp/sumatra, plattformabhängig)
│   ├── tls.py               Selbstsigniertes HTTPS-Zertifikat
│   └── routes/              API- + WebSocket-Endpunkte
├── automation/    Playwright-Worker + Spikes + E2E-Skripte (Ausgaben: automation/out/, gitignored)
├── web/           statische UI ohne Build-Step
│   ├── host.html            Host-UI (Laptop): Klasse/Queue/Helfer/Modus B
│   ├── scan.html / scan.js  Helfer-Scanner (Modus A, html5-qrcode, Beep, Torch)
│   ├── student.html         Schüler-UI (Modus B)
│   ├── qr-display.html      iPad-QR-Anzeige (Modus B)
│   ├── html5-qrcode.min.js  vendored
│   └── beep.mp3
├── docs/
│   ├── PLAN.md          Projektplan — Arbeitsgrundlage
│   ├── deployment.md    Windows-/Macbook-Setup + USB-Drucker
│   ├── test_status.md   Test-/Verifizierungsstand (lebend)
│   └── spikes/          Spike-Protokolle
├── tests/         pytest-Unit-Tests (reine Logik, kein IServ/Playwright)
├── setup.bat / start.bat / start.sh
└── pyproject.toml
```

## Sicherheits- und Produktionsregeln

- `ausleihe-api` läuft hier **ausnahmslos read-only** (`allow_writes=False`);
  niemals PUT/POST/DELETE gegen die Produktion.
- Schreiboperationen nur via Playwright durch das offizielle IServ-Frontend.
- Tests nur mit Niklas' Account und **ausgemusterten Büchern**; Test-Ausleihen
  werden sofort zurückgenommen (Rückbau-Plan vor jedem Probelauf, PLAN §6).
- Keine dauerhafte Speicherung von Schülerdaten; Logs ohne personenbezogene
  Daten. Server nur im Schul-WLAN erreichbar, Zugriff rollenbasiert
  (Details: PLAN §3).
- Credentials nur in `.env` (gitignored), niemals committen.

## Status

Modus A (Stapelerstellung) ist funktional fertig: Host-UI, Helfer-Scanner,
Playwright-Worker-Pool, Leihschein-Druck, Auto-Buchung mit Vorabprüfung
(gegated per `ALLOW_BOOKING`, Default `false` = read-only). Modus B
(Live-Ausgabe-Pilot) ist initial gebaut (Pairing-Flow, QR-Anzeige, E2E grün).
Noch offen: echter Buchungstest gegen Produktion (wartet auf Freigabe),
Generalprobe im Schul-WLAN. Details/Phasenstand: [`docs/PLAN.md`](docs/PLAN.md)
§5, laufender Verifizierungsstand: [`docs/test_status.md`](docs/test_status.md).

Historie: Das Repo ist ein entkoppelter Fork des
[Barcode-Scanners](https://github.com/niklas-mlrr/barcode-scanner-simple); der alte
Node-Server/Keyboard-Client liegt in der Git-Historie (bis `0bd06bc`).
