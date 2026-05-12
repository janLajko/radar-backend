from __future__ import annotations

import logging
from collections.abc import Sequence

from radar_backend.services.operational_webhooks import OperationalWebhookService
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import RunnableStep, StageResult
from radar_backend.worker.stages.collect_source_items import CollectSourceItemsStage
from radar_backend.worker.stages.create_policy_impacts import CreatePolicyImpactsStage
from radar_backend.worker.stages.create_policy_updates import CreatePolicyUpdatesStage
from radar_backend.worker.stages.create_user_actions import CreateUserActionsStage
from radar_backend.worker.stages.send_action_notifications import SendActionNotificationsStage


class PeriodicCycle:
    def __init__(self, steps: Sequence[RunnableStep], logger: logging.Logger | None = None) -> None:
        self._steps = tuple(steps)
        self._logger = logger or logging.getLogger(__name__)

    @property
    def steps(self) -> tuple[RunnableStep, ...]:
        return self._steps

    def run_once(self, context: WorkerContext) -> list[StageResult]:
        results: list[StageResult] = []
        self._logger.info("periodic cycle started")

        for step in self._steps:
            self._logger.info("step started: %s", step.name)
            try:
                result = step.run(context)
            except Exception:
                self._logger.exception("step failed: %s", step.name)
                raise

            results.append(result)
            self._logger.info(
                "step finished: %s processed_count=%s",
                result.stage_name,
                result.processed_count,
            )

        self._logger.info("periodic cycle finished")
        return results


def build_cycle() -> PeriodicCycle:
    return PeriodicCycle(
        steps=[
            CollectSourceItemsStage(),
            CreatePolicyUpdatesStage(),
            CreatePolicyImpactsStage(),
            OperationalWebhookService(),
            CreateUserActionsStage(),
            SendActionNotificationsStage(),
        ]
    )
