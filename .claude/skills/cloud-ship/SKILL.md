---
name: cloud-ship
description: >-
  Run one fire of a scheduled cloud routine: invoke `ship` unattended and relay
  its outcome. Composes `ship`. Use only from a cloud routine's prompt; a human
  runs `/ship`.
metadata:
  version: 2.0.0
---

# cloud-ship

One **fire**: one selected issue driven to one merge-ready PR, ending at the
merge gate with no human present. Everything a fire does is `ship`'s: the
sandbox tooling, the profile's cloud bootstrap, the PR cap, the selection, the
claim, the branch, the isolation, the hand-back and the merge summary. This
skill adds the invocation and the relay, nothing else. The copy under
`.claude/skills/cloud-ship` is a **derived copy**, the same bytes in every repo,
changed in its source repo `Gharib89/skills` and refreshed through the command
the repo's `### Ship` block in CLAUDE.md carries.

## The fire

1. **Invoke `ship` unattended.** Call the Skill tool with skill `ship` and
   arguments `--unattended`, no issue number. Ship runs the whole unattended
   lane (`reference/unattended.md` in its own folder): tooling, the profile's
   `## Cloud lane` bootstrap, the PR cap, selection, then phases 0 to 9 on the
   selected issue, posting the merge summary as a PR comment and returning.
2. **Relay.** Report ship's step-5 result verbatim, prefixed with
   `cloud-ship <version>` (the `metadata.version` above): the PR link, where
   the merge summary is, and each reviewer's exit off its `Review` block
   (`reviewed`, `not reviewed: <reason>` or `not invoked: <primary>
   reviewed`), or the stop reason as ship named it
   (`bootstrap-failed`, `pr-queue-full`, `nothing-ready`,
   `blockers-unavailable`, `host-unreachable`, or any stop from ship's table).
   `nothing-ready` is the one clean no-op; every other stop is a fire worth
   reading in the routine log.

## The one invariant

Git, the tracker, the mechanics and the merge gate are ship's on both sides of
the invocation, so a behaviour defined here would be a second definition of one
ship already owns: hand the sandbox clone over untouched for
`isolate --in-place` to branch, let ship run every mechanic itself, and end the
fire where ship returns, relaying what it said, because a human merges from the
PR and the open PR and the claim keep the next fire off that issue. The tracker
is the guardrail: leave every claim, label, comment and hand-back to ship, which
holds them from selection onwards, and before selection there is no issue to
write to.

## The routine prompt

One sentence, identical in every repo, because the derived copy is the same
bytes everywhere and ship derives the repo from the clone's remote:

```
Run the cloud-ship skill.
```
