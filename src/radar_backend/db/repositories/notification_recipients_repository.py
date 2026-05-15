from __future__ import annotations

from psycopg import Connection

from radar_backend.domain import NotificationRecipientModel


def get_by_id(
    conn: Connection,
    *,
    id: int,
) -> NotificationRecipientModel | None:
    pass


def list_active_recipients_by_user_id(
    conn: Connection,
    *,
    user_id: int,
) -> list[NotificationRecipientModel]:
    pass
