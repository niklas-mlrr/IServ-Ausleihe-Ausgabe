"""Kleiner in-memory Rate-Limiter (sliding window, pro Schlüssel z. B. IP).

Bewusst dependency-frei und leichtgewichtig — passt zum Schul-WLAN-Betrieb
(direkte Verbindungen, kein Proxy). Schützt `POST /api/student/join` davor, dass
jemand beliebig viele pending-Sessions erzeugt (DoS, PLAN §3 / Phase-4-Review).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """Erlaubt max. `max_hits` Treffer pro `window_s` und Schlüssel."""

    def __init__(self, max_hits: int = 5, window_s: float = 10.0) -> None:
        self._max = max_hits
        self._window = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        """Treffer registrieren. True = erlaubt, False = drosseln (Limit erreicht)."""
        now = time.monotonic()
        # .get() statt defaultdict-Zugriff: ein `self._hits[key]` würde bei jedem
        # Lookup einen leeren Bucket anlegen, der nie wieder verschwindet.
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
        cutoff = now - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()
        # Leeren Bucket jetzt wirklich entfernen — wird unten nur bei einem
        # neuen Hit wieder angelegt (via self._hits[key] = dq).
        if not dq:
            self._hits.pop(key, None)
        if len(dq) >= self._max:
            return False
        dq.append(now)
        self._hits[key] = dq
        return True

    def sweep(self) -> None:
        """Veraltete/leere Einträge entfernen (gegen Wachstum bei vielen distinkten
        IPs, die je nur einmal anfragen). Periodisch aus dem Session-Sweeper gerufen."""
        cutoff = time.monotonic() - self._window
        for key in list(self._hits.keys()):
            dq = self._hits[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if not dq:
                del self._hits[key]


# Modul-Level-Instanz für /api/student/join.
join_limiter = SlidingWindowLimiter(max_hits=5, window_s=10.0)

# Modul-Level-Instanz für /api/login (pro-IP). Engere Fenster als join — ein
# Brute-Force-Anlauf gegen das Host-Passwort soll schnell gedrosselt werden,
# ohne legitime Vertipper zu blocken (5 Versuche / 15 s).
login_limiter = SlidingWindowLimiter(max_hits=5, window_s=15.0)
