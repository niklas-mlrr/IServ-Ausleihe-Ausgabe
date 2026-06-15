# Rückbau-Plan — VORLAGE (vor jedem buchenden Testlauf ausfüllen)

> **Pflicht laut PLAN §6 und CLAUDE.md.** Diese Datei ist die Vorlage. Für jeden
> realen Buchungstest eine **Kopie** anlegen:
> `docs/rueckbau/YYYY-MM-DD_<kurz>.md` und dort ausfüllen + abzeichnen.
> Ohne ausgefüllten, von **Niklas UND Lukas** freigegebenen Plan wird **keine**
> Buchung gegen die Produktion ausgelöst.

---

## 0. Freigabe (Voraussetzung — ohne dies kein Testlauf)

| Feld | Wert |
|------|------|
| Datum / Uhrzeit | ____ |
| Durchführende(r) | ____ |
| Niklas anwesend & einverstanden | ☐ ja |
| Lukas anwesend & einverstanden | ☐ ja |
| Freigabe gilt nur für genau diesen Lauf | ☐ bestätigt |

> Freigabe ist **einmalig und einzelfallbezogen** — gilt nicht für spätere Läufe.

## 1. Rahmen & Sicherheitscheck

| Punkt | Soll | Bestätigt |
|-------|------|-----------|
| Testschüler | **Niklas Müller** (eigener Account), student_id `____` | ☐ |
| Bücher | **ausschließlich ausgemusterte** Bücher | ☐ |
| `ausleihe-api` read-only | `allow_writes=False` (Default) — **unverändert** | ☐ |
| Write-Pfad | nur offizielles IServ-Frontend via Playwright | ☐ |
| Beaufsichtigung | Lauf wird **nicht** unbeaufsichtigt/automatisch gestartet | ☐ |
| Keine Schülernamen in Logs (Buch-Codes ja) | PLAN §3.7 | ☐ |

## 2. Ausgangszustand (VORHER dokumentieren — read-only)

Bücher des Testschülers vor dem Test (read-only Snapshot):

| Buch-Code | Titel/ISBN | Status |
|-----------|-----------|--------|
| ____ | ____ | ____ |

> Snapshot-Quelle (read-only): `GET /students/<id>/books` bzw.
> `IsServClient.get_student_info(<id>)` → Feld `current_books`.
> Snapshot **vor** dem Test sichern (Datei/Screenshot): ____

## 3. Geplante Aktionen (Schritt für Schritt)

| # | Aktion | Buch-Code (ausgemustert!) | Erwartung |
|---|--------|---------------------------|-----------|
| 1 | Ausgabe (Enter im Counter-Feld) | ____ | Buch erscheint in Schülerkartei |
| 2 | Ergebnis aus DOM prüfen | — | Erfolg/Fehler korrekt erkannt (O3) |
| 3 | **Sofort** Rücknahme | ____ | Buch wieder entfernt |

> Pro ausgegebenem Buch gilt: **unmittelbar danach zurücknehmen.** Keine
> Test-Ausleihe über das Ende des Laufs hinaus bestehen lassen.

## 4. Rückbau / Rücknahme (wie genau rückgängig machen)

- Rücknahme **über das offizielle IServ-Frontend** (Counter-Seite →
  Buch erneut scannen/„zurücknehmen", bzw. „Bücher zurücknehmen"-Funktion).
- Pro Buch aus Schritt 3 einzeln zurücknehmen und im Frontend bestätigen.
- Verantwortlich für Rückbau: ____

## 5. Verifikation (NACHHER — read-only)

| Prüfung | Ergebnis |
|---------|----------|
| `GET /students/<id>/books` nach Test == Ausgangszustand (§2) | ☐ identisch |
| Keine offene Test-Ausleihe verblieben | ☐ bestätigt |
| Verifiziert durch (Name) | ____ |
| Verifiziert am (Datum/Uhrzeit) | ____ |

## 6. Abbruch- / Notfallplan

- Bei **unerwartetem Verhalten** (falscher Schüler, echtes statt ausgemustertes
  Buch, unklarer Status): **sofort stoppen**, nichts weiter buchen.
- Manuell im offiziellen Frontend prüfen; **Lukas (Admin)** bereinigt einen
  hängengebliebenen Zustand.
- Vorfall hier kurz protokollieren: ____

## 7. Sign-off

| Rolle | Name | Bestätigt (erledigt + verifiziert) |
|-------|------|-----------------------------------|
| Durchführung | ____ | ☐ |
| Gegenprüfung | ____ | ☐ |

---

### Anhang: technische Voraussetzung (Code, erst NACH Freigabe aktiv)

Der buchende Pfad in `automation/worker.py` (`submit_barcode()` mit **Enter**
statt nur `fill()`, plus Fehlerauslesen aus dem DOM) ist bis zur Freigabe
**gesperrt**. Reihenfolge am Testtag:
1. Diese Plan-Kopie ausfüllen + beides Sign-off (Niklas + Lukas).
2. Ausgangszustand-Snapshot (§2) sichern.
3. Erst dann den freigegebenen Enter-Pfad für **einen** Buch-Code aktivieren.
4. Sofort §3.3 Rücknahme + §5 Verifikation.
