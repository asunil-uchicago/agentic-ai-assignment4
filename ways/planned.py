"""Assignment 3's working solution, over Assignment 2's planned way: the same plan,
boundary, checkpoint and second pass, with Assignment 3's tools and the PR step.
Assignment 4 adds what survives a crash and what survives a run.

Assignment 3 changes four things here:
  - Tools are found by role (harness.roles), never by name: the names are the
    team's own (tools/roles.yaml).
  - The plan ends with the PR and then the comment (slide 41): the schema check
    wants exactly one comment step, the last, and exactly one PR step, right before
    it. A PR can be closed; a comment, once read, cannot be taken back. The comment
    is drafted, and passes the second pass, before the first write; the PR opens
    only when that draft names a file, and when it says none the step is skipped
    (status "skipped"), a condition the executor applies, not the model. The draft
    posts last.
  - The PR's issue and branch are the code's: repomind/issue-<n>, whatever the plan
    or the model wrote. The model fills only its title and body.
  - A write the registry cannot confirm (tools.Unverifiable, the third exit) ends
    the run escalated: a person on review duty decides.

Assignment 4 adds five things, and changes no rule above:

  - **Checkpoints** (Part A), saved through state/checkpointer.py in DURABILITY
    mode, which is sync: this run has two irreversible steps, and slide 12's rule
    is sync for any run with one. They go after each read worth not repeating (the
    search and the file read, each of which costs a fill call) and immediately
    before each write, after its arguments are filled and before the call, so that
    the arguments the key is hashed from are on disk before the write runs. The
    issue read gets none: repeating it costs one tool call and no model tokens.
    Assignment 2's Executor.checkpoint is a different thing, the point where the
    plan may be revised; this one is save_state.
  - **Resume** (Part B). run() asks the checkpointer for this run_id's last
    checkpoint. There is one only after a crash, and then the run replays from it:
    a step that completed is skipped and its recorded result reused, and a write is
    replayed with the arguments the checkpoint saved, never with new ones from the
    model, because a new argument is a new key and a new key is a second write. The
    step that was running at the crash is replayed through the registry, whose
    check before every attempt is what settles whether it already happened.
  - **The store** (Part D), state/store.py, read before the plan is written, so the
    store_read line comes before the first search, and written at the end of a run
    that decided. Records sourced issue:<n> are retrieved and logged and never
    rendered into a prompt.
  - **Part F's switches**, read from the environment: TELL_FACT (off, prominent,
    buried or early) tells the run a maintainer's rule and decides where it sits,
    COMPACT_AT compacts the fill history at a low threshold, and FACT_NOTE keeps
    the fact in a structured note outside that history. All three are off unless
    set, so every other run of this way is the run Assignment 3 left.
  - **Part E's shadow copy**: with CP_SHADOW set, every checkpoint is written a
    second time, with the conversation, to its own file. Never to the checkpoint
    database.

The boundary: the executor runs the plan's steps in order and nothing else. Where a
step's arguments are "$fill", one model call fills them, offered that step's tool
alone; a call to any other tool is refused, traced as off_plan, and never runs.
Two tries without an on-plan call halt the run as escalated.

The planner's output is checked against agent/plan_schema.json (harness.schema)
before the four checks, and so is the rule about its comment step (above): a
vendor can accept "strict" and not enforce it, and a plan with no comment step
passes all four checks and posts nothing. A plan that breaks either is rejected
like one that fails a check.

The checkpoint, a check in code: after the first search step, and after any search
step a revision added or changed, until MAX_REVISIONS is spent, the search returned
at least one source file: a .ts or .tsx file under src/, not only documentation
(README.md, CHANGELOG.md, docs/) or translations (src/i18n/*.json). A revision never
changes a step that is done; it may change a failed step, which then runs again.

Part G's ways/notes.py extends this file without changing it: it subclasses
Executor and overrides its hooks, record() (after every step), context() (what
every executor call sees) and the class attribute critique, and asks its questions
once run() has returned, however the run ended.

The second pass, before the first write, in ctx.critique's mode: external runs
agent.critique.verify, code; self asks the model with agent.critique.self_critique.
A failing draft with a round left is rewritten by one call; a draft still failing
in the last round is held, and the run escalates having written nothing. One
comment, after the last round and the PR.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
from datetime import datetime, timezone

from agent.critique import self_critique, verify
from agent.diff import step_args
from agent.plan import SCHEMA, make_plan, revise_plan
from agent.validator import validate
from harness import ROOT
from harness.formats import FILL, format_triage_comment, parse_triage_comment, result_paths, squeeze
from harness.roles import arg, tool_for
from harness.schema import errors as schema_errors
from state import store as memory
from state.checkpointer import Checkpointer
from tools import Unverifiable

MAX_REPLANS = 2       # three planner calls in all; a fourth on the same request is the same coin, so a person looks
MAX_REVISIONS = 2     # a repair of the first search, and one more if the repair also finds nothing
MAX_ROUNDS = 2        # check, rewrite once, check again: slide 43's limit, the same in both modes
MAX_FILL_TRIES = 2    # a step the model will not fill on plan, twice, halts the run
CHECKPOINT = "after_first_search"
SOURCE = re.compile(r"^src/(?!i18n/).+\.tsx?$")   # code, not documentation or translations
CONTEXT_CHARS = 6000  # of each earlier result, in a fill call

# -- Assignment 4 ---------------------------------------------------------------------------------

DURABILITY = "sync"                     # one mode for the run (slide 12): it has irreversible steps
SAVE_AFTER = ("search", "read_file")    # the reads worth not repeating: each costs a fill call
SHADOW = ROOT / "state" / "checkpoints_with_conversation.jsonl"   # Part E only; never the checkpoint database

# Part F. The fact a run can be told, as a maintainer stated it, and when. It changes
# what the comment says and nothing else: a rule that also said where fixes go would
# make the model refuse the pull request.
MAINTAINER = "priya-s"
FACT_WHEN = "2026-10-20T09:15:00Z"
FACT = ("Ledgerline's maintainers label every report about the Export button as feature: "
        "they plan its fixes on the feature board.")
FACT_PLACES = ("off", "prominent", "buried", "early")

# Part F, the attention case: Ledgerline's own files, with the fact a plain line halfway
# through them and no heading to mark it. The answer file of either development issue is
# left out, so the filler steers nothing but where the fact sits.
FILLER_FILES = ("src/api/reports.ts", "src/ingest/queue.ts", "src/db/repos/reportRepo.ts",
                "src/ingest/worker.ts", "src/api/uploads.ts", "src/api/auth.ts", "src/config.ts",
                "src/db/repos/userRepo.ts", "src/db/repos/transactionRepo.ts")

SUMMARY = "## Summary of the earlier steps"
KEEP = 1              # turns kept as they were when the history is compacted


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def flag(name: str) -> bool:
    return env(name).lower() not in ("", "0", "off", "false", "no")


def fact_place() -> str:
    """TELL_FACT: off, or where the maintainer's note sits in a fill call."""
    place = env("TELL_FACT", "off").lower()
    if place not in FACT_PLACES:
        raise SystemExit(f"TELL_FACT={place!r}: one of {FACT_PLACES}, or unset")
    return place


