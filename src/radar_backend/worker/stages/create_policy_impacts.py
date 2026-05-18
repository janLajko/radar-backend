from __future__ import annotations

import logging

from radar_backend.llm import build_provider
from radar_backend.llm.policy_impact_extractor import PolicyImpactInput, extract_policy_impact
from radar_backend.pdf import download_and_parse
from radar_backend.sources.http_client import HttpClient
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult

logger = logging.getLogger(__name__)


class CreatePolicyImpactsStage:
    name = "create_policy_impacts"

    def run(self, context: WorkerContext) -> StageResult:
        repo = context.repositories
        http = HttpClient()
        llm = build_provider(
            context.settings,
            model=context.settings.policy_impact_llm_model,
        )
        extracted = 0

        with context.db.connection() as conn:
            updates = repo.policy_updates.fetch_pending_for_policy_impact(conn)

        logger.info("create_policy_impacts: found %d updates to process", len(updates))

        for update in updates:
            new_count = update.policy_extract_attempt_count + 1

            # --- Step 1: Download attachments (outside transaction) ---
            attachment_text = ""
            if update.pdf_urls:
                try:
                    attachment_text = download_and_parse(update.pdf_urls, http)
                except Exception as exc:
                    logger.warning(
                        "create_policy_impacts: pdf failed id=%s: %s", update.id, exc
                    )
                    with context.db.transaction() as conn:
                        repo.policy_updates.set_policy_extract_status(
                            conn, update.id, "failed", new_count
                        )
                        if new_count >= 3:
                            repo.webhook_events.upsert_attempt_exhausted(
                                conn, "policy_update", update.id
                            )
                    continue

            # --- Step 2: Run agent loop (outside transaction) ---
            try:
                impact_json = extract_policy_impact(
                    llm,
                    PolicyImpactInput(
                        policy_update_id=update.id,
                        source_key=update.source_key,
                        source_title=update.source_title,
                        source_content=update.source_content,
                        briefing=update.briefing,
                        attachment_text=attachment_text,
                        source_url=update.source_url,
                    ),
                )
                logger.info("impact_json:%s", impact_json)
            except Exception as exc:
                logger.warning(
                    "create_policy_impacts: agent failed id=%s: %s", update.id, exc
                )
                with context.db.transaction() as conn:
                    repo.policy_updates.set_policy_extract_status(
                        conn, update.id, "failed", new_count
                    )
                    if new_count >= 3:
                        repo.webhook_events.upsert_attempt_exhausted(
                            conn, "policy_update", update.id
                        )
                continue

            # --- Step 3: Persist result ---
            with context.db.transaction() as conn:
                repo.policy_updates.set_policy_extract_status(
                    conn, update.id, "succeeded", new_count, impact_json=impact_json
                )
                repo.webhook_events.upsert_policy_impact_ready(conn, update.id)
            extracted += 1
            logger.info(
                "create_policy_impacts: succeeded id=%s measures=%d",
                update.id,
                len(impact_json.get("measures", [])),
            )

        return StageResult(stage_name=self.name, processed_count=extracted)
