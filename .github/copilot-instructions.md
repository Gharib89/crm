# Copilot instructions — crm

Python CLI for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) and Dataverse online (OAuth) — over the Dataverse Web API (OData v4). `crm/core/*` holds the Web API logic (pyright strict); `crm/commands/*` are thin Click wrappers (`# pyright: basic`, permanent); credentials come only from saved profiles or `--password`, never env vars.

Canonical standards: `docs/contributing/coding-standards.md` — this file is derived from it. On conflict the doc wins; rule changes land there first.

You review **round 1 only** (no re-requests). Focus where you catch the most: correctness and error contracts. CI already runs pytest, pyright (strict on `crm/core/*` and `crm/utils/d365_backend.py`), ruff, and `mkdocs build --strict` — never flag what those tools catch.

## Environment — modern by design, don't flag as portability bugs

- **Python floor is 3.13** (ADR 0026). PEP 695 type aliases and generics (`type X = …`, `def f[T](…)`), `match`, `X | None` unions, and `Generator[T]` with the send/return params omitted are all correct — not compatibility problems.
- **ruff enforces `B905`**: every `zip()` already passes `strict=`; don't ask for it.
- `# pyright: basic` in `crm/commands/*` and test files is permanent by design — only `crm/core/*` and `crm/utils/d365_backend.py` are strict. Don't suggest tightening the commands layer.

## House rules — correctness & error contracts

- Command layer: wrap file read/write in `try/except OSError` and return a clean error envelope; mutually exclusive flags must `raise click.UsageError` (exit 2), never `ctx.emit(False)` (exit 1); validate untrusted input before calling `ctx.backend()`. Backend failures funnel through `d365_errors(ctx)`, never a hand-built envelope.
- Exit codes (ADR 0001): 0 success, 1 operational failure, 2 usage error — failure-class detail lives in the envelope, not new exit codes.
- Output contract (ADR 0008): `data` is curated and CLI-owned, never a raw Web API passthrough; OData paging keys move to `meta`; `_entity_id` is the one normalized GUID key.
- Dry-run mutators must return a `{_dry_run, would_*}` preview, never the bare success key. Reads-execute rule: GETs still run live under `--dry-run`.
- Never `assert` for a runtime invariant in shipped code — stripped under `python -O`; raise `D365Error` even for logically-unreachable checks.
- Header-invariant logic ("never emit both X and Y") must use `requests`' `CaseInsensitiveDict`, not a plain dict.
- `@odata.bind` navigation-property names must match `$metadata` casing — system entities (sdkmessage*, solution, …) use lowercase logical names; flag guessed PascalCase.
- `CLIContext.emit(meta=...)` renders in HUMAN mode too — JSON-only meta fields must be gated on `ctx.json_mode`.
- Repo is public: flag real org names, hostnames, or real-looking GUIDs in tests/docs (placeholders: `Contoso` / `internalcrm.contoso.local`).

## Known non-issues — do not flag

- `D365Backend` retries only 502/503/504 (+429) for idempotent methods, NOT all 5xx; a bare 500 in a no-retry test is intentional.
- Click is pinned ≥8.2: `CliRunner` has no `mix_stderr`; stdout and stderr are always separate streams.
- `dict[str, Any]` at the raw OData response seam is deliberate — no `TypedDict` casts over external JSON; reserve `TypedDict` for CLI-constructed structures.
- OAuth token acquisition raises `D365Error` during `session.get()` (not a requests exception); callers catching `D365Error` on raw session calls are correct.

Only comment when you are confident the issue is real. Skip stylistic nits governed by ruff, pyright, or existing formatting.
