"""Team orchestrator — Investigator → Writer → Critic loop until approved.

The agent-team pattern: each round the Critic decides whether to ship the briefing,
ask the Writer to revise wording, or send the Investigator back for more evidence.
A hard cap on rounds prevents runaway loops; if the cap is hit before approval the
last draft is returned with `approved=False` so callers can decide what to do.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agents.critic import Critic, CriticDecision
from agents.investigator import USER_BRIEF, Investigator
from agents.writer import write_briefing

DEFAULT_MAX_ROUNDS = 3


@dataclass
class TeamRound:
    round: int
    verdict: str
    feedback: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    investigator_iterations: int | None = None
    investigator_seconds: float | None = None
    writer_seconds: float | None = None
    critic_seconds: float | None = None
    new_tool_calls: int = 0


@dataclass
class TeamResult:
    briefing: str
    findings: list[dict[str, Any]]
    summary: str
    tool_calls: list[dict[str, Any]]
    rounds: list[TeamRound]
    approved: bool


def run_team(
    *,
    investigator: Investigator,
    openai_endpoint: str,
    chat_deployment: str,
    reference_date: str,
    brief: str = USER_BRIEF,
    critic: Critic | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    verbose: bool = False,
) -> TeamResult:
    if critic is None:
        critic = Critic(
            openai_endpoint=openai_endpoint,
            chat_deployment=chat_deployment,
            verbose=verbose,
        )

    findings: list[dict[str, Any]] = []
    summary: str = ""
    tool_calls: list[dict[str, Any]] = []
    briefing: str = ""
    inv_feedback: str | None = None
    writer_feedback: str | None = None
    rounds: list[TeamRound] = []
    approved = False

    for round_num in range(1, max_rounds + 1):
        if verbose:
            print(f"\n[team] === round {round_num}/{max_rounds} ===")

        inv_iters: int | None = None
        inv_seconds: float | None = None
        new_calls = 0
        if not findings or inv_feedback is not None:
            if verbose:
                tag = "investigator (revision)" if inv_feedback else "investigator"
                print(f"[team] {tag} starting…")
            t0 = time.time()
            inv_result = investigator.investigate(
                brief=brief,
                critic_feedback=inv_feedback,
                prior_findings=findings if inv_feedback else None,
            )
            inv_seconds = time.time() - t0
            findings = inv_result.findings
            summary = inv_result.summary
            tool_calls.extend(inv_result.tool_calls)
            new_calls = len(inv_result.tool_calls)
            inv_iters = inv_result.iterations
            inv_feedback = None
            if verbose:
                print(
                    f"[team] investigator done in {inv_seconds:.1f}s  "
                    f"iters={inv_iters}  new_tool_calls={new_calls}  findings={len(findings)}"
                )

        if verbose:
            tag = "writer (revision)" if writer_feedback else "writer"
            print(f"[team] {tag} composing…")
        t1 = time.time()
        briefing = write_briefing(
            openai_endpoint=openai_endpoint,
            chat_deployment=chat_deployment,
            reference_date=reference_date,
            findings=findings,
            summary=summary,
            tool_calls=tool_calls,
            critic_feedback=writer_feedback,
            previous_draft=briefing if writer_feedback else None,
        )
        writer_seconds = time.time() - t1
        writer_feedback = None
        if verbose:
            print(f"[team] writer done in {writer_seconds:.1f}s")

        if verbose:
            print("[team] critic reviewing…")
        t2 = time.time()
        decision: CriticDecision = critic.review(
            reference_date=reference_date,
            findings=findings,
            summary=summary,
            tool_calls=tool_calls,
            briefing=briefing,
        )
        critic_seconds = time.time() - t2
        if verbose:
            print(
                f"[team] critic done in {critic_seconds:.1f}s  "
                f"verdict={decision.verdict}  issues={len(decision.issues)}"
            )
            if decision.feedback:
                print(f"[team] critic feedback: {decision.feedback}")

        rounds.append(
            TeamRound(
                round=round_num,
                verdict=decision.verdict,
                feedback=decision.feedback,
                issues=decision.issues,
                investigator_iterations=inv_iters,
                investigator_seconds=round(inv_seconds, 2) if inv_seconds is not None else None,
                writer_seconds=round(writer_seconds, 2),
                critic_seconds=round(critic_seconds, 2),
                new_tool_calls=new_calls,
            )
        )

        if decision.verdict == "approve":
            approved = True
            break
        if decision.verdict == "revise_writer":
            writer_feedback = decision.feedback
        elif decision.verdict == "revise_investigator":
            inv_feedback = decision.feedback

    if verbose and not approved:
        print(f"\n[team] max_rounds={max_rounds} reached without approval — returning last draft")

    return TeamResult(
        briefing=briefing,
        findings=findings,
        summary=summary,
        tool_calls=tool_calls,
        rounds=rounds,
        approved=approved,
    )
