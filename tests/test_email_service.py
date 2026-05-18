from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
import importlib

import pytest

from radar_backend.domain import (
    ActionType,
    EmailDeliveryModel,
    EmailDeliveryStatus,
    NotificationRecipientModel,
    RecipientStatus,
)
from radar_backend.services.email_service import EmailSendError

email_service_module = importlib.import_module("radar_backend.services.email_service")


def test_email_service_renders_action_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_messages: list[EmailMessage] = []

    _configure_email(monkeypatch)
    monkeypatch.setattr(
        email_service_module._SMTP_EMAIL_BUCKET,
        "acquire",
        lambda _tokens: None,
    )
    monkeypatch.setattr(
        email_service_module,
        "_send_email_message_once",
        sent_messages.append,
    )

    email_service_module.EmailService().send_email_delivery(_email_delivery(), _recipient())

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message["Subject"].startswith("[GingerControl] USTR USTR-2026-001:")
    assert len(message["Subject"]) <= 70
    assert message["From"] == "Gingercontrol <sender@example.test>"
    assert message["To"] == "recipient@example.test"

    text_body = message.get_body(preferencelist=("plain",))
    assert text_body is not None
    text = text_body.get_content()
    assert "A new USTR update affects 1 products on your sandbox." in text
    assert "Reclassify now: https://frontend.example.test/classifier?tab=compliance_radar_alert" in text
    assert "tariff recalculation." in text
    assert "tariff recalculation (effective" not in text
    assert "Monitoring page: https://frontend.example.test/compliance-radar?user_action_id=1001" in text
    assert (
        "View full briefing: "
        "https://frontend.example.test/compliance-radar?user_action_id=1001&open_type=view_details"
        in text
    )
    assert "Unsubscribe: https://frontend.example.test/compliance-radar/unsubscribe?token=token-1" in text


def test_email_service_retries_transient_send_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[EmailMessage] = []
    sleeps: list[int] = []

    def send_email_message_once(message: EmailMessage) -> None:
        attempts.append(message)
        if len(attempts) == 1:
            raise EmailSendError("temporary SMTP failure")

    _configure_email(monkeypatch)
    monkeypatch.setattr(
        email_service_module._SMTP_EMAIL_BUCKET,
        "acquire",
        lambda _tokens: None,
    )
    monkeypatch.setattr(email_service_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        email_service_module,
        "_send_email_message_once",
        send_email_message_once,
    )

    email_service_module.EmailService().send_email_delivery(_email_delivery(), _recipient())

    assert len(attempts) == 2
    assert sleeps == [10]


def test_email_service_rejects_invalid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_email(monkeypatch)
    delivery = _email_delivery()
    delivery["payload"] = {**delivery["payload"], "headline": ""}

    with pytest.raises(EmailSendError, match="invalid email delivery payload"):
        email_service_module.EmailService().send_email_delivery(delivery, _recipient())


def test_email_service_requires_product_hts_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_email(monkeypatch)
    delivery = _email_delivery()
    delivery["payload"] = {
        **delivery["payload"],
        "affected_products": [{"product_name": "Crystalline Fructose"}],
    }

    with pytest.raises(EmailSendError, match="hts_code must be a non-empty string"):
        email_service_module.EmailService().send_email_delivery(delivery, _recipient())


def test_email_service_reports_missing_frontend_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_email(monkeypatch)
    monkeypatch.delenv("FRONTEND_BASE_URL", raising=False)

    assert "FRONTEND_BASE_URL" in email_service_module.missing_required_configuration()


def _configure_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://frontend.example.test/")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("FROM_EMAIL", "sender@example.test")
    monkeypatch.setenv("FROM_NAME", "Gingercontrol")


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
            "summary": "USTR announced a new exclusion window.",
            "source_url": "https://example.test/source",
            "affected_products": [
                {
                    "product_name": "Crystalline Fructose",
                    "hts_code": "1702.60.40.00",
                }
            ],
            "action_summaries": [
                {
                    "action_type": ActionType.RECLASSIFY_PRODUCT,
                    "product_count": 1,
                    "effective_date": "2026-05-01",
                },
                {
                    "action_type": ActionType.RECALCULATE_TARIFF,
                    "product_count": 1,
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


def _recipient() -> NotificationRecipientModel:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": 2001,
        "user_id": 3001,
        "email": "recipient@example.test",
        "unsubscribe_token": "token-1",
        "status": RecipientStatus.ACTIVE,
        "created_at": now,
        "updated_at": now,
    }
