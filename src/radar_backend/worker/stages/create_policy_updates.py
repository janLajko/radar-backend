from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult


class CreatePolicyUpdatesStage:
    name = "create_policy_updates"

    def run(self, context: WorkerContext) -> StageResult:
        context.logger.info("stage skeleton has no policy update implementation yet")
        return StageResult(stage_name=self.name)