def compact_at() -> int:
    """COMPACT_AT: the token threshold the fill history is compacted at, or 0 for never."""
    try:
        return max(0, int(env("COMPACT_AT", "0")))
    except ValueError:
        raise SystemExit("COMPACT_AT: a number of tokens, or unset")


def tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_filler: str | None = None


def filler() -> str:
    """Ledgerline's own files, concatenated, with the fact halfway through: the same text
    every time, so the two attention runs differ only in where the fact sits."""
    global _filler
    if _filler is None:
        repo = ROOT / "fixtures" / "repo"
        parts = [f"{name}\n{(repo / name).read_text()}" for name in FILLER_FILES]
        text = "\n\n".join(parts)
        cut = text.find("\n", len(text) // 2) + 1
        _filler = text[:cut] + FACT + "\n" + text[cut:]
    return _filler


# The arguments the plan never knows and the step that runs must write (prompts/planner.md).
# The reviser is shown neither the issue nor the arguments a step ran with, so a value it
# writes into one of these is invented; "$fill" is restored and the executor writes it.
EXECUTOR_ARGS = {"search": ("query",), "read_file": ("path",),
                 "comment": ("body",), "open_pr": ("title", "body")}


def role(name: str | None) -> str | None:
    """The role a step's tool plays, or None."""
    return next((r for r in ("read_issue", "search", "read_file", "comment", "open_pr") if tool_for(r) == name), None)


def allows_null(spec: dict) -> bool:
    """Whether a property's schema takes null: "type": [..., "null"], or a null branch."""
    kind = spec.get("type")
    return ("null" in kind if isinstance(kind, list) else kind == "null") or any(
        isinstance(b, dict) and allows_null(b) for b in spec.get("anyOf") or spec.get("oneOf") or [])


class Recording:
    """Part E only. A client that keeps every message this way has sent the model, so
    that a checkpoint can be weighed with and without the conversation. It is wrapped
    around ctx.client only when CP_SHADOW is set; the run's totals are still the
    runner's client's, which this one calls."""

    def __init__(self, client):
        self._client = client
        self.turns: list[dict] = []

    def __getattr__(self, name):
        return getattr(self._client, name)

    def create(self, **kwargs):
        response = self._client.create(**kwargs)
        if kwargs.get("system"):
            self.turns.append({"role": "system", "content": kwargs["system"]})
        self.turns += [{"role": m.get("role"), "content": m.get("content")} for m in kwargs.get("messages") or []]
        self.turns.append({"role": "assistant", "content": response.content})
        return response


def shadow_client(ctx):
    """Replace ctx.client with a recording one, for Part E's measurement only."""
    if not flag("CP_SHADOW"):
        return None
    ctx.client = Recording(ctx.client)
    return ctx.client


# -- the store, before the plan and after the run ---------------------------------------------------

def retrieve(ctx, store) -> list[dict]:
    """The active records for this repository and this issue, read before the plan is
    written, so that the store_read line comes before the run's first search."""
    records = store.active(memory.REPOSITORY, ctx.issue_number)
    ctx.trace.event("store_read", issue=ctx.issue_number, records=[r["id"] for r in records])
    return records


def already_held(store, text: str) -> bool:
    want = squeeze(text)
    return any(squeeze(r["text"]) == want for r in store.records() if r["active"])


def stated_rule() -> dict | None:
    """Part F. A run told the maintainer's rule keeps it as it was stated, word for word,
    with who stated it and when: the write policy's "stated rather than inferred"."""
    if fact_place() == "off":
        return None
    return {"repository": memory.REPOSITORY, "issue": None, "kind": "procedural", "text": FACT,
            "source": "user", "stated_by": MAINTAINER, "written_at": FACT_WHEN,
            "origin": "ingested", "confirmed": True}


def remember(ctx, executor, store) -> None:
    """The write policy's "when": at the end of a run, once it has decided, and never
    mid-run on a guess the next step might overturn."""
    written = []
    rule = stated_rule()
    if rule is not None and not already_held(store, rule["text"]):
        written.append(store.write(rule))
    findings = getattr(executor, "findings", None) or {}
    if executor is not None and executor.posted and findings.get("label"):
        text = (f"On {today()} RepoMind triaged issue {ctx.issue_number} as label: {findings['label']}, "
                f"file: {findings.get('file') or 'none'}, and posted the comment that says so.")
        if not already_held(store, text):
            written.append(store.write({
                "repository": memory.REPOSITORY, "issue": ctx.issue_number, "kind": "episodic", "text": text,
                "source": "memory", "origin": "derived", "confirmed": True}))
    if written:
        ctx.trace.event("store_write", issue=ctx.issue_number, records=written)


# -- the run ------------------------------------------------------------------------------------------

def run(ctx) -> None:
    shadow_client(ctx)
    store = memory.for_run(ctx.run_id)
    records = retrieve(ctx, store)
    saver = Checkpointer()
    saved = saver.last(ctx.run_id)
    if saved is not None:                         # a resume: replay from the last checkpoint
        executor = Executor(ctx, saved.get("plan") or {"meta": {}, "steps": []},
                            store=store, memory=records, saver=saver)
        executor.restore(saved)
    else:
        plan = plan_until_valid(ctx)
        if plan is None:
            return                                # escalated with nothing decided: the store stays as it was
        ctx.trace.plan(1, plan)
        executor = Executor(ctx, plan, store=store, memory=records, saver=saver)
    executor.run()
    remember(ctx, executor, store)


def schema_check(ctx, plan: dict, version: int) -> list[dict]:
    """The plan as the planner's tool returned it, against agent/plan_schema.json. A
    failure is written as a schema line, and comes back as failures for a re-plan."""
    problems = schema_errors({k: v for k, v in plan.items() if k != "meta"}, SCHEMA)
    steps = [s for s in plan.get("steps") or [] if isinstance(s, dict)]
    roles = [role(s.get("tool")) for s in steps]
    if roles.count("comment") != 1 or roles[-1] != "comment":
        problems.append(f"the comment step comes once, last, with the PR step right before it; this plan's steps are {roles}")
    elif roles.count("open_pr") != 1 or roles[-2] != "open_pr":
        problems.append("the PR step comes exactly once, right before the comment step")
    if problems:
        ctx.trace.event("schema", version=version, errors=problems[:20])
    return [{"step": None, "check": "schema", "error": e} for e in problems[:5]]


def wants_fill(value) -> bool:
    """True when the plan left this argument for a later call to write. The planner is
    told to write "$fill" and nothing else, but a model that wraps it in a template of
    its own, such as "File: $fill", has left the same hole: the marker counts wherever
    it sits. Taking only a leading marker is how a plan's template reaches the tracker
    unfilled, which no later step can undo."""
    return FILL in str(value)


def named_tools(plan: dict) -> dict:
    """Strip a namespace from each step's tool name. Some OpenAI-compatible models write
    a tool as "functions.issues_read", the form their own API uses, and every check here
    reads the bare name, so the plan is rejected five times over for a prefix. The name
    is only accepted when its last segment is one of this repository's tools."""
    for step in plan.get("steps") or []:
        name = step.get("tool")
        if isinstance(name, str) and "." in name and role(name) is None and role(name.rsplit(".", 1)[-1]):
            step["tool"] = name.rsplit(".", 1)[-1]
    return plan


def plan_until_valid(ctx) -> dict | None:
    """The reject exit: re-plan up to MAX_REPLANS times, then escalate. Never execute a rejected plan."""
    failures = None
    for attempt in range(1, MAX_REPLANS + 2):
        plan = named_tools(make_plan(ctx.client, ctx.issue_number, ctx.tools.schemas, trace=ctx.trace, failures=failures))
        broken = schema_check(ctx, plan, 1)
        if broken:                                # rejected before the four checks: it never runs
            failures = broken
            continue
        verdict = validate(plan)
        ctx.trace.event("validation", version=1, attempt=attempt, valid=verdict["valid"],
                        failures=verdict["failures"])
        if verdict["valid"]:
            return plan
        failures = verdict["failures"]
    ctx.trace.end("escalated", detail=f"no valid plan in {MAX_REPLANS + 1} attempts; last failures {failures}")
    return None


class Executor:
    critique = True   # the notes way turns the second pass off

    def __init__(self, ctx, plan: dict, *, store=None, memory=None, saver=None):
        self.ctx = ctx
        self.plan = copy.deepcopy(plan)
        self.version = 1
        self.results: list[dict] = []    # step_results (checks/FORMATS.md, section 10.3), in the order steps ran
        self.outputs: dict[str, str] = {}  # each step's tool output, for later calls; never shown to the reviser
        self.calls = 0                   # execute and critique calls so far: the step number of the next lines
        self.revisions = 0
        self.revised: set[str] = set()   # ids of steps a revision added or changed: the checkpoint follows them
        self.ran: set[str] = set()       # ids of steps that ran and are not to run again
        # Assignment 4
        self.store = store
        self.memory = list(memory or [])
        self.saver = saver or Checkpointer()
        self.completed: list[dict] = []  # one entry per tool call that reached its tool, with its key
        self.findings: dict = {"label": None, "file": None, "comment_args": None, "pr_args": None}
        self.posted = False              # the comment is in the tracker, this run's or a replay's
        self.resumed = False
        self.fill_turns: list[str] = []       # the fill history, compacted past COMPACT_AT
        self.compactions = 0
        self.place = fact_place()
        self.compact_at = compact_at()
        if self.place == "early":        # the fact in an early turn, where a summary can drop it
            self.fill_turns.append(f"## A note from {MAINTAINER} (maintainer), {FACT_WHEN[:10]}\n{FACT}")

    # -- the plan, in order ------------------------------------------------------------
    def next_step(self) -> dict | None:
        return next((s for s in self.plan.get("steps") or [] if str(s.get("id")) not in self.ran), None)

    def run(self) -> None:
        while True:
            step = self.next_step()
            if step is None:
                return                            # the plan is finished; the runner writes completed
            if role(step.get("tool")) in ("open_pr", "comment") and self.draft is None:
                if not self.prepare(self.comment_step()):
                    return                        # held, or not filled: nothing was written
            if role(step.get("tool")) == "open_pr":
                if not self.pull_request(step):
                    return
                continue
            if role(step.get("tool")) == "comment":
                if not self.post(step):
                    return
                continue
            first = not any(role(r["tool"]) == "search" for r in self.results)
            result = self.run_step(step)
            if result is None:
                return                            # halted, and ended escalated
            if role(step.get("tool")) == "search" and (first or str(step.get("id")) in self.revised):
                self.checkpoint(CHECKPOINT, any(SOURCE.match(p) for p in result.get("paths") or []))
            if role(step.get("tool")) in SAVE_AFTER:
                self.save_state(step.get("id"), running=self.next_id())

    def next_id(self):
        step = self.next_step()
        return None if step is None else step.get("id")

    # -- Assignment 4: the checkpoint ----------------------------------------------------
    def statuses(self, running=None) -> dict:
        """Each plan step's status, keyed by its id; the one in hand is running."""
        out = {str(s.get("id")): s.get("status") or "pending" for s in self.plan.get("steps") or []}
        if running is not None and out.get(str(running), "pending") == "pending":
            out[str(running)] = "running"
        return out

    def save_state(self, step, *, running=None, mode: str = DURABILITY) -> None:
        """Slide 14's five fields, and nothing else. In sync mode the checkpoint is on
        disk before this returns, and the trace line is written after it, so a reader of
        the trace knows the save had already happened when the next line was written."""
        plan = {**self.plan, "meta": {**(self.plan.get("meta") or {}), "version": self.version}}
        checkpoint = {
            "plan": plan,
            "step_status": self.statuses(running),
            "completed_calls": [dict(c) for c in self.completed],
            "findings": {k: v for k, v in self.findings.items() if v is not None},
            "current_step": None if running is None else str(running),
        }
        self.saver.save(self.ctx.run_id, checkpoint, step=step, mode=mode)
        self.ctx.trace.event("checkpoint", step=step, mode=mode)
        self.shadow(checkpoint, step, mode)

    def shadow(self, checkpoint: dict, step, mode: str) -> None:
        """Part E. The same checkpoint with the conversation, to its own file: every
        message this way has sent the model so far. Never the checkpoint database, which
        refuses one that holds a conversation."""
        turns = getattr(self.ctx.client, "turns", None)
        if turns is None:
            return
        payload = {**checkpoint, "conversation": [dict(t) for t in turns]}
        line = {"run_id": self.ctx.run_id, "step": None if step is None else str(step), "mode": mode,
                "structured_bytes": len(json.dumps(checkpoint, ensure_ascii=False).encode()),
                "bytes": len(json.dumps(payload, ensure_ascii=False, default=str).encode()),
                "checkpoint": payload}
        SHADOW.parent.mkdir(parents=True, exist_ok=True)
        with SHADOW.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")

    def restore(self, saved: dict) -> None:
        """Replay from the last checkpoint: every step it says finished is skipped and its
        recorded result reused, and the draft comes back as it was, so no model call
        rewrites a write's arguments into a new key."""
        self.resumed = True
        self.version = int(((saved.get("plan") or {}).get("meta") or {}).get("version") or 1)
        status = saved.get("step_status") or {}
        self.completed = [dict(c) for c in saved.get("completed_calls") or []]
        self.findings = {**self.findings, **(saved.get("findings") or {})}
        for call in self.completed:
            sid = str(call.get("step"))
            self.outputs[sid] = str(call.get("result") or "")
            self.results.append({"id": call.get("step"), "tool": call.get("tool"),
                                 "status": status.get(sid, "done"), "count": None, "paths": [],
                                 "error_code": None, "args": call.get("args") or {}})
            self.fill_turns.append(f"## Step {call.get('step')}, {call.get('tool')}, {status.get(sid, 'done')}\n"
                              f"{self.outputs[sid][:CONTEXT_CHARS]}")
        for step in self.plan.get("steps") or []:
            sid = str(step.get("id"))
            if status.get(sid) in ("done", "failed", "skipped"):
                step["status"] = status[sid]
                self.ran.add(sid)                 # a step that ran is not run again; only the one in hand is
        if self.findings.get("comment_args"):
            self.draft = dict(self.findings["comment_args"])
            self.drafted_file = self.findings.get("file") or "none"
        self.ctx.trace.event("replay", from_step=saved.get("current_step"),
                             skipped=sorted(self.ran), calls=len(self.completed),
                             findings=sorted(k for k, v in self.findings.items() if v is not None))

    # -- one step ------------------------------------------------------------------------
    def run_step(self, step: dict) -> dict | None:
        tool, args = step.get("tool"), step_args(step)
        if not tool:
            return self.record(step, args, None, "no_tool")
        args = self.filled(step, args)
        if args is None:
            return None
        result = self.call(step, tool, args)
        return None if result is None else self.record(step, args, result)

    def call(self, step: dict, tool: str, args: dict):
        """One tool call for a plan step; None when the registry could not confirm a
        write, and the run has ended escalated (the third exit)."""
        try:
            return self.ctx.call_tool(self.calls, tool, args, plan_step=step.get("id"))
        except Unverifiable as exc:
            self.record(step, args, None, "unverifiable")
            self.ctx.trace.end("escalated", detail=f"step {step.get('id')}: {exc}; a person on review duty decides")
            return None

    def filled(self, step: dict, args: dict) -> dict | None:
        to_fill = [k for k, v in args.items() if wants_fill(v)]   # "$fill", "$fill: a note", or a template round it
        if not to_fill:
            return args
        filled = self.fill(step, args, to_fill)
        if filled is None:
            self.record(step, args, None, "off_plan")
            self.ctx.trace.end("escalated", detail=f"step {step.get('id')}: no on-plan call in {MAX_FILL_TRIES} tries")
        return filled

    def fill(self, step: dict, args: dict, keys: list[str], *, system: str = "executor.md",
             purpose: str = "execute", note: str = "") -> dict | None:
        """One model call fills a step's "$fill" arguments, offered the step's tool alone. It
        keeps every argument the model gave that the schema knows, the ones the plan never
        mentioned included, and a null where the schema allows one (Part A's required and
        nullable fields); the plan's own values win."""
        schema = next((t for t in self.ctx.tools.schemas if t["name"] == step["tool"]), None)
        if schema is None:
            return None
        fixed = {k: v for k, v in args.items() if not wants_fill(v)}
        props = (schema.get("input_schema") or {}).get("properties") or {}
        nullable = {k for k, s in props.items() if isinstance(s, dict) and allows_null(s)}
        for _ in range(MAX_FILL_TRIES):
            self.calls += 1
            response = self.ctx.client.create(system=self.ctx.prompt(system), tools=[schema],
                                              messages=[{"role": "user", "content": self.fill_request(step, fixed, keys, note)}])
            self.ctx.trace.model_call(self.calls, response, purpose=purpose)
            for call in response.tool_calls:
                if call.name != step["tool"]:
                    self.refuse(step, call)       # the boundary: never run, always traced
                    continue
                filled = {**{k: v for k, v in call.input.items() if k in props}, **fixed}   # the plan's values win
                if any(k not in filled or filled[k] == "" or wants_fill(filled[k])
                       or (filled[k] is None and k not in nullable)
                       for k in keys):
                    continue
                if role(step["tool"]) == "comment":
                    body = arg("comment", "body")
                    label, file = parse_triage_comment(str(filled[body]))
                    filled[body] = format_triage_comment(label or "bug", file)
                return filled
        return None

    def refuse(self, step: dict, call) -> None:
        self.ctx.trace.event("off_plan", step=step.get("id"), tool=call.name, input=call.input, version=self.version)

    # -- what a fill call sees ---------------------------------------------------------------
    def compact(self) -> None:
        """Part F. Past COMPACT_AT tokens, every turn but the last KEEP becomes one
        model-written summary of the tools called and the paths they returned. A rule
        stated in an earlier turn does not survive that summary, which is the point of
        the case: the fix is a note outside the history, not a bigger window."""
        if not self.compact_at:
            return
        size = tokens("\n\n".join(self.fill_turns))
        older, recent = self.fill_turns[:-KEEP], self.fill_turns[-KEEP:]
        if not older or (len(older) == 1 and older[0].startswith(SUMMARY)) or size <= self.compact_at:
            return
        self.calls += 1
        response = self.ctx.client.create(system=self.ctx.prompt("compact_steps.md"),
                                          messages=[{"role": "user", "content": "\n\n".join(older)}])
        self.ctx.trace.model_call(self.calls, response)
        self.fill_turns = [f"{SUMMARY}\n{response.text}"] + recent
        self.compactions += 1
        self.ctx.trace.event("compaction", threshold=self.compact_at, tokens=size,
                             summarized=len(older), kept=len(recent))

    def context(self) -> list[str]:
        """What the run has gathered so far, one block per step, for a fill or a critique."""
        if self.compact_at:
            self.compact()
            history = list(self.fill_turns)
        else:
            history = [f"## Step {r['id']}, {r['tool']}, {r['status']}\n{self.outputs.get(str(r['id']), '')[:CONTEXT_CHARS]}"
                       for r in self.results]
        if self.place == "buried":
            history = [f"Background, from Ledgerline's own files:\n\n{filler()}"] + history
        return history

    def standing(self) -> list[str]:
        """What is kept outside the history and shown on every call: the memory store's
        active records, and Part F's structured note."""
        blocks = []
        rendered = memory.render(self.memory)
        if rendered:
            blocks.append(self.ctx.prompt("memory.md").strip() + "\n\n" + rendered)
        if self.place != "off" and flag("FACT_NOTE"):
            blocks.append("A standing note, kept outside this conversation and shown on every call.\n"
                          f"{MAINTAINER} (maintainer), {FACT_WHEN[:10]}: {FACT}")
        if self.place == "prominent":
            blocks.append(f"A note from Ledgerline's maintainers, not from the issue. {MAINTAINER} "
                          f"(maintainer), {FACT_WHEN[:10]}: {FACT}")
        return blocks

    def fill_request(self, step: dict, fixed: dict, keys: list[str], note: str = "") -> str:
        parts = [f"Triage issue #{self.ctx.issue_number}. You are running step {step.get('id')} of a fixed "
                 f"plan: {step.get('action')}. Call {step['tool']} once. Fill in: {', '.join(keys)}. "
                 f"The plan fixes: {json.dumps(fixed)}."]
        if role(step["tool"]) == "comment":
            parts.append("The comment's first two lines must be:\nlabel: <bug | feature | question | docs>\n"
                         "file: <path relative to the repository root, or none>")
        if role(step["tool"]) == "open_pr":
            parts.append(f"The triage comment's draft names {self.drafted_file}. The pull request's title says what "
                         "the change fixes; its body names that file first, then proposes the change in prose.")
        notes = {k: str(v)[len(FILL):].lstrip(": ").strip() for k, v in step_args(step).items()
                 if str(v).startswith(FILL) and str(v).strip() != FILL}
        if notes:
            parts.append("The plan's notes on them: " + "; ".join(f"{k}: {v}" for k, v in notes.items()))
        if note:
            parts.append(note)
        return "\n\n".join(parts + self.context() + self.standing())

    def record(self, step: dict, args: dict, result, error: str | None = None) -> dict:
        tool = step.get("tool")
        text = result.content if result is not None else ""
        count, paths = None, []
        if result is not None and role(tool) in ("search", "read_file"):
            paths = result_paths(tool, args, text, result.is_error)
            count = len(paths) if role(tool) == "search" else None
        if error is None and result is not None and result.is_error:
            error = "not_found" if "no such file" in text else "tool_error"
        if error is None and role(tool) == "search" and not count:
            error = "no_matches"
        status = "skipped" if error == "skipped" else "failed" if error else "done"
        entry = {"id": step.get("id"), "tool": tool, "status": status, "count": count,
                 "paths": paths, "error_code": error, "args": args}
        self.results.append(entry)
        self.ran.add(str(step.get("id")))
        self.outputs[str(step.get("id"))] = text
        self.fill_turns.append(f"## Step {entry['id']}, {tool}, {status}\n{text[:CONTEXT_CHARS]}")
        self.remember_call(step, tool, args, result)
        for s in self.plan.get("steps") or []:
            if str(s.get("id")) == str(step.get("id")):
                s["status"] = entry["status"]
        return entry

    def remember_call(self, step: dict, tool, args: dict, result) -> None:
        """completed_calls: one entry per tool call that reached its tool, with the
        idempotency key it carried (null for a read) and what it returned, so that a
        resumed run takes a later step's context from here and never calls again. A write
        the boundary rejected never reached the registry and carried no key, so it is not
        one of these; its status says it failed."""
        if result is None:
            return
        key = getattr(result, "key", None)
        if role(tool) in ("comment", "open_pr") and not key:
            return
        self.completed.append({"step": step.get("id"), "tool": tool, "args": args, "key": key,
                               "result": (result.content or "")[:CONTEXT_CHARS]})

    # -- revision, at the checkpoint whose check failed --------------------------------------
    def checkpoint(self, name: str, ok: bool) -> bool:
        """Assignment 2's checkpoint, where the plan may be revised: nothing is saved here.
        True when a new version is now active."""
        if ok or self.revisions >= MAX_REVISIONS:
            return False
        self.revisions += 1
        proposed = named_tools(revise_plan(self.ctx.client, self.plan, self.results, trace=self.ctx.trace))
        broken = schema_check(self.ctx, proposed, self.version + 1)
        verdict = {"valid": False, "failures": broken} if broken else validate(proposed)
        self.ctx.trace.event("revision", checkpoint=name, version=self.version + 1, valid=verdict["valid"],
                             failures=verdict["failures"])
        if not verdict["valid"]:
            return False                          # rejected: it never runs, and it counted against the bound
        before = {str(s.get("id")): {k: v for k, v in s.items() if k != "status"} for s in self.plan.get("steps") or []}
        changed = {str(s.get("id")) for s in proposed.get("steps") or []
                   if before.get(str(s.get("id"))) != {k: v for k, v in s.items() if k != "status"}}
        self.revised |= changed
        for step in proposed.get("steps") or []:
            if str(step.get("id")) in changed:
                self.restore_fills(step)
        failed = {str(r["id"]) for r in self.results if r["status"] == "failed"}
        done = {str(r["id"]) for r in self.results if r["status"] == "done"}
        self.ran -= (changed & failed) - done      # a failed step the revision changed runs again
        self.version += 1
        self.plan = proposed
        self.ctx.trace.plan(self.version, proposed)
        return True

    @staticmethod
    def restore_fills(step: dict) -> None:
        """Put "$fill" back on a revised step's executor-owned arguments. A reviser that
        never saw the issue cannot write a search query, and a query it invents is sent to
        the tool as it stands, which is how a revision spends its retry on nothing. The
        reviser's repair is the step's action; the executor still writes the value."""
        names = [arg(role(step.get("tool")), a) for a in EXECUTOR_ARGS.get(role(step.get("tool")), ())]
        args = step_args(step)
        if not names or not any(args.get(n) is not None and not wants_fill(args[n]) for n in names):
            return
        for name in names:
            if name in args:
                args[name] = FILL
        step.pop("args", None)
        step["args_json"] = json.dumps(args)

    # -- the comment's draft and the second pass, before the first write ---------------------------
    draft: dict | None = None        # the comment's arguments, checked, waiting to be posted last
    drafted_file: str | None = None

    def comment_step(self) -> dict:
        return next(s for s in self.plan.get("steps") or [] if role(s.get("tool")) == "comment")

    def prepare(self, step: dict) -> bool:
        """Draft the comment and run the second pass, before the PR or the comment is
        written. False when the run has ended, with nothing written."""
        draft = self.filled(step, step_args(step))
        if draft is None:
            return False
        final = self.second_pass(step, draft) if self.critique else draft
        if final is None:
            self.record(step, draft, None, "held")
            self.ctx.trace.end("escalated", detail=f"the draft still failed the second pass after {MAX_ROUNDS} rounds")
            return False
        self.draft = final
        label, file = parse_triage_comment(str(final.get(arg("comment", "body"))))
        self.drafted_file = file or "none"
        self.findings.update({"label": label or "bug", "file": self.drafted_file,
                              "comment_args": dict(final)})
        return True

    def post(self, step: dict) -> bool:
        """Post the checked draft, the run's last write. False when the run has ended."""
        self.save_state(step.get("id"), running=step.get("id"))   # the arguments on disk before the write
        result = self.call(step, step["tool"], self.draft)
        if result is None:
            return False
        self.record(step, self.draft, result)
        self.posted = not result.is_error
        return True

    # -- the pull request, before the comment ---------------------------------------------------
    def pull_request(self, step: dict) -> bool:
        """Open the draft PR when the comment's draft names a file; skip the step when it
        says none. The issue and the branch are fixed here, by code. A resume opens it with
        the arguments the checkpoint saved, never with new ones: a new argument is a new
        key. False when the run has ended."""
        if self.drafted_file in (None, "none"):
            self.record(step, step_args(step), None, "skipped")
            return True
        number = self.ctx.issue_number
        if self.findings.get("pr_args"):
            filled = dict(self.findings["pr_args"])
        else:
            args = step_args(step)
            args[arg("open_pr", "number")] = f"issue-{number}"
            args[arg("open_pr", "branch")] = f"repomind/issue-{number}"
            filled = self.filled(step, args)
            if filled is None:
                return False
            self.findings["pr_args"] = dict(filled)
        self.save_state(step.get("id"), running=step.get("id"))   # the arguments on disk before the write
        result = self.call(step, step["tool"], filled)
        if result is None:
            return False
        self.record(step, filled, result)
        return True

    def evidence(self) -> dict:
        issue = next((self.outputs.get(str(r["id"]), "") for r in self.results if role(r["tool"]) == "read_issue"), "")
        return {"issue": issue, "results": self.context()}

    def second_pass(self, step: dict, args: dict) -> dict | None:
        mode = self.ctx.critique
        if mode not in ("self", "external"):
            return args
        body = arg("comment", "body")
        for round_ in range(1, MAX_ROUNDS + 1):
            draft = args[body]
            if mode == "external":
                verdict = verify(draft, self.ctx)
            else:
                self.calls += 1
                v = self_critique(self.ctx.client, draft, self.evidence(), self.ctx.trace, step=self.calls)
                verdict = {"passed": v["decision"] == "accept", "reasons": [v["reason"]] if v.get("reason") else []}
            reason = "; ".join(verdict["reasons"]) or None
            changed = False
            if not verdict["passed"] and round_ < MAX_ROUNDS:
                rewritten = self.rewrite(step, args, reason or "the critic asked for a revision")
                if rewritten is not None:
                    changed = rewritten[body] != draft
                    args = rewritten
            self.ctx.trace.event("critique", round=round_, mode=mode, result="pass" if verdict["passed"] else "fail",
                                 reason=reason, changed=changed, draft=draft)
            if verdict["passed"]:
                return args
        return None                               # still failing in the last round: held

    def rewrite(self, step: dict, args: dict, reason: str) -> dict | None:
        """One model call rewrites the draft with the reason, offered the comment tool alone."""
        body = arg("comment", "body")
        note = f"Rewrite the draft. The draft:\n\n{args[body]}\n\nWhy it must change: {reason}"
        return self.fill(step, {**args, body: FILL}, [body], system="rewrite.md", purpose="critique", note=note)
