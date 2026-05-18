from __future__ import annotations

import json
import re
from dataclasses import dataclass

from radar_backend.llm.provider import LLMProvider

_SYSTEM_PROMPT = """\
You are a trade compliance analyst. Determine whether the following government document should be ingested into the "recent policy updates" feed.

## Ingest if ANY of the following apply
These must be POLICY CHANGES that alter the rules or rates applicable to importers — not routine administrative determinations:
- Establishes, adjusts, suspends, or revokes import tariff rates applicable to HTS codes (e.g. new Section 301/232 tariffs, tariff exemptions, quota-based rate changes)
- Changes HTS code classification rules or schedules
- Modifies de minimis thresholds or scope
- Creates or modifies import quotas, tariff-rate quotas (TRQs), licenses, or compliance requirements
- Establishes new procedures for importers to obtain tariff exemptions, exclusions, or adjustments
- Amends, extends, or terminates existing trade remedy measures (safeguards, Section 201/301/232) at the policy level

## Discard if ANY of the following apply
- Antidumping (AD) or countervailing duty (CVD) administrative review results — these determine duty rates for specific foreign producers for a past review period; they do not change the tariff structure or HTS applicability
- Purely ceremonial proclamations (holidays, commemorations, honorary designations)
- Executive orders unrelated to goods imports (immigration, defense, personnel, domestic policy)
- Procedural notices unrelated to trade compliance (internal reorganizations, appointments)
- Scope rulings, circumvention findings, or other case-specific AD/CVD determinations that apply only to named companies or products already under an existing order
- HTS change record entries where the Source field is "PP xxxxx", "Executive Order", or "Notice" (already covered by other data sources)

## reference_number format by source
- Federal Register Notice: Federal Register citation, e.g. "91 FR 27248"
- Presidential Proclamation: e.g. "Proclamation 11027"
- Executive Order: e.g. "Executive Order 14384"
- HTS Archive: archive ID, e.g. "2026HTSBasic"
- If not determinable: null

## Output format
Output ONLY the following JSON wrapped in <json></json> tags, with no other content:

<json>
{
  "should_ingest": true,
  "discard_reason": null,
  "headline": "One-sentence title",
  "summary": "2-4 sentence summary of the policy and its scope",
  "briefing": "## Background\\n...\\n## Key Provisions\\n...\\n## Impact on Importers\\n...",
  "effective_date": "2025-02-04"
}
</json>

Rules:
- When should_ingest=true, headline, summary, and briefing must be non-empty
- When should_ingest=false, discard_reason must be non-empty; headline/summary/briefing may be empty strings
- effective_date format: YYYY-MM-DD; use null if not determinable
"""

_JSON_RE = re.compile(r"<json>\s*(.*?)\s*</json>", re.DOTALL)


@dataclass(frozen=True)
class PolicyFilterInput:
    source_key: str
    source_label: str
    source_title: str
    source_content: str
    attachment_text: str  # empty string when no PDFs
    reference_number: str | None = None  # pre-known citation; LLM should use as-is


@dataclass(frozen=True)
class PolicyUpdateDraft:
    should_ingest: bool
    discard_reason: str | None
    reference_number: str | None
    headline: str
    summary: str
    briefing: str
    effective_date: str | None


def filter_and_generate(
    llm: LLMProvider,
    input: PolicyFilterInput,
) -> PolicyUpdateDraft:
    """Call the LLM to decide whether to ingest and generate a briefing.

    Raises ``ValueError`` on invalid/unparseable output.
    The caller is responsible for treating this as a failed attempt.
    """
    user = _build_user_message(input)
    raw = llm.complete(_SYSTEM_PROMPT, user, max_tokens=2048)
    return _parse_response(raw)


def _build_user_message(input: PolicyFilterInput) -> str:
    parts = [
        f"Source: {input.source_label} ({input.source_key})",
        f"Title: {input.source_title}",
    ]
    if input.reference_number:
        parts.append(f"Reference Number: {input.reference_number} (use this exact value)")
    parts += [
        "",
        "## Document Content",
        input.source_content,
    ]
    if input.attachment_text:
        parts += ["", "## Attachment Content", input.attachment_text]
    return "\n".join(parts)


def _parse_response(raw: str) -> PolicyUpdateDraft:
    match = _JSON_RE.search(raw)
    if not match:
        raise ValueError(f"no <json> block found in LLM response: {raw[:200]!r}")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in LLM response: {exc}") from exc

    should_ingest = bool(data.get("should_ingest", False))

    if should_ingest:
        for field in ("headline", "summary", "briefing"):
            if not data.get(field, "").strip():
                raise ValueError(
                    f"should_ingest=true but {field!r} is empty"
                )
    else:
        if not data.get("discard_reason", "").strip():
            raise ValueError("should_ingest=false but discard_reason is empty")

    return PolicyUpdateDraft(
        should_ingest=should_ingest,
        discard_reason=data.get("discard_reason") or None,
        reference_number=data.get("reference_number") or None,
        headline=data.get("headline") or "",
        summary=data.get("summary") or "",
        briefing=data.get("briefing") or "",
        effective_date=data.get("effective_date") or None,
    )
