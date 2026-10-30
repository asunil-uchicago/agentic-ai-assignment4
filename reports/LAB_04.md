# Assignment 4 · Kill it and resume: state, keys and a memory with provenance

Model: OpenAI, `gpt-4o-mini`, the same for every run.

### A note on the runs in `runs/`

Part A's marked plan and Part B's hand walk were committed and pushed on their own,
as commit `dcd2c04` at 09:13:50, before any run on this model was made. The first run
cited anywhere in `reports/LAB_04.json` started at 09:27:14.

Between those two times, three exploratory runs of 4201 exposed three faults in
Assignment 3's solution that only appear on this model: the planner wrote a literal
template where a `$fill` belonged and nothing filled it, so one run posted a comment
reading `label: <bug | feature | question | docs>`; the reviser invented search
queries although its own prompt tells it that it never sees the issue; and the model
wrote `functions.issues_read` for a tool name, which matched no role. The fixes are
in `CHANGES.md` and in `ways/planned.py` (`wants_fill`, `restore_fills`,
`named_tools`) and in three files under `prompts/`. Those three runs are left in
`runs/` rather than removed, and nothing in this report or in the JSON cites them.

No prompt changed after 09:25:58, and every cited run started after it, so each
comparison the report makes ran on one unchanging configuration: Part B's ten cases,
Part C's ten, Part D's two arms of five, and Part F's seven. The last code change,
`named_tools`, landed at 09:46:51, and the five runs behind Part E's byte measurement
and all seven of Part F's ran after it.

## Part A · Mark the plan, place the checkpoints

The plan, read from one scripted run of issue 4201 and the same in every run of this
way: read the issue, search, read a file, open the draft pull request, post the
comment.

| Step | Tool | Kind | Checkpoint |
|---|---|---|---|
| 1 | `issues_read` (read_issue) | read | none |
| 2 | `code_search` (search) | read | after |
| 3 | `code_read_file` (read_file) | read | after |
| 4 | `pulls_open` (open_pr) | keyed write | before |
| 5 | `issues_comment` (comment) | keyed write | before |

**Why step 1 gets none.** Slide 13 puts a checkpoint after a read only when the read
is worth not repeating. Step 1's arguments are fixed by the plan, so it needs no fill
call: replaying it costs one tool call against a local fixture and zero model tokens.
Steps 2 and 3 each cost a fill call before their tool runs, which is the expensive
part, so a checkpoint goes after each of them. A crash at step 1 or step 2 therefore
has nothing to load and starts over under the same run_id, which is what the handout
says such a run does.

**Why the writes get a checkpoint before, not after.** Slide 13 needs none before a
keyed write, because the key makes a replay safe. RepoMind's keys are not free-standing:
`tools.derive_key` hashes the run, the plan step and the arguments, and the arguments
are what a model wrote a moment earlier. A replay is safe only with those same
arguments, so they must be on disk before the write runs. The save therefore sits
after the fill call that produced them and immediately before the tool call, with
nothing in between (`ways/planned.py`, `save_state` called from `pull_request` and
`post`).

**Durability mode: `sync`.** Slide 12 sets the mode once for the run, not per step,
and its rule is sync for any run with an irreversible step. This run has two. Exit
mode would lose everything to `kill -9`, which is exactly the failure being tested;
async mode leaves a gap in which the checkpoint for a write might not have landed
when the write does, and that gap sits in front of the only two steps that matter.
The cost is one SQLite commit with `synchronous=FULL` per checkpoint, four per run.

**The weaker of the two writes: the comment.** The pull request is protected twice
over. Its key is one guard, and `tools/__init__.py`'s check before every attempt reads
it back by **branch**, not by key: `tracker.prs(run_id, branch="repomind/issue-4201")`.
The branch is fixed in code from the issue number, so it is the same string on every
attempt of every replay, whatever the model writes in the title or the body. Lose the
key and the branch still finds it. The comment has only its key. Its check is
`tracker.comment_by_key(run_id, key)`, and the key is a hash of arguments the model
wrote, so a replay that re-drafted the comment, or any change to how the key is
derived, produces a key the tracker has never seen and a second comment on a public
issue. Nothing else about a comment is unique: a run may legitimately post more than
one, so the tracker cannot dedupe by issue, and the handout forbids adding a check of
my own that finds a comment by its issue.

