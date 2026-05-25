from __future__ import annotations

from psycopg import Connection

from radar_backend.domain import ActionType, AffectedProduct


class UserActionTargetsRepository:
    def create_targets_for_user_action(
        self,
        conn: Connection,
        *,
        user_action_id: int,
        policy_update_id: int,
        user_id: int,
        affected_products: list[AffectedProduct],
    ) -> int:
        rows: list[dict[str, object]] = []
        for product in affected_products:
            product_uid = str(product.get("product_uid") or "").strip()
            if not product_uid:
                continue
            for action_type in product.get("suggested_actions") or []:
                rows.append(
                    {
                        "user_action_id": user_action_id,
                        "policy_update_id": policy_update_id,
                        "user_id": user_id,
                        "product_uid": product_uid,
                        "action_type": ActionType(action_type).value,
                    }
                )

        if not rows:
            return 0

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO radar_user_action_targets (
                  user_action_id,
                  policy_update_id,
                  user_id,
                  product_uid,
                  action_type
                )
                VALUES (
                  %(user_action_id)s,
                  %(policy_update_id)s,
                  %(user_id)s,
                  %(product_uid)s,
                  %(action_type)s
                )
                ON CONFLICT (user_action_id, product_uid, action_type) DO NOTHING
                """,
                rows,
            )
            return cur.rowcount
