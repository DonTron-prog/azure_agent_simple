"""Critic agent — reviews the Investigator's findings and the Writer's draft briefing.

The Critic closes the loop in the Investigator → Writer → Critic team. It cannot
fabricate or correct numbers itself; it issues one of three verdicts:

  - approve              the briefing is solid; ship it.
  - revise_writer        the facts are sound but the briefing has issues
                         (missing citations, overreach, wording problems).
  - revise_investigator  the underlying findings are insufficient or unsupported
                         (a flagged movement was never decomposed, a number in
                         findings doesn't appear in any tool result, etc.).

Inputs:
  - findings + summary from the Investigator
  - full tool-call log
  - the draft briefing (Markdown) from the Writer

A small deterministic pre-pass extracts every number from the briefing and checks
whether it appears verbatim in the tool log. The unsupported list is fed to the
LLM so the verdict is grounded in objective evidence. The LLM still makes the
final call — it can forgive a missing number as legitimate rounding or flag a
worse problem the regex cannot see.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

Verdict = Literal["approve", "revise_writer", "revise_investigator"]
ALLOWED_VERDICTS: tuple[Verdict, ...] = ("approve", "revise_writer", "revise_investigator")

# Integers, decimals, percentages, optional comma grouping, optional sign.
_NUMBER_RE = re.compile(r"-?[0-9][0-9,]*(?:\.[0-9]+)?%?")

SYSTEM_PROMPT = """You are the critic in a three-agent trend-analytics team. The Investigator queried a sales warehouse with seven bounded tools and produced structured findings. The Writer turned those findings into a Markdown briefing for a business analyst. Your job is to decide whether the briefing is ready to ship.

You have three verdicts:

- "approve"               The briefing is correct, well-cited, and covers what matters. Ship it.
- "revise_writer"         The Investigator's findings are sound, but the briefing itself has problems
                          the Writer can fix without new data. Examples: missing tool_call_id citations,
                          numbers stated more loosely than the source supports, important findings buried,
                          overreaching language, missing Sources section.
- "revise_investigator"   The findings themselves are unsafe to publish. The Investigator must dig deeper.
                          Examples: a number in findings.detail is not supported by any tool_result; a metric
                          moved meaningfully (>=5%) but was never decomposed; a finding is classified as a
                          trend without time_series evidence; data_sufficiency_check should have been called
                          for a tight filter and was skipped.

How to judge:

1. Grounding. Every number in the briefing must trace to a tool result. You will be given a list of numbers
   the briefing cites that did NOT appear verbatim in the tool log. Use judgment: a number can legitimately
   differ by rounding (e.g. 12.345 in the source vs 12.3 in the briefing is fine; one decimal place on a
   percentage is fine). A number that is far off, of opposite sign, or has no plausible source in the log
   is a fabrication and forces revise_investigator.

