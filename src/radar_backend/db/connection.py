from __future__ import annotations

from dataclasses import dataclass, field

from psycopg import Connection
from psycopg_pool import ConnectionPool

from radar_backend.config import Settings


@dataclass
class Database:
    settings: Settings
    _opened: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._pool = ConnectionPool(
            conninfo=self.settings.database_dsn_radar,
            min_size=self.settings.db_pool_min_size,
            max_size=self.settings.db_pool_max_size,
            timeout=self.settings.db_pool_timeout_seconds,
            open=False,
        )

    @property
    def pool(self) -> ConnectionPool[Connection]:
        return self._pool

    def open(self) -> None:
        self._pool.open(wait=True)
        self._opened = True

    def close(self) -> None:
        if self._opened:
            self._pool.close()
            self._opened = False

    def check(self) -> None:
        self._pool.check()
