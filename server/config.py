from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    iserv_domain: str
    iserv_username: str
    iserv_password: str
    host_password: str
    # Read-Timeout für IServ-Requests (Sekunden). Default der ausleihe-api ist
    # 30s — spürbar beim Leihschein-Druck: der PDF-Abruf läuft synchron VOR dem
    # eigentlichen Druck, der Drucker-Slot ist dabei schon belegt (s. print_queue
    # `_claim_fills`/`_dispatch`). Ein kürzerer Timeout lässt eine hängende
    # IServ-Antwort schneller fehlschlagen, statt den Druckauftrag lange
    # unsichtbar warten zu lassen. Der Connect-Timeout bleibt bei 5s.
    iserv_read_timeout_s: float = 10.0
    port: int = 3443
    # Erzwingt die LAN-IP in QR-/Join-URLs. Nötig, wenn der Laptop mehrere
    # Interfaces hat (WLAN + VPN/Tailscale/Docker) und die Auto-Erkennung das
    # falsche Netz wählt — Schüler-Handys müssen diese IP erreichen können.
    host_ip: str | None = None
    worker_contexts: int = 2
    # Playwright sichtbar machen (Debug). Default headless. Auf headless-Servern
    # braucht headful ein Display (z. B. xvfb-run).
    headless: bool = True
    slow_mo_ms: int = 0  # >0 verlangsamt Playwright-Aktionen (Debug)
    tls_cert: Path = field(default_factory=lambda: PROJECT_ROOT / "certs/server.crt")
    tls_key: Path = field(default_factory=lambda: PROJECT_ROOT / "certs/server.key")
    # Modus B: harte Zugriffsentzug-Schwellen (Sekunden).
    pending_pairing_ttl_s: int = 300  # QR gescannt, aber nicht gepairt → verfällt
    # Gepairt, aber GETRENNT (keine WebSocket) → verfällt. Verbundene Sessions
    # verfallen nicht (s. sessions.sweep_expired_sessions). 30 min überbrücken
    # ein zwischendurch ausgeschaltetes Handy, ohne dass eine wirklich
    # abgebrochene Session den Worker-Context ewig hält.
    paired_idle_ttl_s: int = 1800
    # Helfer-Scan-QR („Bücher als Helfer einscannen") ungenutzt → verfällt.
    helper_scan_ttl_s: int = 600
    # Host-Login: gleitendes Timeout (verlängert sich bei jeder Anfrage).
    host_session_ttl_s: int = 43200  # 12 h — deckt einen Ausgabetag ab
    # Leihschein-Druck (siehe server/printing.py).
    print_backend: str = "auto"  # auto|file|lp|sumatra|win-default
    printer_name: str | None = None  # leer = Standarddrucker
    sumatra_path: str | None = None  # optionaler expliziter SumatraPDF-Pfad
    print_output_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT / "automation/out/loan_slips"
    )
    log_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    # Buchung gegen die IServ-Produktion (Enter auf der Counter-Seite).
    # Default AUS — Buchung nur nach expliziter Freigabe Niklas + Lukas (PLAN §6).
    allow_booking: bool = False


_config: Config | None = None


def load_config(env_file: Path | None = None) -> Config:
    global _config
    load_dotenv(env_file or PROJECT_ROOT / ".env")

    def req(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            raise SystemExit(f"Fehler: {name} fehlt in .env — .env.example als Vorlage nutzen.")
        return v

    def req_int(name: str, default: int) -> int:
        """int()-Parse mit sauberer Fehlermeldung (wie req(), nur für Zahlen).

        Raw int() würde bei z. B. PORT=abc mit kryptischem ValueError sterben;
        req_int gibt einen klaren SystemExit wie bei den String-Pflichtfeldern.
        """
        raw = os.environ.get(name, str(default))
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise SystemExit(f"Fehler: {name} muss eine Zahl sein (war '{raw}')") from None

    def path_value(name: str, default: Path) -> Path:
        raw = os.environ.get(name, "").strip()
        path = Path(raw) if raw else default
        return path if path.is_absolute() else PROJECT_ROOT / path

    def positive(name: str, default: int, *, minimum: int = 1) -> int:
        value = req_int(name, default)
        if value < minimum:
            raise SystemExit(f"Fehler: {name} muss mindestens {minimum} sein")
        return value

    def positive_float(name: str, default: float, *, minimum: float = 0.1) -> float:
        raw = os.environ.get(name, str(default))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise SystemExit(f"Fehler: {name} muss eine Zahl sein (war '{raw}')") from None
        if value < minimum:
            raise SystemExit(f"Fehler: {name} muss mindestens {minimum} sein")
        return value

    port = positive("PORT", 3443)
    if port > 65535:
        raise SystemExit("Fehler: PORT muss höchstens 65535 sein")
    backend = os.environ.get("PRINT_BACKEND", "auto").strip() or "auto"
    allowed_backends = {"auto", "file", "lp", "sumatra", "win-default"}
    if backend not in allowed_backends:
        raise SystemExit(
            "Fehler: PRINT_BACKEND muss einer von " + ", ".join(sorted(allowed_backends)) + " sein"
        )
    tls_cert = path_value("TLS_CERT", PROJECT_ROOT / "certs/server.crt")
    tls_key = path_value("TLS_KEY", PROJECT_ROOT / "certs/server.key")
    if tls_cert == tls_key:
        raise SystemExit("Fehler: TLS_CERT und TLS_KEY dürfen nicht identisch sein")

    _config = Config(
        iserv_domain=req("ISERV_DOMAIN"),
        iserv_username=req("ISERV_USERNAME"),
        iserv_password=req("ISERV_PASSWORD"),
        host_password=req("HOST_PASSWORD"),
        iserv_read_timeout_s=positive_float("ISERV_READ_TIMEOUT_S", 10.0),
        port=port,
        host_ip=(os.environ.get("HOST_IP", "").strip() or None),
        worker_contexts=positive("WORKER_CONTEXTS", 2),
        headless=os.environ.get("HEADLESS", "true").strip().lower() not in ("0", "false", "no"),
        slow_mo_ms=positive("SLOW_MO_MS", 0, minimum=0),
        tls_cert=tls_cert,
        tls_key=tls_key,
        pending_pairing_ttl_s=positive("PENDING_PAIRING_TTL_S", 300),
        paired_idle_ttl_s=positive("PAIRED_IDLE_TTL_S", 1800),
        helper_scan_ttl_s=positive("HELPER_SCAN_TTL_S", 600),
        host_session_ttl_s=positive("HOST_SESSION_TTL_S", 43200),
        print_backend=backend,
        printer_name=(os.environ.get("PRINTER_NAME", "").strip() or None),
        sumatra_path=(os.environ.get("SUMATRA_PATH", "").strip() or None),
        print_output_dir=path_value(
            "PRINT_OUTPUT_DIR", PROJECT_ROOT / "automation/out/loan_slips"
        ),
        log_dir=path_value("LOG_DIR", PROJECT_ROOT / "logs"),
        allow_booking=os.environ.get("ALLOW_BOOKING", "").strip().lower() in ("1", "true", "yes"),
    )
    return _config


def get_config() -> Config:
    if _config is None:
        return load_config()
    return _config
