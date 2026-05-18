from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from radar_backend.sources.base import RawSourceItemCandidate
from radar_backend.sources.http_client import HttpClient

_ARCHIVE_PAGE = "https://hts.usitc.gov/download/archive"

# Matches strings like "2026HTSBasic", "2025HTSChapter99", etc.
_ARCHIVE_ID_RE = re.compile(r"\d{4}HTS\w+", re.IGNORECASE)

# Matches change record PDF filenames like "Change Record_2026HTSBasic (1).pdf"
_CHANGE_RECORD_RE = re.compile(r"change.?record", re.IGNORECASE)


class HTSArchiveAdapter:
    """Detects new HTS archives on hts.usitc.gov and emits one item per archive."""

    def fetch(
        self, fetch_config: dict, http: HttpClient
    ) -> list[RawSourceItemCandidate]:
        resp = http.get(_ARCHIVE_PAGE)
        soup = BeautifulSoup(resp.text, "html.parser")
        return _parse_archives(soup)


def _parse_archives(soup: BeautifulSoup) -> list[RawSourceItemCandidate]:
    candidates: list[RawSourceItemCandidate] = []
    seen_ids: set[str] = set()

    # Look for all <a> tags that link to PDF or archive resources
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        link_text: str = a.get_text(strip=True)

        # Try to find an archive ID in the href or link text
        archive_id = _extract_archive_id(href) or _extract_archive_id(link_text)
        if not archive_id or archive_id in seen_ids:
            continue

        change_record_url = _find_change_record_url(soup, archive_id)
        if not change_record_url:
            # Only emit an item when we can identify a change record
            change_record_url = urljoin(_ARCHIVE_PAGE, href) if href.lower().endswith(".pdf") else None
            if not change_record_url:
                continue

        seen_ids.add(archive_id)
        published_at = _extract_archive_date(soup, archive_id)

        candidates.append(
            RawSourceItemCandidate(
                source_item_key=archive_id,
                source_url=_ARCHIVE_PAGE,
                source_title=f"HTS Archive: {archive_id}",
                published_at=published_at,
                source_content=f"New HTS archive {archive_id} available.",
                source_metadata={
                    "archive_id": archive_id,
                    "change_record_url": change_record_url,
                },
                pdf_urls=[change_record_url],
            )
        )

    return candidates


def _extract_archive_id(text: str) -> str | None:
    m = _ARCHIVE_ID_RE.search(text)
    return m.group(0) if m else None


def _find_change_record_url(soup: BeautifulSoup, archive_id: str) -> str | None:
    """Find the change record PDF URL for a specific archive ID."""
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        link_text: str = a.get_text(strip=True)
        if _CHANGE_RECORD_RE.search(link_text) or _CHANGE_RECORD_RE.search(href):
            if archive_id.lower() in href.lower() or archive_id.lower() in link_text.lower():
                return urljoin(_ARCHIVE_PAGE, href)
    return None


def _extract_archive_date(soup: BeautifulSoup, archive_id: str) -> datetime | None:
    """Try to find a publication date near the archive ID in the page."""
    year_match = re.match(r"(\d{4})", archive_id)
    if year_match:
        year = int(year_match.group(1))
        if 2000 <= year <= 2100:
            return datetime(year, 1, 1, tzinfo=timezone.utc)
    return None
