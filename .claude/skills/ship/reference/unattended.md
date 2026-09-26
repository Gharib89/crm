# The unattended run

`--unattended` means no human is present for the run. The `cloud-ship` sibling
invokes `ship --unattended` from a cloud routine and relays the outcome; a
human can run the same command locally to reproduce a fire exactly. Nothing
here is a different pipeline: the same ten phases, with three things fixed by
the flag.

## What the flag changes

1. **Isolation is the clone.** `isolate ... --in-place`: fetch, branch from
   `origin/HEAD` in the current checkout, no worktree, no carried files.
2. **A blocked stop hands back.** Every stop in SKILL.md's table that reads
   "attended: ask" becomes `manage-issue <issue> handback "<reason>"`; for a
   `hand-off`, the reason carries the exact command the human would have run.
   The run then returns with the reason: a fire either reaches merge-ready or
   hands the issue back, and leaves no issue claimed and spinning. Admission is
   narrower too: `ready-for-human` is the stop `ready-for-human: attended only`.
3. **The merge gate posts and returns.** `comment-pr` with the uncompressed
   summary, then return with the PR link. The claim holds; the open PR is what
   keeps later fires off the issue.

Before any of it, **`prepare --unattended`**, which in this lane runs whether
or not the run is in a cloud sandbox, so a local `ship --unattended` still
reproduces a fire. Its first step is `tooling --install`: the sandbox image has
`jq`, `curl` and `git` but not the host's CLI, and only the host adapter knows
what that is and how it installs (GitHub: `apt-get install -y gh`, the one route
the sandbox proxy passes). Its second is the profile's `## Cloud lane`
`Bootstrap:`. Read the token through preflight's `user` and repo reads, which
are the proof; `gh auth status` reports the working token as invalid behind the
sandbox proxy.

Two sandbox facts the run meets, neither a failure. The proxy refuses GitHub
GraphQL, where review-thread state lives; the GitHub adapter switches to REST
routes on that refusal, so the thread mechanics work as they do outside the
sandbox, and `threads: "unavailable"` means the read failed on both paths.
Remote ref deletion is blocked both ways, which costs nothing: the lane returns
at the merge gate, and `merge`, `cleanup` and the tracker commands run from a
human's machine.

Unchanged: `defer-to-ci` is the only verification disposition that proceeds,
and an `unexercised` result proceeds with nothing to hand back for; a `not
reviewed` reviewer still proceeds to the merge gate on green CI; the local
gate's `unavailable` hands back with `local gate unavailable: <gates>`, the PR
unopened. Compose skills with an explicit unattended signal (`code-review`,
`tdd` and any reviewer helper); they cannot infer the absence of a human.

## The lane with no issue: `ship --unattended`

With no `<issue>`, ship runs the whole unattended lane before phase 0. It
writes nothing to the tracker until the claim in phase 1; the stops below
leave no trace on any issue.

1. **Prepare.** `prepare --unattended`, both steps before anything can be
   claimed: `failed: "tooling"` is `host-unreachable`, `failed: "bootstrap"` is
   `bootstrap-failed`. This `Bootstrap:` is sandbox-image repair for the repo's
   own stack, distinct from `## Worktree`'s, which runs in every lane after
   isolate.
2. **PR cap.** `list-prs --open`; at or above the profile's `PR cap:` (default
   3, `none` disables), stop `pr-queue-full`. One operator's merge-review queue
   is the bottleneck, not the backlog.
3. **Select.** `select`: the oldest open `ready-for-agent` issue with no
   assignee and no open blocker. No candidate: stop `nothing-ready`, the one
   clean no-op. The host's blocker query exists and failed: stop
   `blockers-unavailable`, because shipping a dependent issue out of order builds
   a PR on unmerged work.
4. **Run** phases 0 to 9 on the selected issue as `ship <issue> --unattended`,
   skipping `prepare`, which is done.
5. **Report**: the PR link and the merge summary's location, or the stop
   reason verbatim.

The repo is derived from the clone's `origin` remote; no repo name is passed
anywhere. `cloud-ship` invokes the `ship` skill with `--unattended` through the
Skill tool and relays step 5 verbatim, leaving every mechanic to ship.
