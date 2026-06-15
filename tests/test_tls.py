"""Unit-Tests für die TLS-Cert-Erzeugung (server/tls.py).

Stellt sicher, dass das Zertifikat SubjectAltName enthält (sonst lehnen Handys die
Verbindung über die LAN-IP ab) und dass ein zweiter Aufruf nicht überschreibt.
Kein Netzwerk, kein openssl-Binary.
"""

from __future__ import annotations

from cryptography import x509

from server.tls import generate_selfsigned_cert


def test_cert_has_san(tmp_path):
    crt, key = tmp_path / "c.crt", tmp_path / "c.key"
    generate_selfsigned_cert(crt, key, cn="example.test")
    cert = x509.load_pem_x509_certificate(crt.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    names = [str(g.value) for g in san]
    assert "localhost" in names
    assert "127.0.0.1" in names
    assert "example.test" in names  # cn landet zusätzlich als DNS-SAN


def test_cert_is_idempotent(tmp_path):
    crt, key = tmp_path / "c.crt", tmp_path / "c.key"
    generate_selfsigned_cert(crt, key)
    mtime = crt.stat().st_mtime
    generate_selfsigned_cert(crt, key)  # zweiter Aufruf darf nicht überschreiben
    assert crt.stat().st_mtime == mtime