That is the asymmetry Part C is built to show, and it is why the resume replays the
comment from `findings.comment_args` rather than asking the model again.

## Part B · Kill it at every step

### The hand walk, written before the matrix

*Written and committed before any killed run was made. Part E, item 1, says where it
turned out to be wrong.*

**The crash I expect to be worst: `--die-during-step 5`,** the comment killed after
the tracker committed it and before the acknowledgement came back. It is the worst
because it is the only case where an irreversible, publicly visible write has happened
and the process has no record of it at all: the call record has no line for the
attempt, the trace stops mid-step, and the comment is already on the issue.

**Its last checkpoint.** The sync checkpoint for plan step 5, written by
`Executor.post` immediately before the tool call. It holds the plan, every step's
status with 1 to 4 done and 5 running, the four completed calls with their keys,
`findings.label`, `findings.file` and `findings.comment_args` (the exact arguments the
comment is about to be posted with), and `current_step: "5"`.

**The steps between that checkpoint and the crash.** None. That is what placing the
checkpoint immediately before the write buys: the replay window holds exactly one
step. The one everybody leaves out is the one that is left: **step 5 itself, the step
that was running at the crash.**

**Each step in the window, its side effect, and what closes it.**

| Step in the window | Side effect | What closes it |
|---|---|---|
| 5, post the comment | yes, and irreversible: a comment on a public issue | **the key, and nothing else.** On the resume the arguments come back from `findings.comment_args`, so `derive_key(run_id, 5, args)` is the identical hash, the registry's check before the attempt finds the row with `comment_by_key`, and the tool returns the first result marked "already there". A recorded result would have closed it too, but there is none: the process died before it learned anything |

Steps 1 to 4 are outside the window. They are closed by a recorded result: their
entries in `completed_calls` carry what they returned, so the resume reuses them and
never calls them again.

**What I predict the matrix will show.**

1. With derived keys, every one of the ten cases ends with exactly one comment on
   4201 and exactly one open pull request from `repomind/issue-4201`, and the comment
   says what the run's own last checkpoint's findings say. No crash, at any step,
   leaves a second write.
2. Crashes at steps 1 and 2 will have no checkpoint to load and will restart from the
   planner. They will spend roughly a full run's tokens on the resume, and the resumed
   half may name a different file from the crashed half, because it asks the model
   again. That is not a failure: `matches` compares each run with its own last
   checkpoint.
3. Crashes at steps 3, 4 and 5 will resume. Resumes of steps 4 and 5 should spend
   **zero model tokens**: everything they need, including the draft, is in the
   checkpoint, so they only replay the write.
4. The resumed half of a crash at step 3 will make one fill call for step 3 and go on
   from there, repeating nothing that finished before the last checkpoint.
5. With random keys (Part C), the duplicates will appear at **step 5 only, both after
   and during**, because step 5 is the only step at which a comment already exists when
   the process dies. Steps 1 to 4 leave no comment to duplicate. No pull request will
   duplicate at any step, because its check reads by branch and the branch does not
   depend on the key.

**The matrix, derived keys.** Uncrashed run: `planned-4201-s01-2f6dd7`, 8,248 tokens,
`label: bug`, `file: src/notifications/templates.ts`, one comment and one open pull
request. Its plan numbered five steps, the five Part A marked, so the matrix is ten
cases, and every crashed run's plan was those same five steps. All ten ran with the
empty store, so that the only thing varying across the matrix is the crash: with the
live store each case would have been reading the decisions of the cases before it.

| Step | When | run_id | Comments | Open PRs | Matches its findings | Resume tokens |
|---|---|---|---|---|---|---|
| 1 | after | `planned-4201-s01-da8380` | 1 | 1 | yes | 8,267 |
| 1 | during | `planned-4201-s01-66db68` | 1 | 1 | yes | 14,602 |
| 2 | after | `planned-4201-s01-37d8dc` | 1 | 1 | yes | 8,227 |
| 2 | during | `planned-4201-s01-08af68` | 1 | 1 | yes | 8,281 |
| 3 | after | `planned-4201-s01-c0454b` | 1 | 1 | yes | 4,650 |
| 3 | during | `planned-4201-s01-b764d0` | 1 | 1 | yes | 4,657 |
| 4 | after | `planned-4201-s01-005600` | 1 | 1 | yes | 0 |
| 4 | during | `planned-4201-s01-98bf47` | 1 | 1 | yes | 0 |
| 5 | after | `planned-4201-s01-3afd3b` | 1 | 1 | yes | 0 |
| 5 | during | `planned-4201-s01-bdb81c` | 1 | 1 | yes | 0 |

