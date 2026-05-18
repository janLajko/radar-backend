from __future__ import annotations

import logging

from radar_backend import config
from radar_backend.llm import build_provider
from radar_backend.llm.policy_filter import PolicyFilterInput, filter_and_generate
from radar_backend.pdf import download_and_parse
from radar_backend.sources.http_client import HttpClient
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult
from radar_backend.db.repositories import policy_updates_repository
from radar_backend.db.repositories import raw_source_items_repository
from radar_backend.db.repositories import webhook_events_repository
from radar_backend.domain import (
    AttemptExhaustedPayload,
    RawSourceItemModel,
    WebhookEntityType,
    WebhookEventType,
)
from radar_backend.db.connection import (
    acquire_connection,
    acquire_connection_with_transaction,
)

logger = logging.getLogger(__name__)


class CreatePolicyUpdatesStage:
    name = "create_policy_updates"

    def run(self, context: WorkerContext) -> StageResult:
        llm = build_provider(
            model=config.policy_update_llm_model(),
        )
        http = HttpClient()
        ingested = 0

        with acquire_connection() as conn:
            items = raw_source_items_repository.fetch_pending_for_policy_update(conn)

        logger.info("create_policy_updates: found %d items to process", len(items))

        for item in items:
            new_count = item["policy_update_attempt_count"] + 1

            # --- Step 1: PDF download (outside transaction) ---
            attachment_text = ""
            if item["pdf_urls"]:
                try:
                    attachment_text = download_and_parse(item["pdf_urls"], http)
                except Exception as exc:
                    logger.warning(
                        "create_policy_updates: pdf failed item_id=%s: %s",
                        item["id"],
                        exc,
                    )
                    with acquire_connection_with_transaction() as conn:
                        raw_source_items_repository.set_policy_update_status(
                            conn, item["id"], "failed", new_count
                        )
                        if new_count >= 3:
                            webhook_events_repository.create_webhook_event(
                                conn,
                                event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                                entity_type=WebhookEntityType.POLICY_UPDATE,
                                entity_id=item["id"],
                                payload=_attempt_exhausted_payload(
                                    item,
                                    reason="policy_update_pdf_failed",
                                    attempt_count=new_count,
                                ),
                            )
                    continue

            # --- Step 2: LLM filter + generate (outside transaction) ---
            try:
                draft = filter_and_generate(
                    llm,
                    PolicyFilterInput(
                        source_key=item["source_key"],
                        source_label=item["source_label"],
                        source_title=item["source_title"],
                        source_content=item["source_content"],
                        attachment_text=attachment_text,
                        reference_number=item["source_metadata"].get("citation"),
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "create_policy_updates: llm failed item_id=%s: %s", item["id"], exc
                )
                with acquire_connection_with_transaction() as conn:
                    raw_source_items_repository.set_policy_update_status(
                        conn, item["id"], "failed", new_count
                    )
                    if new_count >= 3:
                        webhook_events_repository.create_webhook_event(
                            conn,
                            event_type=WebhookEventType.ATTEMPT_EXHAUSTED,
                            entity_type=WebhookEntityType.POLICY_UPDATE,
                            entity_id=item["id"],
                            payload=_attempt_exhausted_payload(
                                item,
                                reason="policy_update_llm_failed",
                                attempt_count=new_count,
                            ),
                        )
                continue

            # --- Step 3: persist result ---
            if not draft.should_ingest:
                with acquire_connection_with_transaction() as conn:
                    raw_source_items_repository.set_policy_update_status(
                        conn, item["id"], "discarded", new_count
                    )
                logger.info(
                    "create_policy_updates: discarded item_id=%s reason=%s",
                    item["id"],
                    draft.discard_reason,
                )
            else:
                with acquire_connection_with_transaction() as conn:
                    policy_updates_repository.create_policy_updates(conn, item, draft)
                    raw_source_items_repository.set_policy_update_status(
                        conn, item["id"], "ingested", new_count
                    )
                ingested += 1
                logger.info(
                    "create_policy_updates: ingested item_id=%s headline=%s",
                    item["id"],
                    draft.headline,
                )

        return StageResult()


def _attempt_exhausted_payload(
    item: RawSourceItemModel,
    *,
    reason: str,
    attempt_count: int,
) -> AttemptExhaustedPayload:
    reference_number = item["source_metadata"].get("citation")
    webhook_payload: AttemptExhaustedPayload = {
        "reason": reason,
        "source_label": item["source_label"],
        "headline": item["source_title"],
        "stage": CreatePolicyUpdatesStage.name,
        "attempt_count": attempt_count,
    }
    if isinstance(reference_number, str) or reference_number is None:
        webhook_payload["reference_number"] = reference_number

    return webhook_payload
