from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from radar_backend.sources.http_client import HttpClient


@dataclass(frozen=True)
class RawSourceItemCandidate:
    source_item_key: str
    source_url: str
    title: str
    published_at: datetime | None
    raw_content: str
    raw_metadata: dict
    pdf_urls: list[str]


class SourceAdapter(Protocol):
    def fetch(
        self, fetch_config: dict, http: "HttpClient"
    ) -> list[RawSourceItemCandidate]:
        ...
