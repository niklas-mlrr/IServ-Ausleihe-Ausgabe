from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

from ..book_order import get_book_order_for_form, get_hidden_isbns_for_form
from ..hub import get_hub
from ..sessions import (
    advance_helper,
    apply_hidden_books,
    assign_student_to_helper,
    booking_isbn_sets_from_info,
    end_student,
    expected_isbns_from_info,
    gen_registration_code,
    print_loan_slip_for,
    process_scan,
    send_display_update,
)
from ..state import DisplaySession, get_state

log = logging.getLogger(__name__)
router = APIRouter()

# Grace-Frist für den Scanner-Disconnect: das finally des Scanner-WS stößt den
# Schüler-Teardown (end_student: Schüler zurück auf 'pending', Worker zu) nicht
# sofort an, sondern verzögert als Task. Lädt der Helfer die Seite neu, nimmt
# die neue Verbindung den Grace-Task cancel't und lädt den Schüler neu (Reconnect
# mit ggf. Worker-Reload). Ohne Reconnect innerhalb der Frist gilt die Trennung
# als echt → Teardown läuft (so bleibt ein „active" auf einem toten Helfer-Token
# nicht stehen; Modus-A-Queue-Einträge werden vom Sweeper nicht abgeräumt).
_RECONNECT_GRACE_S = 3.0


async def _deferred_end(state, hub, helper, student_id: int) -> None:
    """Verzögerter Teardown des Helfer-Schülers nach WS-Trennung (s. ws_scanner).

    Re-Checks vor dem Eingreifen schützen gegen Reconnect / Weiter-Schalten
    während der Frist: ein Reconnect cancelt diesen Task (dann läuft er nicht
    mehr bis hier); zur Sicherheit prüfen wir trotzdem ``helper.ws`` (neue
    Verbindung?) und ``helper.student_id`` (noch derselbe Schüler?)."""
    try:
        await asyncio.sleep(_RECONNECT_GRACE_S)
    except asyncio.CancelledError:
        return
    # Re-Check 1: Helfer hat wieder eine Verbindung (Reconnect) → kein Teardown.
    if helper.ws is not None:
        return
    # Re-Check 2: Helfer wurde inzwischen weitergeschaltet/zurückgesetzt
    # (z. B. /api/skip, /api/reset-queue, /api/disconnect-all — alle setzen
    # helper.student_id auf None bzw. einen neuen Schüler).
    if helper.student_id != student_id:
        return
    try:
        if helper.load_task is not None and not helper.load_task.done():
            helper.load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await helper.load_task
            helper.load_task = None
        await end_student(
            state, hub, student_id,
            queue_status="pending", session_state="revoked",
        )
    except Exception:  # noqa: BLE001 — Sweeper-Loop-artige Robustheit: ein fehlgeschlagener Teardown darf den Task nicht crashen
        log.exception("deferred end_student für %d fehlgeschlagen", student_id)
    try:
        await hub.broadcast_host(state.state_snapshot())
    except Exception:  # noqa: BLE001 — Broadcast-Fehler nicht propagieren
        pass


