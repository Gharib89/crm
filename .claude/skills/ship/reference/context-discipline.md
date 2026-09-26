# Context discipline: a ship run is long; protect the main thread

## Contents

- [While a subagent is out, end the turn](#while-a-subagent-is-out-end-the-turn)
- [First action: the Run file](#first-action-the-run-file)

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
`<scratchpad>/scratch/<role>/`, `<role>` being its job in the run (`map`,
`execute`, `verify`, `standards`, `spec`, `writing`). A subagent handed nowhere
to write reaches for the scratchpad its own environment block names, which is
the Run file's parent, and can overwrite the checklist with no failure signal.
The directory is a **sibling** of the Run file's rather than a child, so a path a
subagent invents below it still lands clear of the record. Edits to the repo go
under the worktree prefix. Pass it the way you pass the model tier: written into
the prompt, every dispatch.

## While a subagent is out, end the turn

Dispatch a composed skill's subagents, then **end the turn**. The completion
notification resumes the run on its own: a poll loop, a sleep, a status ping or
a read of the output file buys nothing it does not deliver. This is the one
place where having nothing to do is the correct next action.

## First action: the Run file

**Before phase 0, before the worktree**, run `run-file init <issue|slug>
--scratchpad <dir>`, where `<dir>` is the scratchpad directory the harness names
in its environment block, else the OS temp directory: outside the repo either
way, since a path inside it would dirty the tree the local gate reads. Hand it
the profile's `Tripwires:`, the applicable verifications, the reviewer list and
the CI `Legs:`. It writes the ten-item checklist to
`<scratchpad>/ship-<issue>/run.md` and returns the items, one harness task each
(`TaskCreate`).

The file is the run's **record**, the source of truth for where the run is and
the home of the design and plan; it survives a mid-run context summary, so work
straight through one, each phase at the width its lane gives it rather than the
width the remaining context suggests. The task list is its **display**: flip a
phase with `run-file open|close|skip`, against `--file <path>` or `--issue
<issue> --scratchpad <dir>` (the two facts a compacted context still holds),
then set its task to the `mirror` value the flip returned. Close a phase only
once its `Done when:` holds: the mechanic stamps whatever close it is given. A
**small-lane** run keeps all ten items and `skip`s each collapsed phase, so the
record shows a decision and not a gap; re-running `skip <n> "<reason>"` replaces
a reason a wider diff outgrew. A harness that refuses the task tools has
answered: run on the file alone.

**A phase-4 report is written to a Report file before one of its findings is
dispositioned**, `<scratchpad>/scratch/<role>/<role>-report.md`, because a
report held in context alone is lost to a compaction the Run file survives. Each
dispatch names the file, and takes back only the path and a summary of at most
five lines. No file at the named path is a report that failed to arrive: the
bounded retry, then `red-after-retry`. An inline pass writes the same file
itself before dispositioning. The Run file's `## Design and plan` names the
paths; disposition from the file, and quote the merge summary's `Self-review`
rows from it.

**A clobbered Run file** answers a flip with a refusal naming a missing line,
and that refusal carries the rebuild. A `--state` with no recoverable range is
`done` bare and reads `unverified` on the `Timing:` row, as does the phase
re-opened at the rebuild; afterwards re-check the task mirror against the file.
