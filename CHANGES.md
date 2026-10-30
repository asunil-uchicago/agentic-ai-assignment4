# CHANGES.md

Three lines per assignment: what it added, what it removed, what it constrained.
Removals are logged like additions.

## Assignment 1
- **Added:** a bounded tool loop; four ways to triage an issue (a direct call, a workflow, an agent, and a hybrid of the two); a grounded success check that reads the tracker; a results script
- **Removed:** nothing
- **Constrained:** every agent run to three bounds checked in code (steps, tokens, wall clock); success to what the tracker holds for the run's ID

## Assignment 2
- **Added:** a plan written as data before the issue is read; a validator with four checks; an executor that runs only the plan's steps; one checkpoint that may revise the plan; a second pass before the comment; detectors and a diff over traces; a progress file and a compaction
- **Removed:** the model's choice of the next tool, in the planned way
- **Constrained:** every tool call to a step of a validated plan; revisions to steps not yet done; the comment to drafts that pass the second pass

## Assignment 3
- **Added:** five rewritten tool definitions with examples, among them open_pr; a boundary check before every call; errors that name the fix; harness-derived idempotency keys, a check before every write attempt and a read-back after a timeout; the call record; a fail-closed hook
- **Removed:** free-text tool descriptions and free-form filters; protocol detail in anything the model sees
- **Constrained:** every write to be safe to retry, and to a person when its outcome cannot be read back; the comment to the last write, after the PR

## Assignment 4
- **Added:** a SQLite checkpointer holding slide 14's five fields and refusing a conversation, with sync, async and exit modes; a resume that replays from the last checkpoint and puts the step that was running through the three exits with the saved arguments; a memory store that enforces provenance at the write, supersedes rather than appends, and evicts on access; the write policy, written before the store; a retrieval before the first search and a write at the end of a run; the switches that tell a run a fact, bury it, compact it away, and keep it in a note outside the history
- **Removed:** state that lived only in the process: a killed run's plan, its finished calls with their keys, and its drafted comment are now on disk under its run_id instead of dying with it; and the planner's ability to leave a template where a value belongs, which reached the tracker unfilled
- **Constrained:** every write to sit behind a sync checkpoint carrying the arguments it is about to use, so a replay derives the same key; a resumed write to the arguments the checkpoint saved, never to a fresh model call; every store record to a source, an ISO 8601 time, an origin and a confirmed flag, refused at the write when any is missing; records whose source is an issue to stay out of every prompt

