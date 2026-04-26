"""Investigator agent — runs the bounded tool loop over the sales warehouse.

Contract:
  - Input: reference date and a short investigation brief (e.g. "monthly trend review").
  - Tools: the seven defined in `tools.py`. Nothing else.
  - Output: a list of `findings`, each citing the tool_call_ids that support it,
    plus the full tool-call log for the Writer to reference.

The Investigator produces *findings*, not prose. The Writer turns findings into
the analyst-facing briefing. Keeping the two agents separate means the Writer
has no way to fabricate numbers — it can only reference what the Investigator
already found.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from db import Warehouse
from tools import TOOL_SCHEMAS, AnalysisTools, dispatch

MAX_TOOL_ITERATIONS = 15
CLASSIFICATIONS = ["rising", "declining", "accelerating", "decelerating", "stable"]

SYSTEM_PROMPT = """You are a trend-analytics investigator. Your job is to look at a small sales warehouse and find the few most significant movements worth telling a business analyst about.

You have seven tools, each backed by validated SQL. You do not write SQL yourself — you only pick a tool and fill its arguments. The tools are:

- metric_overview          — current vs prior value for one or more metrics; your usual first call.
- period_comparison        — one metric across two periods, with optional filters.
- dimension_decomposition  — rank a metric by country / product / customer for a period.
- time_series              — metric over time at day/week/month grain.
- top_contributors         — rank dimension values by signed contribution to a change between two periods.
- data_sufficiency_check   — check row counts before running a comparison with tight filters.
- list_dimension_values    — list valid values for a dimension (prevents wrong filter guesses).

Domain:
- Metrics: revenue, units, orders.
- Dimensions: country, product, customer.
- Reference date (anchors all named periods): {reference_date}.
- Named periods available: current_month, prior_month, same_month_last_year, current_quarter, prior_quarter, trailing_30d, prior_30d, trailing_90d, prior_90d.

Significance thresholds (use these to decide what's worth reporting):
- Magnitude: |pct_change| >= 5% AND |abs_change| at a scale that matters for the metric.
- Concentration: if a change is driven >= 40% by a single dimension value, that value is worth naming.
- Persistence: if a series has moved in the same direction for 3+ consecutive buckets, that's a trend.

How to work:
1. Start with metric_overview comparing current_month vs prior_month.
2. For any metric that moved meaningfully, decompose by the most informative dimension, then use top_contributors to explain the change.
3. Use time_series to confirm whether a change is a one-off or part of a trend.
4. If you want to filter to a specific dimension value (e.g. country="France"), call list_dimension_values first so you don't guess.
5. Call data_sufficiency_check before any comparison with tight filters.
6. Do not exceed 15 tool calls. Stop earlier if you've already covered the significant movements.

When you are done investigating, respond with a JSON object (and nothing else — no prose, no Markdown fences) with this exact shape:

{{
  "findings": [
    {{
      "finding_id": "F1",
      "headline": "<one short sentence naming the metric and direction>",
      "classification": "<one of: rising, declining, accelerating, decelerating, stable>",
      "detail": "<2-4 sentences with the specific numbers and drivers you found>",
      "evidence": [
        {{"tool_call_id": "<id from a tool call you made>", "note": "<what this call supports>"}}
      ]
    }}
  ],
  "summary": "<one-sentence overall readout>"
}}

Every number you cite in `detail` must appear in at least one of the tool results referenced by `evidence`. If nothing significant moved, return an empty `findings` array and say so in `summary`.""".strip()

USER_BRIEF = (
    "Produce the weekly trend and performance briefing for the reference date. "
    "Focus on the most significant movements across revenue, units, and orders."
)


@dataclass
class InvestigationResult:
    findings: list[dict[str, Any]]
    summary: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw_final_message: str = ""
    iterations: int = 0


class Investigator:
    def __init__(
        self,
        openai_endpoint: str,
        warehouse: Warehouse,
        reference_date: str,
        chat_deployment: str = "gpt-4o",
        verbose: bool = False,
    ) -> None:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        self.client = AzureOpenAI(
            azure_endpoint=openai_endpoint,
            azure_ad_token_provider=token_provider,
            api_version="2024-10-21",
        )
        self.chat_deployment = chat_deployment
        self.reference_date = reference_date
        self.verbose = verbose
        self.tools = AnalysisTools(warehouse=warehouse, reference_date=reference_date)
        self.system_prompt = SYSTEM_PROMPT.format(reference_date=reference_date)

    def investigate(self, brief: str = USER_BRIEF) -> InvestigationResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": brief},
        ]
        tool_calls_log: list[dict[str, Any]] = []

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            resp = self.client.chat.completions.create(
                model=self.chat_deployment,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = resp.choices[0].message

            if not msg.tool_calls:
                final = msg.content or ""
                findings, summary = _parse_findings(final)
                return InvestigationResult(
                    findings=findings,
                    summary=summary,
                    tool_calls=tool_calls_log,
                    raw_final_message=final,
                    iterations=iteration,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments},
                        }
                        for c in msg.tool_calls
                    ],
                }
            )

            for call in msg.tool_calls:
                name = call.function.name
                args_raw = call.function.arguments
                if self.verbose:
                    print(f"  [iter {iteration}] {name}({args_raw})")
                try:
                    result_json = dispatch(self.tools, name, args_raw)
                except Exception as exc:
                    result_json = json.dumps({"error": str(exc)})
                if self.verbose:
                    preview = result_json if len(result_json) <= 220 else result_json[:217] + "..."
                    print(f"     → {preview}")
                tool_calls_log.append(
                    {
                        "tool_call_id": call.id,
                        "name": name,
                        "arguments": args_raw,
                        "result": result_json,
                        "iteration": iteration,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_json,
                    }
                )

        # Budget exhausted — ask the model once for a final JSON with no further tool use.
        messages.append(
            {
                "role": "user",
                "content": (
                    "Tool budget reached. Produce the findings JSON now based on what you already have. "
                    "Do not call any more tools."
                ),
            }
        )
        resp = self.client.chat.completions.create(
            model=self.chat_deployment,
            messages=messages,
            temperature=0.2,
        )
        final = resp.choices[0].message.content or ""
        findings, summary = _parse_findings(final)
        return InvestigationResult(
            findings=findings,
            summary=summary,
            tool_calls=tool_calls_log,
            raw_final_message=final,
            iterations=MAX_TOOL_ITERATIONS,
        )


def _parse_findings(text: str) -> tuple[list[dict[str, Any]], str]:
    """Best-effort JSON extraction. Returns (findings, summary)."""
    s = text.strip()
    # Strip markdown fences if the model added them despite instructions.
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    # Locate outermost object.
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        return [], f"(investigator returned non-JSON: {text[:200]})"
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return [], f"(investigator JSON did not parse: {text[:200]})"
    findings = obj.get("findings") or []
    summary = obj.get("summary") or ""
    return findings if isinstance(findings, list) else [], str(summary)
