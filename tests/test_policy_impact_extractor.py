from __future__ import annotations

from typing import Any

import pytest

from radar_backend.llm.policy_impact_extractor import (
    PolicyImpactInput,
    _AUDIT_SYSTEM_PROMPT,
    _REPAIR_SYSTEM_PROMPT,
    _SYSTEM_PROMPT,
    _parse_audit_output,
    _normalize_pdf_url,
    _parse_json_output,
    audit_policy_impact,
    extract_policy_impact,
)


class FakeProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        raise AssertionError("complete should not be called")

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "Policy Update ID: 123" in user
        assert {tool["name"] for tool in tools} == {
            "http_get",
            "read_pdf_pages",
            "search_csv_rows",
        }
        if self.call_count == 1:
            assert "trade compliance analyst" in system
            return '<json>{"source": {}, "hts_modifications": [], "scope_sets": [], "measures": []}</json>'
        assert "senior trade compliance auditor" in system
        return """
        <json>{
            "verdict": "pass",
            "issues": []
        }</json>
        """


def test_extract_policy_impact_uses_llm_provider_tool_loop() -> None:
    provider = FakeProvider()
    result = extract_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Content",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
    )

    assert result == {"source": {}, "hts_modifications": [], "scope_sets": [], "measures": []}
    assert provider.call_count == 2


class FakeAuditCorrectedJsonProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        self.call_count += 1
        assert "9903.82.03" in user
        assert "senior trade compliance editor" in system
        assert "Audit Errors To Fix" in user
        assert "Policy Document Content" not in user
        return """
        <json>{
            "source": {},
            "hts_modifications": [{
                "action": "insert",
                "note": 16,
                "deleted": [],
                "inserted": ["9903.82.03"]
            }],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9903.82.03",
                "measure_heading_type": "chapter99",
                "note": 16,
                "description": "Supported inserted heading",
                "ad_valorem_rate": null,
                "value_basis": null,
                "country_iso2": null,
                "is_potential": false,
                "effective_start_date": null,
                "effective_end_date": null,
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "9903.82.03" in user
        assert "senior trade compliance auditor" in system
        if self.call_count == 1:
            return """
            <json>{
                "verdict": "fail",
                "issues": [{
                    "severity": "error",
                    "json_path": "measures",
                    "problem": "Inserted heading 9903.82.03 has no corresponding measure.",
                    "source_evidence": "The source lists 9903.82.03 as inserted.",
                    "recommended_fix": "Add a supported measure for 9903.82.03."
                }]
            }</json>
            """
        return """
        <json>{
            "verdict": "pass",
            "issues": []
        }</json>
        """


def test_audit_policy_impact_uses_repair_after_failed_audit() -> None:
    provider = FakeAuditCorrectedJsonProvider()
    result = audit_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Source inserts 9903.82.03.",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
        {
            "source": {},
            "hts_modifications": [{
                "action": "insert",
                "note": 16,
                "deleted": [],
                "inserted": ["9903.82.03"],
            }],
            "scope_sets": [],
            "measures": [],
        },
    )

    assert result["measures"][0]["measure_heading"] == "9903.82.03"
    assert provider.call_count == 3


def test_parse_json_output_accepts_dotted_ten_digit_hts_headings() -> None:
    result = _parse_json_output(
        """
        <json>{
            "source": {},
            "hts_modifications": [{
                "action": "replace",
                "note": null,
                "deleted": ["2106.90.99.98"],
                "inserted": ["2106.90.9998"]
            }],
            "scope_sets": [{
                "id": "scope_2106",
                "source": "direct_hts_modification",
                "note": null,
                "subdivision": null,
                "label": "Ten-digit HTS scope",
                "headings": ["2106.90.99.98", "2106.90.9998", "2106.90.99.98-2106.90.99.99"]
            }],
            "measures": [{
                "measure_heading": "2106.90.99.98",
                "measure_heading_type": "ordinary_hts",
                "note": null,
                "description": "Supported ten-digit HTS heading",
                "ad_valorem_rate": null,
                "value_basis": null,
                "country_iso2": null,
                "is_potential": false,
                "effective_start_date": null,
                "effective_end_date": null,
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """
    )

    assert result["scope_sets"][0]["headings"][0] == "2106.90.99.98"
    assert result["measures"][0]["measure_heading"] == "2106.90.99.98"


def test_parse_json_output_accepts_chapter98_measure_type() -> None:
    result = _parse_json_output(
        """
        <json>{
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9819.11.09",
                "measure_heading_type": "chapter98",
                "note": 2,
                "description": "Chapter 98 provision modified by governing note text.",
                "ad_valorem_rate": null,
                "value_basis": null,
                "country_iso2": null,
                "is_potential": false,
                "effective_start_date": "2002-10-01",
                "effective_end_date": null,
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """
    )

    assert result["measures"][0]["measure_heading_type"] == "chapter98"
    assert result["measures"][0]["effective_start_date"] == "2002-10-01"
    assert result["measures"][0]["effective_end_date"] is None


def test_parse_json_output_requires_heading_for_chapter98_measure() -> None:
    with pytest.raises(ValueError, match="measure_heading is required for chapter98"):
        _parse_json_output(
            """
            <json>{
                "source": {},
                "hts_modifications": [],
                "scope_sets": [],
                "measures": [{
                    "measure_heading": null,
                    "measure_heading_type": "chapter98",
                    "note": 2,
                    "description": "Invalid Chapter 98 measure without carrier heading.",
                    "ad_valorem_rate": null,
                    "value_basis": null,
                    "country_iso2": null,
                    "is_potential": false,
                    "effective_start_date": null,
                    "effective_end_date": null,
                    "affected_scope_refs": [],
                    "excluded_scope_refs": [],
                    "excluded_chapter99_headings": [],
                    "superseded_chapter99_headings": []
                }]
            }</json>
            """
        )


def test_parse_json_output_rejects_scope_refs_for_chapter98_measure() -> None:
    with pytest.raises(ValueError, match="affected_scope_refs must be empty for chapter98"):
        _parse_json_output(
            """
            <json>{
                "source": {},
                "hts_modifications": [],
                "scope_sets": [{
                    "id": "scope_9819",
                    "source": "direct_hts_modification",
                    "note": null,
                    "subdivision": null,
                    "label": "Invalid repeated Chapter 98 scope",
                    "headings": ["9819.11.09"]
                }],
                "measures": [{
                    "measure_heading": "9819.11.09",
                    "measure_heading_type": "chapter98",
                    "note": 2,
                    "description": "Invalid Chapter 98 measure using scope refs.",
                    "ad_valorem_rate": null,
                    "value_basis": null,
                    "country_iso2": null,
                    "is_potential": false,
                    "effective_start_date": "2002-10-01",
                    "effective_end_date": null,
                    "affected_scope_refs": ["scope_9819"],
                    "excluded_scope_refs": [],
                    "excluded_chapter99_headings": [],
                    "superseded_chapter99_headings": []
                }]
            }</json>
            """
        )


class FakeAuditAlwaysFailProvider:
    def __init__(self) -> None:
        self.call_count = 0
        self.audit_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        self.call_count += 1
        assert "senior trade compliance editor" in system
        assert "Audit Errors To Fix" in user
        return """
        <json>{
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9903.82.03",
                "measure_heading_type": "chapter99",
                "note": 16,
                "description": "Repair attempted",
                "ad_valorem_rate": null,
                "value_basis": null,
                "country_iso2": null,
                "is_potential": false,
                "effective_start_date": null,
                "effective_end_date": null,
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "senior trade compliance auditor" in system
        self.audit_count += 1
        if self.audit_count > 1:
            assert "Previous Audit Errors" in user
        return f"""
        <json>{{
            "verdict": "fail",
            "issues": [{{
                "severity": "error",
                "json_path": "measures[0].description",
                "problem": "Description needs source-supported clarification.",
                "source_evidence": "The source supports the corrected description.",
                "recommended_fix": "Use the corrected description."
            }}]
        }}</json>
        """


def test_audit_policy_impact_rejects_json_when_verdict_stays_fail() -> None:
    provider = FakeAuditAlwaysFailProvider()
    with pytest.raises(ValueError, match="impact audit failed"):
        audit_policy_impact(
            provider,
            PolicyImpactInput(
                policy_update_id=123,
                source_key="test",
                source_title="Title",
                source_content="Source supports a correction.",
                briefing="",
                attachment_text="",
                source_url="https://example.test/policy",
            ),
            {
                "source": {},
                "hts_modifications": [],
                "scope_sets": [],
                "measures": [],
            },
        )

    assert provider.audit_count == 3
    assert provider.call_count == 5


class FakeAuditDateCorrectedJsonProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        self.call_count += 1
        assert "senior trade compliance editor" in system
        assert "Audit Errors To Fix" in user
        return """
        <json>{
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9903.04.61",
                "measure_heading_type": "chapter99",
                "note": 40,
                "description": "Temporary treatment",
                "ad_valorem_rate": 0.0,
                "value_basis": null,
                "country_iso2": null,
                "is_potential": false,
                "effective_start_date": "2026-07-31",
                "effective_end_date": "2026-09-28",
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "senior trade compliance auditor" in system
        if self.call_count == 1:
            return """
            <json>{
                "verdict": "fail",
                "issues": [{
                    "severity": "error",
                    "json_path": "measures[0].effective_end_date",
                    "problem": "effective_end_date is modeled as 2026-09-29, but the legal cutoff is before 12:01 a.m. eastern time on September 29, 2026. Thus the last covered moment is 2026-09-28 23:59:59 ET (date-only: 2026-09-28).",
                    "source_evidence": "Covered only before 12:01 a.m. eastern time on September 29, 2026.",
                    "recommended_fix": "Set effective_end_date to 2026-09-28."
                }]
            }</json>
            """

        assert '"effective_end_date": "2026-09-28"' in user
        return """
        <json>{
            "verdict": "pass",
            "issues": []
        }</json>
        """


def test_audit_policy_impact_uses_repaired_date_before_next_round() -> None:
    provider = FakeAuditDateCorrectedJsonProvider()
    result = audit_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Source has a before 12:01 a.m. cutoff.",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
        {
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [],
        },
    )

    assert result["measures"][0]["effective_end_date"] == "2026-09-28"
    assert provider.call_count == 3


class FakeAuditNullEndDateCorrectedJsonProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        self.call_count += 1
        assert "senior trade compliance editor" in system
        assert "Audit Errors To Fix" in user
        return """
        <json>{
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9903.04.66",
                "measure_heading_type": "chapter99",
                "note": 40,
                "description": "Continuing treatment",
                "ad_valorem_rate": 0.0,
                "value_basis": null,
                "country_iso2": null,
                "is_potential": false,
                "effective_start_date": "2026-07-31",
                "effective_end_date": null,
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "senior trade compliance auditor" in system
        if self.call_count == 1:
            return """
            <json>{
                "verdict": "fail",
                "issues": [{
                    "severity": "error",
                    "json_path": "measures[0].effective_end_date",
                    "problem": "Measure 9903.04.66 is incorrectly ended on 2029-01-19; the heading continues after note renumbering.",
                    "source_evidence": "Annex renumbers the note reference and does not terminate 9903.04.66.",
                    "recommended_fix": "Set effective_end_date to null."
                }]
            }</json>
            """

        assert '"effective_end_date": null' in user
        return """
        <json>{
            "verdict": "pass",
            "issues": []
        }</json>
        """


def test_audit_policy_impact_uses_repaired_null_end_date_before_next_round() -> None:
    provider = FakeAuditNullEndDateCorrectedJsonProvider()
    result = audit_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Source has a note renumbering but no heading termination.",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
        {
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [],
        },
    )

    assert result["measures"][0]["effective_end_date"] is None
    assert provider.call_count == 3


class FakeAuditPassProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        raise AssertionError("complete should not be called")

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "senior trade compliance auditor" in system
        return """
        <json>{
            "verdict": "pass",
            "issues": []
        }</json>
        """


class FakeAuditWarningOnlyFailProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        raise AssertionError("complete should not be called")

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "senior trade compliance auditor" in system
        return """
        <json>{
            "verdict": "fail",
            "issues": [{
                "severity": "warning",
                "json_path": "measures[0].description",
                "problem": "Human review may want clearer wording.",
                "source_evidence": "The source-supported data is otherwise valid.",
                "recommended_fix": null
            }]
        }</json>
        """


def test_audit_policy_impact_returns_current_json_when_audit_passes() -> None:
    provider = FakeAuditPassProvider()
    result = audit_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Source has a cutoff.",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
        {
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9903.04.61",
                "measure_heading_type": "chapter99",
                "note": 40,
                "description": "Temporary treatment",
                "ad_valorem_rate": 0.0,
                "value_basis": None,
                "country_iso2": None,
                "is_potential": False,
                "effective_start_date": "2026-07-31",
                "effective_end_date": "2026-09-28",
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": [],
            }],
        },
    )

    assert result["measures"][0]["effective_end_date"] == "2026-09-28"
    assert provider.call_count == 1


def test_audit_policy_impact_returns_current_json_when_no_error_issues() -> None:
    provider = FakeAuditWarningOnlyFailProvider()
    impact_json = {"source": {}, "hts_modifications": [], "scope_sets": [], "measures": []}
    result = audit_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Source has a warning-only ambiguity.",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
        impact_json,
    )

    assert result is impact_json
    assert provider.call_count == 1


class FakeAuditGenericCorrectedJsonProvider:
    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, system: str, user: str, max_tokens: int = 2048) -> str:
        self.call_count += 1
        assert "senior trade compliance editor" in system
        assert "Audit Errors To Fix" in user
        return """
        <json>{
            "source": {},
            "hts_modifications": [{
                "action": "insert",
                "note": 40,
                "deleted": [],
                "inserted": ["9903.04.63"]
            }],
            "scope_sets": [],
            "measures": [{
                "measure_heading": "9903.04.63",
                "measure_heading_type": "chapter99",
                "note": 40,
                "description": "UK treatment",
                "ad_valorem_rate": 10.0,
                "value_basis": null,
                "country_iso2": "GB",
                "is_potential": false,
                "effective_start_date": null,
                "effective_end_date": null,
                "affected_scope_refs": [],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": [],
                "superseded_chapter99_headings": []
            }]
        }</json>
        """

    def complete_with_tools(
        self,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        dispatch_tool,
        max_tokens: int = 8192,
        max_iterations: int = 20,
    ) -> str:
        self.call_count += 1
        assert "senior trade compliance auditor" in system
        if self.call_count == 1:
            return """
            <json>{
                "verdict": "fail",
                "issues": [{
                    "severity": "error",
                    "json_path": "measures[0]",
                    "problem": "The measure rate, country code, and extra modification need repair.",
                    "source_evidence": "The source supports a 10 percent rate and one modification.",
                    "recommended_fix": "Set the supported rate and country code; delete the extra modification."
                }]
            }</json>
            """

        assert '"ad_valorem_rate": 10.0' in user
        assert '"country_iso2": "GB"' in user
        assert '"modify"' not in user
        return """
        <json>{
            "verdict": "pass",
            "issues": []
        }</json>
        """


