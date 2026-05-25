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
    "conditions",
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
- Each non-null measure_heading must appear at most once in measures. Do not split one HTS
  heading into multiple measures for different company groups, country groups, date windows,
  rate phases, or eligibility groups. Use one measure per heading and preserve those differences
  in conditions.
- If one HTS heading has staggered effective dates for different company groups,
  country groups, or eligibility groups, keep a single measure for that heading. Use the earliest
  applicable effective_start_date and describe the later effective dates and affected groups in
  conditions.
- For date-only effective_end_date, use the previous calendar date only when the
  measure's own heading, rate, or eligibility treatment is terminated, deleted, or
  superseded effective at 12:01 a.m. on the next date. Do not end a measure merely
  because a note subdivision is renumbered or an article-description reference is
  conformed while the same heading and duty treatment continue.
- If a heading is inserted or active for a period and later terminated or deleted on
  a future effective date, do not add a separate hts_modifications.deleted entry for
  that future deletion because hts_modifications has no date field. Keep the heading
  as a measure, set effective_end_date to the last applicable calendar date, and
  preserve the future termination/deletion wording in conditions. Do not add
  effective_at or any other field outside the schema.
- When HTS heading text says "Except as provided for in heading(s) X, Y, Z" or
  equivalent wording that gives other Chapter 99 headings priority over the current
  heading, put those referenced Chapter 99 headings in excluded_chapter99_headings.
  This is a source-supported heading carveout, not an audit error. Also preserve the
  prose in conditions when useful for human review.
- If one heading has phased or future rate changes, keep one measure for the heading.
  Set ad_valorem_rate to the earliest/current directly applicable policy rate for that
  heading and preserve later rate phases, dates, and eligibility groups in conditions.
- Use excluded_chapter99_headings only for Chapter 99 heading priority carveouts or
  explicit Chapter 99 exceptions. Use excluded_scope_refs for ordinary HTS product
  scope exclusions represented by scope_sets. Use conditions for text-only limitations
  that cannot be expressed as either of those structured fields.
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
- measures must not contain duplicate non-null measure_heading values. If the same heading has
  phased rates or staggered applicability, merge the details into one measure and use conditions
  to preserve the timeline and eligibility rules.
- conditions must contain applicability rules such as country, origin-content rules,
  quota requirements, Column 1 rate thresholds, or date windows. Do not encode these
  conditions as HTS headings.
- When a measure applies to a U.S. note subdivision range such as 16(c)(i)-(iv),
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
- If an Annex or HTS table says "applicable subheading + X%", ad_valorem_rate should still be
  X.0 when X is the policy ad valorem increment or policy rate. Preserve the additive or cap
  formula in conditions.
- Do not mark ad_valorem_rate as unsupported merely because the Annex uses formula wording
  rather than a standalone percentage, as long as the numeric rate is correct and the legal
  formula is preserved in conditions.
- A Chapter 99 heading is supported as inserted if it is listed in the Annex, HTS amendment
  table, or inserted heading list, even if note subdivisions do not separately define every
  heading in prose.
- Do not reject inserted headings solely because the note text lacks a dedicated subdivision
  for that exact heading.
- For zero-rate or no-additional-duty headings, create or preserve measures when the heading
  represents a legally material tariff treatment, exemption, exclusion, or no-duty category.
- Annex IV zero-tariff / Proclamation 11012 surcharge-exclusion lists are review-supporting
  context. If those HTS codes are captured in a scope_set with source/label/conditions indicating
  Annex IV, section 232 zero tariff, or Proclamation 11012 exclusion, do not fail the audit solely
  because there is no separate measure for that Annex IV scope. Treat unclear Annex IV modeling
  as a warning for human review unless it contradicts a tariff rate, heading, or applicability rule.
- If a heading's duty treatment says "the duty provided in the applicable subheading",
  "no additional duty", "zero tariff", "shall be zero", or equivalent wording, then
  ad_valorem_rate = 0.0 is valid when the measure represents a legally material zero-rate
  or no-additional-duty category. Preserve the exact treatment in conditions. Do not require
  ad_valorem_rate = null solely because the legal text expresses the treatment as ordinary
  subheading duty rather than a standalone "0%" phrase.
- hts_modifications is heading-oriented. Note subdivision deletion, renumbering, redesignation,
  or conforming article-description changes are useful context, but missing those textual note
  mechanics is not an error by itself when the affected headings, rates, dates, and applicability
  conditions are captured. Treat this as a warning or clarifying recommendation unless the
  omission creates a factual contradiction or drops a legally material heading, rate, date, or
  applicability condition.
