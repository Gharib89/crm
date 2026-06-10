# CRM developer scenarios research — design

**Date:** 2026-06-10
**Status:** Draft (pending user review)
**Type:** Research effort (no implementation in this effort)

## Goal

Research how Dynamics 365 CE developers actually work day to day (customization
focus), evaluate the `crm` CLI + Claude Code combo against those real workflows,
and produce (a) a research report and (b) a prioritized GitHub issue backlog of
skill and CLI enhancements. Implementation happens later, issue by issue.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Deliverable | Research report + prioritized GitHub issue backlog (no implementation now) |
| Personas | Pro-code dev (plugins, JS/web resources, ribbon, queries, integrations) + customizer/functional consultant (tables, forms, views, BPFs, roles, apps) |
| Scenario sources | Community + docs research, competitive tool gap analysis, hands-on skill trial. (No user-interview source.) |
| Trial org | `crmworx` — local on-prem test environment, full e2e mutations allowed; trial doubles as CLI bug hunt |
| Out-of-scope family (XML surgery: #37, #166, #169) | May be challenged with evidence. XML-surgery features are acceptable **if** built with validation and the relevant XML schema included |
| Build vs adopt | For every CLI gap, first check whether an existing tool (pac CLI, XrmToolBox, Plugin Registration Tool, SolutionPackager, Configuration Migration, …) covers it on **both** on-prem and online. If yes → recommend adopt/wrap/document, not rebuild. Build only when no dual-target tool exists or shell/agent ergonomics demand native support. Verify dual-target claims per command (pac is largely online-only — check, don't assume) |
| Skill ↔ docs | The published docs site exposes llm-text output (mkdocs `llmstxt` plugin → `llms-full.txt`) built from `docs/how-to/*`. Skill enhancements may reference these published URLs for deep how-tos (public URLs are allowed in the shipped skill; repo paths are not), and scenario gaps may be closed by adding new how-to pages |
| Artifact locations | Spec: `docs/superpowers/specs/` (this file). Plan: `docs/superpowers/plans/`. Research outputs: `docs/research/` (existing, unpublished). Prior art already in `docs/research/` (`crm-cli-agentic-enhancements.*`, `ai-agent-tooling-onprem.md`, `onprem-automation.md`) is ingested as Phase 1 input |

## Phase 1 — Scenario harvest

Parallel research subagents, one per source family:

1. **MS Learn / official docs** — customization and pro-dev guides, ALM docs,
   Dataverse Web API how-tos (MS Learn MCP tools).
2. **Community pain points** — Dynamics Community forums, Reddit
   (r/Dynamics365, r/PowerPlatform), Stack Overflow `dynamics-365`/`dataverse`
   top questions (Exa, web-search, fetch).
3. **XrmToolBox catalogue** — top plugins by install count; each popular plugin
   is a proven recurring task the native UI/SDK serves poorly (firecrawl on the
   plugin store).
4. **pac CLI surface** — full command-group inventory; the daily jobs Microsoft
   itself puts at the shell. Feeds the build-vs-adopt column directly.
5. **AI-assisted D365 dev** — posts on Copilot/agents in Power Platform dev
   work; workflows people already try to automate with LLMs.

Plus: ingest prior art from `docs/research/` before external research.

Each agent returns structured candidates:
`{scenario, persona, frequency-signal, pain-signal, tooling-today, source-url}`.

Dedup + merge into a **scenario catalogue**: target 30–60 scenarios, grouped by
domain (schema/metadata, forms/views/apps, plugins/automation, solutions/ALM,
data ops, security, troubleshooting), scored frequency × pain, with on-prem vs
cloud-only applicability noted per scenario.

**Artifact:** `docs/research/2026-06-crm-dev-scenario-catalogue.md`

## Phase 2 — Coverage matrix

Each catalogue scenario assessed on two axes against current state:

- **CLI axis** — can `crm` do it today? Source of truth: `crm describe`,
  command code, docs. Never guessed.
- **Skill axis** — does `crm/skills/` (router + 9 reference files) correctly
  guide an agent through it?

Plus an **existing-tool coverage** column (which external tool covers it, and
whether it supports both targets) to make build-vs-adopt auditable.

Classification per scenario:

| Class | Meaning |
|---|---|
| `covered` | CLI can do it and skill teaches it |
| `skill-gap` | CLI can do it; skill doesn't teach it (or teaches it wrong) |
| `cli-gap` | No CLI capability; build-vs-adopt rule applies |
| `out-of-scope-challenge` | Hits the XML-surgery rejection family; needs evidence + validation/schema plan |

The matrix is the report's backbone and selects Phase 3 trial scenarios.

**Artifact:** matrix section inside the report (Phase 4).

## Phase 3 — Hands-on skill trial (crmworx)

**Selection.** 6–8 scenarios from the matrix: mostly `covered`/`skill-gap`
(tests skill quality and flushes CLI bugs), at least one exercising on-prem
quirks (v9.1 API ceiling, no `*Multiple`), at least one full customization
workflow (e.g. new table → form/view changes → solution export).

**Mechanics.** Per scenario, a fresh subagent gets ONLY the shipped skill
content (as an end user would have it — no repo access) plus the task in
user-story form. It works against `crmworx` with the real CLI in `--json`
mode. Each transcript is reviewed and stumbles logged:
`{scenario, step, what-went-wrong, root-cause}` where root-cause ∈
`skill-text | cli-behavior | cli-bug | agent-error`.

**Safety/logistics.** Dedicated throwaway unmanaged solution per scenario;
cleanup pass at the end. `crmworx` is a local test environment — mutations
fine. Known friction: the destructive-op PreToolUse hook gates some verbs;
scenarios needing gated verbs surface permission prompts (user nearby, or
scope around them).

**Output.** Stumble log + minimal repro per CLI bug found.

**Artifact:** `docs/research/2026-06-skill-trial-log.md` (+ per-scenario
transcript summaries).

## Phase 4 — Synthesis

**Report** (`docs/research/2026-06-crm-dev-scenarios-report.md`):

1. Exec summary — top findings, top 5 recommendations.
2. Personas + day-in-the-life workflows (research-grounded).
3. Scenario catalogue (with sources).
4. Coverage matrix (CLI axis / skill axis / existing-tool column / class).
5. Trial findings — stumble log, breakdown by root cause.
6. Recommendations in four buckets:
   - **Skill enhancements** — concrete edits to `crm/skills/`, respecting the
     "state only what `describe`/`--help` cannot" rule; may lean on published
     llm-text docs URLs and propose new `docs/how-to/` pages.
   - **CLI enhancements** — each marked **build** or **adopt/wrap** per the
     dual-target rule.
   - **Out-of-scope reversal proposals** — evidence + proposed validation +
     XML-schema plan, flagged explicitly as reversals.
   - **CLI bugs** — minimal repros from the trial.

**Backlog.** Recommendations become GitHub issues on `Gharib89/crm`, labeled
`needs-triage`, priority noted in the body, scenario evidence linked. **The
draft issue list is reviewed by the user before anything is filed** — nothing
posted silently.

## Success criteria

- Scenario catalogue ≥ 30 scenarios, each with at least one source.
- Every catalogue scenario classified in the matrix.
- 6–8 trial scenarios run e2e with transcripts and a stumble log.
- Report complete with all six sections.
- Backlog drafted → user review → filed.

## Risks

- **Gated destructive verbs during trials** — permission prompts mid-trial;
  mitigate by scheduling runs with the user available or scoping scenarios.
- **pac CLI on-prem assumptions** — verify per command; most pac commands are
  online-only.
- **Research noise** — community sources are anecdotal; mitigate by requiring
  multiple independent signals before scoring a scenario high-frequency.
- **Scope creep** — this effort produces research + issues only; no skill or
  CLI implementation here.

## Out of scope (this effort)

- Implementing any skill or CLI change.
- Re-architecting the skill bundle layout (only content recommendations).
- Scenarios for personas not selected (ALM/DevOps engineer, support/ops) —
  noted in passing if sources surface them, not researched deeply.
