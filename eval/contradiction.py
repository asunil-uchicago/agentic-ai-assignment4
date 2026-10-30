#!/usr/bin/env python3
"""Assignment 4, Part E, task 2: the contradiction test, as slide 25 asks for it.

Set a fact, contradict it, check in a third session. Three sessions, three separate
processes, because a test inside one process proves nothing: an append-only store
looks correct until something else reads it.

    python eval/seed_memory.py                 # session 1: stores the maintainer's rule
    python eval/contradiction.py contradict    # session 2: the same maintainer, a new rule
    python eval/contradiction.py retrieve      # session 3: what does the store now say?

Session 2 writes the contradicting rule with Store.supersede, so the new record names
the old one in `supersedes` and the old one goes inactive, in one transaction. Session
3 asks the store for the maintainer's rule on duplicates and prints the ids it got
back: that list is `contradiction_test` in reports/LAB_04.json.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from state.store import REPOSITORY, Store      # noqa: E402

RULE = re.compile(r"duplicate", re.IGNORECASE)
MAINTAINER = "priya-s"
WHEN = "2026-10-24T11:40:00Z"
NEW_RULE = ("For duplicates: label them `duplicate`, link the original, and close them "
            "after 14 days. I no longer close them by hand.")


def rules(store: Store, every: bool = True) -> list[dict]:
    """Every record of the maintainer's rule on duplicates, active or not."""
    pool = store.records() if every else store.active(REPOSITORY)
    return [r for r in pool if r.get("source") == "user" and RULE.search(str(r.get("text") or ""))]


def contradict(store: Store) -> int:
    """Session 2. The same maintainer, a rule that contradicts the one in the store."""
    live = [r for r in rules(store) if r["active"]]
    if not live:
        print("no active rule from a maintainer about duplicates: run eval/seed_memory.py first")
        return 1
    if len(live) > 1:
        print(f"{len(live)} active rules about duplicates already: {[r['id'] for r in live]}; "
              "the store should hold one")
        return 1
    old = live[0]
    if NEW_RULE.strip() in [str(r["text"]).strip() for r in rules(store)]:
        print("the contradicting rule is already in the store, written once")
        return 0
    new_id = store.supersede(old["id"], {
        "repository": REPOSITORY, "issue": None, "kind": "procedural", "text": NEW_RULE,
        "source": "user", "stated_by": MAINTAINER, "written_at": WHEN,
        "origin": "ingested", "confirmed": True})
    print(f"session 2: {new_id} supersedes {old['id']}")
    print(f"  was:  {' '.join(str(old['text']).split())}")
    print(f"  now:  {' '.join(NEW_RULE.split())}")
    print(f"  {old['id']} is now active={store.by_id(old['id'])['active']}")
    return 0


def retrieve(store: Store) -> int:
    """Session 3. What a run that asks the store for the rule on duplicates is given."""
    got = [r for r in store.active(REPOSITORY) if r.get("source") == "user"
           and RULE.search(str(r.get("text") or ""))]
    print("session 3 retrieved: " + (", ".join(str(r["id"]) for r in got) or "nothing"))
    for r in got:
        print(f"  [{r['id']}] {r['kind']}, {r['source']}, stated by {r.get('stated_by')}, "
              f"written_at {r['written_at']}, supersedes {r.get('supersedes')}")
        print(f"      {' '.join(str(r['text']).split())}")
    inactive = [r for r in rules(store) if not r["active"]]
    print("inactive, kept: " + (", ".join(str(r["id"]) for r in inactive) or "none"))
    print("contradiction_test = " + repr([str(r["id"]) for r in got]))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("session", choices=["contradict", "retrieve"], help="session 2 or session 3")
    a = p.parse_args(argv)
    store = Store()
    print(f"store: {store.path}")
    return contradict(store) if a.session == "contradict" else retrieve(store)


if __name__ == "__main__":
    sys.exit(main())
