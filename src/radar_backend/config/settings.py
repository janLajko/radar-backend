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
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_dsn_radar: str
    worker_poll_interval_seconds: int = 300
    db_pool_min_size: int = 10
    db_pool_max_size: int = 50
    db_pool_timeout_seconds: float = 60
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        database_dsn_radar = _required("DATABASE_DSN_RADAR")
        return cls(
            database_dsn_radar=database_dsn_radar,
            worker_poll_interval_seconds=_int("WORKER_POLL_INTERVAL_SECONDS", 300),
            db_pool_min_size=_int("DB_POOL_MIN_SIZE", 10),
            db_pool_max_size=_int("DB_POOL_MAX_SIZE", 50),
            db_pool_timeout_seconds=_float("DB_POOL_TIMEOUT_SECONDS", 60),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    def validate(self) -> None:
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
