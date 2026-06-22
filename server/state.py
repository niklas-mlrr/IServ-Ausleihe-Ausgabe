from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from automation.worker import StudentSession, WorkerPool

    from .iserv_client import IsServClient

# Modus-B-Lebenszyklus einer Schüler-Session.
StudentSessionState = Literal[
    "pending_pairing",  # QR gescannt, Code angezeigt, wartet auf Host-Zuordnung
    "paired",           # vom Host einem Schüler zugeordnet → Daten/Scan frei
    "completed",        # regulär abgeschlossen
    "expired",          # Timeout
    "revoked",          # vom Host abgebrochen / Ausgabe geschlossen
]


@dataclass
class QueueStudent:
    student_id: int
    lastname: str
    firstname: str
    form: str
    status: Literal["pending", "active", "done", "skipped"] = "pending"
    assigned_helper: str | None = None

    def as_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "lastname": self.lastname,
            "firstname": self.firstname,
            "form": self.form,
            "status": self.status,
            "assigned_helper": self.assigned_helper,
        }


@dataclass
class HelperSession:
    token: str
    name: str
    student_id: int | None = None
    ws: object | None = None  # WebSocket (avoid import cycle)
    created_at: datetime = field(default_factory=datetime.now)
    last_scan: str | None = None
    # ISBNs des aktuell zugewiesenen Schülers (Anmeldung + bereits ausgeliehen),
    # für die Scan-Vorabprüfung (analog Modus B).
    expected_isbns: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "name": self.name,
            "student_id": self.student_id,
            "connected": self.ws is not None,
            "last_scan": self.last_scan,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class StudentSessionB:
    """Modus-B-Schüler-Session (Live-Ausgabe).

    `session_token` ist der eigentliche Zugangs-Credential (lang, zufällig).
    `pairing_code` ist nur die menschlich vermittelte Zuordnungshilfe am
    Host und gewährt für sich genommen NIE Datenzugriff.
    """

    session_token: str
    pairing_code: str
    student_id: int | None = None
    state: StudentSessionState = "pending_pairing"
    ws: object | None = None  # WebSocket
    payment_overridden: bool = False
    last_scan: str | None = None
    # ISBNs, die der Schüler laut Anmeldung erhalten soll bzw. bereits hat.
    # Vor jedem Scan wird das gescannte Buch dagegen geprüft (Modus B).
    expected_isbns: set[str] = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.now)
    paired_at: datetime | None = None
    last_activity: datetime = field(default_factory=datetime.now)

    def as_dict_public(self) -> dict:
        """Für den Host sichtbar — bewusst OHNE Schülerdaten.

        Solange nicht `paired`, kennt der Server keinen Schülerbezug; selbst
        danach reicht hier die ID (Namen kommen aus der Queue, nicht von hier).
        """
        return {
            "pairing_code": self.pairing_code if self.state == "pending_pairing" else None,
            "state": self.state,
            "student_id": self.student_id,
            "connected": self.ws is not None,
            "age_s": int((datetime.now() - self.created_at).total_seconds()),
        }


@dataclass
class DisplaySession:
    """iPad-QR-Anzeige. Eigene Rolle ohne jeden Schülerdatenzugriff."""

    display_id: str
    registration_code: str
    authorized: bool = False
    ws: object | None = None
    created_at: datetime = field(default_factory=datetime.now)


