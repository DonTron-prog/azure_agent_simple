"""Runtime config, pulled from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Config:
    openai_endpoint: str
    chat_deployment: str
    warehouse_db: Path
    runs_dir: Path
    reference_date: str | None


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config(
        openai_endpoint=_req("OPENAI_ENDPOINT"),
        chat_deployment=os.environ.get("CHAT_DEPLOYMENT", "gpt-4o"),
        warehouse_db=Path(os.environ.get("WAREHOUSE_DB", "data/warehouse.db")),
        runs_dir=Path(os.environ.get("RUNS_DIR", "data/runs")),
        reference_date=os.environ.get("REFERENCE_DATE") or None,
    )


def _req(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"missing required env var: {name}")
    return v
