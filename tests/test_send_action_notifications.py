from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock

import pytest

from radar_backend.domain import (
    ActionType,
    EmailDeliveryModel,
    EmailDeliveryStatus,
    NotificationRecipientModel,
    RecipientStatus,
    WebhookEntityType,
    WebhookEventType,
)
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages import send_action_notifications
from radar_backend.worker.stages.send_action_notifications import (
    SendActionNotificationsStage,
)


def test_stage_skips_without_email_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        send_action_notifications.email_service,
        "missing_required_configuration",
        lambda: ["SMTP_HOST"],
    )
    monkeypatch.setattr(
        send_action_notifications,
        "_list_email_deliveries_to_send",
        _fail_if_called,
    )

    SendActionNotificationsStage().run(WorkerContext(run_id="test-run"))


def test_stage_skips_without_frontend_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        send_action_notifications.email_service,
        "missing_required_configuration",
        lambda: ["FRONTEND_BASE_URL"],
    )
    monkeypatch.setattr(
        send_action_notifications,
        "_list_email_deliveries_to_send",
        _fail_if_called,
    )

    SendActionNotificationsStage().run(WorkerContext(run_id="test-run"))


def test_stage_marks_successful_and_failed_deliveries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery_to_send = _email_delivery(1)
    delivery_to_fail = _email_delivery(2)
    recipient = _recipient(1)
    sent: list[int] = []
    failed: list[int] = []
    lock = Lock()

    def send_email_delivery(
        delivery: EmailDeliveryModel,
        _recipient: NotificationRecipientModel,
    ) -> None:
        if delivery["id"] == delivery_to_fail["id"]:
            raise RuntimeError("send failed")

    def mark_sent(
        delivery: EmailDeliveryModel,
        _recipient: NotificationRecipientModel,
    ) -> None:
        with lock:
            sent.append(delivery["id"])

    def mark_failed(
        delivery: EmailDeliveryModel,
        _recipient: NotificationRecipientModel,
    ) -> None:
        with lock:
            failed.append(delivery["id"])

    monkeypatch.setattr(
        send_action_notifications.email_service,
        "missing_required_configuration",
        lambda: [],
    )
    monkeypatch.setattr(
        send_action_notifications,
        "_list_email_deliveries_to_send",
        lambda: [delivery_to_send, delivery_to_fail],
    )
    monkeypatch.setattr(send_action_notifications, "_get_recipient", lambda _delivery: recipient)
    monkeypatch.setattr(
        send_action_notifications.email_service,
        "send_email_delivery",
        send_email_delivery,
    )
    monkeypatch.setattr(send_action_notifications, "_mark_sent", mark_sent)
    monkeypatch.setattr(send_action_notifications, "_mark_failed", mark_failed)

    SendActionNotificationsStage().run(WorkerContext(run_id="test-run"))

    assert sent == [delivery_to_send["id"]]
    assert failed == [delivery_to_fail["id"]]


def test_stage_marks_inactive_recipient_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _email_delivery(1)
    recipient = _recipient(1, status=RecipientStatus.UNSUBSCRIBED)
    skipped: list[int] = []

    monkeypatch.setattr(
        send_action_notifications.email_service,
        "missing_required_configuration",
        lambda: [],
    )
    monkeypatch.setattr(
        send_action_notifications,
        "_list_email_deliveries_to_send",
        lambda: [delivery],
    )
    monkeypatch.setattr(send_action_notifications, "_get_recipient", lambda _delivery: recipient)
    monkeypatch.setattr(
        send_action_notifications.email_service,
        "send_email_delivery",
        _fail_if_called,
    )
    monkeypatch.setattr(
        send_action_notifications,
        "_mark_skipped",
        lambda delivery_to_mark: skipped.append(delivery_to_mark["id"]),
    )

    SendActionNotificationsStage().run(WorkerContext(run_id="test-run"))

    assert skipped == [delivery["id"]]


