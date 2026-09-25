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
one mechanic's projected JSON) work inline; a subagent there costs more than it
saves. A small-lane run typically spawns none of its own; a large or unfamiliar
change may spawn several. The rule is a lever rather than a dependency: a session
with no subagent tools runs everything inline and is still a complete run.
Every lever below is subject to it, in rough order of impact:

- **Delegate reading, not just review.** Send the investigation to a cheap-tier
  subagent ("map how X, Y, Z connect; return signatures, call sites and data
  shapes"), with the `map` scratch directory below, and read only the exact
  lines you will edit. A file body you only
  need to *understand* stays out of main context; only the hunk you *change*
  enters it.
- **Mechanics already project.** Every host read comes through a mechanic that
  returns a small JSON; there is no bare host CLI call to over-fetch with.
- **Investigate inside the worktree from the start**, so every file you read is
  the copy you will edit, rather than a main-checkout copy read twice.
- **Targeted test nodes during the loop; the full suite only at the local
  gate.** Re-running the whole suite every cycle is slow noise.
- **Delegate noisy verification runs.** A phase-3 verification that dumps
  volumes goes to a cheap-tier subagent, with the `verify` scratch directory
  below, returning `pass | fail` plus the failing lines. The local gate,
  `base-fresh`, `poll-pr` and `ci-wait` project their own output: run them
  inline.
- **One Run file** for the checklist and the design and plan. It survives a
  mid-run context summary; the same summary repeated across turns does not.

**Every subagent prompt names the scratch directory that subagent writes in.** A
subagent handed nowhere to write reaches for the scratchpad its own environment
block names, which is the Run file's parent, and a scratch file written there
can overwrite the checklist with no failure signal. So every prompt carries one
line naming `<scratchpad>/scratch/<role>/` as the one place that subagent writes
scratch of its own, `<role>` being its job in the run (`map`, `execute`,
`verify`, `standards`, `spec`, `writing`). Edits to the repo are
separate, and go under the worktree prefix. It is a **sibling** of the Run file's
directory rather than a child, so a path a subagent invents below the one it was
given still lands clear of the record. Pass it the way you pass the model tier:
written into the prompt, every dispatch.

## While a subagent is out, end the turn

Dispatch a composed skill's subagents, then **end the turn**. The completion
notification is what resumes the run, and it arrives on its own: every call
spent asking whether the result is ready yet (a poll loop, a sleep, a status
ping, an agent listing, a read of the output file) buys nothing the
notification does not deliver.

A turn that ends with work dispatched has already acted: this is the one place
where having nothing to do is the correct next action.

## First action: the Run file

**Before phase 0, before the worktree**, run `run-file init <issue|slug>
--scratchpad <dir>`, where `<dir>` is the scratchpad directory the harness names
in its environment block, the OS temp directory where it names none, and a path
outside the repo either way, since one inside it would dirty the tree the local
gate reads. Hand it the profile's `Tripwires:`, the applicable verifications,
the reviewer list and the CI `Legs:` (`--tripwires`, `--verifications`,
`--reviewers`, `--legs`): it writes the ten-item checklist to
`<scratchpad>/ship-<issue>/run.md`, a directory of its own so a subagent that
reaches for the run's own name cannot overwrite the record, and returns the
items, one per harness task you then create (`TaskCreate`). The file is the
run's **record**, the source of truth for where the run is and the home of the
design and plan as they form; it survives a mid-run context summary, so work
straight through one in the same session, each phase at the width its lane gives
it rather than the width the remaining context suggests. The harness task list
is its **display**: flip a phase with `run-file open <n>`, `close <n>` or `skip
<n> "<reason>"` against `--file <path>`, or against `--issue <issue>
--scratchpad <dir>`, which resolves the same path from the two facts a compacted
context still holds; then set that phase's task to the
`mirror` value the flip returned (`TaskUpdate`); close a phase only once its
verification passed, which is judgement the mechanic cannot hold: it stamps
whatever close it is given. It owns the stamp, the one-open-phase invariant and
every refusal, and `run-file timing` computes the merge summary's `Timing:` row;
a **small-lane** run keeps all ten items and `skip`s each collapsed phase, so
the record shows a decision and not a gap. A later phase can widen the diff
past the reason a phase was skipped under: re-run `skip <n> "<reason>"` with the
corrected wording and it replaces the old reason in place, leaving `init
--rebuild` below to a clobbered file. A harness that refuses
the task tools has answered: run on the file alone.

**A phase-4 report is written to a Report file before one of its findings is
dispositioned.** Both `code-review` axis reports and the `writing-for-agents`
pass's report would otherwise reach the run in context alone, where a compaction
takes them while the Run file survives. So each dispatch names the subagent's
Report file, `<scratchpad>/scratch/<role>/<role>-report.md` under the role that
produces it, asks the subagent to write its report there, and takes back only
the path and a summary of at most five lines: a report handed back whole and
re-typed to disk spends its length twice. No file at the named path when the
subagent hands back is a report that failed to arrive: the bounded retry, then
`red-after-retry`, since the run holds five lines and not the report. An inline
pass (the small lane's `writing-for-agents` pass) has no hand-back: it writes
the same file itself before any of its findings is dispositioned. The Run
file's `## Design and plan` section names the paths, the role in each filename
so a row of the merge summary names its report and not a third `report.md`.
Disposition from the file, re-read it there after a compaction, and quote the
merge summary's `Self-review` rows from it rather than from the transcript.

**A refusal naming a line you thought was there is a clobbered Run file**: a
subagent wrote over the path, and the mechanic says so rather than flipping a
line that is not present. Rebuild it in place with `run-file init ... --rebuild`
and one `--state <n>=<spec>` per phase this transcript and the task list still
account for: `done:<HH:MM→HH:MM>` where the range is recoverable, `done` where it
is not, `skipped:<reason>`, and `open` for the phase that was running, which is
re-opened at the current clock. Invent no range; a phase left without one reads
`unverified` on the `Timing:` row, as does the phase re-opened at the rebuild.
Then log it in the deviations log (what stood in the file's place, what was
lost, which ranges are unverified) and re-check the mirror against the rebuilt
file.
