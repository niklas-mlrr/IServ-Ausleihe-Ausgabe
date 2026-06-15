from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_config
from .iserv_client import IsServClient
from .routes.api import router as api_router
from .routes.ws import router as ws_router
from .sessions import sweep_expired_sessions
from .state import get_state

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    state = get_state()

    state.iserv = IsServClient(cfg.iserv_domain, cfg.iserv_username, cfg.iserv_password)

    from automation.worker import WorkerPool
    pool = WorkerPool(
        n=cfg.worker_contexts,
        domain=cfg.iserv_domain,
        username=cfg.iserv_username,
        password=cfg.iserv_password,
    )
    try:
        await pool.start()
        state.worker_pool = pool
        log.info("WorkerPool gestartet (%d Contexts)", cfg.worker_contexts)
        # Read-only Selektor-Drift-Check (non-fatal) — warnt, falls IServ-DOM sich änderte.
        try:
            await pool.check_selectors()
        except Exception:
            log.exception("Selektor-Canary fehlgeschlagen (non-fatal)")
    except Exception:
        log.exception("WorkerPool-Start fehlgeschlagen — weiter ohne Playwright")
        state.worker_pool = None

    sweeper = asyncio.create_task(sweep_expired_sessions())
    log.info("Modus-B-Timeout-Sweeper gestartet")

    yield

    sweeper.cancel()
    try:
        await sweeper
    except asyncio.CancelledError:
        pass

    if state.worker_pool:
        await state.worker_pool.stop()
        log.info("WorkerPool gestoppt")


def create_app() -> FastAPI:
    app = FastAPI(title="Ausleihe-Ausgabe", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(ws_router)
    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
