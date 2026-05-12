from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult


class SendActionNotificationsStage:
    name = "send_action_notifications"

    def run(self, context: WorkerContext) -> StageResult:
        context.logger.info("stage skeleton has no action notification implementation yet")
        return StageResult(stage_name=self.name)

