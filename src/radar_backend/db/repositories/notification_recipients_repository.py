from __future__ import annotations

from datetime import datetime
from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row

from radar_backend.domain import NotificationRecipientModel, RecipientStatus

_NOTIFICATION_RECIPIENT_COLUMNS = """
id,
user_id,
email,
unsubscribe_token,
status,
created_at,
updated_at
"""


class NotificationRecipientsRepository:
    def get_by_id(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> NotificationRecipientModel | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_NOTIFICATION_RECIPIENT_COLUMNS}
                FROM radar_notification_recipients
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            row = cur.fetchone()

        return self._to_model(row) if row is not None else None

    def list_active_recipients_by_user_id(
        self,
        conn: Connection,
        *,
        user_id: int,
    ) -> list[NotificationRecipientModel]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_NOTIFICATION_RECIPIENT_COLUMNS}
                FROM radar_notification_recipients
                WHERE user_id = %(user_id)s
                  AND status = 'active'
                ORDER BY created_at ASC, id ASC
                """,
                {"user_id": user_id},
            )
            rows = cur.fetchall()

        return [self._to_model(row) for row in rows]

    def _to_model(self, row: dict[str, object]) -> NotificationRecipientModel:
        return {
            "id": cast(int, row["id"]),
            "user_id": cast(int, row["user_id"]),
            "email": cast(str, row["email"]),
            "unsubscribe_token": cast(str, row["unsubscribe_token"]),
            "status": RecipientStatus(cast(str, row["status"])),
            "created_at": cast(datetime, row["created_at"]),
            "updated_at": cast(datetime, row["updated_at"]),
        }
