---
name: cloud-ship
description: >-
  Run one scheduled cloud fire: invoke `ship` unattended, relay its outcome.
  Use when a routine says "Run the cloud-ship skill"; humans run `/ship`.
metadata:
  version: 0.3.1
---

# cloud-ship

One **fire**: one selected issue driven to one merge-ready PR, ending at the
merge gate with no human present. This skill adds the invocation and the relay,
nothing else.

## The fire

1. **Invoke `ship` unattended.** Call the Skill tool with skill `ship` and
   arguments `--unattended`, no issue number. Ship runs the whole unattended
   lane (`reference/unattended.md` in its own folder) and returns.
2. **Relay.** Report that lane's step-5 result verbatim, prefixed with
   `cloud-ship <version>`: the PR link and where the merge summary is, or the
   stop reason as ship named it. With a PR, add each reviewer's exit as ship's
   merge summary words it in its `Review` block. The harness strips this file's
   frontmatter on load, so read the version with `sed -n 's/^  version: //p'
   <base directory>/SKILL.md`.

## The one invariant

Hand the sandbox clone over untouched for `isolate --in-place` to branch, let
ship run every mechanic and write every claim, label, comment and hand-back,
and end the fire where ship returns.
