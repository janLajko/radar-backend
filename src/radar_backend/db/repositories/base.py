from __future__ import annotations

from dataclasses import dataclass

from radar_backend.db.connection import Database


@dataclass(frozen=True)
class BaseRepository:
    db: Database

