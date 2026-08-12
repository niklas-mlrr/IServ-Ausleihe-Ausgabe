"""Persistenz von Helfern/Drucker-Displays/Scan-Stationen über einen
Serverneustart (`server/helper_store.py`, `printer_display_store.py`,
`scan_station_store.py`, plus die Filterung in `sessions.persist_*`).

Zwei Verwerfungsregeln, unabhängig von der reinen Datei-IO:
1. Andere Server-IP beim Laden als beim Speichern → gar nicht geladen.
2. Ein Eintrag, der im laufenden Serverlauf nie per WS verbunden war
   (`connected_since_start`), wird gar nicht erst gespeichert.
"""

from __future__ import annotations

import server.helper_store as helper_store
import server.printer_display_store as printer_display_store
import server.scan_station_store as scan_station_store
import server.sessions as sessions
from server.state import (
    AppState,
    HelperSession,
    PrinterConfig,
    PrinterDisplaySession,
    ScanStationSession,
)


def _store_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(helper_store, "STORE_PATH", tmp_path / "helpers.json")
    monkeypatch.setattr(printer_display_store, "STORE_PATH", tmp_path / "printer_displays.json")
    monkeypatch.setattr(scan_station_store, "STORE_PATH", tmp_path / "scan_stations.json")


def _fix_ip(monkeypatch, ip: str | None) -> None:
    """`sessions.server_lan_ip()` deterministisch machen (kein echtes Netz)."""
    monkeypatch.setattr(sessions, "server_lan_ip", lambda: ip)


# ---------------------------------------------------------------------------
# Store-Ebene: IP-Fingerprint
# ---------------------------------------------------------------------------


def test_helper_store_load_rejects_ip_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(helper_store, "STORE_PATH", tmp_path / "helpers.json")
    h = HelperSession(token="tok1", name="Anna")
    helper_store.save([h], "10.0.0.5")
    assert helper_store.load("10.0.0.5") == [("tok1", "Anna")]
    assert helper_store.load("10.0.0.6") == []  # andere IP → verworfen
    assert helper_store.load(None) == []


def test_printer_display_store_load_rejects_ip_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(printer_display_store, "STORE_PATH", tmp_path / "printer_displays.json")
    entries = [
        {
            "display_id": "disp1",
            "label": "Raum 1",
            "theme": "dark",
            "assigned_printer_names": ["HP"],
        }
    ]
    printer_display_store.save(entries, "10.0.0.5")
    assert printer_display_store.load("10.0.0.5") == entries
    assert printer_display_store.load("10.0.0.6") == []


def test_scan_station_store_load_rejects_ip_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_station_store, "STORE_PATH", tmp_path / "scan_stations.json")
    entries = [
        {"station_id": "stat1", "label": "Tisch 1", "theme": None, "input_mode": "manual"}
    ]
    scan_station_store.save(entries, "10.0.0.5")
    assert scan_station_store.load("10.0.0.5") == entries
    assert scan_station_store.load("10.0.0.6") == []


# ---------------------------------------------------------------------------
# sessions.persist_* — nur je verbunden gewesene Einträge werden geschrieben
# ---------------------------------------------------------------------------


def test_persist_helpers_skips_never_connected(monkeypatch, tmp_path):
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    state.helper_sessions["a"] = HelperSession(token="a", name="Nie verbunden")
    state.helper_sessions["b"] = HelperSession(
        token="b", name="Verbunden", connected_since_start=True
    )

    sessions.persist_helpers(state)

    assert helper_store.load("10.0.0.5") == [("b", "Verbunden")]


def test_persist_printer_displays_skips_unauthorized_and_never_connected(monkeypatch, tmp_path):
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    state.settings.printers = [PrinterConfig(id="p1", name="HP")]
    state.printer_displays["never"] = PrinterDisplaySession(
        display_id="never", registration_code="AAAA", authorized=True
    )
    state.printer_displays["unauth"] = PrinterDisplaySession(
        display_id="unauth",
        registration_code="BBBB",
        authorized=False,
        connected_since_start=True,
    )
    state.printer_displays["ok"] = PrinterDisplaySession(
        display_id="ok",
        registration_code="CCCC",
        authorized=True,
        connected_since_start=True,
        assigned_printer_ids=["p1"],
    )

    sessions.persist_printer_displays(state)

    loaded = printer_display_store.load("10.0.0.5")
    assert [e["display_id"] for e in loaded] == ["ok"]
    assert loaded[0]["assigned_printer_names"] == ["HP"]


def test_persist_scan_stations_skips_unauthorized_and_never_connected(monkeypatch, tmp_path):
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    state.scan_stations["never"] = ScanStationSession(
        station_id="never", registration_code="AAAA", authorized=True
    )
    state.scan_stations["ok"] = ScanStationSession(
        station_id="ok",
        registration_code="BBBB",
        authorized=True,
        connected_since_start=True,
        label="Tisch 1",
    )

    sessions.persist_scan_stations(state)

    loaded = scan_station_store.load("10.0.0.5")
    assert [e["station_id"] for e in loaded] == ["ok"]


def test_restored_sessions_start_not_connected() -> None:
    """Frisch erzeugte (bzw. aus der Persistenz wiederhergestellte) Sessions
    starten `connected_since_start=False` — sie zählen erst nach dem ersten
    echten WS-Connect in DIESEM Lauf als persistenzwürdig (s. routes/ws.py)."""
    display = PrinterDisplaySession(display_id="d", registration_code="AAAA")
    station = ScanStationSession(station_id="s", registration_code="AAAA")
    assert HelperSession(token="t", name="n").connected_since_start is False
    assert display.connected_since_start is False
    assert station.connected_since_start is False
