from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult


class DispatchOperationalWebhooksStage:
    name = "dispatch_operational_webhooks"

    def run(self, context: WorkerContext) -> StageResult:
        context.logger.info("stage skeleton has no operational webhook dispatch implementation yet")
        return StageResult(stage_name=self.name)
