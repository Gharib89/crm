---
status: accepted
---

# Client-side output shaping: `--fields` (and future `--jq`) at the emit seam

## Context

Coding agents are the primary consumer of `crm --json`, and they run under hard
context budgets (~25k tokens per call). A live audit (agent-cloud, v9.2,
2026-07-08; PRD #734) found that ADR 0008 curation already stripped all per-row
bloat — the remaining fat is **row count**: `solution list` is ~930 rows × 6
curated keys ≈ 260 KB, `metadata entities` ~870 rows × 7 keys ≈ 669 KB,
`solution components Default` ~27k rows ≈ 6.5 MB. Per-row trimming cannot rescue
these; an agent needs to *reshape* the payload — project it to a few columns,
count it, slice it — before it enters context. Its only prior options were
writing to a file and shelling out to external `jq`, or eating the tokens.

## Decision

Output shaping is a **client-side, post-curation, envelope-preserving**
transform applied at the CLI's single emit seam (`CLIContext.emit`), **after**
ADR 0008 curation (OData-key stripping, envelope normalization, paging → `meta`)
and **before** serialization/render. It transforms only what sits inside `data`;
the envelope contract (`ok` / `error` / `meta`, including `next_link` / `count` /
`warnings`; exit codes, ADR 0001) is never touched. Because it lives at the
highest existing seam, every command inherits it with zero per-command code and
no new seam is introduced. Shaping is purely client-side — it does not alter
requests, `$select`, or paging, and is identical for on-prem and cloud.

This ADR records the whole shaping decision. It is delivered in slices:

- **`--fields KEY[,KEY...]`** (this ADR's first slice, #735): project the curated
  `data` down to the named top-level keys.
  - **List of objects** → each row projected to the named keys in flag order; a
    key missing from a row is omitted from that row (not nulled).
  - **Single object** → the same projection on the record.
  - **Non-object payload** (a scalar, a formxml string, a list carrying no
    dicts) → passed through unchanged with a `meta.warnings` advisory; there is
    nothing to project.
  - A requested field that matched **zero** rows/records adds a `meta.warnings`
    entry naming it (the typo tripwire, on the existing warnings channel).
  - **Human mode** → the named fields select and order the rendered table columns
    (matched against header cells case-insensitively).
- **`--jq PROGRAM`** (follow-up, #736): run a jq program over the curated `data`;
  compiled before any backend call; `--fields` + `--jq` is a usage error (exit 2).
  Not part of this slice.

Shaping applies to **success** envelopes only. **Error envelopes bypass shaping
entirely** (`ok: false` emits exactly as today, so an error message is never
mangled or hidden). **Dry-run previews are shaped like any other data.**

## Considered options

- **Per-row trimming knobs (`--minimal`) / per-command projection**: rejected —
  the audit proved the residual cost is row count, not per-row width, so trimming
  cannot rescue the worst offenders, and per-command knobs force agents to learn a
  different flag per verb (the opposite of the global-seam goal).
- **Auto-truncation / output budget guard** (`meta.truncated` at N tokens):
  rejected — an opinionated mechanism that takes control from the agent; shaping
  keeps the agent deciding what to keep.
- **Shape before curation / per-command**: rejected — it would reintroduce the
  per-command drift ADR 0008 removed and expose the raw OData shape to the
  projection.

## Consequences

- **Purely additive**: with no shaping flag, every verb's output is
  byte-identical to before. No default payload changes.
- Agents get one shaping idiom that works on every command, shaped and unshaped
  output alike keep the one ADR 0008 extraction rule (`ok`/`data`/`meta`).
- The contract term lives in `CONTEXT.md` (**Shaped payload**); the flags are
  documented in the CLI reference and the query how-to; the crm skill teaches the
  budget idiom at spine level (project/count a fat verb before loading it).
- `--jq` (#736) and its PyInstaller bundling (#737) extend this decision without
  revisiting it.