def test_stage_does_not_mark_failed_after_successful_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _email_delivery(1)
    recipient = _recipient(1)
    failed: list[int] = []

    def mark_sent(
        _delivery: EmailDeliveryModel,
        _recipient: NotificationRecipientModel,
    ) -> None:
        raise RuntimeError("mark sent failed")

    def mark_failed(
        delivery_to_mark: EmailDeliveryModel,
        _recipient: NotificationRecipientModel,
    ) -> None:
        failed.append(delivery_to_mark["id"])

    monkeypatch.setattr(
        send_action_notifications.email_service,
        "missing_required_configuration",
        lambda: [],
    )
    monkeypatch.setattr(
        send_action_notifications,
        "_list_email_deliveries_to_send",
        lambda: [delivery],
    )
    monkeypatch.setattr(send_action_notifications, "_get_recipient", lambda _delivery: recipient)
    monkeypatch.setattr(
        send_action_notifications.email_service,
        "send_email_delivery",
        lambda _delivery, _recipient: None,
    )
    monkeypatch.setattr(send_action_notifications, "_mark_sent", mark_sent)
    monkeypatch.setattr(send_action_notifications, "_mark_failed", mark_failed)

    SendActionNotificationsStage().run(WorkerContext(run_id="test-run"))

    assert failed == []


def test_list_email_deliveries_to_send_uses_batch_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, int | None]] = []
    connection = object()

    class FakeConnectionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *_exc_info: object) -> None:
            return None

    def list_email_deliveries_to_send(
        conn: object,
        *,
        limit: int | None,
    ) -> list[EmailDeliveryModel]:
        calls.append((conn, limit))
        return []

    monkeypatch.setattr(
        send_action_notifications,
        "acquire_connection",
        FakeConnectionContext,
    )
    monkeypatch.setattr(
        send_action_notifications.email_deliveries_repository,
        "list_email_deliveries_to_send",
        list_email_deliveries_to_send,
    )

    assert send_action_notifications._list_email_deliveries_to_send() == []
    assert calls == [
        (
            connection,
            send_action_notifications._EMAIL_DELIVERY_BATCH_SIZE,
        )
    ]


def test_mark_failed_creates_attempt_exhausted_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _email_delivery(1)
    recipient = _recipient(1)
    connection = object()
    created_webhooks: list[dict[str, object]] = []

    class FakeTransactionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *_exc_info: object) -> None:
            return None

    def mark_email_delivery_failed(conn: object, *, id: int) -> int:
        assert conn is connection
        assert id == delivery["id"]
        return 1

    def get_by_id(conn: object, *, id: int) -> EmailDeliveryModel:
        assert conn is connection
        assert id == delivery["id"]
        return {**delivery, "attempt_count": 3}

    def create_webhook_event(conn: object, **kwargs: object) -> int:
        assert conn is connection
        created_webhooks.append(kwargs)
        return 10

    monkeypatch.setattr(
        send_action_notifications,
        "acquire_connection_with_transaction",
        FakeTransactionContext,
    )
    monkeypatch.setattr(
        send_action_notifications.email_deliveries_repository,
        "mark_email_delivery_failed",
        mark_email_delivery_failed,
    )
    monkeypatch.setattr(
        send_action_notifications.email_deliveries_repository,
        "get_by_id",
        get_by_id,
    )
    monkeypatch.setattr(
        send_action_notifications.webhook_events_repository,
        "create_webhook_event",
        create_webhook_event,
    )

    send_action_notifications._mark_failed(delivery, recipient)

    assert created_webhooks == [
        {
            "event_type": WebhookEventType.ATTEMPT_EXHAUSTED,
            "entity_type": WebhookEntityType.EMAIL_DELIVERY,
            "entity_id": delivery["id"],
            "payload": {
                "reason": "email_delivery_failed",
                "source_label": delivery["payload"]["source_label"],
                "reference_number": delivery["payload"]["reference_number"],
                "headline": delivery["payload"]["headline"],
                "recipient_id": delivery["recipient_id"],
                "recipient_email": recipient["email"],
                "user_action_id": delivery["user_action_id"],
                "stage": SendActionNotificationsStage.name,
            },
        }
    ]


def _email_delivery(id: int) -> EmailDeliveryModel:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": id,
        "user_action_id": 9000 + id,
        "recipient_id": 8000 + id,
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
                }
            ],
        },
        "status": EmailDeliveryStatus.PENDING,
        "attempt_count": 0,
        "last_attempt_at": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _recipient(
    id: int,
    *,
    status: RecipientStatus = RecipientStatus.ACTIVE,
) -> NotificationRecipientModel:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": 8000 + id,
        "user_id": 7000 + id,
        "email": f"recipient-{id}@example.test",
        "unsubscribe_token": f"token-{id}",
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


def _fail_if_called(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("should not be called")
