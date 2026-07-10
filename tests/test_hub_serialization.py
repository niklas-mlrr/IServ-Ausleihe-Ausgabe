"""Regressionstest für die Per-Verbindung-Serialisierung in ``server/hub.py``.

Hintergrund (Wave 2 der Hub-Migration, s. CHANGELOG): `Hub._safe_send` hält pro
WebSocket ein `asyncio.Lock`, weil mehrere unabhängige Tasks (z. B. ein
Scan-Ergebnis-Push und ein Settings-Broadcast) denselben Helfer-WS gleichzeitig
treffen können. Ohne das Lock könnten die zugrunde liegenden `send_json`-Aufrufe
am ASGI-Layer interleaven. Dieser Test simuliert genau diese Konkurrenzsituation
mit einer Fake-WebSocket, die zwischen "Sendebeginn" und "Sendeende" bewusst
mehrfach die Kontrolle abgibt (`await asyncio.sleep(0)`), und prüft, dass die
beiden Sends dennoch strikt nacheinander (nicht verschachtelt) beim Fake-Client
ankommen.

Rein async/RAM — kein echter ASGI-WebSocket, kein Netzwerk, keine Produktion.
"""

from __future__ import annotations

import asyncio

from server.hub import Hub
from server.state import AppState, HelperSession


class _FakeWebSocket:
    """Zeichnet Beginn/Ende jedes ``send_json`` auf und gibt dazwischen mehrfach
    die Kontrolle ab — genug Gelegenheit für ein Interleaving, falls der
    Aufrufer nicht seriell serialisiert."""

    def __init__(self, log: list[tuple[str, str]], ticks: int = 3) -> None:
        self.log = log
        self.ticks = ticks

    async def send_json(self, msg: dict) -> None:
        label = msg["type"]
        self.log.append(("start", label))
        for _ in range(self.ticks):
            await asyncio.sleep(0)
        self.log.append(("end", label))


def _assert_non_overlapping(log: list[tuple[str, str]]) -> None:
    """Jedes 'start' muss von seinem eigenen 'end' gefolgt werden, bevor ein
    weiteres 'start' auftaucht — sonst haben sich zwei Sends überlappt."""
    open_label: str | None = None
    for kind, label in log:
        if kind == "start":
            assert open_label is None, f"Sends überlappen sich: {log!r}"
            open_label = label
        else:
            assert open_label == label, f"Sends überlappen sich: {log!r}"
            open_label = None
    assert open_label is None, f"Sequenz endet mitten in einem Send: {log!r}"


def _build_state_with_helper(ws: _FakeWebSocket) -> AppState:
    state = AppState()
    helper = HelperSession(token="h1", name="Helfer", ws=ws)
    state.helper_sessions["h1"] = helper
    return state


def test_hub_serializes_concurrent_sends_to_same_connection() -> None:
    """Zwei unabhängige Hub-Sends (send_websocket + broadcast_settings) auf
    denselben Helfer-WS dürfen sich nicht überlappen."""
    log: list[tuple[str, str]] = []
    ws = _FakeWebSocket(log)
    state = _build_state_with_helper(ws)
    hub = Hub()

    async def run() -> None:
        await asyncio.gather(
            hub.send_websocket(ws, {"type": "scan_result"}),
            hub.broadcast_settings(state),
        )

    asyncio.run(run())

    # broadcast_settings sendet genau eine "settings"-Nachricht an unseren
    # (einzigen, unzugewiesenen) Helfer.
    labels = {label for _, label in log}
    assert labels == {"scan_result", "settings"}
    _assert_non_overlapping(log)


def test_unlocked_send_would_interleave() -> None:
    """Gegenprobe: patcht man ``_safe_send`` so, dass es das Lock umgeht (also
    exakt das alte, fehlerhafte Verhalten vor Wave 2), interleaven die beiden
    Sends tatsächlich. Beweist, dass der obige Test nicht vakuum ist — er
    würde gegen eine ungesicherte Implementierung fehlschlagen."""
    log: list[tuple[str, str]] = []
    ws = _FakeWebSocket(log)
    state = _build_state_with_helper(ws)
    hub = Hub()

    async def _unlocked_safe_send(target_ws, msg: dict) -> bool:
        # Bewusst OHNE das Per-Verbindung-Lock — simuliert den Wave-1-Zustand.
        try:
            await target_ws.send_json(msg)
            return True
        except Exception:
            return False

    hub._safe_send = _unlocked_safe_send  # type: ignore[method-assign]

    async def run() -> None:
        await asyncio.gather(
            hub.send_websocket(ws, {"type": "scan_result"}),
            hub.broadcast_settings(state),
        )

    asyncio.run(run())

    labels = {label for _, label in log}
    assert labels == {"scan_result", "settings"}
    # Ohne Lock interleaven die beiden Sends: start/start/end/end statt
    # start/end/start/end.
    starts_before_first_end = 0
    for kind, _ in log:
        if kind == "start":
            starts_before_first_end += 1
        else:
            break
    assert starts_before_first_end == 2, (
        f"Erwartetes Interleaving (zwei 'start' vor dem ersten 'end') blieb aus: {log!r}"
    )
