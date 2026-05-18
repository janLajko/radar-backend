from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

from radar_backend import config
from radar_backend.sources.base import RawSourceItemCandidate
from radar_backend.sources.config import SourceConfig, load_source_configs
from radar_backend.sources.executive_order import ExecutiveOrderAdapter
from radar_backend.sources.federal_register import FederalRegisterNoticeAdapter
from radar_backend.sources.http_client import HttpClient
from radar_backend.sources.hts_archive import HTSArchiveAdapter
from radar_backend.sources.proclamation import ProclamationAdapter
from radar_backend.worker.context import WorkerContext
from radar_backend.worker.stages.base import StageResult
from radar_backend.db.repositories import raw_source_items_repository
from radar_backend.db.connection import (
    acquire_connection,
    acquire_connection_with_transaction,
)

_ADAPTER_REGISTRY = {
    "proclamation": ProclamationAdapter(),
    "executive_order": ExecutiveOrderAdapter(),
    "federal_register": FederalRegisterNoticeAdapter(),
    "hts_archive": HTSArchiveAdapter(),
}

logger = logging.getLogger(__name__)


class CollectSourceItemsStage:
    name = "collect_source_items"

    def run(self, context: WorkerContext) -> StageResult:
        configs = load_source_configs(Path(config.source_config_path()))
        enabled = [c for c in configs if c.enabled]

        if not enabled:
            logger.info("collect_source_items: no enabled sources configured")
            return StageResult()

        http = HttpClient()

        # --- Stage 1a: fetch all sources in parallel ---
        per_source: dict[str, list[RawSourceItemCandidate]] = {}

        with ThreadPoolExecutor(max_workers=len(enabled)) as executor:
            future_to_cfg = {
                executor.submit(_run_adapter, cfg, http, logger): cfg
                for cfg in enabled
            }
            done, _ = wait(future_to_cfg)

        for future in done:
            cfg = future_to_cfg[future]
            exc = future.exception()
            if exc is not None:
                logger.error(
                    "collect_source_items: adapter %s failed: %s",
                    cfg.source_key,
                    exc,
                )
            else:
                per_source[cfg.source_key] = future.result()

        # --- Stage 1b: write new items to DB (all adapters must finish first) ---
        inserted_total = 0

        with acquire_connection_with_transaction() as conn:
            for cfg in enabled:
                candidates = per_source.get(cfg.source_key, [])
                inserted = 0
                skipped = 0
                for candidate in candidates:
                    if raw_source_items_repository.insert_if_not_exists(
                        conn, candidate, cfg.source_key, cfg.source_label
                    ):
                        inserted += 1
                    else:
                        skipped += 1
                logger.info(
                    "collect_source_items: source=%s inserted=%d skipped=%d",
                    cfg.source_key,
                    inserted,
                    skipped,
                )
                inserted_total += inserted

        return StageResult()


def _run_adapter(
    cfg: SourceConfig,
    http: HttpClient,
    logger: logging.Logger,
) -> list[RawSourceItemCandidate]:
    adapter = _ADAPTER_REGISTRY.get(cfg.adapter)
    if adapter is None:
        raise ValueError(f"unknown adapter: {cfg.adapter!r}")
    logger.info("collect_source_items: fetching source=%s", cfg.source_key)
    return adapter.fetch(cfg.fetch, http)
