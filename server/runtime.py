"""Application-local services and ASGI context binding.

State, WebSocket hub and their background tasks are intentionally in-memory,
but they must belong to one FastAPI app instance rather than module globals.
"""

from __future__ import annotations

from contextlib import contextmanager

from .hub import Hub, bind_hub, reset_hub
from .state import AppState, bind_state, reset_state


class Runtime:
    def __init__(self) -> None:
        self.state = AppState()
        self.hub = Hub()

    @contextmanager
    def activate(self):
        state_token = bind_state(self.state)
        hub_token = bind_hub(self.hub)
        try:
            yield self
        finally:
            reset_hub(hub_token)
            reset_state(state_token)


class RuntimeBindingMiddleware:
    """Bind the app-local runtime for HTTP and WebSocket scopes alike."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        root_app = scope.get("app")
        runtime = getattr(getattr(root_app, "state", None), "runtime", None)
        if runtime is None:
            await self.app(scope, receive, send)
            return
        with runtime.activate():
            await self.app(scope, receive, send)