@router.websocket("/ws/host")
async def ws_host(websocket: WebSocket, session_id: str | None = Cookie(default=None)) -> None:
    state = get_state()
    from ..config import get_config
    if not state.is_host_session_valid(session_id, get_config().host_session_ttl_s):
        await websocket.close(code=4003, reason="Nicht authentifiziert")
        return

    await websocket.accept()
    state.host_ws_connections.append(websocket)
    try:
        await websocket.send_json(state.state_snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            state.host_ws_connections.remove(websocket)
        except ValueError:
            pass


@router.websocket("/ws/scanner/{token}")
async def ws_scanner(websocket: WebSocket, token: str) -> None:
    state = get_state()
    hub = get_hub()

    if token not in state.helper_sessions:
        await websocket.close(code=4004, reason="Ungültiger Token")
        return

    await websocket.accept()
    helper = state.helper_sessions[token]
    # Frische Verbindung → Peek-Zustand ist clientseitig nicht mehr gesetzt.
    helper.peeking = False
    # Reconnect (Seite erneut geöffnet): einen noch laufenden Grace-Teardown-
    # Task des gerade getrennten alten WS abräumen (sonst würde er nach der
    # Frist den soeben neugeladenen Schüler doch noch abbrechen).
    t = helper.end_task
    helper.end_task = None
    if t is not None and not t.done():
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    # Synchron übernehmen — VOR jedem await. So erkennt das finally des alten
    # WS (das asynchron zum Reconnect läuft) an `helper.ws is websocket`, dass
    # ein Reconnect übernommen hat, und löst KEINEN Teardown aus.
    old_ws = helper.ws
    helper.ws = websocket
    # Reconnect: die alte Verbindung sauber schließen, statt sie verwaist offen
    # zu lassen.
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009, reason="Neue Verbindung")
        except Exception:
            pass

    # Schüler bereits zugewiesen? Info sofort schicken. Die Reihenfolge wird
    # anhand des Jahrgangs *dieses* Schülers ermittelt (nicht der einen globalen
    # `state.book_order`) — sonst würde die direkt danach folgende `settings`-
    # Nachricht sie bei klassenübergreifenden Warteschlangen wieder überschreiben.
    book_order = state.book_order
    if helper.student_id is not None:
        student = state.find_student(helper.student_id)
        if student and state.iserv:
            try:
                info = await state.iserv.get_student_info(helper.student_id, state.selected_schoolyear)
                info["form"] = student.form
                book_order = await get_book_order_for_form(state, student.form)
                info["book_order"] = book_order
                apply_hidden_books(info, await get_hidden_isbns_for_form(state, student.form))
                helper.expected_isbns = expected_isbns_from_info(info)
                helper.vormerk_isbns, helper.lent_isbns = booking_isbn_sets_from_info(info)
                helper.last_scan = None  # Worker-Page wird ggf. neu geladen → Feld leer
                # Modus A: Bücherliste sofort. Sends über das Hub-Lock
                # (send_websocket), damit sie nicht mit den Sends des In-Flight-
                # Lade-Tasks (send_scanner auf denselben neuen WS) interleaven.
                await hub.send_websocket(websocket, {"type": "student_info", "student": info})
                load_inflight = helper.load_task is not None and not helper.load_task.done()
                worker_session = state.student_worker_sessions.get(helper.student_id)
                worker_present = worker_session is not None
                if worker_present:
                    # Worker war bereits bereit → Seite im Worker neu laden
                    # (read-only GET-Reload auf dem bestehenden Context, kein
                    # neuer Context). Identität danach re-checken: wurde der
                    # Worker während des Reloads freigegeben (z. B. /api/skip),
                    # KEIN `worker_ready` senden, sondern Fehler.
                    ws_ref = worker_session
                    reload_ok = False
                    try:
                        await ws_ref.reload()
                        reload_ok = state.student_worker_sessions.get(helper.student_id) is ws_ref
                    except Exception as e:  # noqa: BLE001
                        log.warning("Worker-Reload (Reconnect) für %d fehlgeschlagen: %s", helper.student_id, e)
                        reload_ok = False
                    if reload_ok:
                        await hub.send_websocket(websocket, {"type": "worker_ready"})
                    else:
                        await hub.send_websocket(
                            websocket, {"type": "error", "msg": "Worker-Reload fehlgeschlagen"}
                        )
                elif load_inflight:
                    # Worker wird gerade erst geöffnet (open_student läuft).
                    # KEIN `worker_ready` senden — der In-Flight-Lade-Task
                    # (`load_and_push_helper_student`) liefert ihn über
                    # send_scanner(token) an den neuen WS. student_info steht
                    # schon (oben gesendet), ggf. doppelt (vom In-Flight-Task) —
                    # harmlos.
                    pass
                else:
                    # Degraded-Modus (kein worker_pool) oder Worker nie
                    # bereit: wie bisher sofort `worker_ready` senden.
                    await hub.send_websocket(websocket, {"type": "worker_ready"})
            except Exception as e:
                await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})
        elif student is None:
            await hub.send_websocket(websocket, {"type": "waiting", "msg": "Warte auf Schüler-Zuweisung", "queue_size": state.pending_count(), "queue": state.pending_queue_as_list()})
    else:
        await hub.send_websocket(websocket, {"type": "waiting", "msg": "Warte auf Schüler-Zuweisung", "queue_size": state.pending_count(), "queue": state.pending_queue_as_list()})

    # Host-Default „Schüler-Leihschein" (Druck-Dialog) + Bücher-Reihenfolge.
    await hub.send_websocket(websocket, {
        "type": "settings",
        "slip_second_page": state.slip_second_page_default,
        "book_order": book_order,
    })

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Malformedes Frame (kein valides JSON) — nicht tödlich, Client
                # bleibt verbunden. Loggen und ignorieren, statt die Schleife
                # mit einem rohen Traceback sterben zu lassen.
                log.warning("Ungültiges JSON-Frame vom Scanner-WS (token=%s) — ignoriert", token)
                continue
            mtype = raw.get("type")

            if mtype == "next":
                # Aktuellen Schüler abschließen (kein Browser-Submit) und
                # nächsten Wartenden auf diesen Helfer setzen.
                await advance_helper(state, hub, helper)
                continue

            if mtype == "call":
                # Helfer ruft einen konkreten wartenden Schüler aus der
                # Warteschlange auf (Button in der Queue-Anzeige). Rein lokale
                # Zuweisung — kein IServ-/DB-Schreibzugriff. Der Schüler muss
                # noch 'pending' sein; zwischen Prüfung und Zuweisung liegt kein
                # Await, also atomar im Eventloop (kein Doppel-Aufruf zweier
                # Helfer auf denselben Schüler).
                sid = raw.get("student_id")
                target = state.find_student(sid) if sid is not None else None
                if target is None or target.status != "pending":
                    await websocket.send_json({
                        "type": "error",
                        "msg": "Schüler nicht (mehr) in der Warteschlange",
                    })
                    # Queue sofort nachpushen, damit der Client die aktuelle
                    # Liste sieht (z. B. zwischenzeitlich von anderem Helfer
                    # aufgerufen) — statt auf den nächsten Broadcast zu warten.
                    await hub.broadcast_queue_size(state)
                    continue
                if helper.student_id is not None:
                    # Aufrufen aus der Peek-Ansicht (Menü): der alte Schüler wird
                    # NICHT abgeschlossen, sondern als 'pending' zurück in die
                    # Warteschlange gelegt (noch nicht bearbeitet). Der Worker-
                    # Context schließt (revoked), der Schüler bleibt aber in der
                    # Queue verfügbar — wie beim Disconnect-Teardown
                    # (`_deferred_end`). Der „Weiter"-Button (`next`) dagegen
                    # schließt den Schüler als 'done' ab (s. advance_helper).
                    await end_student(
                        state, hub, helper.student_id,
                        queue_status="pending", session_state="revoked",
                        helper_notify={"type": "loading"},  # Queue verbergen — neuer wird geladen
                    )
                await assign_student_to_helper(state, hub, helper, target)
                continue

            if mtype == "peek_queue":
                # Menü-Toggle: Helfer schaltet auf die Warteschlangen-Ansicht,
                # während sein Schüler im Hintergrund verbunden bleibt (kein
                # Trennen, kein IServ-/DB-Zugriff). Peek-Flag setzen, damit
                # nachfolgende `broadcast_queue_size`-Updates diesen Helfer
                # erreichen, und sofort die aktuelle Queue pushen (für ein
                # unmittelbares Rendern, ohne auf den nächsten Broadcast zu
                # warten).
                helper.peeking = True
                await hub.send_scanner(token, {
                    "type": "queue_update",
                    "queue_size": state.pending_count(),
                    "queue": state.pending_queue_as_list(),
                })
                continue

            if mtype == "peek_close":
                # Menü-Toggle zurück zur Schüler-Ansicht. Kein Push nötig — der
                # Client stellt die Bücherliste lokal wieder her.
                helper.peeking = False
                continue

            if mtype == "clear_book_alert":
                # Helfer schließt sein Ausgemustert-Hinweis-Modal selbst (Button)
                # → Host-Meldung für diesen Schüler ebenfalls aufräumen, damit das
                # Now-Serving-Kästchen wieder normal angezeigt wird. Read-only,
                # kein IServ-/DB-Zugriff; nur ein Host-Broadcast.
                sid = helper.student_id
                if sid is not None:
                    await hub.broadcast_host({
                        "type": "book_alert",
                        "student_id": sid,
                        "cleared": True,
                    })
                continue

            if mtype == "print":
                # Leihschein des aktuell zugewiesenen Schülers drucken.
                # Read-only PDF-Abruf + lokaler Druck (kein IServ-Submit).
                if helper.student_id is None:
                    await websocket.send_json({"type": "print_result", "ok": False, "msg": "Kein Schüler zugewiesen"})
                    continue
                # Seite 1 wird immer gedruckt; Seite 2 (Schüler-Leihschein) nur,
                # wenn der Helfer sie im Druck-Dialog aktiviert hat.
                second_page = bool(raw.get("second_page"))
                pages = None if second_page else "1"
                try:
                    result = await print_loan_slip_for(state, helper.student_id, pages=pages)
                    await websocket.send_json({"type": "print_result", **result})
                except Exception as e:  # noqa: BLE001 — Fehler dem Client melden
                    log.exception("Leihschein-Druck (Scanner) fehlgeschlagen")
                    await websocket.send_json({"type": "print_result", "ok": False, "msg": str(e)})
                continue

            if mtype != "scan":
                continue

            barcode = str(raw.get("value", "")).strip()
            if not barcode:
                continue

            helper.last_scan = barcode
            log.info("Scan von Helper %s: %s", token, barcode)

            student_id = helper.student_id
            if student_id is None:
                await websocket.send_json({
                    "type": "scan_result",
                    "barcode": barcode,
                    "status": "error",
                    "msg": "Kein Schüler zugewiesen",
                })
                continue

            # Scan verarbeiten: Buchungs-Vorabprüfung (im Lager? bestellt? Reihe
            # noch nicht ausgeliehen?) → buchen (Enter) oder — Gate aus — stagen.
            # Nicht erfüllt → Feld wird NICHT berührt (Freigabe 2026-07-02).
            result = await process_scan(
                state, student_id, helper.vormerk_isbns, helper.lent_isbns, barcode,
                source="helper",
            )
            # ISBN mitgeben, damit der Helferclient das gescannte Buch in seiner
            # Liste markieren kann.
            await websocket.send_json({"type": "scan_result", "barcode": barcode, **result})
            await hub.broadcast_host(state.state_snapshot())

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn keine neue Verbindung übernommen hat.
        if helper.ws is websocket:
            helper.ws = None
            # Echte Trennung (kein Reconnect hat übernommen): Schüler-Teardown
            # verzögert anstoßen (Grace-Frist). Ein innerhalb der Frist
            # folgender Reconnect (Seite neu laden) cancelt diesen Task und lädt
            # den Schüler neu — so geht der Schüler beim Helfer-Neuladen nicht
            # verloren. Ohne Reconnect läuft der Teardown nach der Frist
            # (Schüler zurück auf 'pending', Worker zu) — so bleibt kein
            # „active" auf einem toten Helfer-Token stehen (Modus-A-Queue-
            # Einträge werden vom Sweeper nicht abgeräumt, s. _deferred_end).
            if helper.student_id is not None:
                # Eventuell noch laufenden Grace-Task der vorigen Trennung
                # abräumen (z. B. zweite Trennung während der Frist) — synchron
                # lesen+nullen, damit ein konkurrierender Reconnect nicht den
                # neu gesetzten Task überschreibt.
                t0 = helper.end_task
                helper.end_task = None
                if t0 is not None and not t0.done():
                    t0.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t0
                helper.end_task = asyncio.create_task(
                    _deferred_end(state, hub, helper, helper.student_id)
                )
        # else: Reconnect hat helper.ws bereits übernommen → Student/Worker
        # unangetastet lassen (der Reconnect-Pfad lädt den Schüler neu).
        try:
            await hub.broadcast_host(state.state_snapshot())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modus B — iPad-Display (nur QR, keine Schülerdaten)
