from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_outer_quotes(value.strip())
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_dsn_radar: str
    source_config_path: str
    llm_api_key: str
    anthropic_api_key: str = ""
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    policy_update_llm_model: str | None = None
    policy_impact_llm_model: str | None = None
    worker_poll_interval_seconds: int = 300
    db_pool_min_size: int = 10
    db_pool_max_size: int = 50
    db_pool_timeout_seconds: float = 60
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        database_dsn_radar = _required("DATABASE_DSN_RADAR")
        source_config_path = _required("SOURCE_CONFIG_PATH")
        return cls(
            database_dsn_radar=database_dsn_radar,
            source_config_path=source_config_path,
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            llm_provider=os.getenv("LLM_PROVIDER", "openai"),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o"),
            policy_update_llm_model=_optional("POLICY_UPDATE_LLM_MODEL"),
            policy_impact_llm_model=_optional("POLICY_IMPACT_LLM_MODEL"),
            worker_poll_interval_seconds=_int("WORKER_POLL_INTERVAL_SECONDS", 300),
            db_pool_min_size=_int("DB_POOL_MIN_SIZE", 10),
            db_pool_max_size=_int("DB_POOL_MAX_SIZE", 50),
            db_pool_timeout_seconds=_float("DB_POOL_TIMEOUT_SECONDS", 60),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> None:
        provider = self.llm_provider.lower()
        if provider == "openai" and not self.llm_api_key.strip():
            raise ValueError("LLM_API_KEY is required for openai provider")
        if provider in {"anthropic", "claude"} and not (
            self.anthropic_api_key.strip() or self.llm_api_key.strip()
        ):
            raise ValueError("ANTHROPIC_API_KEY or LLM_API_KEY is required for claude provider")
        if provider not in {"openai", "anthropic", "claude"}:
            raise ValueError(f"unsupported LLM_PROVIDER: {self.llm_provider!r}")
        if self.worker_poll_interval_seconds <= 0:
            raise ValueError("WORKER_POLL_INTERVAL_SECONDS must be positive")
        if self.db_pool_min_size < 0:
            raise ValueError("DB_POOL_MIN_SIZE must be zero or positive")
        if self.db_pool_max_size <= 0:
            raise ValueError("DB_POOL_MAX_SIZE must be positive")
        if self.db_pool_min_size > self.db_pool_max_size:
            raise ValueError("DB_POOL_MIN_SIZE cannot exceed DB_POOL_MAX_SIZE")
        if self.db_pool_timeout_seconds <= 0:
            raise ValueError("DB_POOL_TIMEOUT_SECONDS must be positive")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def _optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    return value


def _strip_outer_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
