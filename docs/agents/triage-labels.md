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

`agent-working` is not a triage role — it is a runtime claim state that `/ship` sets while it works an issue (see `CLAUDE.md` → "Triage labels"). It is listed here only so the label is recognized, not stripped.

Edit the right-hand column to match whatever vocabulary you actually use.

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
