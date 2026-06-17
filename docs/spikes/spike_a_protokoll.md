# Spike A — Playwright gegen die offizielle Counter-Seite

> Status: **Explore erfolgreich (2026-06-12)** — Login, Counter, Schülersuche und
> Schülerkartei laufen headless per Playwright. Buchungsteil bewusst gesperrt,
> wartet auf Freigabe.
> Skript: `automation/spike_a_counter.py` · Bezug: `docs/PLAN.md` §5 Phase 1, O2/O3.

## Ziel & Risiko

Kritisches Projektrisiko: Der gesamte Write-Pfad des Projekts hängt daran, dass
sich die offizielle IServ-Ausleihe-Counter-Seite per Playwright bedienen lässt
(Login → Schüler öffnen → Barcode eintragen → Submit → Erfolg/Fehler aus dem DOM
lesen). Scheitert das, muss der Write-Pfad neu diskutiert werden
(`processBook`-API wäre die Alternative — erfordert Skizzen-/Policy-Entscheidung).

## Vorgehen

1. **Explore (read-only, gefahrlos):**
   `uv run python -m automation.spike_a_counter --explore --student <Name>`
   → Login, Ausleihe-App öffnen, Screenshots/DOM-Dumps/Link-Listen nach
   `automation/out/`. Kein Submit außer dem Login-Formular.
2. **Auswertung:** DOM-Struktur der Counter-Seite analysieren (Selektoren für
   Schülersuche, Barcode-Feld, Submit, Erfolgs-/Fehlermeldungen) — unten
   dokumentieren.
3. **Buchungstest (nur nach Freigabe durch Niklas):** Ausgemustertes Buch auf
   Niklas' Account ausleihen **und sofort zurücknehmen**. Vorher Rückbau-Plan
   ausfüllen (unten).

## Sicherheitsregeln (verbindlich, siehe CLAUDE.md)

- Explore-Modus macht **keine Buchung** — einziger Submit ist der Login.
- Buchungstests nur mit Niklas' Account + **ausgemustertem Buch**, nie
  unbeaufsichtigt, jede Test-Ausleihe sofort zurücknehmen.

## Ergebnisse (auszufüllen)

### O3 — Verhalten der Counter-Seite (Explore-Lauf 2026-06-12)

| Frage | Befund |
|-------|--------|
| URL/Route der Counter-Ansicht | `https://ausleihe.<domain>/#/counter` („Aus- u. Rückgabe"); Schülerkartei: `#/counter/student/<id>` (numerische Student-ID wie in der ausleihe-api) |
| Selektor Schülersuche | `input.tt-input[name="input"]` — Placeholder „Ausweis scannen oder Namen eingeben"; sf-typeahead (Twitter Typeahead), Vorschläge als `.tt-suggestion`. Tippen mit `press_sequentially` (fill() triggert das Typeahead nicht zuverlässig), Vorschlag anklicken. **Update 2026-06-17:** Im Produktiv-Worker abgelöst — Kartei wird direkt über die ID-Route `#/counter/student/<id>` geladen (eindeutig, keine Namenssuche); dieser Selektor wird nicht mehr genutzt. |
| Selektor Barcode-Eingabefeld | **dasselbe Feld** — nach Schülerauswahl wechselt der Placeholder auf „Buch scannen oder neuen Schüler über Ausweis oder Namen aufrufen" (guter Indikator „Kartei geladen") |
| Submit-Mechanik | Enter im Eingabefeld → `ng-submit="c.evaluateInput()"`; das Feld unterscheidet selbst zwischen Buchcode und Schülersuche. **Enter mit Buchcode = Buchung!** |
| Erfolgs-/Status-Feedback im DOM | Bücherliste rechts: pro Buch Titel, Code, „Ausgegeben am"; Validierungs-Hinweise inline und rot (z. B. „Nicht für dieses Buch angemeldet"). Kartei links: Anmeldestatus pro Schuljahr („Bezahlt" grün / „NICHT ANGEMELDET" rot) |
| Weitere Aktionen in der Kartei | „Leihschein"-Button (`c.printLoanSlip()`); pro Buch `bl.deleteBook(book, "lost"/"unusable")`; „Bücher ohne Schülerkartei zurücknehmen"-Link auf der Counter-Startseite |
| Fehlerfälle (falsche Serie, nicht bezahlt, schon verliehen, unbekannter Code) | _offen — erst im freigegebenen Buchungstest beobachtbar_ |
| Schüler-Wechsel innerhalb einer Session | Im selben Feld neuen Namen/Ausweis eingeben (laut Placeholder); alternativ direkt `#/counter/student/<id>` ansteuern (funktioniert, alle XHRs 200) |
| Login-Hürden für Playwright | **Keine.** OAuth2-Redirect-Kette + Meta-Refresh erledigt der echte Browser selbst; kein 2FA, kein Captcha. Headless Chromium reicht. `networkidle` ist unbrauchbar (IServ hält SSE/Long-Polling offen) → `domcontentloaded` + gezielte Element-Waits |
| API-Calls der Kartei (beobachtet) | `GET /students/:id?books=true&enrollments=true&forms=true`, `/students/:id/claims?open=true`, `/students/:id/books` — deckungsgleich mit der read-only ausleihe-api |

**Bewertung:** Das kritische Risiko ist weitgehend entschärft — die offizielle
Counter-Seite ist headless automatisierbar, Selektoren sind stabil benennbar
(Angular-`ng-*`-Attribute + Typeahead-Klassen). Offen bleibt nur die Buchungs-
Mechanik selbst (Erfolgs-/Fehler-Feedback nach Barcode-Enter), die den
freigegebenen Test mit ausgemustertem Buch braucht.

**Nebenbefund (geklärt 2026-06-12):** Der `.env`-Account ist der Admin-Account
von Lukas Podleschny (Mitentwickler) — **ausschließlich lesend** zu verwenden.
Testschüler für spätere Buchungstests ist **Niklas Müller**; jede Buchung auf
dem Live-System (auch Tests) nur nach expliziter Bestätigung von Niklas und
Lukas. Der Buchungstest (`--issue`/`--return`) ist bis dahin zurückgestellt.

### O2 — Parallele Sessions desselben Accounts (→ Spike B)

| Frage | Befund |
|-------|--------|
| 2–3 parallele Browser-Contexts gleichzeitig eingeloggt? | **Ja, 3/3 OK** (Spike B, 2026-06-12) |
| Session-Invalidierung beim Zweit-Login? | **Nein** — alle Contexts bleiben eingeloggt |

Details: `docs/spikes/spike_b_protokoll.md`

## Rückbau-Plan (vor jedem Buchungstest ausfüllen)

| Feld | Wert |
|------|------|
| Datum/Uhrzeit | _offen_ |
| Test-Account | Niklas (eigener Account) |
| Buch-Code (ausgemustert!) | _offen_ |
| Geplante Aktion | Ausgabe → sofortige Rücknahme |
| Rückbau verifiziert durch | GET `/students/:id/books` (read-only) |
| Rückbau erledigt am | _offen_ |
