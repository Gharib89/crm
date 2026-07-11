# Coding standards

> The canonical coding-standards doc — assembled from the rules already in force across
> `.github/copilot-instructions.md`, `CLAUDE.md`, `CONTEXT.md`, the ADRs, and the actual code;
> previously-open points (docstrings, formatting/linting, the commands-layer type-checking split)
> have their decision record in
> [Decide: coding standards for the repo](https://github.com/Gharib89/crm/issues/826).
>
> This doc is the single source reviewers (the `code-review` skill, CodeRabbit's path
> instructions, `.github/copilot-instructions.md`) derive from — they should shrink to a pointer
> here rather than repeat these rules.

## Architecture & layering

- `crm/core/*` — Web API logic, one module per domain (`entity`, `connection`, `metadata`,
  `async_ops`, `batch`, `charts`, `dashboard`, `dup`, …).
- `crm/commands/*` — thin Click wrappers, one file per `crm <group>`, one command group per
  domain, mirroring the `core` module it wraps.
- Credentials come only from a saved profile (`crm profile add`) or a per-run `--password`;
  never environment variables.

## Type checking

- `pyrightconfig.json` sets `strict` globally; `crm/commands/*` and most test files opt back out
  to basic via a `# pyright: basic` header comment. This split is **permanent by design**, not
  debt: the commands layer is thin, decorator-heavy Click glue where strict mode fights the
  framework for near-zero bug yield. Don't flag it and don't tighten files opportunistically —
  when a command file accumulates real logic, the fix is moving that logic into `crm/core/*`
  (the strict surface, together with `crm/utils/d365_backend.py`), not tightening the wrapper.
- Boundary rule for D365 payloads: `dict[str, Any]` at the raw OData seam (a `TypedDict` cast
  over external JSON is a false contract — the server can add/omit keys the type doesn't know
  about). Reserve `TypedDict` for structures the CLI itself constructs and owns (e.g.
  `Reference` in `crm/core/references.py`).
- Run pyright locally with `--pythonpath .venv/bin/python --pythonversion 3.13` — omitting either
  flag masks import errors or lets 3.10+-only symbols pass that fail at the pinned runtime.

## Docstrings

- Intent rule, not a coverage quota: a public function in `crm/core/*` gets a docstring when its
  name and signature don't already say what it does and why. Self-explanatory helpers stay bare —
  forced docstrings are narrative noise.
- Sectioned docstrings use **Google style** (`Args:` / `Returns:` / `Raises:`) — the only style
  in use today; don't introduce Sphinx/NumPy variants.
- Lint enforces **format only** (ruff `D2xx`/`D4xx`: quoting, blank lines, imperative mood) on
  docstrings that exist; there is deliberately no missing-docstring (`D1xx`) rule.

## Formatting & linting

Decision record: [Decide: coding standards for the repo](https://github.com/Gharib89/crm/issues/826);
gates landed via [Task: adopt ruff](https://github.com/Gharib89/crm/issues/828) — CI enforces
`ruff check` + `ruff format --check`, and `pre-commit install` (once per clone) runs both on
staged files so the format round-trip never reaches CI.

- **ruff** is the single formatter and linter. `pyproject.toml` is the source of truth for the
  exact rule codes; the agreed shape:
    - line length **100** (matches the codebase's natural style; not the ruff default 88),
    - rule families `E`, `W`, `F`, `I` (import order), `B` (bugbear), `UP` (pyupgrade,
      `target-version = py39`), plus the format-only `D` subset above,
    - deliberately excluded: `S` (bandit — rejected in the tooling sweep), `C90` complexity,
      `ANN` (pyright owns typing), `D1xx` docstring coverage, and `D205` (blank line between
      docstring summary and description) — D205 treats only physical line 1 as the summary, so
      it forces multi-line summaries onto one line, which then trips `E501` as breakable prose;
      482 of 569 sites can't satisfy both without rewording, and the descriptive-first-line
      convention here is deliberate (#843),
- `ruff format` covers the whole tree — `crm/` including tests, plus `scripts/` — no carve-outs.
- Noisy rules in tests get a scoped `per-file-ignores` entry, never a global rule removal.

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
    ctx.emit(True, data=brief)
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
