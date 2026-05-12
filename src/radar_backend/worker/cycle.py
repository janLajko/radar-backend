from __future__ import annotations

import logging
from collections.abc import Sequence

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult, WorkerStage
from radar_backend.worker.stages.collect_source_items import CollectSourceItemsStage
from radar_backend.worker.stages.create_policy_impacts import CreatePolicyImpactsStage
from radar_backend.worker.stages.create_policy_updates import CreatePolicyUpdatesStage
from radar_backend.worker.stages.create_user_actions import CreateUserActionsStage
from radar_backend.worker.stages.dispatch_operational_webhooks import DispatchOperationalWebhooksStage
from radar_backend.worker.stages.send_action_notifications import SendActionNotificationsStage


class PeriodicCycle:
    def __init__(self, stages: Sequence[WorkerStage], logger: logging.Logger | None = None) -> None:
        self._stages = tuple(stages)
        self._logger = logger or logging.getLogger(__name__)

    @property
    def stages(self) -> tuple[WorkerStage, ...]:
        return self._stages

    def run_once(self, context: WorkerContext) -> list[StageResult]:
        results: list[StageResult] = []
        self._logger.info("periodic cycle started")

        for stage in self._stages:
            self._logger.info("stage started: %s", stage.name)
            try:
                result = stage.run(context)
            except Exception:
                self._logger.exception("stage failed: %s", stage.name)
                continue

            results.append(result)
            self._logger.info(
                "stage finished: %s processed_count=%s",
                result.stage_name,
                result.processed_count,
            )

        self._logger.info("periodic cycle finished")
        return results


def build_cycle() -> PeriodicCycle:
    return PeriodicCycle(
        stages=[
            CollectSourceItemsStage(),
            CreatePolicyUpdatesStage(),
            CreatePolicyImpactsStage(),
            CreateUserActionsStage(),
            SendActionNotificationsStage(),
            DispatchOperationalWebhooksStage(),
        ]
    )
