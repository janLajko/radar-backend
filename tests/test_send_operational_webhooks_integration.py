from __future__ import annotations

import importlib
import ssl
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg import Connection

from radar_backend import config
from radar_backend.db.connection import acquire_connection, close_pool, open_pool
from radar_backend.db.repositories import webhook_events_repository
from radar_backend.domain import (
    WebhookEntityType,
    WebhookEventStatus,
    WebhookEventType,
)
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.send_operational_webhooks import (
    SendOperationalWebhooksStage,
)

ENABLE = False
VERIFY_TLS = False
_TEST_ENTITY_ID_BASE = 920_000_000_000

webhook_service_module = importlib.import_module("radar_backend.services.webhook_service")


def test_send_operational_webhooks_against_real_lark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not ENABLE:
        pytest.skip("send operational webhooks against real lark integration test is disabled")

    config.load_dotenv(Path(".env"))
    if not VERIFY_TLS:
        _disable_lark_tls_verification(monkeypatch)

    open_pool()
    event_id: int | None = None
    entity_id = _test_entity_id()

    try:
        with acquire_connection() as conn:
            event_id = _create_test_webhook_event(conn, entity_id=entity_id)
            conn.commit()

        SendOperationalWebhooksStage().run(WorkerContext(run_id=uuid4().hex))

        with acquire_connection() as conn:
            event = webhook_events_repository.get_by_id(conn, id=event_id)

        assert event is not None
        assert event["status"] is WebhookEventStatus.SENT
        assert event["attempt_count"] == 1
        assert event["last_attempt_at"] is not None
        assert event["sent_at"] is not None
    finally:
        if event_id is not None:
            with acquire_connection() as conn:
                _delete_test_webhook_event(conn, event_id=event_id)
                conn.commit()
        close_pool()


def _create_test_webhook_event(conn: Connection, *, entity_id: int) -> int:
    webhook_event_id = webhook_events_repository.create_webhook_event(
        conn,
        event_type=WebhookEventType.POLICY_IMPACT_READY_FOR_REVIEW,
        entity_type=WebhookEntityType.POLICY_EXTRACT,
        entity_id=entity_id,
        payload={
            "headline": "Section 301 exclusion window requires reviewer confirmation",
            "source_label": "USTR",
            "reference_number": "USTR-2026-001",
            "review_url": f"https://cms/compliance-radar/review/{entity_id}",
        },
    )
    assert webhook_event_id is not None
    return webhook_event_id


def _delete_test_webhook_event(conn: Connection, *, event_id: int) -> None:
    conn.execute(
        """
        DELETE FROM radar_webhook_events
        WHERE id = %(event_id)s
        """,
        {"event_id": event_id},
    )


def _test_entity_id() -> int:
    return _TEST_ENTITY_ID_BASE + uuid4().int % 1_000_000


def _disable_lark_tls_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    original_urlopen = webhook_service_module.urllib.request.urlopen

    def urlopen_without_tls_verification(request, *, timeout: int):
        return original_urlopen(
            request,
            timeout=timeout,
            context=ssl._create_unverified_context(),
        )

    monkeypatch.setattr(
        webhook_service_module.urllib.request,
        "urlopen",
        urlopen_without_tls_verification,
    )
