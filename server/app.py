from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_config
from .iserv_client import IsServClient
from .routes.api import router as api_router
from .routes.ws import router as ws_router
from .runtime import Runtime, RuntimeBindingMiddleware
from .sessions import (
    gen_registration_code,
    persist_helpers,
    persist_printer_displays,
    persist_printer_scanners,
    persist_scan_stations,
    server_lan_ip,
    sweep_expired_sessions,
    sweep_scan_stations,
)
from .state import get_state

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent.parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime: Runtime = app.state.runtime
    # Lifespan-created tasks inherit this context, so the sweeper and print
    # queue keep using this exact app's state/hub after startup.
    with runtime.activate():
        cfg = get_config()
        state = get_state()

        # Startzeit DIESES Laufs — Basis für die Persistenz-Regel „nie
        # verbundene Geräte erst nach >5 min Laufzeit verwerfen"
        # (s. sessions.persist_*). Hier gesetzt, nicht erst im AppState-
        # Konstruktor, damit ein früh erzeugter State die Uhr nicht vorzieht.
        state.started_at_monotonic = time.monotonic()

        state.iserv = IsServClient(
            cfg.iserv_domain,
            cfg.iserv_username,
            cfg.iserv_password,
            read_timeout_s=cfg.iserv_read_timeout_s,
        )

    # Bücher-Reihenfolge/Ausblendung aus letzter Sitzung laden (Persistenz):
    # reine Datei-IO, non-fatal — Fehler lässt den State leer wie ohne Persistenz.
        from .booklist_store import load as load_booklist_state

        try:
            (
                state.caches.book_orders_by_grade,
                state.caches.hidden_isbns_by_grade,
                state.caches.empty_isbns,
            ) = load_booklist_state()
            log.info(
                "Bücher-Reihenfolge/Ausblendung/Bestand-leer geladen: "
                "%d Jahrgänge, %d Bestand-leer",
                len(state.caches.book_orders_by_grade),
                len(state.caches.empty_isbns),
            )
        except Exception:
            log.exception("Laden der booklist-Persistenz fehlgeschlagen (non-fatal)")

    # Server-IP-Fingerprint für die Helfer-/Display-/Stations-Persistenz (s.
    # sessions.persist_helpers & Co.): weicht sie vom beim Speichern erkannten
    # Netz ab, wird unten gar nicht erst geladen (alte Token stecken in URLs,
    # die auf die alte IP zeigen — auf einem anderen Netz ohnehin tot).
        current_ip = server_lan_ip()

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

    # Helfer aus letzter Sitzung laden (Persistenz): reine Datei-IO, non-fatal.
    # Der alte Token bleibt gültig — ein Handy mit noch offener /scan?token=…-
    # Seite verbindet sich nach dem Neustart wieder mit demselben Helfer.
        from .helper_store import load as load_helper_state
        from .state import HelperSession

        try:
            for token, name in load_helper_state(current_ip):
                state.helper_sessions[token] = HelperSession(token=token, name=name)
            log.info("Helfer geladen: %d", len(state.helper_sessions))
        except Exception:
            log.exception("Laden der Helfer-Persistenz fehlgeschlagen (non-fatal)")

    # Freigeschaltete Drucker-Displays aus letzter Sitzung laden: Drucker-
    # Zuweisung ist über den (laufzeitstabilen) Drucker-`name` gespeichert und
    # wird hier auf die frisch geladenen Pool-`id`s aufgelöst (s.
    # printer_display_store.py). Non-fatal, reine Datei-IO.
        from .printer_display_store import load as load_printer_display_state
        from .state import PrinterDisplaySession

        try:
            printer_ids_by_name: dict[str | None, str] = {}
            for p in state.settings.printers:
                printer_ids_by_name.setdefault(p.name, p.id)
            for entry in load_printer_display_state(current_ip):
                names = entry["assigned_printer_names"]
                assigned_printer_ids = (
                    None
                    if names is None
                    else [printer_ids_by_name[n] for n in names if n in printer_ids_by_name]
                )
                # Gemeinsame Drucker+Scanner-Reihenfolge zurück auf IDs
                # auflösen: Drucker über den Namen (wie oben), Scanner über
                # ihre (stabile) `scanner_id` direkt — unbekannte Namen/IDs
                # fallen einfach weg (AppState._ordered_display_items hängt
                # tatsächlich zugewiesene, aber nicht gelistete Items ohnehin
                # stabil ans Ende an).
                item_order = []
                for item in entry.get("item_order", []):
                    if item["kind"] == "printer" and item["name"] in printer_ids_by_name:
                        item_order.append(f"printer:{printer_ids_by_name[item['name']]}")
                    elif item["kind"] == "scanner":
                        item_order.append(f"scanner:{item['id']}")
                state.printer_displays[entry["display_id"]] = PrinterDisplaySession(
                    display_id=entry["display_id"],
                    registration_code=gen_registration_code(),
                    authorized=True,
                    label=entry["label"],
                    theme=entry["theme"],
                    assigned_printer_ids=assigned_printer_ids,
                    # `assigned_scanner_ids` referenziert Scanner über ihren
                    # (stabilen) Token direkt — kein Namens-Remapping nötig,
                    # s. sessions.persist_printer_displays.
                    assigned_scanner_ids=entry.get("assigned_scanner_ids"),
                    item_order=item_order,
                )
            log.info("Drucker-Displays geladen: %d", len(state.printer_displays))
        except Exception:
            log.exception("Laden der Drucker-Display-Persistenz fehlgeschlagen (non-fatal)")

    # Freigeschaltete Scan-Stationen aus letzter Sitzung laden. Non-fatal,
    # reine Datei-IO.
        from .scan_station_store import load as load_scan_station_state
        from .state import ScanStationSession

        try:
            for entry in load_scan_station_state(current_ip):
                state.scan_stations[entry["station_id"]] = ScanStationSession(
                    station_id=entry["station_id"],
                    registration_code=gen_registration_code(),
                    authorized=True,
                    label=entry["label"],
                    theme=entry["theme"],
                    input_mode=entry["input_mode"],
                )
            log.info("Scan-Stationen geladen: %d", len(state.scan_stations))
        except Exception:
            log.exception("Laden der Scan-Station-Persistenz fehlgeschlagen (non-fatal)")

    # Freigeschaltete Drucker-Scanner aus letzter Sitzung laden. Non-fatal,
    # reine Datei-IO.
        from .printer_scanner_store import load as load_printer_scanner_state
        from .state import PrinterScannerSession

        try:
            for entry in load_printer_scanner_state(current_ip):
                state.printer_scanners[entry["scanner_id"]] = PrinterScannerSession(
                    scanner_id=entry["scanner_id"],
                    registration_code=gen_registration_code(),
                    authorized=True,
                    label=entry["label"],
                    theme=entry["theme"],
                    input_mode=entry["input_mode"],
                )
            log.info("Drucker-Scanner geladen: %d", len(state.printer_scanners))
        except Exception:
            log.exception("Laden der Drucker-Scanner-Persistenz fehlgeschlagen (non-fatal)")

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

        # Eigener, feinerer Takt für die Scan-Stationen: ihr Leerlauf-TTL ist
        # mit 30 s kürzer als der 30-s-Takt des Modus-B-Sweepers.
        station_sweeper = asyncio.create_task(sweep_scan_stations())
        log.info("Scan-Station-Sweeper gestartet")

    # Interne Druckerwarteschlange (Rollen-Rangfolge, 2-in-flight, OS-Completion-
    # Polling) — startet den Worker-Task, der Druckaufträge serialisiert.
        state.print_queue.start()
        log.info("Druckerwarteschlange gestartet")

        yield

        # Letzter Persistenz-Stand vor dem Beenden: entfernt Einträge, die in
        # diesem kompletten Lauf nie verbunden waren (s. sessions.persist_*),
        # auch wenn seit ihrem Laden kein anderes Ereignis mehr geschrieben
        # hat — sonst würden sie beim nächsten Start erneut geladen. Lief der
        # Server kürzer als 5 min, wird nichts verworfen (zu kurz für einen
        # verlässlichen Reconnect).
        persist_helpers(state)
        persist_printer_displays(state)
        persist_scan_stations(state)
        persist_printer_scanners(state)

        sweeper.cancel()
        station_sweeper.cancel()
        for task in (sweeper, station_sweeper):
            try:
                await task
            except asyncio.CancelledError:
                pass

        await state.print_queue.stop()
        log.info("Druckerwarteschlange gestoppt")

        if state.worker_pool:
            await state.worker_pool.stop()
            log.info("WorkerPool gestoppt")


