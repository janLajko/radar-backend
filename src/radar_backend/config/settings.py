from __future__ import annotations

import os
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


def database_dsn_radar() -> str:
    return _required("DATABASE_DSN_RADAR")


def worker_poll_interval_seconds() -> int:
    value = _int("WORKER_POLL_INTERVAL_SECONDS", 300)
    if value <= 0:
        raise ValueError("WORKER_POLL_INTERVAL_SECONDS must be positive")
    return value


def log_level() -> str:
    value = os.getenv("LOG_LEVEL", "INFO")
    if value.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
    return value


def lark_webhook_url() -> str:
    return os.getenv("LARK_WEBHOOK_URL", "").strip()


def _required(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")
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
