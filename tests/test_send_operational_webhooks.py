from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock

import pytest

from radar_backend.domain import (
    WebhookEntityType,
    WebhookEventStatus,
    WebhookEventType,
)
from radar_backend.domain.models import WebhookEventModel
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages import send_operational_webhooks
from radar_backend.worker.stages.send_operational_webhooks import (
    SendOperationalWebhooksStage,
)


def test_stage_skips_without_lark_webhook_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(send_operational_webhooks.config, "lark_webhook_url", lambda: "")
    monkeypatch.setattr(
        send_operational_webhooks,
        "_list_webhook_events_to_send",
        _fail_if_called,
    )

    SendOperationalWebhooksStage().run(WorkerContext(run_id="test-run"))


def test_stage_marks_successful_and_failed_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_to_send = _webhook_event(1)
    event_to_fail = _webhook_event(2)
    sent: list[int] = []
    failed: list[int] = []
    lock = Lock()

    def send_webhook_event(event: WebhookEventModel) -> None:
        if event["id"] == event_to_fail["id"]:
            raise RuntimeError("send failed")

    def mark_sent(event: WebhookEventModel) -> None:
        with lock:
            sent.append(event["id"])

    def mark_failed(event: WebhookEventModel) -> None:
        with lock:
            failed.append(event["id"])

    monkeypatch.setattr(
        send_operational_webhooks.config,
        "lark_webhook_url",
        lambda: "https://example.test/lark",
    )
    monkeypatch.setattr(
        send_operational_webhooks,
        "_list_webhook_events_to_send",
        lambda: [event_to_send, event_to_fail],
    )
    monkeypatch.setattr(
        send_operational_webhooks.webhook_service,
        "send_webhook_event",
        send_webhook_event,
    )
    monkeypatch.setattr(send_operational_webhooks, "_mark_sent", mark_sent)
    monkeypatch.setattr(send_operational_webhooks, "_mark_failed", mark_failed)

    SendOperationalWebhooksStage().run(WorkerContext(run_id="test-run"))

    assert sent == [event_to_send["id"]]
    assert failed == [event_to_fail["id"]]


def test_stage_does_not_mark_failed_after_successful_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _webhook_event(1)
    failed: list[int] = []

    def mark_sent(_event: WebhookEventModel) -> None:
        raise RuntimeError("mark sent failed")

    def mark_failed(event_to_mark: WebhookEventModel) -> None:
        failed.append(event_to_mark["id"])

    monkeypatch.setattr(
        send_operational_webhooks.config,
        "lark_webhook_url",
        lambda: "https://example.test/lark",
    )
    monkeypatch.setattr(
        send_operational_webhooks,
        "_list_webhook_events_to_send",
        lambda: [event],
    )
    monkeypatch.setattr(
        send_operational_webhooks.webhook_service,
        "send_webhook_event",
        lambda _event: None,
    )
    monkeypatch.setattr(send_operational_webhooks, "_mark_sent", mark_sent)
    monkeypatch.setattr(send_operational_webhooks, "_mark_failed", mark_failed)

    SendOperationalWebhooksStage().run(WorkerContext(run_id="test-run"))

    assert failed == []


def test_list_webhook_events_to_send_uses_batch_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, int | None]] = []
    connection = object()

    class FakeConnectionContext:
        def __enter__(self) -> object:
            return connection

        def __exit__(self, *_exc_info: object) -> None:
            return None

    def list_webhook_events_to_send(
        conn: object,
        *,
        limit: int | None,
    ) -> list[WebhookEventModel]:
        calls.append((conn, limit))
        return []

    monkeypatch.setattr(
        send_operational_webhooks,
        "acquire_connection",
        FakeConnectionContext,
    )
    monkeypatch.setattr(
        send_operational_webhooks.webhook_events_repository,
        "list_webhook_events_to_send",
        list_webhook_events_to_send,
    )

    assert send_operational_webhooks._list_webhook_events_to_send() == []
    assert calls == [
        (
            connection,
            send_operational_webhooks._WEBHOOK_EVENT_BATCH_SIZE,
        )
    ]


def _webhook_event(id: int) -> WebhookEventModel:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "id": id,
        "event_type": WebhookEventType.POLICY_IMPACT_READY_FOR_REVIEW,
        "entity_type": WebhookEntityType.POLICY_EXTRACT,
        "entity_id": 9000 + id,
        "payload": {
            "headline": "Policy impact ready for review",
            "source_label": "USTR",
            "reference_number": "USTR-2026-001",
            "review_url": "https://cms/compliance-radar/review/1",
        },
        "status": WebhookEventStatus.PENDING,
        "attempt_count": 0,
        "last_attempt_at": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _fail_if_called() -> None:
    raise AssertionError("should not be called")
