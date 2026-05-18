from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from radar_backend.llm.provider import LLMProvider

logger = logging.getLogger(__name__)

_CACHE_DIR = Path("/tmp/hts_cache")

_JSON_RE = re.compile(r"<json>\s*(.*?)\s*</json>", re.DOTALL)
_HTS_HEADING_RE = r"\d{4}(?:\.\d{2})?(?:\.\d{2}|\.\d{4})?"
_HTS_HEADING_ONLY_RE = re.compile(rf"^{_HTS_HEADING_RE}$")
_HTS_HEADING_OR_RANGE_RE = re.compile(
    rf"^{_HTS_HEADING_RE}(?:-{_HTS_HEADING_RE})?$"
)

_CURRENT_CHAPTER_99_PDF_URL = (
    "https://hts.usitc.gov/reststop/file?release=currentRelease&filename=Chapter%2099"
)

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_HTTP_GET_TOOL = {
    "name": "http_get",
    "description": (
        "Fetch the text content of a URL. Use for HTML pages (e.g. HTS archive index) "
        "and CSV files. Returns up to max_chars characters of the response body."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return (default 20000)",
                "default": 20000,
            },
        },
        "required": ["url"],
    },
}

_READ_PDF_PAGES_TOOL = {
    "name": "read_pdf_pages",
    "description": (
        "Download a PDF (cached locally by URL) and extract text from the specified page range. "
        "Pages are 1-indexed. Use binary search to locate a specific section without loading "
        "the entire document — start with a wide range, then narrow down. "
        "Example pages values: '1-5', '200-215', '218-235'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "PDF URL"},
            "pages": {
                "type": "string",
                "description": "Page range, e.g. '1-5' or '200-215'",
            },
        },
        "required": ["url", "pages"],
    },
}

_SEARCH_CSV_ROWS_TOOL = {
    "name": "search_csv_rows",
    "description": (
        "Download a CSV file (or use cached version) and return rows whose text contains "
        "the keyword. Useful for looking up HTS heading rates from the Chapter CSV."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "CSV URL"},
            "keyword": {"type": "string", "description": "Case-insensitive keyword to filter rows"},
            "max_rows": {
                "type": "integer",
                "description": "Maximum number of matching rows to return (default 50)",
                "default": 50,
            },
        },
        "required": ["url", "keyword"],
    },
}

_TOOLS = [_HTTP_GET_TOOL, _READ_PDF_PAGES_TOOL, _SEARCH_CSV_ROWS_TOOL]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a trade compliance analyst specializing in US Harmonized Tariff Schedule (HTS) analysis.

Your task is to analyze a government policy document and extract structured information about
which HTS codes are affected, including any tariff rate changes.

## Step-by-step process

### Step 1: Parse the policy document
From the source content and attachment text, identify:
a. HTS codes explicitly listed (e.g., "9903.82.02")
b. Differential modifications to HTS notes (e.g., "U.S. note 16 is modified by deleting X and inserting Y")
c. New heading definitions with rates, descriptions, and applicable conditions

### Step 2: Fetch the latest HTS revision index
Call http_get("https://hts.usitc.gov/download/archive") to get the archive page.
Parse it to find the latest revision's:
- Chapter 99 PDF URL. Use the actual href from the page. For the current release, use:
  https://hts.usitc.gov/reststop/file?release=currentRelease&filename=Chapter%2099
  Do not invent URLs like https://hts.usitc.gov/download/Chapter_99_2026HTSRev7.pdf.
- Chapter CSV URL (e.g., "htsdata.csv" or similar)

### Step 3: If the document modifies HTS notes (differential changes)
Use binary search to locate the full note text in the Chapter 99 PDF:
1. read_pdf_pages(chapter99_pdf_url, "1-5") — confirm PDF structure and find approximate notes location
2. read_pdf_pages(chapter99_pdf_url, "X-Y") — binary search toward target note number
3. read_pdf_pages(chapter99_pdf_url, "P-Q") — read complete note text (typically 5-15 pages)

For note modifications, do not summarize or output the note modification itself.
Resolve the note modification into concrete HTS heading impacts and measure impacts.
Combine the differential description with the note's full text to determine:
- Which HTS headings are being deleted
- Which HTS headings are being inserted
- Which HTS headings have changed rates, conditions, country scope, date windows,
  exclusions, or applicability because of the note modification
- Which HTS headings are included by each U.S. note subdivision reference. Put those
  concrete headings in reusable scope_sets. For example, if a measure applies to
  "U.S. note 16(c)(i)-(iv)", create one scope_set for each relevant subdivision
  (16(c)(i), 16(c)(ii), 16(c)(iii), 16(c)(iv)) and reference those scope_set IDs
  from the measure.

