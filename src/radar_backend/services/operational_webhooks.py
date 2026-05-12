from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from radar_backend.db import Database
from radar_backend.domain import WebhookEntityType


@dataclass(frozen=True)
class OperationalWebhookResult:
    status: Literal["sent", "skipped", "failed"]


@dataclass(frozen=True)
class OperationalWebhookService:
    db: Database

    def notify_policy_impact_ready_for_review(
        self,
        *,
        policy_update_id: int,
        review_url: str,
    ) -> OperationalWebhookResult:
        raise NotImplementedError("notify_policy_impact_ready_for_review is not implemented yet")

    def notify_attempt_exhausted(
        self,
        *,
        entity_type: WebhookEntityType,
        entity_id: int,
    ) -> OperationalWebhookResult:
        raise NotImplementedError("notify_attempt_exhausted is not implemented yet")
