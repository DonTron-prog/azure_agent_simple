"""SQLite access for the bounded analysis tools.

One read-only connection to `warehouse.db`. All tools funnel queries through
`run_query`, which enforces a hard row cap and a query timeout. This is the
only place raw SQL runs — the agent never writes SQL, it only fills parameters
in the tool signatures defined in `tools.py`.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROW_CAP = 10_000
QUERY_TIMEOUT_SECONDS = 5.0


class Warehouse:
    def __init__(self, db_path: Path) -> None:
        if not db_path.exists():
            raise RuntimeError(f"warehouse not found at {db_path} — run `python scripts/ingest.py` first")
        # `check_same_thread=False` so we can set a timeout interrupt from a watchdog thread.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Read-only pragmas. SQLite doesn't enforce read-only at connection time
        # without URI mode; these pragmas plus the fact that no tool ever issues
        # DML are the guardrail.
        self._conn.execute("PRAGMA query_only = ON")
        self._lock = threading.Lock()

    def run_query(
        self, sql: str, params: dict[str, Any] | tuple[Any, ...] | None = None
    ) -> tuple[list[dict[str, Any]], bool]:
        """Returns (rows, truncated). Truncated is True if the result exceeded ROW_CAP."""
        params = params if params is not None else {}
        with self._lock:
            watchdog = threading.Timer(QUERY_TIMEOUT_SECONDS, self._conn.interrupt)
            watchdog.start()
            try:
                cur = self._conn.execute(sql, params)
                rows = cur.fetchmany(ROW_CAP + 1)
            finally:
                watchdog.cancel()
        truncated = len(rows) > ROW_CAP
        return [dict(r) for r in rows[:ROW_CAP]], truncated

    def reference_date(self) -> str:
        cur = self._conn.execute("SELECT MAX(invoice_date) AS d FROM sales_v")
        row = cur.fetchone()
        return row["d"] if row and row["d"] else ""

    def distinct_values(self, dimension: str, limit: int = 100) -> list[str]:
        col = _DIMENSION_COLUMNS[dimension]
        cur = self._conn.execute(
            f"SELECT DISTINCT {col} AS v FROM sales_v WHERE {col} IS NOT NULL ORDER BY {col} LIMIT ?",
            (limit,),
        )
        return [r["v"] for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


# Public dimension names (what the agent sees) → actual column names in sales_v.
_DIMENSION_COLUMNS: dict[str, str] = {
    "country": "country",
    "product": "description",
    "customer": "customer_id",
}

# Public metric names → SQL expression.
METRIC_EXPRESSIONS: dict[str, str] = {
    "revenue": "SUM(quantity * price)",
    "units": "SUM(quantity)",
    "orders": "COUNT(DISTINCT invoice)",
}


def dimension_column(dimension: str) -> str:
    if dimension not in _DIMENSION_COLUMNS:
        raise ValueError(f"unknown dimension: {dimension}. must be one of {list(_DIMENSION_COLUMNS)}")
    return _DIMENSION_COLUMNS[dimension]


def metric_expression(metric: str) -> str:
    if metric not in METRIC_EXPRESSIONS:
        raise ValueError(f"unknown metric: {metric}. must be one of {list(METRIC_EXPRESSIONS)}")
    return METRIC_EXPRESSIONS[metric]


@contextmanager
def warehouse(db_path: Path):
    wh = Warehouse(db_path)
    try:
        yield wh
    finally:
        wh.close()
