# CRMWorx Guide — Plan 3: How-tos + Skill/CLI Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the per-group how-to pages from the live run, document the skill↔CLI sync workflow, reconcile the existing skill drift, and verify the whole docs site builds clean.

**Architecture:** Pure documentation + a skill resync. How-to pages distil real commands captured in `docs/guides/crmworx-walkthrough.md` (Plan 2) into reusable recipes. The skill source of truth `crm/skills/SKILL.md` is reinstalled to the active Claude location, eliminating the drift where the active copy lacks the destructive-ops section.

**Tech Stack:** MkDocs, the `crm skill` command, diff/patch.

**Prerequisites:** Plans 1 and 2 complete (scaffold + transcribed walkthrough exist).

---

## File structure

- Modify: `docs/how-to/{connection,entity,query,metadata,solution,data,action}.md`
- Modify: `docs/contributing/skill-and-cli.md`
- Modify (resync target): `~/.claude/skills/crm/SKILL.md`
- Possibly modify: `crm/skills/SKILL.md` (only if the run exposed undocumented behavior)

### Task 1: Fill the seven how-to pages from the walkthrough

**Files:**
- Modify: `docs/how-to/connection.md`, `entity.md`, `query.md`, `metadata.md`, `solution.md`, `data.md`, `action.md`

- [ ] **Step 1: Write each how-to from real captured commands**

For each group, replace the stub with 2–4 task recipes drawn from the CRMWorx run.
Each recipe is a `## <task>` heading, the exact command, and a one-line note on output.
Pull the commands verbatim from `docs/guides/crmworx-walkthrough.md` so they match
reality. Example for `docs/how-to/metadata.md`:

```markdown
# How-to: metadata

Recipes for schema work, taken from the CRMWorx build. See the
[CLI reference](../reference/cli.md) for every flag.

## Create a global option set (idempotent)

```bash
crm --json metadata create-optionset --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical --if-exists skip
```
`--if-exists skip` makes re-runs a no-op.

## Add a picklist column bound to that option set

```bash
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Priority --display "Priority" --optionset-name cwx_priority --if-exists skip
```

## Create a 1:N relationship (adds a lookup on the N side)

```bash
crm --json metadata create-one-to-many --schema-name cwx_sla_cwx_ticket \
  --referenced-entity cwx_sla --referencing-entity cwx_ticket \
  --lookup-schema cwx_SLA --lookup-display "SLA Policy" --if-exists skip
```
```

Do the equivalent for the other six groups (connection: whoami/connect/profiles;
entity: create/update/lookup-bind; query: odata/fetchxml; solution: components/export/
publish-all; data: export; action: function). `session` has no dedicated page — fold
`session info`/`history` into the connection page or the walkthrough coverage table.

- [ ] **Step 2: Build strict**

Run: `mkdocs build --strict`
Expected: no warnings.

- [ ] **Step 3: Commit**

```bash
git add docs/how-to/
git commit -m "docs: fill how-to recipes from the CRMWorx run"
```

### Task 2: Reconcile the agent-skill drift

**Files:**
- Read: `crm/skills/SKILL.md` (source of truth), `.claude/skills/crm/SKILL.md` (active, drifted)
- Modify: `~/.claude/skills/crm/SKILL.md` via `crm skill install --force`

- [ ] **Step 1: Confirm the drift**

```bash
diff crm/skills/SKILL.md .claude/skills/crm/SKILL.md
```
Expected: the active copy is missing the "Destructive operations — `--yes` confirm
contract" section (and possibly run-exposed updates). Confirm the source-of-truth copy
is the more complete one.

- [ ] **Step 2: If the run exposed undocumented behavior, update the source of truth first**

Only if Plan 2 surfaced behavior not in `crm/skills/SKILL.md` (e.g. a corrected
`@odata.bind` property name pattern, or a metadata create quirk), add a short note to
`crm/skills/SKILL.md`. Otherwise skip this step.

- [ ] **Step 3: Reinstall the skill to the active Claude location**

```bash
crm --json skill install --target claude --force
diff crm/skills/SKILL.md .claude/skills/crm/SKILL.md && echo "IN SYNC"
```
Expected: `IN SYNC` (the diff is now empty).

- [ ] **Step 4: Commit any source-of-truth change**

```bash
git add crm/skills/SKILL.md
git commit -m "docs(skill): sync SKILL.md with CRMWorx run findings" || echo "no source change"
```
(The `~/.claude/...` copy lives outside the repo; only the source-of-truth commit matters.)

### Task 3: Write the skill ↔ CLI sync + bug-loop contributing page

**Files:**
- Modify: `docs/contributing/skill-and-cli.md`

- [ ] **Step 1: Replace the stub with the real workflow**

```markdown
# Keeping the agent skill in sync with the CLI

## Source of truth

`crm/skills/SKILL.md` is the canonical agent skill. The package ships it
(`package_data` in `setup.py`) and `crm skill install` copies it into an agent's
skill directory:

| Target | Destination |
| --- | --- |
| `claude` | `~/.claude/skills/crm/SKILL.md` |
| `copilot` | `~/.copilot/skills/crm/SKILL.md` |
| `cursor` | `~/.cursor/rules/crm/SKILL.md` |

```bash
crm skill install --target claude --force
```

## When the CLI changes

After adding or changing a command, update `crm/skills/SKILL.md`, then reinstall with
`--force`. To detect drift:

```bash
diff crm/skills/SKILL.md ~/.claude/skills/crm/SKILL.md
```

## The bug loop used to build CRMWorx

While building the walkthrough, defects were handled with a hybrid policy:

- **Trivial / single-function:** write a failing test in `crm/tests/`, fix in `crm/`,
  re-run, continue — the fix lands in the same session.
- **Larger:** file a triaged GitHub issue with a minimal repro, work around, continue.

Issues filed during the run are tracked in
[GitHub Issues](https://github.com/Gharib89/crm/issues).
```

- [ ] **Step 2: Build strict + commit**

```bash
mkdocs build --strict
git add docs/contributing/skill-and-cli.md
git commit -m "docs: document skill/CLI sync workflow and bug loop"
```

### Task 4: Final whole-site verification

**Files:**
- None (verification only)

- [ ] **Step 1: Strict build, zero warnings**

Run: `mkdocs build --strict 2>&1 | tee /tmp/mkdocs.log; grep -c WARNING /tmp/mkdocs.log`
Expected: `0` warnings; final line `Documentation built in ...`.

- [ ] **Step 2: Confirm every nav page resolves**

Run: `for f in index getting-started/install getting-started/configure guides/crmworx-walkthrough how-to/connection how-to/entity how-to/query how-to/metadata how-to/solution how-to/data how-to/action reference/cli contributing/skill-and-cli; do test -e "site/$f/index.html" -o -e "site/$f.html" && echo "ok $f" || echo "MISSING $f"; done`
Expected: every line starts with `ok`.

- [ ] **Step 3: Confirm coverage table lists all groups + no stub markers remain**

Run: `grep -rl "Status:.*scaffold" docs/ || echo "no stubs remain"`
Expected: `no stubs remain`.

- [ ] **Step 4: Final commit (if any verification touched files)**

```bash
git status --porcelain
```
If clean, nothing to commit. Otherwise stage the fix and commit with a descriptive message.

## Self-review notes

- Plan 3 produces no server mutations except `crm skill install` (local file copy).
- The `~/.claude/skills/crm/SKILL.md` resync is intentionally outside git; only the
  in-repo source of truth is versioned.
- Success criteria from the spec satisfied here: strict build clean, all groups covered,
  skill resynced + drift reconciled, no remaining stubs.
