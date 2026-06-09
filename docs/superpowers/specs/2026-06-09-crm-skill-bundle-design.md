# Design — crm agent-skill restructure (router + reference files)

Date: 2026-06-09
Status: approved-pending-implementation

## Problem

`crm/skills/SKILL.md` is a single 1022-line file. It is the canonical agent skill;
the wheel/PyInstaller binary ships it and `crm skill install` copies it into an
agent's skill dir (`~/.claude/skills/crm/SKILL.md`, etc.). Three pains, all
observed:

1. **Context bloat** — the entire 1022-line file loads into context on every
   trigger, even when the task touches one command group.
2. **Discovery** — the agent struggles to find the right command/workflow inside
   the wall of text.
3. **Drift upkeep** — the file restates every command's flags, choices, and
   defaults, which duplicate the live CLI and rot with every CLI change.

A multi-skill *plugin* was considered and rejected: it is a Claude-Code-only
format (kills the copilot/cursor install path), fragments one coherent tool's
trigger across N competing skill descriptions, and adds a plugin+marketplace
distribution layer *on top of* the existing wheel/PyInstaller pipeline — more
sync surface, not less. The drift win is orthogonal to packaging.

## Solution — thin router skill + on-demand reference files

A "skill" is a directory. Restructure `crm/skills/` into a thin SKILL.md router
plus a `reference/` subtree loaded on demand. Forward-compatible with a future
Claude-only plugin wrapper (the same dir is the unit a plugin would bundle), so
the plugin question is deferred, not foreclosed.

The implementation **uses the `skill-creator:skill-creator` skill** to author the
restructure (progressive-disclosure conventions, packaging/validation, and
description-triggering evals).

### File layout

```
crm/skills/
  SKILL.md                  router (~180 lines, was 1022)
  reference/
    records.md              entity CRUD + relationships + query (odata/fetchxml/saved/user) + data import/export + action
    metadata.md             describe/entities/attributes/picklist/relationships/dependencies/export-spec/clone-entity/cache
    authoring.md            apply, scaffold, optionsets, view create (declarative desired-state)
    solutions.md            solution lifecycle + packager (extract/pack) + validate + components drift
    customizations.md       app, webresource, ribbon, form, sitemap
    automation.md           plugin assembly/steps + workflow lifecycle
    security.md             roles + assignment (BU-scoped)
    troubleshooting.md      error taxonomy + retry semantics + connection doctor + session/audit + on-prem-vs-cloud quirks
    feedback.md             how to report a bug / request a capability (self-contained: label + repro template)
```

9 reference files: 8 mapped to command groups + `feedback.md`. The exact split is
adjustable during implementation; skill-creator's conventions take precedence on
naming/structure (e.g. `reference/` vs `references/`).

### Self-containment constraint (load-bearing)

The skill ships to end users who have **only the skill**, not the repository. No
SKILL.md or reference file may link to a repo-only path (`docs/agents/*`,
`docs/adr/*`, `CONTEXT.md`, `docs/contributing/*`, etc.) or assume any repo file
is present. Anything the agent needs — label names, repro templates, error tables
— is **inlined** into the skill. The only external dependency the skill may assume
is the installed `crm` binary itself (for `crm describe` / `--help`) and, for the
feedback flow, the `gh` CLI.

### SKILL.md router contract — what stays vs what moves

**Stays in SKILL.md** (the content `crm <group> --help` / `crm describe` cannot
provide):

- Frontmatter — `name` + `description` (the trigger string) kept; this is what
  fires the skill and is always in context. Unchanged unless evals say otherwise.
- When-to-use, install, configure (auth/env vars — the one setup detail not in
  `--help`).
- **JSON / agent contract**: `--json` envelope shape, exit-code table, `--dry-run`
  (`meta.dry_run`), `--yes`, `meta.warnings` channel, REPL fail-fast for
  non-interactive callers.
- **Command-group map** — a table routing each domain to its `reference/*.md`.
- **Discovery rule** — for exact flags/choices/defaults, run `crm describe <group>`
  or `crm <group> --help`; never guess a flag.
- **Destructive-op `--yes` contract** — kept prominent in root for safety.
- **Feedback section** (see below).

**Moves to `reference/*.md`**: every per-command recipe, flag list, and example
block currently in SKILL.md §1–§13 and the per-group sections.

### The drift rule (the core fix)

`crm describe [group]` walks the **live Click command tree** and emits every
command, option, argument, Choice enum, default, and envvar — auto-generated from
code, so it **cannot drift**. Therefore:

