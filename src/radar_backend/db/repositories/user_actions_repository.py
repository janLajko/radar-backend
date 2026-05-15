from __future__ import annotations

from psycopg import Connection

from radar_backend.domain import ActionItem, AffectedProduct, UserActionModel


def create_user_action(
    conn: Connection,
    *,
    user_id: int,
    policy_update_id: int,
    affected_products: list[AffectedProduct],
    action_items: list[ActionItem],
) -> int | None:
    pass


def get_by_id(
    conn: Connection,
    *,
    id: int,
) -> UserActionModel | None:
    pass
