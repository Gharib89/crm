# crm Agent-Skill Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single 1022-line `crm/skills/SKILL.md` with a thin (~180-line) router plus on-demand `reference/*.md` files, ending context bloat and skill↔CLI drift while keeping the skill self-contained for end users who have only the skill (not the repo).

**Architecture:** A skill is a directory. `SKILL.md` keeps only what `crm describe`/`--help` cannot give (trigger, install, auth config, JSON/agent contract, destructive-op contract, a command-group→reference map, and a discovery rule). Per-command recipes, flags, and examples move into nine `reference/*.md` files loaded on demand. `crm skill install` is upgraded to copy the whole tree. The bundle states no flags/choices/defaults (those come from the live `crm describe`) and links to no repo-only path.

**Tech Stack:** Python 3.9-floor, Click 8.2+, pytest, pyright (strict on core), mkdocs-material, PyInstaller, `gh` CLI. Authoring uses the `skill-creator:skill-creator` skill.

**Spec:** `docs/superpowers/specs/2026-06-09-crm-skill-bundle-design.md`

**Source content:** the current `crm/skills/SKILL.md` at HEAD is the content source — move sections verbatim where possible; drop any line that merely restates a flag/choice/default (regenerable by `crm describe`).

---

## File Structure

- Create: `crm/skills/reference/records.md` — entity CRUD + relationships/lookups + query (odata/fetchxml/saved/user) + data import/export + `action` + `@odata.bind`/`--validate` payload guidance
- Create: `crm/skills/reference/metadata.md` — describe/entities/attributes/picklist/relationships/dependencies/export-spec/clone-entity/write-readiness brief/entity-def cache/`--expect` verify/service-document
- Create: `crm/skills/reference/authoring.md` — `apply -f`, `scaffold table`, option sets, `view create`, stage-then-publish workflow
- Create: `crm/skills/reference/solutions.md` — solution lifecycle (publisher/create/set-version/export/import/components), packager extract/pack, validate, component drift, solution dependencies
- Create: `crm/skills/reference/customizations.md` — `app` (appmodule/sitemap), `webresource`, `ribbon`, `form`
- Create: `crm/skills/reference/automation.md` — `plugin` (assembly + step lifecycle), `workflow`
- Create: `crm/skills/reference/security.md` — roles + assignment (BU-scoped)
- Create: `crm/skills/reference/troubleshooting.md` — error taxonomy + retry semantics + `connection doctor`/`whoami` + `session`/audit + on-prem-vs-cloud quirks
- Create: `crm/skills/reference/feedback.md` — self-contained bug/feature reporting via `gh`
- Modify: `crm/skills/SKILL.md` — rewrite to thin router
- Create: `crm/tests/test_skill_bundle.py` — structural guards (line cap, files present, links, self-containment)
- Create: `crm/tests/test_skill_install.py` — install/uninstall tree-copy behavior
- Modify: `crm/commands/skill.py` — install/uninstall/path operate on the tree
- Modify: `setup.py:13-15` — add `skills/reference/*.md` to `package_data`
- Modify: `README.md:31-32` — describe the skill directory
- Modify: `CLAUDE.md:9,27` — describe the dir + drift/self-containment rules
- Modify: `docs/how-to/skill.md` — install copies a tree
- Modify: `docs/contributing/skill-and-cli.md` — source-of-truth dir, drift rule, `diff -r`

`crm.spec` is NOT modified — it already bundles `('crm/skills', 'crm/skills')`, so `reference/` rides along.

### Content map — current SKILL.md section → destination