def test_audit_policy_impact_uses_repaired_generic_json_before_next_round() -> None:
    provider = FakeAuditGenericCorrectedJsonProvider()
    result = audit_policy_impact(
        provider,
        PolicyImpactInput(
            policy_update_id=123,
            source_key="test",
            source_title="Title",
            source_content="Source supports generic repairs.",
            briefing="",
            attachment_text="",
            source_url="https://example.test/policy",
        ),
        {
            "source": {},
            "hts_modifications": [],
            "scope_sets": [],
            "measures": [],
        },
    )

    assert len(result["hts_modifications"]) == 1
    assert result["measures"][0]["ad_valorem_rate"] == 10.0
    assert result["measures"][0]["country_iso2"] == "GB"
    assert provider.call_count == 3


def test_normalize_hts_chapter_99_download_url() -> None:
    assert _normalize_pdf_url(
        "https://hts.usitc.gov/download/Chapter_99_2026HTSRev7.pdf"
    ) == "https://hts.usitc.gov/reststop/file?release=currentRelease&filename=Chapter%2099"


def test_prompts_model_future_heading_deletions_as_measure_end_dates() -> None:
    for prompt in (_SYSTEM_PROMPT, _AUDIT_SYSTEM_PROMPT):
        assert "future" in prompt
        assert "effective_end_date" in prompt
        assert "hts_modifications.deleted" in prompt
        assert "date context" in prompt or "no date field" in prompt
        assert "effective_at" in prompt


