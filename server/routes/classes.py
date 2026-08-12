"""Schuljahr / Klassen / Klassen-Kontexte (Multi-Tab) + Einzel-Schüler + Test-Config."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import HTTPException

from ..book_order import get_hidden_isbns_for_form
from ..hub import get_hub
from ..sessions import (
    apply_hidden_books,
    booking_isbn_sets_from_info,
    broadcast_printer_displays,
    end_student,
    revoke_all_teacher_sessions,
    revoke_teacher_sessions_for_context,
    teardown_students,
)
from ..state import AppState, ClassContext, QueueStudent, get_state
from ._deps import (
    AddStudentRequest,
    CloseClassRequest,
    ContextDoneOptionsRequest,
    ContextIdBody,
    ContextLiveAusgabeRequest,
    ContextPrintersRequest,
    ContextSlipTriggerRequest,
    OpenClassRequest,
    SelectSchoolyearRequest,
    host_router,
)
from .booklists import _ensure_class_catalog

log = logging.getLogger(__name__)

# Beim Klassen-Laden wählbare Sofort-fertig-Filter, s. `_load_student_flags`.
_AUTO_DONE_FILTERS = {
    "not_enrolled", "unpaid", "remission_pending", "exemption_pending", "all_lent",
}


def _resolve_allowed_printers(printers: list[str] | None) -> set[str] | None:
    """Client-Druckerauswahl → Allowlist. `None` (Feld fehlt) = kein Filter
    (alle Pool-Drucker, Default, kompatibel mit Öffnen ohne Angabe / Tests).
    Eine explizite Liste — auch eine leere — wird als Menge der übergebenen
    Drucker-IDs interpretiert (Dubletten/Leerstring herausgefiltert). Eine
    leere Liste bedeutet also bewusst „kein Drucker ausgewählt": keine
    Vorauswahl für den Helfer/Host-Druckdialog; der Druck bleibt über die
    manuelle Auswahl im Druckdialog weiterhin möglich (s. slips.py / ws.py
    `_handle_print`, wo eine explizite `printers`-Auswahl die Allowlist
    übersteuert)."""
    if printers is None:
        return None
    return {pid.strip() for pid in printers if pid and pid.strip()}


def _require_printer_for_live(allowed: set[str] | None) -> None:
    """Kopplung Drucker ↔ Live-Ausgabe pro Klasse: Live-Ausgabe benötigt
    mindestens einen Drucker. `None` = alle Pool-Drucker (gilt als „gewählt");
    eine **explizit leere Menge** bei aktiver Live-Ausgabe → 400 (gleiche
    Meldung wie der clientseitige rote Hinweis)."""
    if allowed is not None and not allowed:
        raise HTTPException(400, "Es ist mindestens ein Drucker auszuwählen")


def _resolve_done_collected(signed: bool, collected: bool) -> bool:
    """„Leihschein eingesammelt" ist ohne „unterschreiben" bedeutungslos —
    still auf `False` normalisieren statt einen inkonsistenten Zustand zuzulassen
    (Client greyt die Checkbox zwar aus, ein deaktiviertes Element behält aber
    seinen zuletzt gesetzten `checked`-Wert)."""
    return collected and signed


async def _load_student_flags(state: AppState, ctx: ClassContext, auto_done: list[str]) -> None:
    """Anmelde-/Zahlstatus der ganzen Klasse laden — parallele read-only
    IServ-GETs pro Schüler (`get_student_info`, wie in der Scanner-Anzeige).

    Zwei Zwecke aus EINEM Abruf:

    1. **Info-Anzeige (immer):** `QueueStudent.set_info_flags` füllt
       `enrolled`/`paid`/`remission_pending`/`exemption_pending` für die
       Info-Spalte der Host-Queue. Rein informativ — der `status` bleibt
       unberührt.
    2. **Auto-Fertig (nur mit gewählten Filtern):** Schüler, auf die eine der
       gewählten Bedingungen zutrifft, direkt auf 'done' setzen (nicht
       angemeldet / nicht bezahlt / Ermäßigungs- bzw. Befreiungsantrag ohne
       Nachweis / alle vorgemerkten Bücher bereits ausgeliehen).

    Fehler pro Schüler sind nicht fatal (Flags bleiben `None`, Schüler bleibt
    'pending'), damit ein einzelner IServ-Fehler nicht das ganze Klassen-Laden
    blockiert.

    'nicht angemeldet' schließt die übrigen Filter aus — ohne Anmeldung liefert
    IServ keinen Zahl-/Nachweis-/Bücher-Status, also wären `unpaid` u. a. sonst
    bedeutungslose Platzhalterwerte, die einen unangemeldeten Schüler
    fälschlich träfen, selbst wenn nur z. B. `unpaid` gewählt wurde."""
    filters = set(auto_done) & _AUTO_DONE_FILTERS

    async def _check(student: QueueStudent) -> None:
        try:
            info = await state.iserv.get_student_info(student.student_id, state.selected_schoolyear)
        except Exception:
            log.exception(
                "Anmelde-/Zahlstatus für Schüler %s konnte nicht geladen werden", student.student_id
            )
            return
        student.set_info_flags(info)
        student.auto_skipped = False
        if not info.get("enrolled"):
            if "not_enrolled" in filters:
                student.status = "done"
                student.auto_skipped = True
            return
        if (
            ("unpaid" in filters and not info.get("paid"))
            or ("remission_pending" in filters and info.get("remission_pending"))
            or ("exemption_pending" in filters and info.get("exemption_pending"))
        ):
            student.status = "done"
            student.auto_skipped = True
            return
        if "all_lent" in filters:
            hidden = await get_hidden_isbns_for_form(state, student.form)
            apply_hidden_books(info, hidden)
            vormerk, _lent, _lent_codes = booking_isbn_sets_from_info(info)
            if not vormerk:
                student.status = "done"
                student.auto_skipped = True

    await asyncio.gather(*(_check(s) for s in ctx.queue))


# ---------------------------------------------------------------------------
# Schuljahr
# ---------------------------------------------------------------------------


@host_router.get("/api/schoolyears")
async def get_schoolyears() -> dict:
    """Auswählbare Schuljahre + aktuell gewähltes (None = aktuelles Jahr)."""
    state = get_state()
    try:
        years = await state.iserv.get_schoolyears()
    except Exception as e:
        log.exception("Schuljahre konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"schoolyears": years, "selected": state.selected_schoolyear}


@host_router.post("/api/select-schoolyear")
async def select_schoolyear(body: SelectSchoolyearRequest) -> dict:
    """Schuljahr wählen. Setzt die Queue/Klasse zurück, da Klassen jahresspezifisch sind.

    `schoolyear=null` (oder leer) → aktuelles Schuljahr.
    """
    state = get_state()
    hub = get_hub()

    raw = body.schoolyear
    schoolyear = str(raw).strip() if raw else None

    # Guard: laufende Sessions würden durch den Wechsel verwaist. Über ALLE
    # Kontexte prüfen (nicht nur den aktiven Tab) — ein aktiver Schüler in
    # einem nicht-fokussierten Klassen-Tab würde sonst übersehen und der
    # Schuljahreswechsel risse ihn ohne Warnung ab.
    active_q = state.active_students()
    live_b = [
        s for s in state.student_sessions.values() if s.state in ("pending_pairing", "paired")
    ]
    if (active_q or live_b) and not body.force:
        raise HTTPException(
            409,
            detail={
                "reason": "active_sessions",
                "msg": f"{len(active_q)} aktive Schüler / {len(live_b)} Live-Session(s) — "
                "Schuljahreswechsel bricht sie ab.",
            },
        )

    # Kontext noch nicht wegwerfen: end_student benötigt ihn, um Helfer und
    # Worker vollständig zu lösen.
    all_ids = {student.student_id for ctx in state.contexts.values() for student in ctx.queue}
    await teardown_students(
        state,
        hub,
        all_ids,
        reason="schuljahreswechsel",
        clear_unbound_sessions=True,
    )
    for helper in state.helper_sessions.values():
        helper.context_id = None  # Klassen-Bindung hinfällig (Kontexte fliegen weg)
    # Alle Kontexte fallen weg → jede Lehrkraft-Session wird ungültig (ihr
    # Kontext existiert gleich nicht mehr); Reconnect mit ihrem Token muss
    # danach zuverlässig scheitern.
    await revoke_all_teacher_sessions(state, reason="schuljahreswechsel")

    state.selected_schoolyear = schoolyear
    # Alle Klassen-Kontexte fallen — Klassen/Schüler sind jahresspezifisch.
    # (Kompat-Felder `active_form`/`queue`/`book_order` laufen leer, da kein
    # aktiver Kontext mehr gesetzt ist.)
    state.contexts = {}
    state.active_context_id = None
    # Reihenfolge/Ausblendung bleiben erhalten (serverseitig persistiert, global
    # über alle Schuljahre); `normalize_book_order` + `hidden & catalog` fangen
    # ISBN-Drift zum anderen Schuljahr ab. Nur der Katalog-Cache muss weg, da
    # die ISBNs jahresspezifisch sind.
    state.caches.form_catalog_cache.clear()
    await hub.broadcast_host(state.state_snapshot())
    # Alle Klassen-Kontexte weg → Drucker-Displays live nachziehen (die
    # Schülerauftrag-Bedingung entfällt für jedes Display).
    await broadcast_printer_displays(state)
    return {"ok": True, "selected": schoolyear}


# ---------------------------------------------------------------------------
# Klassen
# ---------------------------------------------------------------------------


@host_router.get("/api/classes")
async def get_classes() -> dict:
    state = get_state()
    try:
        classes = await state.iserv.get_class_names(state.selected_schoolyear)
    except Exception as e:
        log.exception("IServ-Klassen konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"classes": classes}


# ---------------------------------------------------------------------------
# Queue-Aufbau
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Klassen-Kontexte (Multi-Tab) — öffnen / schließen / aktivieren
# ---------------------------------------------------------------------------


@host_router.post("/api/open-class")
async def open_class(body: OpenClassRequest) -> dict:
    """Neuen Klassen-Kontext öffnen (Klassen-Tab am Host). Lädt die Schüler der
    Klasse in eine frische, separate Queue und aktiviert den Kontext. Mehrere
    Klassen können parallel offen sein (je ein Tab). Doppel-Öffnen derselben
    Klasse aktiviert den bestehenden Kontext wieder (keine zweite Queue)."""
    form = body.form.strip()
    if not form:
        raise HTTPException(400, "form fehlt")
    state = get_state()
    hub = get_hub()

    existing = next((c for c in state.contexts.values() if c.form == form), None)
    if existing is not None:
        # Noch ladender Kontext → nicht als „geladen" zurückmelden (would-be
        # count 0). Client-seitige Per-Klassen-Sperre verhindert das beim
        # selben Host; dieser Guard ist die Absicherung gegen Races/andere
        # Hosts. Der „Öffnen"-Button bleibt für andere Klassen frei.
        if existing.loading:
            raise HTTPException(409, {"reason": "loading", "msg": "Klasse wird noch geladen"})
        state.set_active_context(existing.id)
        # Erneutes Öffnen aktualisiert die Druck-Allowlist („Öffnen" ist der
        # Bedienpunkt dafür); leer/None = alle Pool-Drucker.
        resolved = _resolve_allowed_printers(body.printers)
        _require_printer_for_live(resolved if body.live_ausgabe else None)
        existing.allowed_printer_ids = resolved
        existing.live_ausgabe = body.live_ausgabe
        existing.slip_trigger = body.slip_trigger
        existing.done_signed = body.done_signed
        existing.done_collected = _resolve_done_collected(body.done_signed, body.done_collected)
        await hub.broadcast_host(state.state_snapshot())
        # Druck-Allowlist dieser Klasse geändert → Helfer-Vorauswahl neu pushen.
        await hub.broadcast_settings(state)
        # Freigegebene Drucker/Liveausgabe können sich geändert haben → die
        # Schülerauftrag-Bedingung eines Drucker-Displays live nachziehen.
        await broadcast_printer_displays(state)
        return {"ok": True, "context_id": existing.id, "count": len(existing.queue), "reused": True}

    try:
        students = await state.iserv.get_students_for_form(form, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schüler konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e

    ctx = state.open_context(form)
    # Kontext erst veröffentlichen, wenn er vollständig geladen ist: während
    # die langsamen IServ-Awaits (Flags/Katalog) laufen, darf kein anderer
    # Broadcast (Helfer hinzufügen, Modus B …) die leere Queue snapshotten —
    # sonst erscheint der Klassen-Tab am Host vorzeitig. `loading=True` hält
    # den Kontext aus `state_snapshot`/`real_contexts_summary` heraus.
    ctx.loading = True
    resolved = _resolve_allowed_printers(body.printers)
    _require_printer_for_live(resolved if body.live_ausgabe else None)
    ctx.allowed_printer_ids = resolved
    ctx.live_ausgabe = body.live_ausgabe
    ctx.slip_trigger = body.slip_trigger
    ctx.done_signed = body.done_signed
    ctx.done_collected = _resolve_done_collected(body.done_signed, body.done_collected)
    ctx.queue = [QueueStudent.from_iserv(s, form=form) for s in students]
    # Immer (nicht nur bei gewählten Auto-Fertig-Filtern): der Abruf füllt auch
    # die Info-Flags für die Queue-Anzeige. Fehler sind pro Schüler gekapselt
    # und nicht fatal — die Klasse bleibt geladen.
    try:
        await _load_student_flags(state, ctx, body.auto_done or [])
    except Exception:
        log.exception("Anmelde-/Zahlstatus der Klasse %s konnte nicht geladen werden", form)
    # Katalog + Bücher-Reihenfolge sofort aufbauen (übernimmt eine im
    # Einstellungen-Dialog vorkonfigurierte Reihenfolge automatisch für den
    # Scanner) — Fehler hier sind nicht fatal, die Klasse bleibt trotzdem geladen.
    try:
        await _ensure_class_catalog(state, context_id=ctx.id)
    except Exception:
        log.exception("Klassen-Bücherkatalog konnte beim Öffnen nicht vorgebaut werden")
    # Vollständig geladen → veröffentlichen und an alle Clients broadcasten.
    ctx.loading = False
    await hub.broadcast_host(state.state_snapshot())
    await hub.broadcast_settings(state)
    # Neue Klasse mit Liveausgabe/Druck-Allowlist kann die Schülerauftrag-
    # Bedingung eines Drucker-Displays aktivieren — live nachziehen.
    await broadcast_printer_displays(state)
    return {"ok": True, "context_id": ctx.id, "count": len(ctx.queue)}


@host_router.post("/api/close-class")
async def close_class(body: CloseClassRequest) -> dict:
    """Klassen-Kontext schließen (Tab × am Host). Beendet laufende Sessions der
    Schüler dieses Kontexts, löst Helfer-Bindungen an diesen Kontext und entfernt
    den Kontext. Read-only bzgl. IServ — keine Buchung, nur In-Memory-Teardown."""
    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")

    # Alle Schüler des Kontexts sauber beenden (Worker zu, Helfer notify,
    # Modus-B-Session revoked). end_student nimmt Student über alle Kontexte
    # wahr (student_id eindeutig); broadcast=False → am Ende einmal bündeln.
    for s in list(ctx.queue):
        await end_student(
            state,
            hub,
            s.student_id,
            queue_status="skipped",
            session_state="revoked",
            broadcast=False,
        )
    # Helfer-Bindungen an diesen Kontext lösen (ihre Schüler oben bereits
    # abgeschlossen; context_id weg → „Nächster" zieht künftig aus dem aktiven
    # Kontext oder einem neu gewählten Tab).
    for helper in state.helper_sessions.values():
        if helper.context_id == context_id:
            helper.context_id = None

    # Eine ggf. gebundene Lehrkraft-Session entwerten — ihr Kontext verschwindet
    # gleich, ein Reconnect mit ihrem Token darf danach nicht mehr klappen.
    await revoke_teacher_sessions_for_context(state, context_id, reason="klasse-geschlossen")

    state.close_context(context_id)
    await hub.broadcast_host(state.state_snapshot())
    # Klasse (samt Liveausgabe/Druck-Allowlist) weg → Drucker-Displays live
    # nachziehen (die Schülerauftrag-Bedingung kann jetzt entfallen).
    await broadcast_printer_displays(state)
    return {"ok": True, "context_id": context_id}


@host_router.post("/api/set-active-context")
async def set_active_context(body: ContextIdBody) -> dict:
    """Aktiven Klassen-Kontext setzen (welcher Tab am Host fokussiert ist).
    `context_id=null` → kein aktiver Kontext (Host-Tab ohne Klasse)."""
    state = get_state()
    context_id = body.context_id
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")
    state.set_active_context(context_id)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "active_context_id": state.active_context_id}


@host_router.post("/api/context-printers")
async def set_context_printers(body: ContextPrintersRequest) -> dict:
    """Druck-Allowlist einer bereits geöffneten Klasse nachträglich setzen
    (Checkboxen im Klassen-Tab). `printers` = Drucker-IDs; `None`/leer = kein
    Filter (alle Pool-Drucker). Wirkt ab dem nächsten Druckauftrag (bereits
    wartende behalten ihre Allowlist — s. print_queue `PrintJob.allowed_printers`).

    Kopplung Live-Ausgabe: eine **explizit leere** Allowlist (`[]`, nicht `None`)
    wird verweigert, solange Live-Ausgabe für diese Klasse aktiv ist — erst
    Live-Ausgabe ausschalten, dann den letzten Drucker abwählen (400
    „Zuerst Live-Ausgabe schließen").

    Reiner In-Memory-State, kein DB-/IServ-Zugriff. Weckt den Scheduler, damit
    künftige Aufträge sofort verteilt werden können."""
    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")
    new_allowed = _resolve_allowed_printers(body.printers)
    # Letzten Drucker bei aktiver Live-Ausgabe nicht wegnehmen — erst Live-Ausgabe
    # ausschalten. `None` (alle) gilt als „Drucker gewählt" und ist immer erlaubt.
    if new_allowed is not None and not new_allowed and ctx.live_ausgabe:
        raise HTTPException(400, "Zuerst Live-Ausgabe schließen")
    ctx.allowed_printer_ids = new_allowed
    state.print_queue.wake()
    await hub.broadcast_host(state.state_snapshot())
    # Druck-Allowlist dieser Klasse geändert → Helfer-Vorauswahl neu pushen.
    await hub.broadcast_settings(state)
    # Freigegebene Drucker geändert → die Schülerauftrag-Bedingung eines
    # Drucker-Displays live nachziehen.
    await broadcast_printer_displays(state)
    return {"ok": True, "context_id": context_id, "allowed_printers": (
        None if ctx.allowed_printer_ids is None else sorted(ctx.allowed_printer_ids)
    )}


@host_router.post("/api/context-live-ausgabe")
async def set_context_live_ausgabe(body: ContextLiveAusgabeRequest) -> dict:
    """Live-Ausgabe (Modus B) für eine bereits geöffnete Klasse nachträglich
    ein-/ausschalten (Schalter im Klassen-Tab unter der Drucker-Auswahl). Rein
    In-Memory, kein DB-/IServ-Zugriff. `True` → Modus-B-Kasten sichtbar +
    Pairing zulässig; `False` → beides ausgeblendet/abgewiesen. Das globale
    Modus-B-Backend (Join-Secret/QR, iPad-Freischalt) bleibt unberührt.

    Kopplung Drucker: Aktivieren setzt mindestens einen Drucker für die Klasse
    voraus (`None` = alle gilt als gewählt); sonst 400 „Es ist mindestens ein
    Drucker auszuwählen". Ausschalten ist immer erlaubt."""

    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")
    # Live-Ausgabe aktivieren setzt mindestens einen Drucker voraus (gleiche
    # Meldung wie der clientseitige rote Hinweis). Ausschalten immer erlaubt.
    if body.live_ausgabe:
        _require_printer_for_live(ctx.allowed_printer_ids)
    ctx.live_ausgabe = bool(body.live_ausgabe)
    await hub.broadcast_host(state.state_snapshot())
    # Liveausgabe-Schalter kann die Schülerauftrag-Bedingung eines Drucker-
    # Displays kippen (s. `AppState._printer_display_students_only`).
    await broadcast_printer_displays(state)
    return {"ok": True, "context_id": context_id, "live_ausgabe": ctx.live_ausgabe}


@host_router.post("/api/context-slip-trigger")
async def set_context_slip_trigger(body: ContextSlipTriggerRequest) -> dict:
    """Wann der Leihschein dieser Klasse am Schülerclient (Modus B) gedruckt
    wird, sobald alle vorgemerkten Bücher gescannt sind (Druckmodus) —
    nachträglich setzbar (Dropdown im Klassen-Tab unter „Leihschein Druck:").
    Rein In-Memory, kein DB-/IServ-Zugriff. Wert aus
    {"auto","student","helper","barcode"} (s. ClassContext.slip_trigger)."""

    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")
    ctx.slip_trigger = body.slip_trigger
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": context_id, "slip_trigger": ctx.slip_trigger}


@host_router.post("/api/context-done-options")
async def set_context_done_options(body: ContextDoneOptionsRequest) -> dict:
    """„Fertig"-Voraussetzungen einer bereits geöffneten Klasse nachträglich
    setzen (Checkboxen „Leihschein unterschreiben"/„…wird vom Lehrer
    eingesammelt" in den Klasseneinstellungen). Rein In-Memory, kein
    DB-/IServ-Zugriff — aktuell ohne Auswirkung auf den Fertig-Übergang selbst
    (folgt später), nur Persistenz + Anzeige (s. ClassContext.done_signed/
    done_collected). `done_collected` wird auf `False` normalisiert, wenn
    `done_signed=False` gesetzt wird."""

    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")
    ctx.done_signed = body.done_signed
    ctx.done_collected = _resolve_done_collected(body.done_signed, body.done_collected)
    await hub.broadcast_host(state.state_snapshot())
    return {
        "ok": True, "context_id": context_id,
        "done_signed": ctx.done_signed, "done_collected": ctx.done_collected,
    }


@host_router.get("/api/students-for-class")
async def students_for_class(form: str) -> dict:
    """Schülerliste einer Klasse für die Einzel-Auswahl (ohne die Queue anzufassen)."""
    form = form.strip()
    if not form:
        raise HTTPException(400, "form fehlt")
    state = get_state()
    try:
        students = await state.iserv.get_students_for_form(form, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schüler konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"students": students}


@host_router.post("/api/add-student")
async def add_student_to_queue(body: AddStudentRequest) -> dict:
    """Einen einzelnen Schüler an die Queue eines Klassen-Kontexts anhängen
    (klassenübergreifend). `context_id` optional — fehlt er, wird der aktive
    Kontext genutzt (bei Einzel-Schüler-Reiter im Klassen-Tab gesetzt); ohne
    aktiven Kontext (kein Klassen-Tab offen) schlägt der Request mit 400 fehl,
    statt still einen Geister-Kontext anzulegen.

    Im Gegensatz zu `/api/open-class` wird die Queue NICHT ersetzt und es
    werden keine laufenden Sessions angefasst.
    """
    state = get_state()
    hub = get_hub()

    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt/ungültig")
    student_id = body.student_id
    lastname = body.lastname.strip()
    firstname = body.firstname.strip()
    form = body.form.strip()
    if not lastname and not firstname:
        raise HTTPException(400, "Name fehlt")

    context_id = str(body.context_id or "").strip() or None
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")

    if state.find_student(student_id):
        raise HTTPException(409, "Schüler bereits in der Queue")

    target_ctx = state.ctx_or_active(context_id)
    if target_ctx is None:
        raise HTTPException(400, "Kein Klassen-Tab geöffnet")
    target_ctx.queue.append(
        QueueStudent(student_id=student_id, lastname=lastname, firstname=firstname, form=form)
    )
    if not target_ctx.form:
        target_ctx.form = form or ""

    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": len(target_ctx.queue)}


# Testschüler für den "Test Config"-Reiter (IDs einmalig per read-only
# Namenssuche ermittelt, siehe Git-Historie). Klassen-Angabe nur informativ —
# die Queue arbeitet rein über student_id.
#
# Die vier Testschüler stehen bewusst im Source (Niklas = freigegebener
# Testschüler für Buchungstests; Lukas/Lucas/Finn = Mitentwickler/Mitschüler
# für Queue-/UI-Tests, keine Buchung). Eine optionale pro-Entwickler:in-
# Override-Datei `tests/test_students.local.json` (gitignored) kann die Liste
# ersetzen — fehlt sie, gilt dieser Default. Buchungen gegen Produktion werden
# ohnehin nur mit Niklas + expliziter Freigabe gefahren (CLAUDE.md).
_TEST_STUDENTS_FILE = (
    Path(__file__).resolve().parent.parent.parent / "tests" / "test_students.local.json"
)
_TEST_STUDENTS_DEFAULT = [
    {"student_id": 2159, "firstname": "Niklas", "lastname": "Müller", "form": "Klasse 12Slw"},
    {"student_id": 2164, "firstname": "Lukas", "lastname": "Podleschny", "form": "Klasse 12Mk"},
    {"student_id": 2167, "firstname": "Lucas", "lastname": "Stolpe", "form": "Klasse 12Slw"},
    {"student_id": 2415, "firstname": "Finn", "lastname": "Podleschny", "form": "Klasse 10c"},
]


def _load_test_students() -> list[dict]:
    try:
        with _TEST_STUDENTS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.warning("Testschüler-Datei nicht gefunden (%s) — nutze Default.", _TEST_STUDENTS_FILE)
        return list(_TEST_STUDENTS_DEFAULT)
    except (OSError, ValueError) as exc:
        log.warning(
            "Testschüler-Datei nicht lesbar (%s: %s) — nutze Default.", _TEST_STUDENTS_FILE, exc
        )
        return list(_TEST_STUDENTS_DEFAULT)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        log.warning("Testschüler-Datei hat falsches Format — nutze Default.")
        return list(_TEST_STUDENTS_DEFAULT)
    return data


TEST_STUDENTS = _load_test_students()

# Pseudo-Klassen-Name für den dedizierten "Test Config"-Tab (kein echter IServ-
# Klassencode, daher kollisionsfrei mit `/api/open-class`-Dedup über `c.form`).
TEST_CONFIG_FORM = "Test Config"


@host_router.post("/api/open-test-config")
async def open_test_config() -> dict:
    """Dedizierten "Test Config"-Tab öffnen (kein IServ-Roundtrip, kein echter
    Klassen-Katalog) und sofort mit den festen Testschülern befüllen. Erneutes
    Öffnen aktiviert den bestehenden Tab wieder (keine zweite Queue), analog zu
    `/api/open-class`."""
    state = get_state()
    hub = get_hub()

    existing = next(
        (c for c in state.contexts.values() if c.form == TEST_CONFIG_FORM),
        None,
    )
    if existing is not None:
        state.set_active_context(existing.id)
        await hub.broadcast_host(state.state_snapshot())
        return {"ok": True, "context_id": existing.id, "count": len(existing.queue), "reused": True}

    ctx = state.open_context(TEST_CONFIG_FORM)
    for s in TEST_STUDENTS:
        if state.find_student(s["student_id"]):
            continue
        ctx.queue.append(QueueStudent.from_iserv(s, form=s["form"]))
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": ctx.id, "count": len(ctx.queue)}
