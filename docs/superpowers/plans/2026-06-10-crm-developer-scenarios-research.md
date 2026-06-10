# CRM Developer Scenarios Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a research report on real-world D365 developer workflows, a coverage matrix evaluating the `crm` CLI + skill against them, a hands-on trial log from crmworx, and a user-reviewed GitHub issue backlog of enhancements.

**Architecture:** Four sequential phases per the approved spec (`docs/superpowers/specs/2026-06-10-crm-developer-scenarios-research-design.md`): (1) scenario harvest via parallel research subagents into per-family candidate files, (2) merge into a scored catalogue then build a two-axis coverage matrix, (3) hands-on skill trial on the crmworx test org with fresh subagents that see only the shipped skill, (4) synthesis report + draft backlog → user review → file issues. No skill/CLI implementation in this effort.

**Tech Stack:** Claude Code Agent tool (research + trial subagents), web research tools (WebSearch, Exa MCP, WebFetch, firecrawl, MS Learn MCP), `crm` CLI v2.1.2+ against the `crmworx` profile, `gh` CLI for issue filing.

**Conventions for every task:**
- All artifacts live under `docs/research/`. Commit directly to `main` after each artifact (solo-repo convention), message prefix `docs(research): …` (no PSR version bump).
- Research subagents must return/write **structured rows**, schema: `| scenario | persona | frequency-signal | pain-signal | tooling-today | source URL |` where persona ∈ `pro-code | customizer | both`, frequency/pain ∈ `high | med | low` with a one-clause justification.
- Never guess CLI capability — interrogate `python -m crm describe <group>` (use `python -m crm`, not the bare binary, to dodge the destructive-verb PreToolUse gate on `--help`/`describe` inspection).

---

### Task 1: Prior art ingest

**Files:**
- Read: `docs/research/onprem-automation.md`, `docs/research/ai-agent-tooling-onprem.md`, `docs/research/crm-cli-agentic-enhancements.json`
- Create: `docs/research/harvest/2026-06/00-prior-art.md`

- [ ] **Step 1: Read the three existing research artifacts** and extract every concrete day-to-day developer scenario they mention or imply (including enhancement ideas that imply a scenario).

- [ ] **Step 2: Write `docs/research/harvest/2026-06/00-prior-art.md`** using the standard schema table, one row per extracted scenario. Source column = the prior-art filename. Add a one-paragraph header: what each prior file covered and its date, so the report can cite it.

- [ ] **Step 3: Verify** — file exists, table parses (every row has 6 columns), at least 5 rows expected from three files (if fewer, note why in the header rather than padding).

- [ ] **Step 4: Commit**

```bash
git add docs/research/harvest/2026-06/00-prior-art.md
git commit -m "docs(research): ingest prior art into scenario harvest"
```

---

### Task 2: Scenario harvest — five parallel research subagents

**Files:**
- Create: `docs/research/harvest/2026-06/01-mslearn.md`
- Create: `docs/research/harvest/2026-06/02-community.md`
- Create: `docs/research/harvest/2026-06/03-xrmtoolbox.md`
- Create: `docs/research/harvest/2026-06/04-pac-cli.md`
- Create: `docs/research/harvest/2026-06/05-ai-assisted.md`

Dispatch all five subagents **in one message** (independent work). Each gets the same output contract plus its family-specific prompt. Shared contract to embed in every prompt:

> You are researching real day-to-day work of Dynamics 365 CE developers (two personas: pro-code dev — plugins, JS web resources, ribbon, FetchXML/OData, integrations; customizer/functional consultant — tables, columns, forms, views, BPFs, security roles, model-driven apps). Find CONCRETE recurring tasks/scenarios, not product features. For each scenario write one table row: `| scenario (imperative, ≤15 words) | persona (pro-code/customizer/both) | frequency-signal (high/med/low + one clause why) | pain-signal (high/med/low + one clause why) | tooling-today (how people do it now) | source URL |`. 10–20 rows. Note per row if it is on-prem-only, online-only, or both. Use ToolSearch to load web tools you need (WebSearch, WebFetch, mcp__claude_ai_Exa__web_search_exa, mcp__claude_ai_Microsoft_Learn__microsoft_docs_search). Return ONLY the markdown table plus a short "method" note listing queries used. Do not editorialize about the crm CLI — you are cataloguing the world, not evaluating the tool.

