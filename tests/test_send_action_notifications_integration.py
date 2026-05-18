from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radar_backend import config
from radar_backend.domain import (
    ActionType,
    EmailDeliveryModel,
    EmailDeliveryStatus,
    NotificationRecipientModel,
    RecipientStatus,
)
from radar_backend.services import email_service

ENABLE = False


def test_send_action_notification_against_real_smtp() -> None:
    if not ENABLE:
        pytest.skip("send action notification against real smtp integration test is disabled")

    config.load_dotenv(Path(".env"))
    recipient_email = os.getenv("TEST_EMAIL_RECIPIENT")
    if not recipient_email:
        pytest.fail("TEST_EMAIL_RECIPIENT is required when SMTP integration test is enabled")

    delivery = _email_delivery()
    recipient = _recipient(recipient_email)

    email_service.send_email_delivery(delivery, recipient)


def _email_delivery() -> EmailDeliveryModel:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": 1,
        "user_action_id": 1001,
        "recipient_id": 2001,
        "payload": {
            "account_owner_email": "owner@example.test",
            "source_label": "USTR",
            "reference_number": "USTR-2026-001",
            "headline": "Section 301 exclusion window reopens",
            "summary": "USTR announced a new exclusion window for affected products.",
            "source_url": "https://example.test/source",
            "affected_products": [
                {
                    "product_name": "Crystalline Fructose",
                    "hts_code": "1702.60.40.00",
                },
                {
                    "product_name": "Allulose Syrup",
                    "hts_code": "2106.90.99.99",
                },
            ],
            "action_summaries": [
                {
                    "action_type": ActionType.RECLASSIFY_PRODUCT,
                    "product_count": 1,
                    "effective_date": "2026-05-01",
                },
                {
                    "action_type": ActionType.RECALCULATE_TARIFF,
                    "product_count": 2,
                    "effective_date": None,
                },
            ],
        },
        "status": EmailDeliveryStatus.PENDING,
        "attempt_count": 0,
        "last_attempt_at": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _recipient(email: str) -> NotificationRecipientModel:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": 2001,
        "user_id": 3001,
        "email": email,
        "unsubscribe_token": "test-unsubscribe-token",
        "status": RecipientStatus.ACTIVE,
        "created_at": now,
        "updated_at": now,
    }
