from __future__ import annotations

from datetime import datetime
from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from radar_backend.domain import EmailDeliveryModel, EmailDeliveryPayload, EmailDeliveryStatus

_EMAIL_DELIVERY_COLUMNS = """
id,
user_action_id,
recipient_id,
payload,
status,
attempt_count,
last_attempt_at,
sent_at,
created_at,
updated_at
"""


class EmailDeliveriesRepository:
    def create_email_delivery(
        self,
        conn: Connection,
        *,
        user_action_id: int,
        recipient_id: int,
        payload: EmailDeliveryPayload,
    ) -> int | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO radar_email_deliveries (
                  user_action_id,
                  recipient_id,
                  payload
                )
                VALUES (
                  %(user_action_id)s,
                  %(recipient_id)s,
                  %(payload)s
                )
                ON CONFLICT (user_action_id, recipient_id) DO NOTHING
                RETURNING id
                """,
                {
                    "user_action_id": user_action_id,
                    "recipient_id": recipient_id,
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
    ) -> EmailDeliveryModel | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_EMAIL_DELIVERY_COLUMNS}
                FROM radar_email_deliveries
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            row = cur.fetchone()

        return self._to_model(row) if row is not None else None

    def list_email_deliveries_to_send(
        self,
        conn: Connection,
        *,
        limit: int | None = None,
    ) -> list[EmailDeliveryModel]:
        query = f"""
            SELECT {_EMAIL_DELIVERY_COLUMNS}
            FROM radar_email_deliveries
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

    def mark_email_delivery_skipped(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_email_deliveries
                SET status = 'skipped'
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def mark_email_delivery_sent(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_email_deliveries
                SET status = 'sent',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    sent_at = now()
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def mark_email_delivery_failed(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_email_deliveries
                SET status = 'failed',
                    attempt_count = attempt_count + 1,
                    last_attempt_at = now(),
                    sent_at = NULL
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def _to_model(self, row: dict[str, object]) -> EmailDeliveryModel:
        return {
            "id": cast(int, row["id"]),
            "user_action_id": cast(int, row["user_action_id"]),
            "recipient_id": cast(int, row["recipient_id"]),
            "payload": cast(EmailDeliveryPayload, row["payload"]),
            "status": EmailDeliveryStatus(cast(str, row["status"])),
            "attempt_count": cast(int, row["attempt_count"]),
            "last_attempt_at": cast(datetime | None, row["last_attempt_at"]),
            "sent_at": cast(datetime | None, row["sent_at"]),
            "created_at": cast(datetime, row["created_at"]),
            "updated_at": cast(datetime, row["updated_at"]),
        }
