"""Persistenz freigeschalteter Drucker-Displays (`AppState.printer_displays`).

Schmaler Sync-IO-Layer (Spiegel von `booklist_store.py`/`printer_store.py`):
lädt/speichert `data/printer_displays.json`. Kein IServ-Kontakt, keine
AppState-Abhängigkeit — reine Datei-IO, damit der In-Memory-State
(`state.py`) die Leading Source bleibt und Schreibfehler nie den Endpoint
crashen.

Nur **freigeschaltete** (`authorized`) Displays werden persistiert — nicht
freigeschaltete Sessions werden ohnehin beim Trennen der Verbindung entfernt
(s. `routes/ws.py::ws_drucker_display`), es gäbe also nichts sinnvoll
wiederherzustellen.

Zugewiesene Drucker werden NICHT über die (nur laufzeitstabile, s.
`printer_store.py`) Pool-`id` referenziert, sondern über den Drucker-`name`
(bzw. `None` für den Standarddrucker) — dieselbe Identität, die
`printer_store.py` selbst für seine Validierung nutzt. Das Auflösen
Name → aktuelle `id` (nach dem Laden des Drucker-Pools) macht der Aufrufer
(`server/app.py`), diese Datei kennt nur die primitive Form.

`server_ip` (Fingerprint der Server-LAN-IP, s. `sessions.server_lan_ip`) wird
mitgespeichert: weicht sie beim Laden vom aktuell erkannten Netz ab, wird GAR
NICHT geladen. Welche Displays überhaupt zum Speichern anstehen (nur je
einmal in diesem Serverlauf verbunden gewesene), entscheidet der Aufrufer
(`sessions.persist_printer_displays`).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path(__file__).resolve().parent.parent / "data/printer_displays.json"

_write_lock = threading.Lock()


def load(current_ip: str | None) -> list[dict]:
    """Gespeicherte Displays lesen. Jeder Eintrag: `display_id` (str),
    `label` (str), `theme` (`'light'`/`'dark'`/`None`),
    `assigned_printer_names` (`None` = alle Pool-Drucker, sonst geordnete
    Liste von Namen/`None` für den Standarddrucker). Liefert `[]` bei
    fehlender/korrupter Datei oder wenn `current_ip` von der gespeicherten
    Server-IP abweicht (non-fatal)."""
    if not STORE_PATH.is_file():
        return []
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("Drucker-Display-Persistenz nicht lesbar (%s) — starte leer", STORE_PATH)
        return []

    if not isinstance(data, dict):
        return []
    saved_ip = data.get("server_ip")
    if saved_ip != current_ip:
        log.info(
            "Drucker-Display-Persistenz verworfen (Server-IP geändert: %s -> %s)",
            saved_ip, current_ip,
        )
        return []

    entries = data.get("displays", [])
    result: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        display_id = entry.get("display_id")
        if not isinstance(display_id, str) or not display_id:
            continue
        label = entry.get("label", "")
        if not isinstance(label, str):
            label = ""
        theme = entry.get("theme")
        if theme not in ("light", "dark"):
            theme = None
        names = entry.get("assigned_printer_names", None)
        assigned_printer_names: list[str | None] | None
        if names is None:
            assigned_printer_names = None
        elif isinstance(names, list):
            assigned_printer_names = [n for n in names if n is None or isinstance(n, str)]
        else:
            assigned_printer_names = None
        result.append(
            {
                "display_id": display_id,
                "label": label,
                "theme": theme,
                "assigned_printer_names": assigned_printer_names,
            }
        )
    return result


def save(displays: list[dict], current_ip: str | None) -> None:
    """Aktuelle (freigeschaltete, vorgefilterte) Displays atomar wegschreiben.
    `displays`: Liste von Dicts wie von `load()` zurückgegeben. Schreibfehler
    werden geloggt, nicht weitergeworfen — der Aufrufer (Endpoint) darf nicht
    crashen."""
    data = {"server_ip": current_ip, "displays": displays}
    with _write_lock:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, STORE_PATH)
        except OSError:
            log.exception("Speichern der Drucker-Display-Persistenz fehlgeschlagen")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