### Step 4: Look up rates for affected headings
For each affected HTS heading (e.g., "9903.82"), call:
search_csv_rows(csv_url, keyword="9903.82", max_rows=50)

Extract from matching CSV rows: description, ad valorem rate, value basis, country scope.

### Step 5: Output JSON
Output the result wrapped in <json></json> tags as the final message. Do not include any text
after the closing </json> tag.

## Special case: source_key = hts_archive
- The attachment_text contains a change record; parse its entries
- Skip entries where Source field is "PP xxxxx", "Executive Order", or "Notice"
- Process remaining entries normally

## Output JSON format
<json>
{
    "source": {
        "type": "proclamation",
        "id": "11002",
        "url": "https://example.gov/...",
        "detected_at": "2026-05-07"
    },
    "hts_modifications": [
        {
            "action": "replace",
            "note": 16,
            "deleted": ["9903.78.01", "9903.81.87"],
            "inserted": ["9903.82.02"]
        }
    ],
    "scope_sets": [
        {
            "id": "note16_c_i",
            "source": "us_note_subdivision",
            "note": 16,
            "subdivision": "16(c)(i)",
            "label": "Aluminum articles",
            "headings": ["7601", "7604", "7605"]
        },
        {
            "id": "direct_7601",
            "source": "direct_hts_modification",
            "note": null,
            "subdivision": null,
            "label": "Directly modified heading 7601",
            "headings": ["7601"]
        }
    ],
    "measures": [
        {
            "measure_heading": "9903.82.02",
            "measure_heading_type": "chapter99",
            "note": 16,
            "description": "Articles of aluminum...",
            "ad_valorem_rate": 50.0,
            "value_basis": "CIF",
            "country_iso2": null,
            "is_potential": false,
            "effective_start_date": "2026-04-06",
            "effective_end_date": null,
            "affected_scope_refs": ["note16_c_i"],
            "excluded_scope_refs": [],
            "conditions": [],
            "excluded_chapter99_headings": [],
            "superseded_chapter99_headings": ["9903.78.01"]
        }
    ]
}
</json>

If no HTS impact can be determined, output:
<json>{"source": {}, "hts_modifications": [], "scope_sets": [], "measures": []}</json>

Rules:
- Always output valid JSON inside <json></json> tags
- Use null for unknown values, not empty strings for optional fields
- ad_valorem_rate must be a number (e.g., 50.0), not a string
- effective_start_date format: YYYY-MM-DD or null
- hts_modifications.deleted and hts_modifications.inserted must contain only
  concrete HTS headings/ranges. Do not put note-text placeholders such as
  "US note 16 (prior text)", "prior U.S. note text", "new U.S. note text", or
  "U.S. note 16" in these arrays. If exact headings cannot be determined after
  using the tools, use [] rather than a prose placeholder.
- scope_sets[].headings must be arrays of concrete HTS headings or HTS heading ranges
  only, e.g. ["7601", "7604.10.10", "7616.99.5160", "7210.61.00-7210.70.60"].
  Do not put prose, note citations, conditions, countries, or product descriptions
  in headings arrays.
- measures must not contain includes_headings or excludes_headings. Use
  affected_scope_refs and excluded_scope_refs to reference scope_sets instead.
- measure_heading is the HTS heading that carries the policy measure. It may be a
  Chapter 99 heading, a Chapter 1-98 ordinary HTS heading, or null for note-only
  changes. Set measure_heading_type to one of: "chapter99", "ordinary_hts",
  "note_only", "unknown".
- conditions must contain applicability rules such as country, origin-content rules,
  quota requirements, Column 1 rate thresholds, or date windows. Do not encode these
  conditions as HTS headings.
- When a measure applies to a U.S. note subdivision range such as 16(c)(i)-(iv),
  read the note text and expand every subdivision in that range into scope_sets with
  actual HTS headings/ranges. If the exact headings cannot be determined after using
  the tools, create the scope_set with an empty headings array rather than prose.
