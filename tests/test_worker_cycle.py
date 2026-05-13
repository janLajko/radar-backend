from __future__ import annotations

from radar_backend.worker.context import WorkerContext
from radar_backend.worker.cycle import PeriodicCycle
from radar_backend.worker.stages.base import StageResult


class RecordingStage:
    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self._calls = calls
        self._fail = fail

    def run(self, _context: WorkerContext) -> StageResult:
        self._calls.append(self.name)
        if self._fail:
            raise RuntimeError(self.name)
        return StageResult()


def test_periodic_cycle_runs_stages_in_order() -> None:
    calls: list[str] = []
    cycle = PeriodicCycle(
        stages=[
            RecordingStage("first", calls),
            RecordingStage("second", calls),
        ]
    )

    cycle.run_once(WorkerContext(run_id="test-run"))

    assert calls == ["first", "second"]


def test_periodic_cycle_continues_after_stage_exception() -> None:
    calls: list[str] = []
    cycle = PeriodicCycle(
        stages=[
            RecordingStage("first", calls),
            RecordingStage("second", calls, fail=True),
            RecordingStage("third", calls),
        ]
    )

    cycle.run_once(WorkerContext(run_id="test-run"))

    assert calls == ["first", "second", "third"]