> Skill files state only what `crm describe` / `--help` **cannot**: workflows,
> cross-command recipes, the JSON contract, and D365 gotchas (on-prem v9.1 API
> cap, `@odata.bind` navigation-property names, global-optionset `MetadataId`
> binding, dry-run stubbing all HTTP incl. GETs, etc.). **Flags, choices, and
> defaults are never restated.**

Maintenance test, to be documented in the contributing guide: *if a line could be
regenerated by `crm describe`, delete it.* This is what removes the upkeep pain.

### Install mechanism — `crm/commands/skill.py`

`crm skill install` currently copies a single file. Change to copy the tree:

- `_bundled_skill_path` → return the bundled `skills/` **directory** (rename to
  reflect a dir; keep a helper for the SKILL.md path where needed).
- `install` → copy `SKILL.md` + `reference/` into the target dir
  (`shutil.copytree(src, dest, dirs_exist_ok=True)` guarded by the existing
  `--force` check on `SKILL.md`).
- `uninstall` → remove the installed tree (SKILL.md + reference/), then the dir if
  empty.
- `skill path` → report the bundled dir.
- Update the existing skill-install test(s) to assert the tree (SKILL.md +
  at least one reference file) lands at the destination.

### Packaging

- `setup.py` `package_data` is `"crm": ["skills/*.md", "README.md"]`. The
  `skills/*.md` glob does **not** recurse, so add `"skills/reference/*.md"`.
- `crm.spec` bundles the whole dir (`('crm/skills', 'crm/skills')`) — the
  `reference/` subtree rides along automatically. **No spec change**, and this is
  not a bundle-shape change (no new path site).

### Feedback section (self-contained: root pointer + `reference/feedback.md`)

SKILL.md root carries a short `## Found a bug or missing capability?` trigger:
when the agent hits a CLI bug or wants a capability that does not exist, **surface
it to the user and offer to file** — do **not** silently file — and point to
`reference/feedback.md` for the how.

`reference/feedback.md` is fully self-contained (no repo-doc dependency) and holds:

- the exact command: `gh issue create --repo Gharib89/crm --label needs-triage`
  (the `needs-triage` label is inlined here, not referenced from
  `docs/agents/triage-labels.md`);
- a minimal-repro template: the command run, its `--json` envelope, and
  expected-vs-actual;
- the rule to confirm with the user before filing.

`Gharib89/crm` is the upstream repo, so filing works for any user with `gh`
authenticated; the skill assumes nothing about the user having a local checkout.

### Docs shipped in the same change (project house rule)

- **README.md** (lines 31–32) — describe the skill *directory* (thin SKILL.md +
  `reference/*.md`), not a single file.
- **CLAUDE.md** (lines 9, 27) — "single tracked `SKILL.md`" → "skill dir: thin
  `SKILL.md` + `reference/*.md`, single source of truth"; add the drift rule.
- **docs/how-to/skill.md** — `install` now copies a tree; drift check is
  `diff -r`.
- **docs/contributing/skill-and-cli.md** — source of truth is the dir; document
  the drift rule and the "stop restating flags" maintenance test; drift detection
  via `diff -r crm/skills ~/.claude/skills/crm`.
- mkdocs nav: no change (skill + skill-and-cli already listed). `mkdocs build
  --strict` must stay green (no broken internal links from the rewrite).

## Out of scope

- Multi-skill plugin / marketplace packaging (deferred; the dir layout keeps it
  open as a future wrapper).
- New `crm feedback` CLI command (feedback routes through `gh` per the chosen
  channel).
- Changing the skill `description` / trigger string, unless skill-creator's
  evals show a triggering regression.
- copilot/cursor-specific tuning. The tree is copied to those targets too; even a
  harness that ignores reference files still works, because SKILL.md teaches
  runtime discovery via `crm describe` / `--help`.

## Verification

- `crm skill install --target claude --force` lands SKILL.md + `reference/` at the
  destination; `diff -r` is clean against `crm/skills`.
- SKILL.md is materially shorter (target ~180 lines) and contains no restated flag
  lists.
- `pytest` (skill-install test updated) green; `pyright --pythonpath` clean.
- `mkdocs build --strict` green.
- Spot-check: pick 3 command groups, confirm each reference file's content is
  reachable from the SKILL.md map and contains no line regenerable by
  `crm describe`.
- Self-containment audit: `grep -rn 'docs/\|CONTEXT.md\|\.\./' crm/skills/` finds
  no repo-only path reference in any shipped skill file.
