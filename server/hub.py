from __future__ import annotations

import asyncio
import json
import logging
import weakref

from fastapi import WebSocket

from .book_order import get_book_order_for_form
from .state import AppState, HelperSession, get_state

log = logging.getLogger(__name__)


class Hub:
    """WebSocket-Verteiler für Host- und Scanner-Verbindungen."""

    def __init__(self) -> None:
        # Pro Verbindung ein Lock: `broadcast_host`, `broadcast_settings`,
        # `broadcast_queue_size` und `send_scanner` laufen als unabhängige
        # Tasks (z. B. ein Scan-Ergebnis-Push zeitgleich mit einem
        # Settings-Broadcast an denselben Helfer) und können denselben
        # WebSocket gleichzeitig treffen. Ohne Serialisierung können die
        # zugrunde liegenden ASGI-Sends interleaven oder in falscher
        # Reihenfolge beim Client ankommen. `WeakKeyDictionary`, damit Locks
        # toter Verbindungen nicht dauerhaft im Speicher bleiben.
        self._ws_locks: "weakref.WeakKeyDictionary[object, asyncio.Lock]" = weakref.WeakKeyDictionary()

    def _lock_for(self, ws: object) -> asyncio.Lock:
        lock = self._ws_locks.get(ws)
        if lock is None:
            lock = asyncio.Lock()
            self._ws_locks[ws] = lock
        return lock

    async def _safe_send(self, ws: object, msg: dict) -> bool:
        """Sendet serialisiert pro Verbindung. True bei Erfolg, False wenn tot."""
        async with self._lock_for(ws):
            try:
                await ws.send_json(msg)
                return True
            except Exception:
                return False

    async def broadcast_host(self, msg: dict, state: AppState | None = None) -> None:
        s = state or get_state()
        dead = []
        for ws in list(s.host_ws_connections):
            if not await self._safe_send(ws, msg):
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
        queue = s.pending_queue_as_list()
        for helper in list(s.helper_sessions.values()):
            if helper.student_id is None and helper.ws is not None:
                if not await self._safe_send(
                    helper.ws, {"type": "queue_update", "queue_size": qsize, "queue": queue}
                ):
                    helper.ws = None

    async def broadcast_settings(self, state: AppState | None = None) -> None:
        """Helfer-relevante Settings an alle verbundenen Scanner schicken.

        Der Host-Default „Schüler-Leihschein" (2. Seite) für den Druck-Dialog und
        die Bücher-Reihenfolge für die Scanner-Liste. Die Reihenfolge wird **pro
        Helfer** anhand des Jahrgangs seines aktuell zugewiesenen Schülers
        ermittelt (`get_book_order_for_form`) — nicht die eine globale
        `state.book_order` für alle. Nötig für klassenübergreifende
        Warteschlangen (einzeln hinzugefügte Schüler, „Test Config") mit
        Schülern aus verschiedenen Jahrgängen; Helfer ohne zugewiesenen Schüler
        bekommen den Fallback `state.book_order`."""
        s = state or get_state()
        for helper in list(s.helper_sessions.values()):
            if helper.ws is None:
                continue
            student = s.find_student(helper.student_id) if helper.student_id is not None else None
            book_order = await get_book_order_for_form(s, student.form) if student else s.book_order
            msg = {
                "type": "settings",
                "slip_second_page": s.slip_second_page_default,
                "book_order": book_order,
            }
            if not await self._safe_send(helper.ws, msg):
                helper.ws = None

    async def send_scanner(self, token: str, msg: dict, state: AppState | None = None) -> None:
        s = state or get_state()
        helper = s.helper_sessions.get(token)
        if not helper or helper.ws is None:
            return
        if not await self._safe_send(helper.ws, msg):
            helper.ws = None
            log.warning("Scanner WS für Token %s ist tot", token)


_hub = Hub()


def get_hub() -> Hub:
    return _hub
