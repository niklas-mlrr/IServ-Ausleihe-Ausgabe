"""Server-interne Druckerwarteschlange mit Drucker-Pool-Verteilung.

Serialisiert alle Leihschein-Druckaufträge (Host, Helfer, künftig Schüler) und
verteilt sie auf den konfigurierten Drucker-Pool (`RuntimeSettings.printers`):

  - Zentrale Warteschlange (`waiting`): rollen-gerecht geordnet
    (HOST > HELFER > SCHÜLER), Aufträge ohne zugewiesenen Drucker.
  - Pro Drucker 2-Slots-Pipeline: Slot 0 = druckt gerade (am OS, „Wird
    gedruckt"), Slot 1 = schon an den Drucker gespoolt, wartet auf den Drucker.
  - Verteilung (level-weise, Allowlist-gerecht): erst alle idle-Drucker (Last 0)
    einen Auftrag bekommen, dann Drucker auf Last 1 — so bekommt kein Drucker
    einen 2. Auftrag (Slot 1), solange ein anderer *erlaubter* Drucker noch idle
    ist (Parallelismus statt nacheinander). Pro Level picken die Drucker in der
    konfigurierten Reihenfolge (linkester zuerst); jeder zieht den ranghöchsten
    Auftrag, der ihn erlaubt (`job.allowed_printers`: `None` = alle erlaubt,
    sonst nur IDs darin). Sind alle Drucker voll, warten weitere Aufträge
    zentral, bis ein Drucker wieder Kapazität hat.

„gedruckt" = physisches Ende, erkannt per OS-Queue-Polling
(`printing.await_print_completion`). Bewusster Trade-off: ein später
eintreffender HOST-Auftrag reiht sich in `waiting` vor niedriger-rangige
Aufträge ein, aber bereits gespoolte/druckende Aufträge sind am OS verbindlich
und werden von der Umsortierung nicht mehr berührt.

Leerer Pool (`state.settings.printers == []`): der Scheduler dispatcht nichts —
Aufträge bleiben in `waiting`. Die Enqueue-Stellen (Host-Endpoint / Scanner-WS)
verweigern den Druck vorab mit „Kein Drucker konfiguriert", damit kein Auftrag
endlos wartet.

Notifications (nur an den jeweiligen Urheber):
  Helfer-WS  — `print_progress` (status/position/printer) + `print_result` (ok/fehlgeschlagen)
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
_SlotType = Literal["printing", "spooled"]

# Rangfolge für die Einfügung in die zentrale Warteschlange (`waiting`):
# niedrigerer Wert = höherer Vorrang. Bereits gespoolte/druckende Aufträge sind
# am OS verbindlich und werden von der Umsortierung nicht mehr berührt.
_RANK: dict[Role, int] = {"host": 0, "helper": 1, "student": 2}

# Kapazität je Drucker: 1 druckend (Slot 0) + 1 gespoolt (Slot 1).
_PRINTER_CAPACITY = 2


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
    assigned_printer_id: str | None = None  # None = in zentraler Warteschlange
    # Erlaubte Drucker für diesen Auftrag (Snapshot der Klassen-Allowlist zum
    # Enqueue-Zeitpunkt). `None` = jeder Pool-Drucker erlaubt (Default, keine
    # Einschränkung); eine Menge (auch leer) beschränkt auf genau diese IDs.
    # Bereits wartende Aufträge behalten ihre Allowlist, auch wenn die Klasse
    # später umkonfiguriert wird (gewollt: „mit in der Warteschlange gespeichert").
    allowed_printers: set[str] | None = None
    created_at: datetime = field(default_factory=datetime.now)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def create(cls, **kw) -> PrintJob:
        kw.setdefault("id", uuid.uuid4().hex[:12])
        return cls(**kw)


@dataclass
class _Slots:
    """2-Slots-Pipeline eines Druckers: Slot 0 druckt, Slot 1 ist gespoolt."""

    printing: PrintJob | None = None
    spooled: PrintJob | None = None

    @property
    def load(self) -> int:
        return (1 if self.printing else 0) + (1 if self.spooled else 0)


class PrintQueue:
    """Einzige Serialisierungs-/Verteilungsstelle für Leihschein-Drucke."""

    def __init__(self) -> None:
        self.waiting: list[PrintJob] = []
        self.slots: dict[str, _Slots] = {}
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
        """Auftrag rollen-gerecht in die zentrale Warteschlange einreihen;
        0-basierte Position in `waiting` zurückgeben."""
        async with self._lock:
            idx = len(self.waiting)
            for i, j in enumerate(self.waiting):
                if _RANK[j.role] > _RANK[job.role]:
                    idx = i
                    break
            self.waiting.insert(idx, job)
            position = idx
        self._wake.set()
        await self._notify_all()
        return position

    def wake(self) -> None:
        """Scheduler wecken — aufrufen, wenn ein Drucker hinzugefügt wurde
        (wartende Aufträge können jetzt verteilt werden)."""
        self._wake.set()

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
        from .state import get_state

        printers = list(get_state().settings.printers)

        # (1) Befördern: pro Drucker mit leerem printing- aber belegtem
        #     spooled-Slot → spooled wird printing (Status spooled→printing).
        async with self._lock:
            self._reconcile(printers)
            promoted = self._promote_all()
        if promoted:
            await self._notify_all()

        # (2) Pipeline füllen: wartende Aufträge auf freie Drucker-Slots
        #     verteilen (niedrigste Last, linkester Tie-Break). Claims sammeln
        #     (langsame Dispatches erfolgen außerhalb des Locks).
        async with self._lock:
            claims = self._claim_fills(printers)
        if claims:
            await asyncio.gather(
                *(self._dispatch_to(c) for c in claims), return_exceptions=True
            )
            await self._notify_all()

        # (3) Druckende Aufträge auf physisches Ende pollen (parallel).
        async with self._lock:
            polls = self._collect_polls(printers)
        if polls:
            await asyncio.gather(
                *(self._poll_one(pid, job) for pid, job in polls),
                return_exceptions=True,
            )
            return  # finalize ist in _poll_one; nächster Schritt befördert/füllt neu

        # (4) Nichts im Flug und nichts befüllbar → auf neuen Auftrag /
        #     Config-Wechsel warten.
        if not promoted and not claims:
            await self._wait_for_work()

    # ---- Reconcile -----------------------------------------------------

    def _reconcile(self, printers: list) -> None:
        """Slots an die aktuelle Drucker-Konfiguration anpassen: für jeden
        konfigurierten Drucker einen Slot-Eintrag sicherstellen. Entfernte
        Drucker mit noch aktiven Jobs bleiben als verwaiste Slots erhalten,
        bis sie ausgedrained sind (neue Zuweisungen bekommen sie nicht, da
        `_claim_fills`/`_collect_polls` nur über `printers` iterieren). Leer
        laufende verwaiste Slots werden aufgeräumt."""
        for p in printers:
            self.slots.setdefault(p.id, _Slots())
        # Verwaiste, leere Slots entfernen (Drucker nicht mehr konfiguriert).
        configured_ids = {p.id for p in printers}
        orphaned_empty = [
            pid for pid, s in self.slots.items()
            if pid not in configured_ids and s.load == 0
        ]
        for pid in orphaned_empty:
            del self.slots[pid]

    # ---- Befördern / Füllen / Pollen / Finalisieren --------------------

    def _promote_all(self) -> list[str]:
        """Pro Drucker: printing leer & spooled belegt → spooled rückt auf
        printing. Liefert die Druckerkennungen, bei denen befördert wurde."""
        promoted: list[str] = []
        for pid, s in self.slots.items():
            if s.printing is None and s.spooled is not None:
                s.printing = s.spooled
                s.spooled = None
                s.printing.status = "printing"
                promoted.append(pid)
        return promoted

    def _claim_fills(self, printers: list) -> list[tuple[str, str | None, PrintJob, _SlotType]]:
        """Wartende Aufträge auf freie Drucker-Slots verteilen — level-weise und
        Allowlist-gerecht (s. Modul-Docstring). Liefert Claims
        `(printer_id, printer_name, job, slot_type)`; der Job wird aus `waiting`
        entfernt, in den Slot gelegt und auf `dispatching` gesetzt. Dispatch
        erfolgt außerhalb des Locks. Fehlende Slots (z. B. vor dem ersten
        Reconcile) gelten als leer (Last 0).

        Level-weise (erst Last 0, dann Last 1): so bekommt kein Drucker einen
        2. Auftrag, solange ein anderer *erlaubter* Drucker noch idle ist
        (Parallelismus). Pro Level in der konfigurierten Reihenfolge
        (linkester zuerst); jeder Drucker zieht den ranghöchsten Auftrag, der
        ihn erlaubt (`allowed_printers is None` = alle, sonst ID darin). Ist der
        Kopf der Warteschlange für mehrere freie Drucker erlaubt, druckt der
        linkeste — weil er zuerst pickt."""
        claims: list[tuple[str, str | None, PrintJob, _SlotType]] = []
        # Level 0 (idle) vor Level 1: verhindert, dass ein Drucker einen 2. Auftrag
        # bekommt, während ein anderer erlaubter Drucker noch idle ist.
        for target_load in range(_PRINTER_CAPACITY):
            for printer in printers:
                s = self.slots.get(printer.id)
                load = s.load if s else 0
                if load != target_load or load >= _PRINTER_CAPACITY:
                    continue  # nur Drucker auf diesem Füll-Level, mit Kapazität
                # ersten ranghöchsten Auftrag suchen, der diesen Drucker erlaubt.
                picked = None
                for i, job in enumerate(self.waiting):
                    if job.allowed_printers is None or printer.id in job.allowed_printers:
                        picked = i
                        break
                if picked is None:
                    continue  # kein Auftrag erlaubt diesen Drucker → idle bleiben
                job = self.waiting.pop(picked)
                job.assigned_printer_id = printer.id
                job.status = "dispatching"
                slots = self.slots.setdefault(printer.id, _Slots())
                slot_type: _SlotType = "printing" if slots.printing is None else "spooled"
                if slot_type == "printing":
                    slots.printing = job
                else:
                    slots.spooled = job
                claims.append((printer.id, printer.name, job, slot_type))
        return claims

    async def _dispatch_to(self, claim: tuple[str, str | None, PrintJob, _SlotType]) -> None:
        """`print_loan_slip_for` rufen (langsam), dann Status + Handle setzen."""
        _pid, printer_name, job, slot_type = claim
        res = await self._dispatch(job, printer_name=printer_name)
        async with self._lock:
            job.result = res
            job.job_handle = res.get("job_handle") if res.get("ok") else None
            job.status = slot_type  # `printing` (Slot 0) oder `spooled` (Slot 1)
        await self._notify_all()

    async def _dispatch(self, job: PrintJob, *, printer_name: str | None) -> dict:
        """`print_loan_slip_for` aufrufen, Exceptions in ein ok=False-Result wandeln."""
        from .sessions import print_loan_slip_for
        from .state import get_state

        try:
            return await print_loan_slip_for(
                get_state(), job.student_id, pages=job.pages, printer_name=printer_name
            )
        except Exception as e:  # noqa: BLE001 — Fehler als Ergebnis weiterreichen
            log.exception("Druckauftrag dispatch fehlgeschlagen (student_id=%s)", job.student_id)
            return {"ok": False, "msg": str(e)}

    def _collect_polls(self, printers: list) -> list[tuple[str, PrintJob]]:
        """Druckende Aufträge (Slot 0) sammeln, die Completion-Polling brauchen.
        Bereits fehlgeschlagene (ok=False) werden hier nicht gepollt — sie werden
        im nächsten Schritt finalisiert (Status steht auf `printing`, aber
        result.ok=False); finalize erkennt das am Result."""
        polls: list[tuple[str, PrintJob]] = []
        for p in printers:
            s = self.slots.get(p.id)
            if s and s.printing is not None and s.printing.status == "printing":
                polls.append((p.id, s.printing))
        return polls

    async def _poll_one(self, printer_id: str, job: PrintJob) -> None:
        """Auf physisches Druckende pollen, dann finalisieren."""
        from .printing import await_print_completion

        res = job.result or {}
        if res.get("ok"):
            await await_print_completion(job.job_handle)
        await self._finalize(printer_id, job)

    async def _finalize(self, printer_id: str, job: PrintJob) -> None:
        """Erledigten printing-Job aus dem Slot nehmen, done/failed setzen,
        Urheber benachrichtigen. Sein evtl. spooled-Nachbar rückt im nächsten
        Schritt auf printing (Beförderung)."""
        async with self._lock:
            s = self.slots.get(printer_id)
            if s is None or s.printing is not job:
                return  # zwischenzeitlich entfernt/verschieben — nichts zu tun
            res = job.result or {}
            job.status = "done" if res.get("ok") else "failed"
            s.printing = None
            job.done.set()
            finalized = job
        await self._notify_result(finalized)
        await self._notify_all()  # Positionen der Verbleibenden rücken nach

    async def _wait_for_work(self) -> None:
        self._wake.clear()
        await self._wake.wait()

    # ---- Pool-Snapshot (für state_snapshot) ---------------------------

    def pool_printers(self, printers: list) -> list[dict]:
        """Pro Drucker den Live-Status für den Host-Snapshot: Last, belegte
        Slots (Job-Name) und Duplex. Iteriert in der konfigurierten Reihenfolge
        (bestimmt die Verteilungspriorität).

        Lock-frei (synchrone Lesefunktion, aufgerufen vom sync `state_snapshot`
        — das async-Lock ist dort nicht verfügbar). Konsistenz ist für die
        Statusanzeige ausreichend: Referenz-Reads sind in CPython atomar, ein
        konkurrierender Scheduler-Schritt liefert allenfalls einen minimal
        veralteten, aber nie zerrissenen Stand."""
        out: list[dict] = []
        for p in printers:
            s = self.slots.get(p.id)
            out.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "duplex": p.duplex,
                    "is_default": p.name is None,
                    "load": s.load if s else 0,
                    "printing_name": (s.printing.name if s and s.printing else None),
                    "spooled_name": (s.spooled.name if s and s.spooled else None),
                }
            )
        return out

    def pool_summary(self) -> dict:
        """Aggregat über die Warteschlange (für den Host-Snapshot)."""
        # Kein Lock nötig — `len()` ist atomar genug für die Anzeige; ein
        # gleichzeitiger Druckauftrag kann die Zahl um 1 verfälschen, was für
        # die Statusanzeige irrelevant ist.
        return {"waiting": len(self.waiting)}

    def waiting_list(self, state) -> list[dict]:
        """Wartende Aufträge (zentrale Warteschlange, noch ohne zugewiesenen
        Drucker) für den Host-Snapshot: Position, Schüler, Klasse, Auftraggeber
        und die erlaubten Drucker (Allowlist der Klasse zum Enqueue-Zeitpunkt).
        Lock-frei (Anzeige-Konsistenz reicht, s. `pool_printers`).

        Schüler-/Klassen-/Urheber-Lookup live aus dem State, nicht zum
        Enqueue-Zeitpunkt eingefroren — ein Helfer kann sich zwischenzeitlich
        umbenannt haben, ein Schüler aus der Kontext-Queue gerutscht sein
        (dann Fallback auf den am Auftrag gespeicherten `name`). Die Allowlist
        hingegen ist am Auftrag gespeichert und bleibt stabil, auch wenn die
        Klasse später umkonfiguriert wird (s. PrintJob.allowed_printers)."""
        pool = list(state.settings.printers)
        out: list[dict] = []
        for idx, j in enumerate(self.waiting):
            student = state.find_student(j.student_id)
            if student is not None:
                student_name = slip_name(student.lastname, student.firstname, None)
                form_clean = (student.form or "").removeprefix("Klasse ").strip()
                form = form_clean or None
            else:
                # Schüler nicht mehr in einer aktiven Kontext-Queue — der am
                # Auftrag hinterlegte `name` trägt die Form in Klammern.
                student_name = j.name
                form = None
            # Erlaubte Drucker in Pool-Priorität (linkester zuerst). `None` =
            # alle Pool-Drucker; sonst nur die IDs darin. Verwaiste IDs (Drucker
            # nach dem Enqueue entfernt) fallen raus — `all_allowed=False` mit
            # leerer Liste signalisiert dem Host einen nicht bedienbaren Auftrag.
            if j.allowed_printers is None:
                all_allowed = True
                allowed_names = [self._printer_display(p) for p in pool]
            else:
                all_allowed = False
                allowed_names = [
                    self._printer_display(p) for p in pool if p.id in j.allowed_printers
                ]
            out.append(
                {
                    "position": idx,
                    "student": student_name,
                    "form": form,
                    "originator": self._originator_label(state, j),
                    "all_allowed": all_allowed,
                    "allowed_printers": allowed_names,
                }
            )
        return out

    @staticmethod
    def _printer_display(p) -> str:
        """Anzeige-Label eines Pool-Druckers für die Warteschlangen-Liste:
        `name=None` ist der Standarddrucker des Geräts (s. PrinterConfig)."""
        return "Standarddrucker" if p.name is None else p.name

    def _originator_label(self, state, job: PrintJob) -> str:
        """Auftraggeber für die Warteschlangen-Anzeige: Helfer namentlich
        (Token-Lookup), Host als „Host", Schüler als „Schüler" (derzeit nicht
        enqueueiert, s. Modul-Docstring „künftig"). Fallback auf die Rolle."""
        if job.helper_token:
            h = state.helper_sessions.get(job.helper_token)
            name = getattr(h, "name", None) if h is not None else None
            return name or "Helfer"
        if job.host_sid:
            return "Host"
        if job.role == "student":
            return "Schüler"
        return job.role or "–"

    # ---- Notifications -------------------------------------------------

    async def _notify_all(self) -> None:
        """Allen Aufträgen ihre aktuelle Position + Status pushen (nur an den
        jeweiligen Urheber). Position: Index in `waiting` (zentrale
        Warteschlange) bzw. 0 für druckende/gespoolte Aufträge am Drucker."""
        from .hub import get_hub
        from .state import get_state

        async with self._lock:
            snapshot: list[tuple[PrintJob, int, JobStatus, str | None]] = []
            for idx, j in enumerate(self.waiting):
                snapshot.append((j, idx, j.status, self._printer_name(j.assigned_printer_id)))
            for s in self.slots.values():
                if s.printing is not None:
                    pjob = s.printing
                    pname = self._printer_name(pjob.assigned_printer_id)
                    snapshot.append((pjob, 0, pjob.status, pname))
                if s.spooled is not None:
                    sjob = s.spooled
                    sname = self._printer_name(sjob.assigned_printer_id)
                    snapshot.append((sjob, 1, sjob.status, sname))
        hub = get_hub()
        state = get_state()
        for job, position, status, printer in snapshot:
            await self._send_progress(hub, state, job, position, status, printer)
        # Druck-Übergänge (dispatch/spool/druckt/fertig) als vollen State an
        # alle verbundenen Hosts pushen, damit deren Druckerwarteschlangen-Box
        # live folgt — der Snapshot spiegelt über `pool_printers`/`pool_summary`
        # Last und zentrale Warteschlange. `send_all_hosts` (ohne Queue-Size-
        # Folgebroadcast) hält die Helfer-WS frei von Zustands-Pushes, die nur
        # den Host interessieren. Ohne verbundene Hosts (auch im Test) entfällt
        # der Snapshot-Aufwand komplett.
        if state.host_ws_connections:
            await hub.send_all_hosts(state.state_snapshot())

    def _printer_name(self, printer_id: str | None) -> str | None:
        """Druckername zur Kennung (für Notifications). None = Standarddrucker
        (Name None). Verwaiste Kennungen → None."""
        if printer_id is None:
            return None
        from .state import get_state

        for p in get_state().settings.printers:
            if p.id == printer_id:
                return p.name
        return None

    async def _send_progress(
        self, hub, state, job: PrintJob, position: int, status: JobStatus, printer: str | None
    ) -> None:
        msg = {
            "type": "print_progress",
            "job_id": job.id,
            "status": status,
            "position": position,
            "name": job.name,
            "printer": printer,
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