"""

# ---------------------------------------------------------------------------
# Input dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyImpactInput:
    policy_update_id: int
    source_key: str
    source_title: str
    source_content: str
    briefing: str
    attachment_text: str
    source_url: str


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _tool_http_get(url: str, max_chars: int = 20000) -> str:
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        return resp.text[:max_chars]
    except Exception as exc:
        return f"ERROR fetching {url}: {exc}"


def _get_cached_pdf(url: str) -> Path:
    """Download PDF to local cache dir (keyed by URL hash). Returns local path."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    url = _normalize_pdf_url(url)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    local = _CACHE_DIR / f"{url_hash}.pdf"
    if not local.exists():
        logger.info("policy_impact_extractor: downloading PDF %s", url)
        resp = httpx.get(url, timeout=120, follow_redirects=True)
        resp.raise_for_status()
        if not resp.content.startswith(b"%PDF"):
            raise ValueError(
                f"downloaded content is not a PDF from {url}: "
                f"content_type={resp.headers.get('content-type')!r} "
                f"prefix={resp.content[:20]!r}"
            )
        local.write_bytes(resp.content)
        logger.info("policy_impact_extractor: cached PDF %s -> %s", url, local)
    return local


def _normalize_pdf_url(url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.netloc == "hts.usitc.gov"
        and parsed.path.startswith("/download/")
        and re.search(r"chapter[_\s%20-]*99", url, re.IGNORECASE)
    ):
        return _CURRENT_CHAPTER_99_PDF_URL
    return url


def _tool_read_pdf_pages(url: str, pages: str) -> str:
    try:
        local = _get_cached_pdf(url)
        reader = PdfReader(str(local))
        total = len(reader.pages)

        # Parse page range (1-indexed)
        if "-" in pages:
            start_s, end_s = pages.split("-", 1)
            start = max(1, int(start_s.strip()))
            end = min(total, int(end_s.strip()))
        else:
            start = end = int(pages.strip())

        if start > total:
            return f"ERROR: PDF only has {total} pages; requested page {start}"
        if start > end:
            return f"ERROR: invalid page range '{pages}' (start {start} > end {end})"

        texts = []
        for i in range(start - 1, end):
            text = reader.pages[i].extract_text() or ""
            texts.append(f"[Page {i + 1}]\n{text}")

        result = "\n\n".join(texts)
        logger.debug(
            "policy_impact_extractor: read_pdf_pages url=%s pages=%s-%s chars=%d",
            url, start, end, len(result),
        )
        return result
    except Exception as exc:
        return f"ERROR reading PDF pages {pages} from {url}: {exc}"


def _tool_search_csv_rows(url: str, keyword: str, max_rows: int = 50) -> str:
    try:
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        lines = resp.text.splitlines()
        if not lines:
            return ""
        keyword_lower = keyword.lower()
        header = lines[0]
        matches = [line for line in lines[1:] if keyword_lower in line.lower()]
        result_lines = [header] + matches[:max_rows]
        return "\n".join(result_lines)
    except Exception as exc:
        return f"ERROR fetching CSV {url}: {exc}"


def _dispatch_tool(name: str, inputs: dict) -> str:
    if name == "http_get":
        return _tool_http_get(
            url=inputs["url"],
            max_chars=inputs.get("max_chars", 20000),
        )
    if name == "read_pdf_pages":
        return _tool_read_pdf_pages(
            url=inputs["url"],
            pages=inputs["pages"],
        )
    if name == "search_csv_rows":
        return _tool_search_csv_rows(
            url=inputs["url"],
            keyword=inputs["keyword"],
            max_rows=inputs.get("max_rows", 50),
        )
    return f"ERROR: unknown tool {name!r}"


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------


def _build_user_message(inp: PolicyImpactInput) -> str:
    parts = [
        f"Policy Update ID: {inp.policy_update_id}",
        f"Source Key: {inp.source_key}",
        f"Title: {inp.source_title}",
        f"Source URL: {inp.source_url}",
        "",
        "## Policy Document Content",
        inp.source_content,
    ]
    if inp.briefing:
        parts += ["", "## Briefing", inp.briefing]
    if inp.attachment_text:
        parts += ["", "## Attachment Content", inp.attachment_text]
    return "\n".join(parts)


def _parse_json_output(full_text: str) -> dict:
    """Extract the <json>...</json> block from the final assistant message."""
    match = _JSON_RE.search(full_text)
    if not match:
        raise ValueError(f"no <json> block in agent final response: {full_text[:300]!r}")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in agent response: {exc}") from exc
    _validate_impact_json(data)
    return data


def _validate_impact_json(data: dict) -> None:
    _validate_hts_modifications(data)
    scope_ids = _validate_scope_sets(data)
    _validate_measures(data, scope_ids)


def _validate_hts_modifications(data: dict) -> None:
    modifications = data.get("hts_modifications", [])
    if not isinstance(modifications, list):
        raise ValueError("impact JSON field 'hts_modifications' must be an array")

    for index, modification in enumerate(modifications):
        if not isinstance(modification, dict):
            raise ValueError(f"impact JSON hts_modifications {index} must be an object")
        for field in ("deleted", "inserted"):
            values = modification.get(field, [])
            if not isinstance(values, list):
                raise ValueError(f"impact JSON hts_modifications {index}.{field} must be an array")
            for value in values:
                _validate_heading_or_range(
                    value,
                    f"hts_modifications {index}.{field}",
                )


