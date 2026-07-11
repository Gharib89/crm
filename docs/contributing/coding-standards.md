# Coding standards

> **Draft.** Assembled from the rules already in force across `.github/copilot-instructions.md`,
> `CLAUDE.md`, `CONTEXT.md`, the ADRs, and the actual code — nothing here is invented. Gaps and
> contested points are called out at the end for the follow-up grilling session
> ([Decide: coding standards for the repo](https://github.com/Gharib89/crm/issues/826)); everything
> above that line is already-established convention, not up for debate.
>
> Once accepted, this doc is the single source reviewers (the `code-review` skill, CodeRabbit's
> path instructions, `.github/copilot-instructions.md`) derive from — they should shrink to a
> pointer here rather than repeat these rules.

## Architecture & layering

- `crm/core/*` — Web API logic, one module per domain (`entity`, `connection`, `metadata`,
  `async_ops`, `batch`, `charts`, `dashboard`, `dup`, …).
- `crm/commands/*` — thin Click wrappers, one file per `crm <group>`, one command group per
  domain, mirroring the `core` module it wraps.
- Credentials come only from a saved profile (`crm profile add`) or a per-run `--password`;
  never environment variables.

## Type checking

- `pyrightconfig.json` sets `strict` globally; `crm/commands/*` and most test files opt back out
  to basic via a `# pyright: basic` header comment — intentional, not a gap to close file-by-file.
  `crm/core/*` and `crm/utils/d365_backend.py` are the strict surface and stay that way.
- Boundary rule for D365 payloads: `dict[str, Any]` at the raw OData seam (a `TypedDict` cast
  over external JSON is a false contract — the server can add/omit keys the type doesn't know
  about). Reserve `TypedDict` for structures the CLI itself constructs and owns (e.g.
  `Reference` in `crm/core/references.py`).
- Run pyright locally with `--pythonpath .venv/bin/python --pythonversion 3.9` — omitting either
  flag masks import errors or lets 3.10+-only symbols pass that fail at the pinned runtime.

## Click command pattern

Every command follows the same shape: `@pass_ctx` decorator, `ctx: CLIContext` as the first
parameter, `ctx.backend()` to reach the backend, `ctx.json_mode` / `ctx.dry_run` to branch
behavior, `ctx.emit(...)` as the single result seam.

```python
@org_group.command("brief")
@pass_ctx
def org_brief(ctx: CLIContext):
    with d365_errors(ctx):
        brief = org_mod.org_brief(ctx.backend())
    if ctx.json_mode:
        ctx.emit(True, data=brief, meta=meta)
```

House rules on top of the shape:

- Wrap file read/write in `try/except OSError` and return a clean error envelope — never let a
  raw `OSError` traceback reach the user.
- Mutually exclusive flags raise `click.UsageError` (exit `2`); never fake it with
  `ctx.emit(False)` (exit `1`) — those are different failure classes (see Exit codes below).
  Validate untrusted input before calling `ctx.backend()`, not after.
- `CLIContext.emit(meta=...)` renders in human mode too, not just `--json` — gate any
  JSON-only field on `ctx.json_mode` before adding it to `meta`.
- When serializing a Click option for introspection (`crm describe`, docs generation), emit
  `opt.secondary_opts` (the `--no-*` forms) alongside `opt.opts`, not `opts` alone.

## Error handling & exit codes

- All D365/backend failures are a `D365Error` (`crm/utils/d365_backend.py`) carrying
  `status`, `code`, `response_body`, and (for multi-stage writes) `completed_steps` / `stage`.
  Commands funnel it through the `d365_errors(ctx)` context manager
  (`crm/commands/_helpers/errors.py`), never a bare `try/except D365Error` that hand-builds the
  envelope.
- Exit-code contract ([ADR 0001](../adr/0001-cli-exit-code-contract.md)): `0` success, `1`
  operational failure (server error, in-command validation, declined confirmation), `2` Click
  usage error. Granular per-class codes are rejected — failure-class detail lives in the
  envelope (`error`, `meta.status/code/category/retryable`), not the exit code.
- The envelope's `{status, code, category, retryable}` is reserved and always derived from the
  caught `D365Error` itself; an `enrich(exc)` callback's `extra_meta` is strictly additive and
  raises if it names a reserved key.
- Never `assert` for a runtime invariant in shipped code — `assert` is stripped under
  `python -O`, and the frozen PyInstaller build can run optimized. Raise `D365Error` (or the
  appropriate domain error) even for a logically-unreachable check.

## Output contract

- The `data` payload is curated and CLI-owned, never a passthrough of the raw D365 Web API
  response ([ADR 0008](../adr/0008-cli-output-contract.md)). List verbs put a bare array in
  `data`; OData paging (`@odata.nextLink`, `@odata.count`) relocates to `meta.next_link` /
  `meta.count`; per-row `@odata.*` protocol keys are stripped.
- `_entity_id` (+ `_entity_id_url`) is the one normalized key for an affected record's GUID
  across `create`/`update`/`delete`/`entity get` — the leading underscore marks it as
  CLI-synthesized, distinct from the real PK attribute.
- Client-side output shaping (`--fields`, `--jq`, [ADR 0023](../adr/0023-client-side-output-shaping.md))
  is a post-curation, envelope-preserving transform applied once at the `CLIContext.emit` seam —
  never re-implemented per command. It only touches `data`; error envelopes bypass shaping
  entirely.

## Dry-run contract

- Reads-execute rule: under `--dry-run`, only mutations are previewed — every GET still runs for
  real, which is what lets a preview report live facts (`_exists`, `would_skip`) instead of
  guesses.
- A dry-run mutator returns `{_dry_run: true, would_*}` (plus `meta.dry_run: true`), never the
  bare success key a real write would return (`deleted: true`, etc.).

## Encoding

Full policy in [`docs/contributing/encoding.md`](encoding.md); the load-bearing rules:

- Every file read uses an explicit encoding — never the OS-locale default. `utf-8-sig` for
  anything a user or external tool may author (CSV, JSONL, YAML/JSON specs, FetchXML); plain
  `utf-8` (no BOM) for writes.
- Subprocess captures pin `encoding="utf-8", errors="replace"` — `text=True` alone decodes with
  the locale default and can crash on a stray non-UTF-8 byte.
- Unix-only imports (`fcntl`, `pwd`, `termios`, …) need `try/except ImportError` at the import
  line so the module still loads on Windows.
- Consult metadata to type a value crossing the file boundary; don't infer type from the value's
  shape (a string-typed alternate-key column keeps its verbatim text, leading zeros and all).

## D365 API conventions

- Logic enforcing a header invariant ("never emit both X and Y") uses `requests`'
  `CaseInsensitiveDict`, never a plain `dict` — headers are case-insensitive on the wire.
- `@odata.bind` navigation-property names must match `$metadata` casing exactly; system entities
  (`sdkmessage*`, `solution`, …) use lowercase logical names — flag a guessed PascalCase name.

## Docs-in-sync obligation

Every new/changed command, flag, or behavior ships its docs in the same change:
`docs/how-to/<group>.md`, `docs/reference/cli.md`, `README.md` if user-facing, and `crm/skills/`
if the change is workflow-visible. Never hand-edit `CHANGELOG.md` — `python-semantic-release`
generates it from commit subjects. A new lazily-loaded command group is added to `crm.spec`
hiddenimports (PyInstaller) in the same change.

## Public-repo hygiene

The repo is public and must stay generic: flag real org names, internal hostnames, or
real-looking GUIDs in tests/docs. Placeholders are `Contoso` / `internalcrm.contoso.local`.

## Known non-issues — do not flag

- `D365Backend` retries only `502`/`503`/`504` (+`429`) for idempotent methods, not all 5xx; a
  test using a bare `500` to exercise the no-retry path is intentional.
- Click is pinned `>=8.2`: `CliRunner` has no `mix_stderr` parameter — stdout/stderr are always
  separate streams.
- `# pyright: basic` headers in test files are intentional (see Type checking above).
- OAuth token acquisition raises `D365Error` during `session.get()`, not a `requests` exception —
  a caller catching `D365Error` around a raw session call is correct, not dead code.

## Gaps & contested points (for the grilling session)

These are open questions this research ticket surfaces but does not resolve — for
[Decide: coding standards for the repo](https://github.com/Gharib89/crm/issues/826):

- **Docstrings**: present in `crm/core/*` (module + function level, e.g. `entity.py`) but
  spot-checked rather than systematic or enforced anywhere. No stated rule exists today beyond
  CLAUDE.md's "document public APIs and non-obvious WHY" — worth deciding whether that's
  sufficient or needs a sharper, checkable rule.
- **Enforcement**: none of the above is mechanically gated yet (no ruff, no docstring linter).
  This doc is convention-only until [Task: adopt ruff](https://github.com/Gharib89/crm/issues/828)
  lands; whether ruff should also enforce any of these specific conventions (import order,
  line length, etc.) is undecided.
- **`crm/commands/*` pyright basic**: is the split permanent-by-design, or should command files
  tighten to strict over time? Not decided — flagging so the grilling session states it
  explicitly rather than leaving it implicit.
- **Reviewer wiring**: this doc's structure assumes CodeRabbit path instructions and
  `.github/copilot-instructions.md` will point here rather than restate it — the actual wiring
  (which sections map to which path globs, how much of copilot-instructions.md shrinks) is
  [Task: wire coding standards into all reviewers](https://github.com/Gharib89/crm/issues/827),
  not this ticket.
