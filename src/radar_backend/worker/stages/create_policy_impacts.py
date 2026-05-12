from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult


class CreatePolicyImpactsStage:
    name = "create_policy_impacts"

    def run(self, context: WorkerContext) -> StageResult:
        context.logger.info("stage skeleton has no policy impact implementation yet")
        return StageResult(stage_name=self.name)