Ten of ten: one comment, one open pull request, and a comment stating the label and
file of that run's own last checkpoint. Nine of the ten named
`src/notifications/templates.ts`, the file the uncrashed run named. The tenth,
`--die-during-step 1`, named `src/api/auth.ts`: it died before its first checkpoint,
restarted from the planner under the same run_id, and searched differently. Its
comment still states what its own findings say, which is what `matches` asks.

## Part C · Break it on purpose

Same checkpoints, same placement, same resumes. `KEY_MODE=uuid` on every crashed
run, and a resume keeps the run's own switches, so both halves write with fresh
random keys.

| Step | When | run_id | Comments | Comment IDs | Open PRs |
|---|---|---|---|---|---|
| 1 | after | `planned-4201-s01-33b69b` | 1 | 1 | 1 |
| 1 | during | `planned-4201-s01-a214e8` | 1 | 7 | 1 |
| 2 | after | `planned-4201-s01-5433fe` | 1 | 2 | 1 |
| 2 | during | `planned-4201-s01-7c5bcd` | 1 | 8 | 1 |
| 3 | after | `planned-4201-s01-2f388a` | 1 | 3 | 1 |
| 3 | during | `planned-4201-s01-5ee1a0` | 1 | 9 | 1 |
| 4 | after | `planned-4201-s01-52d002` | 1 | 4 | 1 |
| 4 | during | `planned-4201-s01-e7c65a` | 1 | 10 | 1 |
| 5 | after | `planned-4201-s01-628451` | **2** | **5, 6** | 1 |
| 5 | during | `planned-4201-s01-918e49` | **2** | **11, 12** | 1 |

Two cases duplicated, both at step 5, and no pull request duplicated anywhere.

**What the checkpoint did, and what the key did.** The checkpoints did exactly what
they did in Part B and no more: they fixed where each resume started and how much of
the run came back. Every case replayed the same steps as its Part B twin, reused the
same recorded results, replayed the comment from the same saved arguments, and the
resumes of steps 4 and 5 again spent zero model tokens. What changed is that the
tracker no longer recognised the replayed write. In Part B, `derive_key(run_id, 5,
args)` over the saved arguments reproduced the key byte for byte, the check before
the attempt found the row, and the tool returned the first result. With a random key
the same replay, from the same checkpoint, with the same arguments, asks the tracker
about a key it has never seen, it answers no, and the comment is posted a second
time. Steps 1 to 4 are untouched because no comment existed yet to duplicate. The
pull request never duplicated at any step under either key mode, because its check
reads back by branch, `tracker.prs(run_id, branch="repomind/issue-4201")`, and the
branch is built in code from the issue number, so it is the same string on every
attempt whatever the key is. That is slide 47's claim measured twice over: the
checkpoint bounded how much replayed, and only the key, or a second guard that does
not depend on it, made what replayed safe to replay.

## Part D · A memory that can say where it came from

**The policy, in brief** (`state/write_policy.md`, written before `state/store.py`).
*When*: at the end of a run, or once a claim is verified, never mid-run on a guess a
later step could overturn. *What*: what was expensive to derive, what was stated
rather than inferred, and what was confirmed. *How*: insert, or merge; a replacement
is a new record naming the one it replaces in `supersedes`, in one transaction that
also marks the old one inactive. *With*: source, `written_at`, `origin` and
`confirmed` on every record, enforced at the write, so a record that cannot say
where it came from is refused rather than stored. Two decisions the policy makes
explicit: `written_at` is when the memory was **first observed**, not when the row
was inserted, because its age is what a later run judges it by; and a person with
standing stating a rule **does** make it `confirmed`, because `confirmed` records
who stands behind a claim, not whether the claim was tested.

**The three candidates** (`eval/seed_memory.py`, which is also session 1 of Part E's
contradiction test).

