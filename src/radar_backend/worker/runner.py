from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from threading import Event
from uuid import uuid4

from radar_backend import config, db
from radar_backend.logging_config import configure_logging
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.cycle import PeriodicCycle, build_cycle

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config.load_dotenv(Path(".env"))

    configure_logging(config.log_level())

    try:
        db.open_pool()
        cycle = build_cycle()

        if args.once:
            cycle.run_once(_new_context())
            return 0

        _run_loop(cycle, config.worker_poll_interval_seconds())
        return 0
    finally:
        db.close_pool()


def _run_loop(cycle: PeriodicCycle, poll_interval_seconds: int) -> None:
    stop_event = Event()

    def request_stop(signum, _frame) -> None:
        logger.info("received signal %s; stopping worker", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    while not stop_event.is_set():
        try:
            cycle.run_once(_new_context())
        except Exception:
            logger.exception("periodic cycle failed")

        stop_event.wait(poll_interval_seconds)


def _new_context() -> WorkerContext:
    return WorkerContext(run_id=uuid4().hex)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Compliance Radar worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one periodic cycle and exit",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
