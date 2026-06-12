from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


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

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "name": self.name,
            "student_id": self.student_id,
            "connected": self.ws is not None,
            "last_scan": self.last_scan,
            "created_at": self.created_at.isoformat(),
        }


class AppState:
    def __init__(self) -> None:
        self.active_form: str | None = None
        self.queue: list[QueueStudent] = []
        self.helper_sessions: dict[str, HelperSession] = {}
        self.leitstand_session_ids: set[str] = set()
        self.leitstand_ws_connections: list[object] = []
        self.worker_pool: object | None = None   # automation.worker.WorkerPool
        self.iserv: object | None = None         # server.iserv_client.IsServClient
        self.student_worker_sessions: dict[int, object] = {}  # student_id -> StudentSession

    def queue_as_list(self) -> list[dict]:
        return [s.as_dict() for s in self.queue]

    def helpers_as_dict(self) -> dict:
        return {t: h.as_dict() for t, h in self.helper_sessions.items()}

    def state_snapshot(self) -> dict:
        return {
            "type": "state",
            "active_form": self.active_form,
            "queue": self.queue_as_list(),
            "helpers": self.helpers_as_dict(),
        }

    def next_pending(self) -> QueueStudent | None:
        return next((s for s in self.queue if s.status == "pending"), None)

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
