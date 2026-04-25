"""The six bounded analysis tools the Investigator agent can call.

Each tool:
  - validates its arguments against a small allow-list of metrics/dimensions,
  - resolves a `period` (named or {start, end}) into an ISO date range,
  - runs one parameterised SQL query via `db.run_query`,
  - returns a dict with `rows`, `sql`, `params`, `row_count`, `truncated`.

The agent never writes SQL. It only fills these tool arguments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from db import METRIC_EXPRESSIONS, Warehouse, dimension_column, metric_expression

_METRICS = list(METRIC_EXPRESSIONS.keys())
_DIMENSIONS = ["country", "product", "customer"]
_GRAINS = ["day", "week", "month"]
_NAMED_PERIODS = [
    "current_month",
    "prior_month",
    "same_month_last_year",
    "current_quarter",
    "prior_quarter",
    "trailing_90d",
    "prior_90d",
    "trailing_30d",
    "prior_30d",
]


@dataclass
class AnalysisTools:
    warehouse: Warehouse
    reference_date: str  # ISO date, anchor for all named periods

    # ---- public tools ----

    def metric_overview(self, metrics: list[str], current_period: dict, comparison_period: dict) -> dict[str, Any]:
        current = _resolve_period(current_period, self.reference_date)
        prior = _resolve_period(comparison_period, self.reference_date)
        out_rows: list[dict[str, Any]] = []
        all_sql: list[str] = []
        for m in metrics:
            expr = metric_expression(m)
            sql = (
                f"SELECT "
                f"  (SELECT {expr} FROM sales_v WHERE invoice_date BETWEEN :cs AND :ce) AS current_value, "
                f"  (SELECT {expr} FROM sales_v WHERE invoice_date BETWEEN :ps AND :pe) AS prior_value"
            )
            params = {"cs": current[0], "ce": current[1], "ps": prior[0], "pe": prior[1]}
            rows, _ = self.warehouse.run_query(sql, params)
            r = rows[0] if rows else {"current_value": None, "prior_value": None}
            cur_v = r.get("current_value") or 0
            prior_v = r.get("prior_value") or 0
            out_rows.append(
                {
                    "metric": m,
                    "current_value": cur_v,
                    "prior_value": prior_v,
                    "abs_change": cur_v - prior_v,
                    "pct_change": _pct(cur_v, prior_v),
                }
            )
            all_sql.append(sql)
        return _result(
            rows=out_rows,
            sql="; ".join(all_sql),
            params={"current": current, "comparison": prior},
        )

    def period_comparison(
        self,
        metric: str,
        period_a: dict,
        period_b: dict,
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        expr = metric_expression(metric)
        a = _resolve_period(period_a, self.reference_date)
        b = _resolve_period(period_b, self.reference_date)
        where_f, params_f = _filter_clause(filters)
        sql = (
            f"SELECT "
            f"  (SELECT {expr} FROM sales_v WHERE invoice_date BETWEEN :as AND :ae {where_f}) AS value_a, "
            f"  (SELECT {expr} FROM sales_v WHERE invoice_date BETWEEN :bs AND :be {where_f}) AS value_b"
        )
        params = {"as": a[0], "ae": a[1], "bs": b[0], "be": b[1], **params_f}
        rows, _ = self.warehouse.run_query(sql, params)
        r = rows[0] if rows else {"value_a": None, "value_b": None}
        va = r.get("value_a") or 0
        vb = r.get("value_b") or 0
        return _result(
            rows=[
                {
                    "metric": metric,
                    "period_a": {"start": a[0], "end": a[1], "value": va},
                    "period_b": {"start": b[0], "end": b[1], "value": vb},
                    "abs_change": va - vb,
                    "pct_change": _pct(va, vb),
                    "filters": filters or {},
                }
            ],
            sql=sql,
            params=params,
        )

    def dimension_decomposition(
        self,
        metric: str,
        dimension: str,
        period: dict,
        filters: dict[str, str] | None = None,
        top_n: int = 10,
    ) -> dict[str, Any]:
        expr = metric_expression(metric)
        col = dimension_column(dimension)
        p = _resolve_period(period, self.reference_date)
        where_f, params_f = _filter_clause(filters)
        top_n = max(1, min(int(top_n), 50))
        sql = (
            f"WITH t AS ("
            f"  SELECT {col} AS dim_value, {expr} AS value "
            f"  FROM sales_v "
            f"  WHERE invoice_date BETWEEN :s AND :e {where_f} "
            f"  GROUP BY {col}"
            f") "
            f"SELECT dim_value, value, "
            f"  value * 1.0 / NULLIF((SELECT SUM(value) FROM t), 0) AS share "
            f"FROM t ORDER BY value DESC LIMIT :top_n"
        )
        params = {"s": p[0], "e": p[1], "top_n": top_n, **params_f}
        rows, _ = self.warehouse.run_query(sql, params)
        return _result(
            rows=rows,
            sql=sql,
            params={"metric": metric, "dimension": dimension, "period": {"start": p[0], "end": p[1]}, **params_f, "top_n": top_n},
        )

    def time_series(
        self,
        metric: str,
        grain: str,
        start: str,
        end: str,
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if grain not in _GRAINS:
            raise ValueError(f"grain must be one of {_GRAINS}")
        expr = metric_expression(metric)
        bucket_expr = {
            "day": "invoice_date",
            "week": "strftime('%Y-W%W', invoice_date)",
            "month": "invoice_month",
        }[grain]
        where_f, params_f = _filter_clause(filters)
        s, e = _validate_date(start), _validate_date(end)
        sql = (
            f"SELECT {bucket_expr} AS bucket, {expr} AS value "
            f"FROM sales_v "
            f"WHERE invoice_date BETWEEN :s AND :e {where_f} "
            f"GROUP BY bucket ORDER BY bucket"
        )
        params = {"s": s, "e": e, **params_f}
        rows, truncated = self.warehouse.run_query(sql, params)
        return _result(
            rows=rows,
            sql=sql,
            params={"metric": metric, "grain": grain, "start": s, "end": e, **params_f},
            truncated=truncated,
        )

    def top_contributors(
        self,
        metric: str,
        dimension: str,
        period_a: dict,
        period_b: dict,
        filters: dict[str, str] | None = None,
        top_n: int = 5,
    ) -> dict[str, Any]:
        expr = metric_expression(metric)
        col = dimension_column(dimension)
        a = _resolve_period(period_a, self.reference_date)
        b = _resolve_period(period_b, self.reference_date)
        where_f, params_f = _filter_clause(filters)
        top_n = max(1, min(int(top_n), 20))
        # SQLite lacks FULL OUTER JOIN — emulate with UNION of two LEFT JOINs.
        sql = (
            f"WITH a AS ("
            f"  SELECT {col} AS dim_value, {expr} AS value_a FROM sales_v "
            f"  WHERE invoice_date BETWEEN :as AND :ae {where_f} GROUP BY {col}"
            f"), b AS ("
            f"  SELECT {col} AS dim_value, {expr} AS value_b FROM sales_v "
            f"  WHERE invoice_date BETWEEN :bs AND :be {where_f} GROUP BY {col}"
            f"), merged AS ("
            f"  SELECT a.dim_value AS dim_value, a.value_a AS value_a, b.value_b AS value_b "
            f"  FROM a LEFT JOIN b ON a.dim_value = b.dim_value "
            f"  UNION "
            f"  SELECT b.dim_value AS dim_value, a.value_a AS value_a, b.value_b AS value_b "
            f"  FROM b LEFT JOIN a ON a.dim_value = b.dim_value "
            f") "
            f"SELECT dim_value, "
            f"  COALESCE(value_a, 0) AS value_a, "
            f"  COALESCE(value_b, 0) AS value_b, "
            f"  COALESCE(value_a, 0) - COALESCE(value_b, 0) AS contribution "
            f"FROM merged "
            f"ORDER BY ABS(contribution) DESC LIMIT :top_n"
        )
        params = {"as": a[0], "ae": a[1], "bs": b[0], "be": b[1], "top_n": top_n, **params_f}
        rows, _ = self.warehouse.run_query(sql, params)
        return _result(
            rows=rows,
            sql=sql,
            params={
                "metric": metric,
                "dimension": dimension,
                "period_a": {"start": a[0], "end": a[1]},
                "period_b": {"start": b[0], "end": b[1]},
                **params_f,
                "top_n": top_n,
            },
        )

    def data_sufficiency_check(
        self,
        metric: str,
        period: dict,
        dimension: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # Validate metric name (raises if unknown) even though we don't use the expression.
        metric_expression(metric)
        p = _resolve_period(period, self.reference_date)
        where_f, params_f = _filter_clause(filters)
        params = {"s": p[0], "e": p[1], **params_f}
        if dimension:
            col = dimension_column(dimension)
            sql = (
                f"SELECT COUNT(*) AS n_rows, "
                f"  COUNT(DISTINCT {col}) AS n_distinct_dim "
                f"FROM sales_v WHERE invoice_date BETWEEN :s AND :e {where_f}"
            )
        else:
            sql = (
                "SELECT COUNT(*) AS n_rows, "
                "  COUNT(DISTINCT invoice_date) AS n_distinct_dates "
                f"FROM sales_v WHERE invoice_date BETWEEN :s AND :e {where_f}"
            )
        rows, _ = self.warehouse.run_query(sql, params)
        r = rows[0] if rows else {}
        n_rows = int(r.get("n_rows") or 0)
        sufficient = n_rows >= 30  # crude but honest floor for comparisons
        reason = "ok" if sufficient else f"only {n_rows} rows in range — too sparse for comparison"
        return _result(
            rows=[{"sufficient": sufficient, "reason": reason, **r}],
            sql=sql,
            params=params,
        )

    def list_dimension_values(self, dimension: str, limit: int = 50) -> dict[str, Any]:
        dimension_column(dimension)  # validate
        limit = max(1, min(int(limit), 500))
        values = self.warehouse.distinct_values(dimension, limit=limit)
        return _result(
            rows=[{"dimension": dimension, "values": values, "count": len(values)}],
            sql=f"SELECT DISTINCT {dimension} FROM sales_v LIMIT {limit}",
            params={"dimension": dimension, "limit": limit},
        )


# ---- helpers ----


def _result(rows: list[dict[str, Any]], sql: str, params: dict[str, Any], truncated: bool = False) -> dict[str, Any]:
    return {
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "sql": " ".join(sql.split()),
        "params": params,
    }


def _pct(a: float, b: float) -> float | None:
    if not b:
        return None
    return round((a - b) / b * 100, 2)


def _validate_date(s: str) -> str:
    return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")


def _filter_clause(filters: dict[str, str] | None) -> tuple[str, dict[str, str]]:
    if not filters:
        return "", {}
    parts: list[str] = []
    params: dict[str, str] = {}
    for dim, value in filters.items():
        col = dimension_column(dim)
        key = f"f_{dim}"
        parts.append(f"AND {col} = :{key}")
        params[key] = value
    return " ".join(parts), params


def _resolve_period(period: dict, reference_date: str) -> tuple[str, str]:
    """Resolve a period dict into an inclusive (start, end) ISO date range.

    Accepts either {"name": "<named period>"} or {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}.
    """
    if "start" in period and "end" in period:
        return _validate_date(period["start"]), _validate_date(period["end"])
    name = period.get("name")
    if not name:
        raise ValueError(f"period must have either 'name' or both 'start' and 'end': got {period}")
    if name not in _NAMED_PERIODS:
        raise ValueError(f"unknown named period '{name}'. must be one of {_NAMED_PERIODS}")
    ref = datetime.strptime(reference_date, "%Y-%m-%d").date()
    return _named_period(name, ref)


def _named_period(name: str, ref: date) -> tuple[str, str]:
    if name == "current_month":
        start = ref.replace(day=1)
        end = _end_of_month(ref)
    elif name == "prior_month":
        last_of_prev = ref.replace(day=1) - timedelta(days=1)
        start = last_of_prev.replace(day=1)
        end = last_of_prev
    elif name == "same_month_last_year":
        ly = ref.replace(year=ref.year - 1)
        start = ly.replace(day=1)
        end = _end_of_month(ly)
    elif name == "current_quarter":
        q = (ref.month - 1) // 3
        start = date(ref.year, q * 3 + 1, 1)
        end = _end_of_month(date(ref.year, q * 3 + 3, 1))
    elif name == "prior_quarter":
        q = (ref.month - 1) // 3
        if q == 0:
            start = date(ref.year - 1, 10, 1)
            end = date(ref.year - 1, 12, 31)
        else:
            start = date(ref.year, (q - 1) * 3 + 1, 1)
            end = _end_of_month(date(ref.year, (q - 1) * 3 + 3, 1))
    elif name == "trailing_90d":
        end = ref
        start = ref - timedelta(days=89)
    elif name == "prior_90d":
        end = ref - timedelta(days=90)
        start = end - timedelta(days=89)
    elif name == "trailing_30d":
        end = ref
        start = ref - timedelta(days=29)
    elif name == "prior_30d":
        end = ref - timedelta(days=30)
        start = end - timedelta(days=29)
    else:  # unreachable due to validation above
        raise ValueError(name)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _end_of_month(d: date) -> date:
    if d.month == 12:
        return date(d.year, 12, 31)
    return date(d.year, d.month + 1, 1) - timedelta(days=1)


# ---- OpenAI tool schemas ----

_PERIOD_SCHEMA = {
    "type": "object",
    "description": (
        "Either a named period anchored at the reference date "
        f"(one of: {', '.join(_NAMED_PERIODS)}) or an explicit ISO date range."
    ),
    "oneOf": [
        {
            "type": "object",
            "properties": {"name": {"type": "string", "enum": _NAMED_PERIODS}},
            "required": ["name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "start": {"type": "string", "description": "Inclusive ISO date YYYY-MM-DD"},
                "end": {"type": "string", "description": "Inclusive ISO date YYYY-MM-DD"},
            },
            "required": ["start", "end"],
            "additionalProperties": False,
        },
    ],
}

_FILTERS_SCHEMA = {
    "type": "object",
    "description": "Optional equality filters on dimensions. Example: {'country': 'United Kingdom'}.",
    "properties": {dim: {"type": "string"} for dim in _DIMENSIONS},
    "additionalProperties": False,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "metric_overview",
            "description": (
                "Return current value and prior-period change for one or more metrics. "
                "Cheap fan-out; use as the first call to see where movement is."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {"type": "array", "items": {"type": "string", "enum": _METRICS}, "minItems": 1},
                    "current_period": _PERIOD_SCHEMA,
                    "comparison_period": _PERIOD_SCHEMA,
                },
                "required": ["metrics", "current_period", "comparison_period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "period_comparison",
            "description": "Compare one metric across two periods, with optional dimension filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": _METRICS},
                    "period_a": _PERIOD_SCHEMA,
                    "period_b": _PERIOD_SCHEMA,
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["metric", "period_a", "period_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dimension_decomposition",
            "description": "Break a metric down by one dimension for a period; returns ranked values with share of total.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": _METRICS},
                    "dimension": {"type": "string", "enum": _DIMENSIONS},
                    "period": _PERIOD_SCHEMA,
                    "filters": _FILTERS_SCHEMA,
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                },
                "required": ["metric", "dimension", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "time_series",
            "description": "Metric over time at daily/weekly/monthly grain between explicit start and end dates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": _METRICS},
                    "grain": {"type": "string", "enum": _GRAINS},
                    "start": {"type": "string", "description": "Inclusive ISO date YYYY-MM-DD"},
                    "end": {"type": "string", "description": "Inclusive ISO date YYYY-MM-DD"},
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["metric", "grain", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "top_contributors",
            "description": (
                "Rank dimension values by their signed contribution to the change in a metric between two periods. "
                "Use to explain why a metric moved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": _METRICS},
                    "dimension": {"type": "string", "enum": _DIMENSIONS},
                    "period_a": _PERIOD_SCHEMA,
                    "period_b": _PERIOD_SCHEMA,
                    "filters": _FILTERS_SCHEMA,
                    "top_n": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                "required": ["metric", "dimension", "period_a", "period_b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_sufficiency_check",
            "description": "Check whether enough rows exist for a reliable comparison before running one with tight filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string", "enum": _METRICS},
                    "period": _PERIOD_SCHEMA,
                    "dimension": {"type": "string", "enum": _DIMENSIONS},
                    "filters": _FILTERS_SCHEMA,
                },
                "required": ["metric", "period"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dimension_values",
            "description": (
                "List valid values for a dimension (e.g. which countries exist). "
                "Call before using a filter to avoid guessing wrong."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {"type": "string", "enum": _DIMENSIONS},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                },
                "required": ["dimension"],
            },
        },
    },
]


def dispatch(tools: AnalysisTools, name: str, arguments: dict[str, Any] | str) -> str:
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    fn = getattr(tools, name, None)
    if fn is None:
        raise ValueError(f"unknown tool: {name}")
    return json.dumps(fn(**arguments), default=str)
