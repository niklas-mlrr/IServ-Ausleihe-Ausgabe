"""Safe Uvicorn process defaults."""

from __future__ import annotations

import server.main as main
from server.config import Config


def test_main_disables_url_bearing_access_log(monkeypatch):
    cfg = Config(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
    )
    seen = {}
    monkeypatch.setattr(main, "load_config", lambda: cfg)
    monkeypatch.setattr(main, "generate_selfsigned_cert", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_add_file_logging", lambda _path: None)
    monkeypatch.setattr(main.uvicorn, "run", lambda *args, **kwargs: seen.update(kwargs))

    main.main()

    assert seen["access_log"] is False
    assert seen["workers"] == 1
