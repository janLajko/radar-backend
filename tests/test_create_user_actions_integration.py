from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from psycopg import Connection

from radar_backend import config
from radar_backend.db.connection import acquire_connection, close_pool, open_pool
from radar_backend.db.repositories import (
    email_deliveries_repository,
    policy_updates_repository,
    user_actions_repository,
)
from radar_backend.domain import (
    ActionCalculateStatus,
    ActionItemStatus,
    ActionType,
    EmailDeliveryStatus,
)
from radar_backend.worker.stages.create_user_actions import (
    _create_user_actions,
    _load_product_match_data,
)


def test_create_user_actions_against_real_database() -> None:
    config.load_dotenv(Path(".env"))
    open_pool()

    suffix = uuid4().hex
    ids: dict[str, int | str] = {}

    try:
        with acquire_connection() as conn:
            ids = _insert_stage_4_fixture(conn, suffix=suffix)
            conn.commit()

        product_match_data = _load_product_match_data()

        with acquire_connection() as conn:
            policy_update = policy_updates_repository.get_by_id(
                conn,
                id=int(ids["policy_update_id"]),
            )
        assert policy_update is not None

        _create_user_actions(policy_update, product_match_data)

        with acquire_connection() as conn:
            updated_policy_update = policy_updates_repository.get_by_id(
                conn,
                id=int(ids["policy_update_id"]),
            )
            assert updated_policy_update is not None
            assert (
                updated_policy_update["action_calculate_status"]
                is ActionCalculateStatus.SUCCEEDED
            )
            assert updated_policy_update["action_calculate_attempt_count"] == 1

            user_action_id = user_actions_repository.get_id_by_user_and_policy_update(
                conn,
                user_id=int(ids["user_id"]),
                policy_update_id=int(ids["policy_update_id"]),
            )
            assert user_action_id is not None

            user_action = user_actions_repository.get_by_id(conn, id=user_action_id)
            assert user_action is not None
            assert user_action["affected_products"] == [
                {
                    "product_uid": ids["calculation_product_uid"],
                    "product_name": "Stage 4 Calculation Product",
                    "hts_code": "1702.60.40.00",
                    "suggested_actions": [
                        ActionType.RECLASSIFY_PRODUCT,
                        ActionType.RECALCULATE_TARIFF,
                    ],
                },
                {
                    "product_uid": ids["imported_product_uid"],
                    "product_name": "Stage 4 Imported Product",
                    "hts_code": "1702.90.10.00",
                    "suggested_actions": [
                        ActionType.RECLASSIFY_PRODUCT,
                        ActionType.RECALCULATE_TARIFF,
                    ],
                },
            ]
            assert user_action["action_items"] == [
                {
                    "action_type": ActionType.RECLASSIFY_PRODUCT,
                    "effective_date": "2026-06-01",
                    "status": ActionItemStatus.ACTION_NEEDED,
                },
                {
                    "action_type": ActionType.RECALCULATE_TARIFF,
                    "effective_date": "2026-05-01",
                    "status": ActionItemStatus.ACTION_NEEDED,
                },
            ]

            delivery = _get_email_delivery_by_user_action_id(conn, user_action_id)
            assert delivery is not None
            assert delivery["recipient_id"] == ids["recipient_id"]
            assert delivery["status"] is EmailDeliveryStatus.PENDING
            assert delivery["payload"]["account_owner_email"] == ids["user_email"]
            assert delivery["payload"]["action_summaries"] == [
                {
                    "action_type": ActionType.RECLASSIFY_PRODUCT,
                    "product_count": 2,
                    "effective_date": "2026-06-01",
                },
                {
                    "action_type": ActionType.RECALCULATE_TARIFF,
                    "product_count": 2,
                    "effective_date": "2026-05-01",
                },
            ]
    finally:
        if ids:
            with acquire_connection() as conn:
                _delete_stage_4_fixture(conn, ids=ids)
                conn.commit()
        close_pool()


