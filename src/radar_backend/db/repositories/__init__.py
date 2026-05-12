from __future__ import annotations

from dataclasses import dataclass

from radar_backend.db.connection import Database
from radar_backend.db.repositories.action_repository import ActionRepository
from radar_backend.db.repositories.notification_repository import NotificationRepository
from radar_backend.db.repositories.policy_repository import PolicyRepository
from radar_backend.db.repositories.webhook_repository import WebhookRepository


@dataclass(frozen=True)
class Repositories:
    policy: PolicyRepository
    action: ActionRepository
    notification: NotificationRepository
    webhook: WebhookRepository

    @classmethod
    def create(cls, db: Database) -> "Repositories":
        return cls(
            policy=PolicyRepository(db),
            action=ActionRepository(db),
            notification=NotificationRepository(db),
            webhook=WebhookRepository(db),
        )


__all__ = [
    "ActionRepository",
    "NotificationRepository",
    "PolicyRepository",
    "Repositories",
    "WebhookRepository",
]

