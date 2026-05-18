from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import Connection

from radar_backend import config
from radar_backend.db.connection import acquire_connection, close_pool, open_pool
from radar_backend.db.repositories import (
    email_deliveries_repository,
    notification_recipients_repository,
    policy_updates_repository,
    user_actions_repository,
    webhook_events_repository,
)
from radar_backend.domain import (
    ActionCalculateStatus,
    ActionItemStatus,
    ActionType,
    EmailDeliveryStatus,
    PolicyExtractStatus,
    PolicyReviewStatus,
    RecipientStatus,
    UserActionStatus,
    WebhookEntityType,
    WebhookEventStatus,
    WebhookEventType,
)


@pytest.fixture
def conn() -> Iterator[Connection]:
    config.load_dotenv(Path(".env"))
    open_pool()

    try:
        with acquire_connection() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                connection.execute("ROLLBACK")
    finally:
        close_pool()


def test_repositories_against_real_database(conn: Connection) -> None:
    suffix = uuid4().hex
    user_id = 910_000_000_000
    another_user_id = 910_000_000_001

    candidate_policy_update_id = _insert_policy_update(
        conn,
        suffix=suffix,
        raw_index=1,
        policy_review_status="approved",
        action_calculate_status="pending",
        action_calculate_attempt_count=0,
    )
    excluded_unapproved_policy_update_id = _insert_policy_update(
        conn,
        suffix=suffix,
        raw_index=2,
        policy_review_status="confirm_needed",
        action_calculate_status="pending",
        action_calculate_attempt_count=0,
    )
    excluded_exhausted_policy_update_id = _insert_policy_update(
        conn,
        suffix=suffix,
        raw_index=3,
        policy_review_status="approved",
        action_calculate_status="failed",
        action_calculate_attempt_count=3,
    )

    policy_update = policy_updates_repository.get_by_id(
        conn,
        id=candidate_policy_update_id,
    )
    assert policy_update is not None
    assert policy_update["id"] == candidate_policy_update_id
    assert policy_update["policy_extract_status"] is PolicyExtractStatus.PENDING
    assert policy_update["policy_review_status"] is PolicyReviewStatus.APPROVED
    assert policy_update["action_calculate_status"] is ActionCalculateStatus.PENDING

    policy_updates_to_calculate = (
        policy_updates_repository.list_policy_updates_to_calculate_user_actions(conn)
    )
    policy_update_ids_to_calculate = {
        policy_update["id"]
        for policy_update in policy_updates_to_calculate
    }
    assert candidate_policy_update_id in policy_update_ids_to_calculate
    assert excluded_unapproved_policy_update_id not in policy_update_ids_to_calculate
    assert excluded_exhausted_policy_update_id not in policy_update_ids_to_calculate
    assert len(
        policy_updates_repository.list_policy_updates_to_calculate_user_actions(
            conn,
            limit=1,
        )
    ) <= 1

    assert policy_updates_repository.mark_action_calculate_failed(
        conn,
        id=candidate_policy_update_id,
    ) == 1
    policy_update = policy_updates_repository.get_by_id(conn, id=candidate_policy_update_id)
    assert policy_update is not None
    assert policy_update["action_calculate_status"] is ActionCalculateStatus.FAILED
    assert policy_update["action_calculate_attempt_count"] == 1

    assert policy_updates_repository.mark_action_calculate_succeeded(
        conn,
        id=candidate_policy_update_id,
    ) == 1
    policy_update = policy_updates_repository.get_by_id(conn, id=candidate_policy_update_id)
    assert policy_update is not None
    assert policy_update["action_calculate_status"] is ActionCalculateStatus.SUCCEEDED
    assert policy_update["action_calculate_attempt_count"] == 2

    active_recipient_id = _insert_notification_recipient(
        conn,
        suffix=suffix,
        user_id=user_id,
        recipient_index=1,
        status="active",
    )
    _insert_notification_recipient(
        conn,
        suffix=suffix,
        user_id=user_id,
        recipient_index=2,
        status="unsubscribed",
    )
    _insert_notification_recipient(
        conn,
        suffix=suffix,
        user_id=user_id,
        recipient_index=3,
        status="deleted",
    )

    active_recipient = notification_recipients_repository.get_by_id(
        conn,
        id=active_recipient_id,
    )
    assert active_recipient is not None
    assert active_recipient["status"] is RecipientStatus.ACTIVE

    active_recipients = notification_recipients_repository.list_active_recipients_by_user_id(
        conn,
        user_id=user_id,
    )
    assert [recipient["id"] for recipient in active_recipients] == [active_recipient_id]

    affected_products = [
        {
            "product_uid": f"product-{suffix}",
            "product_name": "Test Product",
            "hts_code": "1702.60.40.00",
            "suggested_actions": [
                ActionType.RECLASSIFY_PRODUCT,
                ActionType.RECALCULATE_TARIFF,
            ],
        }
    ]
    action_items = [
        {
            "action_type": ActionType.RECLASSIFY_PRODUCT,
            "effective_date": "2026-05-01",
            "status": ActionItemStatus.ACTION_NEEDED,
        },
        {
            "action_type": ActionType.RECALCULATE_TARIFF,
            "effective_date": None,
            "status": ActionItemStatus.ACTION_NEEDED,
        },
    ]

    user_action_id = user_actions_repository.create_user_action(
        conn,
        user_id=user_id,
        policy_update_id=candidate_policy_update_id,
        affected_products=affected_products,
        action_items=action_items,
    )
    assert user_action_id is not None
    assert user_actions_repository.create_user_action(
        conn,
        user_id=user_id,
        policy_update_id=candidate_policy_update_id,
        affected_products=affected_products,
        action_items=action_items,
    ) is None

    user_action = user_actions_repository.get_by_id(conn, id=user_action_id)
    assert user_action is not None
    assert user_action["status"] is UserActionStatus.ACTION_NEEDED
    assert user_action["affected_products"] == affected_products
    assert user_action["action_items"] == action_items

    email_delivery_id = email_deliveries_repository.create_email_delivery(
        conn,
        user_action_id=user_action_id,
        recipient_id=active_recipient_id,
        payload=_email_delivery_payload(suffix),
    )
    assert email_delivery_id is not None
    assert email_deliveries_repository.create_email_delivery(
        conn,
        user_action_id=user_action_id,
        recipient_id=active_recipient_id,
        payload=_email_delivery_payload(suffix),
    ) is None

    email_delivery = email_deliveries_repository.get_by_id(conn, id=email_delivery_id)
    assert email_delivery is not None
    assert email_delivery["payload"] == _email_delivery_payload(suffix)
    assert email_delivery["status"] is EmailDeliveryStatus.PENDING
    assert email_delivery["attempt_count"] == 0

    email_delivery_ids_to_send = {
        delivery["id"]
        for delivery in email_deliveries_repository.list_email_deliveries_to_send(conn)
    }
    assert email_delivery_id in email_delivery_ids_to_send
    assert len(email_deliveries_repository.list_email_deliveries_to_send(conn, limit=1)) <= 1

    assert email_deliveries_repository.mark_email_delivery_failed(
        conn,
        id=email_delivery_id,
    ) == 1
    email_delivery = email_deliveries_repository.get_by_id(conn, id=email_delivery_id)
    assert email_delivery is not None
    assert email_delivery["status"] is EmailDeliveryStatus.FAILED
    assert email_delivery["attempt_count"] == 1
    assert email_delivery["last_attempt_at"] is not None
    assert email_delivery["sent_at"] is None

    assert email_deliveries_repository.mark_email_delivery_sent(
        conn,
        id=email_delivery_id,
    ) == 1
    email_delivery = email_deliveries_repository.get_by_id(conn, id=email_delivery_id)
    assert email_delivery is not None
    assert email_delivery["status"] is EmailDeliveryStatus.SENT
    assert email_delivery["attempt_count"] == 2
    assert email_delivery["last_attempt_at"] is not None
    assert email_delivery["sent_at"] is not None
    email_delivery_ids_to_send = {
        delivery["id"]
        for delivery in email_deliveries_repository.list_email_deliveries_to_send(conn)
    }
    assert email_delivery_id not in email_delivery_ids_to_send

    skipped_user_action_id = user_actions_repository.create_user_action(
        conn,
        user_id=another_user_id,
        policy_update_id=candidate_policy_update_id,
        affected_products=affected_products,
        action_items=action_items,
    )
    assert skipped_user_action_id is not None
    skipped_delivery_id = email_deliveries_repository.create_email_delivery(
        conn,
        user_action_id=skipped_user_action_id,
        recipient_id=active_recipient_id,
        payload={},
    )
    assert skipped_delivery_id is not None
    assert email_deliveries_repository.mark_email_delivery_skipped(
        conn,
        id=skipped_delivery_id,
    ) == 1
    skipped_delivery = email_deliveries_repository.get_by_id(conn, id=skipped_delivery_id)
    assert skipped_delivery is not None
    assert skipped_delivery["status"] is EmailDeliveryStatus.SKIPPED
    assert skipped_delivery["attempt_count"] == 0
    assert skipped_delivery["last_attempt_at"] is None
    assert skipped_delivery["sent_at"] is None
    email_delivery_ids_to_send = {
        delivery["id"]
        for delivery in email_deliveries_repository.list_email_deliveries_to_send(conn)
    }
    assert skipped_delivery_id not in email_delivery_ids_to_send

    webhook_event_id = webhook_events_repository.create_webhook_event(
        conn,
        event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
        entity_type=WebhookEntityType.EMAIL_DELIVERY,
        entity_id=email_delivery_id,
        payload={"reason": "provider timeout"},
    )
    assert webhook_event_id is not None
    assert webhook_events_repository.create_webhook_event(
        conn,
        event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
        entity_type=WebhookEntityType.EMAIL_DELIVERY,
        entity_id=email_delivery_id,
        payload={"reason": "provider timeout"},
    ) is None

    webhook_event = webhook_events_repository.get_by_id(conn, id=webhook_event_id)
    assert webhook_event is not None
    assert webhook_event["event_type"] is WebhookEventType.ATTEMPT_EXHAUSTED
    assert webhook_event["entity_type"] is WebhookEntityType.EMAIL_DELIVERY
    assert webhook_event["payload"] == {"reason": "provider timeout"}
    assert webhook_event["status"] is WebhookEventStatus.PENDING

    webhook_event_ids_to_send = {
        event["id"]
        for event in webhook_events_repository.list_webhook_events_to_send(conn)
    }
    assert webhook_event_id in webhook_event_ids_to_send
    assert len(webhook_events_repository.list_webhook_events_to_send(conn, limit=1)) <= 1

    assert webhook_events_repository.mark_webhook_event_failed(
        conn,
        id=webhook_event_id,
    ) == 1
    webhook_event = webhook_events_repository.get_by_id(conn, id=webhook_event_id)
    assert webhook_event is not None
    assert webhook_event["status"] is WebhookEventStatus.FAILED
    assert webhook_event["attempt_count"] == 1
    assert webhook_event["last_attempt_at"] is not None
    assert webhook_event["sent_at"] is None

    assert webhook_events_repository.mark_webhook_event_sent(
        conn,
        id=webhook_event_id,
    ) == 1
    webhook_event = webhook_events_repository.get_by_id(conn, id=webhook_event_id)
    assert webhook_event is not None
    assert webhook_event["status"] is WebhookEventStatus.SENT
    assert webhook_event["attempt_count"] == 2
    assert webhook_event["last_attempt_at"] is not None
    assert webhook_event["sent_at"] is not None
    webhook_event_ids_to_send = {
        event["id"]
        for event in webhook_events_repository.list_webhook_events_to_send(conn)
    }
    assert webhook_event_id not in webhook_event_ids_to_send


