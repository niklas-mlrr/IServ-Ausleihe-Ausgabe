"""Einstiegspunkt: Server starten.

Aufruf: uv run python -m server.main
"""
from __future__ import annotations

import logging
from pathlib import Path

import uvicorn

from .config import load_config
from .tls import generate_selfsigned_cert

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")


def main() -> None:
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
