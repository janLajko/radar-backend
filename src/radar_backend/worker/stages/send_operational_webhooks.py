from __future__ import annotations

import logging

from radar_backend import config
from radar_backend.db.connection import (
    acquire_connection,
    acquire_connection_with_transaction,
)
from radar_backend.db.repositories import webhook_events_repository
from radar_backend.domain import WebhookEventModel
from radar_backend.services import webhook_service
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult

logger = logging.getLogger(__name__)

_WEBHOOK_EVENT_BATCH_SIZE = 500


class SendOperationalWebhooksStage:
    name = "send_operational_webhooks"

    def run(self, context: WorkerContext) -> StageResult:
        logger.info(
            "stage invoked: name=%s run_id=%s",
            self.name,
            context.run_id,
        )
        if not config.lark_webhook_url():
            logger.error("LARK_WEBHOOK_URL is not configured; skip operational webhooks")
            return StageResult()

        events = _list_webhook_events_to_send()
        logger.info(
            "webhook events selected: count=%s run_id=%s",
            len(events),
            context.run_id,
        )

        for event in events:
            try:
                _process_webhook_event(event)
            except Exception:
                logger.exception(
                    "webhook event processing failed unexpectedly: "
                    "id=%s event_type=%s entity_type=%s entity_id=%s",
                    event["id"],
                    event["event_type"],
                    event["entity_type"],
                    event["entity_id"],
                )

        return StageResult()


def _list_webhook_events_to_send() -> list[WebhookEventModel]:
    with acquire_connection() as conn:
        return webhook_events_repository.list_webhook_events_to_send(
            conn,
            limit=_WEBHOOK_EVENT_BATCH_SIZE,
        )


def _process_webhook_event(event: WebhookEventModel) -> None:
    send_succeeded = False

    try:
        webhook_service.send_webhook_event(event)
        send_succeeded = True
    except Exception:
        logger.exception(
            "webhook send failed: id=%s event_type=%s entity_type=%s entity_id=%s",
            event["id"],
            event["event_type"],
            event["entity_type"],
            event["entity_id"],
        )

    try:
        if send_succeeded:
            _mark_sent(event)
        else:
            _mark_failed(event)
    except Exception:
        if send_succeeded:
            logger.exception(
                "webhook sent but failed to mark sent: "
                "id=%s event_type=%s entity_type=%s entity_id=%s",
                event["id"],
                event["event_type"],
                event["entity_type"],
                event["entity_id"],
            )
            return

        logger.exception(
            "webhook failed and failed to mark failed: "
            "id=%s event_type=%s entity_type=%s entity_id=%s",
            event["id"],
            event["event_type"],
            event["entity_type"],
            event["entity_id"],
        )
        return

    if send_succeeded:
        logger.info(
            "webhook sent: id=%s event_type=%s entity_type=%s entity_id=%s",
            event["id"],
            event["event_type"],
            event["entity_type"],
            event["entity_id"],
        )


def _mark_sent(event: WebhookEventModel) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = webhook_events_repository.mark_webhook_event_sent(
            conn,
            id=event["id"],
        )
    if rowcount == 0:
        raise RuntimeError(
            "failed to mark webhook event sent: "
            f"id={event['id']} event_type={event['event_type']} "
            f"entity_type={event['entity_type']} entity_id={event['entity_id']} "
            "rowcount=0"
        )


def _mark_failed(event: WebhookEventModel) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = webhook_events_repository.mark_webhook_event_failed(
            conn,
            id=event["id"],
        )
    if rowcount == 0:
        raise RuntimeError(
            "failed to mark webhook event failed: "
            f"id={event['id']} event_type={event['event_type']} "
            f"entity_type={event['entity_type']} entity_id={event['entity_id']} "
            "rowcount=0"
        )
