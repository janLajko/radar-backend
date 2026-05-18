from __future__ import annotations

import logging

from radar_backend.llm import build_provider
from radar_backend.llm.policy_filter import PolicyFilterInput, filter_and_generate
from radar_backend.pdf import download_and_parse
from radar_backend.sources.http_client import HttpClient
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult

logger = logging.getLogger(__name__)


class CreatePolicyUpdatesStage:
    name = "create_policy_updates"

    def run(self, context: WorkerContext) -> StageResult:
        repo = context.repositories
        llm = build_provider(
            context.settings,
            model=context.settings.policy_update_llm_model,
        )
        http = HttpClient()
        ingested = 0

        with context.db.connection() as conn:
            items = repo.raw_source_items.fetch_pending_for_policy_update(conn)

        logger.info("create_policy_updates: found %d items to process", len(items))

        for item in items:
            new_count = item.policy_update_attempt_count + 1

            # --- Step 1: PDF download (outside transaction) ---
            attachment_text = ""
            if item.pdf_urls:
                try:
                    attachment_text = download_and_parse(item.pdf_urls, http)
                except Exception as exc:
                    logger.warning(
                        "create_policy_updates: pdf failed item_id=%s: %s", item.id, exc
                    )
                    with context.db.transaction() as conn:
                        repo.raw_source_items.set_policy_update_status(
                            conn, item.id, "failed", new_count
                        )
                        if new_count >= 3:
                            repo.webhook_events.upsert_attempt_exhausted(
                                conn, "raw_policy_update", item.id
                            )
                    continue

            # --- Step 2: LLM filter + generate (outside transaction) ---
            try:
                draft = filter_and_generate(
                    llm,
                    PolicyFilterInput(
                        source_key=item.source_key,
                        source_label=item.source_label,
                        source_title=item.source_title,
                        source_content=item.source_content,
                        attachment_text=attachment_text,
                        reference_number=item.source_metadata.get("citation"),
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "create_policy_updates: llm failed item_id=%s: %s", item.id, exc
                )
                with context.db.transaction() as conn:
                    repo.raw_source_items.set_policy_update_status(
                        conn, item.id, "failed", new_count
                    )
                    if new_count >= 3:
                        repo.webhook_events.upsert_attempt_exhausted(
                            conn, "raw_policy_update", item.id
                        )
                continue

            # --- Step 3: persist result ---
            if not draft.should_ingest:
                with context.db.transaction() as conn:
                    repo.raw_source_items.set_policy_update_status(
                        conn, item.id, "discarded", new_count
                    )
                logger.info(
                    "create_policy_updates: discarded item_id=%s reason=%s",
                    item.id,
                    draft.discard_reason,
                )
            else:
                with context.db.transaction() as conn:
                    repo.policy_updates.insert(conn, item, draft)
                    repo.raw_source_items.set_policy_update_status(
                        conn, item.id, "ingested", new_count
                    )
                ingested += 1
                logger.info(
                    "create_policy_updates: ingested item_id=%s headline=%s",
                    item.id,
                    draft.headline,
                )

        return StageResult(stage_name=self.name, processed_count=ingested)
