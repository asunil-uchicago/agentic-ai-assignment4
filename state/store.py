"""Assignment 4, Part D: the memory store.

A checkpoint belongs to one run and must be exact. This is the other thing: one
store, shared by every run, keyed by repository and issue, approximate, and
growing. state/write_policy.md decides what enters it; this file enforces what the
policy can express.

Every record carries its provenance, which is the decision most stores skip (slide
27): where it came from (`source`), when it was written down (`written_at`),
whether RepoMind worked it out or read it from outside (`origin`), and whether
anything confirmed it (`confirmed`). A record also has a kind, from slide 18's
taxonomy: episodic (what happened), semantic (what is true) or procedural (how to
do something here).

Contradictions supersede, they do not append (slide 25). supersede() writes the new
record with `supersedes` naming the old one and marks the old one inactive, in one
transaction, so exactly one of the pair is ever active.

Eviction is decided here and not later (slide 27). A store with no eviction grows
until retrieval fills with near-matches:

  - every retrieval first expires what is past its kind's lifetime: episodic
    EXPIRY_DAYS["episodic"] days, procedural longer, semantic longer still, because
    how often a kind's facts change is what should set its lifetime;
  - a rule a person with standing stated (`source: user`) never expires on age. It
    is wrong only when someone contradicts it, and then supersession, not a clock,
    is what retires it;
  - past MAX_ACTIVE active records the oldest are evicted, so retrieval has a
    ceiling whatever the write rate.

Nothing is deleted. An evicted record goes inactive and stays in the store, so
records() still shows how the store got to where it is.

The empty store Part D's second set of five runs needs is a switch, not a second
class: with EMPTY_STORE set, for_run(run_id) opens state/empty/<run_id>.db, a new
and empty file for each run. With it unset, Store() opens the real store, which is
how the checker always calls it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from harness import ROOT

DEFAULT = ROOT / "state" / "store.db"
EMPTY_DIR = ROOT / "state" / "empty"
REPOSITORY = "ledgerline"
KINDS = ("episodic", "semantic", "procedural")
ORIGINS = ("derived", "ingested")
SOURCE = re.compile(r"^(user|repo|memory|issue:\d+)$")

# Eviction: the size, and how long an entry of each kind lives (state/write_policy.md).
MAX_ACTIVE = 200
EXPIRY_DAYS = {"episodic": 120, "procedural": 270, "semantic": 540}
NEVER_EXPIRES = ("user",)     # a maintainer's rule goes only by supersession

FIELDS = ("id", "repository", "issue", "kind", "text", "source", "written_at", "origin",
          "confirmed", "active", "supersedes", "stated_by")

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    repository TEXT NOT NULL,
    issue      INTEGER,
    kind       TEXT NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    written_at TEXT NOT NULL,
    origin     TEXT NOT NULL,
    confirmed  INTEGER NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    supersedes TEXT,
    stated_by  TEXT
);
CREATE INDEX IF NOT EXISTS records_by_scope ON records (repository, active, issue);
"""


class RecordRefused(ValueError):
    """A record the write policy cannot express: a missing or unknown provenance field."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _moment(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def age_days(written_at, now: datetime | None = None) -> float | None:
    when = _moment(written_at)
    if when is None:
        return None
    return ((now or datetime.now(timezone.utc)) - when).total_seconds() / 86400


def problem(record) -> str | None:
    """Why this record cannot be written, or None. Provenance is not optional."""
    if not isinstance(record, dict):
        return f"a record is a dict, not {type(record).__name__}"
    if not str(record.get("text") or "").strip():
        return "a record needs text: the memory, in words"
    if record.get("kind") not in KINDS:
        return f"kind {record.get('kind')!r}; one of {KINDS} (slide 18)"
    if not SOURCE.match(str(record.get("source") or "")):
        return f"source {record.get('source')!r}; one of user, issue:<n>, repo, memory"
    if record.get("origin") not in ORIGINS:
        return f"origin {record.get('origin')!r}; derived (RepoMind worked it out) or ingested (read from outside)"
    if not isinstance(record.get("confirmed"), bool):
        return f"confirmed {record.get('confirmed')!r}; true or false, never missing"
    if record.get("written_at") is not None and _moment(record["written_at"]) is None:
        return f"written_at {record.get('written_at')!r} is not ISO 8601"
    return None


def as_record(row) -> dict:
    """One row as the checker reads it: SQLite's 1 and 0 back as true and false."""
    out = {k: row[k] for k in FIELDS}
    out["confirmed"] = bool(out["confirmed"])
    out["active"] = bool(out["active"])
    return out


