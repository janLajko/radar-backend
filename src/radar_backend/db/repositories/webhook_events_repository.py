from __future__ import annotations

from psycopg import Connection

from radar_backend.domain import (
    WebhookEntityType,
    WebhookEventModel,
    WebhookEventType,
    WebhookPayload,
)


def create_webhook_event(
    conn: Connection,
    *,
    event_type: WebhookEventType,
    entity_type: WebhookEntityType,
    entity_id: int,
    payload: WebhookPayload,
) -> int | None:
    pass


def get_by_id(
    conn: Connection,
    *,
    id: int,
) -> WebhookEventModel | None:
    pass


def list_webhook_events_to_send(
    conn: Connection,
    *,
    limit: int | None = None,
) -> list[WebhookEventModel]:
    pass


def mark_webhook_event_sent(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass


def mark_webhook_event_failed(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass
