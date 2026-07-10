"""Unit-Tests für den Modus-B-Session-Lebenszyklus (server/sessions + state).

Rein logisch (RAM-State) — kein IServ, kein WebSocket, kein Worker.
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions
from server.state import AppState


def test_create_and_lookup():
    st = AppState()
    s = sessions.create_student_session(st)
    assert s.state == "pending_pairing"
    assert len(s.pairing_code) == 4 and s.pairing_code.isdigit()
    assert st.find_session_by_code(s.pairing_code) is s
    assert st.code_in_use(s.pairing_code)
    assert st.student_sessions[s.session_token] is s


def test_session_tokens_and_codes_unique():
    st = AppState()
    tokens, codes = set(), set()
    for _ in range(50):
        s = sessions.create_student_session(st)
        assert s.session_token not in tokens
        assert s.pairing_code not in codes  # eindeutig unter aktiven pending-Sessions
        tokens.add(s.session_token)
        codes.add(s.pairing_code)


def test_invalidate_is_hard_and_idempotent():
    st = AppState()
    s = sessions.create_student_session(st)
    token = s.session_token
    asyncio.run(sessions.invalidate_session(st, s, "revoked", reason="test"))
    assert s.state == "revoked"
    assert token not in st.student_sessions  # Token entwertet (kein Datenzugang mehr)
    assert st.find_session_by_code(s.pairing_code) is None
    # Erneuter Aufruf ändert den terminalen Zustand nicht.
    asyncio.run(sessions.invalidate_session(st, s, "completed"))
    assert s.state == "revoked"


def test_find_session_by_student_only_active():
    st = AppState()
    s = sessions.create_student_session(st)
    s.student_id = 7
    s.state = "paired"
    assert st.find_session_by_student(7) is s
    s.state = "completed"
    assert st.find_session_by_student(7) is None
