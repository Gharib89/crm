# Local audit journal of agent-driven mutations (issue #89)

Append-only JSONL journal at `${CRM_HOME:-~/.crm}/audit/<session>.jsonl`, written in the
**command layer** (not `emit` — emit can't tell reads from writes and lacks
command/target/solution context). A `_journal(...)` helper fires after each successful
`emit` on **mutating** verbs only. Plus a `crm session audit [--tail N] [--session NAME]`
reader.

## Key facts (verified against code)

- `emit(True, ...)` **returns** on success in both JSON and human mode (`cli.py:83`/`114`);
  it raises `click.Exit` only on failure. So a `_journal(...)` call placed *after* a
  successful `emit` always runs. Errors route through `_handle_d365_error` → `emit(False)`
  → raises before reaching the journal line, so failures are never journaled. ✔ matches spec.
- Home dir convention: `Path(os.environ.get("CRM_HOME", str(Path.home()/".crm"))).expanduser()`
  — see `crm/core/metadata_cache.py:45` and `crm/core/session.py:26`. Reuse it; add an
  `audit/` sibling to `profiles/`, `sessions/`, `cache/`.
- `ctx` carries: `session_name` (default `"default"`, `--session`), `profile_name`,
  `dry_run`, `stage_only` (sticky safety flag, `cli.py:390`). `staged` = `ctx.stage_only`.
- Mutating-verb `result` dicts consistently carry an `id` key (`{"deleted":True,"id":...}`,
  `{"updated":True,"id":...}`, create returns `{"id": guid}`, etc.) → `result_id` extracts it
  defensively, falling back to any `*id`/`*Id` GUID-ish value, else `None` (dry-run previews
  have no real id → `None`).
- Tests use `monkeypatch.setenv("CRM_HOME", str(tmp_path))` for isolation; clock is injected
  (`now=`), mirroring `crm/core/metadata_cache.py` tests. `crm/core/*` is pyright **strict**;
  `crm/commands/*` is basic (`# pyright: basic` header).

## Scope decision (call to flag to spec reviewer)

The issue's "What to build" prose lists an *illustrative* set of verbs; the acceptance
criterion is broader: **"Mutating verbs append a JSONL audit line; reads do not."** We
therefore journal **every write verb** — including ones the prose didn't name
(`entity disassociate`/`clear-lookup`, `webresource`, `app`, `view`, `data import`). An audit
journal that silently omits some mutations is a worse audit journal. This is deliberate, not
scope creep.

---

## Task 1 — Core module `crm/core/audit.py` + unit tests (pyright strict)

Pure, dependency-light module. No Click, no ctx, no backend.

**API:**
- `_audit_root() -> Path` — `${CRM_HOME:-~/.crm}/audit`, `mkdir(parents=True, exist_ok=True)`.
- `_journal_path(session: str) -> Path` — `_audit_root() / f"{session}.jsonl"`.
- `_extract_result_id(result: Any) -> str | None` — if `result` is a dict: return
  `str(result["id"])` if present and truthy; else the first value whose key lower-cases to
  `"id"` or ends in `"id"` and looks GUID-ish (`[0-9a-fA-F-]{32,36}`); else `None`. Non-dict → `None`.
- `record(*, session: str, profile: str | None, command: str, target: str | None,
  result: Any, solution: str | None = None, staged: bool = False, dry_run: bool = False,
  ok: bool = True, now: datetime | None = None) -> None`
  — build the line dict in **exact key order** `{ts, profile, command, target, solution,
  staged, dry_run, ok, result_id}`; `ts = (now or datetime.now(timezone.utc)).isoformat()`;
  `result_id = _extract_result_id(result)`; **append** (`open(path, "a", encoding="utf-8")`)
  one `json.dumps(line) + "\n"`, then `f.flush()` + `os.fsync(f.fileno())`. **Never** write
  the request payload. **Best-effort**: wrap the whole write in `try/except OSError: pass` so a
  journal failure never propagates.
- `read(session: str, *, tail: int | None = None) -> list[dict[str, Any]]` — read the file
  (missing file → `[]`), parse each non-blank line with `json.loads`, **skip** malformed lines,
  return all rows or the last `tail`.

**Tests** `crm/tests/test_audit_core.py` (`monkeypatch.setenv("CRM_HOME", tmp_path)`, fixed
`now`):
- record→read roundtrip; exact key order/schema; no `payload`/request-body key ever present.
- append (two `record` calls → two lines, first preserved).
- `dry_run=True` row tagged `dry_run: true`.
- `result_id`: dict with `id`; dict with `accountid` GUID; dict with no id; list; `None`.
- `tail=N` returns last N; `tail` larger than file returns all.
- malformed line in the middle is skipped, valid lines still returned.
- OSError swallowed: point `CRM_HOME` at an unwritable location (or monkeypatch `open` to
  raise OSError) → `record` returns without raising.

Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/core/audit.py` clean.

---

## Task 2 — `_journal` command helper + `crm session audit` reader + tests

**`crm/commands/_helpers.py`** — add:
```python
def _journal(ctx, command, target, result, *, solution=None, staged=None):
    """Best-effort audit-journal a successful mutation (issue #89). Never raises."""
    try:
        from crm.core import audit
        audit.record(
            session=ctx.session_name,
            profile=ctx.profile_name,
            command=command,
            target=target,
            result=result,
            solution=solution,
            staged=ctx.stage_only if staged is None else staged,
            dry_run=ctx.dry_run,
        )
    except Exception:
        pass
