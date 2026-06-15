# IServ Ausleihe-Ausgabe

Seminarfachprojekt „Optimierung der Schulbuchausleihe" — eine Web-App, die die
Buchausgabe der Schulbuchausleihe (IServ-Modul) ergänzt, nicht ersetzt.
**Eine App, zwei Modi:**

| Modus | Einsatz | Ablauf |
|-------|---------|--------|
| **A — Stapel** | Sommerferien, Stapelerstellung | Leitstand (Laptop) wählt Klasse, Schüler werden alphabetisch abgearbeitet; Helfer scannen Bücher per Handykamera; Buchung; Leihschein-Druck |
| **B — Live-Ausgabe** (Pilot) | Schuljahresbeginn, Testklasse ab Jg. 9 | iPad zeigt **allgemeinen anonymen** QR → Schüler scannt → Handy zeigt 4-stelligen Code → Leitstand ordnet Code einem Schüler zu und bestätigt → Schüler scannt eigene Bücher |

Der vollständige Projektplan (Architektur, Sicherheitsmodell, Phasen, offene
Punkte) steht in **[`docs/PLAN.md`](docs/PLAN.md)**.

## Architektur (Kurzfassung)

```
Helfer-/Schüler-Handy (Kamera-Scanner)        iPad (QR-Anzeige)
        │ HTTPS + WebSocket                        │
        ▼                                          ▼
  Python-Server (FastAPI) — Windows-Laptop, Schul-WLAN, Port 3443
  ├─ web/        statische UI (Leitstand, Scanner, QR-Display)
  ├─ ausleihe-api  read-only: Klassen, Schüler, Anmeldungen,
  │                Bezahlstatus, Leihschein-PDF
  ├─ automation/ Playwright: Buchungen durch das OFFIZIELLE
  │              IServ-Frontend (ein Browser-Context pro Schüler)
  └─ Druck       Leihschein-PDF → Silent-Print (Windows)
```

Leitplanken: **keine selbstprogrammierten Schreibzugriffe auf die
Ausleihe-Datenbank** — alle Buchungen laufen durch das offizielle IServ-Frontend
(Playwright) inklusive dessen Validierung. Die
[ausleihe-api](https://github.com/niklas-mlrr/IServ-Ausleihe-API) wird
ausschließlich lesend genutzt. Das bestehende System (USB-Handscanner) bleibt
jederzeit als Fallback nutzbar.

## Setup

Voraussetzungen: Python ≥ 3.12, [uv](https://docs.astral.sh/uv/), das
Schwesterprojekt `ausleihe-api` als Checkout unter `../ausleihe-api`.

```bash
uv sync                              # Umgebung + Dependencies
uv run playwright install chromium   # Browser für den Write-Pfad
cp .env.example .env                 # dann ISERV_* + LEITSTAND_PASSWORD eintragen
```

Auf dem Ausleihe-Laptop (Windows) bzw. einem Macbook übernehmen das `setup.bat`
(einmalig) und `start.bat`/`start.sh` (Start); Details inkl. USB-Drucker-Setup
in **[`docs/deployment.md`](docs/deployment.md)**.

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
│   └── printing.py          Leihschein-Druck (file/lp/sumatra, plattformabhängig)
├── automation/    Playwright-Worker + Spikes (Ausgaben: automation/out/, gitignored)
├── web/           statische UI ohne Build-Step
│   ├── scan.html            Kamera-Scanner (html5-qrcode, Beep, Torch)
│   ├── html5-qrcode.min.js  vendored
│   └── beep.mp3
├── docs/
│   ├── PLAN.md          Projektplan — Arbeitsgrundlage
│   ├── deployment.md    Windows-/Macbook-Setup + USB-Drucker
│   ├── test_status.md   Test-/Verifizierungsstand (lebend)
│   └── spikes/          Spike-Protokolle
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

Phase 0 (Projekt-Setup) — siehe Phasenplan in [`docs/PLAN.md`](docs/PLAN.md).
Historie: Das Repo ist ein entkoppelter Fork des
[Barcode-Scanners](https://github.com/niklas-mlrr/Barcode-Scanner); der alte
Node-Server/Keyboard-Client liegt in der Git-Historie (bis `0bd06bc`).
