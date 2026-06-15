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


def _add_file_logging() -> None:
    """Rotierendes Logfile unter logs/ ergänzen (zusätzlich zu stdout).

    Hinweis: KEINE Schülernamen loggen (PLAN §3.7) — Buch-Codes/IDs ja, Namen nein.
    """
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / "server.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
    logging.getLogger().addHandler(handler)


def main() -> None:
    _add_file_logging()
    cfg = load_config()
    generate_selfsigned_cert(cfg.tls_cert, cfg.tls_key, cn=cfg.iserv_domain)

    print(f"\nLeitstand: https://localhost:{cfg.port}/leitstand.html")
    print(f"Scanner:   https://<IP>:{cfg.port}/scan.html?token=<TOKEN>\n")

    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=cfg.port,
        ssl_keyfile=str(cfg.tls_key),
        ssl_certfile=str(cfg.tls_cert),
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
