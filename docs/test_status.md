# Test- & Verifizierungsstand

> **Lebendes Dokument.** Hält fest, was bereits getestet/verifiziert ist und was
> noch zu testen bleibt. **Konvention:** Jede neue Funktion bzw. jedes neue
> Risiko hier unter „Offen / zu testen" eintragen; nach erfolgreichem Test in
> „Verifiziert" verschieben (mit Datum + Skript/Befund). Bezug: `docs/PLAN.md`.
>
> Stand: 2026-08-06 (Schülerclient-Druckmodus: Statusanzeige und relative
> Queue-Position, 389 Tests grün).
> Alle bisherigen Tests sind **read-only** gegen IServ
> (kein Submit, keine Buchung — PLAN §6).
>
> **Chronologie:** Die ausführliche Beschreibung, *wie* eine Funktion entstand
> (Bugfixes, Nutzer-Korrekturen, Commit-Historie), steht in `docs/CHANGELOG.md`.
> Hier steht nur, *was* aktuell noch zu testen ist.

## Verifiziert (grün)

| # | Was | Wie / Skript | Datum | Befund |
|---|-----|--------------|-------|--------|
| V1 | Spike A — Counter-Seite headless bedienbar (Login, Schülersuche, Kartei) | `automation/spike_a_counter.py --explore` | 2026-06-12 | Selektoren stabil; Login ohne 2FA/Captcha; `docs/spikes/spike_a_protokoll.md` |
| V2 | Spike B — parallele Sessions desselben Accounts (O2) | `automation/spike_b_parallel.py` | 2026-06-12 | 3/3 Logins + 3/3 Cookie-Sharing, keine Invalidierung |
| V3 | Phase-2 E2E-Smoke Modus A (Host→Scanner→Worker→Kartei→staged) | `automation/e2e_smoke.py` | 2026-06-15 | bestanden; Bug in `scan.html` (Panel-Display) gefixt; `docs/phase2_e2e_2026-06-15.md` §1 |
| V4 | Worker-Recovery (Re-Login bei Session-Ablauf) | `automation/recovery_test.py` | 2026-06-15 | deterministisch via `clear_cookies()`, bestanden; `docs/phase2_e2e_2026-06-15.md` §2 |
| V5 | 2-Helfer-Paralleltest (zwei Schüler gleichzeitig, getrennte Karteien) | `automation/e2e_parallel.py` | 2026-06-15 | bestanden, keine Vermischung; `docs/phase2_e2e_2026-06-15.md` §3 |
| V6 | Pool-Härtung (fehlgeschlagene Logins werden nachgezogen, kein Context-Leak) | `WorkerPool.start()` | 2026-06-15 | im Paralleltest mitverifiziert; `docs/phase2_e2e_2026-06-15.md` §3 |
| V7 | Phase-4 E2E Modus B (Pairing-Flow + harte Token-Invalidierung) | `automation/e2e_modus_b.py` | 2026-06-15 | bestanden inkl. Reconnect mit totem Token (Close 4006); `docs/phase4_modus_b_2026-06-15.md` §5 |
| V8 | Druck-Backend-Logik `file`/`auto`-Resolution (ohne Drucker) | `server/printing.py` Smoke (py) | 2026-06-15 | auto→file auf Linux, PDF wird geschrieben (reiner Logik-Check, **kein** echter Druck) |
| V9 | Rate-Limit-Logik (sliding window 5/10 s, pro-IP) | `server/ratelimit.py` Smoke | 2026-06-15 | erste 5 erlaubt, 6. gedrosselt; andere IP unbetroffen |
| V10 | **Buchungs-Gate** — bei `ALLOW_BOOKING=false` wird der Worker (Enter) nie berührt | `handle_commit` Smoke (uv) | 2026-06-15 | Default→`blocked` ohne Worker-Zugriff; echter Config-Default `False`; Snapshot `False`. Beweist: kein Enter gegen Produktion |
| V11 | Härtung: `WorkerPool.stats()`, `worker_pool` im Snapshot, Limiter-`sweep()` | uv-Smoke | 2026-06-15 | stats total/available/in_use korrekt; sweep leert alte/leere Buckets; Snapshot enthält `worker_pool` |
| V12 | **Spike C / O4 — Silent-Print Windows** (echter Druck am Zielgerät) | `automation/test_printer.py "HP LaserJet Professional P1102"` | 2026-06-22 | rc=0, Seite ausgedruckt; SumatraPDF via winget nach `%LOCALAPPDATA%\SumatraPDF\`; `PRINTER_NAME=HP LaserJet Professional P1102` in `.env` setzen |
| V13 | **Stale-Guards Modus A/B** — wird `helper.student_id` bzw. die Modus-B-Session während des `open_student()`-Awaits verändert, schließt der neu geöffnete Worker-Context sich selbst statt registriert zu werden (kein Leak) | `tests/test_stale_guards.py` (4 Tests: je Modus A/B ein Stale- und ein Happy-Path-Fall) | 2026-07-09 | grün; Mutationsprobe bestanden (Guard-Zeile auskommentiert → Test rot) |
| V14 | **WS-Message-Dispatch Scanner** — `call` mit pending-Re-Check, Umbinden an fremde Klasse (`rebind_helper_to_context`), `search_call` (Lupe) für queue-losen Schüler + fehlendes `form`, Peek-Toggle (`queue`/`close`), Scan ohne zugewiesenen Schüler, unbekanntes Token, malformed JSON-Frame beendet die Schleife nicht | `tests/test_ws_scanner.py` (8 Tests, echter `websocket_connect` ohne Lifespan, Fake-IServ, `worker_pool=None`) | 2026-07-09 | grün |
| V15 | **Pairing-TOCTOU** — ändert sich zwischen Code-Auflösung und `get_student_info`-Await der Zustand (Session revoked / Schüler bereits vergeben / Code neu belegt), antwortet `/api/student/pair` mit 409 statt eine stale Zuordnung zu committen | `tests/test_api_guards.py::test_student_pair_toctou_*` (3 Tests) + `test_student_pair_happy_path_binds_when_nothing_changed` | 2026-07-09 | grün |
| V16 | **Kontext-Lifecycle** — doppeltes Öffnen derselben Klasse reaktiviert statt dupliziert; `close_class` löst gebundene Helfer; Aktiv-Kontext-Wechsel bei unbekannter ID → 404 | `tests/test_api_guards.py::test_open_class_creates_context_and_populates_queue`, `test_close_class_switches_active_context_when_active_one_closed`, `test_close_class_unknown_context_404`, `test_set_active_context_*` | 2026-07-09 | grün |
| V17 | **`_read_booking_result`** gegen Fake-Locator — `has_not`-Filter (Schutz gegen Selektor-Drift, s. u.), sichtbarer/unsichtbarer Error-Alert, `nothing_found`, Locator-Exception schluckt statt zu propagieren | `tests/test_booking_result.py` (6 Tests) | 2026-07-09 | grün; ohne den `has_not`-Filter wird `test_barcode_only_in_typeahead_input_does_not_count_as_booked` rot (selbst nachgefahren). **Der getestete False-Positive kann im heutigen DOM nicht auftreten** (Feld liegt in keiner `<tr>`, `inner_text()` liefert keine Input-Werte) — der Test sichert den Filter gegen Selektor-Drift, s. „Offen" unten |
| V18 | **Spectator-/Wartelisten-Mechanismus** — zweiter Helfer (Queue-`call`/Lupe-`search_call`) auf einen bereits aktiven Schüler wird Zuschauer (`student_info` mit `spectator: true`, kein `worker_ready`) statt Fehler/Doppel-Worker; Scan-Fan-out an Spectators; Beförderung des am längsten Wartenden bei `end_student` (echter Queue-Schüler UND transienter Lupe-Schüler); FIFO-Kette bei mehreren Wartenden; Disconnect-Aufräumung der Warteliste | `tests/test_ws_scanner.py` (4 neue Tests, echte Zwei-Client-`websocket_connect`) + `tests/test_queue_flow.py` (3 neue Tests, Beförderung low-level über `end_student`) | 2026-07-11 | grün, 199 → 206 Tests |
| V19 | **Spectator-Feinschliff** — Live-Refresh an Spectators bei Reload des aktiven Helfers (`broadcast_student_info_to_spectators`); Warteposition bleibt über einen Spectator-eigenen Reload erhalten (kein Aufräumen mehr im Disconnect-Handler, Reconnect-Zweig stellt die Ansicht ohne neuen Wartelisten-Eintrag wieder her); **kritischer Bugfix**: Selbst-Aufruf des aktiven Helfers auf seinen EIGENEN Schüler (Queue-`call`/Lupe) bei wartendem Spectator führte vorher zu zwei gleichzeitig aktiven Clients (End_student beförderte den Spectator, der Handler wies den Schüler danach trotzdem wieder dem Aufrufer zu) — behoben durch `refresh_active_student` (reiner Info-Refresh statt End_student+Reassign) | `tests/test_ws_scanner.py` (3 neue Tests: Selbst-Aufruf via `call`/`search_call` je mit wartendem Spectator, Reload-Fan-out) | 2026-07-11 | grün, 206 → 209 Tests |
| V20 | **Selbst-Aufruf zählt als neuer Zugriff** — `refresh_active_student` (V19) wieder entfernt: Selbst-Aufruf MIT Warteliste stellt den Aufrufer jetzt hinten an (der bisher Wartende übernimmt sofort, inkl. Worker) statt nur aufzufrischen; Selbst-Aufruf OHNE Warteliste fällt in den normalen `end_student`+`assign_student_to_helper`-Pfad (voller Reload, sendet `loading` → schließt clientseitig Menü/Such-Panel, was beim reinen Refresh vorher unterblieb) | `tests/test_ws_scanner.py` (2 umbenannte Tests: `..._demotes_caller_to_back_of_queue`, 1 neuer Test für den No-Queue-Reload-Pfad) | 2026-07-11 | grün, 209 → 210 Tests |
| V21 | **Aktive Schüler aufrufbar (Spectator auch bei Modus B)** — aktive Schüler in der Warteschlangen-Ansicht des Helferclients bekommen einen „Aufrufen"-Button und werden beim Klick Zuschauer (warten bis frei). Server: `_handle_call`/`_handle_search_call` machen den Aufrufer jetzt auch dann zum Spectator, wenn der aktive Schüler KEINEN Helfer-Owner hat (Modus-B-Pairing setzt `status='active'` ohne `assigned_helper`) — bisheriger Fehler „Schüler nicht (mehr) in der Warteschlange" bzw. Doppel-Aktiv-Konflikt bei der Lupe. `test_call_non_pending_student_errors` nutzt jetzt `skipped` (aktive → Spectator ist Soll-Zustand) | `tests/test_ws_scanner.py::test_call_helper_becomes_spectator_of_modus_b_active_student` (+ `test_call_non_pending_student_errors` umgestellt) | 2026-07-13 | grün, 222 Tests |
| V22 | **Search→Queue kein Doppel-Aktiv** — Lupe (`search_call`) auf einen Schüler, der zusätzlich als `pending` in einer Klassen-Queue steht, übernimmt jetzt den ECHTEN Queue-Eintrag (`active` + zugewiesen) statt einen transienten Doppelgänger zu bauen. Ohne Claim blieb der Queue-Eintrag `pending` → ein zweiter Helfer konnte ihn via `call` regulär übernehmen → derselbe Schüler zwei aktive Helfer/Worker. Mit Claim läuft der `call` in die Spectator-Warteliste („Warten bis Schüler frei…"). Invariante: nie zwei aktive Helfer auf demselben Schüler. Nebeneffekt: `end_student` löst den Helfer beim Abschluss sauber (vorher stale `student_id`/Worker-Leak). | `tests/test_ws_scanner.py::test_search_call_claims_existing_queue_entry_preventing_double_active` | 2026-07-13 | grün, 223 Tests |
| V23 | **Ausblenden wirkt sofort auf den Clients (Live-Repush)** — das Ausblenden einer Buchreihe im Einstellungen-Dialog schickt den aktiven Helfern (Modus A) UND gepaarten Schüler-Sessions (Modus B) desselben Jahrgangs eine `booklist_update`-Nachricht mit der neu gefilterten Liste + Reihenfolge; die Reihe fällt live aus der Geräteliste, ohne den Worker-Neuaufbau und ohne den clientseitigen Scan-Fortschritt zu löschen (`scannedIsbns`/`scanOrder` bleiben). Server: `repush_booklist` (`server/sessions.py`) holt die Schülerinfo frisch, rechnet die ISBN-Vorabmengen neu (damit `evaluate_scan_for_booking` den neuen Hidden-Stand sieht) und bewahrt die X/Y-Zählung für sichtbare Bücher; `/api/booklist-hidden` repusht per `asyncio.gather` parallel + `broadcast_host`. | `tests/test_booklist_repush.py` (4 Tests: Filter + Vormerk-Neuerung, Scan-Fortschritt erhalten für sichtbare Bücher, Modus-B ws=None-Skip, IServ-Fehler-resilient) | 2026-07-18 | grün, 238 Tests. **Live-Check am echten Gerät noch offen** (s. „Offen" unten) |
| V24 | **Interne Druckerwarteschlange (Logik)** — Rollen-gerechte Einfügung (HOST>HELFER>SCHÜLER, „hinter letzte Gleichrangige"), bereits gespoolte/druckende Aufträge bleiben am Kopf gepinnt (am OS verbindlich); 2-in-flight-Pipeline (B→`spooled` Pos 1, C→`queued` Pos 2, nach A fertig rückt B→`printing` Pos 0); Fehler-Fall wird `failed` mit `ok=False`; `print_pdf` liefert CUPS-`job_handle` („request id is X-123"), `file`-Backend `None`; `await_print_completion(None)` sofort fertig. | `tests/test_print_queue.py` (7 Tests) + `tests/test_printing.py` (3 neue: `job_handle` CUPS, `file` None, Completion-None) | 2026-07-19 | grün, 248 Tests. **Echte-Drucker-Verifizierung (OS-Polling, 2-in-flight am Live-System, Host-Popup nur am startenden Host) noch offen** (s. „Offen" unten) |
| V25 | **Drucker-Pool (Logik + Persistenz)** — Reiter-Verwaltung wie Klassen-Tabs; Round-Robin-Füllung (niedrigste Last, linkester Tie-Break) verteilt 4 Aufträge auf 2 Drucker als `[(p1,printing),(p2,printing),(p1,spooled),(p2,spooled)]`; leerer Pool lässt Aufträge in der zentralen Warteschlange (Scheduler dispatcht nichts); `pool_printers`/`pool_summary` liefern die Snapshot-Form (`is_default`/`load`/`printing_name`/`spooled_name`/`waiting`); Persistenz `data/printers.json` round-trip + Drop fehlender benannter Drucker (gegen Geräte-Liste, inkl. JSON-Bereinigung) + erster-Start-Default `[Standarddrucker]` + unbekannter Duplex-Modus → `one_sided`; Snapshot-Schema `printers`+`print_queue_summary` statt `printer_name` (mitgeführter Charakterisierungs-Test). | `tests/test_print_queue.py` (3 neue: Round-Robin-Füllung, leerer Pool, Snapshot-Form) + `tests/test_printer_store.py` (5: erster Start, Roundtrip, Drop fehlend, leere Geräteliste, unbekannter Duplex) + `tests/test_state_contract.py` (Schema mitgeführt) | 2026-07-19 | grün, 256 Tests. **Live-Mehrdrucker-Smoke (Verteilung am echten Gerät) noch offen** (s. „Offen" unten) |
| V26 | **Pro-Klasse Drucker-Allowlist + pool-gerechte Verteilung** — `ClassContext.allowed_printer_ids: set[str]\|None` (`None`=alle); `PrintJob.allowed_printers`-Snapshot reist mit in die zentrale Warteschlange. Scheduler `_claim_fills` **level-weise** (erst alle Last-0-Drucker, dann Last-1) statt „niedrigste Last": Auftrag erlaubt nur p2 → geht an p2 (p1 bleibt frei, obwohl idle + linkester); Kopf erlaubt p1+p2, beide idle → p1 (linkester); p1 Last 1 + p2 idle, Auftrag beide erlaubt → p2 (Parallelismus, nicht p1s 2. Slot); Kopf nur p2, p1 idle → p1 zieht nächst-erlaubten (späteren), Kopf geht an p2; `allowed=set()` → bleibt waiting (Scheduler-Grace zusätzlich zur Enqueue-Verweigerung). `allowed=None` ≡ alt → bestehende Round-Robin-Tests grün. Vorab-Verweigerung: explizite Allowlist ohne einzigen Pool-Drucker → 400 (HTTP) / `print_result{ok:false}` (WS). `POST /api/context-printers` setzt Allowlist nachträglich + `wake()` + Broadcast. | `tests/test_print_queue.py` (5 neue: nur-p2, linkester-beide-idle, Parallelismus-idle-bevor-2.-Slot, Kopf-überspringen-nicht-erlaubt, leere Menge bleibt waiting) | 2026-07-19 | grün, 261 Tests. **Live-Check (Allowlist-Verteilung + UI-Checkboxen am echten Gerät) noch offen** (s. „Offen" unten) |
| V27 | **Parallele Drucker-Verteilung + OS-echter Druckstatus + Positionen** — drei Live-Bugs behoben: (1) Worker blockierte seriell im Completion-Poll → nur ein Drucker wurde genutzt; neu läuft je gesendeter Auftrag ein eigener Tracker-Task (`_track_job`), der Worker dispatcht nicht-blockierend → mehrere Drucker drucken parallel (Test `test_parallel_dispatch_two_printers` beobachtet beide Drucker gleichzeitig Last 1). (2) „wird gedruckt" war rein logisch gesetzt; neu OS-getrieben via `printing.read_job_state` (Windows `Get-PrintJob.JobStatus`, CUPS `lpstat` „active"): `spooled`=gesendet/wartet, `printing`=OS druckt aktiv, `done`=OS-Job weg; logische Slot-Beförderung entfällt. (3) Positionen waren globaler Index; neu `_compute_positions` = Minimum über alle erlaubten Drucker, wie viele Aufträge dort noch vorliegen (0=druckt, 1=gesendet/wartet, 2=erster zentraler Wartender bei vollem Drucker). `print_queue.py` Rewrite auf Tracker-Architektur (`_Slots.jobs`, 3-Tupel-Claims), `printing.py` neu `read_job_state`; UI zeigt 0-basierte Position + „gesendet, wartet auf Druck". | `tests/test_print_queue.py` (Worker-Tests auf `read_job_state` umgestellt + 1 neuer Parallel-Test) + `tests/test_printing.py` (+3: `read_job_state` None/cups/win) | 2026-07-19 | grün, 266 Tests. **Live-Verifikation am echten Windows-Gerät (≥ 2 Drucker, nur nach Freigabe nach CLAUDE.md §6) noch offen** (s. „Offen" unten) |
| V28 | **Hängender Drucker: Inaktivitäts-Fehler + Peer-Benachrichtigung + Label-Korrektur** — (1) `_INACTIVITY_TIMEOUT_S` (30 s) im Tracker: OS-Status bleibt ohne Wechsel → `stalled` (`ok:false, stalled:true`, „Es dauert ungewöhnlich lange … - <Status>"); `printThenNext` advanced nur bei `ok:true` → Helfer rückt **nicht** automatisch vor. (2) Andere Aufträge am selben Drucker werden `peer_error` („Fehler bei vorigem Auftrag - <Position>"), Tracker cancelt; zentrale Jobs ohne Ersatzdrucker bekommen `peer_error` via `_notify_all` und bleiben stehen. (3) `_compute_positions(printers, faulty_ids=)` zählt den fehlerhaften Drucker für Ersatzdrucker-Jobs nicht mit; `_claim_fills` überspringt fehlerhafte Drucker. (4) `POST /api/printers/reactivate` + „Wieder aktivieren"-Button (Host-Einstellungen) nimmt die Marke zurück. (5) Label: „Wird gedruckt" nur bei `printing`; Pos.0+`spooled` → „gesendet, wartet auf Druck"; Pos.≥1 → „an X. Druckerwarteschlangenposition". | `tests/test_print_queue.py` (+5: Stall-Urheber, Peer, Ersatzdrucker-Position, No-Alternative, Reactivate) + `tests/test_state_contract.py` (`faulty:false` im Erststart) | 2026-07-19 | grün, 271 Tests. **Live-Verifikation (Inaktivität→Fehler, Peer-Anzeige, Reactivate-Button am echten Gerät) noch offen** (s. „Offen" unten) |
| V29 | **Stall-Aufträge im Slot belassen (Warteschlangen-Mitzählung)** — `_handle_stall` entfernt Urheber (`stalled`) + Peers (`peer_error`) **nicht mehr** aus dem Drucker-Slot; sie bleiben mit ihrer Slot-Position stehen und zählen für `load`/`_compute_positions` weiter mit → No-Alternative-Jobs (Allowlist nur auf den fehlerhaften Drucker) behalten ihre Warteposition hinter den Blockierten statt auf 0 einzustürzen. Peers werden im Peer-Durchlauf übersprungen (sonst würde der Urheber doppelt finalisiert). `reactivate()` nimmt nur die Marke zurück; der folgende `_reconcile`-Lauf (unter Lock, raced nicht gegen `_claim_fills`) räumt für nicht-fehlerhafte Drucker die blockierten Aufträge und gibt die Kapazität frei. Ersatzdrucker-Jobs behalten die reduzierte Position (fehl. Drucker zählt für `usable` nicht). `pool_printers` zählt blockierte Jobs in `load`, listet sie aber nicht als druckend/wartend; Host-Box zeigt fehlerhafte Drucker mit rotem Punkt + „⚠ fehlerhaft — N blockiert" (`.pq-dot.pq-fault`). `_notify_all` skippt blockierte Slot-Jobs. | `tests/test_print_queue.py` (+3: No-Alternative-mitzählen, `pool_printers` Load-vs-Anzeige, `_reconcile`-Räumung nach Reaktivierung; bestehende Stall-/Peer-Tests asserten zusätzlich die Slot-Belegung) | 2026-07-20 | grün, 276 Tests. **Live-Verifikation (Blockierten-Anzeige + Reaktivierungs-Räumung am echten Gerät) noch offen** (s. „Offen" unten) |
| V30 | **Stall-/Peer-Meldung: Positionsnummer stabil (keine +1-Hochzählung)** — Peer-Error-Meldung zeigte bisher „Fehler bei vorigem Auftrag - (Position+1). Warteschlangenposition" → der an Pos. 0 druckende/gesendete Job rutschte beim Fehler seines Hintermannes auf „1." hoch, alle folgenden eine Position hoch. Neu zeigen Urheber (`stalled`) und Peers (`peer_error`) einheitlich „Es dauert ungewöhnlich lange … - Drucker <Name>: <Label>" (vorher Peers „Fehler bei vorigem Auftrag …"); das Label ist das normale Warteschlangen-Label aus Position + Pre-Error-Status (`_stall_label`/`_stall_msg`): Pos.0+`printing`→„wird gedruckt", Pos.0+`spooled`→„gesendet, wartet auf Druck" (keine Nummer), Pos.≥1→„an X. Druckerwarteschlangenposition" (X=Position, **ohne +1**). Server liefert den Text im `msg`-Feld (`print_progress` für zentrale No-Alternative-Jobs via `_peer_pname`; Slot-Peers via `print_result`); `scan-ws.js`/`host-render.js` zeigen für `peer_error` nur noch `msg.msg` statt die Position selbst +1 hochzuzählen. | `tests/test_print_queue.py` (+3: `_stall_label` ohne +1, `_stall_msg` mit Label, Pos.-0-Peer zeigt Pos.-0-Label statt „1."; bestehende Peer-Assertion umgestellt) | 2026-07-20 | grün, 279 Tests. **Live-Verifikation (Peer-Meldung + stabile Positionsnummer am echten Gerät) noch offen** (s. „Offen" unten) |
| V31 | **Fehlermeldung positionsbasiert (nur Pos. 0 bekommt Inaktivitäts-Hinweis)** — Aufbauend auf V30: Die Fehlermeldung am hängenden Drucker ist nicht mehr nach Urheber/Peer getrennt, sondern **positionsbasiert** (neu `_error_msg`, verwendet in `_handle_stall` + `_notify_all`). Pos. 0 (der vorn am Drucker druckende/gesendete Auftrag) bekommt den Inaktivitäts-Hinweis „Es dauert ungewöhnlich lange … - Drucker <Name>: <Label>"; **alle** Pos. ≥ 1 (die dahinter wartenden Aufträge, egal ob Urheber oder Peer) bekommen die gewohnte Peer-Formulierung „Fehler bei vorigem Druckauftrag - X. Warteschlangenposition" zurück (X = Position, ohne +1). Ersatzdrucker-Aufträge bekommen weiterhin keine Fehlermeldung, solange ein erlaubter Drucker nicht fehlerhaft ist (`_is_peer_error`/Slot-Markierung); nur wenn *alle* erlaubten Drucker fehlerhaft sind, greift die positionsbasierte Regel. | `tests/test_print_queue.py` (+1: `_error_msg` positionsbasiert; `test_stall_peer_at_same_printer_gets_peer_error` + `test_stall_peer_at_position_zero_shows_pos_zero_label` auf Pos.≥1-Formulierung umgestellt) | 2026-07-20 | grün, 280 Tests. **Live-Verifikation (Pos.-0-Hinweis vs. Pos.≥1-Peer-Formulierung am echten Gerät) noch offen** (s. „Offen" unten) |
| V32 | **Statusanzeige ergänzt „von Drucker <Name>" nach Versand** — Sobald ein Auftrag an einen Drucker gesendet wurde (`assigned_printer_id` gesetzt), zeigt die Statusanzeige des Auftraggebers den Druckername. `print_progress` trägt das `printer`-Feld bereits (aus `_printer_name`); `scan-ws.js` hängt für nicht-`peer_error`-Status „ von Drucker <Name>" an den Statustext an („Wird gedruckt … von Drucker P1", „… gesendet, wartet auf Druck … von Drucker P1", „… an X. Druckerwarteschlangenposition von Drucker P1"); der Host-Toast `printToastText` (`host-render.js`) hängt analog „ — Drucker <Name>" an („… wird gedruckt — Drucker P1", „… gedruckt — Drucker P1"). Zentrale Warteschlangen-Jobs ohne zugewiesenen Drucker (`printer` null) bekommen keinen Zusatz; `peer_error`/`stalled` übernehmen den Server-Text (Pos.0 nennt den Drucker bereits). `print_result` bekommt neu ein `printer`-Feld; Helfer zeigt bei Erfolg „Gedruckt von Drucker <Name>", Host-Toast „… gedruckt — Drucker <Name>" statt nur „gedruckt". | `tests/test_print_queue.py` (+1: `print_progress` + `print_result` tragen `printer` für gesendete Aufträge) | 2026-07-20 | grün, 281 Tests. **Live-Verifikation (Druckername in Helfer- + Host-Statusanzeige am echten Gerät) noch offen** (s. „Offen" unten) |
| V33 | **Audit-Härtung:** `/api/commit-book` nutzt die vollständige Vorabprüfung + per-Schüler-Lock; Queue-/Schuljahr-Reset gibt Worker vollständig frei; Modus-B-Reopen widerruft Pending-Sessions; ersetzte WS verlieren Befehlsrecht; Host-WS schließen bei TTL/Logout; Tokens bleiben aus Logs; App-State/Hub sind je FastAPI-App isoliert; Config/Pfade validiert und CWD-unabhängig. | Neue/erweiterte Tests in `test_booking_precheck.py`, `test_queue_flow.py`, `test_hub.py`, `test_ws_scanner.py`, `test_app_runtime.py`, `test_config.py`, `test_main.py`; `uv run python -W error -m pytest -q`; `uvx ruff check server tests conftest.py` | 2026-08-01 | **294 Tests grün**, 68 % Coverage, Ruff clean, keine Warnungen; ausschließlich offline, kein IServ-Write/Playwright-Enter. |
| V34 | **Drucker-Anzeigename + vereinheitlichte „Name (Systemname)"-Anzeige** — `PrinterConfig.label` (rein kosmetisch, `name` bleibt Identität); Persistenz in `data/printers.json` (Roundtrip + Blank→`None`); `POST /api/printers/add` nimmt `label`, neu `POST /api/printers/label` setzt/löscht ihn. `pool_printers` liefert `label`; Host `printerLabel(p)` zeigt überall `<Label> (<Systemname>)` (Reiter/Panel/Warteschlange/Checkboxen; Standarddrucker ohne verschachtelte Klammern). Warteschlangen-Warteliste (`_printer_display`) + Druck-Toast/Scanner nutzen das Format via neuem WS-Feld `printer_label` in `print_progress`/`print_result` (`printer`-Systemname unverändert). Einstellungs-Latenz: Mutationen rendern aus Endpoint-Antwort statt `lpstat`-Re-Fetch; Pool wird aus WS-Snapshot live nachgeführt (Fokus-Schutz). Static-File-Cache-Busting via `Cache-Control: no-cache, must-revalidate`. | `tests/test_printer_store.py` (+1: `test_load_strips_blank_label`; Roundtrip-Test um `label` erweitert); `tests/test_state_contract.py` unverändert | 2026-08-02 | **295 Tests grün**, Ruff clean, ausschließlich offline. **Live-Check (Anzeigename im Echtbetrieb, Persistenz über Neustart, Label in Warteschlange/Toast/Scanner am echten Gerät) noch offen** (s. „Offen" unten) |
| V35 | **Druck-Status-Texte vereinheitlicht + Countdown „Nächster Schüler"** — Helfer-Client (`scan-ws.js`): Drucker inline als `<Druckername> (<Systemname>)` (`msg.printer_label`), kein „ von Drucker …"-Anhang mehr. `printing`→„… wird von <pname> gedruckt…", Pos.0→„… wartet an <pname> auf Druck…", Pos.1+Drucker→„… an 1. Druckerwarteschlangenposition von <pname>", Pos.1 ohne Drucker (zentraler Wartender)→Kurzform, Pos.≥2→„… an X. Druckerwarteschlangenposition" (kurz), `gedruckt`→„… von <pname> gedruckt." (mit Punkt). Bei „Drucken & nächster Schüler" nach erfolgreichem Druck 4-Sekunden-Countdown „… Nächster Schüler in Xs." (tickt 4→3→2→1, 0 nie sichtbar, dann `advanceToNext`) statt sofortigem Weiterrücken; Dauer entspricht der Host-Toast-Dauer (4000 ms). Generationenzähler schützt vor Doppel-Advance (`cancelNextCountdown` am Anfang von `advanceToNext`). Host-Client (`host-render.js`, `printToastText`): gleiche Texte, aber Präfix „Leihschein von <Name>, <Vorname> (<Klasse>)" (`msg.name`), kein „ — Drucker …"-Suffix, Abschlusspunkt bei „gedruckt."; kein Countdown (Host ohne Auto-Advance). peer_error/stalled/Fehler unverändert. **Keine Server-Änderung** — `printer_label`/`name`/`position`/`status` waren bereits im WS-Payload; `test_state_contract.py` unberührt. | `node --check web/scan-ws.js web/scan-render.js web/host-render.js`; `uvx ruff check`; `uv run pytest -q` | 2026-08-02 | **295 Tests grün**, Ruff clean, `node --check` OK. **Live-Check (alle Status-Phasen + Countdown + Doppel-Advance-Schutz am echten Gerät) noch offen** (s. „Offen" unten) |
| V36 | **Drucker-Display (`/drucker-display`) — Pairing + Drucker-Zuweisung** — Neue anzeigbare Warteschlange für einen Bildschirm neben den Druckern, aufrufbar via `https://<ip>:<port>/drucker-display` (Clean URL, kein Login). Vor Pairing zeigt das Display **nur eine 4-stellige Registrierungs-Nummer** (keine Schülerdaten); erst nach Host-Zuordnung + Drucker-Zuweisung erscheint die Queue (mit Schülernamen, wie im Host). Pairing-Flow wie iPad-Display (`/ws/display`): neue Rolle `PrinterDisplaySession` (`server/state.py`) + unauthentifiziertes WS `/ws/drucker-display` (`server/routes/ws.py`). Push bei Druck-Übergängen (`print_queue._notify_all`→`broadcast_printer_displays`) und Pool-Mutationen (`_after_pool_change`), kein Polling. Pro Display legt der Host fest, welche Pool-Drucker dieses Display sieht (`assigned_printer_ids`: `None`=alle, explizite Menge=Teilmenge); mehrere Displays mit je verschiedenen Druckern + mehrere Drucker auf einem Display. Warteliste serverseitig gefiltert (`print_queue.display_view`): ein Eintrag ist relevant, wenn seine Allowlist die Display-Zuweisung schneidet. Endpunkte `/api/drucker-display/{qr,authorize,assign,forget}` (Host-auth, `server/routes/drucker_display.py`). Host-UI: Button „QR für Druckerdisplay" + Displays-Liste (Code-Eingabe + Zuweisungs-Checkboxes) in der bestehenden Druckerwarteschlange-Karte. `state_snapshot()`-Key `printer_displays`; `print_queue.waiting_list` liefert zusätzlich `allowed_printer_ids`. | `tests/test_drucker_display.py` (QR/Auth-Guard, Authorize/Assign/Forget, Snapshot-Shape, `send_printer_display_update`-Payloads); `tests/test_print_queue.py` (+`display_view`-Filterung + `allowed_printer_ids`); `tests/test_state_contract.py` (Schema um `printer_displays`); WS-Smoke via `TestClient.websocket_connect` (Registration→Queue) | 2026-08-03 | **316 Tests grün**, Ruff clean. **Live-Check (Pairing am echten Display-Bildschirm + Drucker-Zuweisung + Live-Status-Übergänge im WLAN) noch offen** (s. „Offen" unten) |
| V37 | **Drucker-Display: Reiter-Aufräumen beim Trennen** — Im `finally`-Block von `ws_drucker_display` (`server/routes/ws.py`) werden **nicht autorisierte** Sessions beim WS-Trennen ganz entfernt (Host-Reiter verschwindet), **autorisierte** bleiben stehen (`ws=None` → grauer Punkt). Vereinheitlicht zwei Fälle: (1) Display wird vor dem Einschalten geschlossen, (2) Display wird durch erneuten `/drucker-display`-Aufruf abgelöst (frischer Token → neue Session, alte WS trennt). Takeover-Schutz: nur lösen/entfernen, wenn `d.ws is websocket` (kein zwischenzeitlicher Reconnect). Reload eines autorisierten Displays verbindet denselben Token wieder (grün); Reload eines unautorisierten legt eine neue Session mit frischem Code an. **Aktive Abmeldung beim Entladen (Follow-up):** beim Navigieren auf einen neuen Token erreichte der Close-Frame den Server unzuverlässig → alte Seite blieb am Host fälschlich grün (bis zum uvicorn-Ping 20+20 s). Fix wie beim Tab-Schließen: der Client benachrichtigt den Server aktiv via `navigator.sendBeacon` auf den neuen unauthentifizierten Endpunkt `POST /api/drucker-display/departed` (`pagehide`/`beforeunload`) — sendBeacon kommt auch beim Navigieren/Redirect zuverlässig an; der Endpunkt schließt die WS und räumt auf (autorisiert → grau, nicht autorisiert → entfernt), sofort. Zusätzlich schließt der Client die WS sauber mit Code 1001; `unloading`-Flag unterdrückt den Auto-Reconnect. Ein zuvor probierter Heartbeat-Ansatz (10 s-Ping + 20 s-Timeout) wurde wieder entfernt (erkennte erst nach 20 s); für echten Verbindungsabbruch ohne Entladen bleibt der uvicorn-Ping als Fallback. Greift nur pro geschlossener Display-Seite — mehrere Displays auf demselben Gerät bleiben möglich. | `tests/test_drucker_display.py` (+6: 2 WS-Disconnect-Tests + 4 `/departed`-Endpunkt-Tests: unautorisiert → entfernt; autorisiert → grau; ungültig/unbekannt → No-op; idempotent) | 2026-08-04 | **331 Tests grün**, Ruff clean, `node --check` OK. **Live-Check (Reiter verschwindet/grau SOFORT beim Navigieren auf neuen Token am echten Gerät; grauer Punkt + Wieder-Verbinden via Reload/QR) noch offen** |
| V38 | **Leere Drucker-Auswahl = kein Drucker + Kopplung Drucker ↔ Live-Ausgabe pro Klasse** — (1) `_resolve_allowed_printers` (server/routes/classes.py) unterscheidet jetzt `None` (Feld fehlt = alle Pool-Drucker, Default) von `[]` (explizit leer = bewusst kein Drucker, leere Menge). Bisher mappte die Funktion beides auf `None` (alle) — alle Checkboxen abwählen lieferte still alle Drucker als Vorauswahl. `null` bleibt „alle", `[]` wird im Snapshot zu `[]` (nicht mehr `null`). (2) **Kopplung:** Live-Ausgabe (Modus B) pro Klasse setzt ≥1 Drucker voraus; bei aktiver Live-Ausgabe darf der letzte Drucker nicht abgewählt werden (erst Live-Ausgabe schließen). `None` (alle) gilt als „Drucker gewählt". Server (Helfer `_require_printer_for_live`): `open_class` `printers:[]`+`live_ausgabe:true` → 400 „Es ist mindestens ein Drucker auszuwählen" (beide Pfade neu+reused); `set_context_printers` leere Menge + Live an → 400 „Zuerst Live-Ausgabe schließen"; `set_context_live_ausgabe` Aktivieren ohne Drucker → 400, Ausschalten immer erlaubt. Client (Klassen-Tab + panel-new): roter Hinweis (`var(--danger-text)`) „Es ist mindestens ein Drucker auszuwählen" **erst beim Versuch**, den Schalter ohne Drucker zu aktivieren (Schalter revertiert); „Zuerst Live-Ausgabe schließen" beim Versuch, bei aktiver Live-Ausgabe den letzten Drucker abzuwählen (Checkbox revertiert); beide Hinweise verschwinden, sobald wieder ein Drucker gewählt ist. panel-new persistiert die Auswahl jetzt bei jeder Änderung (vorher nur beim Öffnen), damit ein `applyState`-Re-Render sie nicht verwirft. | `tests/test_api_guards.py` (+`test_context_live_ausgabe_requires_printer`, `+test_context_printers_last_not_removable_while_live_on`; `test_open_class_empty_printers_means_none_not_all` um `live_ausgabe`-Fälle ergänzt) | 2026-08-04 | **334 Tests grün**, Ruff clean, `node --check` OK. **Live-Check (Kopplung + rote Hinweise + Revert am echten Gerät, Klassen-Tab + panel-new) noch offen** (s. „Offen" unten) |

| V39 | **Klassen-Lehreransicht `/teacher`** — QR-Minten/-Ersetzen einer noch unautorisierten Session pro Klassen-Tab, Blockieren bei bereits autorisierter (409, Host muss erst trennen), Host-Autorisieren per Registrierungscode, explizites Trennen; token-authentifizierte `pending <-> skipped`-Statuswechsel strikt auf die eigene Klasse beschränkt (Schüler einer anderen Klasse → 404); `AppState.teacher_snapshot` liefert nachweislich nur Klassenname/Summen/Name/Status/Buch-Fortschritt/Druckstatus (kein `paid`/`amount_open`/`assigned_helper`, keine andere Klasse); Host-Snapshot-Kachel je Klassen-Tab leakt nie den Token; `/ws/teacher` zeigt vor Autorisierung nur den Code, danach `teacher_state`, unbekannter/entwerteter Token → sofortiges `forbidden`+Close (kein Reconnect möglich), gültige autorisierte Session reconnectet weiter; `Hub.broadcast_host` pusht Lehrer-Updates bei jeder Zustandsänderung mit; `close_class`/`select_schoolyear` entwerten gebundene Sessions hart. | `tests/test_teacher.py` (38 Tests: HTTP-Endpunkte + `AppState.teacher_snapshot` + `websocket_connect`-WS-Flow + 2 direkte `Hub`/`sessions`-Einheitstests) | 2026-08-04 | **373 Tests grün**, Ruff clean, `node --check web/teacher.js web/host-render.js` OK. **Live-Check (QR-Scan + Pairing + Live-Updates am echten Lehrkraft-Handy im Schul-WLAN) noch offen** (s. „Offen" unten) |
| V40 | **Lehreransicht-Nachbesserung: Wisch-Geste + Leihschein-Eingang** — „Als abwesend" läuft jetzt über eine Wisch-Geste nach rechts (Pointer Events, Touch + Maus) statt eines Buttons, Ziehschwelle dient als Bestätigung (kein Modal mehr für diese Aktion). Neues Flag `QueueStudent.slip_collected` (analog `slip_printed`, in `reset_progress()` zurückgesetzt); neuer token-authentifizierter Endpunkt `POST /api/teacher/slip-collected` verlangt `slip_printed=True` (409 sonst) und ist klassenscharf isoliert (andere Klasse → 404); `AppState.teacher_snapshot` liefert das Flag je Schüler + `slip_collected_count` als Summe. Host sieht die Statistik read-only (eigener Endpunkt setzt sie nicht) über `state_snapshot()`'s bereits vorhandenes `as_dict()`. **Folgekorrektur (selbe Session):** `teacher_qr` broadcastete den Snapshot nach dem Minten bisher nicht (Code-Anzeige im Klassen-Tab blieb bis zu einer zufälligen anderen Aktion veraltet) — behoben; „Abbrechen"-Button jetzt `secondary` statt `ghost warn` (gleiche Größe wie „Bestätigen"); Wisch-Schwelle relativ zur Zeilenbreite (55 %/90 % statt fixer 90px, Label vollständig lesbar) plus Chevron-Hinweis + Hinweistext gegen Zufallsauffindbarkeit. | `tests/test_teacher.py` (+5 neue: Auth-Gate, „kein Leihschein gedruckt"-Gate, Setzen/Zurücknehmen, Klassen-Isolation, `teacher_snapshot`-Zähler; +1 Regression-Assertion für den QR-Broadcast) + `tests/test_queue_progress.py` (`slip_collected` in `reset_progress()`-Assertion) | 2026-08-04 | **378 Tests grün**, Ruff clean, `node --check web/teacher.js web/host-render.js` OK. **Live-Check (Wisch-Geste auf echtem Touchscreen, Checkbox-Sync Lehrkraft↔Host) noch offen** (s. „Offen" unten) |
| V41 | **Klasseneinstellungen: drei Nachbesserungen** — (1) Drucker-Default im panel-new überlebt jetzt einen Server-Neustart: Persistenz über den stabilen `printer.name` (`printerStableKey()`) statt der laufzeit-instabilen `id` (`printer_store.py` vergibt `id`s bei jedem Start neu — dokumentiertes Verhalten, brach aber die `localStorage`-Auswahl). (2) `ClassContext.done_signed`/`done_collected` (In-Memory, Muster wie `live_ausgabe`/`slip_trigger`) — Klasseneinstellungen-Checkboxen „Leihschein unterschreiben"/„…wird vom Lehrer eingesammelt" waren bisher reine UI ohne Serverkontakt; jetzt in `state_snapshot()`, gesetzt via `/api/open-class` (beide Zweige) und neuem `POST /api/context-done-options`; `done_collected` wird serverseitig auf `False` normalisiert, wenn `done_signed=False`. Weiterhin ohne Auswirkung auf den Fertig-Übergang selbst (folgt später). (3) iPad-Freischalten vereinheitlicht mit Drucker-Display/Lehrkraft/Modus-B-Schüler: `POST /api/display/authorize` nimmt jetzt `display_id` (Klick auf einen Host-Listeneintrag) statt getipptem `registration_code`; `modus_b_snapshot()` liefert den Code je Display nur noch zum visuellen Abgleich. | `tests/test_api_guards.py` (+4: `test_open_class_seeds_done_options`, `test_context_done_options_collected_requires_signed`, `test_display_authorize_by_id_not_code`) | 2026-08-04 | **381 Tests grün**, Ruff clean, `node --check` auf allen `web/*.js` OK. **Live-Check (Drucker-Persistenz über echten Neustart, iPad-Freischalt-Klick am echten Gerät) noch offen** (s. „Offen" unten) |

| V42 | **Klassen-Lehreransicht nachgebessert:** Wisch-Geste für „Als abwesend" gespiegelt auf **nach links** (Pointer-Drag, rote Fläche rechts, Chevrons/Hinweis/Animation angepasst). `done_collected` wird im `teacher_snapshot` geführt; die Teacher-UI zeigt „abgegeben" und die Leihschein-Checkboxen nur bei aktivierter Klassenoption (und gedrucktem Schein). `POST /api/teacher/slip-collected` erzwingt zusätzlich `done_collected=true` und `status=done`; `teacher_snapshot` zählt nur abgeschlossene Eingänge bei aktivierter Option. Host-Zeile und grünes Badge folgen derselben Option. | `tests/test_teacher.py` (+2: Klassenoption-Gate, Counter bei deaktivierter Option; bestehende Counter-/Hub-/Host-Assertions erweitert) + `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` | 2026-08-05 | **383 Tests grün**, Ruff clean, JavaScript-Syntaxprüfung grün. **Live-Check (Wisch-Geste nach links auf echtem Touchscreen, Checkbox/Counter-Sync Lehrkraft↔Host) noch offen** |
| V43 | **Lehrer-QR-Modal schließt nach Scan:** `showTeacherQr()` setzt einen kontextgebundenen `qrWatch`; `maybeCloseQrOnScan()` schließt das Modal, sobald `state.contexts[context_id].teacher.connected` wahr ist. Der Lehrer-WebSocket broadcastet den Host-Snapshot jetzt beim Verbinden und Trennen — analog zum QR-Display — sodass der Watcher den Scan-Zustand tatsächlich erhält. | `tests/test_teacher.py` (bestehende WS-Prüfung um Connect-/Disconnect-Snapshot erweitert) + `node --check web/host-render.js web/host-ws.js` | 2026-08-05 | **383 Tests grün**, Syntaxprüfung grün. **Live-Check (QR auf echtem Lehrkraft-Handy scannen → Host-Modal schließt vor der Autorisierung) noch offen** |
| V44 | **Teacher-Statuszeilen responsive zweizeilig:** Jede Schülerzeile trennt Kopfzeile (Name/Punkt/Chevron) von Status-/Aktionszeile; Statuslabels werden mit `white-space: nowrap` und Wortschutz gerendert, Zusatzaktionen bleiben bei schmalen Breiten erreichbar. | `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` + lokaler Chromium-Check bei 390px/1024px mit Links-Wisch | 2026-08-05 | **385 Tests grün**, Ruff und Syntaxchecks grün. **Live-Check am echten Lehrkraft-Handy/Touchscreen weiterhin offen** |
| V45 | **Teacher-Statuszähler mobil als 2×2-Raster:** Die vier Statuszähler brechen bei ≤520px in zwei Spalten um; bei fünf Zählern wird derselbe responsive Grid-Pfad verwendet. Labels bleiben vollständig und ungeteilt. | `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` + lokaler Chromium-Check bei 390px/1024px | 2026-08-05 | **385 Tests grün**, Ruff und Syntaxchecks grün. **Live-Check am echten Lehrkraft-Handy weiterhin offen** |
| V46 | **Leihschein-Zähler präzisiert und verbreitert:** Der fünfte Zähler heißt „Leihschein abgegeben“, ist auf Desktop doppelt so breit wie die übrigen Kacheln und spannt mobil beide Rasterspalten. | `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` + lokaler Chromium-Check bei 390px/1024px | 2026-08-05 | **385 Tests grün**, Ruff und Syntaxchecks grün. **Live-Check am echten Lehrkraft-Handy weiterhin offen** |
| V47 | **Schülerclient-Druckmodus behält Name und Klasse:** Die Identitätszeile wird zusätzlich in `#view-print` gerendert und bei `student_info` gemeinsam mit der aktiven Ansicht aktualisiert; Druckfehler und Betreuerauslöser bleiben dadurch direkt einem Schüler zuordenbar. | `uv run pytest` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` | 2026-08-06 | **387 Tests grün**, Ruff und Syntaxchecks grün; echter Touchscreen-/Browser-Livecheck noch offen |
| V48 | **Schülerclient-Druckstatus + relative Position:** `web/student.js` zeigt dieselben Druckfortschritts-/Ergebnistexte wie der Helferclient; `_notify_all` sendet für Schüleraufträge in der zentralen Queue eine Position, die nur vorherige Schüleraufträge berücksichtigt. Bereits belegte Druckerslots bleiben physisch verbindlich. | `tests/test_print_queue.py` (+2 Positions-/WS-Tests) + `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` | 2026-08-06 | **389 Tests grün**, Ruff und Syntaxchecks grün; echter Touchscreen-/Drucker-Livecheck noch offen |
| V49 | **Schülerclient-Abschluss nach Druckende:** `print_loan_slip_for()` setzt den Leihschein nicht mehr beim Dispatch als gedruckt; die PrintQueue schließt die Modus-B-Session erst nach abgeschlossenem OS-Druckauftrag. | `tests/test_printing.py` + `tests/test_print_queue.py` (Dispatch-vs.-Druckende und Session-Abschluss) + `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` | 2026-08-06 | **391 Tests grün**, Ruff und Syntaxchecks grün; echter Touchscreen-/Drucker-Livecheck noch offen |

| V50 | **Schülerclient blendet Drucker-Systemnamen aus:** Nur ein konfigurierter Anzeigename wird angezeigt; technische Namen werden auch in Stall-/Fehlermeldungen nicht ausgegeben. | `node --check web/*.js` | 2026-08-06 | JavaScript-Syntax grün; echter Geräte-/Browser-Livecheck noch offen |
| V51 | **Schülerclient gibt den Worker beim Druckmodus frei:** `print_mode` löst die Playwright-Kartei idempotent aus der Schülerzuordnung, ohne die Modus-B-Session für Druckstatus und Druckabschluss zu schließen; `print_request` bietet einen Reconnect-Fallback. Der Selbstauslöser blendet den Button beim Absenden aus und verweist bei Fehlern an den Betreuer. | `tests/test_ws_scanner.py::test_student_print_mode_releases_worker_but_keeps_session` + `uv run pytest -q` + `uvx ruff check server/ automation/ tests/` + `node --check web/*.js` | 2026-08-06 | **391 Tests grün**, Ruff und Syntaxchecks grün; echter Touchscreen-/Drucker-Livecheck noch offen |

| V52 | Schülerclient-Leihscheinmodus: Bei aktivierter Klassenoption „Leihschein unterschreiben“ bleibt die Modus-B-Session nach beendetem Druck offen und zeigt einen eigenen Leihscheinmodus. Der Schüler soll unterschreiben und den Schein bei einem Betreuer abgeben; bei aktivem „Leihschein wird vom Lehrer eingesammelt“ wird „beim Lehrer“ angezeigt. Session-Zustand und Ziel überleben Reconnects; der Host schließt nach der physischen Übergabe über „Abschließen“. Ohne Option bleibt der automatische Abschluss unverändert. | tests/test_print_queue.py (beide Ziele + offener Zustand) + tests/test_ws_scanner.py::test_student_slip_mode_survives_reconnect + uv run pytest -q + uvx ruff check server/ automation/ tests/ + node --check web/*.js | 2026-08-06 | **394 Tests grün**, Ruff clean, JavaScript-Syntaxprüfung grün; echter Touchscreen-/Drucker-Livecheck weiterhin offen |

## Offen / zu testen

### Offen 2026-08-05 (Klassen-Lehreransicht `/teacher`: Live-Check)

Pairing-Flow, Klassen-Isolation, Snapshot-Privacy und die erlaubten/verbotenen
Statusübergänge sind per Unit-/HTTP-/WS-Suite abgesichert (V39); reiner
In-Memory-Runtime-State, kein IServ-Write. Am echten Lehrkraft-Handy im
Schul-WLAN noch offen:

- [ ] Host klickt „QR für Lehrkraft anzeigen" im Klassen-Tab → QR öffnet
      `/teacher?token=...` auf dem Handy und zeigt **nur** den
      Registrierungscode (keine Klassen-/Schülerdaten sichtbar, auch nicht
      kurz beim Laden).
- [ ] Host bestätigt im selben Klassen-Tab → Lehrkraft-Ansicht öffnet sich
      automatisch (ohne Reload) mit Klassenname + Summen + Statusliste.
- [ ] Live-Updates ohne Reload: Pairing eines Schülers, Scan-Fortschritt
      (X/Y), Leihschein-Druckstart/-Ende, Abschluss — alle Übergänge kommen
      am Lehrkraft-Handy live an.
- [ ] Lehrkraft wischt einen wartenden Schüler nach links („Als abwesend
      markieren") → Status „Übersprungen" — am Host zeitgleich sichtbar;
      Rücknahme („Nicht abwesend", mit Bestätigungsdialog) funktioniert ebenso.
- [ ] Host klickt „Lehrkraft trennen" (autorisierte Session) → Handy zeigt
      sofort „Zugang beendet", kein Reload stellt den Zugang wieder her.
- [ ] Klassen-Tab schließen (×) bei verbundener Lehrkraft-Session → dieselbe
      sofortige Entwertung.
- [ ] Zwei parallel offene Klassen mit je eigener Lehrkraft-Session: kein
      Vermischen der Daten, kein QR der einen Klasse öffnet die andere.
- [ ] **Wisch-Geste (V42):** auf einem echten Touchscreen einen wartenden
      Schüler nach links wischen → rote „Als abwesend markieren"-Fläche wird
      beim Ziehen sichtbar, unterhalb der relativen Schwelle (55 % der Zeilenbreite)
      schnellt die Zeile zurück, oberhalb löst sie aus (Status „Übersprungen").
      Kurzes Antippen/Verrutschen darf nichts auslösen.
- [ ] **Leihschein-Checkbox (V42):** bei aktivierter Klassenoption bei einem
      abgeschlossenen Schüler auf dem Lehrkraft-Gerät „Leihschein entgegengenommen"
      ankreuzen → die fünfte
      Kachel „abgegeben" zählt hoch; am Host erscheint zeitgleich das grüne
      „Leihschein ✓"-Badge in der Queue-Tabelle und die Zeile „Leihschein
      entgegengenommen: X / Y" in der Lehrkraft-Ansicht-Karte. Häkchen wieder
      entfernen → beide Seiten fallen zurück.

### Offen 2026-08-04 (Drucker ↔ Live-Ausgabe-Kopplung + leere Auswahl: Live-Check)

Die serverseitige Kopplung + leere-Auswahl-Semantik ist per Unit-Suite
abgesichert (V38); berührt keine IServ-/Buchungs-Logik (rein In-Memory-State +
UI). Am echten Gerät (privater Laptop, nach CLAUDE.md §6) noch offen:

- [ ] **Leere Auswahl = kein Drucker:** Klasse öffnen, alle Drucker-Checkboxen
  abwählen → Klasse zeigt „Kein Drucker für diese Klasse ausgewählt"
  (nichts angehakt, nicht still „alle"); Helfer-Druckdialog bekommt keine
  Vorauswahl, Leihschein-Druck per manueller Auswahl im Druckdialog möglich.
- [ ] **Kopplung — Live an ohne Drucker:** bei abgewählten Druckern Live-Ausgabe-
  Schalter betätigen → Schalter springt zurück auf aus + roter Hinweis
  „Es ist mindestens ein Drucker auszuwählen" (nicht dauerhaft, nur beim
  Versuch).
- [ ] **Kopplung — letzten Drucker abwählen:** bei aktiver Live-Ausgabe letzte
  Drucker-Checkbox abwählen → Checkbox springt zurück + roter Hinweis
  „Zuerst Live-Ausgabe schließen". Live-Ausgabe ausschalten → letzter Drucker
  jetzt abwählbar.
- [ ] Beide Hinweise verschwinden, sobald wieder ein Drucker gewählt ist.
- [ ] **Beide Stellen:** Klassen-Tab (Klasseneinstellungen-Detail) **und**
  panel-new („Neue Klasse") verhalten sich gleich; Auswahl in panel-new
  über `applyState`-Re-Render hinweg erhalten.

### Offen 2026-08-04 (Klasseneinstellungen-Nachbesserungen: Live-Check)

Persistenz-/Endpunkt-Logik ist per Unit-Suite abgesichert (V41); rein
In-Memory-State bzw. `localStorage`. Am echten Gerät noch offen:

- [ ] **Drucker-Default nach Neustart:** panel-new-Auswahl treffen (alle
  Drucker angehakt lassen), Server neu starten (neue Laufzeit-`id`s), panel-new
  erneut öffnen → weiterhin alle Drucker angehakt (vorher: keiner).
- [ ] **Fertig-Optionen:** „Leihschein unterschreiben"/„…wird vom Lehrer
  eingesammelt" im Klassen-Tab ankreuzen, Seite neu laden (bzw. anderer
  Host-Rechner) → Haken bleibt erhalten (Snapshot-getrieben); „eingesammelt"
  fällt automatisch zurück, sobald „unterschreiben" abgewählt wird.
- [ ] **iPad-Freischalten ohne Tippen:** iPad scannt den Modus-B-QR → Host
  sieht im Kasten „Live-Ausgabe (Modus B)" einen Listeneintrag mit Code +
  „Freischalten"-Button (kein Textfeld mehr); Klick schaltet das iPad frei,
  Eintrag verschwindet aus der Liste.

### Offen 2026-08-03 (Drucker-Display: Live-Check im Schul-WLAN)

Pairing-Flow, Zuweisung, Snapshot-Schnittstelle und WS-Push sind offline per
Unit-Suite + `websocket_connect`-Smoke abgesichert (V36); berühren keine
IServ-/Buchungs-Logik (rein Anzeige). Am echten Display-Bildschirm neben den
Druckern noch offen:

- `/drucker-display` im Schul-WLAN öffnen → zeigt 4-stelligen Pairing-Code.
- Host: „QR für Druckerdisplay" zeigt QR auf die Seite; Code eingeben → Display
  erscheint in der Liste; Druckerkapazitäten per Checkbox zuordnen.
- Display zeigt zugewiesene Drucker (groß, Status-Dot) + zentrale Warteschlange
  (mit Schülernamen); folgt Druck-Übergängen live (druckt→wartet→fertig).
- Mehrere Displays mit je verschiedenen Druckern + ein Display mit mehreren
  Druckern; Drucker umbenennen/entfernen → Display passt sich an.
- Keine Schülerdaten vor dem Pairing (nur Nummer) —Privacy-Check am öffentlichen
  Bildschirm.
- **Reiter-Aufräumen beim Trennen (V37):** unautorisiertes Display per Tab zu
  schließen → Reiter verschwindet am Host; autorisiertes Display schließen →
  Reiter bleibt als grauer Punkt, per QR/Reload mit demselben Token wieder
  verbinden → grün. Erneut `/drucker-display` (ohne Token) am selben Gerät →
  neues Display mit frischem Code; das alte autorisierte wird **sofort** grau
  (Client benachrichtigt den Server via `sendBeacon` auf
  `/api/drucker-display/departed` beim Entladen), nicht mehr fälschlich grün
  und nicht erst nach 20 s. Zweites Display in einem weiteren Tab auf demselben
  Gerät bleibt davon unberührt (mehrere pro Gerät möglich).

### Offen 2026-08-02 (Druck-Status-Texte + Countdown „Nächster Schüler": Live-Check)

Die neuen Helfer-/Host-Status-Texte und der Countdown sind per `node --check`
+ Unit-Suite offline abgesichert (V35); sie berühren nur Web-Dateien (keine
Server-Logik). Am echten Gerät (privater Laptop, nach CLAUDE.md §6) noch offen:

- [ ] **Helfer-Status pro Phase:** Druck starten (oder `save_pdf_locally`-
      Dev-Toggle) und nacheinander prüfen: Pos.≥2 → „Leihschein an X.
      Druckerwarteschlangenposition" (ohne Drucker); Pos.1 mit zugewiesenem
      Drucker → „… an 1. Druckerwarteschlangenposition von <Name> (<System>)";
      Pos.0/spooled → „… wartet an <Name> (<System>) auf Druck…";
      `printing` → „… wird von <Name> (<System>) gedruckt…";
      fertig → „… von <Name> (<System>) gedruckt." (mit Punkt).
- [ ] **Pos.1 ohne Drucker:** zentraler Wartender an Pos.1 (kein Drucker
      zugewiesen) zeigt die Kurzform ohne Drucker — prüfen, falls im Setup
      reproduzierbar.
- [ ] **Countdown:** „Drucken & nächster Schüler" → nach erfolgreichem Druck
      erscheint „… gedruckt. Nächster Schüler in 4s." und tickt 4→3→2→1; bei 0
      wird der nächste Schüler aufgerufen (0 nie sichtbar).
- [ ] **Doppel-Advance-Schutz:** während des Countdowns manuell „Nächster
      Schüler" klicken → es rückt nur einmal vor (laufender Timer wird
      gecancelt), kein zweiter Sprung nach Ablauf.
- [ ] **Host-Toast:** Druck-Toast zeigt „Leihschein von <Name>, <Vorname>
      (<Klasse>) …"-Präfix (z. B. „… von Müller, Niklas (10a) von <Name>
      (<System>) gedruckt."), kein „ — Drucker …"-Suffix, Abschlusspunkt.

### Offen 2026-08-02 (Drucker-Anzeigename + vereinheitlichte Anzeige: Live-Check)

`PrinterConfig.label`, die „Name (Systemname)"-Anzeige in Warteschlange/
Toast/Scanner sowie die Einstellungs-Latenz-Optimierung sind per Unit-Test
offline abgesichert (V34). Am echten Gerät (privater Windows-Laptop, nach
CLAUDE.md §6) noch offen:

- [ ] **Name vergeben/bearbeiten:** im Einstellungs-Panel pro Drucker unter
      „Name" einen Anzeigenamen tippen + speichern → Reiter/Panel/Warteschlange
      und Klassen-Drucker-Checkboxen zeigen „Name (Systemname)"; leer lassen
      → fällt auf reinen Systemnamen zurück.
- [ ] **Standarddrucker mit Name:** zeigt „Name (Standarddrucker)" (keine
      verschachtelten Klammern); Panel-Zeile „System:" führt den Gerätename.
- [ ] **Persistenz:** Server neustarten → Anzeigenamen bleiben erhalten
      (`data/printers.json` enthält `label`).
- [ ] **Warteliste + Druck-Toast:** wartender Auftrag zeigt erlaubte Drucker
      als „Name (Systemname)"; Druck-Toast/Scanner „Drucker Name (Systemname)".
- [ ] **Einstellungs-Latenz:** Drucker hinzufügen/entfernen → Reiter im
      Einstellungs-Dialog taucht im selben Moment auf wie in der Warteschlange
      (keine spürbare Verzögerung mehr).
- [ ] **Cache-Busting:** nach Server-Update einmal Hard-Refresh; danach werden
      JS/CSS-Änderungen ohne erneuten Hard-Refresh übernommen.

### Offen 2026-08-01 (Schul-WLAN-Smoke nach Audit-Härtung)

Das Netzwerkmodell des Hauptprojekts wurde nicht absichtlich geändert: Server
weiter auf `0.0.0.0`, Handy-Verbindung weiterhin HTTPS/WSS mit selbstsigniertem
Zertifikat und einmaliger Browser-Warnung. Automatisierte Tests können jedoch
weder Schul-WLAN-Routing/Firewall noch die Zertifikatsannahme echter Handys
beweisen. Vor dem nächsten Einsatz mit `ALLOW_BOOKING=false` prüfen:

- [ ] Host auf dem echten Ausleihe-Laptop starten; QR zeigt die im Schul-WLAN
      erreichbare IPv4 statt VPN-/Docker-Adresse.
- [ ] Helfer-Handy per QR verbinden, Zertifikatswarnung akzeptieren, staged Scan
      durchführen — **kein** Enter/keine Buchung.
- [ ] Reload/Reconnect und zweites Helfer-Handy prüfen.
- [ ] Modus B öffnen, Join + Pairing + Reconnect prüfen; Modus B erneut öffnen
      und bestätigen, dass ein altes noch ungepaartes Pairing ungültig ist.
- [ ] Host-Logout prüfen: bereits offener Host-Tab verliert seine WebSocket-
      Verbindung und empfängt keine weiteren Statusdaten.

### Offen 2026-07-19 (Parallele Verteilung + OS-Status + Positionen: Live-Check)

Die Tracker-Architektur, das OS-getriebene `read_job_state` und die min-über-
erlaubte-Drucker-Positionen sind per Unit-Test logisch abgesichert (parallele
2-Drucker-Verteilung, OS-Übergänge spooled→printing→done, Position 0/1/2,
Draht-Format unverändert). Das Live-Verhalten am echten Windows-Gerät
(≥ 2 Pool-Drucker) ist offen — nur nach Freigabe nach CLAUDE.md §6 mit
ausgemusterten Büchern + schriftlichem Rückbau-Plan.

- [ ] **Parallele Verteilung:** 2 (oder mehr) Drucker konfigurieren, mehrere
      Leihschein-Drucke schnell hintereinander anstoßen → beide Drucker werden
      **gleichzeitig** belegt (Druckerwarteschlangen-Box zeigt beide Last 1),
      nicht nacheinander (war der „nur ein Drucker"-Bug).
- [ ] **OS-Status-Übergänge:** Statusfolge je Auftrag `gesendet, wartet auf
      Druck` (spooled) → `wird gedruckt` (OS druckt aktiv, `Get-PrintJob`
      JobStatus=Printing) → `gedruckt` (OS-Job weg). „wird gedruckt" erscheint
      erst, wenn das OS wirklich druckt — nicht schon beim Senden.
- [ ] **Positionen:** zentrale Warteschlange zeigt `#` = 0-basierte Position
      (2 = erster zentraler Wartender bei vollem Drucker); Helfer-Scanner
      „an X. Druckerwarteschlangenposition" für Pos ≥2, „gesendet, wartet auf
      Druck" für Pos 1.
- [ ] **Kapazität 2 + Nachrücken:** ein Drucker hat 2 gesendete Aufträge
      (druckt + wartet); nach Fertigstellung rückt der Wartende auf „wird
      gedruckt" und der nächste erlaubte zentrale Auftrag wird gesendet.

### Offen 2026-07-19 (Pro-Klasse Drucker-Allowlist: Live-Check)

Die pro-Klasse Drucker-Allowlist (V26) ist per Unit-Test logisch abgesichert
(level-weise Verteilung respektiert Allowlist + Parallelismus + linkester-
Tie-Break, `allowed=None` ≡ Altverhalten, leere Menge bleibt waiting). Das
Live-Verhalten am echten Gerät (Verteilung gemäß Allowlist, UI-Checkboxen im
Klassen-Öffnen + im Klassen-Reiter, nachträgliche Änderung + Broadcast) ist
offen. Auf dem Dev-VPS (`file`-Backend) meldet `list_printers` keine Geräte →
nur der Standarddrucker ist wählbar; echte Allowlist-Verteilung braucht einen
CUPS/Sumatra-Rechner mit ≥ 2 konfigurierten Pool-Druckern.

- [ ] **UI `panel-new`:** beim Öffnen einer Klasse zeigt der Drucker-Block
      alle Pool-Drucker als Checkboxen (Default alle angehakt = alle);
      Auswahl wird für das nächste Öffnen gemerkt (`localStorage`).
- [ ] **UI Klassen-Tab:** „Drucker für {Klasse}"-Karte zeigt die Checkboxen
      initialisiert aus dem Snapshot; Änderung feuert `POST /api/context-
      printers`, wird sofort auf anderen Host-Tabs sichtbar (`applyState`).
- [ ] **Kein Switch / nicht ausgegraut:** Checkboxen sind native Checkboxen
      (keine iOS-Toggles), Labels voll opacity (globale `label{opacity:.6}`
      übersteuert).
- [ ] **Verteilung gemäß Allowlist** (nur nach Freigabe nach CLAUDE.md §6,
      mit ausgemusterten Büchern, nicht unbeaufsichtigt): Klasse mit nur
      Drucker 2 erlaubt → Leihschein druckt auf Drucker 2, Drucker 1 bleibt
      frei; Klasse mit beiden erlaubt + 2 schnelle Drucke → verteilt auf
      beide (Last 1/1, nicht 2/0).
- [ ] **Nachträgliche Änderung:** Allowlist im laufenden Klassen-Reiter
      ändern → `wake()` verteilt künftige Drucke neu (wartende Aufträge
      behalten ihre alte Allowlist, nur künftige Drucke sehen die neue).

### Offen 2026-07-19 (Drucker-Pool: Live-Mehrdrucker-Smoke)

Der Drucker-Pool (V25) ist per Unit-Test logisch + persistenz-seitig abgesichert
(Round-Robin-Füllung, leerer Pool, Snapshot-Form, JSON-Roundtrip + Drop fehlender
Drucker). Das Live-Verhalten am echten Gerät (Verteilung auf mehrere Drucker,
Reiter-UI, Drag-Reorder, Persistenz über Neustart) ist offen. Auf dem Dev-VPS
(`file`-Backend) meldet `list_printers` keine Geräte → nur der Standarddrucker
ist wählbar; echte Mehrdrucker-Verteilung braucht einen CUPS/Sumatra-Rechner.

- [ ] **Reiter-UI (Einstellungen):** Drucker-Reiter wie Klassen-Tabs;
      Standarddrucker zu Anfang vorhanden, entfernbar; „+" fügt Geräte-Drucker
      hinzu; Drag umsortieren; Duplex-Dropdown pro Drucker (nur speichern).
- [ ] **Leerer Pool:** Standarddrucker entfernen → Pool leer → Druck-Versuch
      wird mit „Kein Drucker konfiguriert" abgewiesen (Host-Endpoint 400 bzw.
      Scanner-WS `print_result{ok:false}`); `data/printers.json` spiegelt `[]`.
- [ ] **Persistenz über Neustart:** gespeicherter Stand wird geladen; ein
      nicht mehr existierender Drucker wird verworfen + aus JSON gelöscht;
      leerer Pool bleibt leer; erster Start (keine JSON) → `[Standarddrucker]`.
- [ ] **Verteilung auf N Drucker** (nur nach Freigabe nach CLAUDE.md §6, mit
      ausgemusterten Büchern, nicht unbeaufsichtigt): mehrere Leihscheine
      hintereinander drucken → Round-Robin-Füllung (erst alle auf Last 1, dann
      Last 2), Rollen-Rangfolge in der zentralen Warteschlange, 2-in-flight
      pro Drucker (1 druckend + 1 gespoolt).

### Offen 2026-07-19 (Druckerwarteschlange: Verifizierung am echten Drucker)

Die interne Druckerwarteschlange (V24) ist per Unit-Test logisch abgesichert
(Rollen-Einfügung, 2-in-flight-Pipeline, Positionen, Fehler-Fall) — aber das
OS-Completion-Polling und das Live-Verhalten am echten Drucker sind offen. Auf
dem Dev-VPS (`file`-Backend) gibt es keinen physischen Druck, daher ist dort
nur die Pipeline-Logik testbar („gedruckt" sofort).

- [ ] **CUPS `lp`-Pfad (macOS/Linux):** `lpstat`-Polling erkennt das physische
      Druckende (Job-ID verschwindet) → Helfer-Status wechselt auf „Gedruckt",
      „Drucken & nächster Schüler" lädt erst dann den nächsten.
- [ ] **Windows `sumatra`-Pfad:** `Get-PrintJob` per DocumentName-Match erkennt
      das Druckende (SumatraPDF setzt Job-Name = Dateiname; `mkstemp`-Prefix
      `leihschein_<student_id>_` macht ihn eindeutig).
- [ ] **2-in-flight am Live-Drucker:** zweiter Auftrag wird vorspooled, während
      der erste druckt — Positions-Anzeige 0/1/2 wie spezifiziert.
- [ ] **Host-Popup nur am startenden Host:** von zwei eingeloggt-verbundenen
      Host-Rechnern (unterschiedliche `sid`) sieht nur der, der auf das
      Drucker-Icon geklickt hat, das Popup (Position / „wird gedruckt" /
      „gedruckt"); der andere nicht.
- [ ] **Rangfolgen-Schlupf** (dokumentierter Trade-off): HOST-Klick, während ein
      HELFER-Auftrag bereits `spooled` (Pos 1) ist → HOST rückt auf Pos 2
      (hinter den gespoolten HELFER), nicht vor ihn.
- [ ] `win-default`-Backend: kein Polling → „gespoolt = gedruckt" (sofort
      „Gedruckt" nach `os.startfile`), dokumentierter Fallback.
- [ ] Timeout-Fallback (90 s): bleibt ein OS-Job hängen, wird er trotzdem als
      „gedruckt" gewertet (Queue blockiert nicht).

### Offen 2026-07-18 (Ausblenden-Live-Repush: Verifizierung am echten Gerät)

Der Live-Repush beim Ausblenden (`booklist_update`, V23) ist per Unit-Test
abgesichert, aber am echten Helfer-Gerät gegen einen laufenden Server noch
nicht verifiziert — insbesondere die clientseitige Wahrnehmung (Scan-Fortschritt
bleibt, ausgeblendetes Buch fällt sofort raus, wieder eingeblendetes taucht auf).

- [ ] Modus A: zugewiesener Schüler geladen → im Einstellungen-Dialog eine
      Reihe ausblenden + speichern → fällt sofort aus der Helfer-Liste, ohne
      dass der Scan-Fortschritt schon gescannter Bücher zurückgesetzt wird.
- [ ] Modus A: ausgeblendete Reihe wieder einblenden → taucht sofort wieder
      mit ihrem IServ-Status (vorgemerkt/ausgeliehen) auf.
- [ ] Modus A: ein in dieser Session gescanntes Buch wird ausgeblendet → fällt
      aus der Liste, X/Y-Zählung auf dem Host passt (Buch zählt weder in X noch Y).
- [ ] Modus B (Pilot, falls aktiv): gepaarter Schüler, Ausblenden wirkt sofort
      auf dem Schüler-Handy.
- [ ] Grenzfall: Helfer ohne zugewiesenen Schüler bekommt keine `booklist_update`
      (kein Crash, keine Aktion).
- [ ] IServ vorübergehend nicht erreichbar beim Speichern → Save-Endpoint
      crasht nicht, Helfer behält vorübergehend die alte Liste.

### Offen 2026-07-18 (Host-Helferliste: Lupe-Schüler als aktuell + Klasse in Klammern)

Per Lupe (`search_call`) zugewiesene Schüler erscheinen jetzt in der Host-
Helferliste als aktueller Schüler (vorher „–" bei transienten Lupe-Schülern,
die in keiner Queue stehen), mit Klasse in Klammern — aber NUR bei Lupe-
Zuweisung. Server-seitig: `HelperSession.student_lastname`/`student_firstname`/
`student_via_search` (+ `student_form` im Snapshot), `SpectatorWaiter.via_search`,
`assign_student_to_helper(..., via_search=)`. Unit-Test-Suite grün (234), aber
die Host-Anzeige ist nicht test-abgedeckt — Live-Check ausständig.

- [ ] Lupe: transienter Schüler (nicht in einer Queue) erscheint in der
      Host-Helferliste mit Namen + „(Klasse)" — nicht mehr „–".
- [ ] Lupe: Schüler, der bereits in einer Queue steht (Claim-Pfad), erscheint
      ebenfalls mit „(Klasse)".
- [ ] Queue-Aufruf (`call`/„Nächster"): Schüler erscheint OHNE Klammer-Klasse.
- [ ] „Klasse "-Präfix im `form`-Wert wird gestrichen → „(10a)", nicht
      „(Klasse 10a)".
- [ ] Spectator-Beförderung: nach Wartezeit übernommener Lupe-Schüler zeigt
      beim übernehmenden Helfer weiterhin „(Klasse)" (Vererbung `via_search`).
- [ ] Nach Trennen/Wechsel des Schülers verschwindet die Klammer (Reset in
      `_detach_helper`).

### Offen 2026-07-11 (Spectator-Modus: Verifizierung am echten Zwei-Client-Setup)

Der Spectator-/Wartelisten-Mechanismus (V18–V20) ist per Unit-/WS-Test
abgesichert, aber noch **nicht am echten Gerät mit zwei Handys/Browsern**
gegen einen laufenden Server verifiziert worden. Ein erster Live-Test schlug
fehl, weil der getestete Server-Prozess aus einem separaten, nicht
aktualisierten Checkout lief (kein Code-Bug, reines Deployment-Gotcha —
s. `docs/CHANGELOG.md` 2026-07-11 und Wiki-`lessons_learned.md`). Ein
zweiter Live-Test deckte danach den in V19 behobenen Selbst-Aufruf-Bug auf
sowie zwei Feinheiten (Menü schließt nicht bei Selbst-Aufruf; Selbst-Aufruf
sollte wie ein neuer Zugriff zählen), die V20 behebt.

- [ ] Nach Neustart des richtigen (aktualisierten) Prozesses: zwei
      Helfer-Clients denselben Schüler öffnen lassen (einmal aus der Queue,
      einmal per Lupe) — zweiter Client muss „Warten bis Schüler frei…"
      zeigen, Bücherliste read-only, kein „Scanner bereit".
- [ ] Scan am aktiven Client → Bücherliste des wartenden Clients zieht live
      mit (ohne dessen Statuszeile zu überschreiben).
- [ ] Aktiver Client lädt seine Seite neu → Bücherliste des wartenden
      Clients aktualisiert sich ebenfalls (V19).
- [ ] Wartender Client lädt seine Seite neu → bleibt weiterhin Zuschauer an
      derselben Wartelisten-Position, kein Zurücksetzen (V19).
- [ ] Aktiver Client ruft seinen EIGENEN Schüler erneut auf (Queue-`call`
      oder Lupe), während ein anderer Client wartet → der Aufrufer wird
      selbst Zuschauer (hinten in der Warteliste), der bisher Wartende
      übernimmt sofort mit Worker — KEIN zweiter aktiver Client (V20).
- [ ] Aktiver Client ruft seinen EIGENEN Schüler erneut auf OHNE
      Warteliste → Menü/Such-Panel schließt sich, voller Reload (V20).
- [ ] Aktiver Client schließt/wechselt den Schüler → wartender Client wird
      automatisch befördert (Worker lädt, „Scanner bereit").
- [ ] Bei drei Wartenden: FIFO-Reihenfolge der Beförderung am echten Gerät
      bestätigen.

### Offen 2026-07-13 (Aktive Schüler aufrufbar — Modus-B-Pfad live)

V21 erweitert den Spectator-Mechanismus auf Schüler, die per **Schülerclient
(Modus B)** aktiv sind (kein Helfer-Owner). Per WS-Test abgesichert, aber am
echten Gerät noch nicht gefahren:

- [ ] Schüler per Modus B pairen (aktiv am Schülerclient), im Helferclient-
      Menü den aktiven Schüler „Aufrufen" → Status „Warten bis Schüler frei…",
      Bücherliste read-only, kein „Scanner bereit".
- [ ] Schülerclient schließt selbst ab (`finish`) → wartender Helfer wird
      automatisch befördert (eigen Worker, „Scanner bereit"), kein
      Doppel-Aktiv-Konflikt.
- [ ] Lupe auf denselben Modus-B-aktiven Schüler → ebenfalls Zuschauer (kein
      transienter Doppel-Aktiver, kein Fehler).
- [ ] Aktiver Schüler ohne Owner stammt nicht nur von Modus B, sondern ggf.
      von einem abgerissenen Helfer (Stale) — Verhalten dort ebenfalls
      plausibel (Spectator wird beim nächsten `end_student`/Reset befördert).

### Offen 2026-07-10 (Host: Sofort-fertig-Filter beim Klassen-Öffnen)

`OpenClassRequest.auto_done` + `_load_student_flags()` (`server/routes/classes.py`)
setzen Schüler beim Laden einer neuen Klasse sofort auf `done`, wenn eine
gewählte Bedingung zutrifft (nicht angemeldet / nicht bezahlt / Ermäßigungs-
bzw. Befreiungsantrag ohne Nachweis). Nur ad-hoc mit einem temporären
Testfall verifiziert (Fake-IServ-Stub analog `_FakeIServForClasses`, danach
wieder entfernt) — **kein dauerhafter Unit-Test ergänzt**, Suite bleibt bei
187 Tests.

- [ ] Dauerhaften Test in `tests/test_api_guards.py` ergänzen:
      `_FakeIServForClasses` um eine `get_student_info`-Methode erweitern,
      die je nach `student_id` `enrolled`/`paid`/`remission_pending`/
      `exemption_pending` variiert, dann `open-class` mit `auto_done`
      aufrufen und die resultierenden Queue-Status prüfen.
- [ ] Am echten Gerät/Klasse verifizieren, dass die Filter-Checkbox-Auswahl
      korrekt aus `localStorage` vorbelegt wird und die IServ-Parallelabfrage
      (`asyncio.gather` über alle Klassenmitglieder) bei einer echten,
      größeren Klasse (~30 Schüler) performant bleibt.

### Offen 2026-07-09 (Worker: Robustheit der Erfolgs-Erkennung in `_read_booking_result`)

**Vorgeschichte, geklärt:** Ein früherer Eintrag hier vermutete einen
False-Positive — der `has_not`-Filter würde nichts filtern, falls das
Typeahead-Feld ein Geschwister statt ein Nachfahre der Bücherzeilen sei, und
eine fehlgeschlagene Buchung könnte als `booked` durchgehen. Die Auswertung des
DOM-Dumps `automation/out/06b_kartei_geladen.html` beantwortet das:

- `input.tt-input` liegt in einem `<form>` **oberhalb** der Tabellen
  (`form > div.form-group > div.input-group > span.twitter-typeahead`), keine
  der 16 `<tr>` im Dokument enthält ein `<input>`. Der Filter ist also ein No-op.
- Der False-Positive kann trotzdem **nicht** eintreten: der Erfolgs-Check liest
  `inner_text()` einer Zeile, und der *Wert* eines `<input>` ist kein Textknoten.
  Der Filter stammt aus einer früheren Implementierung, die `get_by_text(barcode)`
  über die ganze Seite laufen ließ. Er bleibt als Schutz gegen Selektor-Drift.
- Die Bücherzeilen sind `<tr ng-repeat="book in bl.books">`; der Buchcode steht
  als siebenstellige, nullgepolsterte Zahl in einer eigenen `<td>`-Spalte
  (z. B. `0017798`). Die Selektoren `.books-list`, `.lent-books` und
  `.student-books` kamen im DOM nicht vor und wurden entfernt.

**Was offen bleibt** — beides zeigt in Richtung `unknown`, nie in Richtung
`booked` (und `/api/commit-book` wertet nur `booked` als Erfolg), ist also
sicherheitsseitig unkritisch, aber unbequem im Betrieb:

- [ ] **Substring statt Spalten-Vergleich:** `barcode in row_text` prüft gegen
      den *gesamten* Zeilentext (Titel + Code + Zeitstempel) statt gegen die
      Code-Spalte. Bei durchgängig siebenstelligen Codes praktisch kollisionsfrei,
      aber unpräzise. Ein Wechsel auf einen exakten Spalten-Vergleich ist ein
      Eingriff im scharfen Buchungspfad → **nur mit Freigabe Niklas + Lukas und
      an einem ausgemusterten Buch** (PLAN §6).
- [ ] **Festes Zeitfenster:** `commit_barcode` wartet pauschal
      `wait_for_timeout(1500)` vor dem Auslesen. Rendert Angular die neue Zeile
      langsamer, meldet die Methode `unknown` obwohl gebucht wurde. Beim ersten
      scharfen Lauf beobachten, ob 1500 ms reichen; sonst auf ein
      `wait_for`-Prädikat auf die neue Zeile umstellen.
- [ ] **Erfolgs-/Fehler-Selektoren unverifiziert** (Code-TODO
      `automation/worker.py::commit_barcode`, ~Z. 205, verweist auf
      `_read_booking_result`): die Buchungs-Erfolgs- und Fehler-Selektoren sind
      nach Spike-A-Doku nur best-effort und bis zum ersten freigegebenen
      Realtest **nicht am scharfen DOM bestätigt**. Sicherheitsseitig unkritisch
      (nur ein eindeutiges `booked` im DOM gilt als Erfolg; bei Unsicherheit
      `unknown`, das `/api/commit-book` nicht als Buchung wertet), aber im
      Produktions-Schreibpfad → jede Änderung/Verifikation **nur mit Freigabe
      Niklas + Lukas an einem ausgemusterten Buch** (PLAN §6). TODO im Code
      bewusst stehengelassen, bis der Realtest die Selektoren bestätigt.

### Offen 2026-07-09 (Scanner: Reconnect stellt auch Lupe-Schüler wieder her + schneller Worker-Reload)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09).

- [x] **Unit-Suite**: `uv run pytest` **149 grün** (`tests/test_scanner_reconnect.py`,
      `tests/test_queue_flow.py`).
- [ ] **Am Gerät** (manuell, read-only, erst nach Freigabe — PLAN §6):
      (1) Helfer lädt Schüler (call/next) → Seite neu laden → Schüler sofort
      wieder da, Worker reloaded **direkt** auf die Schüler-Route (Log/Browser:
      `#/counter` → `#/counter/student/<id>`, **kein** Root-Load). (2) Gleiches
      mit **Lupe-Schüler** (`search_call`): Reload → Schüler wiederhergestellt,
      `worker_ready`, keine `waiting`. (3) Gleiches mit **Hintergrund-Schüler**
      (Peek offen): Reload → Schüler als aktiv zurück, Worker reloaded.
      (4) **Fallback**: Situation erzwingen, in der das Barcode-Feld nach
      direktem Reload nicht erscheint → `load_card()` läuft, Kartei steht am
      Ende. (5) Buchdaten nach Reload gegen die IServ-Kartei abgleichen
      (nicht veraltet — gleicher Hash dürfte dank `#/counter`-Hop kein No-Op
      sein). Jede Test-Ausleihe sofort zurücknehmen.

### Offen 2026-07-09 (Host: „Test Config" als eigener Tab statt Sub-Reiter)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09).

- [x] **Unit-Test**: `tests/test_api_guards.py::test_open_test_config_populates_and_reuses`
      — erster Aufruf befüllt mit allen `TEST_STUDENTS`, zweiter Aufruf
      reaktiviert denselben Kontext (`reused: True`, kein zweiter Eintrag in
      `state.contexts`). Suite grün (148 passed).
- [x] **JS-Syntax**: `node --check` auf den extrahierten `<script>`-Block → OK.
- [ ] **Am Gerät** (manuell): „+" → „Test Config öffnen" klicken, Tab erscheint
      mit 3 Testschülern in der Queue; erneutes Öffnen wechselt nur den Fokus
      (keine doppelte Queue); normaler Klassen-Tab zeigt „Schüler hinzufügen"
      weiterhin korrekt ohne Reiter-Leiste.

### Offen 2026-07-09 (Scanner: Hinweis-Modal für JEDEN nicht-verbuchbaren Scan)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Rein client-seitig
(`web/scan.js` + `web/student.html`); Server-Pfad und IServ/DB unangetastet.

- [x] **Syntax**: `node --check web/scan.js` OK; extrahierter
  `<script>`-Block aus `web/student.html` OK.
- [ ] **Am Gerät** (manuell, read-only): pro Status einmal den Scan treiben
  und Modal-Öffnen + Schließen-Verhalten prüfen — Schüler: dismissible
  Status schließen sich per Button **und** beim Folge-Scan (Weiter-Scannen);
  blocking Status (`book_deleted` mit/ohne Ersatzanspruch, `not_in_stock`)
  nur via Host-Freigabe. Helfer: alle Status am Gerät schließbar.

### Offen 2026-07-09 (Host: Tabs & Einstellungen global — Server-State statt localStorage)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Theme bleibt bewusst pro
Browser in localStorage.

- [x] **Unit-Suite**: `uv run pytest` **145 grün** (keine Logik auf Server-
      Modellebene geändert; `state_snapshot` unverändert; 1-Zeilen-Broadcast-
      Zusatz in `/api/slip-default` wird von keiner Bestands-Assertion getroffen).
- [x] **localStorage-Check**: `grep localStorage web/host.html` → nur noch
      `theme` (cycleTheme/applyTheme).
- [ ] **Am Gerät — zwei Browser/Profile gegen denselben Server** (manuell,
      read-only): A öffnet Klasse „5a" → Reiter erscheint bei B ohne Reload;
      A schließt „5a" → Reiter verschwindet bei B; A aktiviert/deaktiviert in
      Einstellungen „PDF lokal speichern" / „Klasse korrigieren" / „Schüler-
      Leihschein" → Haken bei B erscheint/verschwindet; Theme umschalten auf A
      → B ändert sein Theme **nicht** (bleibt lokal); B Seite neu laden →
      offene Klassen weiterhin sichtbar, eigener Tab-Fokus fällt auf Host
      (nicht auf A's Fokus).

### Offen 2026-07-09 (Scanner: Lupen-Suche — Schnellsprung zu beliebigem Schüler)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). **Read-only** (nur IServ-GETs).

- [x] **Backend**: neue WS-Nachrichten `search_classes`/`search_students`/
      `search_call`. **Unit-Suite grün** (145 passed; +2 Tests in
      `tests/test_queue_flow.py`).
- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; Server-Imports OK.
- [ ] **Am Gerät** (manuell, read-only): Peek öffnen → Lupe → Panel + FLIP;
      Klassen-Liste = alle des Schuljahrs; Schüler-Dropdown pro Klasse;
      Vorauswahl der letzten Klasse beim erneuten Öffnen; Schüler laden →
      Peek endet, Scanner-Ansicht kehrt zurück; danach „Nächster"/Trennen
      räumt den transienten Schüler sauber auf (kein Worker-Leak, Helfer frei).
      *Gegen Produktion nur nach Freigabe; keine Buchung.*

### Offen 2026-07-09 (Helfer-Menü: Klassen-Reiter für alle offenen Host-Klassen)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Commit `8bf6c08`.

- [x] **Backend**: `state.real_contexts_summary()` + `contexts_update`-Broadcast +
      `rebind_helper_to_context`. **Unit-Suite grün** (147 passed; +1 in
      `tests/test_hub.py`, +1 in `tests/test_queue_flow.py`).
- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; Server-Imports OK.
- [ ] **Am Gerät** (manuell, read-only): Host öffnet zwei Klassen-Tabs, Helfer
      verbinden; Menü öffnen → Reiter für beide Klassen, eigene (re-)selektiert,
      eigene Queue mit Aufrufen-Buttons; fremden Reiter wählen → dessen Queue;
      Menü schließen+erneut öffnen → wieder eigene Klasse selektiert; aktiver
      Reiter „nach unten offen" (verbindet optisch mit der Queue darunter);
      Aufrufen eines fremden Schülers → Schüler geladen, Helfer an fremde Klasse
      gebunden (Host-Helfer-Tabelle zeigt neue Klasse); im Peek Hintergrund-Schüler
      **nur** in Statuszeile (`.name-row` verborgen); viele Klassen → Reiter
      horizontal scrollbar; Lupe weiterhin verfügbar.

### Offen 2026-07-09 (Helfer-Menü: Menü-Button im Idle nutzbar)

Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-09). Commit `9d5f413`. Rein
client-seitig; keine neuen WS-Typen, kein Server-/DB-/IServ-Zugriff.

- [x] **JS-Syntax/Imports**: `node --check web/scan.js` OK; keine Server-Änderung.
- [ ] **Am Gerät** (manuell, read-only): Helfer ohne Schüler → Menü-Button
      klappt Kamera-Zeile ein, Queue bleibt sichtbar; Titel „Scanner anzeigen";
      erneut tippen → Kamera wieder eingeblendet; Lupe öffnet Suche, Aufruf
      eines IServ-Schülers lädt ihn (serverseitig `search_call` ohne aktuellen
      Schüler); Schüler wird geladen → Idle-Menü hinfällig, Peek-Verhalten ab
      dann wie gehabt; Burger-Icon morphet synchron mit dem Menü-FLIP zu ←.
- [x] **Menü-Icon-Animation** + **Warteschlangen-Überschrift**: CSS-only,
      erledigt (siehe `docs/CHANGELOG.md`).

### Offen 2026-07-08 (Host-Überarbeitung: Settings + Tab-System)

Multi-Kontext-Refactor des Hosts (`web/host.html`) + Backend
(`server/state.py`, `routes/api.py`, `ws.py`, `sessions.py`, `hub.py`).
Details/Umsetzung: `docs/CHANGELOG.md` (2026-07-08).

- [x] **Backend-Kontext-Modell** + **Routen-Migration**: siehe CHANGELOG.
      **Unit-Suite grün** (143 passed) — bestehende Tests laufen über die
      Kompat-Properties weiter.
- [ ] **Frontend Tab-Chrome** (manuell am Gerät): Tab-Leiste unter Status-Bar
      `[Host] [Klasse … ×] … [+]`; Schuljahr-Auswahl aus `#setup-col` in den
      Einstellungen-Dialog (erster Block, inkl. 409-Confirm + Reset aller
      Klassen-Reiter); `hostTabs`-Persistenz in localStorage (Reihenfolge +
      aktiver Tab). Reload stellt Tabs aus Snapshot + localStorage wieder her
      (fehlende `context_id`s nach Server-Restart droppen).
- [ ] **Klassen-Tab pro Kontext**: eigene Queue + eigenes Now-Serving +
      Pairing-Card (Codes zugeordnet zu den wartenden Schülern DIESER Klasse);
      „Schüler hinzufügen" (Einzelne + Test Config) hängt pro Kontext an.
      Host-Tab: Helfer-Tabelle mit neuer **Klassen-Spalte** (Select zum
      Umbinden) + Modus-B-Kontrolle (öffnen/schließen, QR, iPad-Freischalt),
      keine eigene Queue. × pro Tab → `POST /api/close-class` (Confirm bei
      aktiven Schülern).
- [ ] **Helfer-Klassen-Bindung**: „Nächster" zieht aus `helper.context_id`;
      `call`-Guard weist klassenfremde Aufrufe ab („Schüler nicht in deiner
      Klasse"). Am Gerät mit ≥2 Klassen + ≥1 Helfer prüfen.
- [ ] **E2E-Skripte migrieren** (`automation/e2e_smoke.py`,
      `e2e_parallel.py`, `e2e_modus_b.py`): selektoren auf `#queue-tbody` /
      `#now-serving` / `#setup-col` sind obsolet — die Queue lebt jetzt pro
      Klassen-Tab (`[data-ctx-queue="<id>"]`, `[data-ctx-ns="<id>"]`). Skripte
      müssen erst einen Tab öffnen (`POST /api/open-class` → Kontext-Panel)
      und gegen dessen Selektoren prüfen. **Nur read-only / nach Freigabe**
      (PLAN §6, Produktionsschutz).
- [x] **JS-Syntax**: `node --check` auf den extrahierten `<script>`-Block → OK.
- [x] **Server-Imports**: `server.main`/`routes.api`/`routes.ws`/`hub`/
      `sessions`/`state` importieren sauber.

### Überholt 2026-06-17 (Host: Reiter „Test Config")

Der Sub-Reiter „Test Config" innerhalb eines Klassen-Tabs wurde 2026-07-09
entfernt und durch einen eigenen Top-Level-Tab ersetzt — siehe „Offen
2026-07-09 (Host: „Test Config" als eigener Tab statt Sub-Reiter)" oben.
`TEST_STUDENTS`/`add-test-students` (IDs, Idempotenz-Test, Unit-getestet
2026-06-18) bleiben unverändert gültig; kein offener Testpunkt mehr.

### Offen 2026-06-17 (Scanner: Weiter-Button + Statuszeilen-Layout)

- [ ] **Weiter-Button (⏭) am Gerät** (`scan.html`, nur Helfer): schließt den
      aktuellen Schüler ab und lädt den nächsten aus der Queue; alter Schüler
      verschwindet **sofort**, Statuszeile zeigt „Wird geladen…", neuer Schüler
      erscheint nach kurzem Laden. Leere Queue → „Warteschlange leer".
      **Wichtig:** dabei darf im simulierten Browser nichts gebucht werden
      (Worker-Page wird nur geschlossen, kein Submit).
- [ ] **Statuszeilen-Layout:** Statuszeile nur so breit wie das Kamerafeld,
      links Drucker-Button (Druck-Funktion implementiert 2026-06-22 — WS
      `print` → `print_loan_slip_for`, am Gerät zu prüfen), rechts Weiter-Button; Name vertikal
      mittig zu Klasse/Bezahlt; farbiger Status-Punkt in beiden Clients entfernt.

### Offen 2026-06-17 (Scanner: Dark/Light + Klasse + transparente Zeilen)

- [ ] **Dark-/Light-Mode am Gerät** (`scan.html` + `student.html`): folgt
      `prefers-color-scheme` (erst Browser-Override, dann System); kein Toggle.
      In DevTools (*Rendering → Emulate prefers-color-scheme*) und am echten
      Handy beide Themes auf Lesbarkeit prüfen (Kontraste, native Controls).
- [ ] **Buchzeilen transparent:** Tint + Rand statt Vollfläche — vorgemerkt
      (orange) / ausgeliehen (grün) in beiden Themes noch erkennbar.
- [ ] **Klasse über Bezahlstatus:** zeigt z. B. „10c" **ohne** „Klasse"-Präfix;
      erscheint erst nach Schüler-Zuweisung/Pairing (kommt aus der Queue).

### Offen 2026-06-16 (Scanner-UI-Redesign + Buch-Daten-Anreicherung)

- [ ] **Scanner-Layout am Gerät** (`scan.html` + `student.html`): obere Leiste
      Zahnrad/Kamera-Streifen/Taschenlampe+Ton, volle Statuszeile, großer Name mit
      Bezahlstatus rechtsbündig, scrollbare Bücher-Tabelle — auf dem Handy prüfen
      (Querformat-Kamerastreifen + Scan funktioniert, nur Tabelle scrollt).
- [ ] **Bücher-Tabelle mit echten Daten:** Spalten Fach | Titel | Status-Icon;
      vorgemerkt (gelb/orange, ⏳) oben, ausgeliehen (hellgrün/dunkelgrün, ✓) unten;
      Titel + Fach korrekt aus `client.series` aufgelöst (Niklas' Test-Schüler).
- [ ] **Serien-Katalog-Cache** (`IsServClient._get_series_map`, read-only
      `GET /series`): erste Schülerauswahl lädt den Katalog einmalig; Titel/Fach
      auch für bereits ausgeliehene Bücher (nur `code`+`isbn` im Roh-Payload) gefüllt.

### Offen in dieser Session (Druck + Packaging)

- [ ] **Leihschein-Druck `file`-Backend** end-to-end über den Endpoint
      `POST /api/print-loan-slip` (Server laufend, read-only PDF-Abruf gegen
      IServ → PDF in `automation/out/loan_slips/`). Mit Niklas' Test-Schüler.
- [ ] **Druck `lp` (macOS, USB-Drucker)** — echter Ausdruck auf dem Macbook.
- [x] **Druck `sumatra` (Windows)** — Silent-Print am Ausleihe-Laptop mit
      HP LaserJet Professional P1102 (= Spike C / O4) — 2026-06-22 (→ V12).
- [ ] **Host-Button „Leihschein"** (UI) löst Druck korrekt aus, Statusmeldung.
      Code vollständig verdrahtet (2026-06-22): `printLoanSlip` → `POST
      /api/print-loan-slip` → `print_loan_slip_for`; am Gerät zu prüfen.
- [ ] **Seitenwahl-Toggle „Schüler-Leihschein"** (host.html, 2026-06-22): es
      wird stets der 2-seitige Beleg geholt; Seite 1 immer gedruckt, Seite 2 nur
      bei aktivem Toggle (`second_page` → `pages=None`, sonst `pages="1"`).
      Seitenbereich via `lp -o page-ranges=` bzw. SumatraPDF `-print-settings`;
      `win-default` kann **nicht** einschränken (druckt alle Seiten — WARN-Log).
      **Am echten Drucker prüfen**, dass `-print-settings "1"` wirklich nur
      Seite 1 druckt (Sumatra-Pfad ist der Produktivweg).
- [ ] **Scanner-Button „Leihschein" (🖨)** (`scan.html`, nur Helfer) löst Druck
      des aktuell zugewiesenen Schülers aus: WS `{type:'print'}` →
      `print_loan_slip_for(helper.student_id)` → `print_result`. Button während
      Druck deaktiviert, Statuszeile zeigt Backend/Detail bzw. Fehler.
      Unit: `tests/test_printing.py::test_print_loan_slip_for_reads_and_prints`
      (2026-06-22); am Gerät mit echtem Drucker zu prüfen.
- [ ] **`setup.bat` / `start.bat`** am echten Windows-Laptop (uv vorhanden,
      `uv sync`, Playwright-Install, Start).
- [ ] **`start.sh`** auf dem Macbook.
- [ ] Leihschein `variant="student-always_school-auto"` (2-Seiten-Beleg) prüfen,
      falls Schul-Beleg gewünscht.

### Härtung 2026-06-15 (gegen IServ / im Betrieb zu prüfen)

- [ ] **Selektor-Drift-Canary** (`WorkerPool.check_selectors`, read-only) beim
      Server-Start: bestätigen, dass er `input.tt-input[name="input"]` findet und
      bei DOM-Änderung WARN loggt (echter IServ-Read, kein Submit).
- [ ] **Worker-Status im Host** (`Worker: x/y frei`) live prüfen, inkl.
      Warnfarbe bei 0 Workern.
- [ ] **`secure`-Cookie** + **Logfile-Rotation** (`logs/server.log`) im echten Lauf
      gegenchecken (kein Schülername im Log — PLAN §3.7).

### Härtung 2026-06-15 (Frontend/TLS/Robustheit) — am Gerät zu prüfen

- [ ] **TLS-Cert am Zielgerät:** Handy verbindet über `https://<Laptop-IP>:3443`
      ohne CN/Host-Mismatch (SAN greift); Cert-Erzeugung ohne openssl-Binary
      (jetzt via `cryptography`).
- [ ] **`select-class`-Guard:** Klassenwechsel bei aktiven Sessions → 409 +
      Host-Confirm → Force räumt Sessions sauber ab (keine Waisen).
- [ ] **Einzelschüler-Reiter (2026-06-16):** Reiter *Einzelne Schüler* →
      `GET /api/students-for-class` lädt Liste, `POST /api/add-student` hängt
      einzeln an die Queue an (auch klassenübergreifend); Duplikat → 409;
      bestehende Queue/Sessions bleiben unangetastet.
- [ ] **Reconnect-Backoff** (scan/student/qr-display): Trennung → exponentieller
      Backoff bis 30 s, Reset bei Verbindung.

### Offen 2026-07-05 (Einstellungen: Buchreihen ausblenden)

- [ ] **„Ausblenden"-Button je Buch** im Einstellungen-Dialog (`host.html`,
      Reiter „Bücherlisten ordnen"): ausgeblendete Buchreihen eines Jahrgangs
      (`state.hidden_isbns_by_grade`, In-Memory, kein DB-/IServ-Write) gelten
      beim Scannen nicht mehr als „vorgemerkt" — weder in der Scanner- noch
      der Schüler-Anzeige — und sind damit auch nicht buchbar (gefiltert via
      `apply_hidden_books()` in `sessions.py`/`routes/ws.py`, direkt nach
      jedem `get_student_info`-Aufruf). Logik unit-getestet
      (`tests/test_class_book_order.py`: `apply_hidden_books`,
      `get_hidden_isbns_for_form`, State-Reset); **UI-Interaktion am Gerät
      noch zu sichten** (Toggle-Button, Persistenz über „Speichern",
      Live-Effekt bei bereits geladenem Schüler bewusst nicht sofort — analog
      zur bestehenden Bücher-Reihenfolge, erst beim nächsten Laden/Reconnect).

### Offen 2026-07-08 (Serverseitige Persistenz der Buchreihenfolge/Ausblendung)

- [x] **Persistenz `data/booklist_settings.json`** (`server/booklist_store.py`):
      `book_orders_by_grade` + `hidden_isbns_by_grade` werden beim Start geladen
      (`app.py` lifespan) und bei jeder Änderung (`POST /api/booklist-order`,
      `POST /api/booklist-hidden`) atomar weggeschrieben. Einziger globaler Satz
      (nicht pro Schuljahr) — beim Schuljahreswechsel bleibt die Konfiguration
      erhalten, nur `form_catalog_cache` wird geleert (ISBNs jahresspezifisch).
      ISBN-Drift fängt `normalize_book_order` + `hidden & catalog` ab: neue
      Katalog-Bücher sichtbar ans Ende, weggefallene gedroppt (Anforderung).
      Unit-getestet (`tests/test_booklist_store.py`: Round-Trip, fehlende/
      korrupte Datei, data-Dir-Anlage, deterministische Serialisierung,
      neue-ISBNs-ans-Ende, Nicht-String-Einträge gedroppt). Schreib-/Ladefehler
      non-fatal (In-Memory-State bleibt Leading). **Manueller Smoke am Gerät
      noch offen** (Serverneustart → Konfiguration wieder da).

### Aus Review Tier 2 (2026-07-05, PLAN §5 Phase 2)

- [x] **`current_books`-Jahrgangsfilter entfernt (2026-07-06).** Der
      konservative `distributed_at`-Schuljahresfilter in `get_student_info`
      ist raus; `?books=true` liefert zuverlässig nur aktuell ausgeliehene
      Bücher (API-Referenz), so dass alle aktuell ausgeliehenen Exemplare —
      auch noch nicht zurückgegebene Vorjahres-Bücher — als „ausgeliehen"
      ausgewiesen werden. Siehe PLAN §5 Phase 2 (2026-07-06).

### Offen 2026-07-06 (Ermäßigungs-/Befreiungsnachweis + Modus-B-Host-Freigabe)

- [x] **Nachweis-Feldsemantik read-only verifiziert.** Enrollment-Payload
      trägt `remission_request`/`remission_accepted`/`remission_judged_*`
      (Ermäßigung) bzw. `exemption_*` (Befreiung); `*_accepted` ist tri-state
      (`null`=unentschieden, `true`=akzeptiert, `false`=abgelehnt).
      „Nachweis fehlt" = `*_request is True and *_accepted is None`.
      Verifiziert am Testschüler 2159 (kein Antrag → beide Pending=False).
      Gebaut: `get_student_info` liefert `remission_pending`/`exemption_pending`;
      `web/scan.js` + `web/student.html` zeigen den Hinweis in Offen-Farbe
      vor dem Betrag; „Bezahlt" bei Nachweis unterdrückt; „Nicht angemeldet"
      im Schülerclient grau. Suite 92 grün.
- [ ] **Nachweis-Hinweis am Gerät mit echtem Pending-Fall.** Bislang nur
      gegen „kein Antrag" verifiziert — ein Schüler mit unentschiedenem
      Ermäßigungs-/Befreiungsantrag ist auf Prod nicht bekannt. Visueller
      Check der Hinweis-Anzeige + der kombinierte Host-Freigabe-Dialog
      (Modus B, `POST /api/student/pair` → `reason:"blocked"`-409 +
      `blockers`-Liste) steht aus, sobald ein solcher Fall vorliegt.
- [x] **Nicht-angemeldet paaren ohne Nachfrage.** Blocker-Prüfung auf
      `enrolled` gegated — kein False-Positive-Dialog „Nicht bezahlt
      (offen: None €)" mehr. Logik-Review (kein echter Nicht-angemeldet-
      Schüler auf Prod verfügbar).

### Offen 2026-07-07 (Lade-State bis Worker bereit — `worker_ready`)

- [ ] **Modus B live (`web/student.html`):** nach Pairing Statuszeile „Wird
      geladen…", Name/Klasse/Bezahlt sichtbar, Bücher-Bereich zeigt Placeholder
      „Bücher werden geladen…", Scans tun nichts. Sobald Worker ready
      (`worker_ready`-Nachricht): Bücherliste erscheint, Status → „Scanner
      bereit — Buch scannen", Scans funktionieren. **Unit:** Assertion in
      `tests/test_queue_flow.py::test_advance_helper_picks_next_and_completes_previous`
      (Modus A: `student_info` mit `books==[]` + `worker_ready`). Live am
      Testschüler offen (read-only, kein Enter — Niklas+Lukas-Freigabe).
- [ ] **Modus A live (`web/scan.js`):** nach Aufruf Bücherliste sofort sichtbar,
      Status „Warten…", Scans ignoriert. Sobald Worker ready: Status →
      „Scanner bereit — Buch scannen", Scans funktionieren.
- [x] **Reconnect (Seite neu laden) in aktiver Session — Modus A** (`server/routes/ws.py`,
      Mechanismus + 2026-07-09-Update: `docs/CHANGELOG.md`). **Unit:**
      `tests/test_scanner_reconnect.py` (14 Tests). Live am Gerät noch offen
      (read-only).
- [ ] **Reconnect (Seite neu laden) — Modus B** (`ws_student`): Worker bereits
      offen → `worker_ready` + Bücher sofort wiederhergestellt (nicht
      „Warten…"/„Wird geladen…" hängen bleiben).

### Offen 2026-07-08 (Helferclient: Menü-Toggle / Peek zwischen Schüler- und Warteschlangen-Ansicht)

Details/Umsetzung (Protokoll, Animations-Sync 2026-07-09): `docs/CHANGELOG.md`.

- [ ] **Menü-Toggle (Peek) am Gerät** (`web/scan.js`/`scan.html`): Hamburger-Menü
      (≡) schaltet bei zugewiesenem Schüler auf die Warteschlangen-Ansicht,
      ohne ihn zu trennen; nochmal Drücken kehrt zurück; Aufrufen eines
      anderen Schülers aus der Peek-Ansicht legt den alten als „pending"
      zurück; Scheitert der Aufruf, kehrt der Client automatisch in die
      Peek-Ansicht zurück. Unit: `tests/test_hub.py` +1,
      `tests/test_queue_flow.py` +2; Suite **133 grün**; `node --check` OK.
      Live am Gerät offen (read-only, kein Enter — Niklas+Lukas-Freigabe).
- [x] **Animations-Sync** (Steuer-Elemente/Lupe faden synchron mit der
      Statuszeilen-FLIP-Bewegung): Headless verifiziert (Playwright), kein
      JS-Fehler, Layout-Kollaps real. Live am Gerät offen.

### Offen 2026-07-07 (Helferclient: Ausleih-Freigabe-Dialog bei Unstimmigkeit)

Details/Umsetzung: `docs/CHANGELOG.md` (PLAN O10).

- [ ] **Freigabe-Dialog bei Unstimmigkeit** (`web/scan.js`/`scan.html`):
      Schüler mit `remission_pending`/`exemption_pending`/`!paid` (nur bei
      `enrolled`) laden → erstes Buch scannen → Dialog erscheint, Scan geht
      erst nach „Ja, ausleihen" raus; „Nicht ausleihen"/Escape verwirft;
      Neuladen resettet die Freigabe. Rein client-seitig, nur GET, kein
      Schreibzugriff. Kein automatisierter Test (UI-Gate); live am
      Testschüler mit künstlicher Unstimmigkeit offen (read-only —
      Niklas+Lukas-Freigabe).

### Aus dem bisherigen Plan (Phase 3/4)

- [ ] **Lasttest: 5 parallele Schüler-Sessions** (Modus B) — `WORKER_CONTEXTS`
      erhöhen, Pool-Verhalten unter Last prüfen (PLAN §5 Phase 4).
- [ ] **Rate-Limit `/api/student/join` end-to-end** — Drosselung (429) im echten
      Server unter Flut prüfen (Logik ist V9; HTTP-Pfad noch offen, ggf. im Lasttest).
- [ ] **Generalprobe Modus A** im Schul-WLAN mit echtem Drucker (Phase 3).
- [ ] **Spike D — Schul-WLAN-Reichweite / Client-Isolation** (O9), vor Ort.
- [ ] iPad im geführten Zugriff (iOS-Kiosk) — organisatorisch (PLAN §3.4).

### Gesperrt — erst nach Buchungstest-Freigabe (Niklas + Lukas, PLAN §6)

- [ ] **Buchender Submit-Pfad** — Code **gebaut + gated** (`commit_barcode` mit
      Enter, `/api/commit-book`, `ALLOW_BOOKING=false` default; Gate verifiziert = V10).
      Noch zu testen (nur mit Freigabe Niklas + Lukas): `ALLOW_BOOKING=true`, echtes
      Enter, **Erfolgs-/Fehler-Selektoren in `_read_booking_result()` bestätigen**
      (Listen-Selektoren gegen einen DOM-Dump verifiziert, Filter-Logik
      unit-getestet = V17; offen bleiben Substring-statt-Spalten-Vergleich und das
      feste 1500-ms-Fenster — siehe „Offen 2026-07-09 (Worker: Robustheit …)" oben),
      Ausgabe + sofortige Rücknahme eines ausgemusterten Buchs; Rückbau-Plan vorher
      ausfüllen (`docs/rueckbau_plan_VORLAGE.md`).
- [ ] **Scanner-Fehlerfälle** aus dem DOM (falsche Serie, nicht angemeldet, schon
      verliehen, unbekannter Code) — beobachtbar erst im freigegebenen Buchungstest.
- [ ] **End-to-End inkl. echter Buchung** (Modus A und B).

## Unit-Tests (pytest, `uv run pytest`)

Reine Logik, kein IServ/Playwright/Server — schnell + produktionsneutral, als
Regressions-Netz und QS-Beleg. **187 Tests, grün (2026-07-09; +29 aus dem
Wartbarkeits-Refactoring — neu `tests/test_stale_guards.py` (4),
`tests/test_ws_scanner.py` (8), `tests/test_booking_result.py` (6) sowie
Kontext-Lifecycle- und Pairing-TOCTOU-Ergänzungen in `tests/test_api_guards.py`;
siehe V13–V17 oben und `docs/CHANGELOG.md` für die Details.
Vorherige Chronologie (2026-06-16 – 2026-07-08) steht in `docs/CHANGELOG.md`,
nicht mehr hier ausgeschrieben).** Coverage
(`--cov=server` in `addopts`): **59 %** gesamt
(vorher 47 %/2026-07-08, 45 %/2026-07-05, 39 %/2026-06-18, 37 %, initial 20 %);
`routes/ws.py` 13 % → 38 %, `sessions.py` 65 % → 74 %, `modus_b.py` 21 % → 47 %,
`classes.py` 52 % → 75 %. Kernlogik deutlich höher —
`hub.py` 82 %, `state.py` 93 %, `sessions.py` 60 %, `config.py` 93 %,
`ratelimit.py` 100 %, `tls.py` 69 %, `book_order.py` 76 %.
Bewusst niedrig bleiben IServ-/Playwright-/Wiring-Module (`iserv_client.py`
31 %, `routes/ws.py` 38 %, `app.py`, `main.py`) — die decken die E2E-Skripte
V3–V7 ab. `routes/api.py` selbst ist nur noch der Aggregator (100 %, 8
Anweisungen); die Endpunkt-Logik sitzt jetzt in den neun `routes/`-Modulen,
siehe Coverage-Tabelle oben.

| Datei | Deckt ab |
|-------|----------|
| `tests/test_hub.py` | WS-Verteiler: `broadcast_host` (Auslieferung + tote Host-Sockets entfernt), `queue_update` (mit `queue`-Liste) nur an unzugewiesene Scanner, `broadcast_queue_size`/`send_scanner` lösen tote Scanner-Sockets (`ws=None`, kein Leak), no-op bei unbekanntem Token; **2026-07-08:** `broadcast_queue_size` erreicht zugewiesene Helfer mit `peeking=True` (Menü-Peek) |
| `tests/test_ratelimit.py` | Drossel (allow/throttle, Fenster-Ablauf, pro-IP, sweep) |
| `tests/test_booking_gate.py` | Buchungs-Gate: ohne Flag kein Worker-/Enter-Zugriff |
| `tests/test_sessions.py` | Session-Lebenszyklus, Token/Code-Eindeutigkeit, harte Invalidierung |
| `tests/test_queue_flow.py` | Queue-Übergänge: `gen_pairing_code` (skip/Erschöpfung), `end_student` (Status/Helfer-Lösung/Worker-Release), `advance_helper` (leer + nächster; sendet `loading`, kein Idle-`waiting`), `assign_student_to_helper` (gezielter Aufruf aus der Warteschlange — ältester Wartender bleibt unangetastet; `loading`-WS-Push), `pending_queue_as_list` (nur status='pending'), harte Worker-Freigabe; **2026-07-09:** `assign_student_to_helper` setzt `helper.student_form` (Queue- wie Lupe-Schüler; Advance wechselt die Form mit), `end_student` räumt `student_form` (auch transienter Zweig); **2026-07-08:** `end_student`/`assign_student_to_helper` resetten `helper.peeking` (Menü-Peek) |
| `tests/test_api_guards.py` | Endpunkt-Logik: Auth-Guard (`_require_host`), Login, `add-student` (Validierung/Duplikat 409), `add-test-students`-Idempotenz, skip/finish-Validierung, Buchungs-Gate HTTP-Ebene (403), `_base_url`/`_last_scan_for`; **2026-07-09 (V15/V16):** Kontext-Lifecycle (Doppel-Öffnen reaktiviert, `close_class` löst Helfer, unbekannte Kontext-ID → 404) + drei Pairing-TOCTOU-Fälle (Session revoked / Schüler vergeben / Code neu belegt während des `get_student_info`-Awaits → 409) |
| `tests/test_stale_guards.py` (V13) | `open_student()`-Re-Check nach dem Await: ändert sich `helper.student_id` (Modus A) bzw. die Modus-B-Session während des Worker-Aufbaus, wird der neue Context sofort wieder geschlossen statt registriert (kein Leak); Happy-Path registriert korrekt |
| `tests/test_ws_scanner.py` (V14) | Scanner-WS-Dispatch über echten `websocket_connect` (ohne Lifespan, Fake-IServ, `worker_pool=None`): unbekanntes Token, Scan ohne Schüler, malformed JSON-Frame tötet die Schleife nicht, Peek-Toggle, `call` auf nicht-pending Schüler, `call` bindet Helfer an fremde Klasse um, `search_call` für queue-losen Schüler, `search_call` ohne `form` → Fehler |
| `tests/test_booking_result.py` (V17) | `_read_booking_result` gegen Fake-Locator: sichtbarer Error-Alert (gekürzt) → `error`, unsichtbarer Alert ignoriert, Barcode in Buchzeile → `booked`, Barcode **nur** im Typeahead-Feld → `unknown` (der `has_not`-Filter, der genau das verhindert), nichts gefunden → `unknown`, Locator-Exception wird geschluckt |
| `tests/test_printing.py` | Backend-Resolution (auto je Plattform) + `file`-Backend |
| `tests/test_worker_pool.py` | `WorkerPool.stats()` (total/available/in_use) |
| `tests/test_tls.py` | Cert hat SAN (localhost/127.0.0.1/cn), idempotent |
| `tests/test_booking_precheck.py` | Buchungs-Vorabprüfung (`evaluate_scan_for_booking`: Prüf-Reihenfolge `deleted → series_already_lent → not_in_stock → not_enrolled`; `book_deleted`-Vorrang; `not_in_stock`-vor-`not_enrolled`; `series_already_lent`-vor-`not_in_stock` auch bei lagerndem Exemplar; `unknown_book`, `not_ready`, Lookup-Fehler) + `process_scan`-Gate-Verhalten (Buchen/Stagen/kein Feldkontakt) + Alert-Broadcast (`not_in_stock`/`book_deleted`→Alert mit `source`; `series_already_lent`→kein Alert) + `loaned_to`-Durchreichung (Name-Feld getrennt von `msg`; Helfer-`scan_result`+Host-`book_alert` carry `loaned_to`, Schüler-Source strippt es auf `None`; msg bleibt name-frei) — auch für `book_deleted` mit `student_id` (Ersatzanspruch) + `lent` autoritativ aus `current_books` (ungefiltert, ignoriert `apply_hidden_books` — ausgeblendete Reihe die der Schüler hat bleibt `series_already_lent`) + ISBN-Umhängung `vormerk→lent` nach `booked` in derselben Session (Session-Mutables passed-by-reference, kein Neuladen) |
| `tests/test_scanner_reconnect.py` | Scanner-Reconnect/Disconnect-Grace (Modus A): `_deferred_end`-Re-Checks (Reconnect/`student_id`-Wechsel/neuer Schüler → No-op; echte Trennung → Teardown; Cancel → No-op), In-Flight-`load_task`-Cancel, Exception-Robustheit (`end_student`-/`broadcast_host`-Fehler schlucken), Worker-Release ohne Queue-Eintrag; `StudentSession.reload()` **(2026-07-09: direkter Schüler-Route-Hop `#/counter`→`#/counter/student/<id>` auf initialisierter Page statt App-Root+Schüler-Route, mit `load_card`-Fallback; Re-Login bei Login-Redirect, RuntimeError ohne Re-Login, Barcode-Timeout → Fallback)** — RAM-State/Fake-Pages, kein Browser/IServ |

## Hinweise zum Testen (wenn es so weit ist)

- Server für E2E-Skripte muss laufen (`uv run python -m server.main`); die
  `automation/e2e_*`-Skripte treiben die echten Web-Seiten per Playwright.
- Druck-Tests gefahrlos mit `PRINT_BACKEND=file` beginnen (kein physischer Druck).
- Buchende Tests **nie** unbeaufsichtigt/automatisch; nur Einzelfall-Freigabe.
