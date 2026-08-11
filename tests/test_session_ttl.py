"""TTL-Regeln für Modus-B-Schüler-Sessions (`expired_student_sessions`).

Kern der Regel: eine **verbundene** gepairte Session verfällt nie — der offene
Socket ist der Liveness-Beweis, nicht `last_activity`. Erst wenn die
Verbindung weg ist (Handy aus, Netz weg), läuft `paired_idle_ttl_s` ab
`disconnected_at`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from server.sessions import expired_student_sessions
from server.state import AppState, StudentSessionB

NOW = datetime(2026, 8, 11, 10, 0, 0)
CFG = SimpleNamespace(pending_pairing_ttl_s=300, paired_idle_ttl_s=1800)


def _state(session: StudentSessionB) -> AppState:
    state = AppState()
    state.student_sessions[session.session_token] = session
    return state


def _session(**kw) -> StudentSessionB:
    defaults = dict(session_token="tok", pairing_code="4242", created_at=NOW, last_activity=NOW)
    return StudentSessionB(**{**defaults, **kw})


def test_connected_paired_session_never_expires_while_quiet():
    """Schüler steht in der Schlange / Bildschirm aus: Socket offen, keine Frames."""
    session = _session(
        student_id=42,
        state="paired",
        ws=object(),
        last_activity=NOW - timedelta(hours=3),
        disconnected_at=None,
    )

    assert expired_student_sessions(_state(session), CFG, NOW) == []


def test_disconnected_paired_session_survives_short_outage():
    """Handy kurz aus → Token bleibt gültig, kein neuer Pairing-Code."""
    session = _session(
        student_id=42,
        state="paired",
        ws=None,
        last_activity=NOW - timedelta(hours=3),
        disconnected_at=NOW - timedelta(minutes=20),
    )

    assert expired_student_sessions(_state(session), CFG, NOW) == []


def test_disconnected_paired_session_expires_after_ttl():
    session = _session(
        student_id=42,
        state="paired",
        ws=None,
        disconnected_at=NOW - timedelta(minutes=31),
    )

    assert expired_student_sessions(_state(session), CFG, NOW) == [session]


def test_disconnected_paired_session_falls_back_to_last_activity():
    """`disconnected_at` fehlt (Alt-Session) → Fallback statt Unsterblichkeit."""
    session = _session(
        student_id=42,
        state="paired",
        ws=None,
        last_activity=NOW - timedelta(minutes=31),
        disconnected_at=None,
    )

    assert expired_student_sessions(_state(session), CFG, NOW) == [session]


def test_pending_pairing_expires_by_age_even_when_connected():
    """Ungepairt: Alter zählt — sonst blockiert ein offener Tab ewig einen Code."""
    session = _session(state="pending_pairing", ws=object(), created_at=NOW - timedelta(minutes=6))

    assert expired_student_sessions(_state(session), CFG, NOW) == [session]
