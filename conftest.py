# Sorgt dafür, dass das Projekt-Root im sys.path liegt, damit die Tests
# `server.*` und `automation.*` importieren können (Projekt ist package=false).

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server.app import create_app


@pytest.fixture
def client() -> TestClient:
    """Echter HTTP-Client (Starlette TestClient) auf einer frischen App-Instanz.

    Bewusst KEIN `with TestClient(app)` — das würde den Lifespan starten, und
    der loggt einen echten Playwright-WorkerPool gegen die IServ-PRODUKTION
    ein (server/app.py:lifespan). Ohne Context-Manager laufen Startup/Shutdown
    nie; der TestClient kann trotzdem ganz normal Requests schicken (Starlette
    routet direkt über den ASGI-Callable, Depends/Cookie-Injection inklusive).
    Jeder Test bekommt eine frische `create_app()`-Instanz, aber der globale
    State (`server.state.get_state()`) bleibt ein Singleton — Tests, die den
    State beeinflussen, patchen ihn über `monkeypatch.setattr`.
    """
    app = create_app()
    return TestClient(app)