def test_prompts_allow_heading_text_carveouts_as_excluded_chapter99_headings() -> None:
    for prompt in (_SYSTEM_PROMPT, _AUDIT_SYSTEM_PROMPT):
        assert "Except as provided for in heading" in prompt
        assert "excluded_chapter99_headings" in prompt
        assert "carveout" in prompt or "exceptions" in prompt


def test_parse_json_output_accepts_scope_sets_and_scope_refs() -> None:
    result = _parse_json_output(
        """
        <json>{
            "source": {},
            "hts_modifications": [{
                "action": "replace",
                "note": 16,
                "deleted": ["9903.78.01"],
                "inserted": ["9903.82.04"]
            }],
            "scope_sets": [{
                "id": "note16_c_i",
                "source": "us_note_subdivision",
                "note": 16,
                "subdivision": "16(c)(i)",
                "label": "Aluminum articles",
                "headings": [
                    "7601",
                    "7604.10.10",
                    "7616.99.5160",
                    "7210.61.00-7210.70.60"
                ]
            }],
            "measures": [{
                "measure_heading": "9903.82.04",
                "measure_heading_type": "chapter99",
                "note": 16,
                "description": "UK aluminum articles",
                "ad_valorem_rate": 25.0,
                "value_basis": "customs_value",
                "country_iso2": "GB",
                "is_potential": false,
                "effective_start_date": "2026-04-06",
                "effective_end_date": null,
                "affected_scope_refs": ["note16_c_i"],
                "excluded_scope_refs": [],
                "excluded_chapter99_headings": ["9903.82.17"],
                "superseded_chapter99_headings": ["9903.78.01"]
            }]
        }</json>
        """
    )

    assert result["scope_sets"][0]["headings"] == [
        "7601",
        "7604.10.10",
        "7616.99.5160",
        "7210.61.00-7210.70.60",
    ]
    assert result["measures"][0]["affected_scope_refs"] == ["note16_c_i"]


