from __future__ import annotations

from radar_backend.sources._presidency import fetch_presidency_items
from radar_backend.sources.base import RawSourceItemCandidate
from radar_backend.sources.http_client import HttpClient


class ProclamationAdapter:
    """Fetches Presidential Proclamations from presidency.ucsb.edu (category 59)."""

    def fetch(
        self, fetch_config: dict, http: HttpClient
    ) -> list[RawSourceItemCandidate]:
        return fetch_presidency_items(
            http=http,
            category_params=[("category2[]", "59")],
            slug_prefix="proclamation-",
            lookback_days=int(fetch_config.get("lookback_days", 14)),
            items_per_page=int(fetch_config.get("items_per_page", 25)),
        )
