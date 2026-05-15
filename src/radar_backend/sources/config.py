from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceConfig:
    source_key: str
    source_label: str
    adapter: str
    enabled: bool
    fetch: dict


def load_source_configs(path: Path) -> list[SourceConfig]:
    data = yaml.safe_load(path.read_text())
    return [
        SourceConfig(
            source_key=item["source_key"],
            source_label=item["source_label"],
            adapter=item["adapter"],
            enabled=bool(item.get("enabled", True)),
            fetch=dict(item.get("fetch") or {}),
        )
        for item in data["sources"]
    ]
