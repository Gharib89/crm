# The unattended run

`--unattended` means no human is present for the run. Nothing here is a
different pipeline: the same ten phases, with three things fixed by the flag.

## What the flag changes

1. **Isolation is the clone.** `isolate ... --in-place`: fetch, branch from
   `origin/HEAD` in the current checkout, no worktree, no carried files.
2. **A blocked stop hands back.** Every stop in SKILL.md's table that reads
   "attended: ask" becomes `manage-issue <issue> handback "<reason>"`; for a
   `hand-off`, the reason carries the exact command the human would have run.
   The run then returns with the reason: a fire either reaches merge-ready or
   hands the issue back, and leaves no issue claimed and spinning. Admission is
   narrower too: `ready-for-human` is the stop `ready-for-human: attended only`.
3. **The merge gate posts and returns**, as
   [merge-gate.md](merge-gate.md#unattended-post-to-the-pr-then-return)
   carries it.

Before any of it, **`prepare --unattended`**, which in this lane runs whether
or not the run is in a cloud sandbox, so a local `ship --unattended` still
reproduces a fire. Its first step is `tooling --install`, its second the
profile's `## Cloud lane` `Bootstrap:`. Read the token through preflight's
`user` and repo reads, which are the proof; `gh auth status` reports the working
token as invalid behind the sandbox proxy.

The sandbox proxy refuses GitHub GraphQL, where review-thread state lives, and
that is no failure: the GitHub adapter switches to REST routes on that refusal,
so the thread mechanics work as they do outside the sandbox, and `threads:
"unavailable"` means the read failed on both paths.

## The lane with no issue: `ship --unattended`

With no `<issue>`, ship runs the whole unattended lane before phase 0. It
writes nothing to the tracker until the claim in phase 1; the stops below
leave no trace on any issue.

1. **Prepare.** `prepare --unattended`, both steps before anything can be
   claimed. This `Bootstrap:` is sandbox-image repair for the repo's
   own stack, distinct from `## Worktree`'s, which runs in every lane after
   isolate.
2. **PR cap.** `list-prs --open`; at or above the profile's `PR cap:` (default
   3, `none` disables), stop `pr-queue-full`.
3. **Select.** `select`: the oldest open `ready-for-agent` issue with no
   assignee and no open blocker. No candidate: stop `nothing-ready`, the one
   clean no-op. The host's blocker query exists and failed: stop
   `blockers-unavailable`.
4. **Run** phases 0 to 9 on the selected issue as `ship <issue> --unattended`,
   skipping `prepare`, which is done.
5. **Report**: the PR link and the merge summary's location, or the stop
   reason verbatim.

The repo is derived from the clone's `origin` remote; no repo name is passed
anywhere.