- If a heading is inserted or active for a period and later terminated or deleted on a future
  effective date, hts_modifications must not add a separate date-less deleted entry for that
  future deletion. Represent the legal effect on the corresponding measure: set
  effective_end_date to the last applicable calendar date and preserve the future
  termination/deletion wording in conditions. Do not add effective_at or any other schema field.
- If HTS heading text says "Except as provided for in heading(s) X, Y, Z" or equivalent wording,
  the referenced Chapter 99 headings are valid excluded_chapter99_headings for that measure.
  This captures a source-supported priority carveout where the current heading does not apply if
  one of those other Chapter 99 headings applies. Preserve those excluded_chapter99_headings and
  do not mark them as errors merely because the support appears in heading text rather than a
  separate note paragraph.
- If one heading has phased or future rate changes, one measure for that heading is valid. The
  ad_valorem_rate scalar should be the earliest/current directly applicable policy rate, while
  later rate phases, dates, and eligibility groups are preserved in conditions. Do not fail the
  audit merely because a later phase is in conditions instead of the scalar field.
- Use excluded_chapter99_headings only for Chapter 99 heading priority carveouts or explicit
  Chapter 99 exceptions. Use excluded_scope_refs for ordinary HTS product scope exclusions
  represented by scope_sets. Use conditions for text-only limitations that cannot be expressed
  as either structured field.
- Do not infer required note deletion, note renumbering, or conforming text changes beyond
  explicit source evidence. If the source support is ambiguous, report a warning instead of an
  error and keep the supported heading-level data.
- If evidence appears in different sections, evaluate the proclamation body, Annexes,
  attachment text, and HTS evidence together before marking data unsupported.

Audit requirements:
1. Every hts_modifications.inserted heading must be supported by source text or HTS evidence.
2. Every hts_modifications.deleted heading must be supported by source text or HTS evidence.
3. A heading must not appear in both deleted and inserted in the same modification unless the
   source explicitly shows a textual replacement for the same heading. If this is valid, explain
   why in issues as a warning; otherwise correct it.
4. Every inserted Chapter 99 heading that creates or changes a tariff, duty rate, exemption,
   condition, date window, exclusion, or applicability rule must have a corresponding measure.
5. Every measure_heading must be supported by source text or HTS evidence.
6. measure_heading_type must match the heading: Chapter 99 headings are chapter99; Chapter 1-98
   headings are ordinary_hts; note-level changes without a carrier heading are note_only.
7. Every measure's rate, country scope, effective date, excluded heading, superseded heading,
   affected_scope_refs, excluded_scope_refs, and condition must be supported by evidence.
8. scope_sets must contain only headings/ranges supported by source text or HTS evidence.
9. Report unsupported data as issues. Do not invent headings, rates, dates, countries, or
   conditions.
10. If exact data cannot be verified, report a recommended_fix that keeps only what is supported
    and uses null/unknown/note_only according to the schema.
11. Do not block the audit merely because a heading-level hts_modifications record omits note
    subdivision renumbering, redesignation, or conforming prose changes. Flag those as warnings
    unless the omission makes a heading, rate, date, condition, exclusion, or supersession wrong.
12. Do not block the audit merely because Annex IV zero-tariff / Proclamation 11012 exclusion
    HTS codes are represented as a scope_set rather than as a separate measure. If the Annex IV
    list is present and source-supported, and no conflicting nonzero rate is assigned to those
    headings, pass this aspect or report only a warning for human review.
13. Every non-null measure_heading must appear at most once in measures. If the same heading is
    duplicated because of Annex III / non-Annex III effective dates, phased rate changes, country
    conditions, or eligibility groups, merge those records into one measure. Use the earliest
    applicable effective_start_date for that heading and preserve staggered dates, future rate
    changes, and group-specific applicability in conditions. Report duplicate records for the
    same measure_heading as errors.
14. Issues are findings for the repair step. When you identify an unsupported or contradictory
    value, report a specific json_path, source evidence, and recommended_fix. Do not output a
    corrected impact JSON and do not claim that you already fixed the JSON.
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
    effective_end_date and conditions, report any separate hts_modifications.deleted record that
    would make the same heading look both inserted/active and deleted without date context.
18. When a measure's heading text contains an explicit "Except as provided for in headings ..."
    carveout, the referenced Chapter 99 headings should remain in excluded_chapter99_headings.
    You may add the wording to conditions for clarity, but do not remove source-supported
    excluded_chapter99_headings or report them as errors.
19. Do not output patch instructions, repair operations, repairs arrays, or a corrected impact
    JSON. The repair step is responsible for producing the complete corrected JSON.

