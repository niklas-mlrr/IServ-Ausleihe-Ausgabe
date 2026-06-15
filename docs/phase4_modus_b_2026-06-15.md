# Phase 4 — Modus B (Live-Ausgabe): initialer Aufbau (2026-06-15)

> Session-Protokoll (Seminarfach-Material). Bezug: `docs/PLAN.md` §1/§3/§5.
> Reiner Server-/Web-Code, **keine Hardware, keine Buchung** — der Write-Pfad
> bleibt wie in Modus A auf `staged`/kein-Enter gesperrt (PLAN §6).

## 1. Was gebaut wurde

Erstausbau des Live-Ausgabe-Modus, in dem **Schüler am eigenen Gerät** ihre
bestellten Bücher selbst scannen. Komponenten:

- **iPad-QR-Anzeige** (`web/qr-display.html`): zeigt einen **allgemeinen,
  anonymen** QR-Code; registriert sich per Code am Leitstand; **nie**
  Schülerdaten.
- **Schüler-UI** (`web/student.html`, reduziert, aus `web/scan.html`): zeigt nach
  dem QR-Scan einen 4-stelligen Code, nach Freigabe die eigene Bestellliste +
  Bezahlstatus + Scanner + „Fertig".
- **Leitstand** (`web/leitstand.html`): neue Karte „Live-Ausgabe (Modus B)" mit
  Öffnen/Schließen, iPad-/Code-Status; **Pairing-Button** pro wartendem Schüler.
- **Server**: Session-Lebenszyklus + Endpunkte (`server/sessions.py`,
  `server/routes/api.py`, `server/routes/ws.py`), Timeout-Sweeper
  (`server/app.py`).

## 2. Geänderter Pairing-Mechanismus (Entscheidung Niklas, 2026-06-15)

PLAN §3.1 sah ursprünglich ein **personalisiertes Einmal-Token *im* QR** vor
(pro Schüler ein eigener QR auf dem iPad). Geändert auf:

1. iPad zeigt **einen allgemeinen QR** (kein Schülerbezug).
2. Schüler scannt → Browser ruft `POST /api/student/join` → Server legt eine
   Session an und liefert **`session_token` (lang, zufällig)** + **4-stelligen
   `pairing_code`**. Der Browser zeigt nur den 4-stelligen Code.
3. Leitstand **ordnet den 4-stelligen Code einem Schüler aus der Queue zu**
   (`POST /api/student/pair`) → Session wird `paired` → erst jetzt fließen
   Schülerdaten.

**Begründung / Sicherheitsäquivalenz:**

- Der **eigentliche Zugangs-Credential** ist der lange `session_token`
  (`secrets.token_urlsafe(32)`, ~256 bit), nicht der 4-stellige Code. 4 Ziffern
  wären brute-forcebar — sie dienen ausschließlich der **menschlich vermittelten
  Zuordnung** am Leitstand und gewähren für sich genommen **nie** Datenzugriff.
- Die Sicherheits-Leitplanken aus PLAN §3 bleiben erfüllt: serverseitiger
  Zustand entscheidet, Doppel-Bestätigung durch den Leitstand, harter
  Zugriffsentzug, keine Persistenz.
- **Datenschutz besser:** kein per-Schüler-QR, **keine** Namen/PII auf dem iPad
  (O8 = anonym, siehe §3). Kein Generieren/Anzeigen personalisierter QRs nötig.

## 3. Geklärte offene Punkte

- **O8 (iPad zeigt Namen?) → anonym.** Das Display zeigt nur den allgemeinen QR,
  keinerlei Schülerinfo. Konsistent mit dem allgemeinen-QR-Modell.
- **O6 (nicht bezahlt) → anzeigen + Leitstand entscheidet.** Beim Pairing eines
  nicht bezahlten Schülers antwortet `/api/student/pair` mit `409`
  (`reason:"unpaid"` + offener Betrag). Der Leitstand kann mit
  `override_payment:true` **trotzdem freigeben** (Befreiung/Ermäßigung). Die
  Schüler-UI zeigt dann ein „Vom Betreuer freigegeben"-/„nicht bezahlt"-Banner.
  Endgültige fachliche Abstimmung mit Hr. Pühn steht noch aus.
- **Modus-Schaltung:** Modus B als **eigene Karte** im Leitstand neben der
  Helfer-Karte (Modus A); Klasse/Queue werden geteilt.

## 4. Architektur (Datenfluss)

```text
iPad (qr-display.html)        Schüler-Handy (student.html)        Leitstand (Cookie-Auth)
   │ WS /ws/display              │ POST /api/student/join            │ POST /api/modus-b/open
   │ Reg-Code → Leitstand        │   → {session_token, code}         │   → join_secret + QR
   │ ← allgemeiner QR            │ WS /ws/student/{session_token}    │ POST /api/display/authorize
   │ (nur QR, anonym)            │ zeigt 4-stelligen Code  ──────────┤ POST /api/student/pair
                                 │ ← (nach Pairing) Schülerinfo      │   (Code → Schüler, O6-Override)
                                 │ Scan → staged (kein Submit)       │ finish/skip → harte Invalidierung
```

