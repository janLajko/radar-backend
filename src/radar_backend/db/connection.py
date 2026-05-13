from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg_pool import ConnectionPool

from radar_backend.config import Settings


DB_POOL_NAME = "radar-backend"
DB_POOL_APPLICATION_NAME = "radar-backend"

DB_POOL_MIN_SIZE = 1
DB_POOL_MAX_SIZE = 10
DB_POOL_ACQUIRE_TIMEOUT_SECONDS = 60
DB_POOL_CONNECT_TIMEOUT_SECONDS = 10
DB_POOL_MAX_WAITING = 10

_pool: ConnectionPool[Connection] | None = None


def open_pool(settings: Settings) -> None:
    global _pool

    if _pool is not None:
        return

    pool = _create_pool(settings)

    try:
        pool.open(wait=True)
    except Exception:
        pool.close()
        raise

    _pool = pool


def close_pool() -> None:
    global _pool

    if _pool is not None:
        _pool.close()

    _pool = None


@contextmanager
def acquire_connection() -> Iterator[Connection]:
    with _require_open().connection() as conn:
        yield conn


@contextmanager
def acquire_connection_with_transaction() -> Iterator[Connection]:
    with _require_open().connection() as conn:
        with conn.transaction():
            yield conn


def _create_pool(settings: Settings) -> ConnectionPool[Connection]:
    return ConnectionPool(
        conninfo=settings.database_dsn_radar,
        kwargs={
            "application_name": DB_POOL_APPLICATION_NAME,
            "connect_timeout": DB_POOL_CONNECT_TIMEOUT_SECONDS,
        },
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE,
        name=DB_POOL_NAME,
        timeout=DB_POOL_ACQUIRE_TIMEOUT_SECONDS,
        max_waiting=DB_POOL_MAX_WAITING,
        check=ConnectionPool.check_connection,
        open=False,
    )


def _require_open() -> ConnectionPool[Connection]:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool
