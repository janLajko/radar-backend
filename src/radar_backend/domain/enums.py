from __future__ import annotations

from enum import StrEnum


class RawPolicyUpdateStatus(StrEnum):
    PENDING = "pending"
    INGESTED = "ingested"
    DISCARDED = "discarded"
    FAILED = "failed"


class PolicyExtractStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PolicyReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"


class ActionCalculateStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class UserActionStatus(StrEnum):
    ACTION_NEEDED = "action_needed"
    COMPLETED = "completed"


class ActionItemStatus(StrEnum):
    ACTION_NEEDED = "action_needed"
    COMPLETED = "completed"


class RecipientStatus(StrEnum):
    ACTIVE = "active"
    UNSUBSCRIBED = "unsubscribed"
    DELETED = "deleted"


class EmailDeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class WebhookEventType(StrEnum):
    POLICY_IMPACT_READY_FOR_REVIEW = "policy_impact_ready_for_review"
    ATTEMPT_EXHAUSTED = "attempt_exhausted"


class WebhookEntityType(StrEnum):
    RAW_POLICY_UPDATE = "raw_policy_update"
    POLICY_EXTRACT = "policy_extract"
    ACTION_CALCULATE = "action_calculate"
    EMAIL_DELIVERY = "email_delivery"


class ActionType(StrEnum):
    RECLASSIFY_PRODUCT = "reclassify_product"
    RECALCULATE_TARIFF = "recalculate_tariff"

