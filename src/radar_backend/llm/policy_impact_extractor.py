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
_HTS_HEADING_RE = r"\d{4}(?:\.\d{2})?(?:(?:\.\d{2}){1,2}|\.\d{4})?"
_HTS_HEADING_ONLY_RE = re.compile(rf"^{_HTS_HEADING_RE}$")
_HTS_HEADING_OR_RANGE_RE = re.compile(
    rf"^{_HTS_HEADING_RE}(?:-{_HTS_HEADING_RE})?$"
)

_CURRENT_CHAPTER_99_PDF_URL = (
    "https://hts.usitc.gov/reststop/file?release=currentRelease&filename=Chapter%2099"
)

_TOP_LEVEL_FIELDS = {"source", "hts_modifications", "scope_sets", "measures"}
_SOURCE_FIELDS = {"type", "id", "url", "detected_at"}
_HTS_MODIFICATION_FIELDS = {"action", "note", "deleted", "inserted"}
_SCOPE_SET_FIELDS = {"id", "source", "note", "subdivision", "label", "headings"}
_MEASURE_FIELDS = {
    "measure_heading",
    "measure_heading_type",
    "note",
    "description",
    "ad_valorem_rate",
    "value_basis",
    "country_iso2",
    "is_potential",
    "effective_start_date",
    "effective_end_date",
    "affected_scope_refs",
    "excluded_scope_refs",
    "excluded_chapter99_headings",
    "superseded_chapter99_headings",
}

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
a. HTS codes explicitly listed in the source text
b. Differential modifications to HTS notes (for example, a U.S. note is modified by deleting
   one cross-reference list and inserting another)
c. New heading definitions with rates, descriptions, and applicability text

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
- Which HTS headings have changed rates, country scope, date windows,
  exclusions, or applicability because of the note modification
- Which HTS headings are included by each U.S. note subdivision reference. Put those
  concrete headings in reusable scope_sets. For example, if a measure applies to a
  subdivision range within a U.S. note, create one scope_set for each relevant subdivision
  and reference those scope_set IDs from the measure.

### Step 4: Look up rates for affected headings
For each affected HTS heading, call search_csv_rows with the most specific stable heading
prefix that will return the related Chapter 99 rows, for example the shared prefix for a
new heading group.

Extract from matching CSV rows: description, ad valorem rate, value basis, country scope.

### Step 5: Output JSON
Output the result wrapped in <json></json> tags as the final message. Do not include any text
after the closing </json> tag.

## Special case: source_key = hts_archive
- The attachment_text contains a change record; parse its entries
- Skip entries where Source field is "PP xxxxx", "Executive Order", or "Notice"
- Process remaining entries normally

