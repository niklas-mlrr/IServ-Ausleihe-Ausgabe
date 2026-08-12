# Implementierungsplan: Klassen-Lehreransicht für Modus B

> Beschlossen am 2026-08-04. Dieses Dokument beschreibt das Zielbild und den
> Umsetzungsumfang der neuen Unterseite `/teacher`; die chronologische
> Umsetzungshistorie gehört anschließend in `docs/CHANGELOG.md`.

## Zielbild

Eine Lehrkraft einer laufenden Modus-B-Klasse kann auf einem eigenen Gerät den
Fortschritt **ausschließlich dieser Klasse** live verfolgen. Die Ansicht zeigt
alle Schüler mit ihrem aktuellen Ausgabestatus und erlaubt nur, wartende Schüler
als abwesend zu markieren bzw. diese Aktion rückgängig zu machen. Bei
abgeschlossenen Schülern kann ein gedruckter Leihschein einmalig als
entgegengenommen markiert werden.

Die Lehrkraft erhält den Zugang über einen QR-Code, den der Host im jeweiligen
Klassen-Tab erzeugt. Der QR-Code enthält einen langen, zufälligen Token, der
serverseitig fest an den Klassen-Kontext gebunden ist. Das Pairing folgt dem
Muster des vorhandenen QR-Displays: Die Lehrkraft sieht zunächst nur einen
Registrierungscode; erst nach Bestätigung durch den Host erhält sie die
Klassenansicht.

## Sichtbare Status

| Queue-Zustand / Zusatz | Anzeige für Lehrkraft |
|---|---|
| `pending` | Noch nicht begonnen |
| `active` + Fortschritt | In Ausgabe — X/Y Bücher erfasst |
| `active` + `slip_printing` | Leihschein wird gedruckt |
| `done` | Ausgabe abgeschlossen |
| `skipped` | Übersprungen |
| `absent` | Abwesend |

Die Kopfansicht führt fünf getrennte Zähler: **abgeschlossen**, **aktiv**,
**offen**, **übersprungen** und **abwesend**. Jeder Schüler zählt genau einmal.
Ein beim Klassen-Laden automatisch fertig gesetzter Schüler (`done` mit
`auto_skipped`) zählt in dieser Ansicht als **übersprungen**; `absent` bleibt
ein eigener Status.

`Ausgabe abgeschlossen` bedeutet ausdrücklich: Alle vorgesehenen Bücher wurden
gescannt und der Leihschein wurde gedruckt. Die Ansicht behauptet nicht, dass
eine IServ-Buchung verifiziert wurde; bei `ALLOW_BOOKING=false` sind Scans nur
gestaged.

## Sicherheits- und Datenmodell

- Neue `TeacherSession` in `server/state.py`, angelehnt an
  `PrinterDisplaySession`: `token`, `context_id`, `registration_code`,
  `authorized`, `ws`, Zeitstempel.
- Token: kryptographisch zufällig und lang; eine URL enthält nie einen
  Schülernamen oder eine Klassenkennung.
- Vor dem Host-Pairing liefert die Lehrer-WebSocket ausschließlich den
  Registrierungscode, niemals Klassen- oder Schülerdaten.
- Nach Pairing liefert ein eigener, minimierter `teacher_snapshot`, nicht der
  vorhandene Host-`state_snapshot()`.
- Erlaubte Daten: Klassenanzeige, Modus-B-Status, Summen sowie Name,
  Queue-Status, Fortschritt und Druckstatus jedes Schülers der gebundenen
  Klasse.
- Verbotene Daten: andere Klassen, QR-/Pairing-Secrets, Zahlungs- und
  Anmeldedaten, Buchdetails, Drucker, Worker-/Host-Einstellungen und
  Host-Aktionen.
- Beim Klassen-Schließen/Reset, expliziten Host-Trennen oder Token-Ersatz wird
  die Session entwertet und ihre WebSocket geschlossen. Ein Reconnect ist nur
  für eine weiter autorisierte, gültige Session möglich.

## Host-Ablauf

1. Im Klassen-Tab klickt der Host auf „QR für Lehrkraft".
2. `GET /api/teacher/qr?context_id=…` mintet eine neue, an diesen Kontext
   gebundene Session und liefert den QR für `/teacher?token=…`.
3. Die Lehrkraft scannt ihn; `/teacher` verbindet sich mit
   `/ws/teacher?token=…` und zeigt den Registrierungscode.
4. Der Host bestätigt den Code im selben Klassen-Tab über
   `POST /api/teacher/authorize`.
5. Der Server schickt den ersten `teacher_state` und danach Updates per
   WebSocket.

Pro Klasse soll höchstens eine aktive Lehrer-Session existieren. Ein neuer QR
ersetzt eine noch nicht autorisierte Session; für eine autorisierte Session
braucht der Host eine sichtbare Aktion „Lehrkraft trennen", damit ein
versehentlicher QR-Klick keine laufende Ansicht unterbricht.

