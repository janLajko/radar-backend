from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from radar_backend.db.connection import (
    acquire_connection,
    acquire_connection_with_transaction,
)
from radar_backend.db.repositories import (
    email_deliveries_repository,
    notification_recipients_repository,
    webhook_events_repository,
)
from radar_backend.domain import (
    AttemptExhaustedPayload,
    EmailDeliveryModel,
    NotificationRecipientModel,
    RecipientStatus,
    WebhookEntityType,
    WebhookEventType,
)
from radar_backend.services import email_service
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult

logger = logging.getLogger(__name__)

_EMAIL_DELIVERY_BATCH_SIZE = 100
_EMAIL_SEND_MAX_WORKERS = 5
_MAX_DELIVERY_ATTEMPT_COUNT = 3


class SendActionNotificationsStage:
    name = "send_action_notifications"

    def run(self, context: WorkerContext) -> StageResult:
        logger.info(
            "stage invoked: name=%s run_id=%s",
            self.name,
            context.run_id,
        )
        missing_config = email_service.missing_required_configuration()
        if missing_config:
            logger.error(
                "email configuration is incomplete; skip action notifications: invalid_or_missing=%s",
                ",".join(missing_config),
            )
            return StageResult()

        deliveries = _list_email_deliveries_to_send()
        logger.info(
            "email deliveries selected: count=%s run_id=%s",
            len(deliveries),
            context.run_id,
        )
        if not deliveries:
            return StageResult()

        with ThreadPoolExecutor(
            max_workers=_EMAIL_SEND_MAX_WORKERS,
            thread_name_prefix="email-send",
        ) as pool:
            futures = [
                (delivery, pool.submit(_process_email_delivery, delivery))
                for delivery in deliveries
            ]
            for delivery, future in futures:
                try:
                    future.result()
                except Exception:
                    logger.exception(
                        "email delivery processing escaped worker thread: "
                        "id=%s user_action_id=%s recipient_id=%s",
                        delivery["id"],
                        delivery["user_action_id"],
                        delivery["recipient_id"],
                    )

        return StageResult()


def _list_email_deliveries_to_send() -> list[EmailDeliveryModel]:
    with acquire_connection() as conn:
        return email_deliveries_repository.list_email_deliveries_to_send(
            conn,
            limit=_EMAIL_DELIVERY_BATCH_SIZE,
        )


def _process_email_delivery(delivery: EmailDeliveryModel) -> None:
    recipient = _get_recipient(delivery)
    if recipient is None or recipient["status"] is not RecipientStatus.ACTIVE:
        try:
            _mark_skipped(delivery)
        except Exception:
            logger.exception(
                "email delivery skipped but failed to mark skipped: "
                "id=%s user_action_id=%s recipient_id=%s",
                delivery["id"],
                delivery["user_action_id"],
                delivery["recipient_id"],
            )
            return

        logger.info(
            "email delivery skipped: id=%s user_action_id=%s recipient_id=%s",
            delivery["id"],
            delivery["user_action_id"],
            delivery["recipient_id"],
        )
        return

    send_succeeded = False

    try:
        email_service.send_email_delivery(delivery, recipient)
        send_succeeded = True
    except Exception:
        logger.exception(
            "email send failed: id=%s user_action_id=%s recipient_id=%s recipient_email=%s",
            delivery["id"],
            delivery["user_action_id"],
            delivery["recipient_id"],
            recipient["email"],
        )

    try:
        if send_succeeded:
            _mark_sent(delivery, recipient)
        else:
            _mark_failed(delivery, recipient)
    except Exception:
        if send_succeeded:
            logger.exception(
                "email sent but failed to mark sent: "
                "id=%s user_action_id=%s recipient_id=%s recipient_email=%s",
                delivery["id"],
                delivery["user_action_id"],
                delivery["recipient_id"],
                recipient["email"],
            )
            return

        logger.exception(
            "email failed and failed to mark failed: "
            "id=%s user_action_id=%s recipient_id=%s recipient_email=%s",
            delivery["id"],
            delivery["user_action_id"],
            delivery["recipient_id"],
            recipient["email"],
        )
        return

    if send_succeeded:
        logger.info(
            "email sent: id=%s user_action_id=%s recipient_id=%s recipient_email=%s",
            delivery["id"],
            delivery["user_action_id"],
            delivery["recipient_id"],
            recipient["email"],
        )


