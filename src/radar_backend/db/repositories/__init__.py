from radar_backend.db.repositories.email_deliveries_repository import (
    EmailDeliveriesRepository,
)
from radar_backend.db.repositories.notification_recipients_repository import (
    NotificationRecipientsRepository,
)
from radar_backend.db.repositories.policy_impacts_repository import PolicyImpactsRepository
from radar_backend.db.repositories.policy_updates_repository import PolicyUpdatesRepository
from radar_backend.db.repositories.product_match_repository import ProductMatchRepository
from radar_backend.db.repositories.raw_source_items_repository import RawSourceItemsRepository
from radar_backend.db.repositories.user_actions_repository import UserActionsRepository
from radar_backend.db.repositories.user_action_targets_repository import (
    UserActionTargetsRepository,
)
from radar_backend.db.repositories.webhook_events_repository import WebhookEventsRepository

email_deliveries_repository = EmailDeliveriesRepository()
notification_recipients_repository = NotificationRecipientsRepository()
policy_impacts_repository = PolicyImpactsRepository()
policy_updates_repository = PolicyUpdatesRepository()
product_match_repository = ProductMatchRepository()
raw_source_items_repository = RawSourceItemsRepository()
user_actions_repository = UserActionsRepository()
user_action_targets_repository = UserActionTargetsRepository()
webhook_events_repository = WebhookEventsRepository()

__all__ = [
    "email_deliveries_repository",
    "notification_recipients_repository",
    "policy_impacts_repository",
    "policy_updates_repository",
    "product_match_repository",
    "raw_source_items_repository",
    "user_actions_repository",
    "user_action_targets_repository",
    "webhook_events_repository",
]