```
(Local import keeps `audit` off the `crm --version` fast path. `audit.record` already swallows
OSError; the outer guard catches anything else so journaling can never break a command.)

**`crm/commands/session.py`** — add subcommand:
```python
@session_group.command("audit")
@click.option("--tail", type=int, default=None, help="Only the last N rows.")
@click.option("--session", "session_override", default=None,
              help="Read another session's journal (default: current --session).")
@pass_ctx
def session_audit(ctx, tail, session_override):
    """Show this session's audit journal of mutations."""
    from crm.core import audit
    name = session_override or ctx.session_name
    rows = audit.read(name, tail=tail)
    if ctx.json_mode:
        ctx.emit(True, data=rows, meta={"session": name, "count": len(rows)})
        return
    if not rows:
        ctx.skin.info("No audit entries.")
        return
    for r in rows:
        flags = []
        if r.get("dry_run"): flags.append("dry-run")
        if r.get("staged"): flags.append("staged")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        click.echo(f"  {r.get('ts','')}  {r.get('command','')}  "
                   f"{r.get('target') or ''}  {r.get('result_id') or ''}{suffix}")
```
Match the human-mode style of `session_history`. Keep `# pyright: basic`.

**Tests** `crm/tests/test_session_audit.py`:
- `_journal` with a stub ctx (object with `session_name`/`profile_name`/`dry_run`/`stage_only`)
  writes a line readable by `audit.read`; `staged`/`dry_run` reflect ctx; explicit `staged=`
  overrides; explicit `solution=` recorded.
- `_journal` never raises even if `audit.record` blows up (monkeypatch it to raise).
- `crm session audit` via `CliRunner`: empty journal → "No audit entries" (human) / `[]` (json);
  after seeding rows, `--tail 1` returns the last; `--session other` reads a different file;
  JSON envelope shape `{ok, data:[...], meta:{session,count}}`.

---

## Task 3 — Wire `_journal` into every mutating call site + integration tests

After each successful `ctx.emit(True, ...)` / `_emit_with_warning(...)` on a **write** verb,
add `_journal(ctx, "<cli command path>", <target>, <result/info dict>, solution=<solution if in scope>)`.
Import `_journal` from `crm.commands._helpers` in each file (alongside the existing helper
imports). `command` = the CLI path (`"entity create"`, `"metadata create-entity"`,
`"solution import"`, `"batch"`, `"action invoke"`, `"workflow run"`, …). `target` = the primary
identifier argument in scope (`entity_set`, `record_id`-bearing target, metadata logical/schema
name, solution uniquename, workflow id, action name, web-resource name, app id, view name). Pass
`solution=solution` only where a resolved `solution` variable exists (metadata writes).

**Exhaustive call-site list** (from the code map — read each file to confirm the emit line and
pick the natural `target`):
- `crm/commands/entity.py` (8): create, update, upsert, delete, associate, disassociate,
  set-lookup, clear-lookup.
- `crm/commands/metadata.py` (11): create-entity, update-entity, update-attribute,
  update-relationship, delete-entity, delete-attribute, create-one-to-many,
  create-many-to-many, delete-relationship, create-optionset, update-optionset,
  delete-optionset. *(That is 12 verbs — confirm each emit; pass `solution=solution` where the
  command resolved one.)*
- `crm/commands/solution.py`: create-publisher, create, set-version, add-component,
  remove-component, publish-all, publish, import.
- `crm/commands/batch.py` (1): batch.
- `crm/commands/workflow.py` (3): activate, deactivate, run.
- `crm/commands/action.py` (1): invoke.
- `crm/commands/webresource.py` (2): create, update.
- `crm/commands/app.py` (2): create, add-components.
- `crm/commands/view.py` (1): create.
- `crm/commands/data.py` (1): import.

Do **not** touch any read/query/get/list/export command.

**Integration tests** `crm/tests/test_audit_integration.py` (CliRunner, `CRM_HOME`→tmp_path,
`CRM_DOTENV`→noop, env snapshot/restore):
- `entity create … --dry-run` → exactly one journal line, `command=="entity create"`,
  `target==<entity_set>`, `dry_run==true`, `ok==true`.
- A representative non-dry-run mutation against a `requests_mock`-stubbed backend (e.g.
  `entity delete --yes`) → one line with the right `result_id`.
- A **read** verb (e.g. `entity get` or `query`) → journal file absent / **zero** lines.
- One more module for coverage breadth (e.g. a `metadata` or `solution` dry-run verb) → line present.

---

## Task 4 — Docs sync (CLAUDE.md mandate: docs ship with code)

- `README.md` — note the audit journal + `crm session audit` under the relevant section.
- `CHANGELOG.md` — entry under `## [Unreleased]` (Keep a Changelog "Added").
- `docs/how-to/session.md` (or the session how-to that documents `session info/clear/history`;
  grep to find it) — document `session audit [--tail N] [--session NAME]` and the
  `~/.crm/audit/<session>.jsonl` location + line schema.
- `docs/reference/cli.md` — add the `session audit` entry.
- `crm/skills/SKILL.md` — sync the new command (single tracked agent skill, source of truth).
- Verify: `mkdocs build --strict` passes (no stale refs / broken links).

---

## Verification gate (whole feature)

`CRM_DOTENV=/tmp/noop CRM_HOME=/tmp/crmhome PYTHONPATH=<worktree> <main-venv>/python -m pytest -q`
green; `<main-venv>/pyright --pythonpath .venv/bin/python --pythonversion 3.9` clean on
`crm/core/audit.py`; `mkdocs build --strict` clean. Then PR + Copilot review.
