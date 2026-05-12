from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from radar_backend.worker.cycle import PeriodicCycle, build_cycle
from radar_backend.worker.stages.base import StageResult


@dataclass
class FakeContext:
    logger: logging.Logger


class RecordingStep:
    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self._calls = calls
        self._fail = fail

    def run(self, _context) -> StageResult:
        self._calls.append(self.name)
        if self._fail:
            raise RuntimeError(self.name)
        return StageResult(stage_name=self.name, processed_count=1)


def test_periodic_cycle_runs_steps_in_order() -> None:
    calls: list[str] = []
    cycle = PeriodicCycle(
        steps=[
            RecordingStep("first", calls),
            RecordingStep("second", calls),
        ]
    )

    results = cycle.run_once(FakeContext(logger=logging.getLogger(__name__)))

    assert calls == ["first", "second"]
    assert [result.stage_name for result in results] == ["first", "second"]


def test_periodic_cycle_stops_on_step_exception() -> None:
    calls: list[str] = []
    cycle = PeriodicCycle(
        steps=[
            RecordingStep("first", calls),
            RecordingStep("second", calls, fail=True),
            RecordingStep("third", calls),
        ]
    )

    with pytest.raises(RuntimeError, match="second"):
        cycle.run_once(FakeContext(logger=logging.getLogger(__name__)))

    assert calls == ["first", "second"]


def test_default_cycle_step_order() -> None:
    cycle = build_cycle()

    assert [step.name for step in cycle.steps] == [
        "collect_source_items",
        "create_policy_updates",
        "create_policy_impacts",
        "send_operational_webhooks",
        "create_user_actions",
        "send_action_notifications",
    ]
