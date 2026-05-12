from __future__ import annotations

import logging
from dataclasses import dataclass

from radar_backend.worker.cycle import PeriodicCycle, build_cycle
from radar_backend.worker.stages.base import StageResult


@dataclass
class FakeContext:
    logger: logging.Logger


class RecordingStage:
    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self._calls = calls
        self._fail = fail

    def run(self, _context) -> StageResult:
        self._calls.append(self.name)
        if self._fail:
            raise RuntimeError(self.name)
        return StageResult(stage_name=self.name, processed_count=1)


def test_periodic_cycle_runs_stages_in_order() -> None:
    calls: list[str] = []
    cycle = PeriodicCycle(
        stages=[
            RecordingStage("first", calls),
            RecordingStage("second", calls),
        ]
    )

    results = cycle.run_once(FakeContext(logger=logging.getLogger(__name__)))

    assert calls == ["first", "second"]
    assert [result.stage_name for result in results] == ["first", "second"]


def test_periodic_cycle_continues_after_stage_exception() -> None:
    calls: list[str] = []
    cycle = PeriodicCycle(
        stages=[
            RecordingStage("first", calls),
            RecordingStage("second", calls, fail=True),
            RecordingStage("third", calls),
        ]
    )

    results = cycle.run_once(FakeContext(logger=logging.getLogger(__name__)))

    assert calls == ["first", "second", "third"]
    assert [result.stage_name for result in results] == ["first", "third"]


def test_default_cycle_stage_order() -> None:
    cycle = build_cycle()

    assert [stage.name for stage in cycle.stages] == [
        "collect_source_items",
        "create_policy_updates",
        "create_policy_impacts",
        "create_user_actions",
        "send_action_notifications",
        "dispatch_operational_webhooks",
    ]