| Current SKILL.md section | Destination |
|---|---|
| Frontmatter; title; When to use; Install; Configure; On-prem vs cloud; JSON-mode contract (envelope/exit codes/dry-run/REPL); Destructive-ops `--yes` table; Hard constraints; Command discovery | **SKILL.md (router)** |
| Command Groups table | **SKILL.md** → becomes the "Where to look" map (group → reference file) |
| Ex1 whoami, Ex2 odata+`--minimal`, Ex3 CRUD, Ex4 fetchxml, Ex7/7a data export+import, Ex8 action, Ex10 associate/lookup, Ex11 saved view, `@odata.bind`+`--validate` | `reference/records.md` |
| Ex5 browse metadata, Ex5a cache, Ex5b export-spec, Ex5c clone-entity, Ex5e metadata dependencies, Ex9 picklist, Ex9a describe write-readiness, Ex12 `--expect` verify, Ex13 service-document | `reference/metadata.md` |
| Ex12 stage-only+publish-all, `view create`, `apply -f`, `scaffold table` | `reference/authoring.md` |
| Ex5f solution dependencies, Ex6 export+packager extract/pack, Ex6a validate, Solution scaffolding (publisher/create/set-version/add-remove-component), Component drift | `reference/solutions.md` |
| `app`/appmodule/sitemap, `webresource`, `ribbon`, `form` | `reference/customizations.md` |
| `plugin`, `workflow` | `reference/automation.md` |
| `security` | `reference/security.md` |
| Errors & recovery (category table, retry semantics); `connection doctor`; `session`/audit | `reference/troubleshooting.md` |
| (new) | `reference/feedback.md` |

---

## Task 1: Structural guard test for the skill bundle

**Files:**
- Create: `crm/tests/test_skill_bundle.py`

- [ ] **Step 1: Write the failing test**

```python
# crm/tests/test_skill_bundle.py
# pyright: basic
"""Structural guards for the shipped agent-skill bundle (crm/skills/)."""
from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_MD = SKILLS_DIR / "SKILL.md"
REFERENCE_DIR = SKILLS_DIR / "reference"

EXPECTED_REFERENCES = {
    "records.md", "metadata.md", "authoring.md", "solutions.md",
    "customizations.md", "automation.md", "security.md",
    "troubleshooting.md", "feedback.md",
}

# Repo-only paths an end user (skill installed without the repo) would not have.
# A hosted docs URL (https://...) is fine; a local repo path is not.
_FORBIDDEN_PATHS = [
    "CONTEXT.md", "docs/adr", "docs/agents", "docs/contributing",
    "docs/how-to", "docs/reference", "](../", "](docs/",
]

SKILL_MD_MAX_LINES = 250


def _skill_files() -> list[Path]:
    return [SKILL_MD, *sorted(REFERENCE_DIR.glob("*.md"))]


def test_router_is_thin():
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= SKILL_MD_MAX_LINES, (
        f"SKILL.md is {len(lines)} lines (cap {SKILL_MD_MAX_LINES})"
    )


def test_expected_reference_files_present():
    present = {p.name for p in REFERENCE_DIR.glob("*.md")}
    missing = EXPECTED_REFERENCES - present
    assert not missing, f"missing reference files: {sorted(missing)}"


def test_every_reference_is_linked_from_router():
    router = SKILL_MD.read_text(encoding="utf-8")
    for name in sorted(EXPECTED_REFERENCES):
        assert f"reference/{name}" in router, f"{name} not linked from SKILL.md"


def test_no_repo_only_paths_in_shipped_skill():
    for f in _skill_files():
        text = f.read_text(encoding="utf-8")
        for bad in _FORBIDDEN_PATHS:
            assert bad not in text, f"{f.name} references repo-only path '{bad}'"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest crm/tests/test_skill_bundle.py -v`
Expected: FAIL — `test_router_is_thin` (SKILL.md is 1022 lines), `test_expected_reference_files_present` (no `reference/` dir), `test_every_reference_is_linked_from_router`.

- [ ] **Step 3: Commit the guard test**

```bash
git add crm/tests/test_skill_bundle.py
git commit -m "test(skill): structural guards for router + reference bundle"
```

---

## Task 2: Author the router + reference files (uses skill-creator)