def _validate_scope_sets(data: dict) -> set[str]:
    scope_sets = data.get("scope_sets", [])
    if not isinstance(scope_sets, list):
        raise ValueError("impact JSON field 'scope_sets' must be an array")

    scope_ids: set[str] = set()
    for index, scope_set in enumerate(scope_sets):
        if not isinstance(scope_set, dict):
            raise ValueError(f"impact JSON scope_sets {index} must be an object")

        scope_id = scope_set.get("id")
        if not isinstance(scope_id, str) or not scope_id.strip():
            raise ValueError(f"impact JSON scope_sets {index}.id must be a non-empty string")
        if scope_id in scope_ids:
            raise ValueError(f"impact JSON scope_sets {index}.id is duplicated: {scope_id!r}")
        scope_ids.add(scope_id)

        headings = scope_set.get("headings", [])
        if not isinstance(headings, list):
            raise ValueError(f"impact JSON scope_sets {index}.headings must be an array")
        for heading in headings:
            _validate_heading_or_range(heading, f"scope_sets {index}.headings")

    return scope_ids


def _validate_measures(data: dict, scope_ids: set[str]) -> None:
    measures = data.get("measures", [])
    if not isinstance(measures, list):
        raise ValueError("impact JSON field 'measures' must be an array")

    for index, measure in enumerate(measures):
        if not isinstance(measure, dict):
            raise ValueError(f"impact JSON measure {index} must be an object")

        for legacy_field in ("includes_headings", "excludes_headings"):
            if legacy_field in measure:
                raise ValueError(
                    f"impact JSON measure {index} must use scope refs, not {legacy_field}"
                )

        heading_type = measure.get("measure_heading_type")
        if heading_type not in {"chapter99", "ordinary_hts", "note_only", "unknown"}:
            raise ValueError(
                f"impact JSON measure {index}.measure_heading_type must be chapter99, "
                "ordinary_hts, note_only, or unknown"
            )

        measure_heading = measure.get("measure_heading")
        if measure_heading is not None:
            if not isinstance(measure_heading, str) or not _HTS_HEADING_ONLY_RE.match(measure_heading):
                raise ValueError(
                    f"impact JSON measure {index}.measure_heading must be a concrete HTS heading or null"
                )
        elif heading_type in {"chapter99", "ordinary_hts"}:
            raise ValueError(
                f"impact JSON measure {index}.measure_heading is required for {heading_type}"
            )

        for field in ("affected_scope_refs", "excluded_scope_refs"):
            refs = measure.get(field, [])
            if not isinstance(refs, list):
                raise ValueError(f"impact JSON measure {index}.{field} must be an array")
            for ref in refs:
                if not isinstance(ref, str) or ref not in scope_ids:
                    raise ValueError(
                        f"impact JSON measure {index}.{field} contains unknown scope ref {ref!r}"
                    )

        for field in ("excluded_chapter99_headings", "superseded_chapter99_headings"):
            values = measure.get(field, [])
            if not isinstance(values, list):
                raise ValueError(f"impact JSON measure {index}.{field} must be an array")
            for value in values:
                _validate_heading_or_range(value, f"measure {index}.{field}")

        conditions = measure.get("conditions", [])
        if not isinstance(conditions, list):
            raise ValueError(f"impact JSON measure {index}.conditions must be an array")
        for condition in conditions:
            if not isinstance(condition, str):
                raise ValueError(f"impact JSON measure {index}.conditions entries must be strings")


def _validate_heading_or_range(value: object, context: str) -> None:
    if not isinstance(value, str) or not _HTS_HEADING_OR_RANGE_RE.match(value):
        raise ValueError(
            f"impact JSON {context} has invalid entry {value!r}; "
            "expected concrete HTS heading or range"
        )


def extract_policy_impact(
    llm: LLMProvider,
    inp: PolicyImpactInput,
) -> dict:
    """Run the agent loop and return the structured impact JSON dict.

    Raises ``ValueError`` if the agent output cannot be parsed.
    May raise provider API or network errors on transient failures.
    """
    response_text = llm.complete_with_tools(
        _SYSTEM_PROMPT,
        _build_user_message(inp),
        _TOOLS,
        _dispatch_tool,
        max_tokens=8192,
        max_iterations=20,
    )
    return _parse_json_output(response_text)
