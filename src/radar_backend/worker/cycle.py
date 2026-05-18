from __future__ import annotations

import logging
from collections.abc import Sequence
from time import perf_counter

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import WorkerStage
from radar_backend.worker.stages.collect_source_items import CollectSourceItemsStage
from radar_backend.worker.stages.create_policy_impacts import CreatePolicyImpactsStage
from radar_backend.worker.stages.create_policy_updates import CreatePolicyUpdatesStage
from radar_backend.worker.stages.create_user_actions import CreateUserActionsStage
from radar_backend.worker.stages.send_action_notifications import SendActionNotificationsStage
from radar_backend.worker.stages.send_operational_webhooks import SendOperationalWebhooksStage

logger = logging.getLogger(__name__)


class PeriodicCycle:
    def __init__(self, stages: Sequence[WorkerStage]) -> None:
        self._stages = tuple(stages)

    def run_once(self, context: WorkerContext) -> None:
        cycle_started_at = perf_counter()
        logger.info("periodic cycle started: run_id=%s", context.run_id)

        for stage in self._stages:
            stage_started_at = perf_counter()
            logger.info("stage started: %s run_id=%s", stage.name, context.run_id)
            try:
                stage.run(context)
            except Exception:
                logger.exception(
                    "stage failed: %s run_id=%s duration_seconds=%.3f",
                    stage.name,
                    context.run_id,
                    perf_counter() - stage_started_at,
                )
            else:
                logger.info(
                    "stage finished: %s run_id=%s duration_seconds=%.3f",
                    stage.name,
                    context.run_id,
                    perf_counter() - stage_started_at,
                )

        logger.info(
            "periodic cycle finished: run_id=%s duration_seconds=%.3f",
            context.run_id,
            perf_counter() - cycle_started_at,
        )


def build_cycle() -> PeriodicCycle:
    return PeriodicCycle(
        stages=[
            CollectSourceItemsStage(),
            CreatePolicyUpdatesStage(),
            CreatePolicyImpactsStage(),
            CreateUserActionsStage(),
            SendActionNotificationsStage(),
            SendOperationalWebhooksStage(),
        ]
    )
