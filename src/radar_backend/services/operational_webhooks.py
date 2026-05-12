from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult


class OperationalWebhookService:
    name = "send_operational_webhooks"

    def run(self, context: WorkerContext) -> StageResult:
        context.logger.info("service skeleton has no operational webhook implementation yet")
        return StageResult(stage_name=self.name)

