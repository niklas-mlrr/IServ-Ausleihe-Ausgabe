"""Persistenz der jahrgangsweiten Bücher-Reihenfolge & Ausblendung.

Schmaler Sync-IO-Layer: lädt/speichert `book_orders_by_grade` und
`hidden_isbns_by_grade` als einzelner globaler Satz in
`data/booklist_settings.json`. Kein IServ-Kontakt, keine AppState-Abhängigkeit
— reine Datei-IO, damit der In-Memory-State (`server/state.py`) die Leading
Source bleibt und Schreibfehler nie den Endpoint crashen.

„Neue Bücher sichtbar ans Ende anhängen" greift bereits beim Lesen über
`normalize_book_order` + `hidden & catalog` (s. routes/api.py); diese Datei
ergänzt allein die Persistenz über Serverneustarts hinweg.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

STORE_PATH = Path("data/booklist_settings.json")

_write_lock = threading.Lock()


def _empty() -> tuple[dict[int, list[str]], dict[int, set[str]]]:
    return {}, {}


def load() -> tuple[dict[int, list[str]], dict[int, set[str]]]:
    """Gespeicherten Stand lesen. Liefert `({}, {})` bei fehlender oder
    korrupter Datei (non-fatal — In-Memory-State bleibt leer wie vor dieser
    Persistenz). Grades kommen als `int` zurück, `hidden` als `set`."""
    if not STORE_PATH.is_file():
        return _empty()
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("booklist-Persistenz nicht lesbar (%s) — starte leer", STORE_PATH)
        return _empty()

    orders_raw = data.get("orders", {}) if isinstance(data, dict) else {}
    hidden_raw = data.get("hidden", {}) if isinstance(data, dict) else {}
    orders: dict[int, list[str]] = {}
    hidden: dict[int, set[str]] = {}
    for grade_key, seq in orders_raw.items():
        try:
            grade = int(grade_key)
        except (TypeError, ValueError):
            continue
        if isinstance(seq, list):
            orders[grade] = [s for s in seq if isinstance(s, str)]
    for grade_key, seq in hidden_raw.items():
        try:
            grade = int(grade_key)
        except (TypeError, ValueError):
            continue
        if isinstance(seq, list):
            hidden[grade] = {s for s in seq if isinstance(s, str)}
    return orders, hidden


def save(orders: dict[int, list[str]], hidden: dict[int, set[str]]) -> None:
    """Aktuellen In-Memory-Stand atomar wegschreiben. Sets werden als
    `sorted(list)` serialisiert, Grades als `str` (JSON erlaubt nur String-
    Keys). Schreibfehler werden geloggt, nicht weitergeworfen — der Aufrufer
    (Endpoint) darf nicht crashen."""
    data = {
        "orders": {str(g): list(seq) for g, seq in orders.items()},
        "hidden": {str(g): sorted(seq) for g, seq in hidden.items()},
    }
    with _write_lock:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, STORE_PATH)
        except OSError:
            log.exception("Speichern der booklist-Einstellungen fehlgeschlagen")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
