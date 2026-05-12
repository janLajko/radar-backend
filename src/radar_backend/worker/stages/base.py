from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from radar_backend.worker.context import WorkerContext


@dataclass(frozen=True)
class StageResult:
    stage_name: str
    processed_count: int = 0


class RunnableStep(Protocol):
    name: str

    def run(self, context: WorkerContext) -> StageResult:
        ...


class WorkerStage(RunnableStep, Protocol):
    pass

