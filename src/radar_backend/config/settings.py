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


def frontend_base_url() -> str:
    return _required("FRONTEND_BASE_URL").rstrip("/")


def smtp_host() -> str:
    return _required("SMTP_HOST")


def smtp_port() -> int:
    raw_value = _required("SMTP_PORT")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("SMTP_PORT must be an integer") from exc
    if value <= 0:
        raise ValueError("SMTP_PORT must be positive")
    return value


def smtp_username() -> str:
    return _required("SMTP_USERNAME")


def smtp_password() -> str:
    return _required("SMTP_PASSWORD")


def smtp_use_tls() -> bool:
    value = os.getenv("SMTP_USE_TLS", "true").strip().lower()
    if value in {"true", "1", "yes", "y", "on"}:
        return True
    if value in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError("SMTP_USE_TLS must be true or false")


def from_email() -> str:
    return _required("FROM_EMAIL")


def from_name() -> str:
    return _required("FROM_NAME")


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
