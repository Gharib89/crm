# Copilot instructions — crm

Python CLI for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) and Dataverse online (OAuth) — over the Dataverse Web API (OData v4). `crm/core/*` holds the Web API logic (pyright strict); `crm/commands/*` are thin Click wrappers; credentials come only from saved profiles (`crm profile add`) or `--password`, never env vars.

## Code review priorities

Focus on correctness, error handling, and contract violations. CI already runs pytest, pyright (strict on `crm/core/*` and `crm/utils/d365_backend.py`), and `mkdocs build --strict` — do not flag issues those tools catch.

### House rules to enforce

- Command layer: wrap file read/write in `try/except OSError` and return a clean error envelope; mutually exclusive flags must `raise click.UsageError` (exit 2), never `ctx.emit(False)` (exit 1); validate untrusted input before calling `ctx.backend()`.
- Dry-run mutators must return a `{_dry_run, would_*}` preview, never the bare success key (`deleted: true` etc.) — `backend.request` short-circuits ALL methods including pre-flight GETs in dry-run, so existence checks silently no-op.
- Text file I/O must pass `encoding="utf-8"` (Windows defaults to cp1252). Unix-only imports (`fcntl`, `pwd`, `termios`, …) need `try/except ImportError` at the import line.
- Logic enforcing header invariants ("never emit both X and Y") must use `requests`' `CaseInsensitiveDict`, not a plain dict.
- `@odata.bind` navigation-property names must match `$metadata` casing — system entities (sdkmessage*, solution, …) use lowercase logical names; flag guessed PascalCase.
- `CLIContext.emit(meta=...)` renders in HUMAN mode too — JSON-only meta fields must be gated on `ctx.json_mode`.
- When serializing Click options, include `opt.secondary_opts` (the `--no-*` forms), not just `opt.opts`.
- Every new/changed command or flag ships docs in the same PR: `docs/how-to/<group>.md`, `docs/reference/cli.md`, README.md if user-facing, and `crm/skills/` if workflow-visible. Never hand-edit CHANGELOG.md — python-semantic-release generates it.
- Repo is public and must stay generic: flag real org names, internal hostnames, or real-looking GUIDs in tests/docs (placeholders are `Contoso` / `internalcrm.contoso.local`).
- New lazily-loaded command groups must be added to `crm.spec` hiddenimports (PyInstaller).

### Known non-issues — do not flag

- `D365Backend` retries only 502/503/504 (+429) for idempotent methods, NOT all 5xx (`_is_response_retryable`); tests that use a bare 500 to exercise the no-retry path are intentional and fast.
- Click is pinned ≥8.2: `CliRunner` has no `mix_stderr` parameter; stdout and stderr are always separate streams.
- `# pyright: basic` headers in test files are intentional — only `crm/core/*` and `crm/utils/d365_backend.py` are strict.
- OAuth token acquisition raises `D365Error` during `session.get()` (not a requests exception); callers catching `D365Error` on raw session calls are correct, not dead code.

Only comment when you are confident the issue is real. Skip stylistic nits already governed by pyright or existing formatting.
