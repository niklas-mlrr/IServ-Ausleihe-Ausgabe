"""Unit-Tests für WorkerPool.stats() (automation/worker.py).

Reine Buchhaltung — kein Browser-Start, kein IServ.
"""

from __future__ import annotations

from automation.worker import WorkerPool


def test_stats_empty_pool():
    p = WorkerPool(n=3, domain="d", username="u", password="p")
    assert p.stats() == {"total": 0, "available": 0, "in_use": 0}


def test_stats_tracks_checkout():
    p = WorkerPool(n=3, domain="d", username="u", password="p")
    p._contexts = ["a", "b", "c"]
    p._total = 3
    assert p.stats() == {"total": 3, "available": 3, "in_use": 0}
    p._contexts.pop()  # ein Context ausgecheckt (open_student)
    assert p.stats() == {"total": 3, "available": 2, "in_use": 1}
