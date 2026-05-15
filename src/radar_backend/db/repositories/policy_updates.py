from __future__ import annotations

import re

from psycopg import Connection
from psycopg.types.json import Jsonb

from radar_backend.db.repositories.base import BaseRepository


class PolicyUpdatesRepository(BaseRepository):
    """radar_policy_updates persistence boundary."""

    def insert(
        self,
        conn: Connection,
        item: object,   # RawSourceItem — avoid circular import; duck-typed
        draft: object,  # PolicyUpdateDraft — avoid circular import; duck-typed
    ) -> int:
        """Insert a new policy update. Returns the new row id."""
        original_text = re.sub(r"\s{2,}", " ", item.raw_content).strip()  # type: ignore[attr-defined]

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
                reference_number, published_at, pdf_urls, source_metadata,
                headline, summary, briefing_markdown, original_text,
                effective_date,
                policy_extract_status, policy_review_status, action_calculate_status
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s,
                'pending', 'pending', 'pending'
            )
            RETURNING id
            """,
            (
                item.id,                          # type: ignore[attr-defined]
                item.source_key,                  # type: ignore[attr-defined]
                item.source_label,                # type: ignore[attr-defined]
                item.source_url,                  # type: ignore[attr-defined]
                draft.reference_number,           # type: ignore[attr-defined]
                item.published_at,                # type: ignore[attr-defined]
                Jsonb(item.pdf_urls),             # type: ignore[attr-defined]
                Jsonb(item.raw_metadata),         # type: ignore[attr-defined]
                draft.headline,                   # type: ignore[attr-defined]
                draft.summary,                    # type: ignore[attr-defined]
                draft.briefing_markdown,          # type: ignore[attr-defined]
                original_text,
                effective_date,
            ),
        )
        row = cur.fetchone()
        assert row is not None
        return row[0]
