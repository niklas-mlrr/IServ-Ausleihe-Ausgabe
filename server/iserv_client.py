from __future__ import annotations

import asyncio
from urllib.parse import quote

from ausleihe import AusleiheClient


def _enc(sy: str) -> str:
    return quote(sy, safe="")


class IsServClient:
    """Async wrapper around the synchronous AusleiheClient (read-only)."""

    def __init__(self, domain: str, username: str, password: str) -> None:
        self._domain = domain
        self._username = username
        self._password = password
        self._client: AusleiheClient | None = None

    def _get_client(self) -> AusleiheClient:
        if self._client is None:
            self._client = AusleiheClient(
                domain=self._domain,
                username=self._username,
                password=self._password,
                allow_writes=False,
            )
        return self._client

    async def get_forms(self) -> list[dict]:
        """Alle Klassen des aktuellen Schuljahrs mit Schüler-Members."""
        def _sync() -> list[dict]:
            client = self._get_client()
            sy = client.schoolyears.get_current()
            forms = client.get(f"/schoolyears/{_enc(sy['id'])}/forms")
            # Nur Klassen mit mehreren Mitgliedern (>= 5) — filtert Puffer-Klassen heraus.
            return sorted(
                [f for f in forms if len(f.get("members", [])) >= 5],
                key=lambda f: (f["grade"], f["name"]),
            )
        return await asyncio.to_thread(_sync)

    async def get_class_names(self) -> list[str]:
        forms = await self.get_forms()
        return [f["name"] for f in forms]

    async def get_students_for_form(self, form_name: str) -> list[dict]:
        """Alphabetisch sortierte Schüler einer Klasse."""
        def _sync() -> list[dict]:
            client = self._get_client()
            sy = client.schoolyears.get_current()
            forms = client.get(f"/schoolyears/{_enc(sy['id'])}/forms")
            for f in forms:
                if f["name"] == form_name:
                    return sorted(
                        [
                            {
                                "student_id": m["id"],
                                "lastname": m["lastname"],
                                "firstname": m["firstname"],
                                "form": form_name,
                            }
                            for m in f.get("members", [])
                        ],
                        key=lambda s: (s["lastname"], s["firstname"]),
                    )
            return []
        return await asyncio.to_thread(_sync)

    async def get_student_info(self, student_id: int) -> dict:
        """Schüler-Daten für die Scanner-UI: Anmeldestatus, Zahlungsstatus, Bücher."""
        def _sync() -> dict:
            client = self._get_client()
            sy_id = client.schoolyears.get_current()["id"]
            detail = client.students.get_detail(
                student_id,
                enrollments=True,
                books=True,
            )
            # Aktuelle Anmeldung suchen
            current_enrollment = None
            for e in detail.get("enrollments", []):
                if e.get("schoolyear") == sy_id:
                    current_enrollment = e
                    break

            enrolled = current_enrollment is not None
            paid = False
            amount_open = None
            if current_enrollment:
                amount_open = current_enrollment.get("amountOpen")
                exemption = current_enrollment.get("exemption_accepted")
                paid = exemption is True or (amount_open is not None and float(amount_open) <= 0)

            # Bereits ausgeliehene Bücher (compact format for UI)
            current_books = [
                {
                    "code": b.get("code") or b.get("BookView", {}).get("code"),
                    "isbn": b.get("isbn") or b.get("BookView", {}).get("isbn", ""),
                }
                for b in detail.get("books", [])
            ]

            # Bücher die der Schüler laut Anmeldung erhalten soll
            books_to_receive: list[dict] = []
            if current_enrollment:
                for item in current_enrollment.get("booklistItems", []):
                    sd = item.get("series_data", {})
                    books_to_receive.append({
                        "isbn": item.get("series", ""),
                        "title": sd.get("title", ""),
                        "fee": item.get("EnrollmentBooklistItem", {}).get("fee"),
                    })

            return {
                "student_id": student_id,
                "firstname": detail.get("firstname", ""),
                "lastname": detail.get("lastname", ""),
                "enrolled": enrolled,
                "paid": paid,
                "amount_open": amount_open,
                "current_books": current_books,
                "books_to_receive": books_to_receive,
            }
        return await asyncio.to_thread(_sync)

    async def get_loan_slip_pdf(self, student_id: int, variant: str = "student") -> bytes:
        """Leihschein als PDF-Bytes (read-only GET).

        `variant="student"` → 1 Seite (Schüler-Beleg, Default),
        `variant="student-always_school-auto"` → 2 Seiten (Schüler + Schule),
        identisch zum Webseiten-Download.
        """
        def _sync() -> bytes:
            client = self._get_client()
            return client.get_loan_slip_pdf(student_id=student_id, variant=variant)
        return await asyncio.to_thread(_sync)