def _get_recipient(
    delivery: EmailDeliveryModel,
) -> NotificationRecipientModel | None:
    with acquire_connection() as conn:
        return notification_recipients_repository.get_by_id(
            conn,
            id=delivery["recipient_id"],
        )


def _mark_skipped(delivery: EmailDeliveryModel) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = email_deliveries_repository.mark_email_delivery_skipped(
            conn,
            id=delivery["id"],
        )
    if rowcount == 0:
        raise RuntimeError(
            "failed to mark email delivery skipped: "
            f"id={delivery['id']} user_action_id={delivery['user_action_id']} "
            f"recipient_id={delivery['recipient_id']} rowcount=0"
        )


def _mark_sent(
    delivery: EmailDeliveryModel,
    recipient: NotificationRecipientModel,
) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = email_deliveries_repository.mark_email_delivery_sent(
            conn,
            id=delivery["id"],
        )
    if rowcount == 0:
        raise RuntimeError(
            "failed to mark email delivery sent: "
            f"id={delivery['id']} user_action_id={delivery['user_action_id']} "
            f"recipient_id={delivery['recipient_id']} recipient_email={recipient['email']} "
            "rowcount=0"
        )


def _mark_failed(
    delivery: EmailDeliveryModel,
    recipient: NotificationRecipientModel,
) -> None:
    with acquire_connection_with_transaction() as conn:
        rowcount = email_deliveries_repository.mark_email_delivery_failed(
            conn,
            id=delivery["id"],
        )
        if rowcount == 0:
            raise RuntimeError(
                "failed to mark email delivery failed: "
                f"id={delivery['id']} user_action_id={delivery['user_action_id']} "
                f"recipient_id={delivery['recipient_id']} recipient_email={recipient['email']} "
                "rowcount=0"
            )

        updated_delivery = email_deliveries_repository.get_by_id(
            conn,
            id=delivery["id"],
        )
        if updated_delivery is None:
            raise RuntimeError(
                "email delivery disappeared after marking failed: "
                f"id={delivery['id']} user_action_id={delivery['user_action_id']} "
                f"recipient_id={delivery['recipient_id']} recipient_email={recipient['email']}"
            )

        if updated_delivery["attempt_count"] >= _MAX_DELIVERY_ATTEMPT_COUNT:
            webhook_events_repository.create_webhook_event(
                conn,
                event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                entity_type=WebhookEntityType.EMAIL_DELIVERY,
                entity_id=delivery["id"],
                payload=_attempt_exhausted_payload(delivery, recipient),
            )


def _attempt_exhausted_payload(
    delivery: EmailDeliveryModel,
    recipient: NotificationRecipientModel,
) -> AttemptExhaustedPayload:
    webhook_payload: AttemptExhaustedPayload = {
        "reason": "email_delivery_failed",
        "recipient_id": delivery["recipient_id"],
        "recipient_email": recipient["email"],
        "user_action_id": delivery["user_action_id"],
        "stage": SendActionNotificationsStage.name,
    }

    delivery_payload = delivery["payload"]
    source_label = delivery_payload.get("source_label")
    if isinstance(source_label, str):
        webhook_payload["source_label"] = source_label

    headline = delivery_payload.get("headline")
    if isinstance(headline, str):
        webhook_payload["headline"] = headline

    reference_number = delivery_payload.get("reference_number")
    if isinstance(reference_number, str) or reference_number is None:
        webhook_payload["reference_number"] = reference_number

    return webhook_payload
