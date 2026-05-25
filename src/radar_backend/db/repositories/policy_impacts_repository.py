from __future__ import annotations

from datetime import date, datetime
from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row

from radar_backend.domain import PolicyImpactModel, PolicyImpactType

_POLICY_IMPACT_COLUMNS = """
id,
policy_update_id,
hts_number,
impacted_type,
effective_time,
coos,
row_desc,
created_at,
updated_at
"""


class PolicyImpactsRepository:
    def list_by_policy_update_id(
        self,
        conn: Connection,
        *,
        policy_update_id: int,
    ) -> list[PolicyImpactModel]:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_POLICY_IMPACT_COLUMNS}
                FROM radar_policy_impacts
                WHERE policy_update_id = %(policy_update_id)s
                ORDER BY id ASC
                """,
                {"policy_update_id": policy_update_id},
            )
            rows = cur.fetchall()

        return [self._to_model(row) for row in rows]

    def _to_model(self, row: dict[str, object]) -> PolicyImpactModel:
        return {
            "id": cast(int, row["id"]),
            "policy_update_id": cast(int, row["policy_update_id"]),
            "hts_number": cast(str, row["hts_number"]),
            "impacted_type": PolicyImpactType(cast(str, row["impacted_type"])),
            "effective_time": cast(date | None, row["effective_time"]),
            "coos": cast(list[str] | None, row["coos"]),
            "row_desc": cast(str | None, row["row_desc"]),
            "created_at": cast(datetime, row["created_at"]),
            "updated_at": cast(datetime, row["updated_at"]),
        }
