# The mechanics: what all of them have in common

## Contents

- [Which mechanic each phase runs](#which-mechanic-each-phase-runs)
- [Ask the script what its flags are](#ask-the-script-what-its-flags-are)
- [The exit codes](#the-exit-codes)
- [What a failed write says](#what-a-failed-write-says)
- [The vocabulary a read comes back in](#the-vocabulary-a-read-comes-back-in)
- [Run them inline](#run-them-inline)

`scripts/` holds one executable per deterministic step, and a mechanic is the
only way a ship run touches the host, though not every one does: `run-file`
writes the run's own record and nothing else. `SKILL.md` says what each phase
decides; this file says which mechanic the phase runs there and how every one of
them answers, so both are read once rather than re-derived per call.

## Which mechanic each phase runs

When a phase names a mechanic, run it instead of re-deriving what it wraps: it
is the single source of truth for that step, including the host adapter it
sources (`scripts/host/github.sh` or `scripts/host/ado.sh`, chosen from the
`origin` remote).

The table below maps mechanic to phase and carries no flags, because a table
goes stale against the script and `--help` does not. Its one row that is not a
mechanic, the repo's own local gate, keeps its flags: they come from the
local-gate contract, and that script answers no `--help`.

| Mechanic | Phase |
|---|---|
| `prepare` (runs `tooling`, then the Cloud lane `Bootstrap:`) | every run but the no-issue lane's inner one, before `run-file init` |
| `run-file init` | the required first action after `prepare` |
| `run-file open`, `run-file close`, `run-file skip`, `run-file timing` | every phase flip, and the merge summary's `Timing:` row |
| `preflight` | 0 |
| `read-issue` | 0 |
| `isolate` | 0 |
| `manage-issue` | 1; any stop after the claim; 3, to close a scratch issue a verification created; 9 |
| `file-issue` | 2, 4, 7 |
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

## Ask the script what its flags are

For a mechanic's flags, run `<base directory>/scripts/<mechanic>.sh --help`.
Every mechanic answers it with its usage line on stdout, exit 0 and nothing on
stderr, before it loads a host adapter and without reaching the host. Only the
first argument is read, so
`poll-pr.sh 42 --help` is a poll of PR 42 and not a help call.

A mechanic acting for one reviewer, `poll-pr` and `request-review`, takes it as
`--reviewer <name>`, the `### <name>` heading under the profile's
`## Reviewers`, and reads the rest off that block: the login, the landing rule,
the transport, the workflow run to await and the poll's default bound. The run
passes the block's name, the one the merge summary uses.

## The exit codes

Each prints one JSON verdict on stdout, a failing step's last 40 log lines on
stderr, and exits `0` ok, `1` the mechanic's own not-ok answer, `2` tooling. A
malformed invocation is tooling, exit 2 rather than 1: a missing or empty
positional, a flag where a positional belongs and a flag without its value all
print `{"error": "<usage>"}` and exit 2, as an unknown flag does. A leading
`--help` is the one exception, answered above before any of these guards runs.
Exit 1 is an answer, not always a fault: `nothing-ready` from `select`, a
not-actionable `preflight` and a `poll-pr` window that closed are all exit 1 and
none is red.

## What a failed write says

A failed write to an open PR's body or title, a comment on a PR or an issue,
or a thread reply carries the host's `status` beside its `error`: a 5xx or a
429 outlasted the mechanic's own backoff, so retrying is the fix; any other
number is the request itself, so read the body you sent. `null` is neither: the
call got no HTTP answer at all, so the host or the tooling between you and it is
what to look at.
`open-pr` and `file-issue` answer with the error alone, and their stderr carries
the host's own message. Read the JSON, then decide.

## The vocabulary a read comes back in

Reads come back in one vocabulary on both hosts: checks
`pending|success|failure`, mergeable `clean|conflict|unknown`, review
`approved|changes|comment`, and threads as `resolved: true|false` per thread, or
the whole `threads` field as the string `"unavailable"` when the state could not
be read. A thread's `id` is what `reply-thread` and `resolve-thread` take (on
GitHub, the thread's root review comment id, as a string); an id no thread
carries answers `no such thread`.

## Run them inline

Run mechanics **inline**: they project their own output, so a subagent there
burns budget to relay what an exit code already says. Poll loops are bounded and
foreground; reaching the bound leaves the question open, so re-run to extend it
rather than proceed, or pass a wider `--timeout` up front when the
profile's `Legs:` names a leg you know is slower than the bound.
