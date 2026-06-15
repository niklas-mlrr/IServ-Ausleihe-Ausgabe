"""Unit-Tests für die pro-IP Sliding-Window-Drossel (server/ratelimit.py).

Rein logisch — kein Server, kein Netzwerk.
"""

from __future__ import annotations

import server.ratelimit as rl
from server.ratelimit import SlidingWindowLimiter


def test_allows_then_throttles():
    lim = SlidingWindowLimiter(max_hits=3, window_s=100)
    assert [lim.hit("ip") for _ in range(4)] == [True, True, True, False]


def test_per_key_isolation():
    lim = SlidingWindowLimiter(max_hits=1, window_s=100)
    assert lim.hit("a") is True
    assert lim.hit("a") is False
    assert lim.hit("b") is True  # andere IP unbetroffen


def test_window_expiry(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: t[0])
    lim = SlidingWindowLimiter(max_hits=1, window_s=10)
    assert lim.hit("a") is True
    assert lim.hit("a") is False
    t[0] += 11  # Fenster vorbei
    assert lim.hit("a") is True


def test_sweep_drops_stale(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: t[0])
    lim = SlidingWindowLimiter(max_hits=5, window_s=10)
    lim.hit("a")
    lim.hit("b")
    assert lim._hits  # nicht leer
    t[0] += 20  # beide Buckets veraltet
    lim.sweep()
    assert lim._hits == {}
