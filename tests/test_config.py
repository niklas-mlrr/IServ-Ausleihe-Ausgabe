"""Configuration validation and CWD-independent path regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

import server.config as config


def _credentials(monkeypatch) -> None:
    monkeypatch.setenv("ISERV_DOMAIN", "example.org")
    monkeypatch.setenv("ISERV_USERNAME", "user")
    monkeypatch.setenv("ISERV_PASSWORD", "password")
    monkeypatch.setenv("HOST_PASSWORD", "host")


def test_config_resolves_relative_paths_against_project_root(monkeypatch):
    _credentials(monkeypatch)
    monkeypatch.setenv("PRINT_OUTPUT_DIR", "tmp/slips")
    monkeypatch.setenv("TLS_CERT", "tmp/cert.pem")

    cfg = config.load_config(Path("/definitely/not/a/dotenv"))

    assert cfg.print_output_dir == config.PROJECT_ROOT / "tmp/slips"
    assert cfg.tls_cert == config.PROJECT_ROOT / "tmp/cert.pem"


@pytest.mark.parametrize(
    ("name", "value"),
    [("PORT", "0"), ("WORKER_CONTEXTS", "0"), ("PENDING_PAIRING_TTL_S", "-1")],
)
def test_config_rejects_unsafe_numeric_values(monkeypatch, name, value):
    _credentials(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit):
        config.load_config(Path("/definitely/not/a/dotenv"))


def test_config_rejects_unknown_print_backend(monkeypatch):
    _credentials(monkeypatch)
    monkeypatch.setenv("PRINT_BACKEND", "paper-airplane")

    with pytest.raises(SystemExit, match="PRINT_BACKEND"):
        config.load_config(Path("/definitely/not/a/dotenv"))
