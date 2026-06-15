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


def _local_ipv4s() -> list[str]:
    """Lokale IPv4-Adressen des Hosts ermitteln (für SAN)."""
    ips: set[str] = set()
    # Primäre ausgehende IP (kein echter Verbindungsaufbau durch UDP-Socket).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    # Zusätzlich alle über den Hostnamen auflösbaren IPv4.
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.discard("127.0.0.1")
    return sorted(ips)


def generate_selfsigned_cert(cert_path: Path, key_path: Path, cn: str = "localhost") -> None:
    """Cert + Key erzeugen (einmalig). Bestehende Dateien werden nicht überschrieben."""
    if cert_path.exists() and key_path.exists():
        return
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
    now = dt.datetime.now(dt.timezone.utc)
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
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # Privaten Schlüssel restriktiv setzen (best effort; Windows ignoriert das ggf.).
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass

    sans = ["localhost", "127.0.0.1", *local_ips] + ([cn] if cn != "localhost" else [])
    log.info("TLS-Zertifikat erzeugt (SAN: %s): %s", ", ".join(sans), cert_path)
    print(f"TLS-Zertifikat erzeugt (SAN: {', '.join(sans)}): {cert_path}")