| Candidate | Stored? | Kind | source | origin | confirmed | Invites |
|---|---|---|---|---|---|---|
| A, the reproduction procedure RepoMind worked out in a run of 4191 | yes, `mem-0001`, repository-wide | procedural | `memory` | derived | true | **staleness**: `package.json` can change and the procedure not |
| B, the auto-merge claim in the body of issue 4188 | yes, `mem-0002`, scoped to 4188 | episodic | `issue:4188` | ingested | **false** | **poisoning**, if it were stored as a fact. It is a record that the issue said it, never that it is true, and `Store.trusted()` keeps it out of every prompt |
| C, the maintainer's rule on duplicates | yes, `mem-0003`, repository-wide, `stated_by: priya-s` | procedural | `user` | ingested | true | **contradiction** when the rule changes, which Part E then does |

**Eviction.** Size 200 active records. Rule, expiry on access: every retrieval first
expires what is past its kind's lifetime, then cuts the remainder to the cap.
Lifetimes of 120 days episodic, 270 procedural, 540 semantic, set by how often each
kind's facts change. A `user` record never expires on age, because a maintainer's
rule is not wrong for being old; it is wrong when someone contradicts it, and
supersession is what retires it then. Nothing is deleted: an evicted record goes
inactive and stays. At RepoMind's measured write rate of one record per finished run,
and twenty triaged issues a day, that is 20 episodic records a day, so the cap binds
after 200 / 20 = 10 days and an episodic record is evicted at about 10 days old, far
short of its 120 day lifetime. Procedural and semantic records arrive perhaps weekly,
sit well inside the cap, and reach their own lifetimes instead. If the rate fell to
two issues a day the cap would hold 100 days of episodic records and the lifetime
would become the binding limit.

**The reopened issue, 4207.** One seed run first, `planned-4207-s01-e29877`, which
put a prior triage decision for 4207 in the store. Then five runs with the store and
five with an empty one, `EMPTY_STORE=1`, one at a time.

| Issue 4207, five runs each | With the store | Empty store |
|---|---|---|
| Passes | 5 / 5 | 5 / 5 |
| Mean tool calls | 6.2 | 6.0 |
| Mean tokens | 17,782 | 14,158 |

**A measured null result.** The store changed nothing about the answer. All ten runs
triaged 4207 as `bug`, `src/export/scheduler.ts`, which is correct, and all ten
**read that file before naming it**: none took the path from memory, not even the
five whose `store_read` handed them `mem-0004`, the seed run's own decision naming
exactly that path. What the store did change is the bill. It cost 3,624 tokens a run,
25.6 per cent more, for the retrieved records carried in every fill call, and 0.2 of
a tool call, one with-store run having revised its plan into an extra search. On an
issue whose answer one search and one read already find, a store is the most
expensive of slide 34's four fixes and buys nothing. Part F is the case where it is
the only fix that works.

## Part E · Answer from your own runs

**1. My prediction against the matrix.** The hand walk, committed before any crashed
run was made, was right in every particular the matrix can check.
`--die-during-step 5` was the worst case and the only one whose irreversible write
had happened with no record of it in the process. Its last checkpoint was the sync
one for step 5 holding `comment_args`, the replay window held that one step, and the
key closed it. All ten derived-key cases left one comment and one pull request.
Resumes of steps 4 and 5 spent zero model tokens; crashes at steps 1 and 2 restarted
from the planner and spent close to a full run again. The Part C duplicates appeared
at step 5 only, after and during, and no pull request duplicated at any step.

Two things it got less than exactly right. It said a crash at step 3 would "make one
fill call for step 3 and go on from there", which reads as cheap; the resumes of
step 3 spent 4,650 and 4,657 tokens, about 56 per cent of a full run, because going
on from there is three more fill calls, not one. And it hedged that the restarted
halves "may name a different file": one of the two did, `--die-during-step 1` naming
`src/api/auth.ts`, and `--die-after-step 1` did not. The substance held, that
placement bounds the window and the key closes it. What it underestimated is how
much of a run still sits after step 3, which is the practical argument for the
checkpoint after step 3 that Part A placed for a different reason.

**2. The contradiction test.** Three separate processes, as slide 25 requires,
because an append-only store looks correct until something else reads it.

