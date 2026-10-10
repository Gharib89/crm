# Context discipline: a ship run is long; protect the main thread

## Contents

- [Model tiers](#model-tiers)
- [While a subagent is out, end the turn](#while-a-subagent-is-out-end-the-turn)
- [The Run file](#the-run-file)

A full ship touches many files across many turns. What bloats the window is raw
tool output landing in the main thread, not the work itself, so spend tokens on
decisions, not dumps.

**The delegation rule: delegate noise, not size.** A subagent earns its cost
only when the raw output you would otherwise ingest is much larger than the
conclusion you need: a multi-file map, a verification run, CI logs. When you
can already point at the target (one or two known files, a single test node,
one mechanic's projected JSON) work inline. A small-lane run typically spawns
none of its own; a session with no subagent tools runs everything inline and is
still a complete run. The levers, in rough order of impact:

- **Delegate reading, not just review.** Send the investigation to a cheap-tier
  subagent ("map how X, Y, Z connect; return signatures, call sites and data
  shapes") and read only the exact lines you will edit: a file you only need to
  *understand* stays out of main context.
- **Read one reference file per call.** Several files chained in one `cat`
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
failure signal. The directory is a **sibling** of the Run file's rather than a
child, so a path a subagent invents below it still lands clear of the record.
Edits to the repo go under the worktree prefix. Pass it the way you pass the
model tier: written into the prompt, every dispatch.

## Model tiers

Use the cheapest model that fits; reserve the strong tier for judgment.

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
a read of the output file buys nothing it does not deliver. This is the one
place where having nothing to do is the correct next action.

## The Run file

`run-file init <issue|slug> --from-profile` is the run's first action, placed
by SKILL.md's [pipeline step](../SKILL.md#the-pipeline). Init reads the
profile's `Tripwires:`, its Verification names, its reviewers and its `Legs:`
into the checklist. The record lives at
`<git common dir>/ship/ship-<issue>/run.md`, outside every working tree, so the
gate never reads it; `--scratchpad <dir>` overrides the root. Init also prints
`scratch`, `<git common dir>/ship/scratch-<issue>`, the run's own scratch root,
and opens phase 0. It refuses an existing record: a resumed run reads that one,
and a new run of an issue whose earlier run stopped passes `--rebuild` to
replace it. `cleanup` removes both directories after the merge; a free-text run
(`cleanup none`) leaves its `ship-<slug>/` and `scratch-<slug>/` in place, and a
run that stops before the merge gate leaves its record.

The file is the run's **record**, the source of truth for where the run is and
the home of the design and plan; it survives a mid-run context summary, so work
straight through one, each phase at the width its lane gives it rather than the
width the remaining context suggests. Every later call finds it from the issue
alone, `--issue <issue>`, plus the same `--scratchpad` if init took one. The
task list is its **display**, one harness task per open phase: `run-file next
<n>` closes the open phase and opens `<n>` in one call and returns both `mirror`
values, so a phase costs two task calls, a `TaskCreate` for the phase it opened
and a `TaskUpdate` to complete the one it closed. `open`, `close` and `skip`
flip one phase. Phase 4 may open while phase 3 is still open, both stamped; a
phase-4 fix that touches a path a Verification already ran on re-runs that
Verification scoped to the fix before phase 3 closes. `close 3` takes one
`--result <name>=<word>` per Verification named at init (`n/a` for one whose
`Applies when:` you judged false) and refuses while one is missing. Close a
phase only once its `Done when:` holds: the mechanic stamps whatever close it is
given, except that `close 3`, `close 4` and `close 7` refuse without the
results or evidence lines [implement.md](implement.md#phase-4-evidence-lines)
and [review-loop.md](review-loop.md#the-exit) format. A **small-lane** run keeps
all ten items and `skip`s each collapsed phase, so the record shows a decision
and not a gap; re-running `skip <n> "<reason>"` replaces a reason a wider diff
outgrew. A harness that refuses the task tools has answered: run on the file
alone.

**A phase-4 report is written to a Report file before one of its findings is
dispositioned**, `<scratch>/<role>/<role>-report.md`, because a
report held in context alone is lost to a compaction the Run file survives. Each
dispatch names the file, and takes back only the path and a summary of at most
five lines. No file at the named path is a report that failed to arrive: the
bounded retry, then `red-after-retry`. An inline pass writes the same file
itself before dispositioning. The Run file's `## Design and plan` names the
paths; disposition from the file, and quote the merge summary's `Self-review`
rows from it.

**A clobbered Run file** answers a flip with a refusal naming a missing line or
a missing file, and each refusal carries its check and the rebuild. A `--state`
with no recoverable range is `done` bare and reads `unverified` on the `Timing:`
row, as does the phase re-opened at the rebuild; afterwards re-check the task
mirror against the file.
