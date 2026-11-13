# Phase 7: a reviewer that did not converge

Read this file only when a reviewer's loop ends without converging: its last
round was the cap round, a docs-only round or a not-landed primary's, with fixes
that changed the tree, or a primary exits `not reviewed`, capped with findings
or `not landed`, so its fallback is driven. The loop itself is
[review-loop.md](review-loop.md).

## The local review

**The cap round, a docs-only round and a not-landed primary's last round each
get a local review before the merge gate** when their fixes changed the
tree, repeated while its own fixes change files: one `code-review` subagent,
Standards axis only, over the fix-only diff, its Report file
`<scratch>/standards/standards-report-local.md` so it never overwrites phase 4's
round 1. Its findings take the dispositions of
[review-loop.md](review-loop.md#triage-fix-reply); `Cap:` is unchanged and no
host round is requested. Skip this review for a
primary capped with findings or stopped `not landed` whose fallback then exited
`reviewed`: the fallback's host rounds read those fixes. The review goes to the
last reviewer that stopped at `cap`, primary or fallback, and to a capped or
not-landed primary whose fallback exited `not reviewed`.

A local review past a reviewer's `Cap:` is no `Round:` of that reviewer, so its
rounds still read 1 through `Cap:`. Once a review's fixes change no file, record
`Past-cap: <reviewer>: tree unchanged at <sha>` with `run-file note Evidence
"<line>" --issue <issue>`, `<sha>` the head that review read. `gate clean` still
holds on the reviewer, reporting its cap and that `<sha>`, because no host
reviewer read the fixes: the human reads them at the merge gate. Where the head
later moves past that `<sha>` (a hand-resolved conflict merge, a lint fix), the
hold names the range to read, `<sha>..<head>`.

A docs-only round before `Cap:` is spent (any docs-only round under `Cap:
None.`) records `Docs-only: <reviewer>: tree unchanged at <sha>` instead, with
`run-file note Evidence "<line>" --issue <issue>`, `<sha>` the head its local
review read, once that review's fixes change no file; a docs-only round at the
cap records `Past-cap:`. `gate clean` then clears that reviewer's `cap` stop
while `<sha>` covers the PR head, unless a re-raised declined finding caused
the stop. Where the head later moves past `<sha>`, `gate clean` holds, naming
`<sha>..<head>`.

## Fallbacks

A reviewer whose `Fallback-for:` names another stands in for it, on the runs
where that primary exits `not reviewed`, is **capped with findings** or stops
`not landed`; preflight holds it to on-request, since a reviewer that
fires on every push cannot be withheld. Drive every non-fallback reviewer to its
exit first, because a fallback's only input is how its primary exited.

- Primary `not reviewed` for any reason other than `inline lane`, capped with
  findings, or `Stop: <primary>: not landed`: drive the fallback as an ordinary
  on-request reviewer under its own `Cap:`. Capped with findings is `Stop:
  <primary>: cap` (or `cap at <sha>`) after it spent `Cap:` (rounds == Cap)
  on a last round whose fixes changed the tree, so no host round read them; a
  `cap` from a [re-raised declined finding](review-loop.md#the-exit) is not
  that and drives no fallback.
  Its exit is its own, and its `## Review` line and merge-summary block both
  name the primary's reason (`fallback for codex: capped with findings` or
  `fallback for codex: not landed: blocked`, say), the only record of why a
  second reviewer was paid for.
- Primary `reviewed` otherwise (it stopped on `tree unchanged at <sha>`, a last
  round whose findings were all declined included, on `small lane` or
  `auto-once`, on `cap` from a re-raised declined finding, or a docs-only
  fix-only diff ended its loop before `Cap:` was spent): do not request it; it
  exits `not invoked: <primary> reviewed`, so a reader sees the reviewer exists.
- Primary `not reviewed: inline lane`: request nothing; the fallback records
  `Stop: <fallback>: inline lane` too
  ([small-lane.md](small-lane.md#the-inline-lane)).
- Nothing is a fallback for a fallback: a chain is one deep.
