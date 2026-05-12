from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult


class CreateUserActionsStage:
    name = "create_user_actions"

    def run(self, context: WorkerContext) -> StageResult:
        context.logger.info("stage skeleton has no user action implementation yet")
        return StageResult(stage_name=self.name)

