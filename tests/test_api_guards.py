"""Unit-Tests für die HTTP-Endpunkt-Logik (server/routes/api.py).

Ruft die Endpunkt-Coroutinen direkt auf (kein laufender Server, kein httpx) —
gleiche Linie wie test_booking_gate. Geprüft werden Auth-Guard, Validierung,
Idempotenz und das Buchungs-Gate auf HTTP-Ebene; IServ/Worker bleiben außen vor.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException, Response

import server.routes.api as api
from server.config import Config
from server.state import AppState

# ---------------------------------------------------------------------------
# Fixtures: frische Singletons pro Test (Host-Login bereits gültig)
# ---------------------------------------------------------------------------

class _FakeHub:
    async def broadcast_host(self, snapshot) -> None:
        pass


def _make_config(**over) -> Config:
    base = dict(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        allow_booking=False,
    )
    base.update(over)
    return Config(**base)


@pytest.fixture
def ctx(monkeypatch):
    """Frischer State + Config + Fake-Hub; gültige Host-Session 'sid'."""
    state = AppState()
    state.add_host_session("sid")
    cfg = _make_config()
    hub = _FakeHub()
    monkeypatch.setattr(api, "get_state", lambda: state)
    monkeypatch.setattr(api, "get_config", lambda: cfg)
    monkeypatch.setattr(api, "get_hub", lambda: hub)
    return state, cfg, hub


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Auth-Guard (_require_host)
# ---------------------------------------------------------------------------

def test_require_host_rejects_missing_cookie(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.get_state_endpoint(session_id=None))
    assert ei.value.status_code == 403


def test_require_host_rejects_unknown_session(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.get_state_endpoint(session_id="bogus"))
    assert ei.value.status_code == 403


def test_require_host_accepts_valid_session(ctx):
    res = run(api.get_state_endpoint(session_id="sid"))
    assert res["type"] == "state"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_wrong_password(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.login({"password": "nope"}, Response(), request=None))
    assert ei.value.status_code == 403


def test_login_correct_password_sets_session(ctx):
    state, _, _ = ctx
    res = run(api.login({"password": "secret"}, Response(), request=None))
    assert res == {"ok": True}
    # Genau eine neue Host-Session zusätzlich zur fixture-'sid'.
    assert len(state.host_sessions) == 2


# ---------------------------------------------------------------------------
# add-student: Validierung & Duplikat-Schutz
# ---------------------------------------------------------------------------

def test_add_student_invalid_id(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.add_student_to_queue({"student_id": "x", "lastname": "M"}, session_id="sid"))
    assert ei.value.status_code == 400


def test_add_student_missing_name(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.add_student_to_queue({"student_id": 1}, session_id="sid"))
    assert ei.value.status_code == 400


def test_add_student_without_open_context_rejected(ctx):
    """Ohne offenen Klassen-Tab (kein aktiver Kontext) schlägt der Request mit
    400 fehl — kein stiller Geister-Kontext mehr (implizite Kontexte wurden
    entfernt)."""
    with pytest.raises(HTTPException) as ei:
        run(api.add_student_to_queue(
            {"student_id": 1, "lastname": "Müller", "firstname": "N", "form": "10a"},
            session_id="sid",
        ))
    assert ei.value.status_code == 400


def test_add_student_success_then_duplicate(ctx):
    state, _, _ = ctx
    state.open_context("10a")
    res = run(api.add_student_to_queue(
        {"student_id": 1, "lastname": "Müller", "firstname": "N", "form": "10a"},
        session_id="sid",
    ))
    assert res == {"ok": True, "count": 1}
    assert state.active_context.form == "10a"   # erste Klasse übernommen
    with pytest.raises(HTTPException) as ei:
        run(api.add_student_to_queue({"student_id": 1, "lastname": "Müller"}, session_id="sid"))
    assert ei.value.status_code == 409


# ---------------------------------------------------------------------------
# open-test-config: Idempotenz (offener Punkt aus docs/test_status.md) —
# ein bereits in einer Queue stehender Testschüler wird bei erneutem Öffnen
# nicht doppelt eingefügt (state.find_student-Check in open_test_config).
# ---------------------------------------------------------------------------

def test_open_test_config_idempotent_when_student_already_queued(ctx):
    state, _, _ = ctx
    # Einen der Testschüler bereits in einer anderen Klasse einreihen, bevor
    # der Test-Config-Tab geöffnet wird — open_test_config muss ihn dann
    # überspringen (find_student-Check), statt ihn doppelt einzufügen.
    from server.state import QueueStudent

    other = api.TEST_STUDENTS[0]
    other_ctx = state.open_context("Andere Klasse")
    other_ctx.queue.append(
        QueueStudent(
            student_id=other["student_id"],
            lastname=other["lastname"],
            firstname=other["firstname"],
            form=other["form"],
        )
    )

    first = run(api.open_test_config(session_id="sid"))
    ctx_id = first["context_id"]
    context = state.contexts[ctx_id]
    # Alle Testschüler außer dem bereits eingereihten landen im Test-Config-Tab.
    assert len(context.queue) == len(api.TEST_STUDENTS) - 1
    assert other["student_id"] not in {s.student_id for s in context.queue}

    # Erneutes Öffnen (bestehender Tab) fügt nichts doppelt hinzu.
    second = run(api.open_test_config(session_id="sid"))
    assert second["context_id"] == ctx_id
    assert second["reused"] is True
    assert len(state.contexts[ctx_id].queue) == len(api.TEST_STUDENTS) - 1


# ---------------------------------------------------------------------------
# open-test-config: dedizierter Tab, sofort befüllt, Wieder-Öffnen reaktiviert
# ---------------------------------------------------------------------------

def test_open_test_config_populates_and_reuses(ctx):
    state, _, _ = ctx
    first = run(api.open_test_config(session_id="sid"))
    assert first["count"] == len(api.TEST_STUDENTS)
    ctx_id = first["context_id"]
    context = state.contexts[ctx_id]
    assert context.form == api.TEST_CONFIG_FORM
    assert len(context.queue) == len(api.TEST_STUDENTS)

    # Zweiter Aufruf (z. B. erneutes "+" -> "Test Config öffnen") reaktiviert
    # denselben Kontext statt eine zweite Queue anzulegen.
    second = run(api.open_test_config(session_id="sid"))
    assert second["context_id"] == ctx_id
    assert second["reused"] is True
    assert len(state.contexts) == 1
    assert state.active_context_id == ctx_id


# ---------------------------------------------------------------------------
# select-schoolyear: Guard muss über ALLE Klassen-Kontexte prüfen, nicht nur
# den aktiven Tab (Bugfix — AppState.active_students() statt nur dem aktiven
# Kontext).
# ---------------------------------------------------------------------------

def test_select_schoolyear_blocks_on_active_student_in_inactive_context(ctx):
    from server.state import QueueStudent

    state, _, _ = ctx
    # Zwei Klassen-Tabs offen; der aktive Schüler steht im NICHT-aktiven Tab.
    inactive_ctx = state.open_context("Klasse A")
    inactive_ctx.queue.append(
        QueueStudent(student_id=1, lastname="M", firstname="N", form="Klasse A", status="active")
    )
    active_ctx = state.open_context("Klasse B")  # wird zum aktiven Kontext
    assert state.active_context_id == active_ctx.id

    with pytest.raises(HTTPException) as ei:
        run(api.select_schoolyear({"schoolyear": "2026/2027"}, session_id="sid"))
    assert ei.value.status_code == 409
    assert ei.value.detail["reason"] == "active_sessions"
    # Kontexte bleiben unangetastet, solange der Guard blockiert.
    assert state.contexts

    # Mit force=True darf der Wechsel trotzdem durch.
    res = run(api.select_schoolyear(
        {"schoolyear": "2026/2027", "force": True}, session_id="sid"
    ))
    assert res == {"ok": True, "selected": "2026/2027"}
    assert state.contexts == {}


# ---------------------------------------------------------------------------
# skip / finish: Validierung
# ---------------------------------------------------------------------------

def test_skip_missing_student_id(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.skip_student({}, session_id="sid"))
    assert ei.value.status_code == 400


def test_skip_unknown_student(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.skip_student({"student_id": 999}, session_id="sid"))
    assert ei.value.status_code == 404


def test_finish_unknown_student(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.finish_student({"student_id": 999}, session_id="sid"))
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# Buchungs-Gate auf HTTP-Ebene (V10 testet nur handle_commit direkt)
# ---------------------------------------------------------------------------

def test_commit_book_blocked_when_flag_off(ctx):
    """Gate 1 (Server-Flag) greift vor confirm/Barcode — Default false."""
    with pytest.raises(HTTPException) as ei:
        run(api.commit_book({"student_id": 1, "confirm": True, "barcode": "B1"}, session_id="sid"))
    assert ei.value.status_code == 403


def test_commit_book_requires_auth(ctx):
    with pytest.raises(HTTPException) as ei:
        run(api.commit_book({"student_id": 1, "confirm": True}, session_id=None))
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# Pure Helfer
# ---------------------------------------------------------------------------

def test_last_scan_for_prefers_session(ctx):
    import server.sessions as sessions
    state, _, _ = ctx
    sess = sessions.create_student_session(state)
    sess.student_id = 4
    sess.state = "paired"
    sess.last_scan = "B-SESSION"
    assert api._last_scan_for(state, 4) == "B-SESSION"


def test_last_scan_for_empty_when_nothing(ctx):
    state, _, _ = ctx
    assert api._last_scan_for(state, 123) == ""


class _FakeRequest:
    def __init__(self, host: str):
        self.headers = {"host": host}


def test_base_url_ignores_spoofed_host_header_uses_config_ip(ctx):
    """Der Host-Header-Hostname darf NICHT in die QR-URL wandern (trägt join_secret).
    Nur der Port aus dem Host-Header übernommen; Hostname aus cfg.host_ip."""
    _, cfg, _ = ctx
    cfg.host_ip = "10.0.0.9"   # deterministischer Override
    url = api._base_url(_FakeRequest("evil.example:3443"))
    assert url == "https://10.0.0.9:3443"
    assert "evil.example" not in url


def test_base_url_rewrites_localhost(ctx, monkeypatch):
    _, cfg, _ = ctx
    cfg.host_ip = "10.0.0.9"   # expliziter Override → deterministisch
    url = api._base_url(_FakeRequest("localhost"))
    assert url == f"https://10.0.0.9:{cfg.port}"
