"""Server-interne Druckerwarteschlange mit Rollen-Rangfolge und 2-in-flight.

Serialisiert alle Leihschein-Druckaufträge (Host, Helfer, künftig Schüler) an
einen physischen Drucker und ordnet sie rollen-gerecht: HOST > HELFER > SCHÜLER.

Pipeline (2-in-flight, Geschwindigkeit vor strikter Rangfolge):
  Position 0  — druckt gerade (am OS, „Wird gedruckt")
  Position 1  — schon an den Drucker gespoolt, wartet auf den Drucker
  Position 2+ — intern, noch nicht an den Drucker gesendet (rollen-gerecht geordnet)

„gedruckt" = physisches Ende, erkannt per OS-Queue-Polling
(`printing.await_print_completion`). Bewusster Trade-off: ein später eintreffender
HOST-Auftrag reiht sich hinter einen *bereits gespoolten* (Position 1) niedriger-
rangigen Auftrag, weil wir am OS nicht umsortieren — nur der druckende Auftrag ist
verbindlich, alle intern Wartenden bleiben rollen-gerecht geordnet.

Notifications (nur an den jeweiligen Urheber):
  Helfer-WS  — `print_progress` (status/position) + `print_result` (ok/fehlgeschlagen)
  Host-WS    — zielgerichtet an den startenden Host (`host_sid`), gleiche Payload
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

log = logging.getLogger(__name__)

Role = Literal["host", "helper", "student"]
JobStatus = Literal["queued", "dispatching", "spooled", "printing", "done", "failed"]

# Rangfolge für die Einfügung unter den *noch nicht gespoolten* Aufträgen:
# niedrigerer Wert = höherer Vorrang. Bereits gespoolte/druckende Aufträge sind
# am OS verbindlich und werden von der Umsortierung nicht mehr berührt.
_RANK: dict[Role, int] = {"host": 0, "helper": 1, "student": 2}


def slip_name(lastname: str | None, firstname: str | None, form: str | None) -> str:
    """„Nachname, Vorname (Form)" für das Host-Druck-Popup — der Klassen-Präfix
    „Klasse " wird abgeschnitten (IServ liefert teils „Klasse 5a", gezeigt wird
    „5a"). Fehlt die Klasse, entfällt der Klammerzusatz."""
    last = (lastname or "").strip()
    first = (firstname or "").strip()
    form_clean = (form or "").removeprefix("Klasse ").strip()
    base = ", ".join(p for p in (last, first) if p)
    return f"{base} ({form_clean})" if form_clean else base


@dataclass
class PrintJob:
    id: str
    role: Role
    student_id: int
    pages: str | None
    name: str  # „Nachname, Vorname (Form)" fürs Host-Popup
    helper_token: str | None = None  # Urheber ist ein Helfer (WS-Ziel)
    host_sid: str | None = None  # Urheber ist ein Host-Browser (sid-Ziel)
    status: JobStatus = "queued"
    result: dict | None = None  # Druck-Result (mit job_handle); Finalresult für HTTP
    job_handle: dict | None = None  # OS-Job-Handle für Completion-Polling
    created_at: datetime = field(default_factory=datetime.now)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def create(cls, **kw) -> PrintJob:
        kw.setdefault("id", uuid.uuid4().hex[:12])
        return cls(**kw)


