# Deployment & Druck — Ausleihe-Laptop (Windows) / Macbook

> Bezug: `docs/PLAN.md` §5 Phase 3 (Generalprobe), O4 (Drucker), O7 (Packaging).
> Betrifft nur lokalen Betrieb am Ausgabe-Gerät — **keine** Produktions-Writes.

## 1. Voraussetzungen

- **[uv](https://docs.astral.sh/uv/)** (Python-Toolchain; installiert Python ≥3.12 selbst,
  kein separates Python/Node.js nötig). `setup.bat` installiert `uv` unter Windows
  automatisch, falls es fehlt (macOS/Linux: siehe uv-Doku).
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
3. Soll ein **bestimmter** Drucker (nicht der Standarddrucker) verwendet werden:
   exakten Namen per PowerShell ermitteln — `Get-Printer | Select-Object Name` —
   und den Wert als `PRINTER_NAME` in die `.env` eintragen (leer = Standarddrucker).
4. Testdruck über den Host-Button **„Leihschein"** an einem Schüler.

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

## 8. Gotcha: QR-Code zeigt `localhost` statt LAN-IP (2026-06-17)

Symptom: Öffnet der Host die Seite lokal über `https://localhost:3443/host`
(Server läuft auf demselben Laptop), enthielten die generierten QR-Codes
(Helfer-Pairing `/scan?token=…`, Modus-B-Join `/student?j=…`) die Adresse
`https://localhost:3443/…` — für Schüler-/Helfer-Geräte unbrauchbar.

Ursache: `_base_url()` (`server/routes/api.py`) baute die URL aus dem
`Host`-HTTP-Header; bei lokalem Aufruf ist der eben `localhost`.

Fix: `_base_url()` erkennt `localhost`/`127.0.0.1`/`::1` und ersetzt den
Hostnamen durch die primäre LAN-IP des Host-Rechners (`_local_ipv4s()[0]`
aus `server/tls.py` — dieselbe Quelle wie die Cert-SANs); der Port aus dem
Header bleibt, sonst Fallback `cfg.port`. Ruft der Host bereits über die IP
auf, bleibt alles unverändert.

Rest-Gotcha (überholt durch §9): Bei mehreren Interfaces konnte die ausgehende
Primär-IP die „falsche" Schnittstelle treffen → siehe IP-Ranking + Toggle unten.

## 9. QR-IP-Auswahl: Ranking, Tailscale-Erkennung, Header-Toggle (2026-06-22)

Problem: Auf dem VPS (Remote-Test) zeigten die QR-Codes die **öffentliche** IP
statt der über Tailscale erreichbaren `100.x` — die alte Logik schloss CGNAT
(`100.64.0.0/10`) ganz aus und fiel auf `candidates[0]` (öffentliche
Default-Route-IP) zurück. Die Tailscale-IP wurde zudem gar nicht erst gefunden:
Default-Route-Probe + `gethostname()` liefern sie nicht.

Lösung (`server/tls.py`):
- **Tailscale-Erkennung:** UDP-Connect auf die MagicDNS-IP `100.100.100.100`
  liefert die Tailscale-Quell-IP — portabel (auch Windows), ohne Zusatz-Dep.
  (Reiner Routen-Probe, kein echter Traffic; analog zum LAN-Default-Probe auf
  `10.255.255.255`.)
- **3-Stufen-Ranking** (`_ip_rank`): RFC1918-LAN (0) > CGNAT/Tailscale (1) >
  public/sonstige (2). Auto wählt damit auf dem Schullaptop die LAN-IP, auf dem
  VPS die Tailscale-IP. Stabil innerhalb einer Stufe (Erkennungsreihenfolge).
- `primary_lan_ip(force_tailscale=False)`: `force_tailscale=True` erzwingt die
  Tailscale/CGNAT-IP (Fallback auf Auto-Ranking, wenn keine vorhanden).

**Header-Toggle „Tailscale-IP"** (Host-UI, `web/host.html`):
- Auto (aus) ⇄ Tailscale erzwingen (an). Zweck: am Schullaptop bewusst über
  Tailscale testen, obwohl Auto dort die LAN-IP wählen würde.
- Server-State (`AppState.force_tailscale_ip`), nicht localStorage → über
  `state_snapshot()` synchron bei Reconnect / zweitem Host.
- `POST /api/force-tailscale-ip {enabled}` setzt das Flag, baut den bei
  `/modus-b/open` **eingefrorenen** Schüler-Join-QR bei offener Ausgabe neu
  (Helfer- + iPad-Display-QR sind On-Demand und ziehen den Modus eh nach).
- `_detect_lan_ip` cacht **pro Modus** (Auto/Tailscale getrennt).
- **Wiring-Fix:** `_base_url` honorierte den Toggle bisher nur, wenn der Host
  über `localhost` zugriff — bei Zugriff über eine echte IP gewann der
  `Host`-Header und der Toggle blieb wirkungslos. Jetzt überschreibt der
  erzwungene Tailscale-Pfad den Header in **jeder** QR-URL.

Sichtbarkeit: Auf dem VPS gibt es nur public + Tailscale; Auto wählt dort eh
schon Tailscale → der Toggle zeigt dort **keinen** Unterschied (erwartbar). Auf
dem Schullaptop wechselt er sichtbar LAN (`10.254.x`) ⇄ Tailscale (`100.x`).

`HOST_IP` (`.env`) gilt nur im Auto-Pfad; der erzwungene Tailscale-Toggle hat
Vorrang vor dem `Host`-Header (aber nicht über `HOST_IP` im Auto-Zweig).

Cert-Gotcha: SAN wird einmalig erzeugt und gecacht. Auf dem VPS kann ein altes
Cert die Tailscale-IP noch nicht im SAN haben → bei Cert-Warnung auf der
`100.x` `certs/` löschen, damit neu generiert wird (SAN enthält dann `100.x`).
