from __future__ import annotations

from datetime import date, datetime
from typing import cast

from psycopg import Connection
from psycopg.rows import dict_row

from radar_backend.domain import (
    ActionCalculateStatus,
    PolicyExtractStatus,
    PolicyReviewStatus,
    PolicyUpdateModel,
)

_POLICY_UPDATE_COLUMNS = """
id,
raw_source_item_id,
source_key,
source_label,
source_url,
source_metadata,
source_title,
source_content,
pdf_urls,
reference_number,
published_at,
effective_date,
headline,
summary,
briefing,
policy_extract_status,
policy_extract_attempt_count,
policy_review_status,
action_calculate_status,
action_calculate_attempt_count,
created_at,
updated_at
"""


class PolicyUpdatesRepository:
    def get_by_id(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> PolicyUpdateModel | None:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_POLICY_UPDATE_COLUMNS}
                FROM radar_policy_updates
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            row = cur.fetchone()

        return self._to_model(row) if row is not None else None

    def list_policy_updates_to_calculate_user_actions(
        self,
        conn: Connection,
        *,
        limit: int | None = None,
    ) -> list[PolicyUpdateModel]:
        query = f"""
            SELECT {_POLICY_UPDATE_COLUMNS}
            FROM radar_policy_updates
            WHERE policy_review_status = 'approved'
              AND action_calculate_status IN ('pending', 'failed')
              AND action_calculate_attempt_count < 3
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

    def mark_action_calculate_succeeded(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_policy_updates
                SET action_calculate_status = 'succeeded',
                    action_calculate_attempt_count = action_calculate_attempt_count + 1
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def mark_action_calculate_failed(
        self,
        conn: Connection,
        *,
        id: int,
    ) -> int:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE radar_policy_updates
                SET action_calculate_status = 'failed',
                    action_calculate_attempt_count = action_calculate_attempt_count + 1
                WHERE id = %(id)s
                """,
                {"id": id},
            )
            return cur.rowcount

    def _to_model(self, row: dict[str, object]) -> PolicyUpdateModel:
        return {
            "id": cast(int, row["id"]),
            "raw_source_item_id": cast(int, row["raw_source_item_id"]),
            "source_key": cast(str, row["source_key"]),
            "source_label": cast(str, row["source_label"]),
            "source_url": cast(str, row["source_url"]),
            "source_metadata": cast(dict[str, object], row["source_metadata"]),
            "source_title": cast(str, row["source_title"]),
            "source_content": cast(str, row["source_content"]),
            "pdf_urls": cast(list[str], row["pdf_urls"]),
            "reference_number": cast(str | None, row["reference_number"]),
            "published_at": cast(datetime | None, row["published_at"]),
            "effective_date": cast(date | None, row["effective_date"]),
            "headline": cast(str, row["headline"]),
            "summary": cast(str, row["summary"]),
            "briefing": cast(str, row["briefing"]),
            "policy_extract_status": PolicyExtractStatus(cast(str, row["policy_extract_status"])),
            "policy_extract_attempt_count": cast(int, row["policy_extract_attempt_count"]),
            "policy_review_status": PolicyReviewStatus(cast(str, row["policy_review_status"])),
            "action_calculate_status": ActionCalculateStatus(
                cast(str, row["action_calculate_status"])
            ),
            "action_calculate_attempt_count": cast(int, row["action_calculate_attempt_count"]),
            "created_at": cast(datetime, row["created_at"]),
            "updated_at": cast(datetime, row["updated_at"]),
        }
