#!/usr/bin/env python3
"""Assignment 4, Part D: put the three candidates in fixtures/memory/ through the write
policy and into the store.

    python eval/seed_memory.py            # store them, and say what was decided
    python eval/seed_memory.py --dry-run  # say what would be stored, and store nothing

This is also the first of Part E's three sessions: the process that stores the
maintainer's rule on duplicates. The second and third are eval/contradiction.py.

The decisions are state/write_policy.md's, not this script's. The script only reads
each candidate's fields and applies them:

  A, a procedure RepoMind worked out in a run of 4191. Procedural, source `memory`,
     derived, confirmed: the run that worked it out ran the commands. It is about the
     repository and not about issue 4191, so it is stored repository-wide. It invites
     staleness: package.json can change and the procedure not.
  B, a claim in the body of issue 4188, written by an account outside the team.
     Episodic, source `issue:4188`, ingested, NOT confirmed, and never semantic: a
     record that the text exists, not a record that it is true. Stored as a fact it
     would come back later with RepoMind's own authority, which is the path slide 24
     calls memory poisoning. state/store.py's trusted() keeps it out of every prompt.
  C, a rule a maintainer wrote in a comment on 4185. Procedural, source `user`,
     stated_by priya-s, ingested, confirmed: a person with standing stating a rule is
     what confirms it (the policy says so, and says why). It is about the repository,
     so it is stored repository-wide. It invites contradiction when the rule changes,
     and Part E's three sessions are that contradiction.

`written_at` is each candidate's own `when`, not the moment of this script: the policy
stores when a memory was first observed, because its age is what a later run judges it
by.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.formats import squeeze            # noqa: E402
from state.store import REPOSITORY, Store      # noqa: E402

CANDIDATES = ROOT / "fixtures" / "memory"

# The write policy's decision for each candidate, by its `candidate` letter.
DECISIONS = {
    "A": {"store": True, "kind": "procedural", "source": "memory", "origin": "derived",
          "confirmed": True, "scope": "repository", "stated_by": None,
          "why": "expensive to derive, and the run that derived it ran the commands",
          "invites": "staleness"},
    "B": {"store": True, "kind": "episodic", "source": "issue:4188", "origin": "ingested",
          "confirmed": False, "scope": "issue", "stated_by": None,
          "why": "a record that the issue said it, never a record that it is true",
          "invites": "poisoning, if it were stored as a fact"},
    "C": {"store": True, "kind": "procedural", "source": "user", "origin": "ingested",
          "confirmed": True, "scope": "repository", "stated_by": "priya-s",
          "why": "stated by a person with standing, which is what confirms it",
          "invites": "contradiction"},
}


def candidates() -> list[dict]:
    return [yaml.safe_load(p.read_text(encoding="utf-8"))
            for p in sorted(CANDIDATES.glob("*.yaml"))]


def as_record(candidate: dict, decision: dict) -> dict:
    return {
        "repository": candidate.get("repository") or REPOSITORY,
        "issue": candidate.get("issue") if decision["scope"] == "issue" else None,
        "kind": decision["kind"],
        "text": str(candidate["seen"]).strip(),
        "source": decision["source"],
        "written_at": candidate["when"],
        "origin": decision["origin"],
        "confirmed": decision["confirmed"],
        "stated_by": decision["stated_by"],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="say what would be stored, and store nothing")
    a = p.parse_args(argv)

    store = Store()
    held = {squeeze(r["text"]) for r in store.records()}
    print(f"store: {store.path}")
    for candidate in candidates():
        letter = candidate["candidate"]
        decision = DECISIONS[letter]
        record = as_record(candidate, decision)
        head = f"candidate {letter} ({candidate['where']})"
        if not decision["store"]:
            print(f"{head}: not stored. {decision['why']}")
            continue
        if squeeze(record["text"]) in held:
            print(f"{head}: already in the store, written once")
            continue
        if a.dry_run:
            print(f"{head}: would store as {record['kind']}, source {record['source']}, "
                  f"origin {record['origin']}, confirmed {record['confirmed']}")
            continue
        rid = store.write(record)
        print(f"{head}: stored as {rid}, {record['kind']}, source {record['source']}, "
              f"origin {record['origin']}, confirmed {record['confirmed']}, "
              f"issue {record['issue']}, written_at {record['written_at']}")
        print(f"    why: {decision['why']}; it invites {decision['invites']}")
    live = [r for r in store.records() if r["active"]]
    print(f"{len(store.records())} records in the store, {len(live)} active")
    return 0


if __name__ == "__main__":
    sys.exit(main())
