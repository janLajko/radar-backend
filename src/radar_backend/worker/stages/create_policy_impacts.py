from __future__ import annotations

import logging

from radar_backend import config
from radar_backend.llm import build_provider
from radar_backend.llm.policy_impact_extractor import PolicyImpactInput, extract_policy_impact
from radar_backend.pdf import download_and_parse
from radar_backend.sources.http_client import HttpClient
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult
from radar_backend.db.repositories import policy_updates_repository
from radar_backend.db.repositories import webhook_events_repository
from radar_backend.domain import (
    AttemptExhaustedPayload,
    PolicyImpactReadyForReviewPayload,
    PolicyUpdateModel,
    WebhookEntityType,
    WebhookEventType,
)
from radar_backend.db.connection import (
    acquire_connection,
    acquire_connection_with_transaction,
)
from radar_backend.services.frontend_urls import policy_impact_review_url

logger = logging.getLogger(__name__)


class CreatePolicyImpactsStage:
    name = "create_policy_impacts"

    def run(self, context: WorkerContext) -> StageResult:
        http = HttpClient()
        llm = build_provider(
            model=config.policy_impact_llm_model(),
        )
        extracted = 0

        with acquire_connection() as conn:
            updates = policy_updates_repository.fetch_pending_for_policy_impact(conn)

        logger.info("create_policy_impacts: found %d updates to process", len(updates))

        for update in updates:
            new_count = update["policy_extract_attempt_count"] + 1

            # --- Step 1: Download attachments (outside transaction) ---
            attachment_text = ""
            if update["pdf_urls"]:
                try:
                    attachment_text = download_and_parse(update["pdf_urls"], http)
                except Exception as exc:
                    logger.warning(
                        "create_policy_impacts: pdf failed id=%s: %s", update["id"], exc
                    )
                    with acquire_connection_with_transaction() as conn:
                        policy_updates_repository.set_policy_extract_status(
                            conn, update["id"], "failed", new_count
                        )
                        if new_count >= 3:
                            webhook_events_repository.create_webhook_event(
                                conn,
                                event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                                entity_type=WebhookEntityType.POLICY_IMPACT,
                                entity_id=update["id"],
                                payload=_attempt_exhausted_payload(
                                    update,
                                    reason="policy_impact_pdf_failed",
                                    attempt_count=new_count,
                                ),
                            )
                    continue

            # --- Step 2: Run agent loop (outside transaction) ---
            try:
                impact_json = extract_policy_impact(
                    llm,
                    PolicyImpactInput(
                        policy_update_id=update["id"],
                        source_key=update["source_key"],
                        source_title=update["source_title"],
                        source_content=update["source_content"],
                        briefing=update["briefing"],
                        attachment_text=attachment_text,
                        source_url=update["source_url"],
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "create_policy_impacts: agent failed id=%s: %s", update["id"], exc
                )
                with acquire_connection_with_transaction() as conn:
                    policy_updates_repository.set_policy_extract_status(
                        conn, update["id"], "failed", new_count
                    )
                    if new_count >= 3:
                        webhook_events_repository.create_webhook_event(
                            conn,
                            event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                            entity_type=WebhookEntityType.POLICY_IMPACT,
                            entity_id=update["id"],
                            payload=_attempt_exhausted_payload(
                                update,
                                reason="policy_impact_extraction_failed",
                                attempt_count=new_count,
                            ),
                        )
                continue

            # --- Step 3: Persist result ---
            with acquire_connection_with_transaction() as conn:
                policy_updates_repository.set_policy_extract_status(
                    conn, update["id"], "succeeded", new_count, impact_json=impact_json
                )
                webhook_events_repository.create_webhook_event(
                    conn,
                    event_type=WebhookEventType.POLICY_IMPACT_READY_FOR_REVIEW,
                    entity_type=WebhookEntityType.POLICY_IMPACT,
                    entity_id=update["id"],
                    payload=_ready_for_review_payload(update),
                )
            extracted += 1
            logger.info(
                "create_policy_impacts: succeeded id=%s measures=%d",
                update["id"],
                len(impact_json.get("measures", [])),
            )

        return StageResult()


def _ready_for_review_payload(
    update: PolicyUpdateModel,
) -> PolicyImpactReadyForReviewPayload:
    return {
        "headline": update["headline"],
        "source_label": update["source_label"],
        "reference_number": update["reference_number"],
        "review_url": policy_impact_review_url(update["id"]),
        "source_url": update["source_url"],
    }


def _attempt_exhausted_payload(
    update: PolicyUpdateModel,
    *,
    reason: str,
    attempt_count: int,
) -> AttemptExhaustedPayload:
    return {
        "reason": reason,
        "source_label": update["source_label"],
        "reference_number": update["reference_number"],
        "headline": update["headline"],
        "source_url": update["source_url"],
        "stage": CreatePolicyImpactsStage.name,
        "attempt_count": attempt_count,
    }
