from __future__ import annotations

from datetime import datetime, timezone

from radar_backend.sources.base import RawSourceItemCandidate
from radar_backend.sources.http_client import HttpClient

_API = "https://www.federalregister.gov/api/v1/documents.json"

_DEFAULT_AGENCIES = [
    "industry-and-security-bureau",
    "international-trade-administration",
    "trade-representative-office-of-united-states",
    "u-s-customs-and-border-protection",
]

_FIELDS = [
    "document_number",
    "citation",
    "title",
    "abstract",
    "html_url",
    "pdf_url",
    "publication_date",
    "agencies",
    "docket_ids",
]


class FederalRegisterNoticeAdapter:
    """Fetches Notices from the Federal Register API, filtered by agency."""

    def fetch(
        self, fetch_config: dict, http: HttpClient
    ) -> list[RawSourceItemCandidate]:
        agencies: list[str] = fetch_config.get("agencies", _DEFAULT_AGENCIES)
        per_page: int = int(fetch_config.get("per_page", 40))

        params: list[tuple[str, str]] = [
            ("conditions[type][]", "NOTICE"),
            ("per_page", str(per_page)),
        ]
        for agency in agencies:
            params.append(("conditions[agencies][]", agency))
        for field in _FIELDS:
            params.append(("fields[]", field))

        resp = http.get(_API, params=params)
        data = resp.json()

        candidates: list[RawSourceItemCandidate] = []
        for item in data.get("results", []):
            candidate = _map_result(item)
            if candidate is not None:
                candidates.append(candidate)
        return candidates


def _map_result(item: dict) -> RawSourceItemCandidate | None:
    document_number: str | None = item.get("document_number")
    html_url: str | None = item.get("html_url")
    if not document_number or not html_url:
        return None

    title: str = item.get("title") or "Untitled"
    abstract: str = item.get("abstract") or ""
    raw_content = f"{title}\n\n{abstract}".strip()

    published_at: datetime | None = None
    date_str: str | None = item.get("publication_date")
    if date_str:
        try:
            published_at = datetime.strptime(date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass

    pdf_urls: list[str] = []
    if item.get("pdf_url"):
        pdf_urls.append(item["pdf_url"])

    agency_names = [
        a.get("name", "") for a in item.get("agencies", []) if a.get("name")
    ]
    docket_ids: list[str] = item.get("docket_ids") or []
    citation: str | None = item.get("citation") or None

    source_metadata: dict = {
        "document_number": document_number,
        "agencies": agency_names,
        "docket_ids": docket_ids,
    }
    if citation:
        source_metadata["citation"] = citation

    return RawSourceItemCandidate(
        source_item_key=document_number,
        source_url=html_url,
        source_title=title,
        published_at=published_at,
        source_content=raw_content,
        source_metadata=source_metadata,
        pdf_urls=pdf_urls,
    )