2. Significance thresholds (from the Investigator's brief):
   - Magnitude: |pct_change| >= 5% AND a meaningful absolute change.
   - Concentration: drivers >= 40% of a change deserve naming.
   - Persistence: 3+ consecutive same-direction buckets is a trend.
   If a finding violates these (e.g. classifies a 1% wobble as "rising"), revise_investigator.

3. Coverage. If a metric moved meaningfully in metric_overview but no decomposition or top_contributors
   call was made for it, that's a gap → revise_investigator.

4. Citations. Every finding must cite tool_call_ids that exist in the log. The briefing should cite tool_call_ids
   inline in the format the Writer was instructed to use.

5. Empty findings are fine if nothing actually moved. Approve them if the tool log shows the Investigator
   genuinely looked.

Be strict but practical. Do not request revision over taste-level wording. Do not request a third investigation
round if the first two already cover the significant movements. The team has a hard cap on rounds; if you say
revise, expect another run to follow.

Respond with a single JSON object (no Markdown, no prose around it) with this exact shape:

{
  "verdict": "approve" | "revise_writer" | "revise_investigator",
  "feedback": "<2-5 sentences of guidance for the next agent. If approve, briefly say what was good. If revise, name the SPECIFIC issues to fix and HOW.>",
  "issues": [
    {"kind": "<one of: fabrication, missing_citation, threshold_violation, coverage_gap, wording, other>",
     "where": "<short locator: 'finding F2', 'briefing bullet 3', 'summary line', etc.>",
     "detail": "<one sentence>"}
  ],
  "numbers_judged_ok": ["<unsupported number that you decided is acceptable rounding>", "..."],
  "numbers_judged_bad": ["<unsupported number that is a real fabrication>", "..."]
}
""".strip()


@dataclass
class CriticDecision:
    verdict: Verdict
    feedback: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    numbers_judged_ok: list[str] = field(default_factory=list)
    numbers_judged_bad: list[str] = field(default_factory=list)
    numbers_unsupported_raw: list[str] = field(default_factory=list)
    raw_response: str = ""


class Critic:
    def __init__(
        self,
        openai_endpoint: str,
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
        self.verbose = verbose

    def review(
        self,
        reference_date: str,
        findings: list[dict[str, Any]],
        summary: str,
        tool_calls: list[dict[str, Any]],
        briefing: str,
    ) -> CriticDecision:
        unsupported = _find_unsupported_numbers(briefing, tool_calls)
        if self.verbose:
            print(f"  [critic] {len(unsupported)} number(s) not found verbatim in tool log")

        tool_call_digest = [
            {
                "tool_call_id": c["tool_call_id"],
                "name": c["name"],
                "arguments": c["arguments"],
                "result": c["result"],
            }
            for c in tool_calls
        ]

        user_payload = {
            "reference_date": reference_date,
            "investigator_summary": summary,
            "findings": findings,
            "tool_calls": tool_call_digest,
            "briefing_markdown": briefing,
            "deterministic_check": {
                "numbers_in_briefing_not_found_verbatim_in_tool_log": unsupported,
                "note": "Verbatim mismatch may be legitimate rounding or real fabrication — you decide.",
            },
        }

        resp = self.client.chat.completions.create(
            model=self.chat_deployment,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload, default=str)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        verdict, feedback, issues, ok, bad = _parse_decision(raw)
        return CriticDecision(
            verdict=verdict,
            feedback=feedback,
            issues=issues,
            numbers_judged_ok=ok,
            numbers_judged_bad=bad,
            numbers_unsupported_raw=unsupported,
            raw_response=raw,
        )


def _find_unsupported_numbers(briefing: str, tool_calls: list[dict[str, Any]]) -> list[str]:
    """Numbers cited in the briefing that don't appear verbatim in any tool result.

    Strict: '12.3' won't match a source that has '12.345'. The LLM critic decides
    whether each unmatched number is rounding or fabrication.
    """
    haystack = "\n".join(c.get("result", "") for c in tool_calls)
    candidates = sorted(set(_NUMBER_RE.findall(briefing)))
    unsupported: list[str] = []
    for n in candidates:
        bare = n.rstrip("%").replace(",", "")
        if bare in haystack or n in haystack:
            continue
        # Skip pure year/date fragments (e.g. "2011" almost certainly appears as
        # part of an ISO date in the briefing's header, not as a metric value).
        if bare.isdigit() and 1900 <= int(bare) <= 2100:
            continue
        unsupported.append(n)
    return unsupported


def _parse_decision(text: str) -> tuple[Verdict, str, list[dict[str, Any]], list[str], list[str]]:
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
        s = s.strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        return "revise_writer", f"(critic returned non-JSON: {text[:200]})", [], [], []
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return "revise_writer", f"(critic JSON did not parse: {text[:200]})", [], [], []

    verdict = obj.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        verdict = "revise_writer"
    feedback = str(obj.get("feedback") or "")
    issues = obj.get("issues") or []
    if not isinstance(issues, list):
        issues = []
    ok = [str(x) for x in (obj.get("numbers_judged_ok") or []) if isinstance(x, (str, int, float))]
    bad = [str(x) for x in (obj.get("numbers_judged_bad") or []) if isinstance(x, (str, int, float))]
    return verdict, feedback, issues, ok, bad