**Files:**
- Modify: `crm/skills/SKILL.md`
- Create: `crm/skills/reference/{records,metadata,authoring,solutions,customizations,automation,security,troubleshooting,feedback}.md`

- [ ] **Step 1: Invoke the skill-creator skill**

Invoke `skill-creator:skill-creator`. Target the existing in-repo skill directory `crm/skills/` (restructure in place — do NOT scaffold a fresh skill elsewhere). Use its progressive-disclosure conventions and packaging/validation guidance. If its eval tooling is run, the existing frontmatter `description` is the trigger string and must NOT change unless an eval shows a triggering regression.

- [ ] **Step 2: Rewrite `crm/skills/SKILL.md` as the router**

Keep the frontmatter verbatim (it is the trigger). Body keeps ONLY: When-to-use, Install, Configure (auth/env vars), On-prem vs cloud, the JSON/agent contract (envelope, exit-code table, `--dry-run`/`meta.dry_run`, `--yes`, `meta.warnings`, REPL fail-fast), the destructive-ops `--yes` table, Hard constraints, a discovery rule, the feedback trigger, and the "Where to look" map. Move everything else per the content map. The map section (this exact heading + table, so the guard test's `reference/<name>` links resolve):

```markdown
## Where to look

For exact flags, choices, and defaults, run `crm describe <group>` or
`crm <group> --help` — **never guess a flag.** The skill states only what those
cannot: workflows, gotchas, and the JSON contract. For per-domain detail:

| Working on… | Read |
|---|---|
| records: create/read/update/delete, query (OData/FetchXML/saved), associate/lookup, bulk import/export, ad-hoc `action` | `reference/records.md` |
| metadata: browse schema, picklists, dependencies, export-spec, clone-entity, write-readiness brief, entity-def cache | `reference/metadata.md` |
| schema authoring: `apply -f`, `scaffold table`, option sets, views, stage-then-publish | `reference/authoring.md` |
| solutions: create/export/import, packager extract/pack, validate, component drift | `reference/solutions.md` |
| customizations: model-driven apps, web resources, ribbon, forms, sitemap | `reference/customizations.md` |
| automation: plug-in assemblies & steps, workflows | `reference/automation.md` |
| security: roles & assignment | `reference/security.md` |
| errors, retries, connection diagnostics, session/audit, on-prem vs cloud | `reference/troubleshooting.md` |
| reporting a bug / requesting a feature | `reference/feedback.md` |
```

Add the feedback trigger to the router (short — full how-to lives in the reference):

```markdown
## Found a bug or missing capability?

If `crm` misbehaves or lacks something you need, **tell the user and offer to file
an issue — do not file silently.** On approval, see `reference/feedback.md`.
```

- [ ] **Step 3: Create each reference file by moving its mapped sections**

For each of the nine files, move the sections named in the content map from the
original `crm/skills/SKILL.md`. Rules for every reference file:
- Lead with a one-line purpose and a pointer back: "Flags/choices: `crm <group> --help`."
- Keep workflows, multi-command recipes, and D365 gotchas (on-prem v9.1 cap,
  `@odata.bind` nav-property names, global-optionset `MetadataId` binding, dry-run
  stubs all HTTP incl. GETs, picklist value lookup before writing).
- **Delete** any line that only restates a flag, choice, or default — that is what
  `crm describe`/`--help` is for.
- **No repo-only paths** (`docs/**`, `CONTEXT.md`, `../`). Inline anything needed.

- [ ] **Step 4: Write `crm/skills/reference/feedback.md` (new, self-contained)**

```markdown
# Reporting bugs & requesting features

Found a `crm` bug, or need a capability the CLI doesn't have? **Surface it to the
user first and offer to file an issue — never file silently.**

On the user's approval, file it on the upstream repo with the `gh` CLI:

​```bash
gh issue create --repo Gharib89/crm --label needs-triage \
  --title "<short summary>" \
  --body-file <path>
​```

Issue body template:

​```
## What I was doing
<the exact crm command(s) run>

## Expected
<what should have happened>

## Actual
<the --json envelope or error output>

## Environment
- crm --version: <x.y.z>
- target: on-prem (NTLM) | Dataverse online (OAuth)
- API version: <v9.1 | v9.2>
​```

For a feature request, drop the Expected/Actual split: describe the capability and
the workflow it unblocks. Keep the `needs-triage` label so the maintainer sees it.
```

- [ ] **Step 5: Run the guard test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest crm/tests/test_skill_bundle.py -v`
Expected: PASS (4 tests). If `test_router_is_thin` fails, move more content into reference files; if `test_no_repo_only_paths_in_shipped_skill` fails, inline the referenced content.

- [ ] **Step 6: Manual content spot-check**

Pick 3 reference files; confirm each is reachable from the SKILL.md map and contains no line regenerable by `crm describe`. Confirm `crm/skills/SKILL.md` reads as a coherent router top-to-bottom.

- [ ] **Step 7: Commit**

```bash
git add crm/skills/SKILL.md crm/skills/reference/
git commit -m "feat(skill): split SKILL.md into thin router + reference files"
```

---

## Task 3: Upgrade `crm skill install` to copy the tree

**Files:**
- Create: `crm/tests/test_skill_install.py`
- Modify: `crm/commands/skill.py`

- [ ] **Step 1: Write the failing test**

```python
# crm/tests/test_skill_install.py
# pyright: basic
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from crm.cli import cli


def _runner(tmp_path: Path, monkeypatch) -> CliRunner:
    # Don't load the repo's real ./.env during a no-connection skill command.
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    return CliRunner()


def test_install_copies_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    result = _runner(tmp_path, monkeypatch).invoke(
        cli, ["--json", "skill", "install", "--dest", str(dest), "--force"]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").exists()
    assert (dest / "reference" / "records.md").exists()


def test_uninstall_removes_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert not (dest / "SKILL.md").exists()
    assert not (dest / "reference").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. .venv/bin/python -m pytest crm/tests/test_skill_install.py -v`
Expected: FAIL — `test_install_copies_tree` (current install copies only `SKILL.md`; `reference/records.md` absent at dest).

- [ ] **Step 3: Rewrite the bundled-path helper and the three commands**

Replace `_bundled_skill_path` and the bodies of `skill_path`, `skill_install`, `skill_uninstall` in `crm/commands/skill.py`:

```python
def _bundled_skill_dir() -> Path:
    """Return the directory of the skill bundle shipped inside the installed package."""
    import crm as _crm_pkg
    return Path(_crm_pkg.__file__).resolve().parent / "skills"
```

```python
@skill_group.command("path")
@pass_ctx
def skill_path(ctx: CLIContext):
    """Show the path of the bundled skill directory inside the installed package."""
    src = _bundled_skill_dir()
    skill_md = src / "SKILL.md"
    ctx.emit(skill_md.exists(), data={"path": str(src), "exists": skill_md.exists()})
```

```python
@skill_group.command("install")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="copilot",
    show_default=True,
    help="Where to install the skill. Ignored if --dest is given.",
)
@click.option(
    "--dest",
    type=click.Path(file_okay=False),
    default=None,
    help="Custom destination directory (overrides --target).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing skill at the destination.")
@pass_ctx
def skill_install(ctx: CLIContext, target: str, dest: str | None, force: bool):
    """Copy the bundled skill tree (SKILL.md + reference/) into the agent's skill directory."""
    src_dir = _bundled_skill_dir()
    src_skill = src_dir / "SKILL.md"
    if not src_skill.exists():
        ctx.emit(False, error=f"Bundled SKILL.md not found at {src_skill}.")

    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"

    if dest_file.exists() and not force:
        ctx.emit(
            False,
            error=f"{dest_file} already exists. Use --force to overwrite.",
            meta={"target": target, "dest": str(dest_dir)},
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src_dir, dest_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    refs = sorted((dest_dir / "reference").glob("*.md")) if (dest_dir / "reference").is_dir() else []
    ctx.emit(
        True,
        data={
            "installed": True,
            "source": str(src_dir),
            "dest": str(dest_dir),
            "files": ["SKILL.md", *[f"reference/{p.name}" for p in refs]],
        },
        meta={"target": target if not dest else "custom"},
    )
```

```python
@skill_group.command("uninstall")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="copilot",
    show_default=True,
)
@click.option("--dest", type=click.Path(file_okay=False), default=None)
@pass_ctx
def skill_uninstall(ctx: CLIContext, target: str, dest: str | None):
    """Remove the installed skill (SKILL.md + reference/, and the directory if empty)."""
    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"
    if not dest_file.exists():
        ctx.emit(True, data={"removed": False, "reason": "not installed", "dest": str(dest_file)})
        return
    ref_dir = dest_dir / "reference"
    if ref_dir.is_dir():
        shutil.rmtree(ref_dir)
    dest_file.unlink()
    try:
        dest_dir.rmdir()
    except OSError:
        pass
    ctx.emit(True, data={"removed": True, "dest": str(dest_dir)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. .venv/bin/python -m pytest crm/tests/test_skill_install.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint the changed module**

Run: `.venv/bin/pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/commands/skill.py`
Expected: 0 errors (file is `# pyright: basic`).

- [ ] **Step 6: Commit**

```bash
git add crm/commands/skill.py crm/tests/test_skill_install.py
git commit -m "feat(skill): install/uninstall the whole skill tree, not just SKILL.md"
```

---

## Task 4: Ship the reference files in the wheel (`setup.py`)

**Files:**
- Modify: `setup.py:13-15`

- [ ] **Step 1: Add the recursive-glob entry**

Change:

```python
    package_data={
        "crm": ["skills/*.md", "README.md"],
    },
```

to:

```python
    package_data={
        "crm": ["skills/*.md", "skills/reference/*.md", "README.md"],
    },
```

- [ ] **Step 2: Verify the data files are picked up in a build**

Run: `.venv/bin/python -m build --sdist 2>/dev/null && tar tzf dist/*.tar.gz | grep 'skills/reference/'`
Expected: lists `crm/skills/reference/records.md` (and the other eight). If `build` is unavailable, instead confirm the line is present: `grep -n 'skills/reference' setup.py`.

- [ ] **Step 3: Commit**

```bash
git add setup.py
git commit -m "build(skill): include skills/reference/*.md in package_data"
```

---

## Task 5: Update docs (README, CLAUDE.md, how-to, contributing)

**Files:**
- Modify: `README.md:31-32`
- Modify: `CLAUDE.md:9,27`
- Modify: `docs/how-to/skill.md`
- Modify: `docs/contributing/skill-and-cli.md`

- [ ] **Step 1: README.md — describe the directory**

Replace:

```markdown
- **`crm/skills/SKILL.md`** — the agent skill loaded by skill-aware harnesses
  (Claude Code, Copilot CLI, …) after `crm skill install`.
```

with:

```markdown
- **`crm/skills/`** — the agent skill loaded by skill-aware harnesses (Claude
  Code, Copilot CLI, …) after `crm skill install`: a thin `SKILL.md` router plus
  `reference/*.md` files loaded on demand.
```

- [ ] **Step 2: CLAUDE.md — line 9 (Architecture bullet)**

Replace:

```markdown
- `crm/skills/SKILL.md` — agent-skill copy shipped in the wheel (kept in sync with the CLI — see below).
```

with:

```markdown
- `crm/skills/` — agent skill shipped in the wheel: a thin `SKILL.md` router + `reference/*.md` loaded on demand (kept in sync with the CLI — see below).
```

- [ ] **Step 3: CLAUDE.md — line 27 (SKILL ↔ CLI bullet)**

Replace:

```markdown
- **SKILL ↔ CLI** — `crm/skills/SKILL.md` is the single tracked agent skill (source of truth); `crm skill install` copies it into a harness dir outside the repo (`~/.claude/skills/crm/`, etc.). Never track an in-repo `.claude/skills/` copy. See `docs/contributing/skill-and-cli.md`.
```

with:

```markdown
- **SKILL ↔ CLI** — `crm/skills/` is the single tracked agent skill (source of truth): a thin `SKILL.md` router + `reference/*.md`. `crm skill install` copies the whole tree into a harness dir outside the repo (`~/.claude/skills/crm/`, etc.). The skill is **self-contained** — it ships to users who have only the skill, not the repo, so never link a shipped skill file to a repo path (`docs/**`, `CONTEXT.md`); inline what's needed. The skill states only what `crm describe`/`--help` cannot (workflows, gotchas, the JSON contract) — **never restate flags/choices/defaults**. Never track an in-repo `.claude/skills/` copy. See `docs/contributing/skill-and-cli.md`.
```

- [ ] **Step 4: docs/how-to/skill.md — reflect the tree**

Replace the "Install the bundled agent skill" body line:

```markdown
Copies the bundled `SKILL.md` into the agent's skill directory; `--target` is `claude | copilot | cursor` (default `copilot`).
```

with:

```markdown
Copies the bundled skill tree (`SKILL.md` + `reference/*.md`) into the agent's skill directory; `--target` is `claude | copilot | cursor` (default `copilot`).
```

Replace the "Install to a custom directory" body line:

```markdown
`--dest` overrides `--target`; `--force` overwrites an existing `SKILL.md` at the destination.
```

with:

```markdown
`--dest` overrides `--target`; `--force` overwrites an existing skill at the destination.
```

Replace the "Show the bundled skill's path" body line:

```markdown
Prints the path of the `SKILL.md` shipped inside the installed package.
```

with:

```markdown
Prints the path of the bundled skill directory shipped inside the installed package.
```

Replace the "Uninstall the skill" body line:

```markdown
Removes the installed `SKILL.md` (and its directory if empty) for the given target.
```

with:

```markdown
Removes the installed skill (`SKILL.md` + `reference/`, and the directory if empty) for the given target.
```

- [ ] **Step 5: docs/contributing/skill-and-cli.md — rewrite "Source of truth" + "When the CLI changes"**

Replace the section from `## Source of truth` through the `diff crm/skills/SKILL.md ~/.claude/skills/crm/SKILL.md` block with:

````markdown
## Source of truth

`crm/skills/` is the canonical agent skill — a thin `SKILL.md` router plus
`reference/*.md` files loaded on demand. The package ships the whole tree
(`package_data` in `setup.py`; bundled in `crm.spec`) and `crm skill install`
copies it into an agent's skill directory:

| Target | Destination |
| --- | --- |
| `claude` | `~/.claude/skills/crm/` |
| `copilot` | `~/.copilot/skills/crm/` |
| `cursor` | `~/.cursor/rules/crm/` |

```bash
crm skill install --target claude --force
```

## Two rules that keep the skill healthy

1. **Self-contained.** The skill ships to users who have only the skill, not this
   repo. No shipped skill file may link to a repo-only path (`docs/**`,
   `CONTEXT.md`, `../`) or assume any repo file is present. Inline what the agent
   needs (labels, templates, tables). The only assumed externals are the installed
   `crm` binary and, for the feedback flow, the `gh` CLI.
2. **Never restate flags.** `crm describe [group]` and `crm <group> --help` emit
   every command, option, choice, default, and envvar straight from the live Click
   tree — they cannot drift. The skill states only what they cannot: workflows,
   gotchas, and the JSON contract. Maintenance test: **if a line could be
   regenerated by `crm describe`, delete it.**

`crm/tests/test_skill_bundle.py` enforces both rules plus a router line cap.

## When the CLI changes

After adding or changing a command, update the relevant `crm/skills/reference/*.md`
(workflows/gotchas only — not flags) and the SKILL.md map if a new group appeared,
then reinstall with `--force`. To detect drift against an install:

```bash
diff -r crm/skills ~/.claude/skills/crm
```
````

- [ ] **Step 6: Build the docs site strict**

Run: `.venv/bin/mkdocs build --strict`
Expected: build succeeds, 0 warnings (no broken internal links).

- [ ] **Step 7: Commit**

```bash
git add README.md CLAUDE.md docs/how-to/skill.md docs/contributing/skill-and-cli.md
git commit -m "docs(skill): document router + reference bundle and the drift/self-containment rules"
```

---

## Task 6: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=. .venv/bin/python -m pytest crm/tests/test_skill_bundle.py crm/tests/test_skill_install.py crm/tests/test_describe.py crm/tests/test_lazy_imports.py -v`
Expected: all PASS. (E2E tests needing live D365 creds may be skipped/deselected.)

- [ ] **Step 2: Lint**

Run: `.venv/bin/pyright --pythonpath .venv/bin/python --pythonversion 3.9`
Expected: no new errors versus baseline.

- [ ] **Step 3: Self-containment audit**

Run: `grep -rnE 'CONTEXT\.md|docs/(adr|agents|contributing|how-to|reference)|\]\(\.\./|\]\(docs/' crm/skills/`
Expected: no output (no shipped skill file references a repo-only path).

- [ ] **Step 4: Docs strict build**

Run: `.venv/bin/mkdocs build --strict`
Expected: succeeds, 0 warnings.

- [ ] **Step 5: Live install smoke test**

Run: `.venv/bin/crm --json skill install --dest /tmp/crm-skill-smoke --force && ls -R /tmp/crm-skill-smoke && rm -rf /tmp/crm-skill-smoke`
Expected: envelope `ok:true` with a `files` list of `SKILL.md` + nine `reference/*.md`; `ls -R` shows the tree.

---

## Self-Review

**Spec coverage:**
- Thin router + 9 reference files → Tasks 1, 2 ✓
- Drift rule (no restated flags) → Task 2 Step 3, Task 5 Step 5, guard test (Task 1) ✓
- Self-containment constraint → Task 1 (`test_no_repo_only_paths`), Task 2 rules, Task 5 (CLAUDE.md + contributing), Task 6 Step 3 ✓
- `reference/feedback.md` self-contained (inlined label + template) → Task 2 Step 4 ✓
- Install tree-copy + test → Task 3 ✓
- Packaging (`setup.py`) → Task 4 ✓; `crm.spec` no-change noted in File Structure ✓
- Docs (README, CLAUDE.md, how-to/skill.md, contributing) → Task 5 ✓
- skill-creator used for authoring → Task 2 Step 1 ✓
- Verification (pytest, pyright, mkdocs --strict, grep audit, live install) → Task 6 ✓

**Placeholder scan:** No TBD/TODO. The reference-file *content* is a move of existing, located SKILL.md sections (mapped exactly in the content-map table), not new prose — the source is the current `crm/skills/SKILL.md`. `feedback.md` is given in full.

**Type/name consistency:** `_bundled_skill_dir()` defined in Task 3 Step 3 and used in `skill_path`/`skill_install` in the same step. `EXPECTED_REFERENCES` set (Task 1) matches the nine created files (Task 2) and the install smoke-test count (Task 6). The "Where to look" map links (Task 2 Step 2) contain every `reference/<name>` the guard test (Task 1) checks.

**Conventions honored:** test files carry `# pyright: basic`; `open()`/`read_text` use `encoding="utf-8"`; tests run via `PYTHONPATH=. .venv/bin/python` with pyright pinned to 3.9; commit subjects are Conventional Commits so PSR versions correctly (`feat:` bumps, `docs:`/`test:`/`build:` do not).
