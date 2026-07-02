from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from urllib.parse import quote

log = logging.getLogger(__name__)

# Default-Schuljahr neu bestimmen, wenn der Cache älter als das ist. Fängt den
# upcoming→running-Übergang ab, falls der Server über einen Schuljahresbeginn
# hinweg läuft, ohne pro Anfrage neu aufzulösen.
_DEFAULT_SY_TTL_S = 6 * 3600

from ausleihe import AusleiheClient
from ausleihe.exceptions import NotFoundError


def _enc(sy: str) -> str:
    return quote(sy, safe="")


def _sy_date(s: object) -> date | None:
    """ISO-String ('2025-08-01T…') → date; tolerant gegenüber fehlenden Werten."""
    try:
        return date.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _sort_ts(s: object) -> float:
    """ISO-Datum → Unix-Timestamp (für Sortierung); fehlend/ungültig → 0.0.

    Toleriert ein angehängtes 'Z' (UTC), das fromisoformat vor 3.11 nicht mag.
    """
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


class IsServClient:
    """Async wrapper around the synchronous AusleiheClient (read-only)."""

    def __init__(self, domain: str, username: str, password: str) -> None:
        self._domain = domain
        self._username = username
        self._password = password
        self._client: AusleiheClient | None = None
        # ISBN -> Series, einmalig (read-only GET /series) für Titel + Fach.
        self._series_map: dict | None = None
        # Default-Schuljahr (laufend, sonst nächstes); lazy gecacht mit TTL, damit
        # der upcoming→running-Übergang nicht erst nach einem Neustart greift.
        self._default_sy_id: str | None = None
        self._default_sy_at: datetime | None = None

    def _get_client(self) -> AusleiheClient:
        if self._client is None:
            self._client = AusleiheClient(
                domain=self._domain,
                username=self._username,
                password=self._password,
                allow_writes=False,
            )
        return self._client

    def _active_years(self, client: AusleiheClient) -> list[dict]:
        """Nicht-archivierte Schuljahre (read-only GET /schoolyears)."""
        return [y for y in client.admin.get_schoolyears() if not y.get("archived_at")]

    def _pick_default(self, years: list[dict], client: AusleiheClient) -> str:
        """Default-Schuljahr: das aktuell laufende; läuft keines, das nächste.

        „Laufend" = heute liegt zwischen `begin` und `end`. Gibt es keine
        Überschneidung (z.B. Sommerpause vor Schuljahresbeginn), wird das
        Jahr mit dem nächstgelegenen künftigen Beginn gewählt. Fallback
        (alle Jahre vergangen): `/schoolyears/current`.
        """
        today = date.today()
        dated = [
            (y, _sy_date(y.get("begin")), _sy_date(y.get("end")))
            for y in years
        ]
        dated = [(y, b, e) for (y, b, e) in dated if b and e]

        running = [y for (y, b, e) in dated if b <= today <= e]
        if running:
            # Bei (untypischer) Überschneidung das jüngste laufende Jahr.
            return max(running, key=lambda y: _sy_date(y["begin"]))["id"]

        upcoming = [(y, b) for (y, b, e) in dated if b > today]
        if upcoming:
            return min(upcoming, key=lambda t: t[1])[0]["id"]

        return client.schoolyears.get_current()["id"]

    def _resolve_sy(self, client: AusleiheClient, schoolyear: str | None) -> str:
        """Explizit gewähltes Schuljahr oder das gecachte Default-Jahr (mit TTL)."""
        if schoolyear:
            return schoolyear
        now = datetime.now()
        stale = (
            self._default_sy_at is None
            or (now - self._default_sy_at).total_seconds() > _DEFAULT_SY_TTL_S
        )
        if self._default_sy_id is None or stale:
            self._default_sy_id = self._pick_default(self._active_years(client), client)
            self._default_sy_at = now
        return self._default_sy_id

    async def get_schoolyears(self) -> list[dict]:
        """Auswählbare (nicht-archivierte) Schuljahre, neuestes zuerst.

        Liefert pro Jahr `id` (z.B. '2025/2026'), `name` und `default`-Flag
        (genau ein Jahr ist Default: das laufende bzw. – wenn keines läuft –
        das nächste).
        """
        def _sync() -> list[dict]:
            client = self._get_client()
            years = self._active_years(client)
            default_id = self._pick_default(years, client)
            self._default_sy_id = default_id  # Cache mitnehmen
            self._default_sy_at = datetime.now()
            out = [
                {
                    "id": y["id"],
                    "name": y.get("name") or y["id"],
                    "default": y["id"] == default_id,
                }
                for y in years
            ]
            out.sort(key=lambda y: y["id"], reverse=True)
            return out
        return await asyncio.to_thread(_sync)

    def _get_series_map(self) -> dict:
        """Serien-Katalog (ISBN -> Series) prozessweit gecacht.

        Der Katalog ist während einer Ausgabe statisch; ein GET /series reicht,
        um Titel und Fach (subjects_flat) für alle Bücher aufzulösen.
        """
        if self._series_map is None:
            client = self._get_client()
            self._series_map = {s.isbn: s for s in client.series.get_all()}
        return self._series_map

    async def get_forms(self, schoolyear: str | None = None) -> list[dict]:
        """Alle Klassen des (gewählten oder aktuellen) Schuljahrs mit Members."""
        def _sync() -> list[dict]:
            client = self._get_client()
            sy_id = self._resolve_sy(client, schoolyear)
            forms = client.get(f"/schoolyears/{_enc(sy_id)}/forms")
            # Nur Klassen mit mehreren Mitgliedern (>= 5) — filtert Puffer-Klassen heraus.
            return sorted(
                [f for f in forms if len(f.get("members", [])) >= 5],
                key=lambda f: (f["grade"], f["name"]),
            )
        return await asyncio.to_thread(_sync)

    async def get_class_names(self, schoolyear: str | None = None) -> list[str]:
        forms = await self.get_forms(schoolyear)
        return [f["name"] for f in forms]

    async def get_students_for_form(
        self, form_name: str, schoolyear: str | None = None
    ) -> list[dict]:
        """Alphabetisch sortierte Schüler einer Klasse."""
        def _sync() -> list[dict]:
            client = self._get_client()
            sy_id = self._resolve_sy(client, schoolyear)
            forms = client.get(f"/schoolyears/{_enc(sy_id)}/forms")
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

    async def get_student_info(
        self, student_id: int, schoolyear: str | None = None
    ) -> dict:
        """Schüler-Daten für die Scanner-UI: Anmeldestatus, Zahlungsstatus, Bücher."""
        def _sync() -> dict:
            client = self._get_client()
            series_map = self._get_series_map()

            def _fach(isbn: str) -> str:
                s = series_map.get(isbn)
                if not s:
                    return ""
                return ", ".join(s.subjects_flat or s.subjects or [])

            def _title(isbn: str, fallback: str = "") -> str:
                s = series_map.get(isbn)
                return (s.title if s else "") or fallback or isbn

            sy_id = self._resolve_sy(client, schoolyear)
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
            current_books = []
            for b in detail.get("books", []):
                isbn = b.get("isbn") or b.get("BookView", {}).get("isbn", "")
                current_books.append({
                    "code": b.get("code") or b.get("BookView", {}).get("code"),
                    "isbn": isbn,
                    "title": _title(isbn),
                    "subject": _fach(isbn),
                    "distributed_at": b.get("distributed_at")
                        or b.get("BookView", {}).get("distributed_at"),
                })

            # Bücher die der Schüler laut Anmeldung erhalten soll
            books_to_receive: list[dict] = []
            if current_enrollment:
                for item in current_enrollment.get("booklistItems", []):
                    sd = item.get("series_data", {})
                    isbn = item.get("series", "")
                    books_to_receive.append({
                        "isbn": isbn,
                        "title": _title(isbn, sd.get("title", "")),
                        "subject": _fach(isbn),
                        "fee": item.get("EnrollmentBooklistItem", {}).get("fee"),
                    })

            # Einheitliche Buchliste für die Scanner-Tabelle:
            # vorgemerkt (noch nicht ausgeliehen) zuerst, ausgeliehen darunter.
            lent_isbns = {b["isbn"] for b in current_books if b["isbn"]}
            lent_by_isbn = {b["isbn"]: b for b in current_books if b["isbn"]}
            books: list[dict] = []
            seen_isbns: set[str] = set()
            for b in books_to_receive:
                isbn = b["isbn"]
                ausgeliehen = isbn in lent_isbns
                books.append({
                    "isbn": isbn,
                    "code": lent_by_isbn.get(isbn, {}).get("code") if ausgeliehen else None,
                    "title": b["title"],
                    "subject": b["subject"],
                    "status": "ausgeliehen" if ausgeliehen else "vorgemerkt",
                    "distributed_at": lent_by_isbn.get(isbn, {}).get("distributed_at")
                        if ausgeliehen else None,
                })
                seen_isbns.add(isbn)
            # Ausgeliehene Bücher ohne passende Vormerkung trotzdem zeigen.
            for b in current_books:
                if b["isbn"] and b["isbn"] in seen_isbns:
                    continue
                books.append({
                    "isbn": b["isbn"],
                    "code": b["code"],
                    "title": b["title"],
                    "subject": b["subject"],
                    "status": "ausgeliehen",
                    "distributed_at": b.get("distributed_at"),
                })
            # 1. nach Status (vorgemerkt vor ausgeliehen, wie gehabt),
            # 2. nach Ausgabezeit absteigend (jüngste oben; negativer Timestamp),
            # 3. alphabetisch als stabiler Fallback (z. B. für vorgemerkte ohne Zeit).
            books.sort(key=lambda x: (
                0 if x["status"] == "vorgemerkt" else 1,
                -_sort_ts(x.get("distributed_at")),
                x["subject"],
                x["title"],
            ))

            return {
                "student_id": student_id,
                "firstname": detail.get("firstname", ""),
                "lastname": detail.get("lastname", ""),
                "enrolled": enrolled,
                "paid": paid,
                "amount_open": amount_open,
                "current_books": current_books,
                "books_to_receive": books_to_receive,
                "books": books,
            }
        return await asyncio.to_thread(_sync)

    async def get_class_book_catalog(
        self, form_name: str, schoolyear: str | None = None
    ) -> list[dict]:
        """Ausleihbare Bücher des Jahrgangs einer Klasse (read-only).

        Grundlage für die klassenweite Bücher-Reihenfolge im Scanner. Quelle ist die
        **Jahrgangs-Bücherliste** (`GET /schoolyears/:sy/booklists/:id`), NICHT die
        Vereinigung der Einzelanmeldungen — so erscheinen alle für den Jahrgang
        ausleihbaren Titel, unabhängig davon, welche Schüler gerade angemeldet sind.

        Klassenstufe → Booklist über `form["grade"]` == `booklist["grade"]`. Aus der
        Liste werden **alle ausleihbaren** Items (`borrowable=True`) genommen (keine
        Kauf-/Arbeitshefte), nach ISBN dedupliziert (Items wiederholen sich über
        Sections/Options) und nach `(subject, title)` sortiert. `series_data` der
        Booklist ist verlässlich (Titel/Fach/ISBN direkt).

        **Mehrjahresbände sind bewusst enthalten** (2026-07-02d): die komplette
        ausleihbare Jahrgangsliste wird gezeigt, auch wenn ein Band die Klassenstufe
        nur als oberen Jahrgang führt.

        Liefert `[{isbn, title, subject}]`; leer, wenn die Klasse/Jahrgang keine
        Booklist hat. Nur GETs.
        """
        def _sync() -> list[dict]:
            client = self._get_client()
            series_map = self._get_series_map()

            sy_id = self._resolve_sy(client, schoolyear)
            forms = client.get(f"/schoolyears/{_enc(sy_id)}/forms")
            form_grade = next(
                (f.get("grade") for f in forms if f["name"] == form_name), None
            )
            if form_grade is None:
                return []

            booklists = client.schoolyears.get_booklists(sy_id)
            bl = next((b for b in booklists if b.get("grade") == form_grade), None)
            if not bl:
                log.warning("Keine Booklist für Jahrgang %s (Klasse %s)", form_grade, form_name)
                return []
            full = client.schoolyears.get_booklist(sy_id, bl["id"])

            seen: set[str] = set()
            catalog: list[dict] = []
            for sec in full.get("sections", []):
                for opt in sec.get("options", []):
                    for item in opt.get("items", []):
                        if not item.get("borrowable"):
                            continue  # Kauf-/Arbeitshefte raus
                        sd = item.get("series_data") or {}
                        isbn = sd.get("isbn") or item.get("series") or ""
                        if not isbn or isbn in seen:
                            continue
                        seen.add(isbn)
                        s = series_map.get(isbn)
                        title = sd.get("title") or (s.title if s else "") or isbn
                        subject = ", ".join(
                            sd.get("subjectsFlat")
                            or (s.subjects_flat or s.subjects if s else [])
                            or []
                        )
                        catalog.append({"isbn": isbn, "title": title, "subject": subject})

            catalog.sort(key=lambda b: (b["subject"], b["title"]))
            return catalog
        return await asyncio.to_thread(_sync)

    async def get_book_by_code(self, code: str) -> dict | None:
        """Buch zu einem gescannten Barcode auflösen (read-only GET /books/{code}).

        Liefert `{code, isbn, title, subject, available, distributed, deleted,
        student_id}` oder `None`, wenn die API das Buch nicht kennt (404). Andere
        Fehler (Auth/Netz) werden durchgereicht, damit sie nicht fälschlich als
        „Buch unbekannt" interpretiert werden.

        `available`/`distributed`/`deleted` bilden den Lager-Status ab: „im Lager"
        = `available and not distributed and not deleted` (Grundlage für die
        Buchungs-Vorabprüfung, PLAN §6 / Freigabe 2026-07-02).
        """
        def _sync() -> dict | None:
            client = self._get_client()
            series_map = self._get_series_map()
            try:
                book = client.books.get_by_code(code)
            except NotFoundError:
                return None
            isbn = book.isbn or ""
            s = series_map.get(isbn)
            subject = ", ".join(s.subjects_flat or s.subjects or []) if s else ""
            title = (s.title if s else "") or isbn
            return {
                "code": book.code,
                "isbn": isbn,
                "title": title,
                "subject": subject,
                "available": bool(book.available),
                "distributed": bool(book.distributed),
                "deleted": bool(book.deleted),
                "student_id": book.student_id,
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
