"""Persistenz von Helfern/Drucker-Displays/Scan-Stationen über einen
Serverneustart (`server/helper_store.py`, `printer_display_store.py`,
`scan_station_store.py`, plus die Filterung in `sessions.persist_*`).

Zwei Verwerfungsregeln, unabhängig von der reinen Datei-IO:
1. Andere Server-IP beim Laden als beim Speichern → gar nicht geladen.
2. Ein Eintrag, der im laufenden Serverlauf nie per WS verbunden war
   (`connected_since_start`), wird gar nicht erst gespeichert — aber erst,
   wenn der Lauf länger als `sessions.PRUNE_MIN_UPTIME_S` (5 min) dauerte.
   Bei kürzeren Läufen bleibt alles erhalten.
"""

from __future__ import annotations

import server.helper_store as helper_store
import server.printer_display_store as printer_display_store
import server.printer_scanner_store as printer_scanner_store
import server.scan_station_store as scan_station_store
import server.sessions as sessions
from server.state import (
    AppState,
    HelperSession,
    PrinterConfig,
    PrinterDisplaySession,
    PrinterScannerSession,
    ScanStationSession,
)


def _store_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(helper_store, "STORE_PATH", tmp_path / "helpers.json")
    monkeypatch.setattr(printer_display_store, "STORE_PATH", tmp_path / "printer_displays.json")
    monkeypatch.setattr(scan_station_store, "STORE_PATH", tmp_path / "scan_stations.json")
    monkeypatch.setattr(printer_scanner_store, "STORE_PATH", tmp_path / "printer_scanners.json")


def _fix_ip(monkeypatch, ip: str | None) -> None:
    """`sessions.server_lan_ip()` deterministisch machen (kein echtes Netz)."""
    monkeypatch.setattr(sessions, "server_lan_ip", lambda: ip)


def _long_run(state: AppState) -> None:
    """So tun, als liefe dieser Server schon deutlich länger als
    `PRUNE_MIN_UPTIME_S` — erst dann greift Verwerfungsregel 2."""
    state.started_at_monotonic -= sessions.PRUNE_MIN_UPTIME_S + 1


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
            "assigned_scanner_ids": ["scan1"],
            "item_order": [{"kind": "scanner", "id": "scan1"}, {"kind": "printer", "name": "HP"}],
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
    _long_run(state)
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
    _long_run(state)
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
    _long_run(state)
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


def test_persist_printer_scanners_skips_unauthorized_and_never_connected(monkeypatch, tmp_path):
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    _long_run(state)
    state.printer_scanners["never"] = PrinterScannerSession(
        scanner_id="never", registration_code="AAAA", authorized=True
    )
    state.printer_scanners["ok"] = PrinterScannerSession(
        scanner_id="ok",
        registration_code="BBBB",
        authorized=True,
        connected_since_start=True,
        label="Drucker 1",
        input_mode="manual",
    )

    sessions.persist_printer_scanners(state)

    loaded = printer_scanner_store.load("10.0.0.5")
    assert [e["scanner_id"] for e in loaded] == ["ok"]
    assert loaded[0]["input_mode"] == "manual"


def test_printer_display_persists_assigned_scanner_ids(monkeypatch, tmp_path):
    """`assigned_scanner_ids` referenziert Scanner über ihren (stabilen)
    Token — anders als Drucker (Namens-Remapping) braucht das kein
    Auflösen beim Speichern/Laden, s. sessions.persist_printer_displays."""
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    _long_run(state)
    state.printer_displays["ok"] = PrinterDisplaySession(
        display_id="ok",
        registration_code="CCCC",
        authorized=True,
        connected_since_start=True,
        assigned_scanner_ids=["scan1", "scan2"],
    )

    sessions.persist_printer_displays(state)

    loaded = printer_display_store.load("10.0.0.5")
    assert loaded[0]["assigned_scanner_ids"] == ["scan1", "scan2"]


