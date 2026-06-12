from __future__ import annotations

import subprocess
from pathlib import Path


def generate_selfsigned_cert(cert_path: Path, key_path: Path, cn: str = "localhost") -> None:
    """Selbstsigniertes TLS-Zertifikat via openssl erzeugen (einmalig beim Start)."""
    if cert_path.exists() and key_path.exists():
        return
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl", "req", "-x509",
            "-newkey", "rsa:2048", "-nodes",
            "-days", "365",
            "-subj", f"/CN={cn}",
            "-keyout", str(key_path),
            "-out", str(cert_path),
        ],
        check=True,
        capture_output=True,
    )
    print(f"TLS-Zertifikat erzeugt: {cert_path}")
