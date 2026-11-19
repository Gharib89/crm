# The rework run

`/ship <issue> --rework ["<feedback>"]` reworks the PR an earlier run of the
issue, attended or unattended, left open at the merge gate. The change to make
is its **feedback**, from three sources: the PR's unresolved review threads
whose latest comment is not Ship's own, the PR's authoritative comments posted
after the **head push** (the push that put the PR's head at its sha; the pushing
run saw older ones), less Ship's own, and the feedback text, if any. Preflight
drops each thread Ship answered last, and it is feedback again once someone
replies in it. A PR comment is authoritative as
[implement.md](implement.md#phase-1-spec-precedence) defines it, and preflight
drops every other one. The run keeps the claim, the branch and the PR, and ends
at a fresh merge gate. It is attended only. `--unattended` and a task-spec run
(free text instead of the issue number) are refused with preflight's usage line,
so `cloud-ship` never starts one. `--inline` composes: the inline lane's rules
apply to the rework's own diff.

The ten phases run as SKILL.md has them; this file lists only what changes, and
a replacement step satisfies any `Done when:` naming the step it replaces.

## Before phase 0

`run-file init <issue> --from-profile --criteria -`, the effective spec read
first; phase 1's feedback criteria stay in the plan. When it refuses an existing
record, run phase 0's `preflight` first, since it writes nothing, and repeat the
init with `--rebuild` only on its `ok: true`: a stop leaves the earlier record
intact. Every rework run gets a new Run file and a fresh `Cap:` per reviewer.

Then, before any edit (`--rework` only), decide if the PR is still wanted and
record it in `## Design and plan`: **modernize** (bring it to the current base
and standards), **rework** (address the review as asked) or **close** (no
longer wanted). On **close**, ask the owner, who closes it or picks another
answer: the run never closes a PR.

## Phase 0: admission and the worktree

`preflight <issue> --rework`, adding `--feedback` when the invocation carried
text. In place of `already claimed`, `existing PR` and `existing branch` it
admits the run only when the claim is your identity's and exactly one open PR
closes the issue, its head being the one `*-<n>` branch on origin. Any other
shape is `not reworkable: <detail>`. An admitted PR with no feedback thread, no
such PR comment and no human text is `no feedback`. Both stops end the run
before any change, the claim unchanged. On `ok`, preflight's `rework` names
`{pr, branch, head_sha, worktree, threads, comments}`, each comment
`{author, created_at, body}`.

`isolate <issue> <type> <slug> --rework` with the profile's `Carry:` files,
where `<type>` and `<slug>` are split from `rework.branch`
(`<type>/<slug>-<issue>`). It reuses the worktree an ended attended run left, if
clean and at the PR's head (`reused: true`), and otherwise checks the PR branch
out into a new worktree. Its refusal
`existing branch: <branch> has commits origin lacks` stops the run like
`not reworkable`. Its `base` is the PR's head before this run: write it to
`## Design and plan` as the **rework base**.

## Phase 1: the feedback is the spec delta

`manage-issue <issue> take` answers `claim: held`, which here meets
`claim: taken`. Read each thread in `rework.threads` whole with
`poll-pr <pr> --brief --full open`. Write one criterion per thread, one per
comment in `rework.comments`, then the human's text, which wins any conflict.
A thread or comment is triaged the way phase 7 triages a reviewer finding:
fixed, or declined with a reason. The issue body still binds wherever the
feedback leaves it alone.

## Phase 2: the lane keys

The lane keys, and the small lane's size, measure the rework's own diff,
`git diff --numstat <rework base>..HEAD`, not the PR's diff against
`origin/HEAD`.

## Phase 6: the same PR

1. `open-pr <issue> --rework` pushes and answers with the open PR, creating
   none. Its `created_at` is null.
2. Rewrite the title with `update-pr-title` when it no longer matches `Grade:`.
3. Rewrite each body section the rework changed with
   `update-pr-body --section --body-file`. The Change outline and Verification
   sections always need it.
4. Skip `reflect`, since the issue already links the PR.
5. Answer each feedback thread with `reply-thread`, saying what changed or why
   it was declined, then `resolve-thread` each one fixed; a declined thread
   stays open for the human.

## Phase 7: reviewers

Rounds run as usual. With `created_at` null:

- An `on-request` reviewer's round 1 skips the poll for a round the host opened
  unbidden with the PR.
- An `auto-once` reviewer exits `not reviewed: auto-once round spent by the
  earlier run`.

## Phase 9: a fresh merge gate

Write the summary per merge-gate.md, naming each comment from `rework.comments`
by author and time with what it changed, or why it was declined: a comment has
no thread to answer in. Then post it with `comment-pr <pr> --body-file`, which
marks it as Ship's own, so a later rework never reads it as feedback. Then take
the attended `Merge:` branch.
