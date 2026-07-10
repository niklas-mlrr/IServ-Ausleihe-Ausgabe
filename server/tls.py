"""Selbstsigniertes TLS-Zertifikat erzeugen — via `cryptography` (keine
openssl-Binary nötig, wichtig fürs Windows-Deployment).

Das Zertifikat enthält **SubjectAltName** mit `localhost`, `127.0.0.1` und allen
erkannten lokalen IPv4-Adressen des Laptops. Nur so akzeptieren moderne Browser
(Handys!) die Verbindung über `https://<Laptop-IP>:3443` ohne CN/Host-Mismatch
(sie ignorieren den Common Name und prüfen ausschließlich SAN).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import socket
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

log = logging.getLogger(__name__)


def _parsed_ip(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """`ip` parsen, `None` bei ungültiger Adresse (statt Exception nach oben)."""
    try:
        return ipaddress.ip_address(ip)
    except ValueError:
        return None


def _is_usable_lan_ip(ip: str) -> bool:
    """True, wenn `ip` eine für andere Geräte erreichbare IPv4 ist.

    Filtert Loopback (das ganze `127.0.0.0/8` — Ubuntu/Debian mappen den
    Hostnamen in `/etc/hosts` oft auf `127.0.1.1`!) und Link-Local
    (`169.254.0.0/16`, APIPA ohne DHCP) heraus.
    """
    addr = _parsed_ip(ip)
    if addr is None:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


# Tailscale/CGNAT (RFC 6598) — vom VPN belegt, kein erreichbares Schul-LAN.
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def _is_private_lan_ip(ip: str) -> bool:
    """True nur für echte LAN-Adressen (RFC1918), nicht für CGNAT/Tailscale."""
    addr = _parsed_ip(ip)
    if addr is None:
        return False
    return addr.is_private and not addr.is_link_local and addr not in _CGNAT_NET


def _route_src_ip(target: str) -> str | None:
    """Quell-IPv4, die der Kernel für eine Verbindung zu `target` wählt.

    Es fließt kein echter Traffic (UDP-Connect setzt nur die Route). Mit
    verschiedenen Zielen lassen sich gezielt unterschiedliche Interfaces
    abfragen — z. B. das LAN-Default-Gateway vs. das Tailscale-Netz.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        return None


def _hostname_ipv4s() -> list[str]:
    try:
        return [
            info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        ]
    except Exception:
        return []


