from __future__ import annotations

from dataclasses import dataclass

from radar_backend.db.connection import Database


@dataclass(frozen=True)
class BaseRepository:
    """Base class for table repositories.

    Query methods must receive an explicit connection from the caller.
    """

    db: Database
