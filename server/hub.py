from __future__ import annotations

import json
import logging

from fastapi import WebSocket

from .state import AppState, HelperSession, get_state

log = logging.getLogger(__name__)


class Hub:
    """WebSocket-Verteiler für Host- und Scanner-Verbindungen."""

    async def broadcast_host(self, msg: dict, state: AppState | None = None) -> None:
        s = state or get_state()
        dead = []
        for ws in list(s.host_ws_connections):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            try:
                s.host_ws_connections.remove(ws)
            except ValueError:
                pass

    async def send_scanner(self, token: str, msg: dict, state: AppState | None = None) -> None:
        s = state or get_state()
        helper = s.helper_sessions.get(token)
        if not helper or helper.ws is None:
            return
        try:
            await helper.ws.send_json(msg)
        except Exception:
            helper.ws = None
            log.warning("Scanner WS für Token %s ist tot", token)


_hub = Hub()


def get_hub() -> Hub:
    return _hub
