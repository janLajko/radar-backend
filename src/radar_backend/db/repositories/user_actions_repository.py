from __future__ import annotations

from datetime import datetime
from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from radar_backend.domain import (
    ActionItem,
    ActionItemStatus,
    ActionType,
    AffectedProduct,
    UserActionModel,
    UserActionStatus,
)

_USER_ACTION_COLUMNS = """
id,
user_id,
policy_update_id,
affected_products,
action_items,
status,
completed_at,
completed_by,
created_at,
updated_at
"""


class UserActionsRepository:
    def create_user_action(
        self,
        conn: Connection,
        *,
        user_id: int,
        policy_update_id: int,
        affected_products: list[AffectedProduct],
        action_items: list[ActionItem],
    ) -> int | None:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO radar_user_actions (
                  user_id,
                  policy_update_id,
                  affected_products,
                  action_items
                )
                VALUES (
                  %(user_id)s,
                  %(policy_update_id)s,
                  %(affected_products)s,
                  %(action_items)s
                )
                ON CONFLICT (user_id, policy_update_id) DO NOTHING
                RETURNING id
                """,
                {
                    "user_id": user_id,
                    "policy_update_id": policy_update_id,
                    "affected_products": Jsonb(affected_products),
                    "action_items": Jsonb(action_items),
                },
            )
            row = cur.fetchone()

        return cast(int, row[0]) if row is not None else None

    def get_by_id(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> UserActionModel | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_USER_ACTION_COLUMNS}
                FROM radar_user_actions
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            row = cur.fetchone()

        return self._to_model(row) if row is not None else None

    def _to_model(self, row: dict[str, object]) -> UserActionModel:
        return {
            "id": cast(int, row["id"]),
            "user_id": cast(int, row["user_id"]),
            "policy_update_id": cast(int, row["policy_update_id"]),
            "affected_products": self._to_affected_products(row["affected_products"]),
            "action_items": self._to_action_items(row["action_items"]),
            "status": UserActionStatus(cast(str, row["status"])),
            "completed_at": cast(datetime | None, row["completed_at"]),
            "completed_by": cast(int | None, row["completed_by"]),
            "created_at": cast(datetime, row["created_at"]),
            "updated_at": cast(datetime, row["updated_at"]),
        }

    def _to_affected_products(self, value: object) -> list[AffectedProduct]:
        products = cast(list[dict[str, object]], value)
        return [
            {
                "product_uid": cast(str, product["product_uid"]),
                "product_name": cast(str, product["product_name"]),
                "hts_code": cast(str, product["hts_code"]),
                "suggested_actions": [
                    ActionType(cast(str, action))
                    for action in cast(list[object], product["suggested_actions"])
                ],
            }
            for product in products
        ]

    def _to_action_items(self, value: object) -> list[ActionItem]:
        items = cast(list[dict[str, object]], value)
        return [
            {
                "action_type": ActionType(cast(str, item["action_type"])),
                "effective_date": cast(str | None, item["effective_date"]),
                "status": ActionItemStatus(cast(str, item["status"])),
            }
            for item in items
        ]
