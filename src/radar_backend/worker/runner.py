from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from threading import Event

from radar_backend.config import Settings, load_dotenv
from radar_backend.db import Database
from radar_backend.db.repositories import Repositories
from radar_backend.logging_config import configure_logging
from radar_backend.services import Services
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.cycle import PeriodicCycle, build_cycle


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv(args.env_file)

    settings = Settings.from_env()
    settings.validate()
    configure_logging(settings.log_level)

    logger = logging.getLogger("radar_backend.worker")
    db = Database(settings)

    try:
        db.open()
        context = WorkerContext(
            settings=settings,
            db=db,
            repositories=Repositories.create(),
            services=Services.create(),
            logger=logger,
        )
        cycle = build_cycle()

        if args.once:
            cycle.run_once(context)
            return 0

        _run_loop(context, cycle)
        return 0
    finally:
        db.close()


def _run_loop(context: WorkerContext, cycle: PeriodicCycle) -> None:
    stop_event = Event()

    def request_stop(signum, _frame) -> None:
        context.logger.info("received signal %s; stopping after current wait", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    while not stop_event.is_set():
        try:
            cycle.run_once(context)
        except Exception:
            context.logger.exception("periodic cycle failed")

        stop_event.wait(context.settings.worker_poll_interval_seconds)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Compliance Radar worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one periodic cycle and exit",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="path to the .env file",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