- [ ] **Step 1: Dispatch agent 1 — MS Learn / official docs.** Family prompt: "Survey Microsoft Learn for D365 CE / Dataverse customization and pro-dev guidance: 'customize model-driven apps', 'Dataverse developer guide', 'plug-in development', 'web resources', 'FetchXML', 'solution concepts / ALM basics', 'business process flows'. Each documented how-to or tutorial topic = evidence of a sanctioned recurring task. Prefer microsoft_docs_search over web search."

- [ ] **Step 2: Dispatch agent 2 — community pain points.** Family prompt: "Mine practitioner pain: Stack Overflow tags `dynamics-365`, `dynamics-crm`, `dataverse` (top-voted questions), Reddit r/Dynamics365 + r/PowerPlatform recurring threads, Dynamics Community forum hot topics, practitioner blogs. High vote/comment counts = frequency/pain signals. Capture the underlying TASK, not the specific bug."

- [ ] **Step 3: Dispatch agent 3 — XrmToolBox catalogue.** Family prompt: "Enumerate the most-installed XrmToolBox plugins (xrmtoolbox.com plugin store, sort by downloads if possible; otherwise community 'essential plugins' lists). Each popular plugin exists because a recurring task is painful in the native UI — derive the task. Examples of the genre: FetchXML Builder, Bulk Data Updater, Attribute Manager, View Designer, Ribbon Workbench. Note which plugins work on-prem (XrmToolBox connects via SDK, most do)."

- [ ] **Step 4: Dispatch agent 4 — pac CLI surface.** Family prompt: "Inventory the Microsoft Power Platform CLI (`pac`) command groups and commands from official docs (microsoft_docs_search 'pac cli reference'). Each command = a task Microsoft put at the shell. For EVERY row, record in tooling-today whether the pac command works against on-prem D365 (most are online-only — verify per command group from docs, do not assume). This feeds a build-vs-adopt decision later, so dual-target accuracy matters more than volume."

- [ ] **Step 5: Dispatch agent 5 — AI-assisted D365 dev.** Family prompt: "Find posts/articles/talks (2024–2026) on using Copilot, LLMs, or agents for Dynamics 365 / Power Platform development work — what workflows do people automate or wish they could (code gen for plugins, FetchXML from natural language, solution review, data migration scripting, Copilot in maker portal). Each attempted/wished automation = a scenario row."

- [ ] **Step 6: Write each agent's returned table** to its harvest file listed above, verbatim plus the method note. (Agents return text; the main session writes files — keeps path control in one place.)

