"""Persistenz freigeschalteter Scan-Stationen (`AppState.scan_stations`).

Schmaler Sync-IO-Layer (Spiegel von `printer_display_store.py`): lädt/
speichert `data/scan_stations.json`. Kein IServ-Kontakt, keine AppState-
Abhängigkeit — reine Datei-IO, damit der In-Memory-State (`state.py`) die
Leading Source bleibt und Schreibfehler nie den Endpoint crashen.

Nur **freigeschaltete** (`authorized`) Stationen werden persistiert — nicht
freigeschaltete Sessions werden ohnehin beim Trennen der Verbindung entfernt
(s. `routes/ws.py::ws_scan_station`).

Bewusst NICHT persistiert: die temporäre Schüler-Bindung (`student_id` &
Scan-Vorabprüf-Felder) — reiner Tagesbetrieb, startet nach einem Neustart
leer (Station zeigt wieder „Zettel-Code scannen").

`server_ip` (Fingerprint der Server-LAN-IP, s. `sessions.server_lan_ip`) wird
mitgespeichert: weicht sie beim Laden vom aktuell erkannten Netz ab, wird GAR
NICHT geladen. Welche Stationen überhaupt zum Speichern anstehen (nur je
einmal in diesem Serverlauf verbunden gewesene), entscheidet der Aufrufer
(`sessions.persist_scan_stations`)."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path(__file__).resolve().parent.parent / "data/scan_stations.json"

_write_lock = threading.Lock()


def load(current_ip: str | None) -> list[dict]:
    """Gespeicherte Stationen lesen. Jeder Eintrag: `station_id` (str),
    `label` (str), `theme` (`'light'`/`'dark'`/`None`), `input_mode`
    (`'camera'`/`'manual'`/`None`). Liefert `[]` bei fehlender/korrupter
    Datei oder wenn `current_ip` von der gespeicherten Server-IP abweicht
    (non-fatal)."""
    if not STORE_PATH.is_file():
        return []
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("Scan-Station-Persistenz nicht lesbar (%s) — starte leer", STORE_PATH)
        return []

    if not isinstance(data, dict):
        return []
    saved_ip = data.get("server_ip")
    if saved_ip != current_ip:
        log.info(
            "Scan-Station-Persistenz verworfen (Server-IP geändert: %s -> %s)",
            saved_ip, current_ip,
        )
        return []

    entries = data.get("stations", [])
    result: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        station_id = entry.get("station_id")
        if not isinstance(station_id, str) or not station_id:
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
                "station_id": station_id,
                "label": label,
                "theme": theme,
                "input_mode": input_mode,
            }
        )
    return result


def save(stations: list[dict], current_ip: str | None) -> None:
    """Aktuelle (freigeschaltete, vorgefilterte) Stationen atomar
    wegschreiben. `stations`: Liste von Dicts wie von `load()` zurückgegeben.
    Schreibfehler werden geloggt, nicht weitergeworfen — der Aufrufer
    (Endpoint) darf nicht crashen."""
    data = {"server_ip": current_ip, "stations": stations}
    with _write_lock:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, STORE_PATH)
        except OSError:
            log.exception("Speichern der Scan-Station-Persistenz fehlgeschlagen")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