def test_parse_json_output_rejects_legacy_includes_headings() -> None:
    with pytest.raises(ValueError, match="must use scope refs"):
        _parse_json_output(
            """
            <json>{
                "source": {},
                "hts_modifications": [],
                "scope_sets": [],
                "measures": [{
                    "measure_heading": "9903.82.04",
                    "measure_heading_type": "chapter99",
                    "includes_headings": ["7601"]
                }]
            }</json>
            """
        )


def test_parse_json_output_rejects_unknown_scope_ref() -> None:
    with pytest.raises(ValueError, match="unknown scope ref"):
        _parse_json_output(
            """
            <json>{
                "source": {},
                "hts_modifications": [],
                "scope_sets": [],
                "measures": [{
                    "measure_heading": "9903.82.04",
                    "measure_heading_type": "chapter99",
                    "affected_scope_refs": ["missing_scope"]
                }]
            }</json>
            """
        )


def test_parse_json_output_rejects_duplicate_measure_heading() -> None:
    with pytest.raises(ValueError, match="measure_heading is duplicated"):
        _parse_json_output(
            """
            <json>{
                "source": {},
                "hts_modifications": [],
                "scope_sets": [],
                "measures": [{
                    "measure_heading": "9903.04.60",
                    "measure_heading_type": "chapter99",
                    "note": 40,
                    "description": "First record",
                    "ad_valorem_rate": 100.0,
                    "value_basis": "unknown",
                    "country_iso2": null,
                    "is_potential": false,
                    "effective_start_date": "2026-07-31",
                    "effective_end_date": null,
                    "affected_scope_refs": [],
                    "excluded_scope_refs": [],
                    "excluded_chapter99_headings": [],
                    "superseded_chapter99_headings": []
                }, {
                    "measure_heading": "9903.04.60",
                    "measure_heading_type": "chapter99",
                    "note": 40,
                    "description": "Duplicate record",
                    "ad_valorem_rate": 100.0,
                    "value_basis": "unknown",
                    "country_iso2": null,
                    "is_potential": false,
                    "effective_start_date": "2026-09-29",
                    "effective_end_date": null,
                    "affected_scope_refs": [],
                    "excluded_scope_refs": [],
                    "excluded_chapter99_headings": [],
                    "superseded_chapter99_headings": []
                }]
            }</json>
            """
        )


def test_parse_json_output_rejects_unknown_impact_fields() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        _parse_json_output(
            """
            <json>{
                "source": {},
                "hts_modifications": [],
                "scope_sets": [],
                "measures": [{
                    "measure_heading": "9903.04.60",
                    "measure_heading_type": "chapter99",
                    "conditions": []
                }]
            }</json>
            """
        )


def test_parse_audit_output_rejects_corrected_json() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        _parse_audit_output(
            """
            <json>{
                "verdict": "pass",
                "issues": [],
                "corrected_json": {
                    "source": {},
                    "hts_modifications": [],
                    "scope_sets": [],
                    "measures": []
                }
            }</json>
            """
        )


def test_parse_audit_output_rejects_separate_errors_field() -> None:
    with pytest.raises(ValueError, match="unknown field"):
        _parse_audit_output(
            """
            <json>{
                "verdict": "fail",
                "issues": [{
                    "severity": "error",
                    "json_path": "measures[0].effective_end_date",
                    "problem": "Wrong end date.",
                    "source_evidence": "Source supports the corrected end date.",
                    "recommended_fix": {"effective_end_date": "2027-12-31"}
                }],
                "errors": [{
                    "severity": "error",
                    "json_path": "measures[0].effective_end_date",
                    "problem": "Duplicated error.",
                    "source_evidence": "Same evidence.",
                    "recommended_fix": {"effective_end_date": "2027-12-31"}
                }]
            }</json>
            """
        )
