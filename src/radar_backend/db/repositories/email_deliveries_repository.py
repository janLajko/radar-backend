from __future__ import annotations

from psycopg import Connection

from radar_backend.domain import EmailDeliveryModel, EmailDeliveryPayload


def create_email_delivery(
    conn: Connection,
    *,
    user_action_id: int,
    recipient_id: int,
    payload: EmailDeliveryPayload,
) -> int | None:
    pass


def get_by_id(
    conn: Connection,
    *,
    id: int,
) -> EmailDeliveryModel | None:
    pass


def list_email_deliveries_to_send(
    conn: Connection,
    *,
    limit: int | None = None,
) -> list[EmailDeliveryModel]:
    pass


def mark_email_delivery_skipped(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass


def mark_email_delivery_sent(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass


def mark_email_delivery_failed(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass
