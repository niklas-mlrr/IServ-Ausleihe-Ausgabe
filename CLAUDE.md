# IServ Ausleihe-Ausgabe — Project Memory

Seminarfachprojekt: Handy-Scanner für die Schulbuch-Stapelerstellung (Modus A)
und Live-Ausgabe-Pilot (Modus B). **Arbeitsgrundlage: `docs/PLAN.md`** (Zielbild,
Architektur, Sicherheitsmodell, Phasen, offene Punkte O1–O9).

Wiki-Kontext: `~/cc/wiki/30_projects/sba/ausleihe_ausgabe/` (Projekt) und
`~/cc/wiki/30_projects/sba/ausleihe_api/` (api_reference, auth, schemas,
write_endpoints).

## ⚠️ PRODUKTIONS-SCHUTZ — UNBEDINGT BEACHTEN

Die `.env` enthält **echte IServ-Zugangsdaten gegen die PRODUKTIONSUMGEBUNG**
der Schule (Ausleihe-Admin-Account). Diese Credentials greifen auf reale,
produktive Schul-/Schülerdaten zu.

**ABSOLUTE REGELN:**

- **Accounts (geklärt 2026-06-12):** Die `.env`-Credentials gehören zum
  Admin-Account von **Lukas Podleschny** (Mitentwickler). Dieser Account dient
  **AUSSCHLIESSLICH zum Lesen** von Informationen (API-Reads, Playwright-
  Browsing) — niemals zum Setzen/Schreiben auf das Live-System.
- **Testschüler ist Niklas Müller:** Auf seiner Schülerkartei werden später
  Test-Buchungen durchgeführt — aber **nur nach expliziter Bestätigung von
  Niklas und Lukas im Einzelfall**. Auch mit dem Testschüler gilt: ohne
  Freigabe nichts am Live-System verändern.
- Die `ausleihe-api` wird hier **ausnahmslos read-only** genutzt:
  `AusleiheClient()` immer mit `allow_writes=False` (Default). **Niemals**
  PUT/POST/DELETE gegen die Produktion — auch nicht „zum Testen". In diesem
  Projekt gibt es keinen Grund, das je zu ändern.
- Schreiboperationen (Buch ausgeben/zurücknehmen) laufen **ausschließlich**
  via Playwright durch das **offizielle IServ-Frontend** — nie durch
  selbstprogrammierte API-Calls. Die in
  `~/cc/wiki/30_projects/sba/ausleihe_api/write_endpoints.md` dokumentierten
  Schreib-Endpunkte (z. B. `processBook`) sind reine Dokumentation.
- **Playwright-Tests nur mit Niklas' Account und ausgemusterten Büchern.**
  Jede Test-Ausleihe wird **sofort zurückgenommen**. Vor jedem Probelauf den
  Rückbau-Plan schriftlich festhalten (PLAN §6).
- Buchende Spike-/Test-Läufe (alles, was eine Ausleihe anlegt oder
  zurücknimmt) **nie unbeaufsichtigt oder automatisch** starten — nur nach
  expliziter Freigabe durch Niklas im jeweiligen Einzelfall.
- Credentials niemals committen oder loggen. Keine Schülerdaten in Logs
  (Buch-Codes ja, Namen nein — PLAN §3.7).

Lesen (GET, Playwright-Browsing ohne Submit) ist okay. Alles andere gegen
Produktion ist tabu.

### Buchungs-Freigabe (2026-07-02) — Enter ist bedingt erlaubt

Niklas hat das Klicken auf **Enter** (Buchung via Playwright/offizielles Frontend)
freigegeben. Es gilt weiterhin: **nie API-Writes**, nur der Enter-Pfad. Enter
darf **ausschließlich** feuern, wenn eine gescannte Buchung **beide** Bedingungen
erfüllt (read-only vorab geprüft, sonst wird der Barcode gar nicht ins Feld getippt):

