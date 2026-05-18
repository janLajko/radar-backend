from __future__ import annotations

from pathlib import Path

import pytest

from radar_backend import config


def test_config_functions_load_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://example/test")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LARK_WEBHOOK_URL", " https://example.test/lark ")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://example.test/app/")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret value")
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("FROM_EMAIL", "sender@example.test")
    monkeypatch.setenv("FROM_NAME", "Gingercontrol")

    assert config.database_dsn_radar() == "postgresql://example/test"
    assert config.worker_poll_interval_seconds() == 60
    assert config.log_level() == "DEBUG"
    assert config.lark_webhook_url() == "https://example.test/lark"
    assert config.frontend_base_url() == "https://example.test/app"
    assert config.smtp_host() == "smtp.example.test"
    assert config.smtp_port() == 2525
    assert config.smtp_username() == "sender@example.test"
    assert config.smtp_password() == "secret value"
    assert config.smtp_use_tls() is False
    assert config.from_email() == "sender@example.test"
    assert config.from_name() == "Gingercontrol"


def test_database_dsn_radar_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_DSN_RADAR", raising=False)

    with pytest.raises(ValueError, match="DATABASE_DSN_RADAR is required"):
        config.database_dsn_radar()


def test_log_level_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        config.log_level()


def test_worker_poll_interval_rejects_non_positive_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0")

    with pytest.raises(ValueError, match="WORKER_POLL_INTERVAL_SECONDS"):
        config.worker_poll_interval_seconds()


def test_smtp_use_tls_rejects_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_USE_TLS", "maybe")

    with pytest.raises(ValueError, match="SMTP_USE_TLS"):
        config.smtp_use_tls()


def test_smtp_port_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMTP_PORT", raising=False)

    with pytest.raises(ValueError, match="SMTP_PORT is required"):
        config.smtp_port()


def test_load_dotenv_does_not_override_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_DSN_RADAR=postgresql://from-file/db\nLOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://from-env/db")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    config.load_dotenv(env_file)

    assert config.database_dsn_radar() == "postgresql://from-env/db"
    assert config.log_level() == "DEBUG"