def _insert_stage_4_fixture(conn: Connection, *, suffix: str) -> dict[str, int | str]:
    user_id = _insert_user(conn, suffix=suffix)
    calculation_product_uid = f"stage4-calc-{suffix}"
    imported_product_uid = f"stage4-import-{suffix}"

    _insert_product(
        conn,
        user_id=user_id,
        product_uid=calculation_product_uid,
        product_name="Stage 4 Calculation Product",
        suffix=suffix,
        index=1,
    )
    _insert_product(
        conn,
        user_id=user_id,
        product_uid=imported_product_uid,
        product_name="Stage 4 Imported Product",
        suffix=suffix,
        index=2,
    )
    _insert_hts_candidate(
        conn,
        product_uid=calculation_product_uid,
        hts_code="1702.60.40.00",
        hts_code_normalized="1702604000",
        candidate_rank=2,
    )
    _insert_hts_candidate(
        conn,
        product_uid=imported_product_uid,
        hts_code="1702.90.10.00",
        hts_code_normalized="1702901000",
        candidate_rank=1,
    )
    _insert_calculation_coo(
        conn,
        product_uid=calculation_product_uid,
        hts_code="1702.60.40.00",
        hts_code_normalized="1702604000",
        country_code="CN",
        suffix=suffix,
    )
    _insert_imported_coo(
        conn,
        product_uid=imported_product_uid,
        country_code="CN",
    )

    policy_update_id = _insert_policy_update(conn, suffix=suffix)
    _insert_policy_impact(
        conn,
        policy_update_id=policy_update_id,
        hts_number="1702",
        impacted_type="desc_changed",
        effective_time=None,
        coos=None,
    )
    _insert_policy_impact(
        conn,
        policy_update_id=policy_update_id,
        hts_number="1702",
        impacted_type="measure_changed",
        effective_time="2026-05-01",
        coos=["CN"],
    )
    recipient_id = _insert_recipient(conn, user_id=user_id, suffix=suffix)

    return {
        "suffix": suffix,
        "user_id": user_id,
        "user_email": f"stage4-{suffix}@example.test",
        "calculation_product_uid": calculation_product_uid,
        "imported_product_uid": imported_product_uid,
        "policy_update_id": policy_update_id,
        "recipient_id": recipient_id,
    }


def _insert_user(conn: Connection, *, suffix: str) -> int:
    row = conn.execute(
        """
        INSERT INTO users (email, provider_sub, name)
        VALUES (%(email)s, %(provider_sub)s, %(name)s)
        RETURNING id
        """,
        {
            "email": f"stage4-{suffix}@example.test",
            "provider_sub": f"stage4-{suffix}",
            "name": "Stage 4 Test User",
        },
    ).fetchone()
    assert row is not None
    return row[0]


def _insert_product(
    conn: Connection,
    *,
    user_id: int,
    product_uid: str,
    product_name: str,
    suffix: str,
    index: int,
) -> None:
    conn.execute(
        """
        INSERT INTO t_product (
          product_uid,
          user_id,
          case_id,
          product_id,
          product_name,
          workflow_status,
          progress_level,
          classification_type,
          product_source
        )
        VALUES (
          %(product_uid)s,
          %(user_id)s,
          %(case_id)s,
          %(product_id)s,
          %(product_name)s,
          'in_progress',
          10,
          'hts',
          'classifier'
        )
        """,
        {
            "product_uid": product_uid,
            "user_id": user_id,
            "case_id": f"stage4-case-{suffix}-{index}",
            "product_id": f"stage4-product-{suffix}-{index}",
            "product_name": product_name,
        },
    )


def _insert_hts_candidate(
    conn: Connection,
    *,
    product_uid: str,
    hts_code: str,
    hts_code_normalized: str,
    candidate_rank: int,
) -> None:
    conn.execute(
        """
        INSERT INTO t_product_hts_candidate (
          candidate_uid,
          product_uid,
          hts_code,
          hts_code_normalized,
          source,
          candidate_rank
        )
        VALUES (
          %(candidate_uid)s,
          %(product_uid)s,
          %(hts_code)s,
          %(hts_code_normalized)s,
          'classification',
          %(candidate_rank)s
        )
        """,
        {
            "candidate_uid": f"candidate-{product_uid}",
            "product_uid": product_uid,
            "hts_code": hts_code,
            "hts_code_normalized": hts_code_normalized,
            "candidate_rank": candidate_rank,
        },
    )


