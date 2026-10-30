#!/usr/bin/env python3
"""Assignment 4, Part E, task 3: what the checkpoints of one run cost on disk.

    python eval/checkpoint_bytes.py planned-4207-s01-abc123 planned-4207-s02-...
    python eval/checkpoint_bytes.py --shadow      # every run in the shadow file

Two numbers per run, both the bytes of all of that run's checkpoints together:

  structured          what state/checkpointer.py saved: slide 14's five fields.
  with_conversation   the same checkpoints with every message the way had sent the
                      model so far, written by ways/planned.py to
                      state/checkpoints_with_conversation.jsonl when CP_SHADOW is set.

The runs measured save their checkpoints as every run does, through the checkpointer,
with their checkpoint lines. The with-conversation copy never goes near the checkpoint
database: the checker fails any checkpoint that holds a conversation, and so does
state/checkpointer.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from state.checkpointer import Checkpointer    # noqa: E402

SHADOW = ROOT / "state" / "checkpoints_with_conversation.jsonl"


def shadow_rows() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if SHADOW.exists():
        for line in SHADOW.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out.setdefault(row["run_id"], []).append(row)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="*", help="the run_ids to measure")
    p.add_argument("--shadow", action="store_true", help="every run in the shadow file")
    a = p.parse_args(argv)

    rows = shadow_rows()
    wanted = a.runs or (sorted(rows) if a.shadow else [])
    if not wanted:
        print("give run_ids, or --shadow for every run with a with-conversation copy")
        return 1
    cp = Checkpointer()
    totals = {"structured": 0, "with_conversation": 0, "runs": 0}
    print(f"{'run_id':<34}{'ckpts':>6}{'structured':>13}{'with conv.':>13}{'ratio':>8}")
    for run_id in wanted:
        saved = cp.lines(run_id)
        structured = sum(int(r["bytes"]) for r in saved)
        conversation = sum(int(r["bytes"]) for r in rows.get(run_id, []))
        if not saved or not conversation:
            print(f"{run_id:<34}{len(saved):>6}{structured:>13,}{'no shadow copy':>13}")
            continue
        totals["structured"] += structured
        totals["with_conversation"] += conversation
        totals["runs"] += 1
        print(f"{run_id:<34}{len(saved):>6}{structured:>13,}{conversation:>13,}"
              f"{conversation / structured:>7.1f}x")
    if totals["runs"]:
        mean_s = round(totals["structured"] / totals["runs"])
        mean_c = round(totals["with_conversation"] / totals["runs"])
        print(f"\nmean over {totals['runs']} runs: structured {mean_s:,} bytes, "
              f"with the conversation {mean_c:,} bytes ({mean_c / mean_s:.1f}x)")
        print("checkpoint_bytes = " + json.dumps(
            {"structured": mean_s, "with_conversation": mean_c, "runs": totals["runs"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
