from __future__ import annotations

from dataclasses import dataclass

from radar_backend.db.connection import Database
from radar_backend.db.repositories.email_deliveries import EmailDeliveriesRepository
from radar_backend.db.repositories.notification_recipients import NotificationRecipientsRepository
from radar_backend.db.repositories.policy_updates import PolicyUpdatesRepository
from radar_backend.db.repositories.raw_source_items import RawSourceItemsRepository
from radar_backend.db.repositories.user_actions import UserActionsRepository
from radar_backend.db.repositories.webhook_events import WebhookEventsRepository


@dataclass(frozen=True)
class Repositories:
    raw_source_items: RawSourceItemsRepository
    policy_updates: PolicyUpdatesRepository
    user_actions: UserActionsRepository
    notification_recipients: NotificationRecipientsRepository
    email_deliveries: EmailDeliveriesRepository
    webhook_events: WebhookEventsRepository

    @classmethod
    def create(cls, db: Database) -> "Repositories":
        return cls(
            raw_source_items=RawSourceItemsRepository(db),
            policy_updates=PolicyUpdatesRepository(db),
            user_actions=UserActionsRepository(db),
            notification_recipients=NotificationRecipientsRepository(db),
            email_deliveries=EmailDeliveriesRepository(db),
            webhook_events=WebhookEventsRepository(db),
        )


__all__ = [
    "EmailDeliveriesRepository",
    "NotificationRecipientsRepository",
    "PolicyUpdatesRepository",
    "RawSourceItemsRepository",
    "Repositories",
    "UserActionsRepository",
    "WebhookEventsRepository",
]
