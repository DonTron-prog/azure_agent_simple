"""Run one briefing: Investigator → Writer → briefing.md + run log.

Manual trigger: `python scripts/run.py`. Each run writes a timestamped folder
under `data/runs/<run_id>/` with:
  - briefing.md   the analyst-facing output
  - findings.json the structured findings from the Investigator
  - tool_calls.jsonl every tool call + result, one per line
  - run.json      metadata (reference date, deployment, iterations, timing)
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from agents.investigator import Investigator  # noqa: E402
from agents.writer import write_briefing  # noqa: E402
from config import get_config  # noqa: E402
from db import warehouse  # noqa: E402


def main() -> None:
    cfg = get_config()
    db_path = ROOT / cfg.warehouse_db if not cfg.warehouse_db.is_absolute() else cfg.warehouse_db
    runs_dir = ROOT / cfg.runs_dir if not cfg.runs_dir.is_absolute() else cfg.runs_dir

    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"[run] {run_id} starting")
    print(f"[run] warehouse: {db_path}")

    with warehouse(db_path) as wh:
        ref_date = cfg.reference_date or wh.reference_date()
        if not ref_date:
            raise RuntimeError("no reference_date in .env and warehouse is empty")
        investigator = Investigator(
            openai_endpoint=cfg.openai_endpoint,
            warehouse=wh,
            reference_date=ref_date,
            chat_deployment=cfg.chat_deployment,
        )

        print(f"[run] reference_date={ref_date}  deployment={cfg.chat_deployment}")

        t0 = time.time()
        print("[run] investigator starting tool loop…")
        result = investigator.investigate()
        inv_elapsed = time.time() - t0
        print(
            f"[run] investigator done in {inv_elapsed:.1f}s  "
            f"iterations={result.iterations}  tool_calls={len(result.tool_calls)}  "
            f"findings={len(result.findings)}"
        )

        t1 = time.time()
        print("[run] writer composing briefing…")
        briefing = write_briefing(
            openai_endpoint=cfg.openai_endpoint,
            chat_deployment=cfg.chat_deployment,
            reference_date=ref_date,
            findings=result.findings,
            summary=result.summary,
            tool_calls=result.tool_calls,
        )
        wr_elapsed = time.time() - t1

    (run_dir / "briefing.md").write_text(briefing, encoding="utf-8")
    (run_dir / "findings.json").write_text(
        json.dumps({"summary": result.summary, "findings": result.findings}, indent=2),
        encoding="utf-8",
    )
    with (run_dir / "tool_calls.jsonl").open("w", encoding="utf-8") as f:
        for c in result.tool_calls:
            f.write(json.dumps(c, default=str) + "\n")
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "reference_date": ref_date,
                "chat_deployment": cfg.chat_deployment,
                "iterations": result.iterations,
                "tool_calls": len(result.tool_calls),
                "findings": len(result.findings),
                "investigator_seconds": round(inv_elapsed, 2),
                "writer_seconds": round(wr_elapsed, 2),
                "raw_final_message": result.raw_final_message,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"[run] wrote {run_dir}/briefing.md")
    print("--- briefing preview ---")
    print(briefing)


if __name__ == "__main__":
    main()
