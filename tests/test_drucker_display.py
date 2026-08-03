"""Tests für das Drucker-Display (`/drucker-display`).

Pairing-Flow (Authorize), Drucker-Zuweisung (Assign), Abmelden (Forget) und
die `state_snapshot`-Schnittstelle (`printer_displays`) — über echtes HTTP
(`starlette.testclient.TestClient`) gegen `create_app()` ohne Lifespan. Plus
Einheitstest für `send_printer_display_update` (Registration vs. Queue-Payload).
IServ/Worker/echter Drucker bleiben außen vor; State/Config/Hub gemockt.
"""

from __future__ import annotations

import asyncio

import pytest

import server.hub as hub
import server.routes.auth as auth_routes
import server.routes.booklists as booklists_routes
import server.routes.classes as classes_routes
import server.routes.drucker_display as drucker_display_routes
import server.routes.helpers as helpers_routes
import server.routes.modus_b as modus_b_routes
import server.routes.queue as queue_routes
import server.routes.settings as settings_routes
import server.routes.slips as slips_routes
import server.sessions as sessions
from server.config import Config
from server.routes import _deps as deps_routes
from server.state import AppState, PrinterDisplaySession


class _FakeWS:
    """Sammelt gesendete JSON-Nachrichten (Drucker-Display-WS)."""

    def __init__(self) -> None:
        self.sent = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


class _FakeHub:
    """Hub-Stub: broadcast_host/Queue/Sends sind No-ops; send_websocket liefert
    an die Fake-WS weiter, damit `send_printer_display_update`-Aufrufe in den
    Endpoints sichtbar werden (Display bekommt Queue-Sicht gepusht)."""

    def __init__(self) -> None:
        self.broadcasts = []

    async def broadcast_host(self, snapshot) -> None:
        self.broadcasts.append(snapshot)

    async def broadcast_settings(self, *a, **kw) -> None:
        pass

    async def send_scanner(self, *a, **kw) -> None:
        pass

    async def send_websocket(self, ws, msg) -> bool:
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001 — wie echtes Hub: False statt werfen
            return False
        return True


def _make_config(**over) -> Config:
    base = dict(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        allow_booking=False,
    )
    base.update(over)
    return Config(**base)


# Route-Module, die get_state/get_config/get_hub selbst importieren — patched,
# damit die Endpunkte den frischen Fixture-State treffen (nicht den Singleton).
_ROUTE_MODULES = [
    deps_routes,
    auth_routes,
    classes_routes,
    booklists_routes,
    helpers_routes,
    queue_routes,
    slips_routes,
    modus_b_routes,
    settings_routes,
    drucker_display_routes,
]


@pytest.fixture
def ctx(monkeypatch):
    """Frischer State + Config + Fake-Hub; gültige Host-Session 'sid'."""
    state = AppState()
    state.add_host_session("sid")
    cfg = _make_config()
    hub_inst = _FakeHub()
    for mod in _ROUTE_MODULES:
        if hasattr(mod, "get_state"):
            monkeypatch.setattr(mod, "get_state", lambda: state)
        if hasattr(mod, "get_config"):
            monkeypatch.setattr(mod, "get_config", lambda: cfg)
        if hasattr(mod, "get_hub"):
            monkeypatch.setattr(mod, "get_hub", lambda: hub_inst)
    # sessions.send_printer_display_update nutzt sessions.get_hub/get_state.
    monkeypatch.setattr(sessions, "get_hub", lambda: hub_inst)
    monkeypatch.setattr(sessions, "get_state", lambda: state)
    monkeypatch.setattr(hub, "get_hub", lambda: hub_inst)
    return state, cfg, hub_inst


# ---- Auth-Guard ------------------------------------------------------------


def test_qr_requires_host(client, ctx):
    r = client.get("/api/drucker-display/qr")
    assert r.status_code == 403


def test_qr_returns_url_and_data_url(client, ctx):
    r = client.get("/api/drucker-display/qr", cookies={"session_id": "sid"})
    assert r.status_code == 200
    d = r.json()
    assert d["url"].endswith("/drucker-display")
    assert d["qr"].startswith("data:image/")


# ---- Enable (Freischaltung per Name) --------------------------------------


def _connected_display(state, *, code="ABCD", authorized=False, display_id="disp1"):
    d = PrinterDisplaySession(display_id=display_id, registration_code=code)
    d.authorized = authorized
    state.printer_displays[d.display_id] = d
    return d


