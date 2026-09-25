## Dimension labels

<!-- setup-skills: written verbatim at the end of docs/agents/triage-labels.md, below the role table and the prose lines that explain it, this comment removed. Create each label on the host with the Color and Description columns below. `bug`, `enhancement` and `documentation` are GitHub's own defaults, color and description included, so a fresh repo already carries those three and only `refactor` and `chore` are created. The size greys run light to dark and the priority colors run red through amber to green, so each dimension reads as a ramp in the tracker's label column rather than as unrelated colors. -->

Three dimensions beside the five triage roles. Triage stamps **at most one label per dimension**, at triage time, alongside the role label, so a maintainer glancing at the tracker reads off what kind of change an issue is, how big it is and how urgent it is without opening it.

Implementation order is **derived** from priority, size and blocking edges, and is never stored as a label: a rank label rots the moment a higher issue ships.

### Kind

What kind of change the issue asks for. Each label names the Conventional Commit type that grades the release, so the label read at triage is the type the PR title carries.

| Label | Type | Color | Description |
| --- | --- | --- | --- |
| `bug` | `fix` | `d73a4a` | Something isn't working |
| `enhancement` | `feat` | `a2eeef` | New feature or request |
| `documentation` | `docs` | `0075ca` | Improvements or additions to documentation |
| `refactor` | `refactor` | `8250df` | Behavior-preserving restructure, no functional change |
| `chore` | `chore` | `fef2c0` | Tooling, deps or housekeeping, no behavior change |

### Size

How much of the codebase the change moves: the effort a maintainer weighs before picking the issue up, not a time estimate.

| Label | Color | Description |
| --- | --- | --- |
| `XS` | `e4e4e7` | Trivial: one spot, minutes |
| `S` | `b4b4bb` | Small: surgical, about one file |
| `M` | `71717a` | Medium: multi-file or new path |
| `L` | `3f3f46` | Large: sweep or new module |
| `XL` | `18181b` | Extra-large: new subsystem, design-gated |

### Priority

How much it costs to leave the issue undone.

| Label | Color | Description |
| --- | --- | --- |
| `critical` | `b60205` | Production-breaking, no workaround |
| `high` | `d93f0b` | Broken functionality or active exposure |
| `med` | `fbca04` | Should do: value but not urgent |
| `low` | `0e8a16` | Nice to have: no urgency |