## Lehrer-Ablauf und Rechte

- `/teacher` ist mobil lesbar, hat keine Host-/Buchungs-/Druck-Steuerung und
  zeigt Klasse sowie die fünf getrennten Summen `abgeschlossen / aktiv / offen /
  übersprungen / abwesend`.
- Bei `pending` darf die Lehrkraft „Als abwesend markieren" auswählen; die
  Wischweite dient als Bestätigung.
- Bei `absent` darf sie „Nicht abwesend" wählen; ein Bestätigungsdialog schützt
  die Rücknahme.
- Bei `skipped` zeigt die Ansicht nur „Übersprungen" an. Host-übersprungene
  Schüler — einschließlich `done + auto_skipped` — haben keine Lehreraktion.
- Der Server erlaubt nur `pending -> absent` und `absent -> pending`.
  `skipped` bleibt der Host-/Auto-Skip-Status; `active` und `done` bleiben
  vollständig hostgesteuert, damit eine Lehrkraft weder eine laufende Ausgabe
  beendet noch einen Abschluss vortäuscht.
- Bei aktiviertem `done_collected` und gedrucktem Leihschein setzt der Button
  „Leihschein entgegengenommen" den Marker einmalig. Die Aktion ist für die
  Lehrkraft nicht rücknehmbar und bei erneutem Aufruf idempotent. Der globale
  Host-Befehl `reset_progress()` darf den Marker für einen neuen Durchlauf
  weiterhin löschen. Bei `helper_scanned` bleibt der Text „Leihschein &
  Bücherstapel entgegengenommen" erhalten.
- Die Schülerliste kann alphabetisch oder nach Status sortiert werden. Die
  Statusreihenfolge lautet `active -> pending -> absent -> done -> skipped`;
  innerhalb einer Gruppe gilt Nachname, Vorname, stabile Schüler-ID.
- Der Statuswechsel erfolgt lokal im Runtime-State; kein IServ-API-Write und
  keine Playwright-Aktion.

## Technische Arbeitspakete

1. State + Lifecycle: `TeacherSession`, kontextgebundene Lookups,
   `teacher_snapshot`, invalidieren beim Kontext-Teardown.
2. API: QR-Minting, Host-Autorisierung/-Trennen und token-autorisierter,
   strikt klassenbezogener Statuswechsel-Endpunkt.
3. WebSocket/Hub: `/ws/teacher`, gezielte Teacher-Broadcasts bei jeder
   Queue-, Fortschritts- und Druckänderung.
4. Host-UI: QR-Button, QR-Dialog, Registrierungscode, Verbindungsanzeige und
   Trennen im konkreten Klassen-Tab.
5. Teacher-UI: neue `web/teacher.html`/`web/teacher.js` und Clean-Route
   `/teacher`; responsive Statusliste, fünf getrennte Zähler,
   Leer-/Offline-/Sperrstatus, Abwesenheits-Wisch-Geste und Rücknahme-Dialog,
   irreversible
   Leihschein-Aktion und Sortierung.
6. Tests: Token-/Autorisierungs-/Klassen-Isolation, Snapshot-Privacy,
   erlaubte und verbotene Statusübergänge, WebSocket-Pairing, Live-Updates,
   Reconnect und Entwertung.
7. Dokumentation: `docs/test_status.md`, `docs/CHANGELOG.md` und die Wiki-
   Overview nach der tatsächlichen Umsetzung aktualisieren.

## Abnahmekriterien

- Ein QR einer Klasse kann nie Daten oder Aktionen einer anderen Klasse
  freischalten.
- Vor Freischaltung sind keine Schülerdaten im Browser oder WebSocket-Payload.
- Die Liste folgt Pairing, Scan-Fortschritt, Druck, Abschluss, Überspringen
  und Rücknahme ohne Reload live.
- Die fünf Lehrerzähler sind getrennt und summieren sich exakt auf die Zahl der
  Schüler in der Liste, einschließlich der `auto_skipped`-Abbildung.
- Ein Lehrer kann nur `pending <-> absent` für Schüler seiner Klasse ändern;
  `skipped` und `done + auto_skipped` bleiben in der Lehreransicht
  aktionslos sichtbar.
- Die Leihschein-Aktion setzt nur `false -> true` (idempotent bei `true`);
  ein Zurücknehmen durch die Lehrkraft wird abgewiesen. `reset_progress()` darf
  den Marker für einen neuen Durchlauf löschen.
- Die Liste ist alphabetisch oder nach `active -> pending -> absent -> done ->
  skipped` sortierbar; WebSocket-Updates behalten die Auswahl.
- Token-Entwertung stoppt sofort weitere Datenupdates und ein Reload kann den
  Zugang nicht wiederherstellen.
- Die komplette Änderung bleibt ohne IServ-API-Write; der bestehende
  Playwright-Buchungsschutz bleibt unverändert.
