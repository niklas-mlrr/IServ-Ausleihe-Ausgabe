"""Unit-Tests für die Drucker-Pool-Persistenz (server/printer_store.py).

Roundtrip speichern/laden, Validierung gegen die Geräte-Druckerliste (fehlende
Drucker werden verworfen + aus der JSON gelöscht) und erster-Start-Default.
Reine Datei-IO gegen eine tmp-Datei (STORE_PATH wird weggemockt)."""

from __future__ import annotations

import json
from pathlib import Path

import server.printer_store as printer_store
from server.state import PrinterConfig


def _tmp_store(monkeypatch, tmp_path: Path) -> Path:
    p = tmp_path / "printers.json"
    monkeypatch.setattr(printer_store, "STORE_PATH", p)
    return p


def test_load_first_start_default(monkeypatch, tmp_path):
    """Keine Datei → erster-Start-Default [Standarddrucker]."""
    _tmp_store(monkeypatch, tmp_path)
    pool = printer_store.load([])
    assert len(pool) == 1
    assert pool[0].name is None  # Standarddrucker
    assert pool[0].duplex == "one_sided"


def test_save_load_roundtrip(monkeypatch, tmp_path):
    """Speichern + Laden erhält Namen und Duplex-Modus (ids werden neu erzeugt)."""
    _tmp_store(monkeypatch, tmp_path)
    pool = [
        PrinterConfig(id="x", name=None),
        PrinterConfig(id="y", name="HP-LJ", duplex="two_sided_long"),
    ]
    printer_store.save(pool)
    loaded = printer_store.load(["HP-LJ"])
    assert [(p.name, p.duplex) for p in loaded] == [
        (None, "one_sided"),
        ("HP-LJ", "two_sided_long"),
    ]
    # ids sind frisch (nicht die gespeicherten „x"/„y").
    assert all(p.id not in ("x", "y") for p in loaded)


def test_load_drops_missing_named_printer(monkeypatch, tmp_path):
    """Benannter Drucker, den das Gerät nicht mehr meldet, wird verworfen — und
    die JSON wird bereinigt zurückgeschrieben (kein Verweis auf ein Gespenst)."""
    p = _tmp_store(monkeypatch, tmp_path)
    p.write_text(
        json.dumps({"printers": [{"name": "Alt"}, {"name": None}, {"name": "Neu"}]}),
        encoding="utf-8",
    )
    loaded = printer_store.load(["Neu"])  # „Alt" fehlt auf dem Gerät
    names = [pp.name for pp in loaded]
    assert "Alt" not in names
    assert names == [None, "Neu"] or names == ["Neu", None]  # Reihenfolge aus Datei
    # JSON wurde bereinigt — „Alt" ist verschwunden.
    cleaned = json.loads(p.read_text(encoding="utf-8"))
    assert [e["name"] for e in cleaned["printers"]] == [None, "Neu"]


def test_load_empty_device_list_drops_all_named(monkeypatch, tmp_path):
    """file-Backend o. Ä. (leere Geräteliste): alle benannten Drucker werden
    verworfen; nur ein ggf. vorhandener Standarddrucker bleibt."""
    p = _tmp_store(monkeypatch, tmp_path)
    p.write_text(json.dumps({"printers": [{"name": "HP"}, {"name": None}]}), encoding="utf-8")
    loaded = printer_store.load([])  # Gerät meldet keine Drucker
    assert [pp.name for pp in loaded] == [None]


def test_load_rejects_unknown_duplex(monkeypatch, tmp_path):
    """Unbekannter Duplex-Modus in der JSON → Default `one_sided`."""
    p = _tmp_store(monkeypatch, tmp_path)
    p.write_text(json.dumps({"printers": [{"name": "HP", "duplex": "bogus"}]}), encoding="utf-8")
    loaded = printer_store.load(["HP"])
    assert loaded[0].duplex == "one_sided"
