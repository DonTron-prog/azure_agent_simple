"""Writer agent — turns findings + tool-call log into a Markdown briefing.

The Writer has **no tools** and no database access. Its input is exactly what
the Investigator produced. This is the guardrail that satisfies the "no
fabricated claims" success criterion: the Writer physically cannot invent a
number, because it cannot query anything.
"""
from __future__ import annotations

import json
from typing import Any

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

SYSTEM_PROMPT = """You are a briefing writer. You turn structured findings produced by an investigator agent into a short Markdown briefing for a business analyst.

Ground rules:
1. You have no tools and no access to data beyond what is provided to you.
2. Every number in your briefing must come from the tool-call results you are shown. Do not compute new numbers. Do not round aggressively (one decimal place on percentages is fine).
3. Every finding must cite the tool_call_id(s) that support it, in the format [tool_call_id].
4. Keep it short: a 1-line overall summary, then one bullet per finding.
5. End with a "Sources" section listing each distinct tool_call_id you cited with the tool name.
6. If findings is empty, say explicitly that nothing significant moved this period.

Output Markdown only. No preamble, no sign-off.""".strip()

BRIEFING_TEMPLATE = """# Weekly Trend Briefing — {reference_date}

{body}
"""


def write_briefing(
    openai_endpoint: str,
    chat_deployment: str,
    reference_date: str,
    findings: list[dict[str, Any]],
    summary: str,
    tool_calls: list[dict[str, Any]],
) -> str:
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    client = AzureOpenAI(
        azure_endpoint=openai_endpoint,
        azure_ad_token_provider=token_provider,
        api_version="2024-10-21",
    )

    # Give the Writer only the fields it needs — trims tokens and removes temptation.
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
    }

    resp = client.chat.completions.create(
        model=chat_deployment,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, default=str)},
        ],
        temperature=0.3,
    )
    body = (resp.choices[0].message.content or "").strip()
    return BRIEFING_TEMPLATE.format(reference_date=reference_date, body=body)
