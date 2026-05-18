from __future__ import annotations

import json
import re
from dataclasses import dataclass

from psycopg import Connection
from psycopg.types.json import Jsonb

from radar_backend.db.repositories.base import BaseRepository


@dataclass(frozen=True)
class PolicyUpdate:
    id: int
    source_key: str
    source_label: str
    source_url: str
    source_title: str
    source_content: str
    briefing: str
    pdf_urls: list
    source_metadata: dict
    reference_number: str | None
    published_at: object  # datetime | None
    policy_extract_attempt_count: int


class PolicyUpdatesRepository(BaseRepository):
    """radar_policy_updates persistence boundary."""

    def insert(
        self,
        conn: Connection,
        item: object,   # RawSourceItem — avoid circular import; duck-typed
        draft: object,  # PolicyUpdateDraft — avoid circular import; duck-typed
    ) -> int:
        """Insert a new policy update. Returns the new row id."""
        source_content = re.sub(r"\s{2,}", " ", item.source_content).strip()  # type: ignore[attr-defined]

        effective_date = None
        if draft.effective_date:  # type: ignore[attr-defined]
            from datetime import date
            try:
                effective_date = date.fromisoformat(draft.effective_date)  # type: ignore[attr-defined]
            except ValueError:
                effective_date = None

        cur = conn.execute(
            """
            INSERT INTO radar_policy_updates (
                raw_source_item_id, source_key, source_label, source_url,
                source_title, reference_number, published_at, pdf_urls, source_metadata,
                headline, summary, briefing, source_content,
                effective_date,
                policy_extract_status, policy_review_status, action_calculate_status
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s,
                'pending', 'confirm_needed', 'pending'
            )
            RETURNING id
            """,
            (
                item.id,                          # type: ignore[attr-defined]
                item.source_key,                  # type: ignore[attr-defined]
                item.source_label,                # type: ignore[attr-defined]
                item.source_url,                  # type: ignore[attr-defined]
                item.source_title,                # type: ignore[attr-defined]
                draft.reference_number,           # type: ignore[attr-defined]
                item.published_at,                # type: ignore[attr-defined]
                Jsonb(item.pdf_urls),             # type: ignore[attr-defined]
                Jsonb(item.source_metadata),      # type: ignore[attr-defined]
                draft.headline,                   # type: ignore[attr-defined]
                draft.summary,                    # type: ignore[attr-defined]
                draft.briefing,                   # type: ignore[attr-defined]
                source_content,
                effective_date,
            ),
        )
        row = cur.fetchone()
        assert row is not None
        return row[0]

    def fetch_pending_for_policy_impact(
        self, conn: Connection, limit: int = 50
    ) -> list[PolicyUpdate]:
        """Return policy updates that need impact extraction (pending or failed, <3 attempts)."""
        cur = conn.execute(
            """
            SELECT id, source_key, source_label, source_url, source_title,
                   source_content, briefing, pdf_urls, source_metadata,
                   reference_number, published_at, policy_extract_attempt_count
            FROM radar_policy_updates
            WHERE policy_extract_status IN ('pending', 'failed')
              AND policy_extract_attempt_count < 3
            ORDER BY id ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        return [
            PolicyUpdate(
                id=row[0],
                source_key=row[1],
                source_label=row[2],
                source_url=row[3],
                source_title=row[4],
                source_content=row[5] or "",
                briefing=row[6] or "",
                pdf_urls=list(row[7]) if row[7] else [],
                source_metadata=dict(row[8]) if row[8] else {},
                reference_number=row[9],
                published_at=row[10],
                policy_extract_attempt_count=row[11] or 0,
            )
            for row in rows
        ]

    def set_policy_extract_status(
        self,
        conn: Connection,
        policy_update_id: int,
        status: str,
        new_attempt_count: int,
        impact_json: dict | None = None,
    ) -> None:
        """Update policy_extract_status, attempt count, and optionally impact_json."""
        conn.execute(
            """
            UPDATE radar_policy_updates
            SET policy_extract_status = %s,
                policy_extract_attempt_count = %s,
                impact_json = COALESCE(%s::jsonb, impact_json),
                updated_at = now()
            WHERE id = %s
            """,
            (
                status,
                new_attempt_count,
                json.dumps(impact_json) if impact_json is not None else None,
                policy_update_id,
            ),
        )
