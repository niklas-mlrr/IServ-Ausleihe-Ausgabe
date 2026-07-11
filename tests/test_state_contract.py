"""Charakterisierungs-Test: die Draht-Formate, die der Host-Client konsumiert.

Dieser Test friert die **öffentliche Schnittstelle** von `AppState` ein, so wie
`web/host.js` sie über `/api/state` bzw. die `state`-WebSocket-Nachricht sieht:
die Schlüsselmenge von `state_snapshot()` und `modus_b_snapshot()`, plus die
Default-Werte der Host-/Entwickler-Toggles.

ZWECK: Er ist ein Netz für Refactorings von `server/state.py` (z. B. das
Herauslösen von `RuntimeSettings`/`IservCaches`). Ein solcher Umbau darf die
Feldnamen im Snapshot NICHT verändern — `web/host.js` liest sie direkt und hat
keine Tests.

DIESER TEST DARF BEI EINEM REFACTORING NICHT ANGEPASST WERDEN. Schlägt er fehl,
hat sich das Draht-Format geändert, nicht der Test.
"""

from __future__ import annotations

from server.state import AppState

# Exakt die Schlüssel, die `state_snapshot()` liefert (Stand vor dem
# AppState-Split). web/host.js liest daraus u. a. `contexts`, `helpers`,
# `modus_b`, `book_order`, `printer_name`.
EXPECTED_SNAPSHOT_KEYS = {
    "type",
    "active_form",
    "active_context_id",
    "contexts",
    "selected_schoolyear",
    "queue",
    "helpers",
    "modus_b",
    "allow_booking",
    "worker_pool",
    "force_tailscale_ip",
    "save_pdf_locally",
    "fix_class_on_slip",
    "slip_second_page_default",
    "printer_name",
    "book_order",
}

EXPECTED_MODUS_B_KEYS = {"open", "join_url", "pending", "pending_count", "displays"}

EXPECTED_WORKER_POOL_KEYS = {"total", "available", "in_use"}


def test_state_snapshot_key_set_is_stable():
    snap = AppState().state_snapshot()
    assert set(snap) == EXPECTED_SNAPSHOT_KEYS, (
        "state_snapshot() hat Felder gewonnen/verloren — web/host.js liest sie "
        "direkt und hat keine Tests. Draht-Format nicht ohne Not ändern."
    )
    assert snap["type"] == "state"


def test_modus_b_snapshot_key_set_is_stable():
    snap = AppState().state_snapshot()["modus_b"]
    assert set(snap) == EXPECTED_MODUS_B_KEYS


def test_worker_pool_stats_key_set_is_stable():
    snap = AppState().state_snapshot()["worker_pool"]
    assert set(snap) == EXPECTED_WORKER_POOL_KEYS


def test_toggle_defaults_are_off():
    """Die fünf Host-/Entwickler-Toggles sind per Default aus bzw. leer.

    `save_pdf_locally`/`fix_class_on_slip` sind Entwickler-Toggles,
    `force_tailscale_ip` erzwingt sonst die Tailscale-IP in QR-URLs,
    `printer_name` None = .env-Default. Ein Refactoring darf diese Defaults
    nicht verschieben.
    """
    snap = AppState().state_snapshot()
    assert snap["force_tailscale_ip"] is False
    assert snap["save_pdf_locally"] is False
    assert snap["fix_class_on_slip"] is False
    assert snap["slip_second_page_default"] is False
    assert snap["printer_name"] is None