## Output JSON format
The values below are illustrative placeholders showing field shapes. Do not copy placeholder
values into the final output; extract concrete values from the source evidence.
<json>
{
    "source": {
        "type": "proclamation",
        "id": "11002",
        "url": "https://example.gov/...",
        "detected_at": "<YYYY-MM-DD>"
    },
    "hts_modifications": [
        {
            "action": "replace",
            "note": "<note_number_or_null>",
            "deleted": ["<deleted_heading_from_source>"],
            "inserted": ["<inserted_heading_from_source>"],
            "effective_date": "<YYYY-MM-DD>"
        }
    ],
    "scope_sets": [
        {
            "id": "<scope_set_id>",
            "source": "us_note_subdivision",
            "note": "<note_number_or_null>",
            "subdivision": "<subdivision_reference_or_null>",
            "label": "<scope_label>",
            "headings": ["<ordinary_hts_heading_or_range_from_source>"]
        },
        {
            "id": "<direct_scope_set_id>",
            "source": "direct_hts_modification",
            "note": null,
            "subdivision": null,
            "label": "<direct_heading_scope_label>",
            "headings": ["<ordinary_hts_heading_from_source>"]
        }
    ],
    "measures": [
        {
            "measure_heading": "<measure_heading_from_source>",
            "measure_heading_type": "chapter99",
            "note": "<note_number_or_null>",
            "description": "<source_supported_description>",
            "ad_valorem_rate": 50.0,
            "value_basis": "CIF",
            "country_iso2": null,
            "is_potential": false,
            "effective_start_date": "<YYYY-MM-DD_or_null>",
            "effective_end_date": null,
            "affected_scope_refs": ["<scope_set_id>"],
            "excluded_scope_refs": [],
            "excluded_chapter99_headings": [],
            "superseded_chapter99_headings": ["<superseded_heading_from_source>"]
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
- For Chapter 99 measures, derive ad_valorem_rate from the HTS table's General duty-rate
  column for that heading when available.
  - If the General column says "The duty provided in the applicable subheading + X%" or
    equivalent, set ad_valorem_rate = X.0.
  - If the General column contains a standalone ad valorem percentage such as "10%" or
    "15%", set ad_valorem_rate to that numeric percentage.
  - If the General column contains no numeric ad valorem percentage and states "No change",
    "The duty provided in the applicable subheading", ordinary duty only, duty-free, or no
    additional duty, set ad_valorem_rate = 0.0.
  - Do not change ad_valorem_rate because another mutually exclusive heading may apply under
    different applicability rules; capture Chapter 99 priority in excluded_chapter99_headings
    when source-supported.
- effective_start_date format: YYYY-MM-DD or null
- All fields named note must be an integer note number or null. Do not put U.S. note labels,
  subdivision references, or prose in note fields; use subdivision or label when string context
  is allowed by the schema.
- effective_start_date is the effective date of the measure_heading carrier itself.
- For Chapter 99 measures, use the Annex/HTS amendment effective date for the Chapter 99 heading
  or governing Chapter 99 note. Do not use a different proclamation operative date unless the
  proclamation directly creates or starts the Chapter 99 carrier without a separate Annex/HTS
  effective date.
- Each non-null measure_heading must appear at most once in measures. Do not split one HTS
  heading into multiple measures for different company groups, country groups, date windows,
  rate phases, or eligibility groups.
- If one HTS heading has staggered effective dates for different company groups,
  country groups, or eligibility groups, keep a single measure for that heading. Use the earliest
  carrier effective_start_date.
- For date-only effective_end_date, use the previous calendar date only when the
  measure's own heading, rate, or eligibility treatment is terminated, deleted, or
  superseded effective at 12:01 a.m. on the next date. Do not end a measure merely
  because a note subdivision is renumbered or an article-description reference is
  conformed while the same heading and duty treatment continue.
- If a heading is inserted or active for a period and later terminated or deleted on
  a future effective date, do not add a separate hts_modifications.deleted entry for
  that future deletion because hts_modifications has no date field. Keep the heading
  as a measure and set effective_end_date to the last applicable calendar date. Do not add
  effective_at or any other field outside the schema.
- When HTS heading text says "Except as provided for in heading(s) X, Y, Z" or
  equivalent wording that gives other Chapter 99 headings priority over the current
  heading, put those referenced Chapter 99 headings in excluded_chapter99_headings.
  This is a source-supported heading carveout, not an audit error.
- If one heading has phased or future rate changes, keep one measure for the heading.
  Set ad_valorem_rate to the earliest/current directly applicable policy rate for that heading.
- Use excluded_chapter99_headings only for Chapter 99 heading priority carveouts or
  explicit Chapter 99 exceptions. Use excluded_scope_refs only for product-scope exclusions
  represented by Chapter 99 scope_sets.
- hts_modifications.deleted and hts_modifications.inserted must contain only
  concrete HTS headings. Do not use compact ranges in these arrays. If the source lists or
  implies a contiguous Chapter 99 heading span, expand it into every discrete heading in that
  span using the same decimal depth as the endpoints. Do not put note-text placeholders such as
  "prior U.S. note text", "new U.S. note text", or a U.S. note reference in these
  arrays. If exact headings cannot be determined after
  using the tools, use [] rather than a prose placeholder.
- scope_sets[].headings must be arrays of concrete HTS headings or HTS heading ranges
  only. Do not put prose, note citations, countries, or product descriptions
  in headings arrays.
- scope_sets are for Chapter 99 measures that need product-scope include/exclude modeling.
  Do not create scope_sets merely to repeat a Chapter 98 or ordinary HTS measure_heading.
- measures must not contain includes_headings or excludes_headings. Use
  affected_scope_refs and excluded_scope_refs to reference scope_sets only for Chapter 99
  scope modeling. For chapter98 and ordinary_hts measures, affected_scope_refs and
  excluded_scope_refs should be empty because the measure_heading itself identifies the
  affected provision.
- measure_heading is the HTS heading that carries the policy measure. It may be a
  Chapter 99 heading, a Chapter 98 special classification heading/subheading, a Chapter 1-97
  ordinary HTS heading/subheading, or null for note-only changes. Set measure_heading_type to
  one of: "chapter99", "chapter98", "ordinary_hts", "note_only", "unknown". Use
  "chapter98" for HTS headings/subheadings beginning with 98 other than Chapter 99.
- effective_start_date is the effective date of the measure_heading carrier itself. For Chapter
  99 measures, use the Annex/HTS amendment effective date for the Chapter 99 heading or governing
  Chapter 99 note. For Chapter 1-98 measures, use the affected HTS provision's own
  effective/applicability date from the governing HTS chapter, heading text, note, or amendment.
- measures must not contain duplicate non-null measure_heading values. If the same heading has
  phased rates or staggered applicability, merge the details into one measure.
- excluded_chapter99_headings and superseded_chapter99_headings must contain discrete Chapter
  99 headings only. Expand any source-supported Chapter 99 heading span into individual headings.
- When a measure applies to a U.S. note subdivision range,
  read the note text and expand every subdivision in that range into scope_sets with
  actual HTS headings/ranges. If the exact headings cannot be determined after using
  the tools, create the scope_set with an empty headings array rather than prose.
"""

_AUDIT_SYSTEM_PROMPT = """\
You are a senior trade compliance auditor reviewing a policy impact JSON.

Your task is to determine whether the JSON is fully supported by the policy document,
attachments, and HTS evidence. You must read every hts_modifications entry, scope_set,
and measure carefully. Do not assume the JSON is correct. Audit only: identify issues
and recommended fixes, but do not output a corrected impact JSON.

Use the available tools to inspect HTS Chapter 99 PDF pages, HTS CSV rows, and URLs when
needed. Every heading, duty rate, effective date, country scope, note reference, condition,
scope reference, exclusion, and supersession must be supported by the provided policy text,
attachment text, or HTS evidence.

Evidence hierarchy and interpretation:
- The proclamation's operative clauses are primary legal authority. Annex tables and HTS
  amendment text implement those clauses and are also binding.
- HTS PDF/CSV evidence confirms heading existence, descriptions, note text, and tariff
  structure.
- If the proclamation body states a tariff rate directly, such as "shall be 10 percent" or
  "shall be 20 percent", that supports ad_valorem_rate = 10.0 or 20.0.
- For Chapter 99 measures, audit ad_valorem_rate against the HTS table's General duty-rate
  column, not against whether the heading is default, overridden, mutually exclusive, or
  conditionally displaced.
  - "The duty provided in the applicable subheading + X%" supports ad_valorem_rate = X.0.
  - A standalone General rate such as "10%" or "15%" supports that numeric
    ad_valorem_rate.
  - "No change", "The duty provided in the applicable subheading", ordinary duty only,
    duty-free, or no-additional-duty wording with no numeric percentage supports
    ad_valorem_rate = 0.0.
- If an Annex or HTS table says "applicable subheading + X%", ad_valorem_rate should still be
  X.0 when X is the policy ad valorem increment or policy rate. Preserve the additive or cap
  formula in the supported measure fields.
- Do not mark ad_valorem_rate as unsupported merely because the Annex uses formula wording
  rather than a standalone percentage, as long as the numeric rate is correct and the legal
  formula is supported by source evidence.
- Do not report an ad_valorem_rate error when the General column numeric rate is correct but
  the JSON could better explain default applicability, mutual exclusivity, lower-rate
  overrides, or heading priority. Report as a warning unless the missing structured value would
  cause a concrete wrong calculation.
- A Chapter 99 heading is supported as inserted if it is listed in the Annex, HTS amendment
  table, or inserted heading list, even if note subdivisions do not separately define every
  heading in prose.
- Do not reject inserted headings solely because the note text lacks a dedicated subdivision
  for that exact heading.
- In hts_modifications.deleted, hts_modifications.inserted, excluded_chapter99_headings, and
  superseded_chapter99_headings, Chapter 99 heading spans must be represented as discrete
  headings, not compact ranges. If a source lists a contiguous Chapter 99 span, a JSON array with
  every heading in the span is required; expand using the same decimal depth as the endpoints.
  Treat compact Chapter 99 range strings in these fields as repairable schema-style issues.
- For zero-rate or no-additional-duty headings, create or preserve measures when the heading
  represents a legally material tariff treatment, exemption, exclusion, or no-duty category.
- Source-supported zero-tariff, no-additional-duty, or surcharge-exclusion lists are
  review-supporting context. If those HTS codes are captured in a scope_set with
  source/label indicating the cited exclusion authority, do not fail the audit solely
  because there is no separate measure for that scope. Treat unclear exclusion-list modeling as a
  warning for human review unless it contradicts a tariff rate, heading, or applicability rule.
- If a heading's duty treatment says "the duty provided in the applicable subheading",
  "no additional duty", "zero tariff", "shall be zero", or equivalent wording, then
  ad_valorem_rate = 0.0 is valid when the measure represents a legally material zero-rate
  or no-additional-duty category. Do not require
  ad_valorem_rate = null solely because the legal text expresses the treatment as ordinary
  subheading duty rather than a standalone "0%" phrase.
- hts_modifications is heading-oriented. Note subdivision deletion, renumbering, redesignation,
  or conforming article-description changes are useful context, but missing those textual note
  mechanics is not an error by itself when the affected headings, rates, dates, and structured
  applicability are captured. Treat this as a warning or clarifying recommendation unless the
  omission creates a factual contradiction or drops a legally material heading, rate, date, or
  applicability rule.
- If a heading is inserted or active for a period and later terminated or deleted on a future
  effective date, hts_modifications must not add a separate date-less deleted entry for that
  future deletion. Represent the legal effect on the corresponding measure by setting
  effective_end_date to the last applicable calendar date. Do not add effective_at or any other
  schema field.
- If HTS heading text says "Except as provided for in heading(s) X, Y, Z" or equivalent wording,
  the referenced Chapter 99 headings are valid excluded_chapter99_headings for that measure.
  This captures a source-supported priority carveout where the current heading does not apply if
  one of those other Chapter 99 headings applies. Preserve those excluded_chapter99_headings and
  do not mark them as errors merely because the support appears in heading text rather than a
  separate note paragraph.
- If one heading has phased or future rate changes, one measure for that heading is valid. The
  ad_valorem_rate scalar should be the earliest/current directly applicable policy rate, while
  later rate phases, dates, and eligibility groups may not fit this schema. Do not fail the audit
  merely because a later phase is not represented when the scalar field is source-supported.
- Use excluded_chapter99_headings only for Chapter 99 heading priority carveouts or explicit
  Chapter 99 exceptions. Use excluded_scope_refs only for product-scope exclusions represented
  by Chapter 99 scope_sets.
- Use scope_sets only for Chapter 99 measures that need product-scope include/exclude modeling.
  For chapter98 and ordinary_hts measures, do not require scope_sets, affected_scope_refs, or
  excluded_scope_refs; the measure_heading itself identifies the affected HTS provision.
- Chapter 98 headings/subheadings must use measure_heading_type="chapter98", not
  "ordinary_hts" and not "note_only" when the legal change expressly affects a named Chapter 98
  heading/subheading.
- Standalone general note changes may be omitted unless they are needed to represent a concrete
  affected HTS heading, rate, date, country, or user-facing review impact. Do not fail audit
  solely because standalone general note modifications are absent.
- Do not infer required note deletion, note renumbering, or conforming text changes beyond
  explicit source evidence. If the source support is ambiguous, report a warning instead of an
  error and keep the supported heading-level data.
- If evidence appears in different sections, evaluate the proclamation body, Annexes,
  attachment text, and HTS evidence together before marking data unsupported.
- Effective-date review must be text-grounded and local to the measure_heading carrier:
  - effective_start_date is the effective date of the measure_heading carrier itself.
  - Read the exact date from the same operative clause, Annex header, note instruction, heading
    instruction, table row, or governing HTS chapter/note text that supports the audited field.
  - Do not rely on memory, nearby examples, publication dates, detected_at dates, current HTS
    archive dates, or dates from a different clause unless the source expressly connects them.
  - For Chapter 99 measures, audit effective_start_date against the Annex/HTS amendment
    effective date for the Chapter 99 heading or governing Chapter 99 note. Do not use a
    different proclamation operative date unless the proclamation directly creates or starts the
    Chapter 99 carrier without a separate Annex/HTS effective date.
  - For Chapter 1-98 provisions, especially Chapter 98, audit effective_start_date and
    effective_end_date against the relevant HTS chapter text, U.S. note, or heading text for the
    provision's own applicability period. If the JSON uses a later technical amendment effective
    date as effective_start_date while HTS text shows an earlier provision start date, report an
    error and recommend the provision's own start date.
  - If a policy deletes an end-date phrase from an existing HTS provision and no remaining
    source-supported end date applies, effective_end_date should be null.
  - If different source sections state different dates, first determine whether they govern
    different legal effects (for example, tariff liability, HTS text modification, temporary
    eligibility, or termination). Use the date that governs the measure_heading carrier.
  - Report a date error only when the JSON date conflicts with a quoted source date for the same
    carrier effective date. If the governing date is ambiguous after reading the cited text,
    report a warning rather than asserting a corrected date.

Audit requirements:
1. Every hts_modifications.inserted heading must be supported by source text or HTS evidence.
2. Every hts_modifications.deleted heading must be supported by source text or HTS evidence.
3. A heading must not appear in both deleted and inserted in the same modification unless the
   source explicitly shows a textual replacement for the same heading. If this is valid, explain
   why in issues as a warning; otherwise correct it.
4. Every inserted Chapter 99 heading that creates or changes a tariff, duty rate, exemption,
   condition, date window, exclusion, or applicability rule must have a corresponding measure.
5. Every measure_heading must be supported by source text or HTS evidence.
6. measure_heading_type must match the heading: Chapter 99 headings are chapter99; Chapter 98
   headings/subheadings are chapter98; Chapter 1-97 headings are ordinary_hts; note-level changes
   without a carrier heading are note_only.
7. Every measure's rate, country scope, effective date, excluded heading, superseded heading,
   affected_scope_refs, and excluded_scope_refs must be supported by evidence.
8. scope_sets must contain only headings/ranges supported by source text or HTS evidence.
   Chapter 99 heading arrays outside scope_sets must use discrete headings, not compact ranges.
   Every note field in hts_modifications, scope_sets, and measures must be an integer or null.
9. Report unsupported data as issues. Do not invent headings, rates, dates, or countries.
10. If exact data cannot be verified, report a recommended_fix that keeps only what is supported
    and uses null/unknown/note_only according to the schema.
11. Do not block the audit merely because a heading-level hts_modifications record omits note
    subdivision renumbering, redesignation, or conforming prose changes. Flag those as warnings
    unless the omission makes a heading, rate, date, condition, exclusion, or supersession wrong.
12. Do not block the audit merely because source-supported zero-tariff, no-additional-duty, or
    surcharge-exclusion HTS codes are represented as a scope_set rather than as a separate
    measure. If the exclusion list is present and source-supported, and no conflicting nonzero
    rate is assigned to those headings, pass this aspect or report only a warning for human
    review.
13. Every non-null measure_heading must appear at most once in measures. If the same heading is
    duplicated because of different annex-listed groups, non-annex-listed groups, phased rate
    changes, country scope, or eligibility groups, merge those records into one measure. Use the
    carrier effective_start_date for that heading. Report duplicate records for the same
    measure_heading as errors.
14. Issues are findings for the repair step. When you identify an unsupported or contradictory
    value, report a specific json_path, source evidence, and recommended_fix. Put all findings
    only in issues; do not output a separate errors field because error issues are already a
    filtered subset of issues. Do not output a corrected impact JSON and do not claim that you
    already fixed the JSON.
15. Use severity="error" only when the current JSON cannot be trusted until repaired. Use
    severity="warning" for source-supported ambiguity or human-review notes.
16. For date-only effective_end_date fields, apply cutoff semantics when evaluating correctness:
    if the source says coverage ends "before 12:01 a.m." on a date, or a replacement becomes
    effective "on or after 12:01 a.m." on a date, the inclusive date-only end date for the old
    treatment is the previous calendar date. This applies only when the measure's own heading,
    rate, or eligibility treatment terminates, is deleted, or is superseded. If the source only
    renumbers a note subdivision or conforms an article description while the same heading and
    duty treatment continue, effective_end_date should be null.
17. If a future-dated heading termination/deletion is already reflected by a measure's
    effective_end_date, report any separate hts_modifications.deleted record that
    would make the same heading look both inserted/active and deleted without date context.
18. When a measure's heading text contains an explicit "Except as provided for in headings ..."
    carveout, the referenced Chapter 99 headings should remain in excluded_chapter99_headings.
    Do not remove source-supported excluded_chapter99_headings or report them as errors.
19. Do not fail audit solely because hts_modifications omits note text edits or general note
    edits when concrete heading-level impacts are captured in measures.
20. Do not output patch instructions, repair operations, repairs arrays, or a corrected impact
    JSON. The repair step is responsible for producing the complete corrected JSON.

Impact JSON schema:
- Top-level fields: source, hts_modifications, scope_sets, measures.
- source: an object. Keep only source metadata from extraction; do not add audit metadata.
- hts_modifications[] fields: action, note, deleted, inserted.
  - action: string such as "insert", "delete", "modify", or "replace".
  - note: integer or null.
  - deleted: array of concrete HTS headings. For Chapter 99 headings, use discrete headings,
    not compact ranges.
  - inserted: array of concrete HTS headings. For Chapter 99 headings, use discrete headings,
    not compact ranges.
- scope_sets[] fields: id, source, note, subdivision, label, headings.
  - id: non-empty string.
  - source: string or null.
  - note: integer or null.
  - subdivision: string or null.
  - label: string or null.
  - headings: array of concrete HTS headings/ranges. Ordinary HTS ranges are allowed here when
    source-supported.
- measures[] fields: measure_heading, measure_heading_type, note, description,
  ad_valorem_rate, value_basis, country_iso2, is_potential, effective_start_date,
  effective_end_date, affected_scope_refs, excluded_scope_refs, excluded_chapter99_headings,
  superseded_chapter99_headings.
  - measure_heading: concrete HTS heading or null.
  - measure_heading_type: "chapter99", "chapter98", "ordinary_hts", "note_only", or "unknown".
  - note: integer or null.
  - description: string or null.
  - ad_valorem_rate: number or null. Do not use strings such as "10%".
  - value_basis: string or null.
  - country_iso2: two-letter ISO country code string or null.
  - is_potential: boolean.
  - effective_start_date: YYYY-MM-DD string or null.
  - effective_end_date: YYYY-MM-DD string or null.
  - affected_scope_refs and excluded_scope_refs: arrays of scope_sets[].id strings. These should
    normally be empty for chapter98 and ordinary_hts measures.
  - excluded_chapter99_headings and superseded_chapter99_headings: arrays of discrete concrete
    Chapter 99 headings. Do not use compact ranges in these fields.

Severity:
- Use severity="error" only for factual contradictions, unsupported headings/rates/dates/
  missing legally material measures, invalid schema semantics, or corrections that are required
  before the JSON can be trusted.
- Use severity="warning" for representational ambiguity where the data is source-supported but
  could be clearer.
- Use severity="warning", not severity="error", when source-supported zero-tariff,
  no-additional-duty, or surcharge-exclusion HTS codes are modeled only as a scope_set. Human
  review will decide whether a separate zero-rate measure should be added.
- If the rate scalar matches the General duty-rate column under the rules above, do not use
  severity="error" for ad_valorem_rate. Applicability ambiguity is normally a warning.
- If a heading's General column wording is "The duty provided in the applicable subheading +
  X%" or equivalent, ad_valorem_rate = X.0 is correct. If the measure needs clearer default,
  override, or mutual-exclusivity modeling, do not mark the numeric rate wrong.
- If a zero-rate/no-additional-duty heading is modeled with ad_valorem_rate = 0.0 and the
  source supports the "duty provided in the applicable subheading" or no-additional-duty wording,
  do not mark it as an error.
- If excluded_chapter99_headings comes from explicit heading-text carveout wording such as
  "Except as provided for in headings X, Y, and Z", it is source-supported and structurally
  valid. Do not mark this as an error.
- Use severity="error" for compact Chapter 99 range strings only when they appear in
  hts_modifications.deleted, hts_modifications.inserted, excluded_chapter99_headings, or
  superseded_chapter99_headings and can be expanded into discrete source-supported headings.
- Do not mark a source-supported earliest/current directly applicable ad_valorem_rate as an error
  merely because later phased rate details do not fit the scalar field.
- Missing note subdivision renumbering, redesignation, or conforming article-description prose is
  normally a warning. Escalate it to an error only when it changes the extracted legal effect.
- Use severity="error" only for remaining problems in the current impact JSON.
- recommended_fix must be concise and directly machine-usable by the repair step. Prefer a JSON
  object or scalar containing the target result, for example {"effective_end_date":"2027-12-31"}
  or {"effective_end_date":null}. Do not write paragraphs, alternatives, explanations, or
  conditional prose inside recommended_fix.
- If a heading's same duty treatment continues after a note renumbering or conforming description
  change, effective_end_date must be null. Do not infer termination from renumbering alone.
- Duplicate non-null measure_heading values are invalid. Merge duplicates into one measure and
  keep only schema-supported fields.

Examples:
- If the source states a tariff rate for products meeting a country-specific eligibility rule
  and an Annex says "applicable subheading + X%", this supports ad_valorem_rate = X.0 when
  the country-specific eligibility formula supports that heading. This is not an error.
- If the source states an ad valorem duty rate for products meeting a named eligibility program,
  this supports the corresponding numeric ad_valorem_rate even if the Annex table phrases the
  HTS treatment as "applicable subheading + X%".
- If an Annex or inserted heading list includes a contiguous Chapter 99 heading group, then
  intermediate headings in that source-supported span are supported as inserted headings even if
  the surrounding note subdivisions are sparse. Represent the group as discrete headings in
  Chapter 99 heading arrays, not as a compact range string.
- If a heading says "The duty provided in the applicable subheading" or equivalent
  no-additional-duty wording, ad_valorem_rate = 0.0 is acceptable when the source supports that
  wording. Do not require null for this legally material zero-rate treatment.
- If a source deletes one heading and also includes related note subdivision renumbering or
  conforming text for another surviving heading, the heading deletion and surviving measure can
  still be valid even if hts_modifications does not separately encode the note-text mechanics.
  Recommend a clarifying warning if useful; do not fail the audit unless a supported
  heading/rate/date/applicability rule is actually wrong or missing.
- If an Annex terminates one heading effective on a future date but only renumbers the note
  reference for another heading, the terminated measure ends on the previous calendar date and
  the surviving measure continues with effective_end_date = null.
- If a proclamation gives one effective_start_date for one eligibility group and a later
  effective_start_date for another group, keep one measure for each affected heading and use the
  carrier effective_start_date.

Return only a JSON audit result wrapped in <json></json>. Do not include text after </json>.

Output format:
<json>
{
  "verdict": "pass",
  "issues": [
    {
      "severity": "warning",
      "json_path": "hts_modifications[0].inserted[0]",
      "problem": "Short explanation of the issue or concern.",
      "source_evidence": "Specific supporting source text, attachment text, or HTS evidence.",
      "recommended_fix": {"effective_end_date": "2027-12-31"}
    }
  ]
}
</json>

Use verdict "pass" only when the current impact JSON is complete, internally consistent, and
fully supported. Use verdict "fail" when the current impact JSON still has error-level problems
that must be repaired before storage.
"""

_REPAIR_SYSTEM_PROMPT = """\
You are a senior trade compliance editor repairing a policy impact JSON.

Your task is mechanical repair only. Do not audit, verify, question, reinterpret, or explain the
audit findings. Directly apply the audit error issues to the current impact JSON.

Rules:
- Output only the repaired complete impact JSON wrapped in <json></json>. The first non-space
  characters of the response must be <json> and the last non-space characters must be </json>.
- Do not output verdict, issues, repairs, comments, markdown, or explanatory text.
- Do not output apologies, limitations, analysis, patch instructions, bullets, or any text
  outside the <json> block. JSON object keys and string values must use double quotes, with no
  trailing commas and no comments.
- Do not decide whether an audit error is correct. Do not re-check source_evidence. Do not look
  up source documents, HTS PDFs, URLs, or external data. Do not call tools.
- Apply every error issue to the current JSON. Treat recommended_fix as the instruction/result
  to apply. If recommended_fix contains a scalar, object, array, or null, copy that target value
  into the referenced json_path or merge the object into the referenced object.
- If recommended_fix is textual, perform the direct edit it names without adding new analysis.
- If multiple error issues touch the same object, apply all of them and keep the JSON schema
  valid.
- Keep exactly the impact JSON schema: source, hts_modifications, scope_sets, measures.
- Fields named note must be integers or null. Do not put U.S. note labels, subdivision labels, or
  prose in note fields; use labels only where the schema allows string context.
- The repaired JSON must be complete. Do not return prose, partial JSON, an empty response, or a
  non-schema object.
- Do not create duplicate non-null measure_heading values. Merge duplicate headings into one
  measure and keep only schema-supported fields.
- Expand compact Chapter 99 heading ranges into discrete headings in hts_modifications.deleted,
  hts_modifications.inserted, excluded_chapter99_headings, and superseded_chapter99_headings.
  Expand using the same decimal depth as the range endpoints. Do not output compact Chapter 99
  range strings in those fields.
- Use scope_sets, affected_scope_refs, and excluded_scope_refs only for Chapter 99 scope
  modeling. Keep affected_scope_refs and excluded_scope_refs empty for chapter98 and ordinary_hts
  measures.
- Keep hts_modifications heading-oriented. Do not include note-only changes with empty
  deleted/inserted arrays as heading modifications.
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


def _build_audit_user_message(
    inp: PolicyImpactInput,
    impact_json: dict,
    *,
    audit_round: int,
    previous_error_issues: list[dict] | None = None,
) -> str:
    parts = [
        f"Audit Round: {audit_round}",
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
    if previous_error_issues:
        parts += [
            "",
            "## Previous Audit Errors",
            "The list below contains previous audit error issues, not impact JSON. Re-check "
            "these prior error issues against the current JSON and source evidence. If the "
            "current JSON has fixed an issue, do not repeat it.",
            json.dumps(previous_error_issues, ensure_ascii=False, indent=2, sort_keys=True),
        ]
    parts += [
        "",
        "## Impact JSON To Audit",
        json.dumps(impact_json, ensure_ascii=False, indent=2, sort_keys=True),
    ]
    return "\n".join(parts)


def _build_repair_user_message(
    inp: PolicyImpactInput,
    impact_json: dict,
    error_issues: list[dict],
    *,
    audit_round: int,
) -> str:
    parts = [
        f"Repair After Audit Round: {audit_round}",
        f"Policy Update ID: {inp.policy_update_id}",
        f"Source Key: {inp.source_key}",
        f"Title: {inp.source_title}",
        "",
        "## Audit Errors To Fix",
        "Fix these errors directly in the repaired JSON. Use the recommended_fix values as the "
        "source of truth. Do not restate errors as issues.",
        json.dumps(error_issues, ensure_ascii=False, indent=2, sort_keys=True),
        "",
        "## Current Impact JSON To Repair",
        json.dumps(impact_json, ensure_ascii=False, indent=2, sort_keys=True),
    ]
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


def _parse_audit_output(full_text: str) -> dict:
    """Extract and validate the audit <json>...</json> result."""
    match = _JSON_RE.search(full_text)
    if not match:
        raise ValueError(f"no <json> block in audit response: {full_text[:300]!r}")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in audit response: {exc}") from exc

    _validate_allowed_fields(data, {"verdict", "issues"}, "audit result")

    verdict = data.get("verdict")
    if verdict not in {"pass", "fail"}:
        raise ValueError("impact audit verdict must be pass or fail")

    issues = data.get("issues")
    if not isinstance(issues, list):
        raise ValueError("impact audit issues must be an array")
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            raise ValueError(f"impact audit issue {index} must be an object")
        severity = issue.get("severity")
        if severity not in {"error", "warning"}:
            raise ValueError(f"impact audit issue {index}.severity must be error or warning")

    return data


def _validate_impact_json(data: dict) -> None:
    _validate_allowed_fields(data, _TOP_LEVEL_FIELDS, "top-level")
    source = data.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("impact JSON field 'source' must be an object")
    _validate_allowed_fields(source, _SOURCE_FIELDS, "source")
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
        _validate_allowed_fields(
            modification,
            _HTS_MODIFICATION_FIELDS,
            f"hts_modifications {index}",
        )
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
        _validate_allowed_fields(scope_set, _SCOPE_SET_FIELDS, f"scope_sets {index}")

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

    measure_headings: set[str] = set()
    for index, measure in enumerate(measures):
        if not isinstance(measure, dict):
            raise ValueError(f"impact JSON measure {index} must be an object")

        for legacy_field in ("includes_headings", "excludes_headings"):
            if legacy_field in measure:
                raise ValueError(
                    f"impact JSON measure {index} must use scope refs, not {legacy_field}"
                )
        _validate_allowed_fields(measure, _MEASURE_FIELDS, f"measure {index}")

        heading_type = measure.get("measure_heading_type")
        if heading_type not in {"chapter99", "chapter98", "ordinary_hts", "note_only", "unknown"}:
            raise ValueError(
                f"impact JSON measure {index}.measure_heading_type must be chapter99, "
                "chapter98, ordinary_hts, note_only, or unknown"
            )

        measure_heading = measure.get("measure_heading")
        if measure_heading is not None:
            if not isinstance(measure_heading, str) or not _HTS_HEADING_ONLY_RE.match(measure_heading):
                raise ValueError(
                    f"impact JSON measure {index}.measure_heading must be a concrete HTS heading or null"
                )
        elif heading_type in {"chapter99", "chapter98", "ordinary_hts"}:
            raise ValueError(
                f"impact JSON measure {index}.measure_heading is required for {heading_type}"
            )
        if measure_heading is not None:
            if measure_heading in measure_headings:
                raise ValueError(
                    f"impact JSON measure {index}.measure_heading is duplicated: {measure_heading!r}"
                )
            measure_headings.add(measure_heading)

        for field in ("affected_scope_refs", "excluded_scope_refs"):
            refs = measure.get(field, [])
            if not isinstance(refs, list):
                raise ValueError(f"impact JSON measure {index}.{field} must be an array")
            if heading_type in {"chapter98", "ordinary_hts"} and refs:
                raise ValueError(
                    f"impact JSON measure {index}.{field} must be empty for {heading_type}"
                )
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


def _validate_heading_or_range(value: object, context: str) -> None:
    if not isinstance(value, str) or not _HTS_HEADING_OR_RANGE_RE.match(value):
        raise ValueError(
            f"impact JSON {context} has invalid entry {value!r}; "
            "expected concrete HTS heading or range"
        )


def _validate_allowed_fields(data: dict, allowed_fields: set[str], context: str) -> None:
    extra_fields = sorted(set(data) - allowed_fields)
    if extra_fields:
        raise ValueError(
            f"impact JSON {context} has unknown field(s): {', '.join(extra_fields)}"
        )


def audit_policy_impact(
    llm: LLMProvider,
    inp: PolicyImpactInput,
    impact_json: dict,
    *,
    max_audit_rounds: int = 3,
) -> dict:
    """Audit and, if needed, repair an extracted impact JSON using the LLM.

    The audit LLM verifies every detail against source text, attachments, and HTS
    evidence accessible through the same tool set used by extraction. Audit reports
    issues only; repairs are generated by ``repair_policy_impact``. The JSON is returned
    only after an audit round reports no remaining error-level issues.
    """
    current_json = impact_json
    last_audit: dict | None = None
    previous_error_issues: list[dict] | None = None

    for audit_round in range(1, max_audit_rounds + 1):
        response_text = llm.complete_with_tools(
            _AUDIT_SYSTEM_PROMPT,
            _build_audit_user_message(
                inp,
                current_json,
                audit_round=audit_round,
                previous_error_issues=previous_error_issues,
            ),
            _TOOLS,
            _dispatch_tool,
            max_tokens=16384,
            max_iterations=20,
        )
        audit = _parse_audit_output(response_text)
        last_audit = audit

        error_issues = _audit_error_issues(audit)
        logger.info(
            "policy_impact_audit: policy_update_id=%s round=%s verdict=%s issues=%s",
            inp.policy_update_id,
            audit_round,
            audit["verdict"],
            audit["issues"],
        )
        if not error_issues:
            return current_json
        if error_issues and audit_round < max_audit_rounds:
            current_json = repair_policy_impact(
                llm,
                inp,
                current_json,
                error_issues,
                audit_round=audit_round,
            )
            logger.info(
                "policy_impact_audit: policy_update_id=%s round=%s repaired error_count=%s",
                inp.policy_update_id,
                audit_round,
                len(error_issues),
            )
        previous_error_issues = error_issues

    assert last_audit is not None
    error_summary = _audit_issue_summary(_audit_error_issues(last_audit))
    if not error_summary:
        error_summary = f"verdict={last_audit['verdict']} after {max_audit_rounds} audit rounds"
    raise ValueError(f"impact audit failed: {error_summary}")


def repair_policy_impact(
    llm: LLMProvider,
    inp: PolicyImpactInput,
    impact_json: dict,
    error_issues: list[dict],
    *,
    audit_round: int,
) -> dict:
    """Repair a failed audit result into a complete candidate JSON for the next audit round."""
    response_text = llm.complete(
        _REPAIR_SYSTEM_PROMPT,
        _build_repair_user_message(
            inp,
            impact_json,
            error_issues,
            audit_round=audit_round,
        ),
        max_tokens=16384,
    )
    return _parse_json_output(response_text)


def _audit_error_issues(audit: dict) -> list[dict]:
    return [
        issue
        for issue in audit.get("issues", [])
        if isinstance(issue, dict) and issue.get("severity") == "error"
    ]


def _audit_issue_summary(issues: list[dict], *, limit: int = 3) -> str:
    parts = []
    for issue in issues[:limit]:
        path = issue.get("json_path") or "<unknown path>"
        problem = issue.get("problem") or "<no problem text>"
        parts.append(f"{path}: {problem}")
    if len(issues) > limit:
        parts.append(f"... {len(issues) - limit} more error(s)")
    return "; ".join(parts)


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
    impact_json = _parse_json_output(response_text)
    logger.info("impact_json:%s", impact_json)
    impact_json_audit = audit_policy_impact(llm, inp, impact_json)
    logger.info("impact_json_audit:%s", impact_json_audit)
    return impact_json_audit
