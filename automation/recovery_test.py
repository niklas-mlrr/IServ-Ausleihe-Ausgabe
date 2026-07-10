"""Deterministischer Recovery-Test fuer den Playwright-Worker (Phase 2).

Erzwingt einen Session-Ablauf via context.clear_cookies() und prueft, dass
open_student() den Re-Login macht und die Kartei trotzdem laedt. Read-only —
kein Submit, keine Buchung. Braucht KEINEN laufenden Server (nutzt WorkerPool
direkt).

Aufruf: `uv run python -m automation.recovery_test`
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from automation.worker import WorkerPool  # noqa: E402
from server.iserv_client import IsServClient  # noqa: E402

DOMAIN = os.environ["ISERV_DOMAIN"]
USER = os.environ["ISERV_USERNAME"]
PW = os.environ["ISERV_PASSWORD"]


def log(*a):
    print(*a, flush=True)


async def main():
    # Einen echten Schueler holen (erste Klasse, erster Schueler) — read-only.
    iserv = IsServClient(DOMAIN, USER, PW)
    forms = await iserv.get_class_names()
    students = await iserv.get_students_for_form(forms[0])
    s = students[0]
    sid, sname = s["student_id"], f"{s['lastname']}, {s['firstname']}"
    log(f"Testschueler-ID {sid} aus '{forms[0]}' (Name nicht geloggt, PLAN 3.7)")

    pool = WorkerPool(n=1, domain=DOMAIN, username=USER, password=PW)
    await pool.start()
    assert pool._contexts, "Kein Context eingeloggt"
    log("OK  Pool gestartet, 1 Context eingeloggt")

    # --- Session-Ablauf erzwingen ---
    for ctx in pool._contexts:
        await ctx.clear_cookies()
    log("..  Cookies geloescht -> Session gilt jetzt als abgelaufen")

    # open_student muss den Login-Redirect erkennen und neu einloggen
    session = await pool.open_student(sid, sname)
    log("OK  open_student trotz abgelaufener Session durchgelaufen (Re-Login griff)")

    res = await session.submit_barcode("RECOVERY-TEST-0000")
    assert res["status"] == "staged", f"Erwartet staged, war {res}"
    log("OK  Kartei nach Recovery nutzbar (Barcode staged, kein Submit)")

    await pool.release(session)
    await pool.stop()
    log("\nRECOVERY-TEST BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