def _insert_calculation_coo(
    conn: Connection,
    *,
    product_uid: str,
    hts_code: str,
    hts_code_normalized: str,
    country_code: str,
    suffix: str,
) -> None:
    conn.execute(
        """
        INSERT INTO t_sandbox_calculation_result (
          result_uid,
          product_uid,
          source,
          hts_code,
          hts_code_normalized,
          country_code,
          result_json
        )
        VALUES (
          %(result_uid)s,
          %(product_uid)s,
          'workspace',
          %(hts_code)s,
          %(hts_code_normalized)s,
          %(country_code)s,
          '{}'::jsonb
        )
        """,
        {
            "result_uid": f"stage4-result-{suffix}",
            "product_uid": product_uid,
            "hts_code": hts_code,
            "hts_code_normalized": hts_code_normalized,
            "country_code": country_code,
        },
    )


def _insert_imported_coo(
    conn: Connection,
    *,
    product_uid: str,
    country_code: str,
) -> None:
    conn.execute(
        """
        INSERT INTO t_sandbox_product_profile (
          product_uid,
          imported_country_code
        )
        VALUES (
          %(product_uid)s,
          %(country_code)s
        )
        """,
        {
            "product_uid": product_uid,
            "country_code": country_code,
        },
    )


def _insert_policy_update(conn: Connection, *, suffix: str) -> int:
    raw_source_item_id = _insert_raw_source_item(conn, suffix=suffix)
    row = conn.execute(
        """
        INSERT INTO radar_policy_updates (
          raw_source_item_id,
          source_key,
          source_label,
          source_url,
          source_metadata,
          source_title,
          source_content,
          pdf_urls,
          reference_number,
          published_at,
          effective_date,
          headline,
          summary,
          briefing,
          policy_extract_status,
          policy_extract_attempt_count,
          policy_review_status,
          action_calculate_status,
          action_calculate_attempt_count
        )
        VALUES (
          %(raw_source_item_id)s,
          'stage4_test',
          'Stage 4 Test',
          %(source_url)s,
          '{}'::jsonb,
          'Stage 4 source title',
          'Stage 4 source content',
          '[]'::jsonb,
          'STAGE4-001',
          now(),
          DATE '2026-06-01',
          'Stage 4 test policy',
          'Stage 4 test summary',
          'Stage 4 test briefing',
          'succeeded',
          1,
          'approved',
          'pending',
          0
        )
        RETURNING id
        """,
        {
            "raw_source_item_id": raw_source_item_id,
            "source_url": f"https://example.test/stage4/{suffix}",
        },
    ).fetchone()
    assert row is not None
    return row[0]


def _insert_raw_source_item(conn: Connection, *, suffix: str) -> int:
    row = conn.execute(
        """
        INSERT INTO radar_raw_source_items (
          source_key,
          source_label,
          source_item_key,
          source_url,
          source_metadata,
          source_title,
          source_content,
          pdf_urls,
          reference_number,
          published_at,
          policy_update_status
        )
        VALUES (
          'stage4_test',
          'Stage 4 Test',
          %(source_item_key)s,
          %(source_url)s,
          '{}'::jsonb,
          'Stage 4 raw source title',
          'Stage 4 raw source content',
          '[]'::jsonb,
          'RAW-STAGE4-001',
          now(),
          'ingested'
        )
        RETURNING id
        """,
        {
            "source_item_key": f"stage4-{suffix}",
            "source_url": f"https://example.test/stage4/raw/{suffix}",
        },
    ).fetchone()
    assert row is not None
    return row[0]


