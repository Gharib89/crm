# Context discipline: a ship run is long; protect the main thread

## Contents

- [Model tiers](#model-tiers)
- [While a subagent is out, end the turn](#while-a-subagent-is-out-end-the-turn)
- [Once isolated](#once-isolated)
- [The Run file](#the-run-file)

What bloats the window is raw tool output landing in the main thread, not the
work itself, so spend tokens on decisions, not dumps. The levers and the model
tiers below are defaults you apply with judgment. What stays required: the
scratch directory and Report file each subagent prompt names, edits under the
worktree prefix, the ten tasks created at `init`, and what the Run file's
mechanics refuse.

**Delegate noise, not size.** A subagent earns its cost
only when the raw output you would otherwise ingest is much larger than the
conclusion you need: a multi-file map, a verification run, CI logs. When you
can already point at the target (one or two known files, a single test node,
one mechanic's projected JSON) work inline. A small-lane run typically spawns
none of its own; a session with no subagent tools runs everything inline and is
still a complete run. The levers, in rough order of impact:

- **Delegate reading, not just review.** Send the investigation to a cheap-tier
  subagent ("map how X, Y, Z connect; return signatures, call sites and data
  shapes") and read only the exact lines you will edit: a file you only need to
  *understand* stays out of main context. Ask neutral, current-state questions
  that want file and line pointers back, and keep the intended change in the
  main thread: a brief that names the change gets back a reading bent toward it.
- **One reference file per call.** Several files chained in one `cat`
  can pass the persist limit, and the caller then sees a 2 KB preview of the
  lot and re-reads each file it chained.
- **Read the profile by `##` section**, naming the heading (`## Reviewers`,
  say) in a section-scoped `awk` or `sed -n`, for the sections a phase uses,
  rather than the whole file.
- **Investigate inside the worktree from the start**, so every file you read is
  the copy you will edit.
- **Targeted test nodes during the loop; the full suite only at the local
  gate.**
- **Delegate noisy verification runs** to a cheap-tier subagent returning
  `pass | fail` plus the failing lines. Mechanics project their own output:
  run them inline.
- **One Run file** for the checklist and the design and plan. It survives a
  mid-run context summary; the same summary repeated across turns does not.

**Every subagent prompt names the scratch directory that subagent writes in**,
`<scratch>/<role>/`, `<scratch>` being the directory `run-file init` printed and
`<role>` its job in the run (`map`, `execute`, `verify`, `standards`, `spec`,
`writing`). A subagent handed nowhere to write reaches for the scratchpad its
own environment block names, and can write over another run's files with no
failure signal. Edits to the repo go under the worktree prefix, and an isolated
run's prompt carries the [one plain command](#once-isolated) rule. Pass the
directory the way you pass the model tier: written into the prompt, every
dispatch.

## Model tiers

Use the cheapest model that fits; reserve the strong tier for judgment. The
table is the default, and a case it does not fit takes the tier it needs.

| Work | Model |
|---|---|
| Investigation and mapping | haiku |
| Phase-2 **execution** from a settled plan; mechanical edits and fixes; the docs-sync pass on human prose; the `code-review` skill's **Spec** axis | sonnet |
| Phase-2 **judgment** (classification, plan, design, the implementation brief); triage of every finding; the `writing-for-agents` pass; the `code-review` skill's **Standards** axis | opus |

When you invoke `code-review`, tier its two axes yourself. Fall back to the
nearest available tier rather than running everything on one model.

## While a subagent is out, end the turn

Dispatch a composed skill's subagents, then **end the turn**. The completion
notification resumes the run on its own: a poll loop, a sleep, a status ping or
a read of the output file buys nothing it does not deliver.

## Once isolated

After `EnterWorktree`, Claude Code guards the session, and every subagent it
dispatches inherits the guard. It refuses an Edit or Write to a path in the
main checkout, and any Bash command running git that it cannot prove targets
this worktree. Measured on Claude Code 2.1.311, it refused:

- a loop around a git command;
- git pointed at another checkout, `git -C <main-checkout> ...`;
- a heredoc fed to a script whose text names git;
- a `run-file note` whose text named `bash -c`, in 3 of the 7 wordings tried,
  read as a nested shell it could not parse;
- an Edit or a Bash command naming a `.git` path.

It admitted plain git run from the worktree, and chains, substitutions and
variables that it could parse. So Edit and Write paths stay under the worktree,
and one Bash rule, stricter than the guard on purpose, holds for the
orchestrator and in every subagent prompt it writes: **once isolated, each Bash
call is one plain command run with the worktree as its cwd**, naming no `.git`
path, with no chain, loop, substitution, variable as the command or heredoc.
Its one exception is a `run-file prove` test command passed as one quoted
`bash -c` argument ([evidence-lines.md](evidence-lines.md)), which the guard
admitted. `run-file note` takes one line per call, naming no `bash -c` or other
nested shell in its text (say "the prove recipe", not its command); a plain
command named as a `Criterion` proof was admitted. Text a script reads
(criteria, a body, a reply) goes into a file written with the Write tool and
passes by path. Where a form works only before isolation, the prose that hands
it out says so.

## The Run file

`run-file init <issue|slug> --from-profile` (`--criteria -` for an issue, before
isolation, [implement.md](implement.md#phase-1-criteria-the-run-owes)) is the
run's first write, placed by SKILL.md's [pipeline
step](../SKILL.md#the-pipeline). Init reads the profile's `Tripwires:`, its
Verification names, its reviewers and its `Legs:` into the checklist. The record
lives at `<root>/ship-<issue>/run.md`, `<root>` being
`${XDG_STATE_HOME:-~/.local/state}/ship/<repo key>` with the key hashed from the
git common dir, so the main checkout and every worktree share it. It sits
outside every checkout, so the gate never reads it and a session isolated by
`EnterWorktree`, whose guard refuses an Edit or Bash command naming a `.git`
path ([Once isolated](#once-isolated)), can still write it. `--scratchpad <dir>`
overrides the root. Init also prints `scratch`, `<root>/scratch-<issue>`, the
run's own scratch root, and opens phase 0. It refuses an existing record: a
resumed run reads that one, and a new run of an issue whose earlier run stopped
passes `--rebuild` to replace it. `cleanup` removes both directories after the
merge; a free-text run (`cleanup none`) leaves its `ship-<slug>/` and
`scratch-<slug>/` in place, and a run that stops before the merge gate leaves
its record.

The file is the run's **record**, the source of truth for where the run is and
the home of the design and plan; it survives a mid-run context summary, so work
straight through one, each phase at the width its lane gives it rather than the
width the remaining context suggests. Every later call finds it from the issue
alone, `--issue <issue>`, plus the same `--scratchpad` if init took one. The
task list is its **display**, one harness task per item: create all ten from
init's `items` in one parallel turn, then set phase 0 to its `mirror`, so the
human sees the phases still ahead and not only the open one. `run-file next
<n>` closes the open phase and opens `<n>` in one call and returns both `mirror`
values, two `TaskUpdate`s; with no phase open it only opens `<n>` (`closed:
null`), one `TaskUpdate`. `open`, `close` and `skip` flip one phase, and each
returns the `mirror` its task takes. Phase 4 may open while phase 3 is still
open, both stamped; a phase-4 fix that touches a path a Verification already ran
on re-runs that Verification scoped to the fix before phase 3 closes. `close 3`
takes one `--result <name>=<word>` per Verification named at init (`n/a` for one
whose `Applies when:` you judged false) and refuses while one is missing. A
Verification the PR adds to the profile owes a result too, since the merge
gate judges the profile's headings: pass it to `close 3` with the rest.
`skip 3 "<reason>"` settles each one still pending as `n/a: <reason>`, and only
those init named, so a Verification the PR adds before phase 3 settles goes to
`close 3` as a `--result`, not to `skip 3`. After phase 3 has closed or been
skipped, a Verification heading a later phase's fix adds, or a re-run of one
already recorded, takes the same `close 3 --result`, which records it (a
re-run's result replacing the entry) without re-opening phase 3 and leaves
phase 3's row and task as they are. Close a phase only once its `Done when:`
holds: the mechanic stamps whatever close it is given, except
that `close 3`, `close 4` and `close 7` refuse without the results or evidence
lines [evidence-lines.md](evidence-lines.md) and
[review-loop.md](review-loop.md#the-exit) format. A **small-lane** run keeps
all ten items and `skip`s each collapsed phase, so the record shows a decision
and not a gap; re-running `skip <n> "<reason>"` replaces a reason a wider diff
outgrew, though phase 3's settled results keep the first skip's reason. A
harness that refuses the task tools has answered: run on the file alone.

**Every attested line goes through `run-file note <section> <line> --issue
<issue>`** (`Round:`, `Stop:`, `Criterion <n>:`, `Noted:`, `Filed:`):
it appends one line under `## <section>` and creates one the record lacks,
where an editor or heredoc write to the file from a worktree-isolated session
draws a guard refusal.

**A phase-4 report is written to a Report file before one of its findings is
dispositioned**, `<scratch>/<role>/<role>-report.md`, or
`<scratch>/standards/standards-report-<n>.md` for round `<n>` of the
[self-review loop](implement.md#phase-4-the-self-review-loop) from round 2 on,
because a report held in context alone is lost to a compaction the Run file
survives. The `<role>-` prefix is required: Claude Code refuses a subagent's
Write to a file named `report*.md` ("subagents should return findings as text"),
a refusal measured on every bare `report*.md` a run tried, while a prefixed name
is admitted. Each dispatch names the file, and takes back only the path and a
summary of at most five lines. No file at the named path is a report that failed
to arrive: retry while retries make progress, as SKILL.md's stops say, then
`red-after-retry`. An inline pass writes the same file itself before
dispositioning. The Run file's `## Design and plan` names every round's paths as
each round is dispatched; disposition from the file, and quote the merge
summary's `Self-review` item from it. Phase 7's [local
review](review-loop.md#the-round) writes
`<scratch>/standards/standards-report-local.md` under the same rule, a path no
phase-4 round uses.

**A clobbered Run file** answers a flip with a refusal naming a missing line or
a missing file, and each refusal carries its check and the rebuild. A `--state`
with no recoverable range is `done` bare and reads `unverified` on the `Timing`
item, as does the phase re-opened at the rebuild; afterwards re-check the task
mirror against the file.
