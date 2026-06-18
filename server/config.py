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
    host_password: str
    port: int = 3443
    # Erzwingt die LAN-IP in QR-/Join-URLs. Nötig, wenn der Laptop mehrere
    # Interfaces hat (WLAN + VPN/Tailscale/Docker) und die Auto-Erkennung das
    # falsche Netz wählt — Schüler-Handys müssen diese IP erreichen können.
    host_ip: str | None = None
    worker_contexts: int = 2
    # Playwright sichtbar machen (Debug). Default headless. Auf headless-Servern
    # braucht headful ein Display (z. B. xvfb-run).
    headless: bool = True
    slow_mo_ms: int = 0                 # >0 verlangsamt Playwright-Aktionen (Debug)
    tls_cert: Path = field(default_factory=lambda: Path("certs/server.crt"))
    tls_key: Path = field(default_factory=lambda: Path("certs/server.key"))
    # Modus B: harte Zugriffsentzug-Schwellen (Sekunden).
    pending_pairing_ttl_s: int = 300   # QR gescannt, aber nicht gepairt → verfällt
    paired_idle_ttl_s: int = 900       # gepairt, aber inaktiv → verfällt
    # Host-Login: gleitendes Timeout (verlängert sich bei jeder Anfrage).
    host_session_ttl_s: int = 43200    # 12 h — deckt einen Ausgabetag ab
    # Leihschein-Druck (siehe server/printing.py).
    print_backend: str = "auto"        # auto|file|lp|sumatra|win-default
    printer_name: str | None = None    # leer = Standarddrucker
    sumatra_path: str | None = None    # optionaler expliziter SumatraPDF-Pfad
    print_output_dir: Path = field(default_factory=lambda: Path("automation/out/loan_slips"))
    # Buchung gegen die IServ-Produktion (Enter auf der Counter-Seite).
    # Default AUS — Buchung nur nach expliziter Freigabe Niklas + Lukas (PLAN §6).
    allow_booking: bool = False


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
        host_password=req("HOST_PASSWORD"),
        port=int(os.environ.get("PORT", "3443")),
        host_ip=(os.environ.get("HOST_IP", "").strip() or None),
        worker_contexts=int(os.environ.get("WORKER_CONTEXTS", "2")),
        headless=os.environ.get("HEADLESS", "true").strip().lower() not in ("0", "false", "no"),
        slow_mo_ms=int(os.environ.get("SLOW_MO_MS", "0")),
        host_session_ttl_s=int(os.environ.get("HOST_SESSION_TTL_S", "43200")),
        print_backend=os.environ.get("PRINT_BACKEND", "auto").strip() or "auto",
        printer_name=(os.environ.get("PRINTER_NAME", "").strip() or None),
        sumatra_path=(os.environ.get("SUMATRA_PATH", "").strip() or None),
        allow_booking=os.environ.get("ALLOW_BOOKING", "").strip().lower() in ("1", "true", "yes"),
    )
    return _config


def get_config() -> Config:
    if _config is None:
        return load_config()
    return _config
