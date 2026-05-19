from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Iterator

import pytest

from radar_backend.domain import (
    ActionItemStatus,
    ActionType,
    PolicyImpactType,
    PolicyReviewStatus,
    PolicyExtractStatus,
    ActionCalculateStatus,
)
from radar_backend.worker.stages.create_user_actions import (
    ActionCalculationError,
    CreateUserActionsStage,
    NormalizedImpact,
    _calculate_user_action_candidates,
    _candidate_hts_prefixes,
    _commit_user_actions,
    _mark_failed,
    _normalize_impact,
)
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages import create_user_actions


def test_calculates_user_action_candidates_with_deduped_products_and_actions() -> None:
    policy_update = _policy_update(effective_date=date(2026, 6, 1))
    impacts = [
        NormalizedImpact(
            id=1,
            hts_prefix="1702",
            impacted_type=PolicyImpactType.DESC_CHANGED,
            action_types=(
                ActionType.RECLASSIFY_PRODUCT,
                ActionType.RECALCULATE_TARIFF,
            ),
            effective_date=None,
            coos=frozenset(),
        )
    ]
    product_match_data = {
        "product_candidates": [
            _product_candidate(
                user_id=100,
                product_uid="product-1",
                hts_code="1702.60.40.00",
                hts_code_normalized="1702604000",
                candidate_rank=5,
            ),
            _product_candidate(
                user_id=100,
                product_uid="product-1",
                hts_code="1702.60.60.00",
                hts_code_normalized="1702606000",
                candidate_rank=1,
            ),
        ],
        "calculation_coos_by_product_uid": {},
        "imported_coos_by_product_uid": {},
    }

    candidates = _calculate_user_action_candidates(
        policy_update=policy_update,
        impacts=impacts,
        product_match_data=product_match_data,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.user_id == 100
    assert candidate.affected_products == [
        {
            "product_uid": "product-1",
            "product_name": "Product product-1",
            "hts_code": "1702.60.60.00",
            "suggested_actions": [
                ActionType.RECLASSIFY_PRODUCT,
                ActionType.RECALCULATE_TARIFF,
            ],
        }
    ]
    assert candidate.action_items == [
        {
            "action_type": ActionType.RECLASSIFY_PRODUCT,
            "effective_date": "2026-06-01",
            "status": ActionItemStatus.ACTION_NEEDED,
        },
        {
            "action_type": ActionType.RECALCULATE_TARIFF,
            "effective_date": "2026-06-01",
            "status": ActionItemStatus.ACTION_NEEDED,
        },
    ]
    assert candidate.email_payload["action_summaries"] == [
        {
            "action_type": ActionType.RECLASSIFY_PRODUCT,
            "product_count": 1,
            "effective_date": "2026-06-01",
        },
        {
            "action_type": ActionType.RECALCULATE_TARIFF,
            "product_count": 1,
            "effective_date": "2026-06-01",
        },
    ]


def test_measure_changed_uses_calculation_or_imported_coo() -> None:
    policy_update = _policy_update()
    impacts = [
        NormalizedImpact(
            id=1,
            hts_prefix="1702",
            impacted_type=PolicyImpactType.MEASURE_CHANGED,
            action_types=(ActionType.RECALCULATE_TARIFF,),
            effective_date=date(2026, 5, 1),
            coos=frozenset({"CN"}),
        )
    ]
    product_match_data = {
        "product_candidates": [
            _product_candidate(
                user_id=100,
                product_uid="calculation-match",
                hts_code_normalized="1702604000",
            ),
            _product_candidate(
                user_id=100,
                product_uid="imported-match",
                hts_code_normalized="1702999999",
            ),
            _product_candidate(
                user_id=100,
                product_uid="no-match",
                hts_code_normalized="1702111111",
            ),
        ],
        "calculation_coos_by_product_uid": {
            "calculation-match": [
                {
                    "product_uid": "calculation-match",
                    "hts_code_normalized": "1702604000",
                    "country_code": "CN",
                }
            ]
        },
        "imported_coos_by_product_uid": {
            "imported-match": [
                {
                    "product_uid": "imported-match",
                    "country_code": "CN",
                }
            ],
            "no-match": [
                {
                    "product_uid": "no-match",
                    "country_code": "MX",
                }
            ],
        },
    }

    candidates = _calculate_user_action_candidates(
        policy_update=policy_update,
        impacts=impacts,
        product_match_data=product_match_data,
    )

    assert [product["product_uid"] for product in candidates[0].affected_products] == [
        "calculation-match",
        "imported-match",
    ]
    assert candidates[0].action_items == [
        {
            "action_type": ActionType.RECALCULATE_TARIFF,
            "effective_date": "2026-05-01",
            "status": ActionItemStatus.ACTION_NEEDED,
        }
    ]


def test_inserted_policy_impact_is_normalized_to_no_action() -> None:
    assert _normalize_impact(_policy_impact(PolicyImpactType.INSERTED)) is None


def test_invalid_hts_prefix_is_rejected() -> None:
    with pytest.raises(ActionCalculationError):
        _normalize_impact(
            _policy_impact(
                PolicyImpactType.RATE_CHANGED,
                hts_number="123",
            )
        )


def test_non_measure_policy_impact_rejects_coos() -> None:
    with pytest.raises(ActionCalculationError):
        _normalize_impact(
            _policy_impact(
                PolicyImpactType.DELETED,
                coos=["CN"],
            )
        )


def test_candidate_hts_prefixes_use_only_valid_business_lengths() -> None:
    assert _candidate_hts_prefixes(
        _product_candidate(
            user_id=100,
            product_uid="product-1",
            hts_code_normalized="1234567890",
        )
    ) == ["12", "1234", "123456", "12345678", "1234567890"]


def test_stage_marks_failed_policy_update_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_policy_update = _policy_update(id=1)
    succeeded_policy_update = _policy_update(id=2)
    processed: list[int] = []
    marked_failed: list[int] = []

    def create_user_actions_stub(policy_update, _product_match_data) -> None:
        processed.append(policy_update["id"])
        if policy_update["id"] == failed_policy_update["id"]:
            raise RuntimeError("action calculation failed")

    monkeypatch.setattr(
        create_user_actions,
        "_list_policy_updates_to_calculate_user_actions",
        lambda: [failed_policy_update, succeeded_policy_update],
    )
    monkeypatch.setattr(
        create_user_actions,
        "_load_product_match_data",
        lambda: {
            "product_candidates": [],
            "calculation_coos_by_product_uid": {},
            "imported_coos_by_product_uid": {},
        },
    )
    monkeypatch.setattr(
        create_user_actions,
        "_create_user_actions",
        create_user_actions_stub,
    )
    monkeypatch.setattr(
        create_user_actions,
        "_mark_failed",
        lambda policy_update: marked_failed.append(policy_update["id"]),
    )

    CreateUserActionsStage().run(WorkerContext(run_id="test-run"))

    assert processed == [1, 2]
    assert marked_failed == [1]


def test_commit_user_actions_marks_succeeded_with_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marked_succeeded: list[int] = []

    monkeypatch.setattr(
        create_user_actions,
        "acquire_connection_with_transaction",
        _fake_transaction,
    )
    monkeypatch.setattr(
        create_user_actions.policy_updates_repository,
        "mark_action_calculate_succeeded",
        lambda _conn, *, id: marked_succeeded.append(id) or 1,
    )

    _commit_user_actions(_policy_update(id=7), [])

    assert marked_succeeded == [7]


def test_mark_failed_writes_attempt_count_to_webhook_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = []

    monkeypatch.setattr(
        create_user_actions,
        "acquire_connection_with_transaction",
        _fake_transaction,
    )
    monkeypatch.setattr(
        create_user_actions.policy_updates_repository,
        "mark_action_calculate_failed",
        lambda _conn, *, id: 1,
    )
    monkeypatch.setattr(
        create_user_actions.policy_updates_repository,
        "get_by_id",
        lambda _conn, *, id: _policy_update(
            id=id,
            action_calculate_attempt_count=3,
        ),
    )

    def create_webhook_event(_conn, **kwargs) -> int:
        payloads.append(kwargs["payload"])
        return 1

    monkeypatch.setattr(
        create_user_actions.webhook_events_repository,
        "create_webhook_event",
        create_webhook_event,
    )

    _mark_failed(_policy_update(id=9))

    assert payloads == [
        {
            "reason": "action_calculate_failed",
            "source_label": "USTR",
            "reference_number": "USTR-2026-001",
            "headline": "Policy headline",
            "source_url": "https://example.test/source",
            "attempt_count": 3,
            "stage": CreateUserActionsStage.name,
        }
    ]


@contextmanager
def _fake_transaction() -> Iterator[object]:
    yield object()


def _policy_update(
    *,
    id: int = 1,
    effective_date: date | None = None,
    action_calculate_attempt_count: int = 0,
):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": id,
        "raw_source_item_id": 10,
        "source_key": "ustr",
        "source_label": "USTR",
        "source_url": "https://example.test/source",
        "source_metadata": {},
        "source_title": "Source title",
        "source_content": "Source content",
        "pdf_urls": [],
        "reference_number": "USTR-2026-001",
        "published_at": now,
        "effective_date": effective_date,
        "headline": "Policy headline",
        "summary": "Policy summary",
        "briefing": "Policy briefing",
        "policy_extract_status": PolicyExtractStatus.SUCCEEDED,
        "policy_extract_attempt_count": 1,
        "policy_review_status": PolicyReviewStatus.APPROVED,
        "action_calculate_status": ActionCalculateStatus.PENDING,
        "action_calculate_attempt_count": action_calculate_attempt_count,
        "created_at": now,
        "updated_at": now,
    }


def _policy_impact(
    impacted_type: PolicyImpactType,
    *,
    hts_number: str = "1702.60.40.00",
    coos: list[str] | None = None,
):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": 1,
        "policy_update_id": 1,
        "hts_number": hts_number,
        "impacted_type": impacted_type,
        "effective_time": None,
        "coos": coos,
        "row_desc": None,
        "created_at": now,
        "updated_at": now,
    }


def _product_candidate(
    *,
    user_id: int,
    product_uid: str,
    hts_code: str = "1702.60.40.00",
    hts_code_normalized: str = "1702604000",
    candidate_rank: int | None = 1,
):
    return {
        "user_id": user_id,
        "account_owner_email": f"user-{user_id}@example.test",
        "product_uid": product_uid,
        "product_name": f"Product {product_uid}",
        "hts_code": hts_code,
        "hts_code_normalized": hts_code_normalized,
        "candidate_rank": candidate_rank,
    }
