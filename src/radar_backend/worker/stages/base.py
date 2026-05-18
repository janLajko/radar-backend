from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from radar_backend.worker.context import WorkerContext


@dataclass(frozen=True)
class StageResult:
    pass


class WorkerStage(Protocol):
    name: str

    def run(self, context: WorkerContext) -> StageResult:
        ...