| Session | Process | What it did |
|---|---|---|
| 1 | `python eval/seed_memory.py` | stored the maintainer's rule as `mem-0003`: procedural, `user`, `stated_by: priya-s`, confirmed |
| 2 | `python eval/contradiction.py contradict` | wrote `mem-0005` with `supersedes: mem-0003`, and marked `mem-0003` inactive, in one transaction |
| 3 | `python eval/contradiction.py retrieve` | asked the store for the maintainer's rule on duplicates |

The third session retrieved **`mem-0005`**, and only that. `mem-0003` is still in the
store, inactive, and is not returned. Had the store appended instead of superseding,
session 3 would have been handed two rules that contradict each other with nothing
to say which is current, and no test inside session 2 could have shown it.

**3. What the checkpoint cost.** Measured over **5 runs** of issue 4201 with
`CP_SHADOW=1`, which writes a second copy of every checkpoint, carrying every message
the way had sent the model so far, to `state/checkpoints_with_conversation.jsonl`.
That copy never goes near the checkpoint database: `state/checkpointer.py` refuses a
checkpoint holding a conversation, as the checker does.

| | Bytes, all four of a run's checkpoints together |
|---|---|
| Structured, slide 14's five fields | **18,952** |
| The same, with the conversation | **102,168** |
| Ratio | 5.4x |

The structured figure is itself dominated by one field: the validated plan is saved
whole in each of the four checkpoints. The difference between the two columns is
that the five fields grow per step, of which there are five, while the conversation
grows per turn and never shrinks.

**4. Is what you built durable execution?** No. It is a recovery aid. Slide 37's
three parts, asked as slide 40's three questions:

| Question | RepoMind | Why |
|---|---|---|
| Does it notice that the run died? | **no** | nothing watches. The trace stops with no `run_end` line, and only a person reading `runs/` sees that |
| Does it restart the run without a person? | **no** | `python run.py --resume <run_id>` is typed by a human who knows the run_id |
| Does it stop finished work being done twice? | **yes** | the journal is there: `completed_calls` carries each finished call with its key and result, the resume skips them, and Part C shows the key is what makes the one unfinished write safe |

One of three, and the one it has is the journal, not the supervisor. To make it
durable execution I would add exactly that: a process that lists the runs in `runs/`
whose traces have no `run_end` line, with a lease or a heartbeat so that a run which
is merely slow is not restarted underneath itself, and re-invokes `--resume` on each
with the switches recorded in its own `run_start`. The checkpoint and the journal are
already written. What is missing is anything that reads them without being asked.

**5. Which abstraction?** Slide 43's four questions, in order, none of them exiting
early.

| Question | Answer | What it adds |
|---|---|---|
| Do you know the steps and their order in advance? | yes. The plan is validated before a step runs, and the revision only repairs it | **a graph** |
| Do runs outlive a single process? | yes, by construction: Part B killed twenty of them | **checkpoints on disk**, which is why an in-memory checkpointer is disallowed |
| Do steps have irreversible side effects? | yes, two, and the comment is public | **a key on every write**; durable execution only if a half-done run must finish on its own, and here a person resumes it, so not yet |
| Do you want to write the loop and compaction code yourself? | yes, and this course is the reason | **your own harness** |

The design the answers point to is a **graph with disk checkpoints, an idempotency
key on every write, and a hand-written loop**, which is what RepoMind is, with
durable execution deferred until a supervisor is worth building.

One thing slide 44 says no framework decides for you, and I pick the first of its
three because this assignment measured it: **what to checkpoint and where**. No
engine knows that RepoMind's keys are hashed over arguments a model wrote a moment
earlier, so the save has to sit between the fill call and the tool call with nothing
in between. A framework checkpointing after every step, slide 12's default, would
have saved after step 4, before step 5's draft existed, and `--die-during-step 5`
would have had no arguments to replay from and would have asked the model again,
which is a new key and a second comment. Placement is a judgement about this system's
own writes, and Part C is what it looks like when you expect a default to cover it.

## Part F · Find the boundary that lost it

The fact, word for word, told to a run by `TELL_FACT` as a maintainer's note:

> Ledgerline's maintainers label every report about the Export button as feature:
> they plan its fixes on the feature board.

All seven runs are runs of `--way planned` on issue 4192, "Export button stays greyed
out after a failed export". Its answer without the fact is `label: bug`. **When a run
uses the fact, its comment states `label: feature`**, and that is the effect each case
is read by. The attention and compaction cases ran with the empty store, so that only
the boundary under test could lose the fact or carry it.

