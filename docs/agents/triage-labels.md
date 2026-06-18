# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |
| _(local — no role)_        | `agent-working`      | Claimed by `/ship` — in progress, or PR open awaiting human merge |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Agent work lifecycle (`/ship`)

`agent-working` is the **claim** state that gives an issue exactly one owner while
`/ship` works it — without it, a second interactive run or a scheduled fire could
pick the same issue. `/ship` drives the lifecycle itself (skill phases 1 and 6); you
don't relabel by hand:

1. **Claim (phase 1, before implementing):** remove `ready-for-agent`, add
   `agent-working`, and comment that the issue is claimed. Claiming is idempotent —
   re-applying it on an already-claimed issue is fine (a scheduled run may pre-claim).

   ```bash
   gh issue edit <num> --remove-label ready-for-agent --add-label agent-working
   gh issue comment <num> --body "Claimed by /ship."
   ```

2. **Reflect the PR (phase 6, right after opening):** the issue **stays**
   `agent-working` (an open PR is still agent-owned), and `/ship` comments the PR link
   so the tracker shows it's moved to *PR open, awaiting review/merge*. The PR body's
   `Closes #<num>` is the durable bidirectional link.

   ```bash
   gh issue comment <num> --body "PR opened, awaiting review/merge: <pr-url>"
   ```

3. **Merge → close:** the squash-merge's `Closes #<num>` closes the issue, which drops
   it (and its `agent-working` label) out of the open queue for good.

4. **Blocked (`/ship` can't finish):** if the issue is too ambiguous, on-prem-only, or
   CI can't be made green, move it `agent-working` → `ready-for-human` with a one-line
   reason. **Don't** requeue it as `ready-for-agent` (that loops it forever).

**Stale-claim recovery:** if a run dies after claiming but before opening a PR, the
issue sits `agent-working` with no PR. It won't be retried automatically — relabel it
`ready-for-agent` by hand to requeue.

## Priority and effort

Triage state (above) is orthogonal to two sizing axes applied alongside it. An issue carries at most one label from each axis.

**Priority** — urgency:

| Label      | Meaning                                              |
| ---------- | ---------------------------------------------------- |
| `critical` | Production-breaking, both targets, no workaround     |
| `high`     | Broken functionality or active exposure              |
| `med`      | Should do — value but not urgent                     |
| `low`      | Nice to have — no urgency                            |

**Effort** — t-shirt size:

| Label | Meaning                                       |
| ----- | --------------------------------------------- |
| `XS`  | Trivial — one spot, minutes                   |
| `S`   | Small — surgical, ~1 file                     |
| `M`   | Medium — multi-file or new path               |
| `L`   | Large — sweep / new module                    |
| `XL`  | Extra-large — new subsystem / design-gated    |

Implementation order is **derived** from these (`priority × effort × dependencies`), not stored as a label — a rank label rots the moment a higher issue ships.