1. **Buch im Lager** (`available` & nicht verliehen & nicht ausgesondert),
2. **Bestellt & Reihe noch nicht ausgeliehen** (ISBN im Status „vorgemerkt").

Absicherung: `evaluate_scan_for_booking()` (streng bei Unsicherheit) + Master-Gate
`ALLOW_BOOKING` (Default `false` = read-only, Scan bleibt staged). Details: PLAN §6.1.
Scharfschalten (`ALLOW_BOOKING=true`) nach wie vor nur mit ausgemusterten Büchern +
Rückbau-Plan; Real-Buchungen nie unbeaufsichtigt.

## Dokumentations-Workflow (Wiki ↔ docs/)

- **`docs/` im Repo ist die Primärquelle** für alles Projektgebundene:
  PLAN.md, Spike-Protokolle, Architekturentscheidungen, Messergebnisse
  (= Seminarfach-Material, versioniert mit dem Code).
- Die Wiki-Seite (`~/cc/wiki/30_projects/sba/ausleihe_ausgabe/overview.md`)
  ist nur Überblick + Einstieg: Status, Key Facts, Links auf `docs/`.
  Inhalte nie duplizieren — eine Info hat genau einen Heimatort.
- **Bei `/wiki-ingest` in diesem Projekt immer beide Seiten prüfen:**
  1. `docs/` — fehlen Protokolle/Entscheidungen/Messwerte aus der Session?
  2. Wiki-Overview — stimmen Status, Phasen-Stand und Verweise noch?
- Die Änderungshistorie (chronologisches Protokoll, was sich wann geändert hat)
  lebt **ausschließlich** in `docs/CHANGELOG.md` — nicht in Code-Kommentaren,
  nicht in `docs/PLAN.md`/`docs/test_status.md` (die bleiben Zielbild bzw.
  Verifiziert-/Offen-Stand) und nicht in der Wiki-Overview.

## Projekt-Setup

- `uv sync` — Umgebung (Python ≥3.12); `iserv-ausleihe-api` kommt als
  editable Install aus dem Schwesterprojekt `../ausleihe-api`.
- `uv run playwright install chromium` — Browser für den Write-Pfad.
- `.env` nach Vorlage `.env.example` (`ISERV_DOMAIN`, `ISERV_USERNAME`,
  `ISERV_PASSWORD`, `HOST_PASSWORD`; optional `PORT`, `WORKER_CONTEXTS`
  sowie die Druck-Variablen `PRINT_BACKEND`, `PRINTER_NAME`, `SUMATRA_PATH`).
- Linting: `uvx ruff check server/ automation/ tests/` (Regeln + Ignores in
  `pyproject.toml` unter `[tool.ruff]`); `pre-commit install` zieht denselben
  Ruff-Hook (`--fix`) vor jedem Commit (`.pre-commit-config.yaml`).

## Struktur

| Pfad | Inhalt |
|------|--------|
| `server/` | FastAPI-App: HTTPS (`tls.py`), WebSocket-Hub (`hub.py`), Rollen/Sessions (`sessions.py`, `state.py` — `AppState` + seit Welle 5 ausgelagert: `RuntimeSettings`, `IservCaches`), IServ-Read-Client (`iserv_client.py`), Druck (`printing.py`), Bücher-Reihenfolge (`book_order.py`), Rate-Limiting (`ratelimit.py`), Endpunkte (`routes/` — Paket aus neun Modulen: `_deps.py`/`auth.py`/`classes.py`/`booklists.py`/`helpers.py`/`queue.py`/`slips.py`/`modus_b.py`/`settings.py`, `api.py` als Aggregator, `ws.py` für WebSockets) |
| `automation/` | Playwright-Worker (`worker.py`) + Spikes + E2E-Skripte (`e2e_*.py`); Ausgaben in `automation/out/` (gitignored) |
| `web/` | Statische UI ohne Build-Step: `host.html` (Host, schlankes Grundgerüst) + `host.js`/`host.css` (Logik/Styles), `common.js` (gemeinsame Helfer: `escapeHtml`, `isBookDone`, `Beeper`, `connectWebSocket`), `scan.html`/`scan.js` (Helfer-Scanner, Modus A), `student.html`/`student.js` (Schüler, Modus B), `qr-display.html` (iPad), `html5-qrcode.min.js`, `beep.mp3` |
| `docs/PLAN.md` | Projektplan — bei Entscheidungen fortschreiben (Arbeitsgrundlage) |
| `docs/CHANGELOG.md` | Chronologisches Änderungsprotokoll, neueste Einträge zuerst — Heimatort der „was hat sich wann geändert"-Historie |
| `docs/test_status.md` | Lebender Test-/Verifizierungsstand (verifiziert vs. offen) |
| `docs/spikes/` | Spike-Protokolle (Seminarfach-Material) |
| `tests/` | pytest-Unit-Tests (Suite grün, siehe `docs/test_status.md` für aktuellen Stand); `tests/test_state_contract.py` friert das Draht-Format von `state_snapshot()` als Charakterisierungs-Test ein — bei Refactorings nicht anpassen |
