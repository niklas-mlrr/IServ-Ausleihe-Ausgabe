"""Persistenz der Helfer-Registrierungen (`AppState.helper_sessions`).

Schmaler Sync-IO-Layer (Spiegel von `booklist_store.py`/`printer_store.py`):
lädt/speichert nur `token` + `name` als `data/helpers.json`, damit ein Helfer
nach einem Serverneustart über denselben Token (URL bereits auf dem Handy
offen) wieder ansprechbar ist. Kein IServ-Kontakt, keine AppState-Abhängigkeit
— reine Datei-IO, Schreibfehler werden geloggt, nicht weitergeworfen.

Bewusst NICHT persistiert: Klassen-Bindung (`context_id`), zugewiesener
Schüler, Queue-/Worker-Zustand — das ist Tagesbetrieb und startet nach einem
Neustart leer (der Host bindet den wiederhergestellten Helfer bei Bedarf neu
an eine Klasse, s. `/api/helper/{token}/class`).

`server_ip` (Fingerprint der Server-LAN-IP, s. `sessions.server_lan_ip`) wird
mitgespeichert: weicht sie beim Laden vom aktuell erkannten Netz ab, wird GAR
NICHT geladen — die alten Token stecken in URLs, die auf die alte IP zeigen
und auf einem anderen Netz ohnehin nie erreichbar wären. Welche Einträge
überhaupt zum Speichern anstehen (nur je einmal verbunden gewesene Helfer),
entscheidet der Aufrufer (`sessions.persist_helpers`) — diese Datei kennt nur
die primitive Form.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path(__file__).resolve().parent.parent / "data/helpers.json"

_write_lock = threading.Lock()


def load(current_ip: str | None) -> list[tuple[str, str]]:
    """Gespeicherte Helfer lesen: Liste von `(token, name)`. Liefert `[]` bei
    fehlender/korrupter Datei oder wenn `current_ip` von der gespeicherten
    Server-IP abweicht (non-fatal — In-Memory-State bleibt leer wie vor
    dieser Persistenz)."""
    if not STORE_PATH.is_file():
        return []
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("Helfer-Persistenz nicht lesbar (%s) — starte leer", STORE_PATH)
        return []

    if not isinstance(data, dict):
        return []
    saved_ip = data.get("server_ip")
    if saved_ip != current_ip:
        log.info(
            "Helfer-Persistenz verworfen (Server-IP geändert: %s -> %s)", saved_ip, current_ip
        )
        return []

    entries = data.get("helpers", [])
    result: list[tuple[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        token = entry.get("token")
        name = entry.get("name")
        if isinstance(token, str) and token and isinstance(name, str):
            result.append((token, name))
    return result


def save(helpers, current_ip: str | None) -> None:
    """Aktuelle Helfer atomar wegschreiben. `helpers`: Iterable von Objekten
    mit `.token`/`.name` (i.d.R. eine vorgefilterte Teilmenge von
    `AppState.helper_sessions.values()`). Schreibfehler werden geloggt, nicht
    weitergeworfen — der Aufrufer (Endpoint) darf nicht crashen."""
    data = {
        "server_ip": current_ip,
        "helpers": [{"token": h.token, "name": h.name} for h in helpers],
    }
    with _write_lock:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, STORE_PATH)
        except OSError:
            log.exception("Speichern der Helfer-Persistenz fehlgeschlagen")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
