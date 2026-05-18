from __future__ import annotations

from typing import Any

import pytest

from radar_backend.llm.policy_impact_extractor import (
    PolicyImpactInput,
    _normalize_pdf_url,
    _parse_json_output,
    extract_policy_impact,
)


class FakeProvider:
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
        assert "trade compliance analyst" in system
        assert "Policy Update ID: 123" in user
        assert {tool["name"] for tool in tools} == {
            "http_get",
            "read_pdf_pages",
            "search_csv_rows",
        }
        return '<json>{"source": {}, "hts_modifications": [], "scope_sets": [], "measures": []}</json>'


def test_extract_policy_impact_uses_llm_provider_tool_loop() -> None:
    result = extract_policy_impact(
        FakeProvider(),
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


def test_normalize_hts_chapter_99_download_url() -> None:
    assert _normalize_pdf_url(
        "https://hts.usitc.gov/download/Chapter_99_2026HTSRev7.pdf"
    ) == "https://hts.usitc.gov/reststop/file?release=currentRelease&filename=Chapter%2099"


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
                "conditions": ["Product of the United Kingdom"],
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
