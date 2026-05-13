from __future__ import annotations

import logging
from dataclasses import dataclass

from radar_backend.config import Settings
from radar_backend.db import Database
from radar_backend.db.repositories import Repositories
from radar_backend.services import Services


@dataclass(frozen=True)
class WorkerContext:
    settings: Settings
    db: Database
    repositories: Repositories
    services: Services
    logger: logging.Logger