# ---------------------------------------------------------------------------

@router.websocket("/ws/display")
async def ws_display(websocket: WebSocket) -> None:
    state = get_state()
    hub = get_hub()

    await websocket.accept()
    display = DisplaySession(display_id=uuid.uuid4().hex[:12], registration_code=gen_registration_code())
    state.displays[display.display_id] = display
    display.ws = websocket
    await send_display_update(state, display)  # zeigt zunächst den Registrierungscode
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            # Display sendet nichts Inhaltliches; receive dient der Trennungserkennung.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.displays.pop(display.display_id, None)
        try:
            await hub.broadcast_host(state.state_snapshot())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modus B — Schüler-Session (Pairing-Gate + Scan)
# ---------------------------------------------------------------------------

@router.websocket("/ws/student/{session_token}")
async def ws_student(websocket: WebSocket, session_token: str) -> None:
    state = get_state()
    hub = get_hub()

    session = state.student_sessions.get(session_token)
    if not session or session.state not in ("pending_pairing", "paired"):
        # Ungültiger/entwerteter Token → neutrale "Vorgang abgeschlossen"-Seite.
        # accept() VOR close(), damit der Browser den 4006-Code zuverlässig
        # erhält (sonst sieht er nur 1006 und kann Token-Tod nicht erkennen).
        await websocket.accept()
        await websocket.close(code=4006, reason="Session ungültig")
        return

    await websocket.accept()
    # Reconnect: vorherige Verbindung derselben Session sauber schließen.
    old_ws = session.ws
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009, reason="Neue Verbindung")
        except Exception:
            pass
    session.ws = websocket
    session.last_activity = datetime.now()

    if session.state == "pending_pairing":
        await websocket.send_json({"type": "pending", "pairing_code": session.pairing_code})
    elif session.state == "paired" and session.student_id is not None:
        # Reconnect nach Pairing: Identität (ohne Bücher) erneut senden; die
        # Bücherliste kommt mit `worker_ready` — sofort, wenn der Worker bereits
        # steht, sonst liefert sie der noch laufende Lade-Task.
        try:
            info = await state.iserv.get_student_info(session.student_id, state.selected_schoolyear)
            qs = state.find_student(session.student_id)
            info["form"] = qs.form if qs else ""
            info["book_order"] = await get_book_order_for_form(state, info["form"])
            apply_hidden_books(info, await get_hidden_isbns_for_form(state, info["form"]))
            session.expected_isbns = expected_isbns_from_info(info)
            session.vormerk_isbns, session.lent_isbns = booking_isbn_sets_from_info(info)
            books = info.get("books", [])
            await websocket.send_json({
                "type": "student_info",
                "student": {**info, "books": []},
                "payment_overridden": session.payment_overridden,
            })
            load_inflight = session.load_task is not None and not session.load_task.done()
            worker_present = state.student_worker_sessions.get(session.student_id) is not None
            if not load_inflight or worker_present:
                await websocket.send_json({"type": "worker_ready", "books": books})
            # Blockierendes Ausgemustert-Hinweis-Modal überlebt einen Reconnect
            # (z. B. Seiten-Reload) — erst der Host darf es per Button schließen.
            if session.book_alert_open and session.book_alert_payload:
                await websocket.send_json(session.book_alert_payload)
        except Exception as e:
            await websocket.send_json({"type": "error", "msg": str(e)})

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Malformedes Frame — loggen und weiterlauschen, statt die
                # Schleife mit Traceback sterben zu lassen.
                log.warning("Ungültiges JSON-Frame vom Schüler-WS (session=%s) — ignoriert",
                            getattr(session, "session_token", "?"))
                continue
            session.last_activity = datetime.now()
            mtype = raw.get("type")

            if mtype == "scan":
                barcode = str(raw.get("value", "")).strip()
                if not barcode:
                    continue
                if session.state != "paired" or session.student_id is None:
                    await websocket.send_json({
                        "type": "scan_result",
                        "barcode": barcode,
                        "status": "error",
                        "msg": "Noch nicht freigegeben",
                    })
                    continue
                if session.book_alert_open:
                    # Blockierendes Hinweis-Modal (ausgemustertes Buch) noch offen —
                    # erst der Host darf per Button freigeben. Barcode ignorieren.
                    continue
                session.last_scan = barcode
                # Scan verarbeiten: Buchungs-Vorabprüfung → buchen (Enter) oder
                # — Gate aus — stagen. Nicht erfüllt → Feld wird NICHT berührt.
                result = await process_scan(
                    state, session.student_id, session.vormerk_isbns, session.lent_isbns, barcode
                )
                payload = {"type": "scan_result", "barcode": barcode, **result}
                # Ausgemustert ODER an jemand anderen verliehen → blockierendes
                # Hinweis-Modal am Schüler-Client (kein eigener Schließen-Button,
                # Host gibt per /api/clear-book-alert frei). „An sich selbst
                # verliehen" (series_already_lent) ist nur ein Hinweis und nicht
                # blockierend — der Schüler schließt ihn selbst.
                if result.get("status") in ("book_deleted", "not_in_stock"):
                    session.book_alert_open = True
                    session.book_alert_payload = payload
                await websocket.send_json(payload)
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "finish":
                # Schüler schließt selbst ab → harter Zugriffsentzug.
                if session.student_id is not None:
                    await end_student(
                        state, hub, session.student_id,
                        queue_status="done", session_state="completed",
                    )
                else:
                    from ..sessions import invalidate_session
                    await invalidate_session(state, session, "completed", reason="self-finish")
                break

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn es noch unsere Verbindung ist.
        if session.ws is websocket:
            session.ws = None
        try:
            await hub.broadcast_host(state.state_snapshot())
        except Exception:
            pass
