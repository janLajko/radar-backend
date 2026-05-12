from __future__ import annotations

from radar_backend.db.repositories.base import BaseRepository


class WebhookRepository(BaseRepository):
    """Operational webhook outbox persistence boundary."""

