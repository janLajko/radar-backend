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
        entity_type = _normalize_entity_type(entity_type)
        conn.execute(
            """
            INSERT INTO radar_webhook_events
                (event_type, entity_type, entity_id, payload, status)
            VALUES ('attempt_exhausted', %s, %s, %s, 'pending')
            ON CONFLICT (event_type, entity_type, entity_id) DO NOTHING
            """,
            (entity_type, entity_id, Jsonb(payload)),
        )

    def upsert_policy_impact_ready(
        self,
        conn: Connection,
        policy_update_id: int,
    ) -> None:
        """Write a policy_impact_ready_for_review webhook event (idempotent)."""
        payload = {"entity_id": policy_update_id}
        conn.execute(
            """
            INSERT INTO radar_webhook_events
                (event_type, entity_type, entity_id, payload, status)
            VALUES ('policy_impact_ready_for_review', 'policy_update', %s, %s, 'pending')
            ON CONFLICT (event_type, entity_type, entity_id) DO NOTHING
            """,
            (policy_update_id, Jsonb(payload)),
        )


def _normalize_entity_type(entity_type: str) -> str:
    if entity_type == "raw_policy_update":
        return "policy_update"
    return entity_type