**Wiederverwendung (kein Neubau):** Playwright-Write-Pfad
(`automation/worker.py`, staged), IServ-Reads (`get_student_info`), Scan→Worker-
Logik (gemeinsamer `handle_scan` für `/ws/scanner` **und** `/ws/student`),
QR-Erzeugung (`qrcode`), Scanner-Frontend (`scan.html` → `student.html`).

**Nebenbei behoben:** Worker-Contexts wurden bei finish/skip bisher nur
geschlossen, nie in den Pool zurückgegeben (Pool schrumpfte). Jetzt
`release_worker()` → `WorkerPool.release()` für Modus A **und** B (relevant für
den 5-parallele-Sessions-Lasttest). Verifiziert: zwei E2E-Modus-B-Läufe
hintereinander auf demselben Pool laufen grün.

## 5. Sicherheits-Review — Token-Lebenszyklus

| Artefakt | Rolle | Eigenschaften |
|----------|-------|---------------|
| `session_token` | **einziger** Daten-Zugang | `secrets.token_urlsafe(32)` (~256 bit), opakes Handle, RAM-only |
| `pairing_code` | Zuordnung am Leitstand | 4 Ziffern, nur in `pending_pairing` gültig, unter aktiven Sessions eindeutig (Kollision → neu würfeln), nach Bindung entwertet — **nie** Datenzugriff |
| `join_secret` | gatet Session-Erzeugung | im allgemeinen QR; vom Leitstand rotierbar/schließbar |
| `registration_code` | iPad-Display-Freischaltung | 4 Zeichen, am Leitstand bestätigt |

**Lebenszyklus:** `pending_pairing` → `paired` → `completed` | `expired` |
`revoked`. Übergang nach `paired` **nur** durch Leitstand-Bestätigung
(Doppel-Bestätigung, PLAN §3.3).

**Geprüfte Eigenschaften (E2E `automation/e2e_modus_b.py`):**

1. **Kein Datenabfluss vor Pairing:** Vor `paired` erhält weder iPad noch
   Schüler-Browser Schülerdaten; der Leitstand sieht nur „Offene Codes: N"
   (kein Name). ✔
2. **Harter Zugriffsentzug (PLAN §3.2):** finish/skip/close/Timeout entwerten
   den Token, schließen den Worker-Context und die Schüler-WS (Close-Code 4006);
   erneuter Aufruf mit altem Token → neutrale „Vorgang abgeschlossen"-Seite,
   keine Daten. ✔ (verifiziert inkl. Reconnect mit totem Token)
3. **Eindeutiger Close-Code:** Server `accept()` **vor** `close(4006)`, damit der
   Browser den Code zuverlässig erhält (sonst nur generisches 1006 → Token-Tod
   nicht erkennbar). ✔
4. **Server-autoritativ:** Gültigkeit entscheidet allein der Server-Zustand;
   Clients halten nur opake Tokens.
5. **Pairing nur durch Leitstand:** `/api/student/pair` erfordert Leitstand-
   Cookie → kein externes Zu-binden möglich.
6. **Read-only/keine Buchung:** Scan bleibt `staged` (kein Enter), Worker nutzt
   ausschließlich den read-only Admin-Account (CLAUDE.md / PLAN §6). ✔
7. **Keine Persistenz / keine Namen in Logs:** Sessions nur im RAM; Logs nur
   Token-Präfix/Code/`student_id` (PLAN §3.7). ✔

**Bekannte Restpunkte (Pilot-Akzeptanz, nicht in diesem Schritt):**

- ~~Kein Rate-Limit auf `/api/student/join`~~ **Erledigt (2026-06-15):** pro-IP
  Drossel (5 Anfragen / 10 s, `server/ratelimit.py`) vor jeder Prüfung; zusätzlich
  weiterhin `join_secret` nötig + Sweeper. End-to-End-Drosselung noch im Lasttest
  zu bestätigen (`docs/test_status.md`).
- WLAN-Client-Isolation (O9) weiter offen — vor Ort verifizieren.
- 4-stelliger Code-Raum (10 000) reicht für Klassengröße; bei sehr vielen
  gleichzeitig Wartenden ggf. 5-stellig.

## 6. Tests

- `automation/e2e_modus_b.py` — voller Flow open → Display-Registrierung/
  -Autorisierung → join → Pairing (inkl. O6-Override-Fallback) → Bestellliste →
  Scan `staged` → finish → harte Invalidierung → close. **Bestanden.**
- Regression: `automation/e2e_smoke.py` (Modus A) und `automation/e2e_parallel.py`
  weiter **grün**.

## 7. Offen / nächste Schritte

- Lasttest 5 parallele Schüler-Sessions (PLAN §5 Phase 4) — Worker-Pool ggf.
  `WORKER_CONTEXTS` erhöhen.
- O6 fachlich mit Hr. Pühn finalisieren (Wortlaut/Workflow „nicht bezahlt").
- iPad im geführten Zugriff (iOS-Kiosk) — organisatorisch (PLAN §3.4).
- Rate-Limit `/api/student/join` vor dem Piloten.
- Buchender Pfad weiterhin gesperrt bis Freigabe Niklas + Lukas (PLAN §6).