- [ ] **Step 7: Verify** — five files exist, each ≥10 rows, every row has a real URL (spot-check 2 per file resolve via WebFetch; replace dead ones with the agent's method-note queries re-run if needed).

- [ ] **Step 8: Commit**

```bash
git add docs/research/harvest/2026-06/
git commit -m "docs(research): harvest scenario candidates from five source families"
```

---

### Task 3: Merge into the scenario catalogue

**Files:**
- Read: all six files in `docs/research/harvest/2026-06/`
- Create: `docs/research/2026-06-crm-dev-scenario-catalogue.md`

- [ ] **Step 1: Merge and dedup.** Read all harvest rows. Merge near-duplicates (same underlying task) keeping ALL source URLs on the merged row. Scenarios with ≥2 independent source families confirming = eligible for `high` frequency; single-source rows cap at `med` (spec's research-noise mitigation).

- [ ] **Step 2: Write the catalogue** grouped under seven domain headings: schema/metadata, forms/views/apps, plugins/automation, solutions/ALM, data ops, security, troubleshooting. Final row schema: `| id | scenario | persona | freq | pain | target (on-prem/online/both) | tooling-today | sources |` with ids like `SCN-001`. Header section: scoring rules, source-family list, date.

- [ ] **Step 3: Verify against spec success criteria** — 30 ≤ count ≤ 60; every row ≥1 source; every domain heading non-empty (if a domain is empty, say so explicitly rather than inventing rows).

- [ ] **Step 4: Commit**

```bash
git add docs/research/2026-06-crm-dev-scenario-catalogue.md
git commit -m "docs(research): scenario catalogue (Phase 1 complete)"
```

---

### Task 4: Coverage matrix

**Files:**
- Read: `docs/research/2026-06-crm-dev-scenario-catalogue.md`, `crm/skills/SKILL.md`, all `crm/skills/reference/*.md`
- Create: `docs/research/2026-06-coverage-matrix.md`

- [ ] **Step 1: Capture the CLI ground truth.** Run and save output to a scratch buffer (not committed):

```bash
python -m crm describe > /tmp/crm-describe-full.txt
python -m crm --version
```

- [ ] **Step 2: Assess the CLI axis** for every catalogue scenario: `yes` (a command path exists — name it), `partial` (possible but multi-step/awkward — name the steps), `no`. Source of truth = the describe output + command code under `crm/commands/` when describe is ambiguous. Never infer from the skill text.

- [ ] **Step 3: Assess the skill axis** for every scenario: `taught` (router or a reference file covers the workflow — cite file+section), `absent`, `wrong/stale` (text contradicts current CLI). Read all 9 reference files once, then map.

- [ ] **Step 4: Fill the existing-tool column** for every `cli-axis=no|partial` row: which external tool covers it (pac / XrmToolBox plugin / Plugin Registration Tool / SolutionPackager / Configuration Migration / none) and whether that tool supports **both** on-prem and online (from Task 2 agent-4 data; verify uncertain claims against MS docs).

- [ ] **Step 5: Classify every scenario** into exactly one class: `covered` / `skill-gap` / `cli-gap` / `out-of-scope-challenge` (anything touching the XML-surgery family: BPF clientdata, solution clone/GUID regen, plugin-step clone, or new members of the same shape).

- [ ] **Step 6: Write `docs/research/2026-06-coverage-matrix.md`**: one table, schema `| id | scenario | cli-axis (+evidence) | skill-axis (+evidence) | existing-tool (dual-target?) | class |`, plus a tally section (count per class) and a shortlist of trial candidates (next task's input).

- [ ] **Step 7: Verify** — row count equals catalogue count; every `cli-gap` row has the existing-tool column filled; every classification has evidence cited.

- [ ] **Step 8: Commit**

```bash
git add docs/research/2026-06-coverage-matrix.md
git commit -m "docs(research): coverage matrix (Phase 2 complete)"
```

---

### Task 5: Trial selection and environment readiness

**Files:**
- Read: `docs/research/2026-06-coverage-matrix.md`
- Create: `docs/research/2026-06-skill-trial-plan.md`

- [ ] **Step 1: Select 6–8 trial scenarios** from the matrix honoring the spec constraints: majority from `covered`/`skill-gap`; ≥1 exercising on-prem quirks (v9.1 ceiling, no `*Multiple` — e.g. a bulk-data scenario that would tempt an agent toward `CreateMultiple`); ≥1 full customization workflow (new table → columns → form/view tweak → add to solution → export). Prefer scenarios whose cleanup is tractable (created components deletable).

- [ ] **Step 2: Write the trial plan file.** Per scenario a card:

```markdown
## TRIAL-1 — <scenario id + title>
**User story:** As a <persona>, I want <task> so that <outcome>.
**Task given to agent (verbatim):** "<the exact prompt text>"
**Preconditions:** solution `agtrial<N>` exists; <any seed records>
**Expected competent path:** <command sequence a skilled operator would use>
**Cleanup:** <exact delete commands>
**Gated verbs expected:** <list or "none"> (user presence needed if any)
```

- [ ] **Step 3: Verify environment.**

```bash
crm --json --profile crmworx connection whoami
```

Expected: `ok: true`, `@odata.context` host `internalcrm.moce.local`, OrganizationId starting `b948cd5f`. Also confirm the installed skill copy is current:

```bash
python -m crm describe skill        # exact flags for the install verb — never guess
crm skill install                   # default target installs to ~/.claude/skills/crm/ (add the target flag describe shows if needed)
diff -rq crm/skills/ ~/.claude/skills/crm/ && echo SKILL-IN-SYNC
```

Expected: `SKILL-IN-SYNC` (trial agents must read exactly the shipped content).

- [ ] **Step 4: Create one throwaway unmanaged solution per trial** (names `agtrial1`…`agtrialN`) using the CLI itself, `--json`, recording each command + envelope in the trial plan file's preconditions.

- [ ] **Step 5: Commit**

```bash
git add docs/research/2026-06-skill-trial-plan.md
git commit -m "docs(research): trial plan and environment readiness"
```

---

### Task 6: Run the trials

**Files:**
- Modify: `docs/research/2026-06-skill-trial-plan.md` (append per-trial results)
- Create: `docs/research/2026-06-skill-trial-log.md`

Run trials **sequentially** (shared org; avoids cross-contamination). Per trial:

- [ ] **Step 1: Dispatch a fresh subagent** with this prompt template (fill per card):

> You are a Claude Code agent helping a Dynamics 365 developer. A skill is installed at `~/.claude/skills/crm/` — read `~/.claude/skills/crm/SKILL.md` first and follow it (it routes to reference files; read the ones it points you to for this task). You have NO access to any source repository — only the installed skill and the `crm` binary on PATH. Use profile `crmworx`. Work inside solution `agtrial<N>` where the skill/CLI supports a solution flag. Your task: "<verbatim task from the trial card>". Work until done or genuinely blocked; if blocked, state precisely what blocked you. Report every command you ran and its envelope outcome.

Do NOT include hints, the expected path, or extra context beyond the card's task text.

- [ ] **Step 2: Review the transcript** against the card's expected path. Log every stumble into `docs/research/2026-06-skill-trial-log.md`, schema: `| trial | step | what-went-wrong | root-cause (skill-text/cli-behavior/cli-bug/agent-error) | evidence (command + envelope) |`. A trial with zero stumbles gets one `clean pass` row.

- [ ] **Step 3: Run the card's cleanup commands**, verify deletions via a `--json` read-back (remember dry-run/exists-check caveats — verify with real GETs, not dry-runs).

- [ ] **Step 4: After all trials — write the trial log summary header**: trials run, stumbles by root-cause class, clean-pass rate.

- [ ] **Step 5: Commit**

```bash
git add docs/research/2026-06-skill-trial-plan.md docs/research/2026-06-skill-trial-log.md
git commit -m "docs(research): skill trial log (Phase 3 complete)"
```

---

### Task 7: CLI bug repros

**Files:**
- Modify: `docs/research/2026-06-skill-trial-log.md` (append repro section)

- [ ] **Step 1: For every `cli-bug` row** in the stumble log, reproduce minimally in the main session: smallest command sequence + full `--json` envelope, against crmworx. If it doesn't reproduce, reclassify the row (likely `agent-error`) and note why.

- [ ] **Step 2: Append a "Bug repros" section** to the trial log: one block per bug with repro commands, observed envelope, expected behavior.

- [ ] **Step 3: Commit**

```bash
git add docs/research/2026-06-skill-trial-log.md
git commit -m "docs(research): minimal repros for trial-discovered CLI bugs"
```

---

### Task 8: Synthesis report

**Files:**
- Create: `docs/research/2026-06-crm-dev-scenarios-report.md`
- Read: all prior artifacts

- [ ] **Step 1: Write the report** with exactly the six spec sections:
  1. Exec summary — top findings + top 5 recommendations.
  2. Personas + day-in-the-life workflows (cite harvest sources).
  3. Scenario catalogue (link the catalogue file; inline the per-domain counts).
  4. Coverage matrix (inline the matrix table from Task 4 — the spec wants it as a report section; keep the standalone file and state it is the source).
  5. Trial findings — stumble log summary + root-cause breakdown + bug repro links.
  6. Recommendations in four buckets: **skill enhancements** (respect "only what describe/--help cannot say"; may cite published llm-text docs URLs and propose new `docs/how-to/` pages), **CLI enhancements** (each explicitly marked `build` or `adopt/wrap` with the dual-target evidence), **out-of-scope reversal proposals** (evidence + validation plan + which XML schema ships — flagged as reversals), **CLI bugs**.

- [ ] **Step 2: Self-check the report** against spec success criteria: catalogue ≥30 sourced scenarios ✓, all classified ✓, 6–8 trials with log ✓, six sections present ✓. Fix gaps before commit.

- [ ] **Step 3: Commit**

```bash
git add docs/research/2026-06-crm-dev-scenarios-report.md
git commit -m "docs(research): CRM developer scenarios report (Phase 4 report)"
```

---

### Task 9: Backlog draft, user review gate, filing

**Files:**
- Create: `docs/research/2026-06-backlog-draft.md`
- Create (transient, untracked): `/tmp/issue-body-<n>.md` per issue

- [ ] **Step 1: Draft the backlog file.** One entry per recommendation, format:

```markdown
## P1 | skill | <title>
**Labels:** needs-triage
**Evidence:** SCN-0xx (+ source links), trial stumble ref if any
**Proposed change:** <concrete change>
**Acceptance criteria:** <verifiable bullets>
```

Buckets map to a `skill | cli-build | cli-adopt | oos-reversal | bug` tag in the title line. Priorities P1–P3 justified by freq × pain from the catalogue.

- [ ] **Step 2: Commit the draft and STOP for user review.**

```bash
git add docs/research/2026-06-backlog-draft.md
git commit -m "docs(research): draft enhancement backlog for review"
```

Present the draft list (titles + priorities) to the user. **Do not file anything until the user approves the list.** Apply any edits they request to the draft file first.

- [ ] **Step 3: After approval — file the issues.** Per issue: write the body with the Write tool to `/tmp/issue-body-<n>.md` (avoids the PreToolUse gate matching gated-verb phrases inside heredocs), then:

```bash
gh issue create --repo Gharib89/crm --title "<title>" --label needs-triage --body-file /tmp/issue-body-<n>.md
```

- [ ] **Step 4: Back-fill issue numbers** into `docs/research/2026-06-backlog-draft.md` next to each entry.

- [ ] **Step 5: Commit**

```bash
git add docs/research/2026-06-backlog-draft.md
git commit -m "docs(research): backlog filed — issue numbers recorded"
```

---

### Task 10: Cleanup and final verification

**Files:**
- Modify: `docs/research/2026-06-skill-trial-plan.md` (cleanup confirmation note)

- [ ] **Step 1: Sweep crmworx for leftovers** — list the `agtrial*` solutions and any trial-created components/records; delete remnants (destructive verbs — user nearby for the gate), verify by read-back.

- [ ] **Step 2: Run the spec success-criteria checklist** one final time against the committed artifacts; record pass/fail per criterion at the bottom of the report.

- [ ] **Step 3: Final commit + push**

```bash
git add -A docs/research/
git commit -m "docs(research): cleanup confirmation and final criteria check"
git push
```

(Earlier tasks commit locally; push at minimum here and after Task 8 — docs CI runs mkdocs `--strict` on `docs/**`, so a failing link in research files would surface then. Research files are not in nav; only genuinely broken intra-repo links would fail.)