def _candidate_ipv4s() -> list[str]:
    """Alle plausiblen lokalen IPv4 — Default-Route, Tailscale, dann Hostname.

    Die Tailscale-Quell-IP taucht sonst nirgends auf: Die Default-Route zeigt
    auf einem VPS ins öffentliche Netz, und `gethostname()` löst meist nicht auf
    die `100.x` auf. Ein UDP-Connect auf die Tailscale-MagicDNS-IP
    (`100.100.100.100`) liefert dagegen genau die Tailscale-Quell-IP — portabel
    (auch Windows) und ohne Zusatz-Abhängigkeit.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    probes = (
        _route_src_ip("10.255.255.255"),  # LAN/Internet-Default-Route
        _route_src_ip("100.100.100.100"),  # Tailscale (MagicDNS), falls aktiv
    )
    for ip in (*probes, *_hostname_ipv4s()):
        if ip and ip not in seen and _is_usable_lan_ip(ip):
            seen.add(ip)
            ordered.append(ip)
    return ordered


def _ip_rank(ip: str) -> int:
    """Sortier-Priorität (kleiner = besser) für die QR-IP-Auswahl.

    RFC1918-LAN zuerst (echtes Schul-WLAN), dann CGNAT/Tailscale (Remote-Test),
    zuletzt öffentliche/sonstige Adressen.
    """
    addr = _parsed_ip(ip)
    if addr is None:
        return 3
    if _is_private_lan_ip(ip):
        return 0
    if addr in _CGNAT_NET:
        return 1
    return 2


def primary_lan_ip(force_tailscale: bool = False) -> str | None:
    """Beste IPv4 für den QR-Code.

    Standard (Auto): bevorzugt RFC1918-LAN (echtes Schul-WLAN, z. B.
    `192.168.x.x`/`10.x`), dann Tailscale/CGNAT (Remote-Test über VPN), erst
    zuletzt öffentliche IPs. So zeigt der QR auf dem Schullaptop die LAN-IP, auf
    dem VPS aber die erreichbare Tailscale-IP.

    `force_tailscale=True` erzwingt die Tailscale/CGNAT-Adresse (Header-Toggle
    „Tailscale-IP", z. B. um am Schullaptop bewusst über Tailscale zu testen);
    gibt es keine, fällt es auf die Auto-Reihenfolge zurück.
    """
    candidates = _candidate_ipv4s()
    if not candidates:
        return None
    if force_tailscale:
        tailscale = [ip for ip in candidates if _ip_rank(ip) == 1]
        if tailscale:
            return tailscale[0]
    # Stabile Sortierung nach Rang; bei Gleichstand bleibt die Erkennungs-
    # Reihenfolge (Default-Route vor Tailscale) erhalten.
    return min(candidates, key=lambda ip: (_ip_rank(ip), candidates.index(ip)))


def _local_ipv4s() -> list[str]:
    """Alle nutzbaren lokalen IPv4-Adressen des Hosts ermitteln (für SAN)."""
    return sorted(set(_candidate_ipv4s()))


def _cert_expired_or_expiring(cert_path: Path, *, within_days: int = 30) -> bool:
    """True, wenn das Zertifikat abgelaufen ist oder innerhalb `within_days`
    Tagen ausläuft. Bei Lesefehlern konservativ True (→ neu erzeugen), damit
    ein kaputtes Cert nicht still stehen bleibt."""
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except Exception:
        return True
    # `not_valid_after_utc` (neu in cryptography>=42); auf älteren Versionen
    # fällt der naive `not_valid_after` zurück — wir normalisieren auf UTC.
    try:
        expiry = cert.not_valid_after_utc
    except AttributeError:
        expiry = cert.not_valid_after.replace(tzinfo=dt.UTC)
    now = dt.datetime.now(dt.UTC)
    return expiry <= now + dt.timedelta(days=within_days)


def generate_selfsigned_cert(cert_path: Path, key_path: Path, cn: str = "localhost") -> None:
    """Cert + Key erzeugen. Bestehende Dateien werden nur dann überschrieben,
    wenn das Zertifikat abgelaufen ist oder innerhalb ~30 Tagen ausläuft
    (vorher wurde ungeachtet des Ablaufs immer frühzeitig returned)."""
    if cert_path.exists() and key_path.exists():
        if not _cert_expired_or_expiring(cert_path):
            return
        log.info(
            "TLS-Zertifikat läuft bald ab oder ist abgelaufen — wird neu erzeugt: %s", cert_path
        )
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    san: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ]
    local_ips = _local_ipv4s()
    for ip in local_ips:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    if cn and cn not in ("localhost",):
        san.append(x509.DNSName(cn))  # z. B. die IServ-Domain als zusätzlicher Name

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))  # max. von vielen Clients akzeptiert
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )

    # Privaten Schlüssel direkt mit 0o600 anlegen (statt Default-umask oft
    # 0o644 + nachträglich chmod 600 — das schließt das world-readable-Fenster).
    # Windows ignoriert die Mode-Bits i. d. R., schadet dort aber nicht.
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_fd = os.open(
        str(key_path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    with os.fdopen(key_fd, "wb") as fh:
        fh.write(key_pem)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    sans = ["localhost", "127.0.0.1", *local_ips] + ([cn] if cn != "localhost" else [])
    log.info("TLS-Zertifikat erzeugt (SAN: %s): %s", ", ".join(sans), cert_path)
    print(f"TLS-Zertifikat erzeugt (SAN: {', '.join(sans)}): {cert_path}")
