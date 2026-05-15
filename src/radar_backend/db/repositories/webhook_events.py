from __future__ import annotations

from psycopg import Connection
from psycopg.types.json import Jsonb

from radar_backend.db.repositories.base import BaseRepository


class WebhookEventsRepository(BaseRepository):
    """radar_webhook_events persistence boundary."""

    def upsert_attempt_exhausted(
        self,
        conn: Connection,
        entity_type: str,
        entity_id: int,
    ) -> None:
        """Write an attempt_exhausted webhook event (idempotent via ON CONFLICT DO NOTHING)."""
        payload = {"entity_id": entity_id}
        conn.execute(
            """
            INSERT INTO radar_webhook_events
                (event_type, entity_type, entity_id, channel, payload, status)
            VALUES ('attempt_exhausted', %s, %s, 'lark', %s, 'pending')
            ON CONFLICT (event_type, entity_type, entity_id, channel) DO NOTHING
            """,
            (entity_type, entity_id, Jsonb(payload)),
        )
