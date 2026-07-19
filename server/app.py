from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
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

    # Bücher-Reihenfolge/Ausblendung aus letzter Sitzung laden (Persistenz):
    # reine Datei-IO, non-fatal — Fehler lässt den State leer wie ohne Persistenz.
    from .booklist_store import load as load_booklist_state

    try:
        state.caches.book_orders_by_grade, state.caches.hidden_isbns_by_grade = (
            load_booklist_state()
        )
        log.info(
            "Bücher-Reihenfolge/Ausblendung geladen: %d Jahrgänge",
            len(state.caches.book_orders_by_grade),
        )
    except Exception:
        log.exception("Laden der booklist-Persistenz fehlgeschlagen (non-fatal)")

    # Liegengebliebene Druck-Temp-PDFs vom letzten Lauf wegräumen (win-default-Leak).
    from .printing import cleanup_stale_print_tempfiles

    try:
        cleanup_stale_print_tempfiles()
    except Exception:
        log.exception("Aufräumen alter Druck-Temp-PDFs fehlgeschlagen (non-fatal)")

    # Drucker-Pool aus letzter Sitzung laden (Persistenz): reine Datei-IO +
    # Validierung gegen die Geräte-Druckerliste, non-fatal — Fehler lässt den
    # State beim ersten-Start-Default ([Standarddrucker]).
    from .printer_store import load as load_printer_state
    from .printing import list_printers

    try:
        info = await list_printers(cfg.print_backend)
        state.settings.printers = load_printer_state(info.get("printers") or [])
        log.info(
            "Drucker-Pool geladen: %d Drucker (%d dem Gerät bekannt)",
            len(state.settings.printers),
            len(info.get("printers") or []),
        )
    except Exception:
        log.exception("Laden der Drucker-Persistenz fehlgeschlagen (non-fatal)")

    from automation.worker import WorkerPool

    pool = WorkerPool(
        n=cfg.worker_contexts,
        domain=cfg.iserv_domain,
        username=cfg.iserv_username,
        password=cfg.iserv_password,
        headless=cfg.headless,
        slow_mo_ms=cfg.slow_mo_ms,
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

    # Interne Druckerwarteschlange (Rollen-Rangfolge, 2-in-flight, OS-Completion-
    # Polling) — startet den Worker-Task, der Druckaufträge serialisiert.
    state.print_queue.start()
    log.info("Druckerwarteschlange gestartet")

    yield

    sweeper.cancel()
    try:
        await sweeper
    except asyncio.CancelledError:
        pass

    await state.print_queue.stop()
    log.info("Druckerwarteschlange gestoppt")

    if state.worker_pool:
        await state.worker_pool.stop()
        log.info("WorkerPool gestoppt")


# Seiten, die auch ohne ".html" erreichbar sein sollen (Clean URLs).
_CLEAN_PAGES = ("host", "scan", "student", "qr-display")


def _page_handler(path: Path):
    """Handler ohne Parameter (sonst würde FastAPI einen Query-Param ableiten)."""

    async def handler() -> FileResponse:
        return FileResponse(path)

    return handler


def create_app() -> FastAPI:
    app = FastAPI(title="Ausleihe-Ausgabe", lifespan=lifespan)
    app.include_router(api_router)
    app.include_router(ws_router)
    if WEB_DIR.is_dir():
        # Clean-URL-Routen VOR dem StaticFiles-Mount registrieren (der Mount auf "/"
        # ist ein Catch-all). Die ".html"-URLs bleiben über StaticFiles gültig.
        for page in _CLEAN_PAGES:
            html = WEB_DIR / f"{page}.html"
            if html.is_file():
                app.add_api_route(f"/{page}", _page_handler(html), include_in_schema=False)
        app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
