from __future__ import annotations

from pathlib import Path

import pytest

from radar_backend.config import Settings, load_dotenv


def test_settings_loads_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://example/test")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings.from_env()

    assert settings.database_dsn_radar == "postgresql://example/test"
    assert settings.worker_poll_interval_seconds == 60
    assert settings.log_level == "DEBUG"


def test_settings_requires_database_dsn_radar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_DSN_RADAR", raising=False)

    with pytest.raises(ValueError, match="DATABASE_DSN_RADAR is required"):
        Settings.from_env()


def test_settings_rejects_invalid_log_level() -> None:
    settings = Settings(database_dsn_radar="postgresql://example/test", log_level="LOUD")

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        settings.validate()


def test_settings_rejects_non_positive_poll_interval() -> None:
    settings = Settings(
        database_dsn_radar="postgresql://example/test",
        worker_poll_interval_seconds=0,
    )

    with pytest.raises(ValueError, match="WORKER_POLL_INTERVAL_SECONDS"):
        settings.validate()


def test_load_dotenv_does_not_override_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_DSN_RADAR=postgresql://from-file/db\nLOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://from-env/db")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    load_dotenv(env_file)
    settings = Settings.from_env()

    assert settings.database_dsn_radar == "postgresql://from-env/db"
    assert settings.log_level == "DEBUG"
