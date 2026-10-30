# Assignment 4 · Kill it and resume: state, keys and a memory with provenance

Model: OpenAI, `gpt-4o-mini`, the same for every run.

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

**The matrix, derived keys.** Uncrashed run: *to be filled in after the runs.*

| Step | When | run_id | Comments | Open PRs | Matches its findings |
|---|---|---|---|---|---|

## Part C · Break it on purpose

| Step | When | run_id | Comments | Comment IDs | Open PRs |
|---|---|---|---|---|---|

What the checkpoint did, and what the key did:

## Part D · A memory that can say where it came from

- The write policy, in brief:
- The three candidates, and how each was stored:

| Candidate | Stored? | Kind | source | origin | confirmed | Invites |
|---|---|---|---|---|---|---|
| A | | | | | | |
| B | | | | | | |
| C | | | | | | |

- Eviction: the size, the rule, and how long an entry lives at your write rate:

| Issue 4207, five runs each | With the store | Empty store |
|---|---|---|
| Passes | | |
| Mean tool calls | | |
| Mean tokens | | |

## Part E · Answer from your own runs

1. Your prediction against the matrix:
2. The contradiction test, and the record IDs the third session retrieved:
3. What your checkpoint cost, structured and with the conversation, and over how many runs:
4. Is what you built durable execution?
5. Which abstraction?

## Part F · Find the boundary that lost it

The fact, word for word, and what the comment states when a run uses it:

| Case | The lost run | Where it lost the fact (trace and request record) | The fix | The fixed run |
|---|---|---|---|---|
| Attention | | | | |
| Compaction | | | | |
| The session | | | | |

The session case's stated run:

## AI assistance

Which tool, what it wrote, and what you checked.
