# Deployment & Druck — Ausleihe-Laptop (Windows) / Macbook

> Bezug: `docs/PLAN.md` §5 Phase 3 (Generalprobe), O4 (Drucker), O7 (Packaging).
> Betrifft nur lokalen Betrieb am Ausgabe-Gerät — **keine** Produktions-Writes.

## 1. Voraussetzungen

- **[uv](https://docs.astral.sh/uv/)** (Python-Toolchain; installiert Python ≥3.12 selbst).
- Das Schwesterprojekt **`ausleihe-api`** als Checkout unter `../ausleihe-api`
  (editable Dependency, siehe `pyproject.toml`).
- Netz: Gerät im **Schul-WLAN**; Handys/iPad erreichen es unter `https://<IP>:3443`.

## 2. Erstinstallation

### Windows (Ausleihe-Laptop)

```bat
setup.bat
```

Führt aus: `uv sync` → `uv run playwright install chromium` → legt `.env` aus der
Vorlage an. Danach `.env` öffnen und `ISERV_*` + `HOST_PASSWORD` eintragen.

### macOS / Linux

```bash
uv sync
uv run playwright install chromium
cp .env.example .env      # dann ISERV_* + HOST_PASSWORD eintragen
```

## 3. Starten

| Plattform | Befehl | Hinweis |
|-----------|--------|---------|
| Windows   | `start.bat` | Doppelklick genügt |
| macOS/Linux | `./start.sh` | |
| manuell   | `uv run python -m server.main` | alle Plattformen |

Beim ersten Start erzeugt `server/tls.py` ein selbstsigniertes Zertifikat
(`certs/`). Host: `https://localhost:3443/host` (Clean URL ohne `.html`;
die `.html`-Pfade bleiben zusätzlich gültig). Auf den Handys das
Zertifikat einmal als Ausnahme bestätigen (selbstsigniert, nur Schul-WLAN).

Konfiguration via `.env`: `PORT` (Default 3443), `WORKER_CONTEXTS` (parallele
Playwright-Contexts), Druck-Variablen (s. u.).

### Playwright-Debug (optional, nur Geräte mit Display)

| `.env` | Wirkung |
|--------|---------|
| `HEADLESS=false` | Browserfenster sichtbar machen (eins pro `WORKER_CONTEXTS`); Default `true` = unsichtbar |
| `SLOW_MO_MS=300` | Playwright wartet X ms vor jeder Aktion (Klick/Tippen/Navigieren); nur sinnvoll mit `HEADLESS=false`; Default `0` |

Auf einem headless-Server (kein Display) bräuchte `HEADLESS=false` ein
virtuelles Display (`xvfb-run …`) und ist nur für Screenshots/Traces nützlich.

## 4. Leihschein-Druck (USB-Drucker)

Der Server holt den Leihschein **read-only** über die ausleihe-api
(`get_loan_slip_pdf`, reiner GET) und druckt ihn **lokal** über `server/printing.py`.
Gesteuert über `PRINT_BACKEND` in `.env`:

| `PRINT_BACKEND` | Verhalten | Plattform |
|-----------------|-----------|-----------|
| `auto` (Default) | Windows→`sumatra`, macOS→`lp`, sonst→`file` | alle |
| `file` | PDF nur nach `automation/out/loan_slips/` speichern, **nicht** drucken | alle (dev-sicher) |
| `lp` | CUPS `lp` an Standard- oder `PRINTER_NAME`-Drucker | macOS / Linux |
| `sumatra` | SumatraPDF `-print-to[-default] -silent` | Windows |
| `win-default` | `os.startfile(pdf, "print")` (verknüpftes PDF-Programm) | Windows |

Weitere `.env`-Variablen: `PRINTER_NAME` (leer = Standarddrucker),
`SUMATRA_PATH` (nur falls SumatraPDF nicht im PATH/Standardpfad liegt).

### Windows-Drucker einrichten

1. Alten USB-Drucker anschließen, Treiber installieren, **als Standarddrucker** setzen.
2. **[SumatraPDF](https://www.sumatrapdfreader.org/)** installieren (klein, kostenlos)
   — ermöglicht echten Silent-Print ohne Dialog. Ohne Sumatra fällt der Service
   automatisch auf `win-default` zurück (öffnet kurz das PDF-Programm).
3. Testdruck über den Host-Button **„Leihschein"** an einem Schüler.

### macOS-Drucker (Macbook)

1. USB-Drucker in *Systemeinstellungen → Drucker & Scanner* hinzufügen.
2. `PRINT_BACKEND=auto` (oder `lp`) genügt; `lp` ist Teil von macOS (CUPS).
   Optional Druckername via `lpstat -p` ermitteln und als `PRINTER_NAME` setzen.

## 5. Fallback

Das bestehende System (USB-Handscanner am offiziellen IServ-Frontend) bleibt
jederzeit nutzbar. Bei Server-Problemen einfach dort weiterarbeiten — die App
ergänzt, ersetzt nicht (PLAN §1).

## 6. Bekannte offene Punkte (vor der Generalprobe)

- Echter Silent-Print am Zielgerät noch nicht verifiziert → `docs/test_status.md`
  (Spike C / O4).
- Schul-WLAN-Client-Isolation (O9, Spike D) vor Ort prüfen.

## 7. Feld-Gotcha: macOS lädt IServ am Hotspot nicht

Symptom (2026-06-17): Am Macbook lud IServ **nur am iPhone-Hotspot** nicht
(auch im normalen Browser), am WLAN problemlos; anderes Gerät am selben Hotspot
lud normal → gerätespezifisch, **kein** IP-/Account-Block. Ursache war **iCloud
Private Relay / „Limit IP Address Tracking"** für dieses Netz. Fix: in
*Wi-Fi → (i) am Netz* „Limit IP Address Tracking" aus (bzw. Apple-ID → iCloud →
Private Relay aus), dann DNS flushen:

```bash
sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder
```

Schnell-Diagnose, falls es wiederkommt: `curl -4` vs `curl -6` gegen
`https://iserv-trg-oha.de/iserv/login` (IPv6-only-Pfad?), `dig`, und im
sichtbaren Browser auf Captive-Portal prüfen.