def _insert_policy_impact(
    conn: Connection,
    *,
    policy_update_id: int,
    hts_number: str,
    impacted_type: str,
    effective_time: str | None,
    coos: list[str] | None,
) -> None:
    conn.execute(
        """
        INSERT INTO radar_policy_impacts (
          policy_update_id,
          hts_number,
          impacted_type,
          effective_time,
          coos,
          row_desc
        )
        VALUES (
          %(policy_update_id)s,
          %(hts_number)s,
          %(impacted_type)s,
          %(effective_time)s,
          %(coos)s,
          'Stage 4 integration policy impact'
        )
        """,
        {
            "policy_update_id": policy_update_id,
            "hts_number": hts_number,
            "impacted_type": impacted_type,
            "effective_time": effective_time,
            "coos": coos,
        },
    )


def _insert_recipient(conn: Connection, *, user_id: int, suffix: str) -> int:
    row = conn.execute(
        """
        INSERT INTO radar_notification_recipients (
          user_id,
          email,
          unsubscribe_token,
          status
        )
        VALUES (
          %(user_id)s,
          %(email)s,
          %(unsubscribe_token)s,
          'active'
        )
        RETURNING id
        """,
        {
            "user_id": user_id,
            "email": f"stage4-recipient-{suffix}@example.test",
            "unsubscribe_token": f"stage4-unsubscribe-{suffix}",
        },
    ).fetchone()
    assert row is not None
    return row[0]


def _get_email_delivery_by_user_action_id(conn: Connection, user_action_id: int):
    row = conn.execute(
        """
        SELECT id
        FROM radar_email_deliveries
        WHERE user_action_id = %(user_action_id)s
        """,
        {"user_action_id": user_action_id},
    ).fetchone()
    assert row is not None
    return email_deliveries_repository.get_by_id(conn, id=row[0])


def _delete_stage_4_fixture(
    conn: Connection,
    *,
    ids: dict[str, int | str],
) -> None:
    policy_update_id = ids["policy_update_id"]
    user_id = ids["user_id"]
    product_uids = [
        ids["calculation_product_uid"],
        ids["imported_product_uid"],
    ]

    conn.execute(
        """
        DELETE FROM radar_email_deliveries
        WHERE user_action_id IN (
          SELECT id FROM radar_user_actions WHERE policy_update_id = %(policy_update_id)s
        )
        """,
        {"policy_update_id": policy_update_id},
    )
    conn.execute(
        "DELETE FROM radar_user_actions WHERE policy_update_id = %(policy_update_id)s",
        {"policy_update_id": policy_update_id},
    )
    conn.execute(
        "DELETE FROM radar_notification_recipients WHERE user_id = %(user_id)s",
        {"user_id": user_id},
    )
    conn.execute(
        "DELETE FROM radar_policy_impacts WHERE policy_update_id = %(policy_update_id)s",
        {"policy_update_id": policy_update_id},
    )
    conn.execute(
        "DELETE FROM radar_policy_updates WHERE id = %(policy_update_id)s",
        {"policy_update_id": policy_update_id},
    )
    conn.execute(
        "DELETE FROM radar_raw_source_items WHERE source_item_key = %(source_item_key)s",
        {"source_item_key": f"stage4-{ids['suffix']}"},
    )
    conn.execute(
        "DELETE FROM t_sandbox_calculation_result WHERE product_uid = ANY(%(product_uids)s)",
        {"product_uids": product_uids},
    )
    conn.execute(
        "DELETE FROM t_sandbox_product_profile WHERE product_uid = ANY(%(product_uids)s)",
        {"product_uids": product_uids},
    )
    conn.execute(
        "DELETE FROM t_product_hts_candidate WHERE product_uid = ANY(%(product_uids)s)",
        {"product_uids": product_uids},
    )
    conn.execute(
        "DELETE FROM t_product WHERE product_uid = ANY(%(product_uids)s)",
        {"product_uids": product_uids},
    )
    conn.execute("DELETE FROM users WHERE id = %(user_id)s", {"user_id": user_id})