def test_enable_unknown_display_404(client, ctx):
    r = client.post(
        "/api/drucker-display/enable",
        json={"display_id": "nope", "label": "Raum 1"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_enable_authorizes_sets_label_and_pushes(client, ctx):
    state, _, hub_inst = ctx
    _connected_display(state, code="ABCD")  # verbunden, nicht autorisiert
    r = client.post(
        "/api/drucker-display/enable",
        json={"display_id": "disp1", "label": "  Raum 1  "},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["display_id"] == "disp1"
    assert state.printer_displays["disp1"].authorized is True
    assert state.printer_displays["disp1"].label == "Raum 1"  # gestript
    # Host bekommt aktualisierte Display-Liste gepusht (Snapshot-Broadcast).
    assert len(hub_inst.broadcasts) == 1


# ---- Assign ---------------------------------------------------------------


def test_assign_filters_orphan_ids(client, ctx):
    state, _, _ = ctx
    from server.state import PrinterConfig

    state.settings.printers = [PrinterConfig(id="p1", name="P1"),
                               PrinterConfig(id="p2", name="P2")]
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/assign",
        json={"display_id": "disp1", "printer_ids": ["p1", "orphan"]},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    # Verwaiste ID (nicht im Pool) wird herausgefiltert; geordnete Liste.
    assert state.printer_displays["disp1"].assigned_printer_ids == ["p1"]


def test_assign_none_means_all_printers(client, ctx):
    state, _, _ = ctx
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/assign",
        json={"display_id": "disp1", "printer_ids": None},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert state.printer_displays["disp1"].assigned_printer_ids is None


def test_assign_unknown_display_404(client, ctx):
    r = client.post(
        "/api/drucker-display/assign",
        json={"display_id": "bogus", "printer_ids": None},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_assign_unauthorized_display_404(client, ctx):
    state, _, _ = ctx
    _connected_display(state, code="ABCD", authorized=False)
    r = client.post(
        "/api/drucker-display/assign",
        json={"display_id": "disp1", "printer_ids": None},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


# ---- Forget ---------------------------------------------------------------


def test_forget_removes_display(client, ctx):
    state, _, hub_inst = ctx
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/forget",
        json={"display_id": "disp1"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert "disp1" not in state.printer_displays
    # Token wird verboten — künftige Verbindungen mit ihm bleiben gesperrt.
    assert "disp1" in state.banned_printer_display_tokens
    assert len(hub_inst.broadcasts) == 1


def test_forget_unknown_display_404(client, ctx):
    r = client.post(
        "/api/drucker-display/forget",
        json={"display_id": "bogus"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


# ---- state_snapshot: printer_displays -------------------------------------


def test_snapshot_printer_displays_shape(client, ctx):
    state, _, _ = ctx
    _connected_display(state, code="ABCD", authorized=True)
    # Geordnete Liste — Reihenfolge bleibt erhalten (keine Sortierung mehr).
    state.printer_displays["disp1"].assigned_printer_ids = ["p2", "p1"]
    r = client.get("/api/state", cookies={"session_id": "sid"})
    assert r.status_code == 200
    pd = r.json()["printer_displays"]
    assert len(pd) == 1
    assert pd[0]["display_id"] == "disp1"
    assert pd[0]["authorized"] is True
    assert pd[0]["connected"] is False  # kein WS gesetzt
    assert pd[0]["assigned_printer_ids"] == ["p2", "p1"]  # Reihenfolge erhalten
    # Default-Name (leer); Registration-Code im Snapshot (für Reiter-Label);
    # Default-Theme None = folgt System-Einstellung.
    assert pd[0]["label"] == ""
    assert pd[0]["registration_code"] == "ABCD"
    assert pd[0]["theme"] is None


def test_snapshot_printer_displays_none_means_all(client, ctx):
    state, _, _ = ctx
    _connected_display(state, code="ABCD", authorized=True)
    r = client.get("/api/state", cookies={"session_id": "sid"})
    pd = r.json()["printer_displays"]
    assert pd[0]["assigned_printer_ids"] is None  # Default = alle Pool-Drucker


def test_assign_preserves_order_and_dedupes(client, ctx):
    """Reihenfolge der Request-Liste bleibt erhalten; Duplikate + verwaiste IDs
    werden entfernt (erstes Vorkommen gewinnt)."""
    state, _, _ = ctx
    from server.state import PrinterConfig

    state.settings.printers = [PrinterConfig(id="p1", name="P1"),
                               PrinterConfig(id="p2", name="P2")]
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/assign",
        json={"display_id": "disp1", "printer_ids": ["p2", "p1", "p2", "orphan"]},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert state.printer_displays["disp1"].assigned_printer_ids == ["p2", "p1"]
    # Snapshot spiegelt die Reihenfolge.
    snap = client.get("/api/state", cookies={"session_id": "sid"}).json()
    assert snap["printer_displays"][0]["assigned_printer_ids"] == ["p2", "p1"]


# ---- Label + Theme --------------------------------------------------------


def test_label_endpoint_sets_name_and_pushes(client, ctx):
    state, _, hub_inst = ctx
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/label",
        json={"display_id": "disp1", "label": "  Raum 104  "},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["label"] == "Raum 104"  # ge-trimmed
    assert state.printer_displays["disp1"].label == "Raum 104"
    # Host-Snapshot aktualisiert.
    assert len(hub_inst.broadcasts) == 1
    assert hub_inst.broadcasts[0]["printer_displays"][0]["label"] == "Raum 104"


def test_label_unauthorized_display_404(client, ctx):
    state, _, _ = ctx
    _connected_display(state, code="ABCD", authorized=False)
    r = client.post(
        "/api/drucker-display/label",
        json={"display_id": "disp1", "label": "x"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_theme_endpoint_sets_theme_and_pushes(client, ctx):
    state, _, hub_inst = ctx
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/theme",
        json={"display_id": "disp1", "theme": "light"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["theme"] == "light"
    assert state.printer_displays["disp1"].theme == "light"
    assert hub_inst.broadcasts[0]["printer_displays"][0]["theme"] == "light"


def test_theme_invalid_value_400(client, ctx):
    state, _, _ = ctx
    _connected_display(state, code="ABCD", authorized=True)
    r = client.post(
        "/api/drucker-display/theme",
        json={"display_id": "disp1", "theme": "rainbow"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400


# ---- send_printer_display_update: Payload je Zustand ----------------------


def test_send_printer_display_update_registration(monkeypatch):
    """Nicht authorisiert → Registration-Payload (nur Code, keine Queue)."""
    state = AppState()
    hub_inst = _FakeHub()
    monkeypatch.setattr(sessions, "get_hub", lambda: hub_inst)
    d = PrinterDisplaySession(display_id="d1", registration_code="XYZ1")
    d.ws = _FakeWS()
    asyncio.run(sessions.send_printer_display_update(state, d))
    # theme=None (Default) → kein theme-Key; das Display folgt der System-Einstellung.
    assert d.ws.sent == [{
        "type": "registration",
        "code": "XYZ1",
        "display_id": "d1",
        "label": "",
    }]


def test_send_printer_display_update_queue(monkeypatch):
    """Authorisiert → Queue-Payload mit gefilterter Drucker-/Wartelisten-Sicht.
    theme nur dabei, wenn der Host es gesetzt hat."""
    from server.state import PrinterConfig

    state = AppState()
    state.settings.printers = [PrinterConfig(id="p1", name="P1")]
    hub_inst = _FakeHub()
    monkeypatch.setattr(sessions, "get_hub", lambda: hub_inst)
    monkeypatch.setattr(sessions, "get_state", lambda: state)
    d = PrinterDisplaySession(display_id="d1", registration_code="XYZ1", authorized=True)
    d.assigned_printer_ids = ["p1"]
    d.theme = "dark"  # Host hat explizit gesetzt → im Payload
    d.ws = _FakeWS()
    asyncio.run(sessions.send_printer_display_update(state, d))
    assert len(d.ws.sent) == 1
    msg = d.ws.sent[0]
    assert msg["type"] == "queue"
    assert msg["label"] == ""
    assert msg["theme"] == "dark"
    assert [p["id"] for p in msg["printers"]] == ["p1"]
    assert msg["waiting"] == 0


def test_send_printer_display_update_dead_ws_cleared(monkeypatch):
    """`send_websocket` liefert False (tote WS) → `display.ws` wird auf None
    gesetzt, damit der WS-Handler die Session aufräumt (s. send_display_update)."""
    state = AppState()
    hub_inst = _FakeHub()
    monkeypatch.setattr(sessions, "get_hub", lambda: hub_inst)

    class _DeadWS:
        async def send_json(self, msg):
            raise RuntimeError("verbindung tot")

    d = PrinterDisplaySession(display_id="d1", registration_code="XYZ1")
    d.ws = _DeadWS()
    asyncio.run(sessions.send_printer_display_update(state, d))
    assert d.ws is None
