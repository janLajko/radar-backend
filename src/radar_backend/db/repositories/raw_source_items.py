from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg import Connection
from psycopg.types.json import Jsonb

from radar_backend.db.repositories.base import BaseRepository
from radar_backend.sources.base import RawSourceItemCandidate


@dataclass(frozen=True)
class RawSourceItem:
    """Projection of radar_raw_source_items used by Stage 2."""

    id: int
    source_key: str
    source_label: str
    source_url: str
    source_item_key: str
    title: str
    published_at: datetime | None
    pdf_urls: list[str]
    raw_metadata: dict
    raw_content: str
    policy_update_attempt_count: int


class RawSourceItemsRepository(BaseRepository):
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
                (source_key, source_label, source_item_key, source_url, title,
                 published_at, pdf_urls, raw_metadata, raw_content)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_key, source_item_key) DO NOTHING
            RETURNING id
            """,
            (
                source_key,
                source_label,
                candidate.source_item_key,
                candidate.source_url,
                candidate.title,
                candidate.published_at,
                Jsonb(candidate.pdf_urls),
                Jsonb(candidate.raw_metadata),
                candidate.raw_content,
            ),
        )
        return cur.fetchone() is not None

    def fetch_pending_for_policy_update(
        self,
        conn: Connection,
        limit: int = 100,
    ) -> list[RawSourceItem]:
        """Return raw items eligible for Stage 2 processing."""
        cur = conn.execute(
            """
            SELECT id, source_key, source_label, source_url, source_item_key,
                   title, published_at, pdf_urls, raw_metadata, raw_content,
                   policy_update_attempt_count
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
            RawSourceItem(
                id=row[0],
                source_key=row[1],
                source_label=row[2],
                source_url=row[3],
                source_item_key=row[4],
                title=row[5],
                published_at=row[6],
                pdf_urls=list(row[7]) if row[7] else [],
                raw_metadata=dict(row[8]) if row[8] else {},
                raw_content=row[9],
                policy_update_attempt_count=row[10],
            )
            for row in rows
        ]

    def set_policy_update_status(
        self,
        conn: Connection,
        item_id: int,
        status: str,
        new_attempt_count: int,
        discard_reason: str | None = None,
    ) -> None:
        """Update the policy_update_status and attempt_count for a raw item."""
        conn.execute(
            """
            UPDATE radar_raw_source_items
            SET policy_update_status = %s,
                policy_update_attempt_count = %s,
                discard_reason = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (status, new_attempt_count, discard_reason, item_id),
        )
