from __future__ import annotations

import logging

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult

logger = logging.getLogger(__name__)


class CollectSourceItemsStage:
    name = "collect_source_items"

    def run(self, context: WorkerContext) -> StageResult:
        logger.info(
            "stage invoked: name=%s run_id=%s",
            self.name,
            context.run_id,
        )
        return StageResult()