| Case | The lost run | Where it lost the fact (trace and request record) | The fix | The fixed run |
|---|---|---|---|---|
| Attention | `planned-4192-s01-e42c21`, comment `label: bug`, `file: src/api/reports.ts` | not lost from the window at all. The drafting call is request **n=8**, and `request_text` of that line **contains the fact**, 20,548 characters from the nearer end of a request of roughly 41,000, buried in the middle of nine of Ledgerline's own files as a plain line with no heading. It was in the context and went unused | a shorter context, or a more prominent place. `TELL_FACT=prominent` drops the filler and puts the note at the end of the request, where the model reads first | `planned-4192-s01-df25b1`, comment `label: feature`, `file: src/components/ExportButton.tsx`. Its drafting call is also n=8 and was also sent the fact, at **0 characters from an end** |
| Compaction | `planned-4192-s01-c4fbd9`, comment `label: bug`, `file: CHANGELOG.md` | the first `compaction` line, `{threshold: 200, tokens: 481, summarized: 3, kept: 1}`, falls after request n=6 and before the drafting call, request **n=10**. Requests **n=2, 4 and 6 were sent the fact**; **n=10 was not**. `prompts/compact_steps.md` asks for the tools called and the paths they returned and nothing else, so a rule stated in an early turn does not survive the summary. A second compaction follows after n=8 | a structured note kept outside the conversation, not a bigger window. `FACT_NOTE=1` puts the fact in a note appended to every fill call, which compaction never touches because it is not in the history | `planned-4192-s01-c98c0d`, comment `label: feature`, `file: none`. The same two compactions at the same threshold, an earlier call (n=2, 4, 6) sent the fact, and the drafting call **n=9 was still sent it**, at 0 characters from an end |
| The session | `planned-4192-s01-847db8`, comment `label: bug`, `file: src/components/ExportButton.tsx` | **no call of the run was sent the fact**: all 8 of its requests were searched and none contains it. It ran with the empty store and was never told the fact, so nothing carried the rule across the run boundary from the stated run 39 seconds earlier. Its `store_read` returned `[]` | a store. The stated run's write policy kept the rule as the maintainer stated it, word for word, with who stated it and when | `planned-4192-s01-8fd1c2`, comment `label: feature`, `file: none`. Its `store_read` line, before its first search, returned `['mem-0001', 'mem-0005', 'mem-0006', 'mem-0007']`, and **`mem-0006` holds the fact word for word**. Requests n=2, 4 and 5 were sent it |

**The session case's stated run** is `planned-4192-s01-b94e2b`, the only one of the
three told the fact, run with `TELL_FACT=prominent` and the real store. It triaged
4192 as `label: feature`, and at the end of the run it wrote two records: **`mem-0006`**,
the rule itself, procedural, source `user`, `stated_by: priya-s`, `written_at`
`2026-10-20T09:15:00Z`, ingested and confirmed, in the maintainer's words unchanged;
and `mem-0007`, episodic, source `memory`, its own triage decision for 4192.

**Which record brought it back.** Both were retrieved, and they are not equivalent.
`mem-0007` says only that RepoMind once labelled 4192 a feature, which is RepoMind's
own earlier conclusion returning with RepoMind's own authority, the shape slide 24
warns about. `mem-0006` is the rule a person with standing stated, and it is the
record that justifies the label rather than merely repeating it. That distinction is
why `prompts/memory.md` tells a run how to treat each source: a `user` record is a
maintainer's rule and is to be followed, a `memory` record is a lead to check, a
`repo` record may be out of date. A prompt that treated every memory as a lead would
have brought the rule back and left it unused, which is a store that pays its full
cost and returns nothing.

**The fourth boundary, the restart,** is Part B's. Nothing in this part is its fix,
and a checkpoint is no use against any of the three here: the attention case's fact
was already in the window, the compaction case's was in a conversation a checkpoint
is told not to save, and the session case crossed a boundary no single run's state
reaches across.

## AI assistance

I used Gemini and Claude Code to better understand the starter repository, the assignment handout, and its specifications. I used GitHub Copilot for autocompletion and linting while coding, and I also used Claude Code to draft the skeletons for prompts and documentation. I made sure to manually verify all of these to ensure correctness.
