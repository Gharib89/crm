# Phases 1 and 2: when the issue and the tree disagree, or outgrow one PR

Read this file only on its branch: an anchor the tree contradicts that changes
a criterion, a criterion asking for a host action no mechanic performs, or a
change you judge too big for one PR. The common path is
[implement.md](implement.md).

## An anchor that changes a criterion

1. Rewrite the issue section the anchor sits in to match the tree with
   `update-issue-body <issue> --section <name> --body-file <path>`, the
   original section kept below the rewrite in a column-0 `<details>` block. The
   mechanic carries that `<details>` record through every later write to the
   section, so a later file holds the new content alone. An anchor in
   the preamble, which the mechanic leaves alone, is restated in the section
   whose criteria build on it.
2. Build against the rewritten criteria, and log the substitution in the
   deviations log.

A contradiction that leaves the issue nothing to build is the `mis-specified`
stop instead.

## A criterion no mechanic can perform

A criterion that asks for a host action no mechanic performs (a repo setting, a
branch protection, an installed app) is one the run cannot meet, and the run
never shrinks the issue's scope on its own authority: the criterion stays
verbatim in the issue body.

- **Unattended**: deliver the rest, log the gap in the deviations log, and list
  the criterion on the merge summary's `Unmet criteria` item with the action a
  human must take. Merging then needs the human's explicit waiver of each one
  ([merge-gate.md](merge-gate.md#unmet-criteria)).
- **Attended**: ask the human which way before building; an answer that the
  issue is wrong is the `mis-specified` stop, and one to build the rest makes
  it an unmet criterion as above.

## Needs-split

Whether the change outgrows one PR, or needs a redesign the issue did not
scope, is judgment, and it often shows before the diff size does. Tells worth
weighing, as examples rather than thresholds:

- **The same workaround recurring**: another copy of one shim, guard or special
  case, which the design the issue assumed should have made unnecessary.
- **Escape-hatch types**: an `any`, a cast, an untyped map or a stringly-typed
  field added only so the change fits the code around it.
- **A wide blast radius**: one signature, file format or vocabulary value whose
  change must reach many call sites in lockstep (ten or so is a fair mark).

The split proposal is a set of tickets, each landing green on its own. Where
the change reshapes something callers reach, those tickets follow expand,
migrate, contract: **expand** adds the new shape beside the old one with no
caller switched; **migrate** moves the callers over in batches sized by the
blast radius, one ticket per batch; **contract** removes the old shape once
nothing reaches it. Give each ticket its title, its criteria and the ticket it
is blocked by.