class AppState:
    def __init__(self) -> None:
        self.active_form: str | None = None
        # Gewähltes Schuljahr (ID wie '2025/2026'); None = aktuelles Schuljahr.
        self.selected_schoolyear: str | None = None
        self.queue: list[QueueStudent] = []
        self.helper_sessions: dict[str, HelperSession] = {}
        # session_id -> letzter Zugriff (für gleitendes TTL, siehe Methoden unten).
        self.host_sessions: dict[str, datetime] = {}
        self.host_ws_connections: list[object] = []
        self.worker_pool: "WorkerPool | None" = None
        self.iserv: "IsServClient | None" = None
        self.student_worker_sessions: dict[int, "StudentSession"] = {}  # student_id -> Session
        # --- Modus B (Live-Ausgabe) ---
        self.modus_b_open: bool = False
        # Neu bei jedem Öffnen der Ausgabe erzeugt; rotiert NICHT mehr pro
        # Zuordnung (PLAN §3, 2026-06-18).
        self.modus_b_join_secret: str | None = None
        self.modus_b_join_url: str | None = None
        self.modus_b_join_qr: str | None = None  # PNG-Data-URL für iPad/Host
        self.student_sessions: dict[str, StudentSessionB] = {}  # session_token -> Session
        self.displays: dict[str, DisplaySession] = {}           # display_id -> Display

    # --- Host-Login-Sessions (gleitendes TTL) ---
    def add_host_session(self, sid: str) -> None:
        self.host_sessions[sid] = datetime.now()

    def remove_host_session(self, sid: str) -> None:
        self.host_sessions.pop(sid, None)

    def is_host_session_valid(self, sid: str | None, ttl_s: int) -> bool:
        """Gültig, wenn bekannt und nicht abgelaufen. Bei Gültigkeit gleitend
        verlängert (aktive Hosts werden nicht ausgeloggt)."""
        if not sid:
            return False
        seen = self.host_sessions.get(sid)
        if seen is None:
            return False
        if (datetime.now() - seen).total_seconds() > ttl_s:
            self.host_sessions.pop(sid, None)
            return False
        self.host_sessions[sid] = datetime.now()
        return True

    def sweep_host_sessions(self, ttl_s: int) -> None:
        now = datetime.now()
        for sid, seen in list(self.host_sessions.items()):
            if (now - seen).total_seconds() > ttl_s:
                del self.host_sessions[sid]

    def queue_as_list(self) -> list[dict]:
        return [s.as_dict() for s in self.queue]

    def helpers_as_dict(self) -> dict:
        return {t: h.as_dict() for t, h in self.helper_sessions.items()}

    def state_snapshot(self) -> dict:
        from .config import get_config
        pool = self.worker_pool
        worker_stats = (
            pool.stats() if pool is not None and hasattr(pool, "stats")
            else {"total": 0, "available": 0, "in_use": 0}
        )
        return {
            "type": "state",
            "active_form": self.active_form,
            "selected_schoolyear": self.selected_schoolyear,
            "queue": self.queue_as_list(),
            "helpers": self.helpers_as_dict(),
            "modus_b": self.modus_b_snapshot(),
            "allow_booking": get_config().allow_booking,
            "worker_pool": worker_stats,
        }

    def modus_b_snapshot(self) -> dict:
        pending = [
            s.as_dict_public()
            for s in self.student_sessions.values()
            if s.state == "pending_pairing"
        ]
        displays = [
            {
                "display_id": d.display_id,
                "authorized": d.authorized,
                "connected": d.ws is not None,
            }
            for d in self.displays.values()
        ]
        return {
            "open": self.modus_b_open,
            "join_url": self.modus_b_join_url,
            "pending": pending,
            "pending_count": len(pending),
            "displays": displays,
        }

    # --- Modus-B-Lookups ---
    def find_session_by_code(self, code: str) -> StudentSessionB | None:
        return next(
            (
                s
                for s in self.student_sessions.values()
                if s.pairing_code == code and s.state == "pending_pairing"
            ),
            None,
        )

    def find_session_by_student(self, student_id: int) -> StudentSessionB | None:
        return next(
            (
                s
                for s in self.student_sessions.values()
                if s.student_id == student_id and s.state in ("pending_pairing", "paired")
            ),
            None,
        )

    def code_in_use(self, code: str) -> bool:
        return any(
            s.pairing_code == code and s.state == "pending_pairing"
            for s in self.student_sessions.values()
        )

    def next_pending(self) -> QueueStudent | None:
        return next((s for s in self.queue if s.status == "pending"), None)

    def pending_count(self) -> int:
        return sum(1 for s in self.queue if s.status == "pending")

    def find_student(self, student_id: int) -> QueueStudent | None:
        return next((s for s in self.queue if s.student_id == student_id), None)

    def find_helper_for_student(self, student_id: int) -> HelperSession | None:
        return next(
            (h for h in self.helper_sessions.values() if h.student_id == student_id),
            None,
        )


_app_state = AppState()


def get_state() -> AppState:
    return _app_state
