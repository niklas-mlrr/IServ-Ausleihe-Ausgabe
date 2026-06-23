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
        # Jede Host-Statusänderung kann die Warteschlange verändert haben –
        # darum die aktuelle Größe live an alle unzugewiesenen Scanner schicken.
        await self.broadcast_queue_size(s)

    async def broadcast_queue_size(self, state: AppState | None = None) -> None:
        s = state or get_state()
        qsize = s.pending_count()
        for helper in list(s.helper_sessions.values()):
            if helper.student_id is None and helper.ws is not None:
                try:
                    await helper.ws.send_json({"type": "queue_update", "queue_size": qsize})
                except Exception:
                    helper.ws = None

    async def broadcast_settings(self, state: AppState | None = None) -> None:
        """Helfer-relevante Settings an alle verbundenen Scanner schicken.

        Aktuell nur der Host-Default „Schüler-Leihschein" (2. Seite), den der
        Druck-Dialog im Helferclient als Vorauswahl nutzt.
        """
        s = state or get_state()
        msg = {"type": "settings", "slip_second_page": s.slip_second_page_default}
        for helper in list(s.helper_sessions.values()):
            if helper.ws is not None:
                try:
                    await helper.ws.send_json(msg)
                except Exception:
                    helper.ws = None

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
