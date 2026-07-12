from __future__ import annotations

import asyncio
import logging
import threading
from datetime import date, datetime
from urllib.parse import quote

from ausleihe import AusleiheClient
from ausleihe.exceptions import NotFoundError

log = logging.getLogger(__name__)

# Default-Schuljahr neu bestimmen, wenn der Cache älter als das ist. Fängt den
# upcoming→running-Übergang ab, falls der Server über einen Schuljahresbeginn
# hinweg läuft, ohne pro Anfrage neu aufzulösen.
# Trade-off: die 6h TTL verzögert auch den running→ended-Übergang (ein abgelaufen
# laufendes Jahr wird bis zu 6h lang noch als Default gemeldet). Wird bewusst in
# Kauf genommen, weil der Sommerferien-Puffer typischerweise länger ist als 6h
# und der upcoming-Fall häufiger auftritt als ein exakt zum TTL-Stichtag laufendes
# Jahr, das gerade endet.
_DEFAULT_SY_TTL_S = 6 * 3600


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
        # Lock schützt die Lazy-Init der drei Singletons (_client, _series_map,
        # _default_sy_id) gegen Concurrent-Instantiate: mehrere asyncio.to_thread-
        # Worker können sonst gleichzeitig _client is None sehen, jeweils einen
        # AusleiheClient instanziieren und sich gegenseitig überschreiben →
        # orphaned requests.Session + geteilte Session über Threads hinweg
        # (Cookie/Connection-State-Korruption). Lock wird nur für die Init-Phase
        # gehalten; danach läuft die eigentliche API-Call-Arbeit außerhalb.
        self._init_lock = threading.Lock()

    def _get_client(self) -> AusleiheClient:
        if self._client is None:
            with self._init_lock:
                # Double-checked locking: nach dem Lock-Erwerb nochmal prüfen,
                # ein anderer Worker könnte uns zuvor gekommen sein.
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
        dated = [(y, _sy_date(y.get("begin")), _sy_date(y.get("end"))) for y in years]
        dated = [(y, b, e) for (y, b, e) in dated if b and e]

        running = [y for (y, b, e) in dated if b <= today <= e]
        if running:
            # Bei (untypischer) Überschneidung das jüngste laufende Jahr.
            return max(running, key=lambda y: _sy_date(y["begin"]))["id"]

        upcoming = [(y, b) for (y, b, e) in dated if b > today]
        if upcoming:
            return min(upcoming, key=lambda t: t[1])[0]["id"]

        return client.schoolyears.get_current()["id"]

    def _sy_cache_stale(self, now: datetime | None = None) -> bool:
        """Ob der gecachte Default-Schuljahr-Wert die TTL überschritten hat."""
        if self._default_sy_id is None:
            return True
        if self._default_sy_at is None:
            return True
        now = now or datetime.now()
        return (now - self._default_sy_at).total_seconds() > _DEFAULT_SY_TTL_S

    def _resolve_sy(self, client: AusleiheClient, schoolyear: str | None) -> str:
        """Explizit gewähltes Schuljahr oder das gecachte Default-Jahr (mit TTL)."""
        if schoolyear:
            return schoolyear
        if self._sy_cache_stale():
            with self._init_lock:
                if self._sy_cache_stale():
                    self._default_sy_id = self._pick_default(self._active_years(client), client)
                    self._default_sy_at = datetime.now()
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
            # _get_client() vor dem Lock holen: es nimmt selbst _init_lock (nicht
            # reentrant), ein Aufruf innerhalb dieses `with`-Blocks würde den
            # aufrufenden Thread für immer an sich selbst blockieren, sobald
            # _get_series_map() als erste Lazy-Init-Stelle erreicht wird (bisher
            # nur durch die Aufreihenfolge in den _sync()-Bodies verhindert).
            client = self._get_client()
            with self._init_lock:
                if self._series_map is None:
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

    async def get_student_info(self, student_id: int, schoolyear: str | None = None) -> dict:
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
            # Ermäßigung (remission) / Befreiung (exemption): „Nachweis fehlt" =
            # Antrag gestellt (`*_request`), aber weder akzeptiert noch abgelehnt
            # (`*_accepted` ist None — True=akzeptiert, False=abgelehnt). Read-only,
            # vgl. PLAN §6.1; ausschließlich für die Scanner-Anzeige.
            remission_pending = False
            exemption_pending = False
            if current_enrollment:
                amount_open = current_enrollment.get("amountOpen")
                exemption = current_enrollment.get("exemption_accepted")
                paid = exemption is True or (amount_open is not None and float(amount_open) <= 0)
                if (
                    current_enrollment.get("remission_request")
                    and current_enrollment.get("remission_accepted") is None
                ):
                    remission_pending = True
                if (
                    current_enrollment.get("exemption_request")
                    and current_enrollment.get("exemption_accepted") is None
                ):
                    exemption_pending = True

            # Bereits ausgeliehene Bücher — laut `?books=true`-Payload (API-
            # Referenz: „aktuell ausgeliehen") alle Exemplare, die der Schüler
            # aktuell noch hat. Wir übernehmen sie ungefiltert: ein Buch, das der
            # Schüler — egal wann — ausgeliehen hat und noch nicht zurückgegeben
            # hat, wird durchgehend als „ausgeliehen" ausgewiesen (kein Filtern
            # auf ein Schuljahrsfenster, sonst würden noch nicht zurückgegebene
            # Vorjahres-Bücher fälschlich unterschlagen). Siehe PLAN §6.1.
            current_books = []
            for b in detail.get("books", []):
                bv = b.get("BookView") or {}
                isbn = b.get("isbn") or bv.get("isbn", "")
                dist_at = b.get("distributed_at") or bv.get("distributed_at")
                current_books.append(
                    {
                        "code": b.get("code") or bv.get("code"),
                        "isbn": isbn,
                        "title": _title(isbn),
                        "subject": _fach(isbn),
                        "distributed_at": dist_at,
                    }
                )

            # Bücher die der Schüler laut Anmeldung erhalten soll
            books_to_receive: list[dict] = []
            if current_enrollment:
                for item in current_enrollment.get("booklistItems", []):
                    sd = item.get("series_data", {})
                    isbn = sd.get("isbn") or item.get("series", "")
                    books_to_receive.append(
                        {
                            "isbn": isbn,
                            "title": _title(isbn, sd.get("title", "")),
                            "subject": _fach(isbn),
                            "fee": item.get("EnrollmentBooklistItem", {}).get("fee"),
                        }
                    )

            # Einheitliche Buchliste für die Scanner-Tabelle:
            # vorgemerkt (noch nicht ausgeliehen) zuerst, ausgeliehen darunter.
            lent_isbns = {b["isbn"] for b in current_books if b["isbn"]}
            lent_by_isbn = {b["isbn"]: b for b in current_books if b["isbn"]}
            books: list[dict] = []
            seen_isbns: set[str] = set()
            for b in books_to_receive:
                isbn = b["isbn"]
                ausgeliehen = isbn in lent_isbns
                books.append(
                    {
                        "isbn": isbn,
                        "code": lent_by_isbn.get(isbn, {}).get("code") if ausgeliehen else None,
                        "title": b["title"],
                        "subject": b["subject"],
                        "status": "ausgeliehen" if ausgeliehen else "vorgemerkt",
                        "distributed_at": lent_by_isbn.get(isbn, {}).get("distributed_at")
                        if ausgeliehen
                        else None,
                    }
                )
                seen_isbns.add(isbn)
            # Ausgeliehene Bücher ohne passende Vormerkung trotzdem zeigen.
            for b in current_books:
                if b["isbn"] and b["isbn"] in seen_isbns:
                    continue
                books.append(
                    {
                        "isbn": b["isbn"],
                        "code": b["code"],
                        "title": b["title"],
                        "subject": b["subject"],
                        "status": "ausgeliehen",
                        "distributed_at": b.get("distributed_at"),
                    }
                )
            # 1. nach Status (vorgemerkt vor ausgeliehen, wie gehabt),
            # 2. nach Ausgabezeit absteigend (jüngste oben; negativer Timestamp),
            # 3. alphabetisch als stabiler Fallback (z. B. für vorgemerkte ohne Zeit).
            books.sort(
                key=lambda x: (
                    0 if x["status"] == "vorgemerkt" else 1,
                    -_sort_ts(x.get("distributed_at")),
                    x["subject"],
                    x["title"],
                )
            )

            return {
                "student_id": student_id,
                "firstname": detail.get("firstname", ""),
                "lastname": detail.get("lastname", ""),
                "enrolled": enrolled,
                "paid": paid,
                "amount_open": amount_open,
                "remission_pending": remission_pending,
                "exemption_pending": exemption_pending,
                "current_books": current_books,
                "books_to_receive": books_to_receive,
                "books": books,
            }

        return await asyncio.to_thread(_sync)

    def _extract_borrowable_catalog(self, full: dict, series_map: dict) -> list[dict]:
        """Ausleihbare Titel aus einer voll aufgelösten Booklist ziehen.

        Aus `sections[]->options[]->items[]` werden **alle ausleihbaren** Items
        (`borrowable=True`) genommen (keine Kauf-/Arbeitshefte), nach ISBN
        dedupliziert (Items wiederholen sich über Sections/Options) und nach
        `(subject, title)` sortiert. `series_data` der Booklist ist verlässlich
        (Titel/Fach/ISBN direkt). Liefert `[{isbn, title, subject}]`.
        """
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
                        sd.get("subjectsFlat") or (s.subjects_flat or s.subjects if s else []) or []
                    )
                    catalog.append({"isbn": isbn, "title": title, "subject": subject})
        catalog.sort(key=lambda b: (b["subject"], b["title"]))
        return catalog

    async def get_class_book_catalog(
        self, form_name: str, schoolyear: str | None = None
    ) -> tuple[int | None, list[dict]]:
        """Jahrgang + ausleihbare Bücher der Klasse (read-only).

        Grundlage für die klassenweite Bücher-Reihenfolge im Scanner. Quelle ist die
        **Jahrgangs-Bücherliste** (`GET /schoolyears/:sy/booklists/:id`), NICHT die
        Vereinigung der Einzelanmeldungen — so erscheinen alle für den Jahrgang
        ausleihbaren Titel, unabhängig davon, welche Schüler gerade angemeldet sind.

        Klassenstufe → Booklist über `form["grade"]` == `booklist["grade"]`.
        **Mehrjahresbände sind bewusst enthalten**: die komplette
        ausleihbare Jahrgangsliste wird gezeigt, auch wenn ein Band die Klassenstufe
        nur als oberen Jahrgang führt.

        Liefert `(grade, [{isbn, title, subject}])`. `grade` ist `None`, wenn die
        Klasse nicht gefunden wird; der Katalog ist leer, wenn der Jahrgang keine
        Booklist hat. Nur GETs.
        """

        def _sync() -> tuple[int | None, list[dict]]:
            client = self._get_client()
            series_map = self._get_series_map()

            sy_id = self._resolve_sy(client, schoolyear)
            forms = client.get(f"/schoolyears/{_enc(sy_id)}/forms")
            form_grade = next((f.get("grade") for f in forms if f["name"] == form_name), None)
            if form_grade is None:
                return None, []

            booklists = client.schoolyears.get_booklists(sy_id)
            bl = next((b for b in booklists if b.get("grade") == form_grade), None)
            if not bl:
                log.warning("Keine Booklist für Jahrgang %s (Klasse %s)", form_grade, form_name)
                return form_grade, []
            full = client.schoolyears.get_booklist(sy_id, bl["id"])
            return form_grade, self._extract_borrowable_catalog(full, series_map)

        return await asyncio.to_thread(_sync)

    async def get_booklists_overview(self, schoolyear: str | None = None) -> list[dict]:
        """Alle Bücherlisten (Jahrgänge) des Schuljahrs — read-only.

        Liefert `[{id, grade, title}]` nach Jahrgang sortiert. Grundlage für die
        Jahrgangs-Auswahl (Reiter) im Einstellungen-Dialog. Nur ein GET.
        """

        def _sync() -> list[dict]:
            client = self._get_client()
            sy_id = self._resolve_sy(client, schoolyear)
            booklists = client.schoolyears.get_booklists(sy_id)
            out: list[dict] = []
            for b in booklists:
                grade = b.get("grade")
                if not isinstance(grade, int):
                    continue
                out.append(
                    {
                        "id": b.get("id"),
                        "grade": grade,
                        "title": b.get("title") or f"Jahrgang {grade}",
                    }
                )
            out.sort(key=lambda b: b["grade"])
            return out

        return await asyncio.to_thread(_sync)

    async def get_booklist_catalog_by_grade(
        self, grade: int, schoolyear: str | None = None
    ) -> list[dict]:
        """Ausleihbare Bücher der Jahrgangs-Bücherliste — read-only.

        Wie `get_class_book_catalog`, aber direkt über den Jahrgang (ohne geladene
        Klasse). Liefert `[{isbn, title, subject}]`; leer, wenn der Jahrgang keine
        Booklist hat. Nur GETs.
        """

        def _sync() -> list[dict]:
            client = self._get_client()
            series_map = self._get_series_map()
            sy_id = self._resolve_sy(client, schoolyear)
            booklists = client.schoolyears.get_booklists(sy_id)
            bl = next((b for b in booklists if b.get("grade") == grade), None)
            if not bl:
                log.warning("Keine Booklist für Jahrgang %s", grade)
                return []
            full = client.schoolyears.get_booklist(sy_id, bl["id"])
            return self._extract_borrowable_catalog(full, series_map)

        return await asyncio.to_thread(_sync)

    async def get_book_by_code(self, code: str) -> dict | None:
        """Buch zu einem gescannten Barcode auflösen (read-only GET /books/{code}).

        Liefert `{code, isbn, title, subject, available, distributed, deleted,
        student_id, loaned_to, loaned_to_id}` oder `None`, wenn die API das Buch
        nicht kennt (404). Andere Fehler (Auth/Netz) werden durchgereicht, damit
        sie nicht fälschlich als „Buch unbekannt" interpretiert werden.

        `available`/`distributed`/`deleted` bilden den Lager-Status ab: „im Lager"
        = `available and not distributed and not deleted` (Grundlage für die
        Buchungs-Vorabprüfung, PLAN §6).

        `loaned_to` („Vorname Nachname") ist der **aktueller Ausleiher**, wenn das
        Buch verliehen ist (`distributed`); `loaned_to_id` dessen student_id;
        `loaned_to_firstname`/`loaned_to_lastname` dieselbe Person in getrennten
        Feldern (für Formatierungen wie „Nachname, Vorname"); `loaned_to_form`
        dessen Klasse (per zusätzlichem read-only Request `GET
        /students/:id?forms=true`, sobald ein Ausleiher bekannt ist — für die
        „an jemand anderen verliehen"- UND die Ersatzanspruch-Meldung). Die
        `/books/:code`-Antwort bettet den Ausleiher bereits als `Student` ein, der
        Normalfall braucht also für Name/Id keinen Extra-Request. Fehlt die
        Einbettung (oder ist anonymisiert), wird per `get_by_id` nachgeladen —
        read-only `GET /students/:id`. Bei Fehlern bleiben die Felder `None`
        (die Lager-Prüfung bleibt davon unberührt). Namen/Klasse werden NUR an
        die UI durchgereicht, nie geloggt (PLAN §3.7).
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
            first, last, loaned_to_id = self._resolve_current_borrower(client, book)
            loaned_to = f"{first} {last}".strip() if (first or last) else None
            loaned_to_form = None
            if loaned_to_id is not None:
                loaned_to_form = self._resolve_student_form(client, loaned_to_id)
            return {
                "code": book.code,
                "isbn": isbn,
                "title": title,
                "subject": subject,
                "available": bool(book.available),
                "distributed": bool(book.distributed),
                "deleted": bool(book.deleted),
                "student_id": book.student_id,
                "loaned_to": loaned_to,
                "loaned_to_id": loaned_to_id,
                "loaned_to_firstname": first,
                "loaned_to_lastname": last,
                "loaned_to_form": loaned_to_form,
            }

        return await asyncio.to_thread(_sync)

    @staticmethod
    def _resolve_current_borrower(
        client: AusleiheClient, book: object
    ) -> tuple[str | None, str | None, int | None]:
        """Vorname, Nachname + id des aktuellen Ausleihers eines Buches (read-only).

        Bevorzugt die in `/books/:code` eingebettete `Student`-Struktur (kein
        Extra-GET). Fehlt sie, Nachladen via `GET /students/:id`. Tolerant bei
        Fehlern/anonymisierten Datensätzen → `(None, None, student_id)`, sodass
        die UI nur zeigt, was sicher bekannt ist.
        """
        sid = getattr(book, "student_id", None)
        student = getattr(book, "student", None)
        if student is not None:
            first = (getattr(student, "firstname", "") or "").strip() or None
            last = (getattr(student, "lastname", "") or "").strip() or None
            if first or last:
                return first, last, sid
        if sid is None:
            return None, None, None
        try:
            st = client.students.get_by_id(sid)
            first = (getattr(st, "firstname", "") or "").strip() or None
            last = (getattr(st, "lastname", "") or "").strip() or None
            return first, last, sid
        except Exception as e:  # noqa: BLE001 — Name ist Kosmetik, nie fatal
            log.warning("Ausleiher zu student_id=%s nicht auflösbar: %s", sid, e)
            return None, None, sid

    @staticmethod
    def _resolve_student_form(client: AusleiheClient, student_id: int) -> str | None:
        """Klasse (Formname) eines Schülers — read-only Zusatz-Request
        (`GET /students/:id?forms=true`), nur für die Ersatzanspruch-Meldung
        gebraucht. Bei mehreren Schuljahren wird das mit der höchsten
        `schoolyear`-id (aktuellstes) genommen. Tolerant bei Fehlern → `None`.
        """
        try:
            detail = client.students.get_detail(student_id, forms=True)
            forms = detail.get("forms") or []
            if not forms:
                return None
            forms_sorted = sorted(forms, key=lambda f: f.get("schoolyear") or 0, reverse=True)
            return forms_sorted[0].get("name")
        except Exception as e:  # noqa: BLE001 — Klasse ist Kosmetik, nie fatal
            log.warning("Klasse zu student_id=%s nicht auflösbar: %s", student_id, e)
            return None

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