Impact JSON schema:
- Top-level fields: source, hts_modifications, scope_sets, measures.
- source: an object. Keep only source metadata from extraction; do not add audit metadata.
- hts_modifications[] fields: action, note, deleted, inserted.
  - action: string such as "insert", "delete", "modify", or "replace".
  - note: integer or null.
  - deleted: array of concrete HTS headings/ranges.
  - inserted: array of concrete HTS headings/ranges.
- scope_sets[] fields: id, source, note, subdivision, label, headings.
  - id: non-empty string.
  - source: string or null.
  - note: integer or null.
  - subdivision: string or null.
  - label: string or null.
  - headings: array of concrete HTS headings/ranges.
- measures[] fields: measure_heading, measure_heading_type, note, description,
  ad_valorem_rate, value_basis, country_iso2, is_potential, effective_start_date,
  effective_end_date, affected_scope_refs, excluded_scope_refs, conditions,
  excluded_chapter99_headings, superseded_chapter99_headings.
  - measure_heading: concrete HTS heading or null.
  - measure_heading_type: "chapter99", "ordinary_hts", "note_only", or "unknown".
  - note: integer or null.
  - description: string or null.
  - ad_valorem_rate: number or null. Do not use strings such as "10%".
  - value_basis: string or null.
  - country_iso2: two-letter ISO country code string or null.
  - is_potential: boolean.
  - effective_start_date: YYYY-MM-DD string or null.
  - effective_end_date: YYYY-MM-DD string or null.
  - affected_scope_refs and excluded_scope_refs: arrays of scope_sets[].id strings.
  - conditions: array of strings.
  - excluded_chapter99_headings and superseded_chapter99_headings: arrays of concrete HTS
    headings/ranges.

Severity:
- Use severity="error" only for factual contradictions, unsupported headings/rates/dates/
  conditions, missing legally material measures, invalid schema semantics, or corrections that
  are required before the JSON can be trusted.
- Use severity="warning" for representational ambiguity where the data is source-supported but
  could be clearer.
- Use severity="warning", not severity="error", when Annex IV zero-tariff / Proclamation 11012
  surcharge-exclusion HTS codes are source-supported but modeled only as a scope_set. Human
  review will decide whether a separate zero-rate measure should be added.
- If the JSON captures the correct numeric rate and preserves the legal formula in conditions,
  do not mark it as an error.
- If a zero-rate/no-additional-duty heading is modeled with ad_valorem_rate = 0.0 and the
  conditions preserve the "duty provided in the applicable subheading" or no-additional-duty
  wording, do not mark it as an error.
- If excluded_chapter99_headings comes from explicit heading-text carveout wording such as
  "Except as provided for in headings 9903.82.14, 9903.85.67 and 9903.85.68", it is
  source-supported and structurally valid. Do not mark this as an error.
- If a later phased rate is preserved in conditions for the same measure_heading, do not mark it
  as an error merely because ad_valorem_rate contains the earliest/current directly applicable
  rate.
- Prefer adding clarifying conditions over deleting measures when the heading and rate are
  supported.
- Missing note subdivision renumbering, redesignation, or conforming article-description prose is
  normally a warning. Escalate it to an error only when it changes the extracted legal effect.
- Use severity="error" only for remaining problems in the current impact JSON.
- If you can identify the exact corrected scalar value for a field such as effective_end_date,
  put that exact value in recommended_fix so the repair step can apply it.
- If a heading's same duty treatment continues after a note renumbering or conforming description
  change, effective_end_date must be null. Do not infer termination from renumbering alone.
- Duplicate non-null measure_heading values are invalid. Merge duplicates into one measure and
  preserve staggered effective dates, future rate changes, country conditions, and eligibility
  groups in conditions.

Examples:
- If the source says "The tariff rate for products of the United Kingdom shall be 10 percent"
  and an Annex says "applicable subheading + 10%", this supports ad_valorem_rate = 10.0 when
  conditions preserve the UK treatment formula. This is not an error.
- If the source says the ad valorem duty rate shall be 20 percent for products of companies
  with approved onshoring plans, this supports ad_valorem_rate = 20.0 even if the Annex table
  phrases the HTS treatment as "applicable subheading + 20%"; preserve that formula in
  conditions.
- If an Annex or inserted heading list includes headings 9903.04.60 through 9903.04.69, then
  headings 9903.04.67 and 9903.04.68 are source-supported as inserted headings even if the
  surrounding note subdivisions are sparse, provided their tariff treatment is supported by the
  proclamation body, Annex, or HTS evidence.