def _insert_policy_update(
    conn: Connection,
    *,
    suffix: str,
    raw_index: int,
    policy_review_status: str,
    action_calculate_status: str,
    action_calculate_attempt_count: int,
) -> int:
    raw_source_item_id = _insert_raw_source_item(conn, suffix=suffix, raw_index=raw_index)
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
          policy_review_status,
          action_calculate_status,
          action_calculate_attempt_count
        )
        VALUES (
          %(raw_source_item_id)s,
          %(source_key)s,
          %(source_label)s,
          %(source_url)s,
          '{}'::jsonb,
          %(source_title)s,
          %(source_content)s,
          '[]'::jsonb,
          %(reference_number)s,
          now(),
          DATE '2026-05-01',
          %(headline)s,
          %(summary)s,
          %(briefing)s,
          %(policy_review_status)s,
          %(action_calculate_status)s,
          %(action_calculate_attempt_count)s
        )
        RETURNING id
        """,
        {
            "raw_source_item_id": raw_source_item_id,
            "source_key": "repository_integration_test",
            "source_label": "Repository Integration Test",
            "source_url": f"https://example.test/policy/{suffix}/{raw_index}",
            "source_title": f"Policy Source {suffix} {raw_index}",
            "source_content": f"Policy source content {suffix} {raw_index}",
            "reference_number": f"REF-{suffix}-{raw_index}",
            "headline": f"Policy headline {suffix} {raw_index}",
            "summary": f"Policy summary {suffix} {raw_index}",
            "briefing": f"Policy briefing {suffix} {raw_index}",
            "policy_review_status": policy_review_status,
            "action_calculate_status": action_calculate_status,
            "action_calculate_attempt_count": action_calculate_attempt_count,
        },
    ).fetchone()
    assert row is not None
    return row[0]


def _email_delivery_payload(suffix: str):
    return {
        "account_owner_email": "owner@example.test",
        "source_label": "USTR",
        "reference_number": f"USTR-{suffix[:8]}",
        "headline": "Section 301 exclusion window reopens",
        "summary": "USTR announced a new exclusion window.",
        "source_url": "https://example.test/source",
        "affected_products": [
            {
                "product_name": "Test Product",
                "hts_code": "1702.60.40.00",
            }
        ],
        "action_summaries": [
            {
                "action_type": ActionType.RECLASSIFY_PRODUCT,
                "product_count": 1,
                "effective_date": "2026-05-01",
            }
        ],
    }


def _insert_raw_source_item(
    conn: Connection,
    *,
    suffix: str,
    raw_index: int,
) -> int:
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
          published_at
        )
        VALUES (
          %(source_key)s,
          %(source_label)s,
          %(source_item_key)s,
          %(source_url)s,
          '{}'::jsonb,
          %(source_title)s,
          %(source_content)s,
          '[]'::jsonb,
          %(reference_number)s,
          now()
        )
        RETURNING id
        """,
        {
            "source_key": "repository_integration_test",
            "source_label": "Repository Integration Test",
            "source_item_key": f"{suffix}-{raw_index}",
            "source_url": f"https://example.test/raw/{suffix}/{raw_index}",
            "source_title": f"Raw Source {suffix} {raw_index}",
            "source_content": f"Raw source content {suffix} {raw_index}",
            "reference_number": f"RAW-{suffix}-{raw_index}",
        },
    ).fetchone()
    assert row is not None
    return row[0]


def _insert_notification_recipient(
    conn: Connection,
    *,
    suffix: str,
    user_id: int,
    recipient_index: int,
    status: str,
) -> int:
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
          %(status)s
        )
        RETURNING id
        """,
        {
            "user_id": user_id,
            "email": f"repo-{suffix}-{recipient_index}@example.test",
            "unsubscribe_token": f"unsubscribe-{suffix}-{recipient_index}",
            "status": status,
        },
    ).fetchone()
    assert row is not None
    return row[0]
