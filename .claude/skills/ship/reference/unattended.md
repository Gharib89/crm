# The unattended run

`--unattended` means no human is present for the run. The `cloud-ship` sibling
invokes `ship --unattended` from a cloud routine and relays the outcome; a
human can run the same command locally to reproduce a fire exactly. Nothing
here is a different pipeline: the same ten phases, with three things fixed by
the flag.

## What the flag changes

1. **Isolation is the clone.** `isolate ... --in-place`: fetch, branch from
   `origin/HEAD` in the current checkout, no worktree, no carried files. The
   sandbox clone is disposable and already isolated.
2. **A blocked stop hands back.** Every stop in SKILL.md's table that reads
   "attended: ask" becomes a hand-back
   (`manage-issue <issue> handback "<reason>"`); for a `hand-off`, the reason
   carries the exact command the human would have run. The run then returns with
   the reason. A fire either reaches merge-ready or hands the issue back; it
   leaves no issue claimed and spinning. Admission is narrower too:
   `preflight <issue> --unattended` makes `ready-for-human` the stop
   `ready-for-human: attended only`.
3. **The merge gate posts and returns.** `comment-pr` with the uncompressed
   summary, then return with the PR link. No waiting, no polling, no merge. The
   claim holds; the open PR is what keeps later fires off the issue.

Before any of it, **`prepare --unattended`**, which in this lane runs whether
or not the run is in a cloud sandbox, so a local `ship --unattended` still
reproduces a fire. Its first step is `tooling --install`: the cloud sandbox
image has `jq`, `curl` and `git` but not the host's CLI, and only the host
adapter knows what that is and how it installs (GitHub: `apt-get install -y
gh`, the one route the sandbox proxy passes). Core in every repo, which is why
it sits in a mechanic rather than on a profile `Bootstrap:` line. Its second is
the profile's `## Cloud lane` `Bootstrap:`. An attended run inside a cloud
sandbox runs the same `prepare`; a failed step stops it under the same stop
word and asks, with the failing step's tail. Read the token through
preflight's `user` and repo reads, which are the proof; `gh auth status`
reports the working token as invalid behind the sandbox proxy.

Two sandbox facts the run meets and neither is a failure. The proxy refuses
GitHub GraphQL, where review-thread state lives, and names REST routes in its
place; the GitHub adapter switches to those on that refusal, so `poll-pr`,
`reply-thread` and `resolve-thread` work on threads as they do outside the
sandbox (a bot's thread `author` keeps its `[bot]` suffix there) and a reviewer
exits by its normal rules. `threads: "unavailable"`, and with it `degraded:
unreachable`, now means the thread read failed on both paths. Remote
ref deletion is blocked both ways, which costs nothing here: the lane returns
at the merge gate, and `merge`, `cleanup` and the `update-issue-body` command
the summary carries for each tracker draft
([merge-gate.md](merge-gate.md#a-tracker-issue-on-targets)) run from a human's
machine.

Unchanged: `defer-to-ci` is the only verification disposition that proceeds
(`hand-off` and `blocked` hand back), and an `unexercised` result proceeds on
its own, with no disposition behind it and nothing to hand back for; a degraded
reviewer exit still proceeds to the merge gate on green CI and is reported there
rather than handed back; the local gate's `unavailable` hands back with `local
gate unavailable: <gates>`, leaving the PR unopened. Compose skills with an
explicit unattended signal (`code-review`, `tdd` and any reviewer helper); they
cannot infer the absence of a human.

## The lane with no issue: `ship --unattended`

With no `<issue>`, ship runs the whole unattended lane before phase 0. It
writes nothing to the tracker until the claim in phase 1; the stops below
leave no trace on any issue.

0. **Prepare.** `prepare --unattended`, one call running both steps before
   anything can be claimed; `failed: "tooling"`: stop `host-unreachable`.
1. **Bootstrap.** Already run as `prepare`'s second step, not run again;
   `failed: "bootstrap"`: stop `bootstrap-failed`. It is sandbox-image repair
   for the repo's own stack, distinct from `## Worktree`'s `Bootstrap:`, which
   runs in every lane after isolate.
2. **PR cap.** `list-prs --open`; at or above the profile's `PR cap:` (default
   3, `none` disables), stop `pr-queue-full`. One operator's merge-review queue
   is the bottleneck, not the backlog.
3. **Select.** `select`: the oldest-created open issue labelled `ready-for-agent`,
   with no assignee and no open blocker, walking candidates ascending
   until one passes. No candidate: stop `nothing-ready`, the one clean no-op.
   The host's blocker query exists and failed: stop `blockers-unavailable`;
   shipping a dependent issue out of order builds a PR on unmerged work, so the
   stop stands until the query answers.
4. **Run** phases 0 to 9 on the selected issue as `ship <issue> --unattended`;
   preparation is done, so skip `prepare`.
5. **Report**: the PR link and the merge summary's location, or the stop
   reason verbatim (ship's own reasons pass through unchanged, including
   `host-unreachable` from preflight).

The repo is derived from the clone's `origin` remote; no repo name is passed
anywhere. The `cloud-ship` sibling is this lane's only caller from a routine:
it invokes the `ship` skill with `--unattended` through the Skill tool and
relays step 5 verbatim, leaving every mechanic to ship.
