from __future__ import annotations

from datetime import datetime
from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from radar_backend.domain import (
    WebhookEntityType,
    WebhookEventModel,
    WebhookEventStatus,
    WebhookEventType,
    WebhookPayload,
)

_WEBHOOK_EVENT_COLUMNS = """
id,
event_type,
entity_type,
entity_id,
payload,
status,
attempt_count,
last_attempt_at,
sent_at,
created_at,
updated_at
"""


class WebhookEventsRepository:
    def create_webhook_event(
        self,
        conn: Connection,
        *,
        event_type: WebhookEventType,
        entity_type: WebhookEntityType,
        entity_id: int,
        payload: WebhookPayload,
    ) -> int | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO radar_webhook_events (
                  event_type,
                  entity_type,
                  entity_id,
                  payload
                )
                VALUES (
                  %(event_type)s,
                  %(entity_type)s,
                  %(entity_id)s,
                  %(payload)s
                )
                ON CONFLICT (event_type, entity_type, entity_id) DO NOTHING
                RETURNING id
                """,
                {
                    "event_type": event_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "payload": Jsonb(payload),
                },
            )
            row = cur.fetchone()

        return cast(int, row[0]) if row is not None else None

    def get_by_id(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> WebhookEventModel | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_WEBHOOK_EVENT_COLUMNS}
                FROM radar_webhook_events
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            row = cur.fetchone()

        return self._to_model(row) if row is not None else None

    def list_webhook_events_to_send(
        self,
        conn: Connection,
        *,
        limit: int | None = None,
    ) -> list[WebhookEventModel]:
        query = f"""
            SELECT {_WEBHOOK_EVENT_COLUMNS}
            FROM radar_webhook_events
            WHERE status IN ('pending', 'failed')
              AND attempt_count < 3
            ORDER BY created_at ASC, id ASC
        """
        params: dict[str, object] = {}
        if limit is not None:
            query += "\nLIMIT %(limit)s"
            params["limit"] = limit

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        return [self._to_model(row) for row in rows]

    def mark_webhook_event_sent(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_webhook_events
                SET status = 'sent',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    sent_at = now()
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def mark_webhook_event_failed(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_webhook_events
                SET status = 'failed',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    sent_at = NULL
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def _to_model(self, row: dict[str, object]) -> WebhookEventModel:
        return {
            "id": cast(int, row["id"]),
            "event_type": WebhookEventType(cast(str, row["event_type"])),
            "entity_type": WebhookEntityType(cast(str, row["entity_type"])),
            "entity_id": cast(int, row["entity_id"]),
            "payload": cast(WebhookPayload, row["payload"]),
            "status": WebhookEventStatus(cast(str, row["status"])),
            "attempt_count": cast(int, row["attempt_count"]),
            "last_attempt_at": cast(datetime | None, row["last_attempt_at"]),
            "sent_at": cast(datetime | None, row["sent_at"]),
            "created_at": cast(datetime, row["created_at"]),
            "updated_at": cast(datetime, row["updated_at"]),
        }
