from __future__ import annotations

from typing import cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from radar_backend.domain import RawSourceItemModel, RawSourceItemPolicyUpdateStatus
from radar_backend.sources.base import RawSourceItemCandidate


class RawSourceItemsRepository:
    """radar_raw_source_items persistence boundary."""

    def insert_if_not_exists(
        self,
        conn: Connection,
        candidate: RawSourceItemCandidate,
        source_key: str,
        source_label: str,
    ) -> bool:
        """Insert a raw source item.

        Returns True if the row was inserted, False if it already existed.
        Uses ON CONFLICT DO NOTHING so the existing row is never modified.
        """
        cur = conn.execute(
            """
            INSERT INTO radar_raw_source_items
                (source_key, source_label, source_item_key, source_url, source_title,
                 published_at, pdf_urls, source_metadata, source_content)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_key, source_item_key) DO NOTHING
            RETURNING id
            """,
            (
                source_key,
                source_label,
                candidate.source_item_key,
                candidate.source_url,
                candidate.source_title,
                candidate.published_at,
                Jsonb(candidate.pdf_urls),
                Jsonb(candidate.source_metadata),
                candidate.source_content,
            ),
        )
        return cur.fetchone() is not None

    def fetch_pending_for_policy_update(
        self,
        conn: Connection,
        limit: int = 100,
    ) -> list[RawSourceItemModel]:
        """Return raw items eligible for Stage 2 processing."""
        cur = conn.execute(
            """
            SELECT id, source_key, source_label, source_item_key, source_url,
                   source_metadata, source_title, source_content, pdf_urls,
                   reference_number, published_at, policy_update_status,
                   policy_update_attempt_count, created_at, updated_at
            FROM radar_raw_source_items
            WHERE policy_update_status IN ('pending', 'failed')
              AND policy_update_attempt_count < 3
            ORDER BY id ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            {
                "id": row[0],
                "source_key": row[1],
                "source_label": row[2],
                "source_item_key": row[3],
                "source_url": row[4],
                "source_metadata": cast(dict, row[5]) if row[5] else {},
                "source_title": row[6],
                "source_content": row[7],
                "pdf_urls": list(row[8]) if row[8] else [],
                "reference_number": row[9],
                "published_at": row[10],
                "policy_update_status": RawSourceItemPolicyUpdateStatus(row[11]),
                "policy_update_attempt_count": row[12],
                "created_at": row[13],
                "updated_at": row[14],
            }
            for row in rows
        ]

    def set_policy_update_status(
        self,
        conn: Connection,
        item_id: int,
        status: str,
        new_attempt_count: int,
    ) -> None:
        """Update the policy_update_status and attempt_count for a raw item."""
        conn.execute(
            """
            UPDATE radar_raw_source_items
            SET policy_update_status = %s,
                policy_update_attempt_count = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (status, new_attempt_count, item_id),
        )
