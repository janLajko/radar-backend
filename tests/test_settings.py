from __future__ import annotations

from pathlib import Path

import pytest

from radar_backend.config import Settings, load_dotenv


def test_settings_loads_required_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://example/test")
    monkeypatch.setenv("SOURCE_CONFIG_PATH", "/etc/radar/sources.yaml")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("POLICY_UPDATE_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("POLICY_IMPACT_LLM_MODEL", "gpt-4.1")
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "1")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "3")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings.from_env()

    assert settings.database_dsn_radar == "postgresql://example/test"
    assert settings.source_config_path == "/etc/radar/sources.yaml"
    assert settings.llm_api_key == "sk-test"
    assert settings.llm_provider == "openai"
    assert settings.llm_model == "gpt-4o"
    assert settings.policy_update_llm_model == "gpt-4o-mini"
    assert settings.policy_impact_llm_model == "gpt-4.1"
    assert settings.worker_poll_interval_seconds == 60
    assert settings.db_pool_min_size == 1
    assert settings.db_pool_max_size == 3
    assert settings.db_pool_timeout_seconds == 4.5
    assert settings.log_level == "DEBUG"


def test_settings_requires_database_dsn_radar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_DSN_RADAR", raising=False)

    with pytest.raises(ValueError, match="DATABASE_DSN_RADAR is required"):
        Settings.from_env()


def test_settings_rejects_invalid_log_level() -> None:
    settings = Settings(
        database_dsn_radar="postgresql://example/test",
        source_config_path="/etc/radar/sources.yaml",
        llm_api_key="sk-test",
        log_level="LOUD",
    )

    with pytest.raises(ValueError, match="LOG_LEVEL"):
        settings.validate()


def test_load_dotenv_does_not_override_existing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_DSN_RADAR=postgresql://from-file/db\nLOG_LEVEL=DEBUG\n")
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://from-env/db")
    monkeypatch.setenv("SOURCE_CONFIG_PATH", "/etc/radar/sources.yaml")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    load_dotenv(env_file)

    assert Settings.from_env().database_dsn_radar == "postgresql://from-env/db"
    assert Settings.from_env().log_level == "DEBUG"


def test_settings_uses_production_pool_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_DSN_RADAR", "postgresql://example/test")
    monkeypatch.setenv("SOURCE_CONFIG_PATH", "/etc/radar/sources.yaml")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.delenv("DB_POOL_MIN_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("DB_POOL_TIMEOUT_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.db_pool_min_size == 10
    assert settings.db_pool_max_size == 50
    assert settings.db_pool_timeout_seconds == 60
