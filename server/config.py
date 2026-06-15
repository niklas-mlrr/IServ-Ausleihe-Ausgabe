from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    iserv_domain: str
    iserv_username: str
    iserv_password: str
    leitstand_password: str
    port: int = 3443
    worker_contexts: int = 2
    tls_cert: Path = field(default_factory=lambda: Path("certs/server.crt"))
    tls_key: Path = field(default_factory=lambda: Path("certs/server.key"))
    # Modus B: harte Zugriffsentzug-Schwellen (Sekunden).
    pending_pairing_ttl_s: int = 300   # QR gescannt, aber nicht gepairt → verfällt
    paired_idle_ttl_s: int = 900       # gepairt, aber inaktiv → verfällt


_config: Config | None = None


def load_config(env_file: Path | None = None) -> Config:
    global _config
    load_dotenv(env_file or Path(__file__).parent.parent / ".env")

    def req(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            raise SystemExit(f"Fehler: {name} fehlt in .env — .env.example als Vorlage nutzen.")
        return v

    _config = Config(
        iserv_domain=req("ISERV_DOMAIN"),
        iserv_username=req("ISERV_USERNAME"),
        iserv_password=req("ISERV_PASSWORD"),
        leitstand_password=req("LEITSTAND_PASSWORD"),
        port=int(os.environ.get("PORT", "3443")),
        worker_contexts=int(os.environ.get("WORKER_CONTEXTS", "2")),
    )
    return _config


def get_config() -> Config:
    if _config is None:
        return load_config()
    return _config