# Seiten, die auch ohne ".html" erreichbar sein sollen (Clean URLs).
_CLEAN_PAGES = ("host", "scan", "student", "qr-display", "teacher")


def _page_handler(path: Path):
    """Handler ohne Parameter (sonst würde FastAPI einen Query-Param ableiten)."""

    async def handler() -> FileResponse:
        return FileResponse(path)

    return handler


def create_app() -> FastAPI:
    app = FastAPI(title="Ausleihe-Ausgabe", lifespan=lifespan)
    app.state.runtime = Runtime()
    app.add_middleware(RuntimeBindingMiddleware)
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

    @app.middleware("http")
    async def _no_cache_static(request: Request, call_next):
        """Statische Web-Dateien (host.js/scan.js/CSS/HTML) dürfen vom Browser
        nicht heuristic-cachen — sonst kommen Code-Änderungen (z. B. neue
        Einstellungs-Felder) bei den Host-Rechnern nicht an. `no-cache` erzwingt
        eine Revalidierung; StaticFiles liefert Last-Modified, so dass unver-
        änderte Dateien mit 304 beantwortet werden (kein Re-Download nötig).
        API-/WS-Routen bleiben unangetastet (JSON wird ohnehin nicht gecacht)."""
        response = await call_next(request)
        if not request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    return app


app = create_app()