- If heading 9903.04.61 says "The duty provided in the applicable subheading" or equivalent
  no-additional-duty wording, ad_valorem_rate = 0.0 is acceptable when conditions preserve that
  wording. Do not require null for this legally material zero-rate treatment.
- If a source deletes heading 9903.04.65 and also includes related note 40 subdivision
  renumbering or conforming text for 9903.04.66, the heading deletion and the surviving
  9903.04.66 measure can still be valid even if hts_modifications does not separately encode the
  note-text mechanics. Recommend a clarifying warning or condition if useful; do not fail the
  audit unless a supported heading/rate/date/applicability condition is actually wrong or missing.
- If Annex I(B) terminates heading 9903.04.65 effective 2029-01-20 but only renumbers the note
  reference for 9903.04.66, the 9903.04.65 measure ends on 2029-01-19 and the 9903.04.66 measure
  continues with effective_end_date = null.
- If a proclamation gives one effective_start_date for Annex III companies and a later
  effective_start_date for other companies, keep one measure for each affected heading. Use the
  earliest applicable effective_start_date and state the later group-specific date in conditions.

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
      "recommended_fix": "Specific correction, or null if no correction is needed."
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

Your task is not to audit or explain. Your task is to directly fix the JSON using the provided
policy document, attachments, HTS evidence, current impact JSON, and audit error issues.

Rules:
- Output only the repaired complete impact JSON wrapped in <json></json>.
- Do not output verdict, issues, repairs, comments, markdown, or explanatory text.
- Directly fix every audit error that can be fixed from the provided evidence.
- If an audit error says an hts_modifications entry is unsupported or misleading, remove or
  rewrite that entry in the repaired JSON.
- If an audit error says a legally material measure is missing, add the supported measure.
- If an audit error says a value is unsupported, replace it with the supported value or null.
- If an issue is ambiguous but not a factual contradiction, preserve source-supported data and
  put the uncertainty in conditions for human review.
- Keep exactly the impact JSON schema: source, hts_modifications, scope_sets, measures.
- Do not create duplicate non-null measure_heading values. Merge duplicate headings into one
  measure and preserve date/rate/eligibility nuances in conditions.
- If one heading has phased or future rate changes, keep one measure for the heading. Set
  ad_valorem_rate to the earliest/current directly applicable policy rate for that heading and
  preserve later rate phases, dates, and eligibility groups in conditions.
- Use excluded_chapter99_headings only for Chapter 99 heading priority carveouts or explicit
  Chapter 99 exceptions. Use excluded_scope_refs for ordinary HTS product scope exclusions
  represented by scope_sets. Use conditions for text-only limitations that cannot be expressed
  as either structured field.
- Keep hts_modifications heading-oriented. Do not include note-only changes with empty
  deleted/inserted arrays as heading modifications.
- For Annex IV zero-tariff / Proclamation 11012 surcharge-exclusion lists, a source-supported
  scope_set is sufficient; a separate measure is optional unless needed to avoid contradiction.
- For zero-rate or no-additional-duty Chapter 99 headings that define an exclusion or treatment,
  preserve a measure with ad_valorem_rate = 0.0 when the heading is source-supported.
- For future-dated heading terminations/deletions, repair the affected measure's
  effective_end_date and conditions. Do not add or keep a separate hts_modifications.deleted
  entry that lacks date context for the future deletion, and do not add effective_at or any
  other field outside the schema.
- Do not remove source-supported excluded_chapter99_headings that come from explicit heading-text
  exceptions such as "Except as provided for in headings X, Y, Z". Preserve those Chapter 99
  headings in excluded_chapter99_headings and keep the prose in conditions if useful.
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
        f"Source URL: {inp.source_url}",
        "",
        "## Policy Document Content",
        inp.source_content,
    ]
    if inp.briefing:
        parts += ["", "## Briefing", inp.briefing]
    if inp.attachment_text:
        parts += ["", "## Attachment Content", inp.attachment_text]
    parts += [
        "",
        "## Audit Errors To Fix",
        "Fix these errors directly in the repaired JSON. Do not restate them as issues.",
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
            "policy_impact_audit: policy_update_id=%s round=%s verdict=%s issues=%s errors=%s",
            inp.policy_update_id,
            audit_round,
            audit["verdict"],
            len(audit["issues"]),
            len(error_issues),
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
    response_text = llm.complete_with_tools(
        _REPAIR_SYSTEM_PROMPT,
        _build_repair_user_message(
            inp,
            impact_json,
            error_issues,
            audit_round=audit_round,
        ),
        _TOOLS,
        _dispatch_tool,
        max_tokens=16384,
        max_iterations=20,
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
