from radar_backend.db.repositories.email_deliveries_repository import (
    EmailDeliveriesRepository,
)
from radar_backend.db.repositories.notification_recipients_repository import (
    NotificationRecipientsRepository,
)
from radar_backend.db.repositories.policy_updates_repository import PolicyUpdatesRepository
from radar_backend.db.repositories.raw_source_items_repository import RawSourceItemsRepository
from radar_backend.db.repositories.user_actions_repository import UserActionsRepository
from radar_backend.db.repositories.webhook_events_repository import WebhookEventsRepository

email_deliveries_repository = EmailDeliveriesRepository()
notification_recipients_repository = NotificationRecipientsRepository()
policy_updates_repository = PolicyUpdatesRepository()
raw_source_items_repository = RawSourceItemsRepository()
user_actions_repository = UserActionsRepository()
webhook_events_repository = WebhookEventsRepository()

__all__ = [
    "email_deliveries_repository",
    "notification_recipients_repository",
    "policy_updates_repository",
    "raw_source_items_repository",
    "user_actions_repository",
    "webhook_events_repository",
]
