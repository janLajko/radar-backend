"""Shared fetching logic for presidency.ucsb.edu (Proclamations and Executive Orders)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from radar_backend.sources.base import RawSourceItemCandidate
from radar_backend.sources.http_client import HttpClient

_BASE = "https://www.presidency.ucsb.edu"

_DATE_FORMATS = (
    "%B %d, %Y",
    "%b. %d, %Y",
    "%b %d, %Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
)


def fetch_presidency_items(
    http: HttpClient,
    category_params: list[tuple[str, str]],
    slug_prefix: str,
    lookback_days: int,
    items_per_page: int,
) -> list[RawSourceItemCandidate]:
    """Fetch recent documents from presidency.ucsb.edu.

    Uses sort=desc to get newest first, then stops when items fall outside
    the lookback window. Date URL filter is not used because it returns 0
    results on the site.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)

    params: list[tuple[str, str]] = [
        ("items_per_page", str(items_per_page)),
        ("order", "field_docs_start_date_time_value"),
        ("sort", "desc"),
    ] + category_params

    resp = http.get(f"{_BASE}/advanced-search", params=params)
    soup = BeautifulSoup(resp.text, "html.parser")

    doc_links = _extract_listing_links(soup, slug_prefix)
    candidates: list[RawSourceItemCandidate] = []

    for href in doc_links:
        absolute_url = urljoin(_BASE, href)
        slug = href.rstrip("/").split("/")[-1]
        try:
            candidate = _fetch_detail(http, absolute_url, slug)
        except Exception:
            continue

        # Stop once we've passed the lookback window (list is sorted newest-first)
        if candidate.published_at is not None and candidate.published_at < cutoff:
            break

        candidates.append(candidate)

    return candidates


def _extract_listing_links(soup: BeautifulSoup, slug_prefix: str) -> list[str]:
    """Extract document hrefs that match the expected slug prefix."""
    prefix_path = f"/documents/{slug_prefix}"
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if href.startswith(prefix_path) and href not in links:
            links.append(href)
    return links


def _fetch_detail(
    http: HttpClient, url: str, slug: str
) -> RawSourceItemCandidate:
    resp = http.get(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    title = _extract_title(soup)
    published_at = _extract_date(soup)
    source_content = _extract_body(soup)
    pdf_urls = _extract_pdf_urls(soup, url)
    source_metadata: dict = {"slug": slug}
    if published_at:
        source_metadata["published_date"] = published_at.date().isoformat()

    return RawSourceItemCandidate(
        source_item_key=slug,
        source_url=url,
        source_title=title,
        published_at=published_at,
        source_content=source_content,
        source_metadata=source_metadata,
        pdf_urls=pdf_urls,
    )


def _extract_title(soup: BeautifulSoup) -> str:
    for selector in (
        "h1.presidential-doc-heading",
        "h1.diet-title",
        "h1",
        "title",
    ):
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(" ", strip=True)
            text = re.sub(r"\s*\|\s*The American Presidency Project.*$", "", text)
            if text:
                return text
    return "Untitled"


def _extract_date(soup: BeautifulSoup) -> datetime | None:
    candidates: list[str] = []

    time_tag = soup.find("time")
    if isinstance(time_tag, Tag):
        dt_attr = time_tag.get("datetime")
        if dt_attr:
            candidates.insert(0, str(dt_attr))
        text = time_tag.get_text(strip=True)
        if text:
            candidates.append(text)

    for cls in ("date-display-single", "field-docs-start-date", "prez-doc__date"):
        tag = soup.find(class_=cls)
        if isinstance(tag, Tag):
            candidates.append(tag.get_text(strip=True))

    for text in candidates:
        dt = _parse_date(text)
        if dt:
            return dt
    return None


def _parse_date(text: str) -> datetime | None:
    text = text.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        pass
    return None


def _extract_body(soup: BeautifulSoup) -> str:
    for selector in (
        "div.field-docs-content",
        "div.prez-doc__body",
        "div.field-body",
        "article",
        "main",
    ):
        tag = soup.select_one(selector)
        if tag:
            return _clean_text(tag.get_text(" ", strip=True))
    return _clean_text(soup.get_text(" ", strip=True))


def _extract_pdf_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if href.lower().endswith(".pdf"):
            absolute = urljoin(base_url, href)
            if absolute not in urls:
                urls.append(absolute)
    return urls


def _clean_text(text: str) -> str:
    return re.sub(r"\s{2,}", " ", text).strip()
