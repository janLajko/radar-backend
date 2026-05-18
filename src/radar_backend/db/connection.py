from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from psycopg import Connection
from psycopg_pool import ConnectionPool

from radar_backend.config import Settings


@dataclass
class Database:
    settings: Settings
    _pool: ConnectionPool[Connection] = field(init=False)
    _opened: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._pool = ConnectionPool(
            conninfo=self.settings.database_dsn_radar,
            min_size=self.settings.db_pool_min_size,
            max_size=self.settings.db_pool_max_size,
            timeout=self.settings.db_pool_timeout_seconds,
            max_idle=60,
            max_lifetime=1800,
            reconnect_timeout=60,
            check=ConnectionPool.check_connection,
            open=False,
        )

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        self._require_open()
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        self._require_open()
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    def open(self) -> None:
        if self._opened:
            return

        try:
            self._pool.open(wait=True)
        except Exception:
            self._pool.close()
            raise

        self._opened = True

    def close(self) -> None:
        if self._opened:
            self._pool.close()
            self._opened = False

    def check(self) -> None:
        self._require_open()
        self._pool.check()

    def _require_open(self) -> None:
        if not self._opened:
            raise RuntimeError("database pool is not open")
