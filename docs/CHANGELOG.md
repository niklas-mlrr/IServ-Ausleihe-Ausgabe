# Changelog

> Chronologisches Änderungsprotokoll, **neueste Einträge zuerst**. Zielbild,
> Architektur, Sicherheitsmodell, offene Punkte und Phasenplan stehen in
> `docs/PLAN.md`; Verifiziert-/Offen-Stand in `docs/test_status.md`.
> Ausführliche Spike-/Test-Protokolle liegen als eigene Dateien in `docs/`
> (`docs/spikes/`, `docs/phase2_e2e_2026-06-15.md`,
> `docs/phase4_modus_b_2026-06-15.md`, `docs/hardening_2026-06-18.md`) und
> werden hier nur verlinkt, nicht dupliziert.

## 2026-07-12 — Unterscheidung Buch- vs. Buchreihe-bereits-verliehen (`book_already_lent` neu)

Nachbesserung am bisherigen `series_already_lent`-Hinweis (Eintrag darunter):
Niklas wollte unterscheiden, ob genau DAS gescannte Exemplar schon auf den
Schüler läuft oder nur ein ANDERES Exemplar derselben Buchreihe. Neuer Status
`book_already_lent` deckt den ersten Fall ab, `series_already_lent` bleibt für
den zweiten.

- **Backend (`server/sessions.py`).** `booking_isbn_sets_from_info()` gibt
  jetzt zusätzlich `lent_codes` zurück (Barcodes der konkret ausgeliehenen
  Exemplare aus `info["current_books"]`). `evaluate_scan_for_booking()`
  vergleicht bei ISBN-Treffer den gescannten Barcode gegen `lent_codes`:
  Treffer → `book_already_lent` ("Bereits an dich verliehen: {title}"),
  sonst weiterhin `series_already_lent` ("Reihe bereits ausgeliehen: {title}").
  `process_scan()` trägt den Code nach erfolgreicher Buchung zusätzlich in
  `lent_codes` nach, damit ein erneuter Scan desselben Exemplars in derselben
  Session korrekt als `book_already_lent` erkannt wird. Neues Session-Feld
  `lent_codes: set[str]` auf `HelperSession`/`StudentSessionB`
  (`server/state.py`), durchgereicht durch `hydrate_student_info` und beide
  `process_scan`-Aufrufer (`server/routes/ws.py`); Reset an den bestehenden
  `vormerk_isbns`/`lent_isbns`-Reset-Stellen (`sessions.py`, `routes/queue.py`).
- **Modal (Helfer- und Schüler-Client).** `book_already_lent`: Überschrift
  „Buch bereits an dich verliehen" (gelb), darunter `<Buchcode> — <Titel>`,
  darunter „Dieses Buch ist bereits an dich verliehen. Du musstest es nicht
  noch einmal scannen." `series_already_lent`: Überschrift „Buchreihe bereits
  an dich verliehen" (gelb), darunter `<Buchcode> — <Titel>`, darunter „Ein
  Buch dieser Buchreihe ist bereits an dich verliehen. Leg es einfach wieder
  zurück." (`web/scan-render.js`, `web/student.js`).
- **Statuszeile.** `book_already_lent`: "<Buchcode> bereits an dich verliehen
  — <Titel>". `series_already_lent`: "<Buchcode> Buchreihe bereits an dich
  verliehen" (ohne Titel). Beide im gleichen Gelb wie die Modal-Überschrift
  (`status-already-lent`, `#e69500`) — `web/common.js` (`scanResultStatusText`),
  `web/scan-ws.js`/`web/student.js` (Klassen-Toggle). Alle Trennstriche in den
  neuen/angepassten Strings sind Halbgeviertstriche ("—"), konsistent mit dem
  Rest der Statuszeilen.
- **Tests.** `tests/test_booking_precheck.py` um Fälle für die neue
  Unterscheidung erweitert (`test_reject_book_already_lent_same_code`,
  `test_series_already_lent_when_different_code_of_same_isbn`,
  `test_process_scan_booked_isbn_moves_to_lent` prüft jetzt zusätzlich, dass
  ein erneuter Scan desselben Exemplars `book_already_lent` liefert).

## 2026-07-12 — Meldung + Statuszeile bei „Buch bereits an dich verliehen" (`series_already_lent`)

Nachbesserung an der Modal-Meldung und der Statuszeile für den Status
`series_already_lent` (ein einzelnes Buch — nicht die ganze Reihe — ist
bereits an den scannenden Schüler ausgeliehen), in `scan-render.js`
(Modus A) und `student.js` (Modus B):

- **Modal.** Überschrift bleibt wie bisher gelb (`#e69500`, „Buch bereits
  an dich verliehen"). Darunter weiterhin `<Buchcode> - <Buchtitel>`. Neu
  darunter, mit Abstand über den bestehenden `.modal-box`-Flex-`gap`, eine
  eigene Zeile `"Dieses Buch ist bereits an dich verliehen."` statt der
  technischen Server-`msg` (neues `<p id="book-alert-note">` in
  `scan.html`/`student.html`).
- **Statuszeile.** Neuer Helper-Fall in `scanResultStatusText()`
  (`common.js`) formatiert `"<Buchcode> bereits an dich verliehen -
  <Titel>"`. Neue CSS-Klasse `status-already-lent` (`#e69500`, fett,
  gleiches Gelb wie die Modal-Überschrift) in `scan.html`/`student.html`;
  `setStatusText()` (`scan-state.js`) bekommt dafür einen vierten
  Parameter `isAlreadyLent`.

## 2026-07-12 — Statuszeile bei erfolgreicher Buchung: Fach + Titel statt Worker-Rohtext, grün eingefärbt

Zwei Nachbesserungen an der Statuszeile für `scan_result`-Status `'booked'`
(tatsächliche Ausgabe bei `ALLOW_BOOKING=true`):

- **Fach + Titel statt DOM-Best-effort-Meldung.** Neue Helper-Funktion
  `scanResultStatusText(msg, books)` in `web/common.js` ersetzt für Status
  `'booked'` die technische Worker-Meldung ("Buchung im DOM bestätigt
  (best-effort)") durch Fach + Titel, nachgeschlagen per ISBN aus der
  Bücherliste des Schülers (`currentBooks`/`student_info`). `scan-ws.js`
  (Modus A) und `student.js` (Modus B) nutzen den gemeinsamen Helper statt
  eigener Ad-hoc-Formatierung.
- **Formatierung + Farbe.** Bei `'booked'` baut der Helper jetzt die
  komplette Zeile selbst: `"<Buchcode> ausgegeben — <Fach> — <Titel>"` —
  ohne Bindestrich zwischen Buchcode und "ausgegeben" (anders als bei allen
  übrigen Status, die weiterhin `"<Buchcode> — <Meldung>"` mit Trenner
  zeigen). Neue CSS-Klasse `status-book-issued` (`#2e7d32`, fett) in
  `scan.html`/`student.html` färbt die Zeile grün; `setStatusText()`
  (`scan-state.js`) bekommt dafür einen dritten Parameter `isIssued`.

## 2026-07-11 — Selbst-Aufruf zählt jetzt als neuer Zugriff (Menü-Schließen-Fix + Rückstellungspflicht)

Nachbesserung am `refresh_active_student`-Kurzschluss aus dem Eintrag
darunter: der reine Info-Refresh bei Selbst-Aufruf (Helfer ruft seinen
EIGENEN aktiven Schüler per Queue-`call`/Lupe erneut auf) sendete bewusst
kein `loading` — dadurch blieb im Helferclient das Menü/Such-Panel offen
(kein Trigger zum Schließen). Niklas wollte zusätzlich eine
Verhaltensänderung: ein Selbst-Aufruf soll wie ein neuer Zugriff zählen,
nicht wie ein bloßer Refresh — existiert eine Warteliste für den Schüler,
muss sich der Aufrufer hinten anstellen (der bisher Wartende übernimmt
sofort), statt sich die Aktivität direkt zurückzuholen.

`refresh_active_student` wieder entfernt (kein Aufrufer mehr). Neue Logik in
`_handle_call`/`_handle_search_call`: Selbst-Aufruf + existierende
Warteliste → regulärer `end_student` (befördert den Ersten in der Liste
automatisch, wie beim normalen Beenden) gefolgt von `spectate_student` für
den bisherigen Besitzer (stellt sich hinten an — KEIN Zurückholen, sonst
wieder zwei aktive Clients). Selbst-Aufruf OHNE Warteliste → fällt in den
unveränderten Standard-Pfad (`end_student` + `assign_student_to_helper` an
denselben Helfer) durch, der ohnehin `loading` sendet und damit auch das
Menü schließt — ein vollständiger Reload statt eines Teil-Refreshs, exakt
wie bei jedem anderen Aufruf.

Tests umbenannt/angepasst (`test_..._does_not_dual_assign` →
`test_..._demotes_caller_to_back_of_queue`) + ein neuer Test für den
No-Queue-Reload-Pfad (`loading` wird gesendet). 209 → 210 Tests.

## 2026-07-11 — Spectator-Feinschliff: Live-Refresh, Warteposition über Reload, Selbst-Aufruf-Bug behoben

Drei Nachbesserungen am Spectator-Mechanismus (Eintrag darunter), gemeldet
nach dem ersten Live-Test:

- **Live-Refresh für Spectators.** Lädt der AKTIVE Helfer seine Seite neu
  (Reconnect in `ws_scanner`), bekommen jetzt auch alle Spectators dieses
  Schülers ein aufgefrischtes `student_info` (neue Funktion
  `sessions.broadcast_student_info_to_spectators`) — vorher blieb ihre
  Ansicht bis zum nächsten Scan auf altem Stand.
- **Warteposition bleibt über Reload erhalten.** Der Disconnect-Handler in
  `ws_scanner` entfernt einen Spectator NICHT mehr sofort aus
  `state.student_spectators` (das tat er vorher, ohne Gnadenfrist). Lädt ein
  wartender Client seine Seite neu, bleibt sein Platz in der FIFO-Liste
  erhalten (Reconnect-Zweig `elif helper.spectating_student_id is not
  None`); nur echte, dauerhaft verwaiste Einträge werden bei ihrer eigenen
  Beförderung von `pop_next_spectator` (das tote WS bereits übersprang)
  verworfen.
- **Kritischer Bugfix — Doppel-Aktiv bei Selbst-Aufruf.** Rief der AKTIVE
  Helfer seinen EIGENEN Schüler über Queue-`call` oder Lupe-`search_call`
  erneut auf, während ein anderer Helfer als Spectator wartete, löste das
  interne `end_student` (Teil des bisherigen „erst beenden, dann neu
  zuweisen"-Musters) dessen Beförderung aus — der Handler wies den Schüler
  aber direkt danach trotzdem wieder dem ursprünglichen Helfer zu: zwei
  Clients gleichzeitig aktiv, genau die Invariante, die der Spectator-
  Mechanismus eigentlich verhindern soll. Neue Funktion
  `sessions.refresh_active_student`: `_handle_call`/`_handle_search_call`
  erkennen jetzt den Fall „Aufrufer ist bereits selbst der Besitzer"
  (`find_helper_for_student(sid).token == helper.token`) und laufen
  stattdessen über einen reinen Info-Refresh (kein `end_student`, keine
  Neuzuweisung, kein Beförderungsrisiko) — inklusive Spectator-Fan-out wie
  oben.

Tests: `tests/test_ws_scanner.py` (Selbst-Aufruf via `call` und
`search_call` je mit wartendem Spectator, Reload-Fan-out, Reload-mit-
erhaltener-Warteposition). 206 → 209 Tests.

## 2026-07-11 — Spectator-Modus + Warteliste statt Doppel-Öffnen-Fehler

Ersetzt den vorherigen reinen Busy-Fehler (Eintrag darunter) durch einen
vollen Zuschauer-/Wartelisten-Mechanismus: versucht ein zweiter Helfer
(Queue-`call` oder Lupe-`search_call`), einen bereits bei einem ANDEREN
Helfer aktiven Schüler zu laden, bekommt er sofort dessen Bücherliste
read-only angezeigt (live mit jedem Scan des aktiven Helfers mitaktualisiert)
— aber KEINEN eigenen Playwright-Worker (es gibt ohnehin nur einen Worker pro
`student_id`). Statuszeile: „Warten bis Schüler frei…". Erst wenn der aktive
Helfer den Schüler beendet, wird der am längsten Wartende automatisch
befördert (jetzt MIT Worker); ein dritter Wartender bleibt entsprechend in
der Liste, bis auch der neu beförderte fertig ist (FIFO-Handoff-Kette).

Neu: `HelperSession.spectating_student_id` (getrennt von `student_id` — das
bleibt strikt „ich besitze Worker + Queue-Slot"), `SpectatorWaiter`-Dataclass
und `AppState.student_spectators`/`add_spectator`/`remove_spectator`/
`pop_next_spectator` (`server/state.py`). `sessions.spectate_student()`
registriert den Zuschauer (räumt vorherige eigene/andere Zuschauer-
Registrierung zuerst auf) und pusht `student_info` mit `spectator: true` —
kein `worker_pool.open_student`. `assign_student_to_helper()` räumt am Anfang
automatisch eine noch offene Zuschauer-Registrierung des Helfers ab (jeder
Pfad, der einen Helfer wirklich einen Schüler zuweist, egal ob „Nächster",
„Aufrufen" oder die neue Beförderung, läuft darüber). `end_student()` bekommt
dafür einen Beförderungs-Zweig (für echte Queue-Schüler UND transiente
Lupe-Ziele, die redundant im `SpectatorWaiter` gespeicherte lastname/
firstname/form nutzen) — bewusst synchron ohne Await zwischen
`pop_next_spectator` und dem Aufruf von `assign_student_to_helper`, damit
kein Zeitfenster entsteht, in dem ein dritter Helfer den Schüler regulär
„callen" könnte, bevor die Beförderung feststeht. `_handle_scan`
(`server/routes/ws.py`) spiegelt jeden Scan des aktiven Helfers zusätzlich an
alle Spectator-Tokens (`spectator: true`). Disconnect eines Zuschauers
räumt ihn sofort (keine Reconnect-Gnadenfrist — er hält keine exklusive
Ressource) aus der Warteliste.

Der neue Guard erkennt jetzt auch belegte TRANSIENTE Lupe-Ziele (über
`find_helper_for_student` statt `find_student`), was der vorherige Fix noch
verpasste (transiente Schüler stehen in keiner Queue). Frontend
(`web/scan-ws.js`): `student_info`/`scan_result` mit `spectator: true` zeigen
die Bücherliste read-only, ohne Statuszeile/Alert-Modal zu überschreiben;
`workerPending` bleibt dauerhaft `true` (sperrt Scans über den bestehenden
Client-Gate). Tests: `tests/test_ws_scanner.py` (Spectate über echte
Websockets, Scan-Fan-out, Disconnect-Aufräumung),
`tests/test_queue_flow.py` (Beförderung + FIFO-Kette, low-level über
`end_student`).

## 2026-07-11 — Guard gegen Doppel-Öffnen desselben Schülers (Lupe)

`_handle_search_call` (`server/routes/ws.py`) prüfte bislang nicht, ob der per
Lupe angesprungene Schüler bereits bei einem ANDEREN Helfer aktiv ist — anders
als `_handle_call` (Queue-Aufruf), das `status not in (pending, done)` bereits
abfängt. Da die Lupe gezielt JEDEN Schüler laden kann (auch außerhalb der
eigenen Queue), konnte so derselbe Schüler auf zwei Clients gleichzeitig
geöffnet werden (zwei parallele Worker-Sessions). Neuer Guard: `state.
find_student(sid)` vor dem Laden prüfen — ist der Treffer `status == "active"`
und `assigned_helper != helper.token`, wird nichts geladen, stattdessen
`{"type": "error", "busy": true, "msg": "Warte bis Schüler frei…"}` gesendet.
Frontend (`web/scan-ws.js`) zeigt bei `busy: true` den Text unverändert in der
Statuszeile (ohne den sonstigen `"Fehler: "`-Prefix); das Such-Panel bleibt
offen für einen erneuten Versuch. Test: `tests/test_ws_scanner.py::
test_search_call_blocks_student_active_on_other_helper`.

## 2026-07-11 — Auto-fertig-Filter „Alle Bücher bereits ausgeliehen"

Fünfter Sofort-fertig-Filter beim Klassen-Öffnen (`_AUTO_DONE_FILTERS` in
`server/routes/classes.py`, ergänzt neben `not_enrolled`/`unpaid`/
`remission_pending`/`exemption_pending`): `all_lent` setzt einen Schüler direkt
auf `done`, wenn seine vorgemerkten Buchreihen — nach Anwendung der
ausgeblendeten ISBNs (`get_hidden_isbns_for_form`) — bereits vollständig
ausgeliehen sind (`booking_isbn_sets_from_info` liefert kein `vormerk` mehr).
UI-Checkbox in `web/host.html`, Persistenz in `web/host-state.js`
(`AUTO_DONE_KEYS`). Spart manuelles Durchklicken von Schülern, die schon
komplett versorgt sind.

## 2026-07-11 — Wartbarkeits-Welle 7 (Subagent-Refactoring)

Neun Verbesserungspunkte aus einem Codebase-Review, ausgeführt von Sonnet-5-
Subagents (Fortsetzung der Wellen 0–6). Alles verhaltenserhaltend; Baseline
`ccdcbd9`, Ergebnis auf `main` (`84497cb`):

- **`routes/ws.py`** — `safe_broadcast()` und `_take_over_ws()` extrahiert
  (ersetzen den ~4× wiederholten `try/except: pass`-Broadcast bzw. den
  Reconnect-Ownership-Swap). `ws_scanner`-Empfangsschleife (`if mtype==…`-Kette)
  auf eine Dispatch-Table `_SCANNER_HANDLERS` (10 kleine `_handle_*`) umgestellt.
  `ws_student`-Reconnect auf dieselbe „Swap vor `await close()`"-Ordnung wie
  `ws_scanner` vereinheitlicht (strikt sicherer gegen den Finally-Race).
- **`web/host.js`/`web/scan.js`** (je ~1500 Z.) in `*-state.js`/`*-ws.js`/
  `*-render.js` gesplittet (geordnete `<script>`-Tags, geteilter Top-Level-
  Scope, additive `window.__host`/`__scan`-Introspektion). `student.js` in eine
  IIFE gewrappt. Verhalten browser-verifiziert (headless-Chromium-Smoke: alle
  drei Seiten laden ohne uncaught JS/ReferenceError/TypeError).
- **`server/state.py`** — die toten `AppState`-Forwarding-Shims (`RuntimeSettings`/
  `IservCaches`, ~110 Z.) entfernt; einziger verbliebener Consumer war
  `setattr(state, …)` in `routes/settings.py` → auf `state.settings` umgebogen.
  Lange Feld-Rationale-Kommentare nach neuem `docs/PLAN.md § State-Feld-Rationale`
  ausgelagert (Typdefinitionen wieder skimmbar).
- **`server/iserv_client.py`** — doppelte TTL-Staleness-Prüfung in `_resolve_sy`
  in einen `_sy_cache_stale()`-Helper faktorisiert (Double-Checked-Locking
  erhalten).
- **`docs/test_status.md`** — fragile Buchungs-Erfolgs-/Fehler-Selektoren
  (`automation/worker.py::_read_booking_result`, Code-TODO) als offener Punkt
  getrackt (Produktions-Schreibpfad).
- Verwaiste, gelockte Worktree `queue-status-boxes` entfernt.

**Tests:** 201 → **199** — zwei Tests in `tests/test_state_contract.py`, die
ausschließlich die entfernten Forwarding-Shims prüften, wurden gelöscht; alle
`state_snapshot()`-Wire-Format-Assertions bleiben unangetastet. `ruff` clean.

**Prozess-Gotcha** (s. `_logs/2026-07-11_…` im Wiki): die parallelen
Isolation-Worktrees wurden von `547cb6a` (First-Parent von `ccdcbd9`) statt vom
Session-HEAD angelegt → zwei Agents refactorten veraltete Dateien und hätten
`queue_all` still gelöscht. Beim Merge via Feature-Marker-Grep erkannt, betroffene
Agents (ws/frontend) im Haupt-Baum neu ausgeführt.

## 2026-07-10 — Helferclient: aktive/fertige Schüler als Gruppen-Boxen unter der Warteschlange

Die Warteschlangen-Ansicht im Helferclient (`web/scan.js`, `renderQueue`)
zeigt jetzt zusätzlich zu den wartenden Schülern (unverändert je eigene Zeile
mit „Aufrufen"-Button) die gerade aufgerufenen (`status: "active"`) und
bereits fertigen (`status: "done"`) Schüler der gewählten Klasse — je Status
eine gemeinsame Box (blau/grün, `.queue-group`) statt einer Einzel-Box pro
Schüler wie bei den Büchern. Aktive Schüler haben keinen Button (bereits bei
einem Helfer); fertige lassen sich erneut aufrufen (z. B. um nachträglich
ein Buch zu erfassen) — Button wie bei den Wartenden. Abstände zwischen den
Boxen sowie zwischen den Namen innerhalb einer Box sind auf 7px
vereinheitlicht (wie zwischen den Steuer-Elementen der oberen Leiste,
`.top-bar`/`.gear-wrap`).

Serverseitig liefert `AppState.real_contexts_summary()` (`server/state.py`)
sowie die `waiting`/`queue_update`-Nachrichten (`server/sessions.py`,
`server/hub.py`, `server/routes/ws.py`) dafür zusätzlich zum bisherigen
`queue`-Feld (nur pending, für Tab-Badge/Status-Count unverändert) ein neues
`queue_all`-Feld mit allen Schülern des Kontexts (inkl. active/done/skipped).
Der `call`-WS-Handler erlaubt jetzt auch das Aufrufen bereits fertiger
Schüler (bisher nur `pending`), damit die Fertig-Box nutzbar ist.

## 2026-07-10 — Wartbarkeits-Wellen 0–4: Hygiene, Tests, Kommentar-Diät

Fünf Wellen Aufräumarbeit an Server/Automation/Web, ohne Verhaltensänderung
am Buchungspfad:

- **Welle 0 (Hygiene):** `.claude/` ignoriert, macOS-Artefakte entfernt,
  ruff-format-Pre-Commit-Hook eingerichtet, danach einmalig `ruff format`
  über `server/`, `automation/`, `tests/` laufen lassen; E501-Ignore auf
  `automation/` eingegrenzt statt global (`e9d603f`, `ab3f62f`, `23ff27d`).
- **Welle 1 (Bugfixes):** doppelte HTML-Maskierung durch `escapeHtml` in
  `textContent`-Zuweisungen entfernt (Host- und Schüler-Client,
  `db6452b`, `101c285`); Selbst-Deadlock in `_get_series_map` durch einen
  nicht-reentranten Lock behoben (`53d0fd4`).
- **Welle 2 (WS-Serialisierung):** alle WebSocket-Sends laufen jetzt über
  den Hub-Lock (`12b3777`), abgesichert durch einen Test, der konkurrierende
  Sends auf derselben Verbindung serialisiert nachweist (`a48bf24`).
- **Welle 3 (Web-Refactor):** `student.html`s Inline-JS nach `web/student.js`
  ausgelagert (`077167b`); `host.js` nutzt den gemeinsamen `Beeper` aus
  `common.js` statt einer eigenen Audio-Kopie (`66dbcef`).
- **Welle 4 (Nebenläufigkeits-Invarianten):** sieben zuvor nur in Prosa
  behauptete Concurrency-Garantien durch benannte Tests abgesichert
  (`4b9bf69`, `4a43fde`). Die Invarianten und ihr jeweiliger Grund:
  1. `_deferred_end` (ws.py) — ein Reconnect innerhalb der Grace-Frist ODER
     ein zwischenzeitliches Weiterschalten darf den verzögerten
     Schüler-Teardown NICHT mehr auslösen (Re-Checks auf `helper.ws` und
     `helper.student_id`).
  2. `ws_scanner`s `finally` (ws.py) — das `if helper.ws is websocket`-Gate
     verhindert, dass die alte Verbindung nach einem Reconnect den frisch
     übernommenen Schüler/Worker wieder abbaut.
  3. `load_and_push_helper_student` (sessions.py) — Stale-Guard vor
     `set_worker_session`: wurde der Helfer während `open_student` schon
     weitergeschaltet, muss der Context selbst geschlossen werden, sonst
     bleibt er als Orphan unter einer toten `student_id` im Pool hängen.
  4. `load_and_push_paired_student` (sessions.py) — dieselbe Garantie für
     Modus B (Prüfung auf `session.student_id`/`session.state`).
  5. `release_worker` + `_release_tasks` (sessions.py) — Release-Tasks
     werden in einem modulglobalen Set stark referenziert, weil
     `asyncio` Tasks sonst nur schwach hält; ohne das Set kann ein
     Fire-and-forget-Task mitten in der Coroutine GC't werden und der
     Context bleibt für immer draußen (bei `WORKER_CONTEXTS=2` genügen
     zwei stille Drains, um den Pool leerzuräumen).
  6. `WorkerPool.open_student` (worker.py) — beide Fehlerpfade
     (`new_page()`, `load_card()`) fangen `BaseException` statt
     `Exception`, weil `except Exception` `asyncio.CancelledError` seit
     Python 3.8 nicht mehr abfängt; ohne den weiten Fang würde ein
     Cancel (z. B. schnelles „Weiter") den Context aus dem Pool verlieren.
  7. `WorkerPool.release` — idempotent per Attribut-Nullung
     (`session._context = None`), damit ein doppelter Release (Race im
     Server-Code) nicht denselben Context zweimal in den Pool anhängt.

Die Kommentare an diesen sieben Stellen sind auf je einen Satz (die
Invariante im Präsens) plus einen Test-Verweis gekürzt; die vorherige
Regressions-Prosa ("ohne X würde Y passieren") lebt jetzt hier. Laut
CLAUDE.md gehört Änderungshistorie ausschließlich ins Changelog, nicht in
Code-Kommentare.

## 2026-07-10 — Welle 4b + 5: Kommentar-Trim vollzogen, AppState entflochten

Ergänzt die „Wellen 0–4"-Zusammenfassung oben um zwei weitere Schritte, die
im direkten Anschluss folgten:

- **Welle 4b (Vollzug):** Der in Welle 4 beschriebene Kommentar-Trim wurde
  umgesetzt — `b1b83f3` hielt die Absicht im Changelog fest, `35b269e` führte
  ihn im Code aus: netto **−34 Kommentarzeilen** über `server/routes/ws.py`,
  `server/sessions.py`, `automation/worker.py`. Die Regressions-Prosa lebt
  jetzt ausschließlich im Changelog (siehe oben); im Code bleibt je
  Invariante ein Satz (die Invariante im Präsens) plus ein Test-Verweis.
- **Welle 5 (State-Split):** `AppState` (`server/state.py`) trug 25 Felder
  über fünf Zuständigkeiten. `RuntimeSettings` (die fünf Host-/Entwickler-
  Toggles) und `IservCaches` (die fünf schuljahresbezogenen IServ-Caches)
  wurden als eigene Dataclasses herausgelöst — `AppState` behält nur noch
  17 direkte Felder plus 11 dünne Forwarding-Properties (nötig, weil
  `server/routes/settings.py::_BOOL_SETTINGS` per `setattr(state, attr,
  value)` auf die alten Attributnamen schreibt). Das Draht-Format von
  `state_snapshot()` bleibt dabei unverändert — vor dem Split per
  Charakterisierungs-Test eingefroren (`tests/test_state_contract.py`,
  `09e2ed5`); dieser Test darf bei künftigen Refactorings **nicht**
  angepasst werden, ein Fehlschlag bedeutet, dass sich das Draht-Format
  geändert hat (`0fef31d`).

Test-Suite: **187 → 201** grün.

## 2026-07-10 — Host: Sofort-fertig-Filter beim Klassen-Öffnen

Im „Neue Klasse öffnen"-Reiter vier Umschalter ergänzt: Schüler ohne aktuelle
Anmeldung, nicht bezahlt, Ermäßigungsantrag ohne Nachweis, Befreiungsantrag
ohne Nachweis. Beim Laden einer neuen Klasse (nicht beim Wieder-Aktivieren
eines bereits offenen Tabs) prüft `_apply_auto_done` (`server/routes/
classes.py`) jeden Schüler parallel per `get_student_info` (read-only,
schuljahrbezogen) gegen die gewählten Bedingungen und setzt Treffer sofort auf
Status `done` — nicht angemeldete Schüler zählen dabei ausschließlich für den
„Nicht angemeldet"-Filter (ohne Anmeldung liefert IServ keinen sinnvollen
Zahl-/Nachweis-Status). Die Auswahl wird im Browser (`localStorage`) gemerkt
und beim nächsten Öffnen vorbelegt (`OpenClassRequest.auto_done`).

## 2026-07-10 — Helferclient: Weiter-Button wandert ins Menü, Lupe zieht in die Warteschlangen-Kopfzeile

`#next-btn` ist jetzt in und außerhalb des Menüs dieselbe, immer sichtbare
Schaltfläche (kein Verschwinden bei leerer Warteschlange mehr): außerhalb
Kind von `.status-bar` wie bisher, im Menü per JS in `.top-section` umgehängt
und dort an die Stelle gesetzt, an der zuvor die Lupe saß (`grid-area: next`,
ersetzt die alte `search`-Spalte). Die Lupe (`#search-btn`) sitzt im Gegenzug
jetzt fest in der „Warteschlange"-Kopfzeile (rechts neben dem Titel) und
blendet dort rein per CSS-Opacity ein/aus, ohne Reparenting. Dazu einheitlicher
7px-Abstand zwischen dieser Kopfzeile und ihren Nachbarn (Statuszeile/Lupen-
Dropdown oben, Klassen-Reiter unten) — passend zum Abstand zwischen Statuszeile
und Menü-/Weiter-Button.

Beim Umbau zwei FLIP-Animations-Bugs behoben: (1) die alte Button-Position
wurde nach statt vor dem Klassen-Toggle gemessen, wodurch der Button ohne
sichtbare Bewegung an sein Ziel sprang; (2) da der Button beim Schließen des
Menüs Kind eines selbst FLIP-animierten Elements (`.status-bar`) wird, addierte
sich sein eigener Transform zum ererbten — er schoss weit über die Zielposition
hinaus statt sanft mitzuwandern. Details + wiederverwendbare Faustregel:
`~/cc/_logs/2026-07-10_sba_helfer_weiter_lupe_swap.md`.

Reiner UI-Fix im Helferclient (`web/scan.html`, `web/scan.js`), kein
Verhaltenseingriff auf dem Buchungspfad. Commit `de59af6`.

## 2026-07-10 — Scan-Client: Alert-Farbe der Statuszeile bleibt am Alert-Text

`web/scan.js` toggelte `status-book-deleted` (rot/fett) auf `#status-text`
direkt neben etlichen `textContent`-Zuweisungen, ohne die Klasse an anderer
Stelle zuverlässig zurückzunehmen — nach einem Alert (ausgemustert/an
jemand anders verliehen) blieb die Formatierung teils auf nachfolgenden,
harmlosen Statustexten (z. B. „Gesendet: `<Code>`") hängen.

Neuer zentraler Setter `setStatusText(text, isAlert = false)` setzt Text und
Klasse in einem Schritt; alle ~25 Zuweisungsstellen laufen jetzt darüber.
Die Alert-Formatierung gilt damit nur noch für den einen Aufruf im
`scan_result`-Handler, der sie mit `isAlert = true` explizit anfordert —
jeder andere Statustext setzt automatisch die normale Schrift zurück.

Reiner UI-Fix, kein Verhaltenseingriff auf dem Buchungspfad.

## 2026-07-09 — `_read_booking_result`: DOM-Annahme geklärt, Selektoren bereinigt

Auswertung des DOM-Dumps `automation/out/06b_kartei_geladen.html` klärt die
zuvor als offen geführte Frage zum `has_not`-Filter:

- `input.tt-input` liegt in einem `<form>` oberhalb der Tabellen; **keine** der
  16 `<tr>` enthält ein `<input>`. Der Filter ist im heutigen DOM ein No-op.
- Der befürchtete False-Positive kann trotzdem nicht eintreten: der Erfolgs-Check
  liest `inner_text()`, und der Wert eines `<input>` ist kein Textknoten. Der
  Filter stammt aus einer Implementierung mit `get_by_text(barcode)` über die
  ganze Seite. Er bleibt — als Schutz gegen Selektor-Drift (Typeahead-Dropdowns
  rendern echte Textknoten).
- `.books-list`, `.lent-books`, `.student-books` kamen im DOM nicht vor und sind
  entfernt. Es bleiben die zwei verifizierten Selektoren, die dieselben
  `<tr ng-repeat="book in bl.books">`-Zeilen treffen. Weniger Kandidaten kann eine
  Erkennung höchstens von `booked` auf `unknown` kippen — die sichere Richtung.

Der Eintrag in `docs/test_status.md` war entsprechend zu alarmistisch und ist
korrigiert. Neu dort als offen geführt: der Substring-Vergleich gegen den ganzen
Zeilentext (statt gegen die Code-Spalte) und das feste `wait_for_timeout(1500)`.
Beide zeigen Richtung `unknown`, nie Richtung `booked`; eine Änderung im scharfen
Buchungspfad nur mit Freigabe (PLAN §6). Kein Verhaltenseingriff in diesem Commit.

## 2026-07-09 — Wartbarkeits-Refactoring (ruff, Modularisierung, Testabdeckung)

Sieben Commits, reines Aufräumen — keine neuen Endpoints, keine Feature-Änderung
außer den beiden unten markierten Verhaltensänderungen.

- **Linter eingezogen** (`39c94f9`): `ruff` (E/F/W/I/B/UP/SIM) + `.pre-commit-config.yaml`;
  vorher gab es keinen. 38 Findings automatisch behoben, 22× `raise … from e/None`
  ergänzt. `E501`/`SIM105` bewusst ignoriert (Begründung in `pyproject.toml`).
- **Toter Code entfernt** (`b7ac0cc`): `/api/select-class` + `/api/add-test-students`
  hatten keine Aufrufer mehr. Damit fiel der als Strangler-Pattern markierte
  AppState-Kompat-Layer (`queue`/`active_form`/`book_order`/`class_catalog*`-
  Properties, `ClassContext.implicit`, `ensure_active_context`) weg. Neu:
  `AppState.book_order_of(context_id)` und `AppState.active_students()`.
  - **Verhaltensänderung:** `book_order_of()` liefert `[]` für einen Kontext
    ohne eigene Reihenfolge statt — wie der Kompat-Layer es tat — still auf die
    gerade aktive Klasse zurückzufallen. Ein Helfer ohne Klassenbindung bekam
    dadurch bisher unbemerkt die Buchreihenfolge einer fremden, zufällig
    aktiven Klasse; jetzt bekommt er eine leere Liste (Client rendert dann in
    Server-Sortierung).
  - **Bugfix:** Der Guard in `/api/select-schoolyear` prüfte nur die Queue des
    aktiven Klassen-Tabs. Aktive Schüler in anderen, nicht-aktiven Tabs wurden
    beim Schuljahreswechsel ohne Warnung abgerissen. `AppState.active_students()`
    iteriert jetzt alle Kontexte, nicht nur den aktiven.
- **Frontend entflochten** (`d66e2e9`): neu `web/common.js` (`escapeHtml`,
  `isBookDone`, `Beeper`, gemeinsames `connectWebSocket`); `web/host.html`
  (2167 Zeilen) aufgeteilt in `web/host.html` (221 Zeilen) + `web/host.js` +
  `web/host.css`. Weiterhin kein Build-Step.
- **Dokumentation strukturiert** (`a0ccb72`): `docs/CHANGELOG.md` neu angelegt;
  `docs/PLAN.md` 993 → 675 Zeilen, `docs/test_status.md` 619 → 461 Zeilen
  (Chronologie-Prosa ausgelagert ins Changelog).
- **Server-Duplikate entfernt** (`84ad84c`): `hydrate_student_info()`,
  `_detach_helper()`, `_grade_and_catalog()`, `QueueStudent.from_iserv()`
  waren mehrfach implementiert bzw. inline dupliziert.
- **API-Schicht umgebaut** (`7dc1f67`, `a7a75b4`): `require_host` ist jetzt eine
  FastAPI-Dependency auf einem `host_router` statt 30× wiederholter Cookie-
  Boilerplate; ~20 Pydantic-Request-Models ersetzen die manuelle Body-Validierung.
  Die drei Dev-Bool-Toggles laufen jetzt über `POST /api/settings/{key}`
  (Whitelist) statt eigener Endpunkte. `server/routes/api.py` (1425 Zeilen) ist
  in neun Module aufgeteilt (`_deps.py`, `auth.py`, `classes.py`, `booklists.py`,
  `helpers.py`, `queue.py`, `slips.py`, `modus_b.py`, `settings.py`); `api.py`
  bleibt als Aggregator/Re-Export, `server/app.py` unverändert.
  - **Verhaltensänderung:** Validierungsfehler bei Request-Bodies liefern jetzt
    HTTP 422 statt 400 (Pydantic-Standard). Kein bestehender Client wertete den
    400er-Statuscode aus, daher unkritisch. Die strukturierten 409-Responses
    (`active_sessions`/`blocked`) und die Buchungs-Gates sind unverändert;
    `confirm` bleibt bewusst `bool = False` statt Pflichtfeld, damit ein
    fehlendes `confirm` weiterhin NACH dem `ALLOW_BOOKING`-Gate abgewiesen wird
    (403 vor 400/422) — empirisch mit Spion-Worker nachgeprüft.
- **Testabdeckung ausgebaut** (`d17ee5b`): 158 → 187 Tests. Neu
  `tests/test_stale_guards.py` (Stale-Guards Modus A/B), `tests/test_ws_scanner.py`
  (WS-Message-Dispatch: `call`, `search_call`, Peek-Toggle, malformed Frame),
  `tests/test_booking_result.py` (`_read_booking_result`, inkl. Typeahead-
  False-Positive-Schutz). Coverage gesamt 47 % → 59 %, `routes/ws.py` 13 % → 38 %,
  `sessions.py` 65 % → 74 %. Jeder neue Guard-Test hat eine Mutationsprobe
  bestanden (Guard-Zeile auskommentiert → Test rot → zurückgenommen).
- **Kommentar-Historie aufgeräumt** (`b1c1d59`): Datums-/Freigabe-Marker und
  Vorher-Nachher-Erzählungen aus Code-Kommentaren entfernt (leben jetzt hier im
  Changelog bzw. im Git-Log). Invarianten, Race-Condition-Hinweise und alle
  Produktionsschutz-/`noqa`-Begründungen blieben unangetastet — verifiziert per
  AST-Vergleich, kein ausführbarer Code geändert.

Ergebnis: 187/187 Tests grün, ruff sauber, kein produktives Verhalten außer den
zwei oben markierten Punkten geändert.

## 2026-07-09 — Menü-Icon-Animation, Warteschlangen-Überschrift

Menü-Icon: drei Balken → Linkspfeil (←) beim Öffnen, auf derselben
`.35s cubic-bezier`-Kurve wie der Menü-FLIP; `prefers-reduced-motion`
respektiert (.01ms). CSS-only, kein JS.

Warteschlangen-Überschrift `qh-title` übernimmt die Schrift des
Schülernamens (`.s-name`: 1.5rem/700/line-height 1.2) — keine
Kapitälchen/Sperrung/Transparenz mehr.

## 2026-07-09 — Animations-Sync Peek-Menü

Beim Öffnen/Schließen faden die ausgeblendeten Steuer-Elemente
(gear/reader/right-col/print/next) und die Lupe (`#search-btn`) jetzt
synchron mit der Statuszeilen-FLIP-Bewegung aus/ein — alles
`.35s cubic-bezier(.22,.61,.36,1)`, beide Richtungen. `flipAnimate` →
`animateMenu(open)`: Steuer-Elemente werden per `position:absolute` an
alter Stelle festgepinnt (aus dem Fluss → Grid kollabiert weiter) und per
`opacity` gefadet; Lupe öffnet per CSS-Opacity, schließt per Pin.
`print`/`next` (in `.status-bar`, das der FLIP per `transform` versieht)
werden für den Übergang ins nicht-transformierte `.top-section`
umgehängt — sonst reiten sie auf dem Transform und machen dessen
diskreten x-Sprung (full-width→Mittel-Spalte) mit. Generation-Guard +
Reset fangen schnelles Toggeln ab. Headless verifiziert (Playwright):
kein JS-Fehler, Layout-Kollaps real (Statuszeile 125→7 px), print/next
nach Zyklus wieder in `.status-bar` in Reihenfolge, keine Inline-Reste.
Live am Gerät offen.

## 2026-07-09 — Scanner: Reconnect stellt auch Lupe-Schüler wieder her + schneller Worker-Reload

Wird die Helferclient-Seite neu geladen, stellt der Reconnect-Pfad
(`server/routes/ws.py` `ws_scanner`) den aktuell geladenen Schüler wieder
her und lädt die Kartei im Worker neu (`StudentSession.reload()` →
`worker_ready`). Zwei Lücken/Verbesserungen:

- **Lupe-Schüler (`search_call`)** ging bisher beim Reload verloren: er
  ist bewusst **nicht** in einer Queue eingetragen, also lief
  `state.find_student` None → der Reconnect sendete `waiting`, der Schüler
  war weg, der Worker wurde **nicht** neu geladen. Fix:
  `HelperSession.student_form` speichert die Klasse beim Zuweisen
  (`assign_student_to_helper`); der Reconnect nimmt die Form daraus, falls
  `find_student` None liefert, und durchläuft dann auch für den
  Lupe-Schüler den Wiederherstellungs-+Worker-Reload-Pfad. `end_student`
  räumt `student_form` in beiden Zweigen mit auf. (Hintergrund/Peek ist nur
  eine Ansicht — beim Reconnect kommt der Schüler ohnehin als aktiv
  zurück, `helper.peeking` wird auf False gesetzt.)
- **`StudentSession.reload()` beschleunigt**: Angular steht auf der
  bereits geöffneten Page → kein App-Root-Load (~4 s) mehr. Stattdessen
  Hop auf `#/counter` (erzwingt echten Re-Render — gleicher Hash allein
  wäre ein Angular-No-Op ohne frische Buchdaten) und zurück auf
  `#/counter/student/<id>`, beides In-App-Hashrouten via `_goto_authed`
  (inkl. Re-Login-Recovery). Sicherer Fallback auf vollständiges
  `load_card()` (Root + Schüler-Route), falls das Barcode-Feld nicht
  erscheint. `load_card` (frisches `open_student`) bleibt unverändert —
  dort muss Angular von der Root initialisiert werden (Spike B). Nur
  GET-Routen, kein `page.reload()` (kein Post-Re-Post-Risiko).

Unit-Suite: `uv run pytest` **149 grün**. `tests/test_scanner_reconnect.py`
reload-Tests an neue Goto-Sequenz (`#/counter` → Schüler-Route, Fallback
`load_card`) angepasst (Re-Login/Timeout/fehlendes-Re-Login/
Schüler-Route-Redirect). `tests/test_queue_flow.py` +1
(`assign_student_to_helper` setzt `student_form` für Queue- wie
Lupe-Schüler; Advance wechselt die Form mit) sowie `student_form`-Clear-
Assertionen im transienten `end_student`- und `assign`-Test.

Am Gerät (manuell, read-only, erst nach Freigabe — PLAN §6) offen: siehe
`docs/test_status.md`.

## 2026-07-09 — Host: „Test Config" als eigener Tab statt Sub-Reiter

Der „Test Config"-Sub-Reiter im „Schüler hinzufügen"-Bereich jedes
Klassen-Tabs entfällt; stattdessen bietet das „+"-Menü (`panel-new`) neben
„Neue Klasse öffnen" jetzt eine zweite Karte „Test Config öffnen". Klick
öffnet einen eigenen, dedizierten Tab (Pseudo-Klasse `Test Config`, kein
echter IServ-Code, kein Katalog-Abruf) und befüllt ihn **sofort** mit den
festen Testschülern. Erneutes Öffnen (weiterer Klick, oder Reload)
reaktiviert denselben Kontext statt eine zweite Queue anzulegen (Dedup
über `ctx.form`, analog `/api/open-class`). „Schüler hinzufügen" in
normalen Klassen-Tabs bleibt unverändert bei „Einzelne Schüler" (jetzt
ohne Sub-Tab-Leiste, da nur noch ein Inhalt).

Löst damit den früheren Reiter „Test Config" ab (2026-06-17, siehe
weiter unten): `TEST_STUDENTS`/`add-test-students` (IDs, Idempotenz-Test)
bleiben unverändert gültig.

- Backend: neue Route `POST /api/open-test-config` (`server/routes/api.py`,
  Konstante `TEST_CONFIG_FORM = "Test Config"`); nutzt weiterhin
  `TEST_STUDENTS`/`_load_test_students()`, aber ohne IServ-Roundtrip.
  Bestehende Route `POST /api/add-test-students` bleibt unverändert
  (weiter nutzbar, um Testschüler in **jeden** offenen Kontext
  nachzuziehen).
- Frontend (`web/host.html`): `panel-new` hat zweite Karte +
  `openTestConfig()` (spiegelt `openClass()`); `buildClassPanel()` ohne
  Sub-Tab-Leiste mehr, tote Funktionen `ctxAddTestStudents`/
  `ctxSwitchSubTab` + Dispatch-Cases entfernt.

Unit-Test: `tests/test_api_guards.py::test_open_test_config_populates_and_reuses`
— erster Aufruf befüllt mit allen `TEST_STUDENTS`, zweiter Aufruf
reaktiviert denselben Kontext (`reused: True`, kein zweiter Eintrag in
`state.contexts`). Suite grün (148 passed). `node --check` auf den
extrahierten `<script>`-Block → OK.

## 2026-07-09 — Scanner: Hinweis-Modal für JEDEN nicht-verbuchbaren Scan (beide Clients)

Bisher öffnete nur `book_deleted`/`not_in_stock`/`series_already_lent` ein
Hinweis-Modal; alle anderen nicht-OK Auswertungen (`not_enrolled` =
„nicht bestellt", `unknown_book` = „unbekannt", `not_ready` = „Buchliste
noch nicht geladen", `error` = Lookup/Client-Fehler) liefen nur als Text
in der Statuszeile mit. Jetzt öffnet **jeder** nicht-OK Scan ein Fenster
(gleicher Modal-Baukasten wie die bestehenden Alerts):

- **Schüler-Client (Modus B, `web/student.html`):** die drei
  sicherheitskritischen Fälle bleiben **Host-geschlossen** (blockierend,
  kein Schließen-Button, serverseitig `book_alert_open` blockiert weitere
  Scans, nur der Betreuer gibt per `book_alert_clear` frei) —
  `book_deleted` (ausgemustert, mit **und** ohne Ersatzanspruch, d. h.
  `loaned_to` spielt keine Rolle für die Schließ-Logik) **und**
  `not_in_stock` (an andere Person verliehen). **Alle übrigen nicht-OK
  Status** (`series_already_lent`, `not_enrolled`, `unknown_book`,
  `not_ready`, `error`) schließt der Schüler **selbst** (Schließen-Button
  **oder** nächster Scan) und scannt weiter — der bestehende
  close-on-next-scan-Pfad greift für jeden dismissiblen Hinweis. Neue
  Hilfs-Sets `OK_STATUSES_STUDENT` (`staged`/`booked`) und
  `BLOCKING_STATUSES_STUDENT` (`book_deleted`/`not_in_stock`);
  `dismissible = !ok && !blocking`.
- **Helfer-Client (Modus A, `web/scan.js`):** **jedes** nicht-OK Modal ist
  am Gerät schließbar (Button / Klick außerhalb / Escape / nächster
  Scan); `dismissBookAlert` beim nächsten Scan räumt ggfls. die
  Host-Meldung auf (`clear_book_alert`), bei Status ohne Host-Broadcast
  (alle neuen + die Selbst-Leihe) ist das Clear ein No-op. `OK_STATUSES`
  statt der alten `ALERT_STATUSES`-Menge.

Beide Clients: `ALERT_META` um Titel/Farbe für die neuen Status ergänzt
(orange = Hinweis: `not_enrolled`/`not_ready`/`series_already_lent`; rot =
Fehler: `unknown_book`/`error`). Rein client-seitig — Server-Pfad
(`evaluate_scan_for_booking`, `process_scan`, `book_alert`-Broadcast) und
IServ/DB unangetastet (read-only, kein GET mehr als bisher, kein Write).
`node --check` OK; manuelle Geräte-Verifikation offen. Commit `eba6071`.

## 2026-07-09 — Host: Tabs & Einstellungen global — Server-State statt localStorage

Offene Klassen-Reiter und Einstellungen sind jetzt auf jedem angemeldeten
Host-Rechner sichtbar/synchron. Quelle der Wahrheit = der bereits globale
In-Memory-Serverstate (`state_snapshot` + `broadcast_host`), nicht mehr
pro-Browser `localStorage`. `web/host.html`: `tabOrder` in `applyState`
aus `state.contexts` abgeleitet; `activeTab` rein pro Bediener
(In-Memory, nicht persistiert — *Menge* offen global, *Fokus* pro
Browser); Dev-Toggles (PDF-lokal, Klasse-korrigieren, Schüler-Leihschein)
aus `state` spiegeln statt localStorage; Login pusht nicht mehr lokal →
Server. `server/routes/api.py`: `/api/slip-default` broadcastet
zusätzlich `broadcast_host`. **Theme (Auto/Hell/Dunkel) bleibt bewusst
pro Browser in localStorage.** Keine IServ-/DB-Writes; nur App-eigene
In-Memory-Endpunkte. Commit `0e39cd5`.

Unit-Suite: `uv run pytest` **145 grün** (keine Logik auf
Server-Modellebene geändert; `state_snapshot` unverändert;
1-Zeilen-Broadcast-Zusatz in `/api/slip-default` wird von keiner
Bestands-Assertion getroffen). `grep localStorage web/host.html` → nur
noch `theme` (cycleTheme/applyTheme).

## 2026-07-09 — Scanner: Lupen-Suche — Schnellsprung zu beliebigem Schüler

Peek-Modus (`scan.js`): die **Lupe** öffnet ein Such-Panel unter der
Statuszeile — Warteliste fährt per FLIP nach unten, zwei Dropdowns
blenden synchron ein (oben Klasse, unten Schüler der gewählten Klasse).
Schüler wählen → `search_call` lädt ihn (ersetzt den
Hintergrund-Schüler). Letzte Klasse wird beim erneuten Öffnen
vorausgewählt (`localStorage`), änderbar. **Read-only** (nur IServ-GETs).

Backend: neue WS-Nachrichten `search_classes`/`search_students` (IServ
`get_class_names`/`get_students_for_form`, schuljahrbezogen im
`state.class_names_cache`/`form_students_cache` gecacht, geleert im
Schuljahreswechsel) + `search_call` (transienter `QueueStudent`, **nicht**
in einer Queue, laden via `assign_student_to_helper`). `end_student`
räumt auch nicht-gequeuete Schüler auf (neuer `else`-Zweig via
`find_helper_for_student`). Unit-Suite grün (145 passed; +2 Tests in
`tests/test_queue_flow.py`: transienter `end_student` + transienter
`assign_student_to_helper`). `node --check web/scan.js` OK;
Server-Imports OK.

## 2026-07-09 — Helfer-Menü: Klassen-Reiter für alle offenen Host-Klassen

Im Peek-Modus (`web/scan.js`/`scan.html` + Server-WS) zeigt das
Helfermenü jetzt **Reiter für alle offenen Host-Klassen** (alle
nicht-impliziten `state.contexts`), horizontal scrollbar; eigene Klasse
vorausgewählt, sonst erste offene. Pro Reiter darunter die Warteschlange
dieser Klasse mit „Aufrufen"-Button (wie bisher). Der im Hintergrund
verbundene Schüler steht im Peek **nur in der Statuszeile**, die große
`.name-row` ist verborgen. Aufrufen aus einer **fremden** Klasse rebindet
den Helfer an diese Klasse (`helper.context_id` wechselt; danach zieht
„Nächster" aus der neuen Klasse) statt abzuweisen. Die Lupe bleibt
unverhalten zusätzlich. Commit `8bf6c08`.

Backend: `state.real_contexts_summary()` (alle offenen Klassen + je
wartende Schüler); `hub.broadcast_queue_size` sendet zusätzlich
`contexts_update` (`{contexts, own_context_id}`, pro Helfer) an denselben
Kreis (`student_id is None or peeking`), `queue_update` bleibt bestehen;
`routes/ws.py`: `contexts_update` bei Connect + `peek_queue`; `call` aus
fremder Klasse rebindet statt Fehler (`rebind_helper_to_context` in
`sessions.py`). Unit-Suite grün (147 passed; +1 in `tests/test_hub.py`
`contexts_update`-Broadcast, +1 in `tests/test_queue_flow.py` Rebind).
`node --check web/scan.js` OK; Server-Imports OK.

**Nachbesserung (Commit `9b11c75`):** Der aktive Reiter ist „nach unten
offen" (Host-Stil: Basis-Linie + 3-seitiger Rahmen ohne Unterkante → geht
in die Queue über), und bei jedem Öffnen des Menüs wird die eigene
Klasse (re-)selektiert (manuelle Reiter-Wahl bleibt nur bis zum
Schließen). Tests: 147 grün.

**Nachbesserung:** Ist keine Klasse offen, steht „Keine Klasse offen" nur
an Stelle der Klassen-Reiter (`renderQueueTabs` in `web/scan.js`), nicht
noch einmal darunter in der eigentlichen Warteschlange (`renderQueue`
lässt die Liste leer, statt den Text zu wiederholen).

## 2026-07-09 — Helfer-Menü: Menü-Button im Idle nutzbar

Das Hamburger-Menü ist jetzt auch **ohne zugewiesenen Schüler** (Idle)
funktionsfähig (Commit `9d5f413`). Es klappt im Idle lediglich die
Kamera-Zeile ein (Fokus auf die ohnehin sichtbare Warteschlange) und
fährt sie wieder aus — **kein Server-Roundtrip** (`peek_queue`/
`peek_close` entfallen), `queue-view` bleibt durchgehend an
(`keepQueueView`-Flag an `animateMenu`). Die Lupe ist im Idle-Menü
ebenfalls nutzbar (`search_call` funktioniert serverseitig auch ohne
aktuellen Schüler). Rein client-seitig (`idleMenuOpen`-Flag in
`web/scan.js`); keine neuen WS-Typen, kein Server-/DB-/IServ-Zugriff. Das
Burger-Icon morphet synchron mit dem Menü-FLIP zu einem Linkspfeil (←).
`node --check web/scan.js` OK; keine Server-Änderung.

## 2026-07-08 — Serverseitige Persistenz der Buchreihenfolge/Ausblendung

`book_orders_by_grade` + `hidden_isbns_by_grade` waren bislang reiner
In-Memory-State (weg beim Neustart). Neues `server/booklist_store.py`
speichert beide als einzelner globaler Satz in
`data/booklist_settings.json` (atomar, `data/` gitignored). Startup lädt
sie (`app.py` lifespan, non-fatal); `POST /api/booklist-order`/
`POST /api/booklist-hidden` schreiben nach jeder Mutation weg.
Schuljahreswechsel wischt die Konfiguration **nicht mehr** — nur
`form_catalog_cache` (ISBNs jahresspezifisch); `reset_booklist_orders()`
bleibt als Utility. ISBN-Drift zwischen Schuljahren fängt
`normalize_book_order` + `hidden & catalog` beim Lesen ab: neue
Katalog-Bücher sichtbar ans Ende, weggefallene gedroppt. Tests:
`tests/test_booklist_store.py` (+8; Round-Trip, fehlende/korrupte Datei,
data-Dir-Anlage, deterministische Serialisierung, neue-ISBNs-ans-Ende,
Nicht-String-Einträge gedroppt); Suite grün. Schreib-/Ladefehler
non-fatal (In-Memory-State bleibt Leading). Manueller Smoke am Gerät
offen (Neustart → Konfiguration wieder da).

## 2026-07-08 — Host-Überarbeitung: Settings + Tab-System (Multi-Kontext-Refactor)

Multi-Kontext-Refactor des Hosts (`web/host.html`) + Backend
(`server/state.py`, `routes/api.py`, `ws.py`, `sessions.py`, `hub.py`).

- **Backend-Kontext-Modell** (`state.py`): `ClassContext`, `contexts`-Dict,
  `active_context_id`, Kompat-Properties (`queue`/`active_form`/
  `book_order` delegieren an aktiven Kontext), `find_student`/
  `find_student_with_ctx` suchen über alle Kontexte, `next_pending`/
  `pending_count`/… nehmen `context_id`. `HelperSession.context_id` neu.
  Unit-Suite grün (143 passed) — bestehende Tests laufen über die
  Kompat-Properties weiter.
- **Routen-Migration**: `/api/open-class`, `/api/close-class`,
  `/api/set-active-context`, `/api/helper/{token}/class` neu;
  `add-student`/`add-test-students`/`disconnect-all`/`reset-queue`/
  `clear-queue` nehmen `context_id` im Body; `next-student` zieht aus
  `helper.context_id`; Scanner-WS-Handler (`peek_queue`, Waiting-Msg,
  `call`-Guard) kontextbewusst. Suite grün (143 passed).
- `node --check` auf den extrahierten `<script>`-Block → OK;
  Server-Imports (`server.main`/`routes.api`/`routes.ws`/`hub`/
  `sessions`/`state`) sauber.

Offene Teile (Frontend-Tab-Chrome, Klassen-Tab pro Kontext,
Helfer-Klassen-Bindung, E2E-Skript-Migration) siehe
`docs/test_status.md`.

## 2026-07-08 — Helferclient: Menü-Toggle / Peek zwischen Schüler- und Warteschlangen-Ansicht

Hamburger-Menü (≡) schaltet bei zugewiesenem Schüler auf die
Warteschlangen-Ansicht, **ohne** ihn zu trennen — er bleibt im
Hintergrund verbunden, Statuszeile zeigt ihn (`renderPeekStatus`),
Name/Zeile bleibt sichtbar. Nochmal Drücken kehrt zur Bücherliste zurück.
Im Peek werden Scans ignoriert. WS `{type:'peek_queue'}`/
`{type:'peek_close'}` + transient `helper.peeking` (Server) steuern
Live-`queue_update`s (`broadcast_queue_size`:
`student_id is None or peeking`).

- **Aufrufen eines anderen Schülers aus der Peek-Ansicht** legt den alten
  als **`pending`** (wartend) zurück in die Warteschlange, **nicht** als
  `done` — `call`-Handler `end_student(queue_status="pending",
  session_state="revoked")` (analog Disconnect-Teardown `_deferred_end`).
  „Weiter" (`next`/`advance_helper`) schließt den alten weiter als
  `done`.
- Scheitert der Aufruf (Schüler inzwischen von anderem Helfer genommen),
  kehrt der Client automatisch in die Peek-Ansicht zurück (kein
  „Schüler wird geladen …"-Stuck).

Unit: `tests/test_hub.py` +1 (Peek-Helfer erhält `queue_update`),
`tests/test_queue_flow.py` +2 (`end_student`/`assign_student_to_helper`
resetten `peeking`); Suite **133 grün**; `node --check` OK. Live am Gerät
offen (read-only, kein Enter — Niklas+Lukas-Freigabe).

## 2026-07-07 — Helferclient: Ausleih-Freigabe-Dialog bei Unstimmigkeit (O10)

Im Helferclient (`web/scan.js`/`scan.html`) wird beim ersten Buch-Scan
eines Schülers mit `remission_pending`/`exemption_pending`/`!paid`
(jeweils nur bei `enrolled`) der Scan zurückgehalten und ein
Bestätigungsdialog (Bauform wie Druck-Dialog) mit gelisteter
Unstimmigkeit gezeigt, **bevor** server-seitig
`evaluate_scan_for_booking` (Lager/angemeldet) + Worker-Eintragung
laufen.

- **„Ja, ausleihen"** → Scan geht raus, Flag `lendingApproved` merkt die
  Freigabe bis zum Neuladen des Schülers (`student_info`/`loading`/
  `waiting` resetten es) → weitere Bücher nicht mehr angefragt.
- **„Nicht ausleihen"**/Escape/Click-außerhalb → Scan verwirft, Flag
  bleibt false → nächster Scan fragt erneut.

Nur GET (`student_info`-Flags kommen ohnehin vom Server), kein
DB-/IServ-Schreibzugriff, keine Host-Benachrichtigung (bewusst
ausgeblendet). Analog zu Modus-B-O6, aber am Helfer-Client statt
Host-Pairing. Manuell verifiziert; kein automatisierter Test (UI-Gate).
Live am Testschüler mit künstlicher Unstimmigkeit offen (read-only —
Niklas+Lukas-Freigabe).

## 2026-07-07 — Bugfix: „Reihe an dich ausgeliehen" bei ausgeblendeten Reihen UND nach Buchung in derselben Session

Zwei Lücken im Erkennen „Buch bereits an dich selbst verliehen"
(`series_already_lent`), die beide denselben Symptom-Pfad hatten — ein
Scan des *eigenen* Exemplars fiel zu `not_in_stock` und deklarierte es
fälschlich als „verliehen an jemand anderes".

1. **Ausgeblendete Buchserie, die der Schüler bereits hat.**
   `apply_hidden_books` entfernt eine ausgeblendete Reihe nur aus
   `info["books"]`, **nicht** aus `info["current_books"]`. Bisher baute
   `booking_isbn_sets_from_info` die `lent`-Menge aus `info["books"]`
   status-basiert auf → eine ausgeblendete, aber bereits ausgeliehene
   Reihe fehlte in `lent` → der Scan des eigenen (durch `distributed`
   gekennzeichneten) Exemplars lief auf die Lager-Prüfung auf
   (`not_in_stock`). Fix: `lent` wird **autoritativ aus
   `info["current_books"]`** (ungefiltert) gebildet; nur falls
   `current_books` fehlt (Unit-Test-Fixture), wird auf die
   status-basierte Menge aus `info["books"]` zurückgefallen.
   `current_books` ist in echten `info`-Payloads aus
   `get_student_info` stets vorhanden.
2. **In derselben Session frisch gebuchtes Buch.** Nach einer Buchung
   (`status == "booked"`) ist das Exemplar serverseitig `distributed` an
   den Schüler, aber `lent_isbns` stammt noch aus der Lade-Zeit (ISBN
   steht dort in `vormerk_isbns`). Ein erneuter Scan desselben Exemplars
   — oder eines weiteren Exemplars derselben Reihe — in derselben Session
   (ohne Schüler-Neuladen) lief deshalb ebenfalls auf `not_in_stock` (mit
   `loaned_to` = Schüler selbst). Fix: `process_scan` hängt nach `booked`
   die ISBN von `vormerk_isbns` nach `lent_isbns` um. Die übergebenen
   Mengen sind die Session-Mutables (passed-by-reference) — das Update
   greift am Helfer- bzw. Schüler-Session-State direkt, ein Neuladen ist
   nicht nötig.

Beide Fixes sind reine read-only-Logik (kein IServ-/DB-Write, keine neuen
Endpunkte). **Lesson:** eine „ist das Buch an dich ausgeliehen"-Prüfung
muss die *ungefilterte* Buchliste des Schülers sehen — ein UI-Filter, der
Reihen für die Anzeige/Tabelle ausblendet (`apply_hidden_books`), darf
nicht die autoritative Quelle für den Verliehen-Status sein; und ein
serverseitiger Zustandswechsel (Buchung) muss die gecachten Prüf-Mengen
der Session mitschreiben, sonst veraltet der Cache bis zum nächsten
Neuladen. Tests: `tests/test_booking_precheck.py` +2
(`test_lent_from_current_books_ignores_hidden_filter`,
`test_process_scan_booked_isbn_moves_to_lent`), Suite 107 grün.
Live-Verifikation am Testschüler offen. Details:
`_logs/2026-07-07_sba_reihe_an_dich_erkannt.md`.

## 2026-07-07 — Ersatzanspruch-Hinweis + Lager-Prüfung vor Bestell-Prüfung

Zwei aufbauende Änderungen an `evaluate_scan_for_booking`.

1. **Ersatzanspruch bei ausgemusterten Büchern mit Schülerbezug.** Ein
   `book_deleted`-Buch, das noch eine `student_id != null` trägt (z. B.
   `[not_timely]` verloren, `[unusable]` beschädigt), reicht `loaned_to`/
   `loaned_to_id` durch — Host + Helfer zeigen zusätzlich „Ersatzanspruch:
   …" (Toast, Now-Serving-Kästchen `ns-borrower`, Helfer-Modal-Borrower-
   Zeile), der **Schüler-Client sieht nur „ausgemustert"** (kein Name,
   kein Hinweis; `process_scan` strippt für `source="student"` wie bei
   `not_in_stock`). `web/scan.js`/`web/host.html` branchen das Wording am
   `kind`/`status` (`book_deleted` → „Ersatzanspruch …", sonst „verliehen
   an …"). Ablösend zur früheren Idee, `[not_timely]` wie verliehen mit
   „verloren"-Wording zu behandeln — solche Bücher bleiben auf dem
   `book_deleted`-Pfad.
2. **Lager-Prüfung VOR Bestell-Prüfung.** Neue Prüf-Reihenfolge:
   `deleted → series_already_lent → nicht-im-Lager (not_in_stock) →
   nicht bestellt (not_enrolled)`. Ein verliehenes Buch zeigt jetzt immer
   „verliehen", auch wenn der Schüler es gar nicht bestellt hat (früher
   kam „Nicht bestellt" durch). `series_already_lent` (ISBN ∈
   `lent_isbns`) bleibt **vor** `not_in_stock`, da das Exemplar an dich
   selbst verliehen sein kann (distributed) — sonst würde „verliehen an
   dich selbst" gemeldet; es greift auch bei lagernden Exemplaren einer
   schon ausgeliehenen Reihe. `book_deleted` bleibt erste Prüfung
   (Ersatzanspruch-Display).

Kein DB-/IServ-Write — nur read-only Flags + WS-Broadcasts. Tests:
`tests/test_booking_precheck.py` +8 (Ersatzanspruch: Durchreichung +
Helper/Student-Unterschied für `book_deleted`; Reihenfolge:
`not_in_stock`-vor-`not_enrolled`, `series_already_lent`-vor-
`not_in_stock`, `series_already_lent`-bei-lagerndem-Exemplar), Suite 100
grün. Commit `9551f4e` (Ersatzanspruch), Reihenfolge-Update folgt.

## 2026-07-07 — Lade-State bis Worker bereit (`worker_ready`)

Beim Aufrufen eines Schülers wurden bisher die komplette `student_info`
(inkl. Bücherliste) sofort gepusht und der Playwright-Worker erst danach
geöffnet (`open_student`, mehrere Sekunden Browser-Navigation) — die
Bücherliste/der „Scanner bereit"-Status erschienen, bevor der Worker
buchungsbereit war, und Früh-Scans liefen auf „Worker-Session nicht
bereit". Neue getrennte Push-Phase über die WS-Nachricht `worker_ready`
(signalisiert „Worker buchungsbereit, Scans frei"), client-spezifisch:

- **Modus A (`web/scan.js`):** `student_info` bleibt vollständig (Bücher
  sofort sichtbar). `worker_ready` (ohne Bücher-Payload) flippt nur
  Statuszeile von „Warten…" auf „Scanner bereit — Buch scannen" + gibt
  Scans frei. Bis dahin ignoriert `onScanSuccess` Scans clientseitig
  (früher „Wird geladen…"-Text → jetzt „Warten…" konsistent mit
  `workerPending`-Flag).
- **Modus B (`web/student.html`):** `student_info` künftig **ohne
  Bücher** (`books: []`, nur Name/Klasse/Bezahlt + `book_order`).
  `worker_ready` trägt die Bücherliste und flippt Status von „Wird
  geladen…" auf „Scanner bereit" + gibt Scans frei. Bücher-Bereich zeigt
  bis dahin Placeholder „Bücher werden geladen…"; `onScanSuccess`
  ignoriert Scans (wie der ausgemusterte-Buch-Block via
  `workerPending`).

Server: `load_and_push_helper_student` (Modus A) sendet `worker_ready`
nach `set_worker_session` (oder sofort ohne `worker_pool`); bei
Playwright-Fehler nur `error`, kein `worker_ready` (Worker nie bereit →
Scans bleiben ignoriert, Helfer hat Bücher schon).
`load_and_push_paired_student` (Modus B) sendet `student_info` ohne
Bücher + `worker_ready` mit Büchern; bei Fehler nur `error` (Bücherliste
bleibt aus, Host muss eingreifen). Stale-Guards in beiden Routinen senden
kein `worker_ready` (neuer Schüler wird separat geladen). Reconnect
(`routes/ws.py` ×2): `student_info` neu + `worker_ready`, wenn Worker
bereits in `state.student_worker_sessions` registriert oder kein
Lade-Task (`helper.load_task`/`session.load_task`) mehr läuft — sonst
liefert der Task es an die neue WS.

Nur GET / read-only — `get_student_info` (GET) + `open_student`
(Browser-Navigation ohne Submit), keine DB-/IServ-Writes, keine neuen
Endpoints. Tests: `tests/test_queue_flow.py` +Assertion (`student_info`
mit `books==[]` + `worker_ready` nach `_advance_and_drain`), Suite grün.
Live-Verifikation am Testschüler noch offen (read-only, braucht
Niklas+Lukas-Freigabe).

**Scanner-Reconnect-Grace (Modus A, gleicher Tag):** Das `finally` des
Scanner-WS ruft den Schüler-Teardown (`end_student`: Schüler `pending`,
Worker zu) nicht mehr inline auf, sondern verzögert als Task
(`_deferred_end`, `_RECONNECT_GRACE_S=3.0`). Lädt der Helfer die Seite
neu (Reconnect), cancelt der neue WS den Grace-Task, übernimmt
`helper.ws` synchron (vor jedem await — so erkennt das alte `finally` an
`helper.ws is websocket` den Reconnect und löst keinen Teardown aus),
lädt `student_info` (GET) neu und — falls der Worker bereits bereit
stand — `StudentSession.reload()` (Re-Navigation über `load_card`/
GET-Routen inkl. Re-Login-Recovery, bewusst KEIN `page.reload()` wegen
Post-Re-Post-Risiko) auf dem **bestehenden** Context, dann
`worker_ready`. Läuft der Lade-Task noch, liefert dieser `worker_ready`
selbst an den neuen WS (`student_info` steht schon). Re-Checks in
`_deferred_end` (`helper.ws` gesetzt bzw. `helper.student_id` ≠ Original)
machen den Task zum No-op, falls er doch durchläuft (Cancel-RC,
`/api/skip`, neuer Schüler, …). Echte Trennung (Tab zu, kein Reconnect) →
Teardown nach der Frist — so steht kein „active" auf einem toten
Helfer-Token (Modus-A-Queue-Einträge räumt der Sweeper nicht ab). Vorbild
war Modus-B `ws_student`, dessen `finally` die Session ohnehin nicht
abbaut. `Hub.send_websocket` serialisiert die Reconnect-Sends über das
Per-WS-Lock gegen den In-Flight-Lade-Task. Nur GET, kein DB-/IServ-Write.
Tests: `tests/test_scanner_reconnect.py` (14). Live am Gerät noch offen.

## 2026-07-07 — Bugfix: Scanner reagiert nicht auf Host-Trennung

`end_student()` löste die Helfer-Zuordnung serverseitig, informierte aber
nie den Scanner-WebSocket selbst — `web/scan.html` hat keinen
Host-State-Feed und reagiert nur auf gezielt gepushte Nachrichten. Betraf
„Trennen" **und** „Alle Verbindungen trennen". Fix: `end_student()`
schickt jetzt zusätzlich `hub.send_scanner(old_helper, {"type": "waiting",
...})` an den betroffenen Helfer. **Lesson:** jede neue serverseitige
Aktion, die einen Helfer-Zustand ändert, braucht einen expliziten
`send_scanner`-Push — ein `broadcast_host`-Aufruf allein erreicht den
Scanner nicht.

## 2026-07-07 — Warteschlange im Helferclient + gezielter Aufruf (`call`)

Bisher zeigte der Helfer-Scanner bei keinem zugewiesenen Schüler eine
*leere* Buchliste + in der Statuszeile nur die Warteschlangen-**größe**
(`queue_update` trug nur `queue_size`, nie die Einträge); „Weiter" nahm
den ältesten Wartenden (`next_pending`), ein *gezielter* Aufruf fehlte.
Neu: bei keinem Schüler zeigt der Buchlistenbereich die
**Warteschlange** — selbes Zeilenformat wie die Bücherliste, aber
**ohne Farbgebung**, mit **„Aufrufen"-Button** pro wartendem Schüler.
Klick ruft genau diesen Schüler gezielt auf (neuer WS-Handler
`{type:'call', student_id}`).

- **Server (read-only, nur lokale Helfer-Zuweisung — kein DB-/IServ-
  Write):** `state.pending_queue_as_list()` (nur `status='pending'`);
  `queue_update` + alle `waiting`-Nachrichten tragen jetzt die
  `queue`-Liste (nur an unzugewiesene Helfer); `assign_student_to_helper()`
  aus `assign_next_pending_to_helper` extrahiert (wird von „nächster" und
  „aufrufen" geteilt); `call`-Handler prüft `target.status == 'pending'`
  **atomar** (kein Await zwischen Prüfung und Zuweisung → kein
  Doppel-Aufruf zweier Helfer auf denselben Schüler), beendet ggf. den
  alten Schüler, weist den gezielten zu; bei Nicht-verfügbar `error` +
  sofortiger `queue_update`-Push.
- **Client (`web/scan.js`/`scan.html`):** `renderQueue()` rendert
  `.queue-row` (transparent, keine `row-vorgemerkt`/`row-ausgeliehen`-
  Tint) mit `.call-btn`; delegierter Klick-Handler sendet
  `{type:'call', student_id}`.

Nur GET / read-only, keine DB-/IServ-Writes, keine neuen
REST-Endpoints. Tests: 105 grün (+2 in `test_queue_flow.py`:
`assign_student_to_helper` gezielt, `pending_queue_as_list`; 2 angepasste
Assertions wegen neuem `queue`-Feld). Live-Verifikation am Testschüler
offen. Details: `_logs/2026-07-07_sba_helfer_queue_anzeige.md`.

**Bugfix (gleicher Tag) — Queue während des Schüler-Ladens verbergen
(auch „Weiter"):** die Queue darf nur erscheinen, wenn *weder* ein
Schüler geladen ist *noch* gerade einer geladen wird. Erster Entwurf
flaggte nur den „Aufrufen"-Klick (`awaitingCall`) — bei „Weiter" (`next`)
stand der nächste Schüler schon fest, aber `student_info` fehlte noch;
in diesem Fenster konnte eine späte `queue_update` die Queue wieder
aufblitzen lassen. Generalisiert: `awaitingCall` → `loadingStudent`,
gesetzt in **beiden** Pfaden (`advanceToNext` für `next` UND
Aufrufen-Klick für `call`); Queue rendert nur bei `!studentActive &&
!loadingStudent`; freigegeben bei `student_info`/`waiting`/`error`.
**Lesson:** ein Lade-Flag vor der ersten Server-Bestätigung muss *jede*
Aktion abdecken, die `student_info` nach sich zieht — nicht nur den neu
eingeführten Pfad.

**Bugfix (gleicher Tag) — Queue während des Schüler-Ladens verbergen,
auch bei Host-„Nächster":** das reine Client-`loadingStudent`-Flag
reichte nicht — der Host-„Nächster"-Button (`/api/next-student`)
triggert `advance_helper`/Zuweisung serverseitig, ohne dass der
Helfer-Client davon weiß; und das `waiting`, das `end_student` beim alten
Schüler schickt, renderte die Queue („Warteschlange angezeigt, obwohl
schon ein neuer Schüler geladen wird"). Neue WS-Nachricht
`{"type":"loading"}`: versetzt den Helfer-Client in den Lade-Zustand
(Queue verbergen, „Schüler wird geladen …", `loadingStudent=true`, kein
`studentActive`). Gesendet (a) von `end_student` im Advance-Kontext statt
des Idle-`waiting` (neuer Param `helper_notify={"type":"loading"}`;
Default `None` → weiter Idle-`waiting` für Disconnect/Skip/Reset, dort
soll die Queue erscheinen), (b) von `assign_student_to_helper` beim
Zuweisen — deckt auch den Fall, dass der Helfer keinen alten Schüler
hatte (Host-„Nächster", „Aufrufen" aus der Queue-Anzeige → kein
`end_student`). `/api/next-student` nutzt jetzt `assign_student_to_helper`
(DRY, bekommt den `loading`-Send gratis). `waiting` heißt jetzt
zuverlässig „idle" → Queue. **Lesson:** ein serverseitig ausgelöster
Übergang am Client braucht ein eigenes Signal (`loading`), wenn der
Client den Zustand nicht selbst initiiert hat — ein Client-Flag greift
nur bei selbst getätigten Aktionen. Tests: `test_queue_flow.py`
+Assertion (`advance_helper` sendet `loading`, kein `waiting`;
`assign_student_to_helper` sendet `loading`), Suite 105 grün.

## 2026-07-06 — `current_books`-Jahrgangsfilter entfernt

Der konservative `distributed_at`-Schuljahresfilter in
`get_student_info` (aus dem Review-Tier-2-Hardening vom 2026-07-05, s.
u.) ist raus; `?books=true` liefert zuverlässig nur aktuell ausgeliehene
Bücher (API-Referenz), der Filter hatte legitime Vorjahres-Bücher (noch
nicht zurückgegeben) unterschlagen. Jetzt werden alle aktuell
ausgeliehenen Exemplare ungefiltert als „ausgeliehen" ausgewiesen —
unabhängig vom Ausgabezeitpunkt. Siehe `server/iserv_client.py::get_student_info`.

## 2026-07-06 — Alert-Topologie verfeinert (Helfer schließt selbst, verliehen-an-andere symmetrisch zu ausgemustert, Selbst-Leihe als Hinweis)

Drei aufeinander aufbauende Nutzer-Korrekturen am Ausgemustert/
verliehen-Alarm.

1. **Helfer-Modal bekommt Schließen-Button, Host ohne für Helfer-Scans.**
   `process_scan()` trägt jetzt `source` (`"helper"` Modus A /
   `"student"` Modus B) in den `book_alert`-Broadcast ein. Der Host
   rendert seinen Schließen-Button im Now-Serving-Kästchen **nur** für
   `source !== "helper"` — am Helfer-Scanner schließt der Helfer sein
   Modal selbst (Button im `web/scan.html`-Modal), der Host zeigt die
   Meldung rot, aber ohne Button.
2. **Helfer-Schließen räumt den Host mit auf.** Neuer
   WS-Message-Typ `clear_book_alert` am Helfer-Scanner
   (`server/routes/ws.py`/`ws_scanner`) — der Server feuert
   `{"type": "book_alert", "student_id", "cleared": true}` an alle
   Host-Verbindungen. `dismissBookAlert()` im Helfer schließt das Modal
   **und** sendet das Clear (guard: nur wenn Modal offen war).
   Kontextwechsel (neuer Schüler/Wartend) bleiben rein lokal — dort räumt
   die Queue das Host-Kästchen ohnehin.
3. **Verliehen-Unterscheidung: an andere vs. an sich selbst.**
   - `not_in_stock` (Buch an **jemand anderen** verliehen) →
     **symmetrisch zu `book_deleted`**: Helfer-Modal mit
     Schließen-Button (räumt Host), Schüler-Modal **ohne** Button +
     **blockierend** (`StudentSessionB.book_alert_open` jetzt auch für
     `not_in_stock`, Scans werden serverseitig ignoriert bis Host-Clear),
     Host-Kästchen rot ohne Button (bei Helfer-Source) / mit Button (bei
     Schüler-Source).
   - `series_already_lent` (Buch bereits an **sich selbst** verliehen) →
     nur ein **Hinweis**, den Helfer wie Schüler **lokal** selbst
     schließen können (Button/nächster Scan), **nicht blockierend**,
     **ohne Host-Bezug** (`process_scan` broadcastet bei
     `series_already_lent` bewusst **nicht**).

   Modal-Titel/Farbe sind dynamisch per Status: `book_deleted`/
   `not_in_stock` rot („Ausgemustertes Buch gescannt" / „Buch noch
   verliehen"), `series_already_lent` orange („Buch bereits an dich
   verliehen"). Der Schüler-Client zeigt bei der blockierenden Variante
   „Bitte warte, bis der Betreuer dies freigibt.", beim Hinweis „Du
   kannst diese Meldung selbst schließen." + Schließen-Button.

Kein DB-/IServ-Write — nur read-only `book["deleted"]`/`distributed`/
`available` + WS-Broadcasts. Tests: `tests/test_booking_precheck.py` +2
(`test_process_scan_broadcasts_alert_for_not_in_stock`,
`test_process_scan_no_alert_for_series_already_lent`), Suite 92 grün.
Commits `09296f2`, `440f5b4`, `b4610de`.

## 2026-07-06 — Verliehen-an-Name bei `not_in_stock`

Wird ein Buch gescannt, das derzeit an **jemand anders** verliehen ist
(`not_in_stock`, `distributed`), zeigen **Helfer-Scanner und Host**
zusätzlich, **an wen** es verliehen ist — der **Schüler-Client (Modus B)
sieht den Namen bewusst nicht** (Privatheit: der Schüler scannt nur, der
Betreuer am Host/Helfer muss wissen, wem das Buch gerade gehört).
`server/iserv_client.py::get_book_by_code` liefert neben `student_id`
`loaned_to` („Vorname Nachname") + `loaned_to_id`. Der aktuelle Ausleiher
ist in `GET /books/:code` bereits als eingebetteter `Student` enthalten →
im Normalfall **kein Extra-Request**; nur falls die Einbettung
fehlt/anonymisiert ist, Nachladen per `GET /students/:id` (read-only,
tolerant bei Fehlern → `None`). `evaluate_scan_for_booking` hält die
`msg` bewusst **name-frei** („Nicht im Lager (verliehen): …") und trägt
den Namen nur als eigenes `loaned_to`-Feld. `process_scan` steuert die
Sichtbarkeit pro Source: der `book_alert`-Broadcast an den Host enthält
`loaned_to` immer (unabhängig davon, wer gescannt hat); das
zurückgegebene `scan_result`-Payload enthält `loaned_to`/`loaned_to_id`
**nur für `source != "student"`** (Helfer Modus A), für den Schüler
werden beide auf `None` gesetzt. UI: `web/scan.html` eigene Zeile
„Aktuell verliehen an: …" im Buch-Hinweis-Modal (liest `msg.loaned_to`);
`web/host.html` ergänzt Toast („— verliehen an …") und eine
`ns-borrower`-Zeile im Now-Serving-Kästchen; `web/student.html` zeigt
unverändert nur die name-freie `msg`. Host-Farbigkeit: im
Now-Serving-Kästchen ist nur der „verliehen an …"-Text rot
(`ns-borrower`-Zeile), der Alert-Meldungstext ist normal
(`ns-alert-muted`); Kästchen selbst bleibt rot (`ns-tile-alert`). Der
Toast bleibt als rotes Kästchen (`toast-warn`, weißer Text inkl.
„verliehen an …"). Namen werden **nicht geloggt** (PLAN §3.7), nur an
Host + Helfer durchgereicht. Kein DB-/IServ-Write. Tests:
`tests/test_booking_precheck.py` +4 (`test_not_in_stock_carries_loaned_to`,
`test_not_in_stock_without_borrower_stays_silent`,
`test_process_scan_loaned_to_for_helper`,
`test_process_scan_hides_loan_from_student`), Suite 96 grün. Commits
`15bf5f1`, `<follow-up>`.

## 2026-07-06 — Bezahlstatus-Quelle geklärt (O5) + Ermäßigungs-/Befreiungsnachweis + Modus-B-Host-Freigabe (O6 erweitert)

`enrollments`-Payload trägt `remission_*` (Ermäßigung) / `exemption_*`
(Befreiung) je Jahrganmeldung; `*_accepted` ist tri-state
(`null`=unentschieden). „Nachweis fehlt" = `*_request is True and
*_accepted is None`. Verifiziert am Testschüler 2159 (kein Antrag →
beide Pending=False). `get_student_info` liefert `paid`/`amount_open`/
`remission_pending`/`exemption_pending`; Clients zeigen „Nachweis fehlt"
in Offen-Farbe vor dem Betrag, „Bezahlt" bei Nachweis unterdrückt;
„Nicht angemeldet" im Schülerclient grau. Suite 92 grün.

O6 erweitert: UI zeigt Bücher + „nicht bezahlt"-Banner; Host kann beim
Pairing per `override_payment` freigeben. Ein ausstehender
Ermäßigungs-/Befreiungsnachweis blockt das Pairing ebenfalls; beide
Blocker (nicht bezahlt + Nachweis) werden gesammelt und in **einem**
kombinierten Host-Dialog freigegeben (`reason:"blocked"`-409 +
`blockers`-Liste; `override_payment` hebt alle auf). Nicht-angemeldete
Schüler lösen keine Nachfrage aus (Prüfung auf `enrolled` gegated,
verifiziert per Logik-Review — kein echter Nicht-angemeldet-Schüler auf
Prod verfügbar). Fachlicher Wortlaut/Workflow noch mit Hr. Pühn final.
Nachweis-Hinweis am Gerät mit echtem Pending-Fall steht noch aus (auf
Prod kein solcher Schüler bekannt) — siehe `docs/test_status.md`.

## 2026-07-05 — Bugfix: Context-Leak bei schnellem „Weiter"-Klicken

Wahrer Grund war ein permanenter Context-Leak, nicht nur eine Race.
`load_and_push_helper_student` läuft als `create_task`; `open_student`
pop'd einen Context und lief in `load_card()` (~5 s), aber erst **nach**
Return registrierte `set_worker_session` den Worker in
`student_worker_sessions[id]`. „Weiter" vor `load_card`-Ende →
`end_student(id)` → `pop(id)` → None → nichts freigegeben → Context
geleakt. Bei `WORKER_CONTEXTS=2` und zwei schnellen Klicks Pool dauerhaft
leer (jeder weitere Schüler: 12 s Timeout). Fix (gekoppelt): (a)
`open_student`: `except Exception` → `except BaseException` —
`CancelledError` ist seit Py3.8 `BaseException`, der alte Code ließ den
Context beim Cancel durchrutschen; Handler gibt Context + `notify_all()`
zurück. (b) `load_task`-Feld an `HelperSession`/`StudentSessionB`;
`end_student`/`invalidate_session` canceln den laufenden Lade-Task →
Context kommt zurück. Zusätzlich (mildere Race) `WorkerPool._lock` →
`asyncio.Condition`, `open_student` wartet bis 12 s statt sofort zu
werfen. Regressionstests in `tests/test_worker_pool.py` +
`tests/test_queue_flow.py`. Siehe
`_logs/2026-07-05_sba_worker_pool_release_race.md`.

## 2026-07-05 — Root-Cause-Fix Context-Leak (Review-Tier 1, Commit `d3a75bd`)

Der obige Fix war symptomatisch; vier strukturelle Lücken blieben:

(a) `release_worker` feuerte `asyncio.create_task(pool.release(...))` ohne
Strong-Ref → Task konnte mid-Release geGC'd werden (asyncio hält Tasks
nur schwach) → Context-leak. Fix: modullevel `_release_tasks`-Set +
`add_done_callback(discard)`.
(b) `load_task.cancel()` wurde **nicht awaited** — war der Task bereits
nach `await open_student` im **synchronen** `set_worker_session`, traf
`CancelledError` erst am nächsten `await` (keines mehr) → Task
registriert Worker für bereits abgebrochenen Schüler → orphaned. Fix:
jedes `cancel()` jetzt `with contextlib.suppress(asyncio.CancelledError):
await task`; plus Stale-Guard in `load_and_push_*` (`assigned_student_id`
capturen, nach `open_student` re-checken, sonst Worker schließen ohne
Registrierung).
(c) `remove_helper` (api.py) + `ws_scanner`-finally (ws.py) clear'ten nur
die WS — Schüler blieb `active`, Worker orphaned (Modus A hatte keine
TTL-Recovery wie Modus B). Fix: beide rufen jetzt `end_student(...,
pending, revoked)` + cancel/await `load_task`.
(d) `sweep_expired_sessions` ohne try/except → eine Exception tötet den
Sweeper dauerhaft. Fix: try/except pro Iteration (CancelledError
re-raise, Rest log+continue) + Batch-Broadcast.

**Privacy im gleichen Commit:** `TEST_STUDENTS` (echte Schülernamen) aus
`server/routes/api.py` in gitignored `tests/test_students.local.json`
ausgelagert (Default nur Niklas); `session_token[:6]`-Logging →
`sha256[:8]`-Handle. Suite grün (85). Siehe
`_logs/2026-07-05_sba_pool_leak_root_causes.md` +
`wiki/40_experience_logs/lessons_learned.md` („Await task.cancel()").

## 2026-07-05 — Review-Tier-2-Hardening (Commit `63a4cb3`)

Edge-Case-Bugs + Härtung aus dem Codebase-Review (4 Review-Agenten,
Tier 2). Dateibegrenzt parallel umgesetzt, Suite grün (85):

(a) `automation/worker.py`: `new_page()` an beiden Stellen im
try/except (Context wird bei Fehlschlag zurück in den Pool gelegt);
`release()` Double-Release-Guard (`session._context = None`);
`start()`-Cancel schließt aufgebaute Contexts; `_read_booking_result`
scoped auf Bücher-Liste (exkl. Eingabefeld), bleibt `unknown`-Default.
(b) `server/iserv_client.py`: `(b.get("BookView") or {})` (null-safe);
`threading.Lock` um Lazy-Init von `_client`/`_resolve_sy`/
`_get_series_map` (Lock hält nicht während API-Calls); konservativer
`current_books`-Jahrgangsfilter via `distributed_at` (keep-when-unknown
— sicher gegen falsche Enter). **Wieder entfernt am 2026-07-06** (siehe
Eintrag oben): der Filter hatte legitime Vorjahres-Bücher unterschlagen.
(c) `web/`: `escapeHtml` auf Kamera-id/-label (scan+student); `host.html`
`JSON.parse` try/catch; `pushSlipDefault` erst post-Login; `qr-img.src`
nur bei `data:image/`-Prefix.
(d) `server/routes/api.py`: 7× `int(student_id)`→400;
`secrets.compare_digest` für Host-Passwort + `join_secret` + neues
`login_limiter` (5/15s); `request.client is None`→400; `_base_url`
vertraut **nicht mehr** dem `Host`-Header-Hostnamen (IP aus
`cfg.host_ip`/Auto-Erkennung, nur Port aus Host — sonst
Host-Header-Injection ins QR-URL mit `join_secret`). `ws.py`:
`receive_json` fängt `json.JSONDecodeError`. `ratelimit.py`:
Dead-Pop-then-recreate entfernt (leere Deques werden jetzt echt
evicted). `config.py`: `req_int`-Helper (klare `SystemExit`-Fehler).
(e) `server/printing.py`: PDF-Dateiname µs+`token_hex` (keine
Sekunden-Kollision); PowerShell UTF-8-Console-Prefix; `_print_win_default`
via `asyncio.to_thread` (blockiert nicht den Event-Loop);
`pages`-Regex-Validierung. `server/tls.py`: Zertifikat-Expiry-Check beim
Start (regeneriert <30d); Key via `os.open(0o600)` (kein
world-readable-Fenster).
(f) `automation/`: Spike-Login-Check `and`→`or` (wie `worker.py`);
`test_printer.py` Single-Quote-Escaping; e2e `HOST_PASSWORD` in `main()`
mit klarem `SystemExit`. Test
`test_base_url_keeps_routable_host` → `test_base_url_ignores_spoofed_host_header_uses_config_ip`
(asserted jetzt die neue Security-Eigenschaft).

Siehe `_logs/2026-07-05_sba_tier2_hardening.md` +
`wiki/40_experience_logs/lessons_learned.md` („Host-Header nicht für
URL-Hostnamen vertrauen").

## 2026-07-05 — Review-Tier-3 (UI-Architektur + Server-Robustheit)

5 dateibegrenzt parallele Agenten + 1 Polish-Agent danach, Suite grün
(85):

(a) `web/scan.html`: großer Inline-`<script>`-Block mechanisch nach
`web/scan.js` extrahiert (493 Zeilen), `scan.html` auf 234 Zeilen
(Markup + `<script src="scan.js">`) reduziert. Ladereihenfolge
(`html5-qrcode.min.js` vor `scan.js`) erhalten, `node --check` grün.
(b) `web/host.html`: alle 34 inline `onclick=`/`onchange=`/`onkeydown=`
entfernt → `addEventListener` (direkt für statische Elemente, delegiert
via `data-action`/`data-student-id`/`data-token`/`data-code` für
dynamisch gerenderte Zeilen/Buttons). Grep bestätigt: keine
`on*=`-Attribute mehr im Markup oder in Template-Literal-`innerHTML`.
(c) `server/sessions.py`: `advance_helper` in zwei klare Schritte
gesplittet — ruft `end_student` und delegiert dann an neues
`assign_next_pending_to_helper` (Zuweisung + Broadcast + Hintergrund-Task
für `load_and_push_helper_student`), analog zur Cleanup-Reihenfolge bei
`/api/helper/{token}` DELETE. Tier-1-Stale-Task-Guards unangetastet.
(d) `server/hub.py`: Broadcast-Race behoben — `broadcast_host`,
`broadcast_queue_size`, `broadcast_settings` und `send_scanner` liefen
als unabhängige Tasks und konnten dieselbe WebSocket-Verbindung
gleichzeitig treffen (Interleaving/Reihenfolge-Risiko bei parallelen
Sends). Neuer `Hub._safe_send()` mit Pro-Verbindung-`asyncio.Lock` (in
`WeakKeyDictionary`, damit Locks toter Verbindungen nicht leaken).
`server/sessions.py`: `print_loan_slip_for` bekommt expliziten
`state.iserv is None`-Guard mit klarer `RuntimeError`-Meldung (statt
unklarem `AttributeError` auf `None.get_loan_slip_pdf`, wird von den
Aufrufern ohnehin generisch abgefangen).
(e) `server/tls.py`: dreifach duplizierte `ipaddress.ip_address`/
`ValueError`-Blöcke zu `_parsed_ip()`-Helper zusammengeführt;
`_hostname_ipv4s` vor Verwendung in `_candidate_ipv4s` einsortiert.
`server/printing.py`: toter `import subprocess` entfernt (nur
`asyncio.subprocess.PIPE`/`STDOUT` in Gebrauch). `automation/e2e_*.py`
bereits konsistent aus Tier 2, unverändert gelassen.
(f) Polish-Pass (nach a+b, gleiche Dateien): `host.html`
`renderStatusBar()` nutzt jetzt `settingsOpen()` statt eigener
DOM-Query-Duplikation; kein Dead-Code/`window.*`-Exposure-Rest aus den
onclick→addEventListener- bzw. Inline-Script-Extraktions-Refactors
gefunden (bereits sauber). Token-Rotation-Kommentare in `showMbQr()`
bereits ausreichend (WHY-only, keine Ergänzung nötig).

Verifiziert: `uv run pytest` 85/85, `node --check` auf `scan.js` +
extrahiertem `host.html`-Inline-Script grün, Grep bestätigt 0
verbleibende `on*=`-Attribute in `web/`. Kein Verhaltensunterschied im
Buchungspfad, `ALLOW_BOOKING`-Gate unangetastet.

## 2026-07-05 — Buchreihen ausblenden (Einstellungen-Dialog)

Jedes Buch im Reiter „Bücherlisten ordnen" (`host.html`) hat einen
👁/🚫-Button; ausgeblendete Reihen
(`state.hidden_isbns_by_grade: dict[grade→set[isbn]]`, reiner
In-Memory-State, kein DB-/IServ-Write) gelten beim Scannen nicht mehr
als „vorgemerkt" (weder Scanner- noch Schüler-Anzeige) und sind damit
nicht buchbar. Neue Funktionen `get_hidden_isbns_for_form()`
(`server/book_order.py`, spiegelt `get_book_order_for_form()`) und
`apply_hidden_books()` (`server/sessions.py`), gefiltert direkt nach
jedem `get_student_info`-Aufruf (4 Call-Sites: Modus A/B je Zuweisung +
Reconnect in `sessions.py`/`routes/ws.py`). Neuer Endpoint
`POST /api/booklist-hidden` (mirrort `/api/booklist-order`);
`GET /api/booklist-order` liefert zusätzlich `hidden: [isbn...]`. Tests:
`tests/test_class_book_order.py` +5, Suite 90 grün. **Live-Effekt bei
bereits geladenem Schüler bewusst nicht sofort** — analog zur
bestehenden Bücher-Reihenfolge greift eine Änderung erst beim nächsten
Laden/Reconnect, nicht rückwirkend auf eine schon offene
Scanner-Session.

**Gotcha (direkt nach Deploy):** Nutzer meldete „anwählbar, aber nicht
speicherbar" — Ursache war kein Code-Bug, sondern ein laufender
Server-Prozess (`reload=False`, kein systemd), der vor dem Code-Edit
gestartet war und die neue Route noch nicht kannte, während das
statische `host.html` sofort die neue UI zeigte. Diagnostiziert via
`ps -o lstart` vs. `stat -c %y`; Neustart bewusst dem Nutzer überlassen
(aktive Helfer-/Queue-Sessions wären sonst verloren gegangen). Details:
`~/cc/_logs/2026-07-05_sba_hide_book_series_and_reload_gotcha.md`,
`~/cc/wiki/40_experience_logs/lessons_learned.md`.

## 2026-07-05 — Karte „Bücher-Reihenfolge (Scanner)" entfernt

Mit dem Einstellungen-Dialog (Bücherlisten-Reiter, 2026-07-04) war die
Klassen-Karte funktional komplett redundant (gleicher Katalog, gleiche
`book_orders_by_grade`-Ablage), zeigte aber zwei Bugs:

1. `POST /api/booklist-order` pushte nur per `broadcast_settings` an die
   Scanner-Helfer-Sessions, nie per `broadcast_host` an den Host selbst —
   eine im Einstellungen-Dialog gespeicherte Reihenfolge aktualisierte
   weder die (jetzt entfernte) Klassen-Karte noch `state.book_order` am
   Host live, bevor man neu geladen hat. Fix: beide
   Bücher-Reihenfolge-POST-Endpunkte rufen jetzt zusätzlich
   `broadcast_host(state.state_snapshot())`.
2. `_ensure_class_catalog` (seedet `book_order` aus
   `book_orders_by_grade`) wurde bisher nur durch den Klick auf „Bücher
   laden & anordnen" ausgelöst — ohne den Klick blieb `book_order` leer,
   auch wenn im Einstellungen-Dialog längst eine Reihenfolge
   vorkonfiguriert war. Fix: `select_class` ruft `_ensure_class_catalog`
   jetzt automatisch auf, Fehler dabei sind nicht fatal (Klasse bleibt
   geladen, `book_order` bleibt leer wie bisher ohne Klick). Damit greift
   eine vorab im Einstellungen-Dialog gesetzte Reihenfolge sofort beim
   Klassenwechsel, ganz ohne Zusatzklick.

`GET|POST /api/class-book-order` + zugehöriges Frontend (`web/host.html`:
`boOrder`/`loadBookOrder`/`renderBookOrderList`/Drag-Handler/
`saveBookOrder`/`syncBookOrderCard`) entfernt; `normalize_book_order`/
`_ensure_class_catalog` bleiben (jetzt einzig von `select_class`
genutzt). Bestehende Tests (`tests/test_class_book_order.py`) testen nur
die Katalog-/Normalisierungs-Logik, nicht die entfernten Endpunkte —
unverändert grün (Suite 92).

## 2026-07-05 — Bücher-Reihenfolge pro Schüler-Jahrgang statt globaler Klassen-Order

Bis hierhin hing die Helfer-Anzeige an **einer** globalen
`state.book_order` für „die aktive Klasse". Für klassenübergreifende
Warteschlangen (einzeln hinzugefügte Schüler, „Test Config"-Tab) mit
Schülern aus verschiedenen Jahrgängen war das falsch: alle Helfer
bekamen dieselbe (meist leere oder zum falschen Jahrgang passende)
Reihenfolge. Fix: neues Modul `server/book_order.py` mit
`get_book_order_for_form(state, form)` — ermittelt den Jahrgang **des
jeweils zugewiesenen Schülers** (über `IsServClient.get_class_book_catalog`)
und liefert dessen `book_orders_by_grade`-Konfiguration, mit
`state.form_catalog_cache` (form → (grade, catalog_isbns)) gegen
wiederholte IServ-Roundtrips. `hub.broadcast_settings()` berechnet die
Reihenfolge jetzt **pro verbundenem Helfer** anhand seines eigenen
Schülers, statt einen globalen Wert an alle zu pushen; alle vier
`student_info`-Baustellen (`sessions.py` ×2, `routes/ws.py` ×2 —
Scanner-Reconnect + Modus-B-Reconnect) nutzen dieselbe Funktion. Live
per Playwright-freiem WS-Test verifiziert: zwei Helfer mit Schülern aus
Jahrgang 10 und 12 (ohne geladene Klasse, reiner Test-Config-Betrieb)
bekamen nach einer Jahrgangs-Umsortierung im Einstellungen-Dialog sofort
ihre jeweils eigene, unterschiedliche Reihenfolge gepusht.
`get_book_order_for_form` fängt IServ-Fehler intern ab (Fallback
`state.book_order`) — ein Fehler dort darf `student_info` nie
verhindern, da der Aufruf in `load_and_push_helper_student` außerhalb
des einzigen Try/Except-Blocks liegt. Suite weiter grün (85).

## 2026-07-04 — Host-Einstellungen-Dialog

Die zwei Inline-Umschalter der Status-Bar (Tailscale-IP,
Schüler-Leihschein) wurden in einen Modal-Dialog
(„Einstellungen"-Button, Stil wie Druck-Dialog) ausgelagert. Speichern
übernimmt nur Änderungen, Abbrechen/Esc verwirft. Enthält zusätzlich:

- **Drucker-Auswahl:** Dropdown der dem Gerät bekannten Drucker.
  `list_printers()` in `server/printing.py` (rein lesend: Windows
  `Get-Printer`/`Win32_Printer Default=TRUE`, macOS/Linux `lpstat
  -e/-d`). `GET /api/printers`, `POST /api/printer` → In-Memory
  `state.printer_name_override` (None = `PRINTER_NAME` aus `.env` bzw.
  Systemstandard). `print_loan_slip_for` nutzt Override vor
  `cfg.printer_name` (Host + Helfer). „Kein Drucker gefunden", wenn
  nichts verfügbar.
- **Bücherlisten ordnen (jahrgangsweit):** verallgemeinert die
  klassenweite Reihenfolge auf **alle Jahrgänge** des Schuljahrs, vorab
  konfigurierbar — ein **Reiter je Booklist** (Jahrgang), Katalog lazy
  geladen, per Drag & Drop sortierbar. `state.book_orders_by_grade`
  (dict grade→ISBN-Liste, In-Memory; Reset nur bei Schuljahreswechsel via
  `reset_booklist_orders`). `GET /api/booklists`
  (`get_booklists_overview` → `[{id,grade,title}]`), `GET|POST
  /api/booklist-order?grade=` (`get_booklist_catalog_by_grade`).
  `get_class_book_catalog` liefert jetzt `(grade, catalog)`;
  `_ensure_class_catalog` seedet `book_order` aus der jahrgangsweiten
  Reihenfolge, `POST /api/class-book-order` schreibt in dieselbe Map —
  Klassen- und Jahrgangs-Ordnung teilen sich `grade` als Key. Speichern
  für den Jahrgang der geladenen Klasse zieht `book_order` live nach
  (`broadcast_settings`). Alles nur GET/In-Memory, kein DB-Write. Tests:
  `tests/test_class_book_order.py` erweitert (Suite 79 grün).

## 2026-07-02 — Konfigurierbare klassenweite Bücher-Reihenfolge (Scanner)

Host legt per Drag & Drop die Anzeige-Reihenfolge fest, gilt für die
ganze Klasse und bleibt über Schülerwechsel (Reset nur bei Klassen-/
Schuljahreswechsel, Queue-leeren). Karte „Bücher-Reihenfolge (Scanner)"
in `web/host.html` zeigt die **ausleihbaren Bücher des Jahrgangs** aus
der offiziellen **Jahrgangs-Bücherliste** (`GET
/schoolyears/:sy/booklists/:id`, Klassenstufe = `form["grade"]`) — Basis
geändert (2026-07-02b): nicht mehr die Vereinigung der
Einzelanmeldungen, sondern die vollständige Jahrgangsliste (unabhängig
davon, welche Schüler gerade angemeldet sind). Nur `borrowable=True`
(keine Kauf-/Arbeitshefte), dedupliziert, `series_data` liefert
Titel/Fach direkt. Zugriff über `GET /api/class-book-order` (on-demand,
`iserv_client.get_class_book_catalog`, read-only, 2 GETs statt N).
**Mehrjahresbände sind enthalten** (2026-07-02d): die komplette
ausleihbare Jahrgangsliste wird gezeigt — der frühere
`min(gradesFlat)`-Filter (nur unterster Jahrgang) wurde auf Wunsch
entfernt. Drag & Drop mit **horizontaler Einfügemarke** (kein
Zeilen-Highlight). Speichern via `POST /api/class-book-order`
(`normalize_book_order` beschränkt auf Katalog + hängt fehlende an).
`state.book_order` reist in `student_info`/`settings` mit; Scanner
(`web/scan.html`, Modus A) **und** Schülerseite (`web/student.html`,
Modus B) sortieren nach `[erledigt, Klassen-Reihenfolge, Original]`.
Jeder Schüler sieht weiterhin nur seine eigenen Bücher. Tests:
`tests/test_class_book_order.py`.

**Erledigt-Gruppe nach Ausgabe-Aktualität sortiert** (2026-07-02d,
jüngstes oben): In der Erledigt-Gruppe ersetzt der „doneRank" die
Klassen-Reihenfolge — **gerade in dieser Session gescannte/ausgegebene
Bücher zuerst** (nach Scan-Reihenfolge, zuletzt oben; `scanOrder`-Map,
da staged/gebuchte Bücher im Client-Payload noch kein `distributed_at`
tragen), darunter die schon vorher ausgeliehenen nach `distributed_at`
(desc). `web/scan.html` + `web/student.html`.

Scanner-Buchliste: erledigte (gescannt/ausgeliehen) sinken nach unten —
`web/scan.html`, `isBookDone()` + stabile Sortierung.

## 2026-07-02 — Buchungs-Freigabe: Auto-Buchung mit Vorabprüfung (O10)

Niklas hat das Klicken auf **Enter** (Buchung gegen die Produktion)
freigegeben — aber **nur**, wenn eine gescannte Buchung **beide**
Bedingungen erfüllt (Details: `docs/PLAN.md` §6.1, dort inhaltlich
gepflegt und sicherheitskritisch unangetastet). Umsetzung:
`server/sessions.py::evaluate_scan_for_booking()` (read-only
Vorabprüfung, streng bei Unsicherheit) + `process_scan()` (gemeinsame
Scan-Verarbeitung Scanner/Schüler) + Master-Gate `ALLOW_BOOKING`
(Default `false`).

**Manueller „Buchen"-Button entfernt:** Der Host-UI-Button
(`web/host.html`, Kachel- + Queue-Ansicht) plus die `commitBook`-JS-
Funktion sind raus — er wurde nur bei `allow_booking=true` gerendert,
also genau dann, wenn die Auto-Buchung ohnehin läuft (redundant). Der
Endpoint `POST /api/commit-book` (+ `handle_commit`) **bleibt** als
dreifach gegateter Fallback bestehen, nur ohne UI-Fläche. Tests:
`tests/test_booking_precheck.py`, `tests/test_booking_gate.py`.

Nachfolgende Updates zu diesem Mechanismus (Ausgemustert-Prüfung
vorgezogen, Alert-Topologie, Ersatzanspruch, …) stehen jeweils unter
ihrem eigenen Datum in diesem Changelog; die aktuelle, vollständige
Beschreibung des Mechanismus steht in `docs/PLAN.md` §6.1.

## 2026-06-23 — Helfer-Druck-Dialog statt Sofortdruck

Klick auf den Drucker-Button (`web/scan.html`) öffnet ein Modal mit (a)
Warnung „Erst X von Y vorgemerkten Büchern gescannt" inkl. Liste der
offenen Titel, (b) Checkbox „Schüler-Leihschein (2. Seite)", (c) Buttons
**Abbrechen / Drucken / Drucken & nächster Schüler** (letzterer schaltet
nur bei `print_result.ok` weiter).

- Checkbox-Default = Host-Toggle, server-gesynct: Host pusht seinen
  `slip-second-page`-Stand via `POST /api/slip-default` →
  `state.slip_second_page_default` → `Hub.broadcast_settings` → Helfer
  (`{type:"settings"}`); Helfer bekommt den Wert auch beim WS-Connect.
  Reines UI-Setting, **kein IServ-/DB-Zugriff**.
- WS `print` nimmt jetzt `second_page` entgegen → `pages = None|"1"`.
- Buchliste aktualisiert sich live nach jedem Scan: `scan_result` trägt
  die `isbn`, der Client markiert das Buch „erledigt" (rein visuell;
  Scans bleiben `staged`, kein Submit). Dialog wartet vor dem Vergleich
  via `pendingScans`-Zähler auf den Abschluss laufender Scans.

## 2026-06-22 — Scan-Vorabprüfung gegen Anmelde-Buchliste

Bevor ein gescannter Barcode an den Worker gestaged wird, prüft der
Server read-only (`GET /books/{code}` → ISBN), ob das Buch zur
Anmelde-Buchliste des Schülers gehört (`check_scanned_book` in
`server/sessions.py`). ISBN-Set `expected_isbns` wird je Session
gehalten — **Modus B** auf `StudentSessionB` (befüllt beim
Pairing/Reconnect), **Modus A** auf `HelperSession` (befüllt beim Laden
des Schülers/Reconnect, geleert beim Schülerwechsel). Treffer → wie
bisher; „not_enrolled"/„unknown_book" → sofortiges `scan_result`,
**kein** Worker-Kontakt. Leeres Set (Buchliste noch nicht geladen) oder
API-Fehler blockieren nicht (der offizielle Frontend-Submit validiert
ohnehin). Reiner Read-Pfad, in Scanner- (`/ws/scanner`) und
Schüler-WS (`/ws/student`) verdrahtet.

Leihschein-Druck-Backends: `file`/`lp`/`sumatra`/`win-default`/`auto`
gebaut (`server/printing.py`), read-only PDF-Abruf via
`get_loan_slip_pdf`, Endpoint `POST /api/print-loan-slip`, Host- und
Scanner-Button verdrahtet.

## 2026-06-18 — Join-QR-Rotation entfernt, Hardening-Pass

Das Join-Secret wird jetzt **bei jedem Öffnen der Ausgabe** neu erzeugt
(`gen_join_secret()` in `/api/modus-b/open`) und bleibt **innerhalb**
einer Ausgabe konstant — der Schüler-QR ändert sich nicht mehr mitten in
der Ausgabe. `_rotate_join_secret` (Pro-Zuordnung-Rotation, eingeführt
2026-06-17) ist entfallen. Schutz liegt weiter auf `modus_b_open`-Gate +
Per-IP-Ratelimit + **manueller Host-Zuordnung** (Pairing). Trade-off: ein
Screenshot des QR bleibt gültig, solange dieselbe Ausgabe offen ist —
neue Joins erzeugen aber nur ungepairte pending-Sessions (verfallen per
TTL). Alte QRs aus einer früheren Ausgabe werden mit dem nächsten Öffnen
ungültig. „Ausgabe öffnen" zeigt den QR nicht automatisch. Auch der
QR-Anzeige-Text (`#qr-url`) zeigt die aktuelle Join-URL.

**Hardening-Pass aus Code-Review:** Worker-Context-Leak (Pool-
Erschöpfung), WS-Reconnect-Leak, Host-Login-TTL (`HOST_SESSION_TTL_S`),
QR-IP-Override (`HOST_IP`), Pairing-TOCTOU, `commit-book`-ok-nur-bei-
booked u. a. Write-Pfad-Gating unangetastet. Details:
`docs/hardening_2026-06-18.md`.

## 2026-06-17 — Modus A: Weiter-Button (O1), Statuszeile, Schuljahr-Auswahl

- **Weiter-Button (⏭):** Helfer tippt „Weiter" im Scanner → WS
  `{type:"next"}` → `sessions.advance_helper`: schließt den aktuellen
  Schüler ab (`end_student`, **kein** Browser-Submit) und vergibt den
  nächsten Pending aus der Queue. Host kann weiterhin via „Nächster
  Schüler" zuweisen. **Kein** Browser-Submit
  (`end_student`→`release_worker`→`page.close()`). Schüler verschwindet
  sofort, Statuszeile „Wird geladen…". Status-Push jetzt **vor**
  Worker-Aufbau (sofort sichtbar statt erst nach Reload); Modus-A-Laden
  zentral in `sessions.load_and_push_helper_student`. Scanner-Statuszeile
  auf Kamerafeld-Breite, flankiert von Drucker-Button (Platzhalter) +
  Weiter-Button; Status-Punkt entfernt.
- **Kartei per Schüler-ID-Route:** `#/counter/student/<id>` via
  `_goto_authed` statt Nachnamen-Typeahead — eindeutig pro Schüler, keine
  Namensgleichheit/Tippfehler (Commit `38c5094`). Debug: `.env`
  `HEADLESS=false` (sichtbarer Browser) + `SLOW_MO_MS` (verlangsamt jede
  Aktion) — nur auf Geräten mit Display (Commit `c77436c`).
- **Host-UI: Schuljahr auswählbar** (`GET /api/schoolyears` + `POST
  /api/select-schoolyear`, read-only). Default = laufendes Jahr, sonst
  das nächste (deterministisch aus `begin`/`end`, nicht blind
  `/schoolyears/current`); Wechsel resettet Queue/Klasse mit
  Active-Session-Guard. Schuljahr wird durch Klassen-/Schüler-/
  Karteiabrufe durchgereicht.
- **Host-Pairing-UI ohne Tippen (Modus B):** wartende Codes werden am
  Host **angezeigt** und per Klick zugeordnet (`web/host.html`, rein
  Frontend). Zwei Wege: *Code-zuerst* (Codes-Liste in der Modus-B-Karte
  mit Schüler-`<select>` + „Zuordnen") und *Schüler-zuerst*
  (Pairing-Button stellt Schüler scharf → Code-Chip klicken). Gemeinsame
  `doPair()` inkl. O6-Override. `prompt()` entfällt.
- **Pairing-Latenz-Fix:** `student_info` wird in
  `load_and_push_paired_student` **vor** dem Worker-Open ans Handy
  gepusht (Worker-`load_card` lief vorher davor und blockierte die
  Anzeige ~7 s). Sicher, weil `handle_scan` „Worker nicht bereit" sauber
  meldet.
- **iPad-Display am Host bedienbar:** Button „QR für iPad anzeigen"
  (`GET /api/display/qr` → QR auf `/qr-display`, host-auth) +
  Freischalt-Feld für den iPad-Registrierungscode (`POST
  /api/display/authorize`, erscheint nur bei verbundenem,
  unautorisiertem iPad). Bestehender Button → „QR für Schüler anzeigen";
  Karte „Live-Ausgabe (Modus B)" → „Schüler".
- **Queue-Steuerung erweitert:** pro Schüler „Trennen" (`/api/disconnect`
  → zurück auf „Wartend", trennt Helfer/Session), global „Alle
  Verbindungen … trennen" (`/api/disconnect-all`) und „Queue Status
  zurücksetzen" (`/api/reset-queue`, alle → pending). Beide global mit
  doppelter Bestätigung, dezenter Link-Stil. Alle bauen auf `end_student`.
- **Reiter „Test Config"** (`host.html`, inzwischen überholt — siehe
  2026-07-09 oben): Auswahl des Reiters fügte die festen Testschüler
  automatisch an die Queue an (`switchTab('test')` →
  `addTestStudents()`); Button als manueller Re-Trigger. IDs fest
  verdrahtet in `TEST_STUDENTS`: Niklas Müller (2159), Lukas Podleschny
  (2164), Lucas Stolpe (2167). Idempotent (Duplikate übersprungen).

## 2026-06-16 — Scanner-UI-Redesign + Buch-Daten-Anreicherung

Obere Leiste Zahnrad/Kamera-Streifen/Taschenlampe+Ton, volle
Statuszeile, großer Name mit Bezahlstatus rechtsbündig, scrollbare
Bücher-Tabelle. Bücher-Tabelle mit echten Daten: Spalten Fach | Titel |
Status-Icon; vorgemerkt (gelb/orange, ⏳) oben, ausgeliehen
(hellgrün/dunkelgrün, ✓) unten; Titel + Fach korrekt aus `client.series`
aufgelöst. Serien-Katalog-Cache (`IsServClient._get_series_map`,
read-only `GET /series`): erste Schülerauswahl lädt den Katalog einmalig;
Titel/Fach auch für bereits ausgeliehene Bücher (nur `code`+`isbn` im
Roh-Payload) gefüllt.

## 2026-06-15 — Kern Modus A: Server, Worker, Druck, Modus-B-Grundgerüst

Umfangreicher Ausbau an einem Tag (Details in
`docs/phase2_e2e_2026-06-15.md`, `docs/phase4_modus_b_2026-06-15.md`):

- FastAPI-Server: HTTPS (selbstsigniert), WebSocket-Hub,
  Session-/Rollenmodell. Host-UI: Login, Klasse wählen, alphabetische
  Queue, Live-Status Helfer-Sessions. Helfer-Scanner-UI: Token-basiert,
  Schüleranzeige (angemeldet/bezahlt/Bücher), Scan-Feedback.
- Playwright-Worker: Context-Pool (N unabhängige Logins), Schülerkartei
  laden, Barcode staged (kein Submit). Recovery (Re-Login bei
  Session-Ablauf, `automation/worker.py`, deterministisch getestet via
  `automation/recovery_test.py`).
- E2E-Smoke headless (read-only): voller Modus-A-Flow
  Host→Scanner→Worker→Kartei→staged (`automation/e2e_smoke.py`) → V3.
  2-Helfer-Paralleltest: zwei Schüler gleichzeitig aktiv, beide Karteien
  parallel, unabhängiges Staging (`automation/e2e_parallel.py`) → V5.
  Pool-Härtung: fehlgeschlagene Worker-Logins werden in `start()` einmal
  nachgezogen, geleakte Contexts geschlossen → V6.
- Buchender Submit-Pfad als Code vorhanden, **dreifach gated**:
  `commit_barcode()` (Enter+Result-Parse) + `handle_commit()` + Endpoint
  `POST /api/commit-book`. Gates: `ALLOW_BOOKING=false` (Default) +
  Host-Auth + `confirm:true`. Feuert ohne Freigabe **nie** gegen
  Produktion (verifiziert: bei Default wird der Worker nicht berührt) →
  V10. Enter/Selektoren unverifiziert bis zum freigegebenen Test.
- Leihschein-Druck — Code fertig: read-only PDF-Abruf + Druck-
  Abstraktion (`server/printing.py`, Endpoint `POST
  /api/print-loan-slip`, Host-Button).
- Modus B (Phase 4, initialer Aufbau, reiner Server-/Web-Code, keine
  Buchung): QR-Display-Rolle (iPad): Registrierung, vom Host gesteuerte
  Anzeige (`web/qr-display.html`, allgemeiner anonymer QR).
  Einmal-Token-System + Pairing-Flow (langer `session_token` +
  4-stelliger Code, Host-Bestätigung). Schüler-UI: reduziert und
  selbsterklärend (`web/student.html`: Bestellliste, Scan, Abschluss).
  Harter Zugriffsentzug (Token-Invalidierung + WS-Close + Worker zu);
  Skip-Funktion deckt Modus B mit ab. Sicherheits-Review
  Token-Lebenszyklus (initial, E2E-verifiziert); iPad-Härtung (iOS-Kiosk)
  bleibt organisatorisch. Rate-Limit `/api/student/join` (pro-IP, 5/10 s,
  `server/ratelimit.py`).

## 2026-06-12 — Projekt-Setup, Spike B, Stack-Entscheidungen

Repo umstrukturiert: Alt-Code raus, Python-Projektgerüst (`server/`,
`web/`, `automation/`, `docs/`, `pyproject.toml`). Scanner-Assets
übernommen (`html5-qrcode.min.js`, `beep.mp3`, Scan-Logik aus
`scanner.html` → `web/scan.html`/`web/scan.js`). `.env`-Handling +
`CLAUDE.md` mit Read-only-/Produktions-Schutzregeln (analog
`ausleihe-api`). Plandokument committet; README neu geschrieben.

Stack-Entscheidungen geklärt (Details in `docs/PLAN.md` §2): Backend
Python (FastAPI + websockets), Write-Pfad Playwright gegen die
offizielle UI, Frontend Vanilla HTML/JS, ein Ausleihe-Admin-Account
(Niklas) für API-Reads **und** Playwright-UI-Sessions.

**Spike B** (→ O2, parallele IServ-Sessions desselben Accounts):
3/3 parallele unabhängige Logins + 3/3 Cookie-Sharing-Contexts, keine
Invalidierung (`automation/spike_b_parallel.py`) → V2.
