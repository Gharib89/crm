# Copilot instructions — crm

Python CLI for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) and Dataverse online (OAuth) — over the Dataverse Web API (OData v4). `crm/core/*` holds the Web API logic (pyright strict); `crm/commands/*` are thin Click wrappers; credentials come only from saved profiles or `--password`, never env vars.

Canonical standards: `docs/contributing/coding-standards.md` — this file is derived from it. On conflict the doc wins; rule changes land there first.

## Code review priorities

Focus on correctness, error handling, and contract violations. CI already runs pytest, pyright (strict on `crm/core/*` and `crm/utils/d365_backend.py`), and `mkdocs build --strict` — do not flag issues those tools catch.

### House rules to enforce

- Command layer: wrap file read/write in `try/except OSError` and return a clean error envelope; mutually exclusive flags must `raise click.UsageError` (exit 2), never `ctx.emit(False)` (exit 1); validate untrusted input before calling `ctx.backend()`. Backend failures funnel through `d365_errors(ctx)`.
- Exit codes (ADR 0001): 0 success, 1 operational failure, 2 usage error — failure-class detail lives in the envelope.
- Output contract (ADR 0008): `data` is curated and CLI-owned, never a raw Web API passthrough; OData paging keys move to `meta`; `_entity_id` is the one normalized GUID key.
- Dry-run mutators must return a `{_dry_run, would_*}` preview, never the bare success key. Reads-execute rule: GETs still run live under `--dry-run`.
- Encoding: explicit encoding on every file read — `utf-8-sig` for user-authored inputs (CSV, JSONL, specs), `utf-8` for writes; subprocess captures pin `encoding="utf-8", errors="replace"`. Unix-only imports (`fcntl`, `pwd`, …) need `try/except ImportError` at the import line.
- Header-invariant logic ("never emit both X and Y") must use `requests`' `CaseInsensitiveDict`, not a plain dict.
- Never `assert` for a runtime invariant in shipped code — stripped under `python -O`; raise `D365Error` even for logically-unreachable checks.
- `@odata.bind` navigation-property names must match `$metadata` casing — system entities (sdkmessage*, solution, …) use lowercase logical names; flag guessed PascalCase.
- `CLIContext.emit(meta=...)` renders in HUMAN mode too — JSON-only meta fields must be gated on `ctx.json_mode`.
- When serializing Click options, include `opt.secondary_opts` (the `--no-*` forms), not just `opt.opts`.
- Docstrings are Google style (`Args:`/`Returns:`/`Raises:`); no coverage quota — don't request docstrings on self-explanatory helpers.
- Every new/changed command or flag ships docs in the same PR (`docs/how-to/`, `docs/reference/cli.md`, README, `crm/skills/`). Never hand-edit CHANGELOG.md — python-semantic-release generates it. New lazily-loaded command groups go into `crm.spec` hiddenimports.
- Repo is public: flag real org names, hostnames, or real-looking GUIDs in tests/docs (placeholders: `Contoso` / `internalcrm.contoso.local`).

### Known non-issues — do not flag

- `D365Backend` retries only 502/503/504 (+429) for idempotent methods, NOT all 5xx; tests using a bare 500 to exercise the no-retry path are intentional.
- Click is pinned ≥8.2: `CliRunner` has no `mix_stderr` parameter; stdout and stderr are always separate streams.
- `# pyright: basic` in `crm/commands/*` and test files is permanent by design — only `crm/core/*` and `crm/utils/d365_backend.py` are strict; don't suggest tightening the commands layer.
- `dict[str, Any]` at the raw OData response seam is deliberate — no `TypedDict` casts over external JSON; reserve `TypedDict` for CLI-constructed structures.
- OAuth token acquisition raises `D365Error` during `session.get()` (not a requests exception); callers catching `D365Error` on raw session calls are correct, not dead code.

Only comment when you are confident the issue is real. Skip stylistic nits already governed by pyright or existing formatting.
