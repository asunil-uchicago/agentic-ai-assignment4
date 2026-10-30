"""Assignment 4, Part A: the checkpointer.

One run's state, saved on a durable backend so that a resume can load it after the
process is gone. Checkpoints are keyed by run_id: all of one run's checkpoints are
one line (a thread), oldest first.

The backend is SQLite, state/checkpoints.db, opened with `synchronous=FULL`, so a
committed row is on disk and not only in the operating system's cache. That is what
makes a sync save a sync save: save() returns after the commit, never before.

The three durability modes of slide 12 are modes of this file, not of the way that
calls it:

    sync    the row is committed before save() returns. Slowest, and the only mode
            that is safe in front of an irreversible step.
    async   save() hands the row to a worker thread and returns. A crash in the gap
            before the worker commits loses that checkpoint.
    exit    save() keeps the row in memory and writes every one of them when the
            process ends normally. A kill -9 loses all of them.

RepoMind runs in sync (reports/LAB_04.md, Part A): it has two irreversible steps,
and slide 12's rule is sync for any run with one.

A checkpoint is a dict of exactly harness.formats.CHECKPOINT_FIELDS and nothing
else. save() refuses anything else, and refuses a checkpoint that carries the
conversation, so that a mistake in ways/planned.py fails here rather than in the
checker. Part E's with-conversation copies go to their own file, written by
ways/planned.py, never through this class.
"""

from __future__ import annotations

import atexit
import json
import sqlite3
import threading
import time
from pathlib import Path

from harness import ROOT
from harness.formats import CHECKPOINT_FIELDS, DURABILITY_MODES

DEFAULT = ROOT / "state" / "checkpoints.db"

# A checkpoint holds the task's state, never the chat. Slide 14 calls the conversation
# optional; a task resumes from the five fields, and the conversation grows every turn.
CONVERSATION_KEYS = {"conversation", "messages", "history", "transcript"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id       INTEGER PRIMARY KEY,
    run_id   TEXT NOT NULL,
    step     TEXT,
    mode     TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    bytes    INTEGER NOT NULL,
    payload  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS checkpoints_by_run ON checkpoints (run_id, id);
"""


class CheckpointRefused(ValueError):
    """A checkpoint this file will not store: the wrong fields, or the conversation."""


def holds_conversation(node) -> bool:
    """A chat, anywhere in the checkpoint: a key that names one, or a list of messages."""
    if isinstance(node, dict):
        if CONVERSATION_KEYS & {str(k).lower() for k in node}:
            return True
        if "role" in node and "content" in node:
            return True
        return any(holds_conversation(v) for v in node.values())
    if isinstance(node, list):
        return any(holds_conversation(v) for v in node)
    return False


def problem(checkpoint) -> str | None:
    """Why this checkpoint cannot be saved, or None."""
    if not isinstance(checkpoint, dict):
        return f"a checkpoint is a dict of {list(CHECKPOINT_FIELDS)}, not {type(checkpoint).__name__}"
    if set(checkpoint) != set(CHECKPOINT_FIELDS):
        return f"fields {sorted(checkpoint)}; a checkpoint holds exactly {list(CHECKPOINT_FIELDS)}"
    if holds_conversation(checkpoint):
        return "it holds the conversation, which a task does not resume from (slide 14)"
    try:
        json.dumps(checkpoint)
    except (TypeError, ValueError) as exc:
        return f"it does not serialize to JSON ({exc}); no open files, connections or clients"
    return None


class Checkpointer:
    def __init__(self, path: Path | None = None, mode: str | None = None):
        """`mode` sets the default durability for save(); each call may override it."""
        self.path = Path(path or DEFAULT)
        self.mode = mode or "sync"
        self._lock = threading.Lock()
        self._pending: list[tuple] = []     # exit mode: rows waiting for the end of the run
        self._threads: list[threading.Thread] = []
        self._ready = False
        atexit.register(self.flush)

    # -- the database ----------------------------------------------------------------
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

    def _insert(self, row: tuple) -> None:
        with self._lock:
            db = self._connect()
            try:
                db.execute("INSERT INTO checkpoints (run_id, step, mode, saved_at, bytes, payload) "
                           "VALUES (?, ?, ?, ?, ?, ?)", row)
                db.commit()      # synchronous=FULL: on disk, not only in the page cache
            finally:
                db.close()

    # -- saving ----------------------------------------------------------------------
    def save(self, run_id: str, checkpoint: dict, *, step=None, mode: str = "sync") -> None:
        """One checkpoint of one run. In sync mode it is on disk before this returns."""
        mode = mode or self.mode
        if mode not in DURABILITY_MODES:
            raise ValueError(f"a checkpoint's mode is one of {DURABILITY_MODES}, not {mode!r}")
        why = problem(checkpoint)
        if why:
            raise CheckpointRefused(f"{run_id}: {why}")
        payload = json.dumps(checkpoint, ensure_ascii=False)
        row = (str(run_id), None if step is None else str(step), mode,
               time.strftime("%Y-%m-%dT%H:%M:%S"), len(payload.encode()), payload)
        if mode == "sync":
            self._insert(row)
        elif mode == "async":
            thread = threading.Thread(target=self._insert, args=(row,), daemon=True)
            self._threads.append(thread)
            thread.start()
        else:
            with self._lock:
                self._pending.append(row)

    def flush(self) -> None:
        """Write what exit mode kept, and wait for what async mode started. A kill -9
        never reaches this: that is the point of the two modes."""
        with self._lock:
            pending, self._pending = self._pending, []
        for row in pending:
            self._insert(row)
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []

    # -- reading -----------------------------------------------------------------------
    def _rows(self, sql: str, args: tuple) -> list[sqlite3.Row]:
        if not self.path.exists():
            return []
        db = self._connect()
        try:
            return list(db.execute(sql, args).fetchall())
        finally:
            db.close()

    def all(self, run_id: str) -> list[dict]:
        """Every checkpoint this run saved, oldest first."""
        return [json.loads(r["payload"]) for r in
                self._rows("SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY id", (str(run_id),))]

    def last(self, run_id: str) -> dict | None:
        """The run's latest checkpoint, or None when it saved none: a resume loads this."""
        rows = self._rows("SELECT payload FROM checkpoints WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                          (str(run_id),))
        return json.loads(rows[0]["payload"]) if rows else None

    def runs(self) -> list[str]:
        """Every run_id with a checkpoint, in the order each first saved one."""
        return [r["run_id"] for r in
                self._rows("SELECT run_id, MIN(id) AS first FROM checkpoints GROUP BY run_id ORDER BY first", ())]

    # -- Part E: what the checkpoints cost ------------------------------------------------
    def bytes_of(self, run_id: str) -> int:
        """The bytes this run's checkpoints take together, as they were saved."""
        rows = self._rows("SELECT SUM(bytes) AS total FROM checkpoints WHERE run_id = ?", (str(run_id),))
        return int(rows[0]["total"] or 0) if rows else 0

    def lines(self, run_id: str) -> list[dict]:
        """Each checkpoint's step, mode, time and size, for the report."""
        return [dict(r) for r in self._rows(
            "SELECT step, mode, saved_at, bytes FROM checkpoints WHERE run_id = ? ORDER BY id", (str(run_id),))]
