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
        dq = self._hits[key]
        cutoff = now - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            # Leere Deques nicht im Dict halten (kein unbegrenztes Wachstum).
            self._hits.pop(key, None)
            dq = self._hits[key]
        if len(dq) >= self._max:
            return False
        dq.append(now)
        return True


# Modul-Level-Instanz für /api/student/join.
join_limiter = SlidingWindowLimiter(max_hits=5, window_s=10.0)
