"""Einstiegspunkt: Server starten.

Aufruf: uv run python -m server.main
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from .config import load_config
from .tls import generate_selfsigned_cert

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")


def _add_file_logging(logs_dir: Path) -> None:
    """Rotierendes Logfile unter logs/ ergänzen (zusätzlich zu stdout).

    Hinweis: KEINE Schülernamen loggen (PLAN §3.7) — Buch-Codes/IDs ja, Namen nein.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "server.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
    logging.getLogger().addHandler(handler)


def main() -> None:
    cfg = load_config()
    _add_file_logging(cfg.log_dir)
    generate_selfsigned_cert(cfg.tls_cert, cfg.tls_key, cn=cfg.iserv_domain)

    print(f"\nHost: https://localhost:{cfg.port}/host")
    print(f"Scanner:   https://<IP>:{cfg.port}/scan?token=<TOKEN>\n")

    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=cfg.port,
        ssl_keyfile=str(cfg.tls_key),
        ssl_certfile=str(cfg.tls_cert),
        reload=False,
        # Runtime state and WebSocket registries are deliberately in-memory;
        # multiple Uvicorn workers would create isolated queues/sessions.
        workers=1,
        # Scanner/student bearer tokens are URL path/query values. Uvicorn's
        # default access log contains the full request target and would leak
        # those credentials to stdout and the rotating application log.
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
