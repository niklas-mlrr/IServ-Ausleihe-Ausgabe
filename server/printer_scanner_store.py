"""Persistenz freigeschalteter Drucker-Scanner (`AppState.printer_scanners`).

Schmaler Sync-IO-Layer (Spiegel von `scan_station_store.py`): lädt/speichert
`data/printer_scanners.json`. Kein IServ-Kontakt, keine AppState-Abhängigkeit
— reine Datei-IO, damit der In-Memory-State (`state.py`) die Leading Source
bleibt und Schreibfehler nie den Endpoint crashen.

Nur **freigeschaltete** (`authorized`) Scanner werden persistiert — nicht
freigeschaltete Sessions werden ohnehin beim Trennen der Verbindung entfernt
(s. `routes/ws.py::ws_drucker_scan`).

Bewusst NICHT persistiert: das letzte Scan-Ergebnis (`last_scan_*`) — reiner
Tagesbetrieb, startet nach einem Neustart leer.

`server_ip` (Fingerprint der Server-LAN-IP, s. `sessions.server_lan_ip`) wird
mitgespeichert: weicht sie beim Laden vom aktuell erkannten Netz ab, wird GAR
NICHT geladen. Welche Scanner überhaupt zum Speichern anstehen (nur je einmal
in diesem Serverlauf verbunden gewesene), entscheidet der Aufrufer
(`sessions.persist_printer_scanners`)."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path(__file__).resolve().parent.parent / "data/printer_scanners.json"

_write_lock = threading.Lock()


def load(current_ip: str | None) -> list[dict]:
    """Gespeicherte Scanner lesen. Jeder Eintrag: `scanner_id` (str), `label`
    (str), `theme` (`'light'`/`'dark'`/`None`), `input_mode`
    (`'camera'`/`'manual'`/`None`). Liefert `[]` bei fehlender/korrupter Datei
    oder wenn `current_ip` von der gespeicherten Server-IP abweicht
    (non-fatal)."""
    if not STORE_PATH.is_file():
        return []
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("Drucker-Scanner-Persistenz nicht lesbar (%s) — starte leer", STORE_PATH)
        return []

    if not isinstance(data, dict):
        return []
    saved_ip = data.get("server_ip")
    if saved_ip != current_ip:
        log.info(
            "Drucker-Scanner-Persistenz verworfen (Server-IP geändert: %s -> %s)",
            saved_ip, current_ip,
        )
        return []

    entries = data.get("scanners", [])
    result: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        scanner_id = entry.get("scanner_id")
        if not isinstance(scanner_id, str) or not scanner_id:
            continue
        label = entry.get("label", "")
        if not isinstance(label, str):
            label = ""
        theme = entry.get("theme")
        if theme not in ("light", "dark"):
            theme = None
        input_mode = entry.get("input_mode")
        if input_mode not in ("camera", "manual"):
            input_mode = None
        result.append(
            {
                "scanner_id": scanner_id,
                "label": label,
                "theme": theme,
                "input_mode": input_mode,
            }
        )
    return result


def save(scanners: list[dict], current_ip: str | None) -> None:
    """Aktuelle (freigeschaltete, vorgefilterte) Scanner atomar wegschreiben.
    `scanners`: Liste von Dicts wie von `load()` zurückgegeben. Schreibfehler
    werden geloggt, nicht weitergeworfen — der Aufrufer (Endpoint) darf nicht
    crashen."""
    data = {"server_ip": current_ip, "scanners": scanners}
    with _write_lock:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, STORE_PATH)
        except OSError:
            log.exception("Speichern der Drucker-Scanner-Persistenz fehlgeschlagen")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