class PrintQueue:
    """Einzige Serialisierungsstelle für Leihschein-Drucke."""

    def __init__(self) -> None:
        self.jobs: list[PrintJob] = []
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopped = False

    # ---- Lebenszyklus --------------------------------------------------

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._wake.set()  # falls schon Aufträge vor Start enqueued wurden
        self._task = asyncio.create_task(self._run(), name="print-queue-worker")

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---- Enqueue -------------------------------------------------------

    async def enqueue(self, job: PrintJob) -> int:
        """Auftrag rollen-gerecht einreihen; 0-basierte Position zurückgeben.

        Eingefügt wird vor dem ersten *noch nicht gespoolten* (queued) Auftrag
        niedrigeren Rangs; sonst ans Ende. Bereits gespoolte/druckende Aufträge
        bleiben unangetastet am Kopf (am OS verbindlich).
        """
        async with self._lock:
            idx = len(self.jobs)
            for i, j in enumerate(self.jobs):
                if j.status == "queued" and _RANK[j.role] > _RANK[job.role]:
                    idx = i
                    break
            self.jobs.insert(idx, job)
            position = idx
        self._wake.set()
        await self._notify_all()  # neuen Auftrag + ggf. verschobene Positionen live
        return position

    # ---- Worker --------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopped:
            try:
                await self._step()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — Worker darf nicht crashen
                log.exception("Print-Queue-Worker-Schritt fehlgeschlagen")
                await asyncio.sleep(1)

    async def _step(self) -> None:
        # (1) Beförderung: ein spooled-Kopf (Index 0) wird zum druckenden Kopf,
        # falls kein `printing`-Auftrag existiert (passiert nach Finalize des
        # vorigen Kopfes).
        async with self._lock:
            promote: PrintJob | None = None
            if self.jobs:
                head = self.jobs[0]
                if head.status == "spooled":
                    head.status = "printing"
                    promote = head
        if promote is not None:
            await self._notify_all()

        # (2) Pipeline füllen: Kopf (falls queued) dispatchen → printing;
        #     zweiten (falls queued) dispatchen → spooled. Dispatch ist langsam
        #     (IServ-GET + Subprocess) → außerhalb des Locks, per `dispatching`-
        #     Marker gegen Doppel-Dispatch gesichert.
        async with self._lock:
            head = self.jobs[0] if self.jobs else None
            second = self.jobs[1] if len(self.jobs) > 1 else None
            head_claim = head if head is not None and head.status == "queued" else None
            second_claim = second if second is not None and second.status == "queued" else None
            if head_claim is not None:
                head_claim.status = "dispatching"
            if second_claim is not None:
                second_claim.status = "dispatching"
        if head_claim is not None:
            await self._dispatch_to(head_claim, "printing")
        if second_claim is not None:
            await self._dispatch_to(second_claim, "spooled")

        # (3) Druckenden Kopf auf physisches Ende pollen.
        async with self._lock:
            head = self.jobs[0] if self.jobs else None
            if head is None or head.status not in ("printing", "spooled"):
                # nichts im Flug → auf neuen Auftrag warten
                wait = True
                handle = None
                failed = False
            else:
                wait = False
                handle = head.job_handle
                failed = bool(head.result and not head.result.get("ok"))
        if wait:
            await self._wait_for_work()
            return

        if not failed:
            from .printing import await_print_completion

            await await_print_completion(handle)

        # (4) Kopf finalisieren (done/failed), entfernen, Notify; daraufhin
        #     rückt der spooled-Kopf im nächsten Schritt nach (printing).
        async with self._lock:
            head = self.jobs[0] if self.jobs else None
            if head is None:
                return
            res = head.result or {}
            head.status = "done" if res.get("ok") else "failed"
            finalized = head
            self.jobs.remove(head)
            head.done.set()
        await self._notify_result(finalized)
        await self._notify_all()  # Positionen der Verbleibenden rücken nach

    async def _dispatch_to(self, job: PrintJob, head_status: JobStatus) -> None:
        """`print_loan_slip_for` rufen (langsam), dann Status setzen + notify."""
        res = await self._dispatch(job)
        async with self._lock:
            job.result = res
            job.job_handle = res.get("job_handle") if res.get("ok") else None
            job.status = head_status  # `printing` (Kopf) oder `spooled` (zweiter)
        await self._notify_all()

    async def _dispatch(self, job: PrintJob) -> dict:
        """`print_loan_slip_for` aufrufen, Exceptions in ein ok=False-Result wandeln."""
        from .sessions import print_loan_slip_for
        from .state import get_state

        try:
            return await print_loan_slip_for(get_state(), job.student_id, pages=job.pages)
        except Exception as e:  # noqa: BLE001 — Fehler als Ergebnis weiterreichen
            log.exception("Druckauftrag dispatch fehlgeschlagen (student_id=%s)", job.student_id)
            return {"ok": False, "msg": str(e)}

    async def _wait_for_work(self) -> None:
        self._wake.clear()
        await self._wake.wait()

    # ---- Notifications -------------------------------------------------

    async def _notify_all(self) -> None:
        """Allen verbleibenden Aufträgen ihre aktuelle Position + Status pushen.

        Nur die jeweils eigene `print_progress`-Nachricht (status/position/name)
        an den Urheber (Helfer-Token / Host-sid). Idempotent — Client setzt den
        Text einfach neu."""
        from .hub import get_hub
        from .state import get_state

        async with self._lock:
            snapshot = [
                (j, idx, j.status) for idx, j in enumerate(self.jobs)
            ]
        hub = get_hub()
        state = get_state()
        for job, position, status in snapshot:
            await self._send_progress(hub, state, job, position, status)

    async def _send_progress(
        self, hub, state, job: PrintJob, position: int, status: JobStatus
    ) -> None:
        msg = {
            "type": "print_progress",
            "job_id": job.id,
            "status": status,
            "position": position,
            "name": job.name,
        }
        if job.helper_token:
            await hub.send_scanner(job.helper_token, msg)
        if job.host_sid:
            for ws in list(state.host_ws_by_sid.get(job.host_sid, [])):
                await hub.send_websocket(ws, msg)

    async def _notify_result(self, job: PrintJob) -> None:
        from .hub import get_hub
        from .state import get_state

        res = job.result or {}
        base = {
            "type": "print_result",
            "job_id": job.id,
            "ok": bool(res.get("ok")),
            "name": job.name,
        }
        if res.get("ok"):
            base["detail"] = res.get("detail", "gedruckt")
        else:
            base["msg"] = res.get("msg") or res.get("detail") or "Druck fehlgeschlagen"
        hub = get_hub()
        state = get_state()
        if job.helper_token:
            await hub.send_scanner(job.helper_token, base)
        if job.host_sid:
            for ws in list(state.host_ws_by_sid.get(job.host_sid, [])):
                await hub.send_websocket(ws, base)