class Store:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT)
        self._lock = threading.Lock()
        self._ready = False

    # -- the database -----------------------------------------------------------------
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        if not self._ready:
            db.executescript(SCHEMA)
            db.commit()
            self._ready = True
        return db

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        db = self._connect()
        try:
            return list(db.execute(sql, args).fetchall())
        finally:
            db.close()

    def _next_id(self, db) -> str:
        used = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        while True:
            used += 1
            candidate = f"mem-{used:04d}"
            if db.execute("SELECT 1 FROM records WHERE id = ?", (candidate,)).fetchone() is None:
                return candidate

    def _insert(self, db, record: dict, *, supersedes=None) -> str:
        why = problem(record)
        if why:
            raise RecordRefused(why)
        rid = str(record.get("id") or self._next_id(db))
        db.execute(
            "INSERT INTO records (id, repository, issue, kind, text, source, written_at, origin, confirmed, "
            "active, supersedes, stated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, str(record.get("repository") or REPOSITORY),
             None if record.get("issue") in (None, "") else int(record["issue"]),
             record["kind"], str(record["text"]).strip(), str(record["source"]),
             str(record.get("written_at") or now_iso()), record["origin"],
             int(bool(record["confirmed"])), 1,
             None if supersedes is None else str(supersedes),
             record.get("stated_by")))
        return rid

    # -- writing -------------------------------------------------------------------------
    def write(self, record: dict) -> str:
        """A new record. Returns its id. The policy decides whether to call this at all."""
        with self._lock:
            db = self._connect()
            try:
                rid = self._insert(db, record, supersedes=record.get("supersedes"))
                db.commit()
                return rid
            finally:
                db.close()

    def supersede(self, old_id, record: dict) -> str:
        """Replace old_id: the new record names it, the old one goes inactive, in one
        transaction, so exactly one of the two is ever active (slide 25)."""
        with self._lock:
            db = self._connect()
            try:
                if db.execute("SELECT 1 FROM records WHERE id = ?", (str(old_id),)).fetchone() is None:
                    raise RecordRefused(f"no record {old_id!r} to supersede")
                rid = self._insert(db, record, supersedes=old_id)
                db.execute("UPDATE records SET active = 0 WHERE id = ?", (str(old_id),))
                db.commit()
                return rid
            finally:
                db.close()

    def deactivate(self, record_id, reason: str = "") -> None:
        with self._lock:
            db = self._connect()
            try:
                db.execute("UPDATE records SET active = 0 WHERE id = ?", (str(record_id),))
                db.commit()
            finally:
                db.close()

    # -- eviction, on every retrieval -------------------------------------------------------
    def evict(self, now: datetime | None = None) -> list[str]:
        """Expire what is past its kind's lifetime, then cut the rest down to MAX_ACTIVE.
        Returns the ids that went inactive. Nothing is deleted."""
        now = now or datetime.now(timezone.utc)
        live = [as_record(r) for r in self._rows("SELECT * FROM records WHERE active = 1 ORDER BY seq")]
        gone = []
        for record in live:
            if record["source"] in NEVER_EXPIRES:
                continue
            age = age_days(record["written_at"], now)
            if age is not None and age > EXPIRY_DAYS.get(record["kind"], EXPIRY_DAYS["semantic"]):
                gone.append(record["id"])
        kept = [r for r in live if r["id"] not in gone]
        if len(kept) > MAX_ACTIVE:
            oldest = sorted(kept, key=lambda r: (_moment(r["written_at"]) or now, r["id"]))
            gone += [r["id"] for r in oldest[:len(kept) - MAX_ACTIVE]]
        if gone:
            with self._lock:
                db = self._connect()
                try:
                    db.executemany("UPDATE records SET active = 0 WHERE id = ?", [(i,) for i in gone])
                    db.commit()
                finally:
                    db.close()
        return gone

    # -- reading ---------------------------------------------------------------------------
    def active(self, repository: str, issue: int | None = None) -> list[dict]:
        """The active records for retrieval: with no issue, the repository's; with one,
        that issue's and every repository-wide record. Eviction runs first."""
        self.evict()
        if issue is None:
            sql = "SELECT * FROM records WHERE active = 1 AND repository = ? ORDER BY seq"
            args: tuple = (str(repository),)
        else:
            sql = ("SELECT * FROM records WHERE active = 1 AND repository = ? AND (issue IS NULL OR issue = ?) "
                   "ORDER BY seq")
            args = (str(repository), int(issue))
        return [as_record(r) for r in self._rows(sql, args)]

    def records(self) -> list[dict]:
        """Every record, active or not, oldest first."""
        return [as_record(r) for r in self._rows("SELECT * FROM records ORDER BY seq")]

    def by_id(self, record_id) -> dict | None:
        rows = self._rows("SELECT * FROM records WHERE id = ?", (str(record_id),))
        return as_record(rows[0]) if rows else None


# -- the empty-store switch, and how a run reads the store --------------------------------------

def empty_store() -> bool:
    return os.environ.get("EMPTY_STORE", "").strip().lower() not in ("", "0", "off", "false", "no")


def for_run(run_id: str) -> Store:
    """The store a run opens: the real one, or, with EMPTY_STORE set, a new and empty
    file of its own, so that Part D's second set of five runs starts from nothing."""
    return Store(EMPTY_DIR / f"{run_id}.db") if empty_store() else Store()


UNTRUSTED = re.compile(r"^issue:\d+$")


def trusted(records: list[dict]) -> list[dict]:
    """The records a model may be shown. A record whose source is issue:<n> is text
    anyone can write: it is retrieved and logged, and never rendered into a prompt,
    the way Assignment 2 kept the issue body out of the planner's context."""
    return [r for r in records if not UNTRUSTED.match(str(r.get("source") or ""))]


def render(records: list[dict], now: datetime | None = None) -> str:
    """What the model sees of the store: one line a record, with its age and its source
    visible, so that a rule a maintainer stated and a guess RepoMind made do not read
    alike."""
    lines = []
    for r in trusted(records):
        age = age_days(r["written_at"], now)
        when = f"{age:.0f} days old" if age is not None else "undated"
        who = f", stated by {r['stated_by']}" if r.get("stated_by") else ""
        mark = "confirmed" if r.get("confirmed") else "unconfirmed"
        lines.append(f"- [{r['id']}] {r['kind']}, source {r['source']}{who}, {when}, {mark}, "
                     f"{r['origin']}: {' '.join(str(r['text']).split())}")
    return "\n".join(lines)


def summary() -> str:
    """The store on one line, for a script or a log."""
    store = Store()
    every = store.records()
    live = [r for r in every if r["active"]]
    return json.dumps({"path": str(store.path), "records": len(every), "active": len(live)})


if __name__ == "__main__":
    print(summary())
