"""Bücherlisten / jahrgangsweite Bücher-Reihenfolge & Ausblendung.

Enthält auch die geteilten Helfer `_ensure_class_catalog` (von
`routes/classes.open_class` genutzt) und `_persist_booklist_settings`.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from ..book_order import normalize_book_order
from ..booklist_store import save as save_booklist_state
from ..hub import get_hub
from ..sessions import repush_booklist
from ..state import get_state
from ._deps import BooklistHiddenRequest, BooklistOrderRequest, host_router

log = logging.getLogger(__name__)


def _student_in_grade(state, student_id: int, grade: int) -> bool:
    """Hat dieser Schüler (über seine Klasse/form) den Jahrgang ``grade``?

    Nutzt den `form_catalog_cache` (beim Laden des Schülers über
    `hydrate_student_info` → `get_book_order_for_form` → `_grade_and_catalog`
    befüllt). Ein Schüler ohne Cache-Eintrag wurde noch nie geladen → hat keine
    angezeigte Bücherliste → muss nicht repushed werden."""
    student = state.find_student(student_id)
    if student is None:
        return False
    cached = state.caches.form_catalog_cache.get(student.form)
    return bool(cached and cached[0] == grade)


# ---------------------------------------------------------------------------
# Klassenweite Bücher-Reihenfolge (Scanner-Anzeige) — konfiguriert wird sie nur
# noch jahrgangsweit im Einstellungen-Dialog (`/api/booklist-order`); hier nur
# noch der Katalog-Aufbau für die aktive Klasse (`select_class` ruft ihn auf).
# ---------------------------------------------------------------------------


def _persist_booklist_settings(state) -> None:
    """Aktuellen jahrgangsweiten Reihenfolge-/Ausblendungs-Stand auf die
    Server-Persistenz (`data/booklist_settings.json`) wegschreiben. Non-fatal —
    Schreibfehler werden geloggt, der In-Memory-State bleibt Leading und der
    Endpoint crasht nicht."""
    try:
        save_booklist_state(state.caches.book_orders_by_grade, state.caches.hidden_isbns_by_grade)
    except Exception:
        log.exception("Speichern der booklist-Einstellungen fehlgeschlagen (non-fatal)")


async def _ensure_class_catalog(state, context_id: str | None = None) -> None:
    """Katalog (ausleihbare Jahrgangs-Bücher) für einen Klassen-Kontext bauen und
    cachen, falls noch nicht für dessen Klasse geschehen. `book_order` wird beim
    ersten Bauen aus der jahrgangsweit gesetzten Reihenfolge übernommen (falls im
    Einstellungen-Dialog vorkonfiguriert), sonst mit der Default-Reihenfolge
    (subject/title) initialisiert. `context_id=None` → aktiver Kontext (Kompat,
    z. B. über /api/select-class)."""
    ctx = state.ctx_or_active(context_id)
    if ctx is None or not ctx.form:
        return
    if ctx.class_catalog_form == ctx.form and ctx.class_catalog:
        return
    grade, catalog = await state.iserv.get_class_book_catalog(ctx.form, state.selected_schoolyear)
    ctx.class_catalog = catalog
    ctx.class_catalog_form = ctx.form
    ctx.class_catalog_grade = grade
    catalog_isbns = [b["isbn"] for b in catalog]
    if grade is not None:
        state.caches.form_catalog_cache[ctx.form] = (grade, catalog_isbns)
    stored = state.caches.book_orders_by_grade.get(grade) if grade is not None else None
    if stored:
        ctx.book_order = normalize_book_order(catalog_isbns, stored)
    elif not ctx.book_order:
        ctx.book_order = catalog_isbns


@host_router.get("/api/booklists")
async def list_booklists() -> dict:
    """Alle Bücherlisten (Jahrgänge) des gewählten Schuljahrs — für die Reiter im
    Einstellungen-Dialog. Read-only (ein GET gegen IServ), kein DB-Write."""
    state = get_state()
    try:
        booklists = await state.iserv.get_booklists_overview(state.selected_schoolyear)
    except Exception as e:
        log.exception("Bücherlisten konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"schoolyear": state.selected_schoolyear, "booklists": booklists}


@host_router.get("/api/booklist-order")
async def get_booklist_order(grade: int) -> dict:
    """Ausleihbare Bücher eines Jahrgangs + aktuelle (ggf. vorkonfigurierte)
    Reihenfolge. Read-only, kein DB-Write."""
    state = get_state()
    try:
        catalog = await state.iserv.get_booklist_catalog_by_grade(grade, state.selected_schoolyear)
    except Exception as e:
        log.exception("Jahrgangs-Bücherliste konnte nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    catalog_isbns = [b["isbn"] for b in catalog]
    stored = state.caches.book_orders_by_grade.get(grade)
    order = normalize_book_order(catalog_isbns, stored) if stored else catalog_isbns
    hidden = sorted(state.caches.hidden_isbns_by_grade.get(grade, set()) & set(catalog_isbns))
    return {"grade": grade, "catalog": catalog, "order": order, "hidden": hidden}


@host_router.post("/api/booklist-order")
async def set_booklist_order(body: BooklistOrderRequest) -> dict:
    """Jahrgangsweite Bücher-Reihenfolge (aus dem Einstellungen-Dialog) speichern.

    Reiner In-Memory-State (kein DB-/IServ-Write). `broadcast_settings()` schickt
    jedem verbundenen Helfer die für **seinen eigenen** zugewiesenen Schüler
    passende Reihenfolge (per Jahrgang ermittelt über `get_book_order_for_form`)
    — funktioniert daher auch bei klassenübergreifenden Warteschlangen mit
    Schülern aus verschiedenen Jahrgängen (z. B. „Test Config"), nicht nur bei
    einer komplett geladenen Klasse. Gehört ein offener Klassen-Kontext zu diesem
    Jahrgang, wird dessen `book_order` + der Host selbst (`broadcast_host`) live
    nachgezogen, damit ein Reload des Hosts konsistent bleibt.
    """
    state = get_state()
    grade = body.grade
    requested = body.order
    if grade is None or requested is None:
        raise HTTPException(400, "grade (int) und order (Liste) erforderlich")
    try:
        catalog = await state.iserv.get_booklist_catalog_by_grade(grade, state.selected_schoolyear)
    except Exception as e:
        log.exception("Jahrgangs-Bücherliste konnte nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    catalog_isbns = [b["isbn"] for b in catalog]
    order = normalize_book_order(catalog_isbns, requested)
    state.caches.book_orders_by_grade[grade] = order
    _persist_booklist_settings(state)
    hub = get_hub()
    # Jeder Helfer bekommt (unabhängig von der aktiven Klasse) seine eigene,
    # zum Jahrgang seines zugewiesenen Schülers passende Reihenfolge.
    await hub.broadcast_settings()
    # Alle gerade offenen Klassen desselben Jahrgangs live nachziehen (je
    # Klassen-Tab seinen eigenen book_order-Stand).
    touched = False
    for c in state.contexts.values():
        if c.class_catalog_grade == grade:
            c.book_order = list(order)
            touched = True
    if touched:
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "grade": grade, "order": order}


@host_router.post("/api/booklist-hidden")
async def set_booklist_hidden(body: BooklistHiddenRequest) -> dict:
    """Ausgeblendete Buchreihen eines Jahrgangs (Einstellungen-Dialog, „Ausblenden"-
    Button je Buch) setzen.

    Reiner In-Memory-State (kein DB-/IServ-Write, kein PUT/POST gegen IServ —
    nur der lesende Katalog-Check zur ISBN-Validierung). Ausgeblendete Reihen
    gelten für neu geladene/neu verbundene Schüler dieses Jahrgangs nicht mehr
    als „vorgemerkt" (`apply_hidden_books` in `sessions.py`/`routes/ws.py`) und
    sind damit auch nicht mehr buchbar (`evaluate_scan_for_booking` sieht die
    ISBN nicht mehr in `vormerk_isbns`)."""
    state = get_state()
    grade = body.grade
    requested = body.hidden
    if grade is None or requested is None:
        raise HTTPException(400, "grade (int) und hidden (Liste) erforderlich")
    try:
        catalog = await state.iserv.get_booklist_catalog_by_grade(grade, state.selected_schoolyear)
    except Exception as e:
        log.exception("Jahrgangs-Bücherliste konnte nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    catalog_isbns = {b["isbn"] for b in catalog}
    hidden = {isbn for isbn in requested if isinstance(isbn, str) and isbn in catalog_isbns}
    state.caches.hidden_isbns_by_grade[grade] = hidden
    _persist_booklist_settings(state)
    hub = get_hub()
    await hub.broadcast_settings()
    # Allen aktiven Clients (Modus A Helfer + Modus B Schüler), deren
    # zugewiesener Schüler in diesem Jahrgang ist, die neu gefilterte Bücherliste
    # live nachschieben — Ausblenden wirkt damit sofort auf dem Gerät, nicht erst
    # beim nächsten Schülerladen. `booklist_update` ersetzt nur die Liste und
    # lässt den Scan-Fortschritt am Client unangetastet (s. `repush_booklist`).
    tasks: list[asyncio.Task] = []
    for helper in state.helper_sessions.values():
        if helper.student_id is None or helper.ws is None:
            continue
        if _student_in_grade(state, helper.student_id, grade):
            tasks.append(
                asyncio.create_task(
                    repush_booklist(state, hub, helper.student_id, helper, helper=True)
                )
            )
    for session in state.student_sessions.values():
        if session.student_id is None or session.ws is None or session.state != "paired":
            continue
        if _student_in_grade(state, session.student_id, grade):
            tasks.append(
                asyncio.create_task(
                    repush_booklist(state, hub, session.student_id, session, helper=False)
                )
            )
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        # Y der „X/Y Bücher"-Queue-Anzeige hat sich geändert (ausgeblendete
        # Reihen zählen nicht mehr) → Host-Snapshot neu pushen.
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "grade": grade, "hidden": sorted(hidden)}
