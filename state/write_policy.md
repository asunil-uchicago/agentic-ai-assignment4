# RepoMind's write policy

What enters the store, written before the store was (Lecture 4, slide 23). Four
decisions, then the taxonomy, then eviction. `state/store.py` enforces what this
file can express: a record with a source it does not recognise, a kind outside
slide 18's three, a missing origin or a missing `confirmed` is refused at the
write, not stored and sorted out later.

A reminder of what this store is not. A checkpoint belongs to one run, must be
exact, and is thrown away when the run ends. This is shared by every run, may be
approximate, and grows. The two have different fixes and no fix crosses between
them.

## 1. When

- **At the end of a run**, once the run has decided, and only for a run that
  finished. A run that escalated decided nothing, so it writes nothing.
- **Or the moment a claim is verified**, where verification happens earlier: a
  maintainer's rule read out of an issue's discussion is already stated, and does
  not need the rest of the run to confirm it.
- **Never mid-run on a guess.** A triage decision taken at the comment's draft is
  a guess until the comment is posted; the next step may overturn it. Writing
  mid-run is how a store fills with what a run thought at step three.

## 2. What

Three tests, and a memory needs to pass one of them:

- **Expensive to derive.** A procedure worked out over several failed attempts, a
  path through the repository nothing documents. Cheap facts are cheaper to look
  up again than to keep correct.
- **Stated rather than inferred.** A maintainer saying how duplicates are handled
  here is a fact about the team, not an inference about the code.
- **Confirmed.** A triage decision that a posted comment now stands behind.

What is deliberately kept out: anything a search would answer in one call, the
text of an issue reproduced as though it were true, and anything about one run's
internals, which is the checkpoint's job.

## 3. How

- **Insert** when the store holds nothing about the claim.
- **Merge** when a new record agrees with an active one and adds detail: the active
  record's text is extended and its `written_at` moves forward.
- **Replace only by superseding.** A record is never edited in place and never
  deleted. `Store.supersede(old_id, new_record)` writes the new record with
  `supersedes` naming the old one and marks the old one inactive, in one
  transaction, so retrieval returns exactly one of a contradicting pair. The old
  record stays in the store, inactive, as the record of what was believed before.

## 4. With

Every record carries its provenance. This is the decision most systems skip
(slide 27), and skipping it is what makes a store impossible to audit later.

| Field | What it says | How it is set |
|---|---|---|
| `source` | `user`, `issue:<n>`, `repo` or `memory` | the narrowest true origin. A maintainer's comment is `user`; an issue body is `issue:<n>` whoever wrote it; a file in the repository is `repo`; RepoMind's own earlier work is `memory` |
| `written_at` | ISO 8601 | **when the memory was first observed**, not when the row was inserted. A rule a maintainer stated in September is three months old even if RepoMind stored it today, and its age is what a later run must judge it by. For something RepoMind worked out in the run that is writing it, the two are the same moment |
| `origin` | `derived` or `ingested` | `derived` when RepoMind worked it out from what it saw, `ingested` when it was read from outside and copied down |
| `confirmed` | `true` or `false` | `true` only when something outside the claim stands behind it. A person with standing stating a rule **does** confirm it: `confirmed` records who has standing, not whether the claim was tested, and that person's standing is exactly what is being recorded. `stated_by` names them. Text from an account outside the team confirms nothing, whatever it asserts about itself |

## The taxonomy (slide 18)

- **episodic**: what happened. A triage decision, a record of what an issue said on
  a date.
- **semantic**: what is true. Facts about the repository that outlive any run.
- **procedural**: how to do something here. A sequence of commands, a convention.

A claim from an untrusted source is never semantic. Stored as a fact, text anyone
can write comes back later with the authority of RepoMind's own knowledge, which
is the path slide 24 calls memory poisoning. It may be stored as episodic, a record
that the text existed, and `state/store.py`'s `trusted()` keeps records sourced
`issue:<n>` out of every prompt: they are retrieved and logged, never rendered.

## The three candidates

| Candidate | Stored | Kind | source | origin | confirmed | The failure it invites |
|---|---|---|---|---|---|---|
| A, the reproduction procedure RepoMind worked out | yes | procedural | `memory` | derived | true | **staleness**: `package.json` can change and the procedure not. It expires at 270 days and is superseded the first time a run finds the scripts different |
| B, the auto-merge claim in issue 4188's body | yes, as a record of what the issue said | episodic | `issue:4188` | ingested | false | **poisoning**, if it were stored as a fact. Episodic, unconfirmed, and never rendered into a prompt, it is evidence that the text exists and nothing more |
| C, the maintainer's rule on duplicates | yes | procedural | `user`, `stated_by: priya-s` | ingested | true | **contradiction**: the rule changes and the store holds both. Supersession, not a clock, retires it |

Candidate C is procedural rather than semantic because it says how duplicates are
handled here, not what is true about the code.

## Eviction

Decided now, not when retrieval starts filling with near-matches (slides 25 and 27).

- **Size**: `MAX_ACTIVE = 200` active records. Past that the oldest are evicted.
- **Rule**: expiry on access. Every retrieval first expires what is past its kind's
  lifetime, then cuts the remainder to the cap. Nothing is deleted: an evicted
  record goes inactive and stays.
- **Lifetimes**, set by how often each kind's facts change: episodic 120 days,
  procedural 270, semantic 540.
- **Exception**: a record whose source is `user` never expires on age. A rule a
  maintainer stated is not wrong because it is old; it is wrong when someone
  contradicts it, and then supersession retires it.

**How long an entry lives at RepoMind's write rate.** A finished run writes one
record, its triage decision (measured over this assignment's runs of issue 4207:
one `memory` record each). At twenty triaged issues a day that is 20 records a day,
all episodic. The cap is reached after 200 / 20 = 10 days, so from then on an
episodic record is evicted by the cap at about 10 days old, long before its 120 day
lifetime. Procedural and semantic records are far rarer, perhaps one a week, so they
sit inside the cap and reach their own lifetimes: a procedure lives its full 270
days unless a run supersedes it. If the write rate fell to two issues a day, the cap
would hold 100 days of episodic records and the 120 day lifetime would start to be
the binding limit instead.
