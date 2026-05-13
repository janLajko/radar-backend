from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from radar_backend.worker.context import WorkerContext

StageResultStatus = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class StageResult:
    stage_name: str
    status: StageResultStatus = "succeeded"
    processed_count: int = 0
    error_message: str | None = None


class WorkerStage(Protocol):
    name: str

    def run(self, context: WorkerContext) -> StageResult:
        ...
