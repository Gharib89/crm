# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |
| _(local — no role)_        | `gate-passed`        | PR label — `/merge-gate` verified it merge-confident (see `docs/agents/pr-merge-gate.md`) |
| _(local — no role)_        | `gate-failed`        | PR label — `/merge-gate` escalated it for a maintainer decision |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

`gate-passed` / `gate-failed` are not triage roles: they live on **PRs**, set only by the `merge-gate` skill after `/ship`'s pipeline ends. `/ship` claims an issue by assignee, not by label.

Edit the right-hand column to match whatever vocabulary you actually use.

## Dimension labels

Three dimensions beside the five triage roles. Triage stamps **at most one label per dimension**, at triage time, alongside the role label, so a maintainer glancing at the tracker reads off what kind of change an issue is, how big it is and how urgent it is without opening it.

Implementation order is **derived** from priority, size and blocking edges, and is never stored as a label: a rank label rots the moment a higher issue ships.

### Kind

What kind of change the issue asks for. Each label names the Conventional Commit type that grades the release, so the label read at triage is the type the PR title carries.

| Label | Type | Color | Description |
| --- | --- | --- | --- |
| `bug` | `fix` | `d73a4a` | Something isn't working |
| `enhancement` | `feat` | `a2eeef` | New feature or request |
| `documentation` | `docs` | `0075ca` | Improvements or additions to documentation |
| `refactor` | `refactor` | `5319e7` | Behavior-preserving restructure: no functional change |
| `chore` | `chore` | `cccccc` | Tooling, deps, or housekeeping: no behavior change |

`refactor` is the right kind for a prefactor that makes a later change easy ("make the change easy, then make the easy change") — its acceptance bar is that no shipped behavior changes. `chore` covers release plumbing, dependency bumps, and CI/tooling that ships no product behavior.

### Size

How much of the codebase the change moves: the effort a maintainer weighs before picking the issue up, not a time estimate.

| Label | Color | Description |
| --- | --- | --- |
| `XS` | `e4e4e7` | Trivial: one spot, minutes |
| `S` | `b4b4bb` | Small: surgical, ~1 file |
| `M` | `71717a` | Medium: multi-file or new path |
| `L` | `3f3f46` | Large: sweep / new module |
| `XL` | `18181b` | Extra-large: new subsystem / design-gated |

### Priority

How much it costs to leave the issue undone.

| Label | Color | Description |
| --- | --- | --- |
| `critical` | `b60205` | Production-breaking, both targets, no workaround |
| `high` | `d93f0b` | Broken functionality or active exposure |
| `med` | `fbca04` | Should do: value but not urgent |
| `low` | `0e8a16` | Nice to have: no urgency |