def test_printer_display_item_order_roundtrips_printer_by_name(monkeypatch, tmp_path):
    """`item_order` referenziert Drucker über ihren Namen (wie
    `assigned_printer_names`) und Scanner über die stabile `scanner_id` — ein
    Neustart mit unveränderter Drucker-Pool-Konfiguration löst den Namen
    zurück auf dieselbe (neu vergebene) `id` auf."""
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    _long_run(state)
    state.settings.printers = [PrinterConfig(id="p1", name="HP")]
    state.printer_displays["ok"] = PrinterDisplaySession(
        display_id="ok",
        registration_code="CCCC",
        authorized=True,
        connected_since_start=True,
        assigned_scanner_ids=["scan1"],
        item_order=["scanner:scan1", "printer:p1"],
    )

    sessions.persist_printer_displays(state)

    loaded = printer_display_store.load("10.0.0.5")
    assert loaded[0]["item_order"] == [
        {"kind": "scanner", "id": "scan1"},
        {"kind": "printer", "name": "HP"},
    ]


def test_restored_sessions_start_not_connected() -> None:
    """Frisch erzeugte (bzw. aus der Persistenz wiederhergestellte) Sessions
    starten `connected_since_start=False` — sie zählen erst nach dem ersten
    echten WS-Connect in DIESEM Lauf als persistenzwürdig (s. routes/ws.py)."""
    display = PrinterDisplaySession(display_id="d", registration_code="AAAA")
    station = ScanStationSession(station_id="s", registration_code="AAAA")
    scanner = PrinterScannerSession(scanner_id="sc", registration_code="AAAA")
    assert HelperSession(token="t", name="n").connected_since_start is False
    assert display.connected_since_start is False
    assert station.connected_since_start is False
    assert scanner.connected_since_start is False


# ---------------------------------------------------------------------------
# Kurzer Serverlauf (< PRUNE_MIN_UPTIME_S) — nie verbundene Einträge bleiben
# ---------------------------------------------------------------------------


def test_persist_helpers_keeps_never_connected_on_short_run(monkeypatch, tmp_path):
    """Ein Neustart kurz nach dem Start darf einen wiederhergestellten, noch
    nicht reconnecteten Helfer nicht wegwerfen — sonst ist er beim
    übernächsten Start endgültig weg."""
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()  # frisch gestartet → Uptime ~0
    state.helper_sessions["a"] = HelperSession(token="a", name="Nie verbunden")

    sessions.persist_helpers(state)

    assert helper_store.load("10.0.0.5") == [("a", "Nie verbunden")]


def test_persist_printer_displays_keeps_never_connected_on_short_run(monkeypatch, tmp_path):
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    state.printer_displays["never"] = PrinterDisplaySession(
        display_id="never", registration_code="AAAA", authorized=True
    )
    state.printer_displays["unauth"] = PrinterDisplaySession(
        display_id="unauth", registration_code="BBBB", authorized=False
    )

    sessions.persist_printer_displays(state)

    # Nur die Uptime-Regel wird ausgesetzt — nicht freigeschaltete Displays
    # bleiben ungespeichert.
    assert [e["display_id"] for e in printer_display_store.load("10.0.0.5")] == ["never"]


def test_persist_scan_stations_keeps_never_connected_on_short_run(monkeypatch, tmp_path):
    _store_paths(monkeypatch, tmp_path)
    _fix_ip(monkeypatch, "10.0.0.5")
    state = AppState()
    state.scan_stations["never"] = ScanStationSession(
        station_id="never", registration_code="AAAA", authorized=True
    )

    sessions.persist_scan_stations(state)

    assert [e["station_id"] for e in scan_station_store.load("10.0.0.5")] == ["never"]


def test_prune_threshold_boundary() -> None:
    """Genau bei `PRUNE_MIN_UPTIME_S` greift die Verwerfungsregel bereits."""
    state = AppState()
    assert sessions._prunes_unconnected(state) is False
    state.started_at_monotonic -= sessions.PRUNE_MIN_UPTIME_S
    assert sessions._prunes_unconnected(state) is True
