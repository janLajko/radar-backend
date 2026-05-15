from __future__ import annotations

from psycopg import Connection

from radar_backend.domain import PolicyUpdateModel


def get_by_id(
    conn: Connection,
    *,
    id: int,
) -> PolicyUpdateModel | None:
    pass


def list_policy_updates_to_calculate_user_actions(
    conn: Connection,
    *,
    limit: int | None = None,
) -> list[PolicyUpdateModel]:
    pass


def mark_action_calculate_succeeded(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass


def mark_action_calculate_failed(
    conn: Connection,
    *,
    id: int,
) -> int:
    pass
