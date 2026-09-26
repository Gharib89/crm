# The mechanics: what all of them have in common

`scripts/` holds one executable per deterministic step, and not every one
touches the host: `run-file` writes the run's own record and nothing else.
`SKILL.md` says what each phase decides; this file says which mechanic the phase
runs and how every one of them answers.

## Which calls need a mechanic

Every host **write**, and every **gating read** (one a phase's `Done when:` or a
stop row depends on: `preflight`, `poll-pr`, `ci-wait`, `base-fresh` and the
like), goes through a mechanic, so create-then-verify, the host status on a
failed write and the same verdict from the same host state all hold. One no
mechanic performs is a Ship defect. An **informational read**, one no phase or
stop branches on, may be made directly where no mechanic covers it: through the
host's REST form (`gh api`, `az rest`), which the cloud sandbox admits where it
refuses GitHub GraphQL, with one line in the Run file's `## Direct reads`
naming the call and why. A read made directly in two runs is a candidate
mechanic. Verification scaffolding, a scratch issue or scratch review thread a
`Run:` line sets up by hand, sits outside the rule.

## Which mechanic each phase runs

A phase that names a mechanic runs it rather than re-deriving what it wraps,
the host adapter it sources (`scripts/host/github.sh` or `scripts/host/ado.sh`,
chosen from the `origin` remote) included. The table carries no flags, because
a table goes stale against the script and `--help` does not; the one row that
is not a mechanic, the repo's own local gate, keeps its flags from the
local-gate contract.

| Mechanic | Phase |
|---|---|
| `prepare` (runs `tooling`, then the Cloud lane `Bootstrap:`) | every run but the no-issue lane's inner one, before `run-file init` |
| `run-file init` | the required first action after `prepare` |
| `run-file open`, `run-file close`, `run-file skip`, `run-file timing` | every phase flip, and the merge summary's `Timing:` row |
| `preflight` | 0 |
| `read-issue` | 0 |
| `isolate` | 0 |
| `manage-issue` | 1; any stop after the claim; 3, to close a scratch issue a verification created; 9 |
| `file-issue` | 2, 4, 7; 9 with `--repo`, per Ship defect draft, on the human's word |
| `base-fresh` | 5, and after every conflict resolution |
| `<Location:>` from the profile `[--small <node>] [--base <ref>]` | 5 (the repo's own local gate) |
| `open-pr` | 6 |
| `reflect` | 6 |
| `update-pr-title` | 6, 9 |
| `read-pr` | 6 and 7, reading a PR back after a title or body write |
| `poll-pr` | 7, 8 |
| `request-review` | 7 |
| `comment-issue` | 2, 4, 7 |
| `comment-pr` | 7, 9 |
| `reply-thread` | 7 |
| `update-pr-body` | 7 |
| `update-issue-body` | 9, after `merge` answers `merged: true`, once per tracker draft |
| `resolve-thread` | 7 |
| `ci-wait` | 8 |
| `merge` | 9, on approval |
| `cleanup` | 9, after merge |
| `list-prs` and `select` | unattended lane |

## Flags, exit codes and failed writes

For a mechanic's flags, run `<base directory>/scripts/<mechanic>.sh --help`: its
usage line on stdout, exit 0, before it loads a host adapter. Only the first
argument is read, so `poll-pr.sh 42 --help` is a poll of PR 42. A mechanic
acting for one reviewer (`poll-pr`, `request-review`) takes `--reviewer
<name>`, the `### <name>` heading under `## Reviewers`, and reads the rest off
that block.

Each prints one JSON verdict on stdout, a failing step's last 40 log lines on
stderr, and exits `0` ok, `1` the mechanic's own not-ok answer, `2` tooling. A
malformed invocation is tooling: it prints `{"error": "<usage>"}` and exits 2.
Exit 1 is an answer, not always a fault: `nothing-ready` from `select`, a
not-actionable `preflight` and a `poll-pr` window that closed are all exit 1 and
none is red.

A failed write to a PR body or title, a comment or a thread reply carries the
host's `status` beside its `error`: a 5xx or 429 outlasted the mechanic's own
backoff, so retrying is the fix; any other number is the request itself, so read
the body you sent; `null` is no HTTP answer at all, so look at the host or the
tooling in between. `open-pr` and `file-issue` answer with the error alone, and
their stderr carries the host's message. Under `--repo`, every exit 1 of `file-issue` and
`update-issue-body`, an unreachable host's or a refused write's, carries a
`command` beside the `error`: the shell-quoted invocation for the human to run
where the write succeeds.

## The vocabulary a read comes back in

Reads come back in one vocabulary on both hosts: checks
`pending|success|failure`, mergeable `clean|conflict|unknown`, review
`approved|changes|comment`, and threads as `resolved: true|false` per thread, or
the whole `threads` field as the string `"unavailable"` when the state could not
be read. A thread's `id` is what `reply-thread` and `resolve-thread` take (on
GitHub, the thread's root review comment id, as a string).

Run mechanics **inline**: they project their own output, so a subagent there
burns budget to relay what an exit code already says. Poll loops are bounded and
foreground; reaching the bound leaves the question open, so re-run to extend it,
or pass a wider `--timeout` up front for a leg you know is slower than the bound.
