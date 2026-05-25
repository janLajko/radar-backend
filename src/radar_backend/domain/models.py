from __future__ import annotations

from datetime import date, datetime
from typing import Any, TypedDict

from radar_backend.domain.enums import (
    ActionCalculateStatus,
    ActionItemStatus,
    ActionType,
    EmailDeliveryStatus,
    PolicyImpactType,
    PolicyExtractStatus,
    PolicyReviewStatus,
    RawSourceItemPolicyUpdateStatus,
    RecipientStatus,
    UserActionStatus,
    WebhookEntityType,
    WebhookEventStatus,
    WebhookEventType,
)


class AffectedProduct(TypedDict):
    product_uid: str
    product_name: str
    hts_code: str
    suggested_actions: list[ActionType]


class ActionItem(TypedDict):
    action_type: ActionType
    effective_date: str | None
    status: ActionItemStatus


class EmailAffectedProduct(TypedDict, total=False):
    product_name: str
    hts_code: str


class EmailActionSummary(TypedDict, total=False):
    action_type: ActionType
    product_count: int
    effective_date: str | None


class EmailDeliveryPayload(TypedDict, total=False):
    account_owner_email: str
    source_label: str
    reference_number: str | None
    headline: str
    summary: str
    source_url: str
    affected_products: list[EmailAffectedProduct]
    action_summaries: list[EmailActionSummary]


class PolicyImpactReadyForReviewPayload(TypedDict, total=False):
    headline: str
    source_label: str
    reference_number: str | None
    review_url: str
    source_url: str


class AttemptExhaustedPayload(TypedDict, total=False):
    reason: str
    source_label: str
    reference_number: str | None
    headline: str
    source_url: str
    attempt_count: int
    recipient_id: int
    recipient_email: str
    user_action_id: int
    stage: str


type WebhookPayload = PolicyImpactReadyForReviewPayload | AttemptExhaustedPayload


class RawSourceItemModel(TypedDict):
    id: int
    source_key: str
    source_label: str
    source_item_key: str
    source_url: str
    source_metadata: dict[str, Any]
    source_title: str
    source_content: str
    pdf_urls: list[str]
    reference_number: str | None
    published_at: datetime | None
    policy_update_status: RawSourceItemPolicyUpdateStatus
    policy_update_attempt_count: int
    created_at: datetime
    updated_at: datetime


class PolicyUpdateModel(TypedDict):
    id: int
    raw_source_item_id: int
    source_key: str
    source_label: str
    source_url: str
    source_metadata: dict[str, Any]
    source_title: str
    source_content: str
    pdf_urls: list[str]
    reference_number: str | None
    published_at: datetime | None
    effective_date: date | None
    headline: str
    summary: str
    briefing: str
    policy_extract_status: PolicyExtractStatus
    policy_extract_attempt_count: int
    policy_review_status: PolicyReviewStatus
    action_calculate_status: ActionCalculateStatus
    action_calculate_attempt_count: int
    created_at: datetime
    updated_at: datetime


class PolicyImpactModel(TypedDict):
    id: int
    policy_update_id: int
    hts_number: str
    impacted_type: PolicyImpactType
    effective_time: date | None
    coos: list[str] | None
    row_desc: str | None
    created_at: datetime
    updated_at: datetime


class UserActionModel(TypedDict):
    id: int
    user_id: int
    policy_update_id: int
    affected_products: list[AffectedProduct]
    action_items: list[ActionItem]
    status: UserActionStatus
    completed_at: datetime | None
    completed_by: int | None
    created_at: datetime
    updated_at: datetime


class NotificationRecipientModel(TypedDict):
    id: int
    user_id: int
    email: str
    unsubscribe_token: str
    status: RecipientStatus
    created_at: datetime
    updated_at: datetime


class EmailDeliveryModel(TypedDict):
    id: int
    user_action_id: int
    recipient_id: int
    payload: EmailDeliveryPayload
    status: EmailDeliveryStatus
    attempt_count: int
    last_attempt_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WebhookEventModel(TypedDict):
    id: int
    event_type: WebhookEventType
    entity_type: WebhookEntityType
    entity_id: int
    payload: WebhookPayload
    status: WebhookEventStatus
    attempt_count: int
    last_attempt_at: datetime | None
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProductCandidate(TypedDict):
    user_id: int
    account_owner_email: str | None
    product_uid: str
    product_name: str
    hts_code: str
    hts_code_normalized: str
    candidate_rank: int | None


class SavedTariffSelection(TypedDict):
    user_id: int
    account_owner_email: str | None
    product_uid: str
    product_name: str
    hts_code: str
    hts_code_normalized: str
    country_code: str
