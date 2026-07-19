"""Persistenz des Leihschein-Drucker-Pools (`RuntimeSettings.printers`).

Schmaler Sync-IO-Layer (Spiegel von `booklist_store.py`): lädt/speichert die
Drucker-Liste + Duplex-Modus als `data/printers.json`. Kein IServ-Kontakt, keine
AppState-Abhängigkeit — reine Datei-IO, damit der In-Memory-State (`state.py`)
die Leading Source bleibt und Schreibfehler nie den Endpoint crashen.

Validierung beim Laden: jeder *benannte* Drucker wird gegen die Geräte-Druckerliste
(`list_printers().printers`) geprüft; fehlt er auf dem Gerät, wird er nicht
geladen **und** aus der JSON gelöscht. Der `name=null`-Eintrag (Standarddrucker)
gilt als immer gültig und bleibt unangetastet. Fehlt die Datei ganz, liefert
`load` den ersten-Start-Default `[Standarddrucker]`.

IDs werden bewusst NICHT persistiert — sie sind nur zur Laufzeit stabil (für
Slot-Zuordnung / Endpoint-Bezug) und werden beim Laden neu erzeugt (die
Druckerwarteschlange startet eh leer).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .state import DUPLEX_MODES, PrinterConfig, _new_printer_id

log = logging.getLogger(__name__)

STORE_PATH = Path("data/printers.json")

_write_lock = threading.Lock()


def _first_start_default() -> list[PrinterConfig]:
    """Erster Start (keine JSON): nur der Standarddrucker."""
    return [PrinterConfig(id=_new_printer_id(), name=None)]


def load(known_printers: list[str]) -> list[PrinterConfig]:
    """Gespeicherten Pool lesen und gegen die Geräte-Druckerliste validieren.

    `known_printers` = aktuelle vom Gerät gemeldete Druckernamen (leer im
    `file`-Backend). Benannte Drucker, die nicht darin stehen, werden verworfen;
    die bereinigte Liste wird atomar zurückgeschrieben, damit nicht mehr
    existierende Drucker aus der JSON verschwinden. Liefert bei fehlender oder
    korrupter Datei den ersten-Start-Default `[Standarddrucker]` (non-fatal).
    """
    if not STORE_PATH.is_file():
        return _first_start_default()
    try:
        raw = STORE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        log.exception("Drucker-Persistenz nicht lesbar (%s) — starte Default", STORE_PATH)
        return _first_start_default()

    entries = data.get("printers", []) if isinstance(data, dict) else []
    known = set(known_printers)
    cleaned: list[PrinterConfig] = []
    dropped: list[str | None] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", None)
        if name is not None and not isinstance(name, str):
            continue
        # name=None (Standarddrucker) gilt als immer gültig; benannte nur, wenn
        # das Gerät sie aktuell meldet.
        if name is not None and name not in known:
            dropped.append(name)
            continue
        duplex = entry.get("duplex", "one_sided")
        if duplex not in DUPLEX_MODES:
            duplex = "one_sided"
        printer = PrinterConfig(id=_new_printer_id(), name=name, duplex=duplex)  # type: ignore[arg-type]
        cleaned.append(printer)

    if dropped:
        log.info(
            "Drucker-Persistenz: %d nicht mehr existierende Drucker verworfen: %s",
            len(dropped), dropped,
        )
        # Bereinigte Liste zurückschreiben, damit die JSON nicht weiter verwaiste
        # Einträge enthält (round-trip mit Validierung).
        save(cleaned)

    return cleaned


def save(printers: list[PrinterConfig]) -> None:
    """Aktuellen Pool atomar wegschreiben. Schreibfehler werden geloggt, nicht
    weitergeworfen — der Aufrufer (Endpoint) darf nicht crashen. `id` wird nicht
    persistiert (nur Laufzeit-zugeordnet)."""
    data = {
        "printers": [
            {"name": p.name, "duplex": p.duplex}
            for p in printers
        ],
    }
    with _write_lock:
        tmp_path = STORE_PATH.with_suffix(".json.tmp")
        try:
            STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, STORE_PATH)
        except OSError:
            log.exception("Speichern der Drucker-Einstellungen fehlgeschlagen")
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
