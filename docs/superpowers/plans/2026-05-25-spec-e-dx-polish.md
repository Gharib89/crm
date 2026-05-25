# Spec E — DX Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship eight DX improvements in a single PR — split `crm/cli.py` into focused command modules, add structured logging, expose Kerberos auth, add `crm init`, three new commands, and lazy REPL tab completion.

**Architecture:** Refactor `cli.py` into a `crm/commands/` package (one Click group per module). Wire stdlib `logging` for HTTP transcripts (text + json-line). `ConnectionProfile` gains `auth_scheme`; `D365Backend._make_auth()` switches between `requests_ntlm` and `requests_negotiate_sspi`. `crm init` writes templates/profiles. REPL gains an in-memory `MetadataCache` + `readline` completer.

**Tech Stack:** Python 3.9+, Click ≥8.0, requests, requests_ntlm, requests_negotiate_sspi (optional extra), prompt_toolkit, stdlib `logging`, `readline`, pytest + requests_mock.

**Spec:** `docs/superpowers/specs/2026-05-25-spec-e-dx-polish-design.md`

**Branch:** `feat/spec-e-dx-polish`

---

## Notes for the engineer

- The current `crm/cli.py` is 2098 lines. The split (Task 1) is purely mechanical; behavior must not change. Run the entire test suite after the split — every existing test must still pass.
- Test infra: pytest + `requests_mock`. No live D365 needed. See `crm/tests/test_admin_headers.py` for the canonical fixture pattern.
- `crm/__init__.py` currently has `__version__ = "0.4.0"` but `setup.py` has `version="0.5.0"`. This drift is real and should be fixed by Task 14 (bump both to `0.6.0`).
- Use TDD where it fits: behavior tests first, refactor (Task 1) is the exception (only existing tests must pass).
- Commit after each task. Conventional Commits format: `feat:`, `refactor:`, `test:`, `chore:`.
- Working directory: `D:\projects\crm`.
- PowerShell shell. Use `pytest` directly (project's `.venv` is on PATH).

---

## File Structure

**New files (created in Task 1):**

```
crm/commands/__init__.py       # Re-exports each command group
crm/commands/connection.py     # connection group (~7 commands)
crm/commands/entity.py         # entity group (~10 commands)
crm/commands/query.py          # query group (4 commands; +query count in Task 6)
crm/commands/metadata.py       # metadata group (~14 commands; +list-actions/functions in Task 7)
crm/commands/solution.py       # solution group (~10 commands)
crm/commands/data.py           # data group (data export)
crm/commands/action.py         # action group (function / invoke)
crm/commands/async_ops.py      # async group (list/get/cancel)
crm/commands/workflow.py       # workflow group
crm/commands/skill.py          # skill group (install/uninstall/path)
crm/commands/session.py        # session group (info/clear/history)
crm/commands/batch.py          # top-level `batch` + `service-document` commands
crm/commands/init.py           # `crm init` (Task 5)
crm/commands/repl.py           # REPL + MetadataCache + readline completer (Task 8)
crm/commands/_helpers.py       # shared helpers: _admin_header_options, _admin_kwargs, _confirm_destructive, _no_retry_scope, _load_payload, _touch_session, _odata_literal, _emit_query_result, _infer_columns, _handle_d365_error
```

**New core helpers:**

```
crm/core/logging_setup.py      # Task 2: CrmLogHandler + setup_logging()
crm/core/metadata.py           # Task 7: append list_entity_names(), list_actions(), list_functions()
crm/core/query.py              # Task 6: append total_record_count()
```

**New tests:**

```
crm/tests/test_logging.py
crm/tests/test_auth_scheme.py
crm/tests/test_crm_init.py
crm/tests/test_query_count.py
crm/tests/test_metadata_actions_functions.py
crm/tests/test_metadata_cache.py
```

**Modified files:**

```
crm/cli.py                     # Slim root group + CLIContext only (Task 1, +Task 2/3 wiring)
crm/utils/d365_backend.py      # auth_scheme + _make_auth() + logger hook (Task 3, Task 2)
crm/utils/d365_types.py        # (untouched)
crm/__init__.py                # version bump (Task 14)
setup.py                       # extras_require[kerberos], version bump (Task 3, Task 14)
CHANGELOG.md                   # 0.6.0 entry (Task 14)
```

---

## Task 1: Split `cli.py` into `crm/commands/` package (mechanical refactor)

**Goal:** Zero behavior change. All existing tests pass unchanged.

**Files:**
- Create: `crm/commands/__init__.py`, `crm/commands/_helpers.py`, `crm/commands/{connection,entity,query,metadata,solution,data,action,async_ops,workflow,skill,session,batch}.py`, `crm/commands/repl.py`
- Modify: `crm/cli.py` (reduce to root group + `CLIContext` + imports + wiring)

- [ ] **Step 1.1: Capture pre-refactor test baseline**

Run the entire suite to confirm the starting state:

```powershell
pytest crm/tests -q
```

Expected: all tests pass (e.g., `XX passed in Ys`). Record the exact pass count — Task 1 must end with the same count.

- [ ] **Step 1.2: Create `crm/commands/__init__.py`**

```python
"""Click command groups for the crm CLI.

Each submodule defines one Click group (or top-level command) and is added
to the root `cli` group by `crm.cli`. Splitting by group keeps any single
file in this package focused on one slice of the surface.
"""
from __future__ import annotations
```

- [ ] **Step 1.3: Create `crm/commands/_helpers.py`**

Move these helpers verbatim from `crm/cli.py`: `_sanitize`, `_short_repr`, `_handle_d365_error`, `_confirm_destructive`, `_admin_header_options`, `_admin_kwargs`, `_load_payload`, `_touch_session`, `_odata_literal`, `_emit_query_result`, `_infer_columns`, `_no_retry_scope`, `_EXPORT_SETTING_KEYS`, `_ASYNC_STATE_NAMES`, `_resolve_async_state`, `_CASCADE`, `_MENU`, `_REQUIRED`.

The `CLIContext` class + `pass_ctx` stays in `crm/cli.py` (root module owns context). Import `CLIContext` and `pass_ctx` from `crm.cli` where helpers need them — but `_helpers.py` only needs `CLIContext` as a type hint for `_handle_d365_error` / `_emit_query_result` / `_touch_session`, so use `from __future__ import annotations` + a `TYPE_CHECKING` import:

```python
"""Shared helpers used across crm.commands.*."""
# pyright: basic
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import click

from crm.core import session as session_mod
from crm.utils.d365_backend import D365Error

if TYPE_CHECKING:
    from crm.cli import CLIContext


def _sanitize(obj: Any) -> Any:
    ...  # body unchanged

# ... (move all helpers listed above verbatim, fixing imports as needed)
```

Note: `_emit_query_result` references `ctx.skin`, `ctx.emit`, `ctx.json_mode` — all attributes on `CLIContext`. Type hint as `"CLIContext"` (string forward-ref).

- [ ] **Step 1.4: Create one module per command group**

For each group (`connection`, `entity`, `query`, `metadata`, `solution`, `data`, `action`, `async_ops`, `workflow`, `skill`, `session`, `batch`), create the module. Pattern (example for `connection`):

```python
# crm/commands/connection.py
"""`crm connection` command group."""
# pyright: basic
from __future__ import annotations

import os
from typing import Any

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _handle_d365_error
from crm.core import connection as conn_mod, session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@click.group("connection")
def connection_group():
    """Manage server connection profiles and authentication."""


@connection_group.command("connect")
@click.option("--url", required=True, help="...")
# ... (move command body verbatim from cli.py)
def connection_connect(ctx: CLIContext, ...):
    ...
```

Each module exports its top-level group (e.g., `connection_group`, `entity_group`, etc.). For modules with multiple top-level commands (e.g., `batch.py` holds both `cli batch` and `cli service-document`), export each as a module-level function and add separately in `cli.py`.

**Naming convention:** rename the inline-decorated group functions to `<group>_group` (avoid colliding with builtin names like `entity`, `query`, `metadata` when imported into `cli.py`). Example: the old `@cli.group()  def connection():` becomes `@click.group("connection")  def connection_group():` in `crm/commands/connection.py`. The command function names inside the module stay the same (`connection_connect`, `connection_status`, etc.).

For groups already using string-named decorators (e.g., `@cli.group("async")  def async_group():`, `@cli.group("skill")  def skill_group():`), keep the existing names.

**Top-level commands** (`cli.command("service-document")`, `cli.command("batch")`) — define them at module level decorated with `@click.command("name")` (not `@cli.command`). They'll be added to `cli` in Task 1.5.

**REPL:** move the entire `@cli.command("repl") def repl(ctx)` body + `_repl_help` into `crm/commands/repl.py`. Decorate as `@click.command("repl")` in the new file. Tab-completion code is NOT added in Task 1 — that's Task 8.

- [ ] **Step 1.5: Slim down `crm/cli.py` to wire everything up**

Replace the body of `crm/cli.py` with:

```python
"""crm — Click-based CLI + REPL for Dynamics 365 CE on-prem 9.x.

Entry point: `crm` (installed) or `python -m crm`.

Running with no subcommand drops into the REPL. Each command supports `--json`
for machine-readable output. `--dry-run` previews the HTTP request without
issuing it.
"""
# pyright: basic

from __future__ import annotations

from typing import Any

import click

from crm import __version__
from crm.core import connection as conn_mod
from crm.utils.d365_backend import D365Backend
from crm.utils.repl_skin import ReplSkin


class CLIContext:
    """Per-invocation state shared across subcommands."""

    def __init__(self):
        self.json_mode: bool = False
        self.dry_run: bool = False
        self.profile_name: str | None = None
        self.password: str | None = None
        self.session_name: str = "default"
        self._backend: D365Backend | None = None
        self._backend_key: tuple[str | None, str | None, bool] | None = None
        self.skin: ReplSkin = ReplSkin("d365", version=__version__)

    def emit(self, ok: bool, data: Any = None, *, error: str | None = None,
             meta: dict | None = None, table: dict | None = None) -> None:
        # ... unchanged body — keep verbatim from current cli.py
        ...

    def backend(self) -> D365Backend:
        # ... unchanged
        ...

    def invalidate_backend(self) -> None:
        # ... unchanged
        ...


pass_ctx = click.make_pass_decorator(CLIContext, ensure=True)


@click.group(invoke_without_command=True,
             context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--dry-run", is_flag=True, help="Preview HTTP request without issuing it.")
@click.option("--profile", "profile_name", help="Connection profile name (from ~/.crm/profiles).")
@click.option("--password", help="Override password (otherwise read from D365_PASSWORD).")
@click.option("--session", "session_name", default="default", help="Session name.")
@click.version_option(__version__, prog_name="crm")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, dry_run: bool,
        profile_name: str | None, password: str | None, session_name: str):
    """Stateful CLI for Dynamics 365 CE on-prem 9.x (Web API)."""
    cli_ctx = ctx.ensure_object(CLIContext)
    cli_ctx.json_mode = json_mode
    cli_ctx.dry_run = dry_run
    if profile_name is not None:
        cli_ctx.profile_name = profile_name
    if password is not None:
        cli_ctx.password = password
    cli_ctx.session_name = session_name

    if ctx.invoked_subcommand is None:
        from crm.commands.repl import repl
        ctx.invoke(repl)


# ── Wire up all command groups ─────────────────────────────────────────
from crm.commands.connection import connection_group  # noqa: E402
from crm.commands.entity import entity_group  # noqa: E402
from crm.commands.query import query_group  # noqa: E402
from crm.commands.metadata import metadata_group  # noqa: E402
from crm.commands.solution import solution_group  # noqa: E402
from crm.commands.data import data_group  # noqa: E402
from crm.commands.action import action_group  # noqa: E402
from crm.commands.async_ops import async_group  # noqa: E402
from crm.commands.workflow import workflow_group  # noqa: E402
from crm.commands.skill import skill_group  # noqa: E402
from crm.commands.session import session_group  # noqa: E402
from crm.commands.batch import batch_cmd, service_document_cmd  # noqa: E402
from crm.commands.repl import repl  # noqa: E402

cli.add_command(connection_group)
cli.add_command(entity_group)
cli.add_command(query_group)
cli.add_command(metadata_group)
cli.add_command(solution_group)
cli.add_command(data_group)
cli.add_command(action_group)
cli.add_command(async_group)
cli.add_command(workflow_group)
cli.add_command(skill_group)
cli.add_command(session_group)
cli.add_command(batch_cmd)
cli.add_command(service_document_cmd)
cli.add_command(repl)


if __name__ == "__main__":
    cli()
```

Keep `CLIContext.emit`, `CLIContext.backend`, `CLIContext.invalidate_backend` bodies **verbatim** from the original `cli.py` (lines 62–127). Do not change their behavior.

- [ ] **Step 1.6: Verify imports and circular-dependency check**

The pattern is:
- `crm/cli.py` imports `CLIContext` from itself (defined there), then imports command groups (which import back from `crm.cli` for `CLIContext` + `pass_ctx`).
- This is fine because the command-group imports happen **after** `CLIContext` and `pass_ctx` are defined.
- `crm/commands/_helpers.py` uses `TYPE_CHECKING` to avoid importing `CLIContext` at runtime.

If pyright/mypy flags circular imports, restructure to: define `CLIContext` in `crm/cli.py`, command modules use `from __future__ import annotations` + string forward-ref `"CLIContext"` in type hints and `from crm.cli import CLIContext, pass_ctx` inside function bodies (lazy import). Try the simpler approach first.

- [ ] **Step 1.7: Run the full test suite**

```powershell
pytest crm/tests -q
```

Expected: identical pass count to Step 1.1. Zero failures, zero new skips.

- [ ] **Step 1.8: Smoke-test the binary**

```powershell
python -m crm --help
python -m crm entity --help
python -m crm metadata --help
python -m crm solution --help
python -m crm async --help
```

Expected: each prints the help block for its group with no errors. Command names and help text unchanged from before the refactor.

- [ ] **Step 1.9: Commit**

```powershell
git add crm/cli.py crm/commands/
git commit -m "refactor: split cli.py into crm/commands/ package

Zero behavior change. Each Click group moves to its own module
under crm/commands/. cli.py keeps only the root group, CLIContext,
and wiring. Shared helpers move to crm/commands/_helpers.py.
Pre-work for Spec E."
```

---

## Task 2: Add `--log-level` / `--log-format` / `--verbose`

**Files:**
- Create: `crm/core/logging_setup.py`
- Create: `crm/tests/test_logging.py`
- Modify: `crm/cli.py` (add root-group options, call setup)
- Modify: `crm/utils/d365_backend.py` (add logger calls around request/response)

- [ ] **Step 2.1: Write the failing test (text formatter)**

`crm/tests/test_logging.py`:

```python
"""Unit tests for Spec E logging setup."""
# pyright: basic
from __future__ import annotations

import json
import logging

import pytest

from crm.core.logging_setup import CrmLogHandler, setup_logging


@pytest.fixture(autouse=True)
def _reset_crm_logger():
    """Strip handlers off the 'crm' logger between tests."""
    logger = logging.getLogger("crm")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    logger.handlers.clear()
    yield
    logger.handlers.clear()
    for h in saved_handlers:
        logger.addHandler(h)
    logger.setLevel(saved_level)


class TestTextFormat:
    def test_request_log_text_format(self, capsys):
        setup_logging(level="debug", fmt="text")
        logging.getLogger("crm.http").debug(
            "request", extra={"event": "request", "method": "GET",
                              "url": "https://crm/api/data/v9.2/accounts"}
        )
        err = capsys.readouterr().err
        assert "[DEBUG]" in err
        assert "GET" in err
        assert "https://crm/api/data/v9.2/accounts" in err

    def test_response_log_text_format_includes_ms(self, capsys):
        setup_logging(level="debug", fmt="text")
        logging.getLogger("crm.http").debug(
            "response", extra={"event": "response", "status": 200, "ms": 142}
        )
        err = capsys.readouterr().err
        assert "200" in err
        assert "142" in err
```

- [ ] **Step 2.2: Run the test to verify it fails**

```powershell
pytest crm/tests/test_logging.py::TestTextFormat -v
```

Expected: `ModuleNotFoundError: No module named 'crm.core.logging_setup'`.

- [ ] **Step 2.3: Create `crm/core/logging_setup.py`**

```python
"""crm logging setup — wires the `crm` logger tree to stderr.

Two output formats:
- text: `[LEVEL] <event/message> [k=v ...]`
- json-line: one JSON object per record on a single line

The handler reads structured fields from `LogRecord.__dict__` (set via the
`extra=` kwarg on logger calls) so callers don't have to format their own
strings.
"""
# pyright: basic
from __future__ import annotations

import json
import logging
import sys
from typing import Literal

LogFormat = Literal["text", "json-line"]
LogLevel = Literal["debug", "info", "warning", "error"]

_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_STRUCT_KEYS = ("event", "method", "url", "status", "ms")


class CrmLogHandler(logging.StreamHandler):
    """StreamHandler that emits either text or json-line per `fmt`."""

    def __init__(self, fmt: LogFormat = "text"):
        super().__init__(stream=sys.stderr)
        self.fmt: LogFormat = fmt

    def format(self, record: logging.LogRecord) -> str:
        struct = {k: getattr(record, k) for k in _STRUCT_KEYS
                  if hasattr(record, k)}
        if self.fmt == "json-line":
            payload = {"level": record.levelname.lower(), **struct}
            if not struct:
                payload["message"] = record.getMessage()
            return json.dumps(payload, default=str)
        # text
        bits: list[str] = [f"[{record.levelname}]"]
        if "event" in struct:
            bits.append(str(struct["event"]))
        if "method" in struct:
            bits.append(str(struct["method"]))
        if "url" in struct:
            bits.append(str(struct["url"]))
        if "status" in struct:
            bits.append(str(struct["status"]))
        if "ms" in struct:
            bits.append(f"({struct['ms']}ms)")
        if not struct:
            bits.append(record.getMessage())
        return " ".join(bits)


def setup_logging(level: LogLevel = "warning", fmt: LogFormat = "text") -> None:
    """Configure the `crm` logger tree.

    Idempotent: previous CrmLogHandlers on the `crm` logger are removed before
    a new one is attached, so repeated calls (e.g. across REPL lines) don't
    stack handlers.
    """
    logger = logging.getLogger("crm")
    logger.setLevel(_LEVEL_MAP[level])
    for h in list(logger.handlers):
        if isinstance(h, CrmLogHandler):
            logger.removeHandler(h)
    handler = CrmLogHandler(fmt=fmt)
    handler.setLevel(_LEVEL_MAP[level])
    logger.addHandler(handler)
    logger.propagate = False
```

- [ ] **Step 2.4: Run the text-format tests**

```powershell
pytest crm/tests/test_logging.py::TestTextFormat -v
```

Expected: both tests PASS.

- [ ] **Step 2.5: Write the failing tests (json-line formatter)**

Append to `crm/tests/test_logging.py`:

```python
class TestJsonLineFormat:
    def test_request_log_emits_single_json_line(self, capsys):
        setup_logging(level="debug", fmt="json-line")
        logging.getLogger("crm.http").debug(
            "request", extra={"event": "request", "method": "GET",
                              "url": "https://crm/api/data/v9.2/accounts"}
        )
        err = capsys.readouterr().err.strip()
        assert err.count("\n") == 0  # exactly one line
        payload = json.loads(err)
        assert payload == {
            "level": "debug",
            "event": "request",
            "method": "GET",
            "url": "https://crm/api/data/v9.2/accounts",
        }

    def test_response_log_includes_status_and_ms(self, capsys):
        setup_logging(level="debug", fmt="json-line")
        logging.getLogger("crm.http").debug(
            "response", extra={"event": "response", "status": 200, "ms": 142}
        )
        payload = json.loads(capsys.readouterr().err.strip())
        assert payload["status"] == 200
        assert payload["ms"] == 142


class TestSetupIdempotency:
    def test_repeated_setup_does_not_stack_handlers(self):
        setup_logging(level="debug", fmt="text")
        setup_logging(level="warning", fmt="json-line")
        handlers = [h for h in logging.getLogger("crm").handlers
                    if isinstance(h, CrmLogHandler)]
        assert len(handlers) == 1
        assert handlers[0].fmt == "json-line"

    def test_level_filters_below_threshold(self, capsys):
        setup_logging(level="warning", fmt="text")
        logging.getLogger("crm.http").debug("request",
            extra={"event": "request", "method": "GET", "url": "..."})
        assert capsys.readouterr().err == ""
```

- [ ] **Step 2.6: Run the new tests**

```powershell
pytest crm/tests/test_logging.py -v
```

Expected: all PASS.

- [ ] **Step 2.7: Wire the CLI options**

In `crm/cli.py`, add two new options to the root group (between `--password` and `--session`):

```python
@click.option("--log-level",
              type=click.Choice(["debug", "info", "warning", "error"]),
              default=None,
              help="Log level (env: CRM_LOG_LEVEL). Default: warning.")
@click.option("--verbose", "verbose", is_flag=True,
              help="Alias for --log-level debug.")
@click.option("--log-format",
              type=click.Choice(["text", "json-line"]),
              default=None,
              help="Log output format (env: CRM_LOG_FORMAT). Default: text.")
```

Add `log_level`, `verbose`, `log_format` parameters to the `cli()` function. Inside the body, before the `invoked_subcommand` check, resolve and apply:

```python
import os
from crm.core.logging_setup import setup_logging

effective_level = log_level or os.environ.get("CRM_LOG_LEVEL") or "warning"
if verbose:
    effective_level = "debug"
effective_fmt = log_format or os.environ.get("CRM_LOG_FORMAT") or "text"
setup_logging(level=effective_level, fmt=effective_fmt)  # type: ignore[arg-type]
```

Put the import at the module top, not inside the function. Use `cast`/`# type: ignore` only if pyright complains about the `Literal` narrowing.

- [ ] **Step 2.8: Add request/response logger calls to backend**

In `crm/utils/d365_backend.py`, near the top of the module add:

```python
import logging as _logging

_http_logger = _logging.getLogger("crm.http")
```

Inside `D365Backend.request`, right before each `self._session.request(...)` call, log a request line:

```python
_http_logger.debug("request", extra={
    "event": "request", "method": method, "url": url,
})
```

After the response is received (after `resp = self._session.request(...)`, before retry-handling logic), log a response line:

```python
elapsed_ms = int((resp.elapsed.total_seconds() if resp.elapsed else 0) * 1000)
_http_logger.debug("response", extra={
    "event": "response", "status": resp.status_code, "ms": elapsed_ms,
})
```

Place the response log immediately after `resp = self._session.request(...)` returns — before `retryable = _is_response_retryable(...)`. This ensures every response (including retried 429s) gets logged.

- [ ] **Step 2.9: Add an end-to-end log test**

Append to `crm/tests/test_logging.py`:

```python
class TestBackendIntegration:
    def test_backend_request_emits_request_response_logs(self, capsys):
        import requests_mock
        from crm.utils.d365_backend import ConnectionProfile, D365Backend

        setup_logging(level="debug", fmt="text")
        profile = ConnectionProfile(
            name="t", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", verify_ssl=False,
        )
        backend = D365Backend(profile, password="pw")
        with requests_mock.Mocker() as m:
            m.get(
                "https://crm.contoso.local/contoso/api/data/v9.2/WhoAmI",
                json={"UserId": "00000000-0000-0000-0000-000000000000"},
            )
            backend.get("WhoAmI")

        err = capsys.readouterr().err
        assert "request" in err
        assert "GET" in err
        assert "response" in err
        assert "200" in err
```

- [ ] **Step 2.10: Run full test suite**

```powershell
pytest crm/tests -q
```

Expected: all pass, no regressions.

- [ ] **Step 2.11: Smoke-test the CLI**

```powershell
$env:CRM_LOG_LEVEL = "debug"
python -m crm --help 2>&1 | Select-String -Pattern "Stateful CLI"
Remove-Item Env:CRM_LOG_LEVEL
python -m crm --verbose --log-format json-line --help
```

Expected: both succeed. (`--help` short-circuits before any HTTP, so no log lines yet — that's fine; this is just checking the flag parses.)

- [ ] **Step 2.12: Commit**

```powershell
git add crm/cli.py crm/core/logging_setup.py crm/tests/test_logging.py crm/utils/d365_backend.py
git commit -m "feat: add --log-level / --log-format / --verbose

Stdlib logging routed through CrmLogHandler with text and json-line
formats. d365_backend.D365Backend.request emits request/response
debug records on the 'crm.http' logger. CRM_LOG_LEVEL and
CRM_LOG_FORMAT env vars supported. Spec E."
```

---

## Task 3: Add `--auth-scheme` + Kerberos via `requests_negotiate_sspi`

**Files:**
- Modify: `crm/utils/d365_backend.py` (add `auth_scheme` field, `_make_auth()`)
- Modify: `crm/cli.py` (add `--auth-scheme` root option)
- Modify: `setup.py` (extras_require[kerberos])
- Create: `crm/tests/test_auth_scheme.py`

- [ ] **Step 3.1: Write failing tests**

`crm/tests/test_auth_scheme.py`:

```python
"""Unit tests for --auth-scheme + Kerberos support."""
# pyright: basic
from __future__ import annotations

import sys

import pytest

from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


def _profile(scheme: str = "ntlm") -> ConnectionProfile:
    return ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice",
        verify_ssl=False, auth_scheme=scheme,
    )


class TestProfileField:
    def test_default_auth_scheme_is_ntlm(self):
        p = ConnectionProfile(
            name="t", url="https://crm/test", domain="D", username="u",
        )
        assert p.auth_scheme == "ntlm"

    def test_profile_to_dict_includes_auth_scheme(self):
        p = _profile("kerberos")
        d = p.to_dict()
        assert d["auth_scheme"] == "kerberos"

    def test_profile_from_dict_defaults_to_ntlm_when_missing(self):
        p = ConnectionProfile.from_dict({
            "name": "t", "url": "https://crm/test",
            "domain": "D", "username": "u",
        })
        assert p.auth_scheme == "ntlm"


class TestAuthSelection:
    def test_ntlm_scheme_uses_ntlm_auth(self):
        from requests_ntlm import HttpNtlmAuth
        b = D365Backend(_profile("ntlm"), password="pw")
        assert isinstance(b._session.auth, HttpNtlmAuth)

    def test_kerberos_scheme_raises_when_package_missing(self, monkeypatch):
        # Force-import-fail requests_negotiate_sspi
        monkeypatch.setitem(sys.modules, "requests_negotiate_sspi", None)
        with pytest.raises(D365Error, match="requests_negotiate_sspi"):
            D365Backend(_profile("kerberos"), password="pw")

    def test_negotiate_scheme_raises_when_package_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "requests_negotiate_sspi", None)
        with pytest.raises(D365Error, match="requests_negotiate_sspi"):
            D365Backend(_profile("negotiate"), password="pw")

    def test_unknown_scheme_raises(self):
        with pytest.raises(D365Error, match="auth_scheme"):
            D365Backend(_profile("oauth2"), password="pw")
```

- [ ] **Step 3.2: Run tests, verify they fail**

```powershell
pytest crm/tests/test_auth_scheme.py -v
```

Expected: `ConnectionProfile.__init__() got an unexpected keyword argument 'auth_scheme'`.

- [ ] **Step 3.3: Add `auth_scheme` field to `ConnectionProfile`**

In `crm/utils/d365_backend.py`, modify the `ConnectionProfile` dataclass (around line 50–67) — add field after `verify_ssl`:

```python
    api_version: str = "v9.2"
    verify_ssl: bool = True
    auth_scheme: str = "ntlm"      # ntlm | kerberos | negotiate
    timeout: int = 120
```

In `__post_init__`, validate the scheme:

```python
        if self.auth_scheme not in ("ntlm", "kerberos", "negotiate"):
            raise D365Error(
                f"ConnectionProfile.auth_scheme must be ntlm|kerberos|negotiate, "
                f"got {self.auth_scheme!r}"
            )
```

In `to_dict`, add `"auth_scheme": self.auth_scheme`. In `from_dict`, add `auth_scheme=d.get("auth_scheme", "ntlm")`.

- [ ] **Step 3.4: Replace NTLM-only auth wiring with `_make_auth()`**

In `crm/utils/d365_backend.py`, in `D365Backend.__init__`, replace the existing NTLM-specific block (around lines 155–171):

```python
        if HttpNtlmAuth is None:
            raise D365Error(
                "requests_ntlm is not installed. Install with: pip install requests_ntlm"
            )
        if not profile.url:
            raise D365Error("Profile is missing the server URL.")
        if not profile.username:
            raise D365Error("Profile is missing the username.")

        self.profile = profile
        self.dry_run = dry_run
        self._session: requests.Session = requests.Session()
        user_principal = (
            f"{profile.domain}\\{profile.username}" if profile.domain else profile.username
        )
        self._session.auth = HttpNtlmAuth(user_principal, password)
```

With:

```python
        if not profile.url:
            raise D365Error("Profile is missing the server URL.")
        if not profile.username:
            raise D365Error("Profile is missing the username.")

        self.profile = profile
        self.dry_run = dry_run
        self._session: requests.Session = requests.Session()
        self._session.auth = self._make_auth(password)
```

Then add the `_make_auth` method to the class (above `url_for` is a fine spot):

```python
    def _make_auth(self, password: str):
        """Pick the auth adapter based on profile.auth_scheme."""
        scheme = self.profile.auth_scheme
        if scheme == "ntlm":
            if HttpNtlmAuth is None:
                raise D365Error(
                    "requests_ntlm is not installed. "
                    "Install with: pip install requests_ntlm"
                )
            user_principal = (
                f"{self.profile.domain}\\{self.profile.username}"
                if self.profile.domain else self.profile.username
            )
            return HttpNtlmAuth(user_principal, password)
        if scheme in ("kerberos", "negotiate"):
            try:
                from requests_negotiate_sspi import HttpNegotiateAuth
            except ImportError as exc:
                raise D365Error(
                    "Kerberos/Negotiate auth requires 'requests_negotiate_sspi'. "
                    "Install it: pip install crm[kerberos]"
                ) from exc
            return HttpNegotiateAuth()
        raise D365Error(
            f"Unknown auth_scheme {scheme!r}; expected ntlm|kerberos|negotiate"
        )
```

Note: the `auth_scheme` validation in `__post_init__` (Step 3.3) catches unknown schemes at profile construction, but the `raise` in `_make_auth` defends against `D365Backend` being constructed with a manually-mutated profile.

- [ ] **Step 3.5: Run auth tests**

```powershell
pytest crm/tests/test_auth_scheme.py -v
```

Expected: all PASS.

- [ ] **Step 3.6: Add `--auth-scheme` to root CLI group**

In `crm/cli.py`, add another root-group option below `--log-format`:

```python
@click.option("--auth-scheme",
              type=click.Choice(["ntlm", "kerberos", "negotiate"]),
              default=None,
              help="HTTP auth scheme (env: CRM_AUTH_SCHEME). Default: ntlm.")
```

Add `auth_scheme` parameter to `cli()` and stash on `cli_ctx`:

```python
    cli_ctx.auth_scheme = (
        auth_scheme or os.environ.get("CRM_AUTH_SCHEME")
    )
```

Add the field to `CLIContext.__init__`:

```python
        self.auth_scheme: str | None = None
```

In `CLIContext.backend()`, when building the `D365Backend`, override the profile's auth_scheme if the CLI flag/env was set:

```python
    def backend(self) -> D365Backend:
        key = (self.profile_name, self.password, self.dry_run, self.auth_scheme)
        if self._backend is None or self._backend_key != key:
            resolved = conn_mod.resolve_credentials(
                profile_name=self.profile_name,
                password_override=self.password,
            )
            if self.auth_scheme is not None:
                resolved.profile.auth_scheme = self.auth_scheme
            self._backend = D365Backend(
                resolved.profile, resolved.password, dry_run=self.dry_run
            )
            self._backend_key = key
        return self._backend
```

Update the type of `self._backend_key` annotation accordingly (4-tuple now).

- [ ] **Step 3.7: Add `kerberos` extra to `setup.py`**

In `setup.py`, extend `extras_require`:

```python
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10", "pyinstaller>=6.0", "pyright>=1.1.380"],
        "kerberos": ["requests_negotiate_sspi"],
    },
```

- [ ] **Step 3.8: Run full test suite**

```powershell
pytest crm/tests -q
```

Expected: all pass. The existing `test_admin_headers.py` fixture builds `ConnectionProfile` without `auth_scheme` → defaults to `"ntlm"`. No regressions.

- [ ] **Step 3.9: Smoke-test the flag**

```powershell
python -m crm --auth-scheme kerberos --help
```

Expected: help renders, no crash. (Won't actually try Kerberos without a subcommand.)

- [ ] **Step 3.10: Commit**

```powershell
git add crm/utils/d365_backend.py crm/cli.py crm/tests/test_auth_scheme.py setup.py
git commit -m "feat: add --auth-scheme for NTLM/Kerberos selection

ConnectionProfile gains auth_scheme (default 'ntlm').
D365Backend._make_auth() picks between requests_ntlm and
requests_negotiate_sspi. crm[kerberos] extra installs the
SSPI dependency. Spec E."
```

---

## Task 4: Add `crm init` (template + interactive wizard)

**Files:**
- Create: `crm/commands/init.py`
- Modify: `crm/cli.py` (wire the new command)
- Create: `crm/tests/test_crm_init.py`

- [ ] **Step 4.1: Write the failing tests**

`crm/tests/test_crm_init.py`:

```python
"""Unit tests for `crm init`."""
# pyright: basic
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli


class TestTemplateMode:
    def test_template_writes_env_example(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--template"])
        assert result.exit_code == 0, result.output
        env_file = tmp_path / ".env.example"
        assert env_file.exists()
        content = env_file.read_text(encoding="utf-8")
        assert "CRM_URL=" in content
        assert "CRM_USERNAME=" in content
        assert "CRM_PASSWORD=" in content
        assert "CRM_AUTH_SCHEME=" in content
        assert "CRM_LOG_LEVEL=" in content

    def test_template_refuses_to_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text("existing", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--template"])
        assert result.exit_code != 0
        assert "already exists" in result.output.lower()
        # Existing file untouched
        assert (tmp_path / ".env.example").read_text(encoding="utf-8") == "existing"


class TestInteractiveWizard:
    def test_wizard_writes_profile(self, tmp_path, monkeypatch):
        # Redirect ~/.crm to tmp_path
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
        monkeypatch.setenv("HOME", str(tmp_path))         # POSIX
        runner = CliRunner()
        inputs = "\n".join([
            "https://crm.contoso.local/contoso",   # URL
            "alice",                                # username
            "pw1234",                               # password
            "CONTOSO",                              # domain
            "ntlm",                                 # auth-scheme
            "myprofile",                            # profile name
            "y",                                    # confirm
        ]) + "\n"
        result = runner.invoke(cli, ["init"], input=inputs)
        assert result.exit_code == 0, result.output
        profile_file = tmp_path / ".crm" / "profiles" / "myprofile.json"
        assert profile_file.exists()
        data = json.loads(profile_file.read_text(encoding="utf-8"))
        assert data["url"].startswith("https://crm.contoso.local")
        assert data["username"] == "alice"
        assert data["domain"] == "CONTOSO"
        assert data["auth_scheme"] == "ntlm"
```

- [ ] **Step 4.2: Run tests, verify they fail**

```powershell
pytest crm/tests/test_crm_init.py -v
```

Expected: failure — `init` command not found.

- [ ] **Step 4.3: Implement `crm/commands/init.py`**

```python
"""`crm init` — env template generator + interactive profile wizard."""
# pyright: basic
from __future__ import annotations

from pathlib import Path

import click

from crm.cli import CLIContext, pass_ctx
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile

_ENV_TEMPLATE = """\
# Dynamics 365 connection settings — copy to .env and fill in values.
CRM_URL=https://your-crm.corp/
CRM_USERNAME=DOMAIN\\\\user
CRM_PASSWORD=
CRM_DOMAIN=CORP
CRM_AUTH_SCHEME=ntlm   # ntlm | kerberos | negotiate

# Logging
CRM_LOG_LEVEL=warning  # debug | info | warning | error
CRM_LOG_FORMAT=text    # text | json-line
"""


@click.command("init")
@click.option("--template", is_flag=True,
              help="Write .env.example to the current directory and exit.")
@pass_ctx
def init_cmd(ctx: CLIContext, template: bool):
    """Bootstrap a crm workspace.

    With --template: writes .env.example to the current directory.
    Without args: interactive wizard to create a connection profile.
    """
    if template:
        _write_template(ctx)
        return
    _run_wizard(ctx)


def _write_template(ctx: CLIContext) -> None:
    dest = Path.cwd() / ".env.example"
    if dest.exists():
        ctx.emit(False, error=f"{dest} already exists; refusing to overwrite.")
        raise SystemExit(1)
    dest.write_text(_ENV_TEMPLATE, encoding="utf-8")
    ctx.emit(True, data={"written": str(dest)})


def _run_wizard(ctx: CLIContext) -> None:
    url = click.prompt("Server URL (e.g. https://crm.corp/org)")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True, default="", show_default=False)
    domain = click.prompt("AD domain (blank for UPN)", default="", show_default=False)
    auth_scheme = click.prompt(
        "Auth scheme",
        type=click.Choice(["ntlm", "kerberos", "negotiate"]),
        default="ntlm",
    )
    profile_name = click.prompt("Profile name", default="default")

    existing = profile_name in session_mod.list_profiles()
    if existing and not click.confirm(
        f"Profile {profile_name!r} already exists. Overwrite?", default=False,
    ):
        ctx.emit(False, error="aborted by user")
        raise SystemExit(1)

    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        auth_scheme=auth_scheme,
    )
    session_mod.save_profile(profile)
    ctx.emit(True, data={
        "profile": profile_name,
        "saved": True,
        "password_set": bool(password),
    })
```

Note: the wizard collects but does not persist the password (D365 profiles are secrets-free by design). Mention this in the success emit so users know to set `D365_PASSWORD` for subsequent calls.

- [ ] **Step 4.4: Wire `init_cmd` into `crm/cli.py`**

Add to the import block at the bottom:

```python
from crm.commands.init import init_cmd  # noqa: E402
```

And to the wiring block:

```python
cli.add_command(init_cmd)
```

- [ ] **Step 4.5: Run init tests**

```powershell
pytest crm/tests/test_crm_init.py -v
```

Expected: all PASS.

- [ ] **Step 4.6: Run full test suite**

```powershell
pytest crm/tests -q
```

Expected: all pass.

- [ ] **Step 4.7: Smoke-test**

```powershell
$tmp = New-Item -ItemType Directory -Path "$env:TEMP\crm-init-smoke" -Force
Push-Location $tmp
python -m crm init --template
Get-Content .env.example
Pop-Location
Remove-Item $tmp -Recurse -Force
```

Expected: prints `.env.example` content with all `CRM_*` placeholders.

- [ ] **Step 4.8: Commit**

```powershell
git add crm/commands/init.py crm/cli.py crm/tests/test_crm_init.py
git commit -m "feat: add 'crm init' command

--template writes .env.example with all CRM_* placeholders.
No args runs an interactive profile wizard that writes
~/.crm/profiles/<name>.json. Spec E."
```

---

## Task 5: Add `query count` (RetrieveTotalRecordCount)

**Files:**
- Modify: `crm/core/query.py` (append `total_record_count()` helper)
- Modify: `crm/commands/query.py` (add `count` subcommand)
- Create: `crm/tests/test_query_count.py`

- [ ] **Step 5.1: Write the failing test**

`crm/tests/test_query_count.py`:

```python
"""Unit tests for query count (RetrieveTotalRecordCount)."""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core.query import total_record_count
from crm.utils.d365_backend import ConnectionProfile, D365Backend


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", verify_ssl=False,
    )
    return D365Backend(profile, password="pw")


class TestCoreHelper:
    def test_total_record_count_returns_count(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                "https://crm.contoso.local/contoso/api/data/v9.2/"
                "RetrieveTotalRecordCount(EntityNames=['account'])",
                json={"EntityRecordCountCollection": {
                    "Keys": ["account"], "Values": [42]
                }},
            )
            n = total_record_count(backend, "account")
        assert n == 42

    def test_total_record_count_raises_for_empty_entity(self, backend):
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            total_record_count(backend, "")


class TestCLI:
    def test_cli_count_json_envelope(self, monkeypatch):
        # Stub backend
        from crm.cli import CLIContext

        class StubBackend:
            def get(self, path, **kw):
                assert "RetrieveTotalRecordCount" in path
                return {"EntityRecordCountCollection": {
                    "Keys": ["account"], "Values": [7]
                }}

        monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "query", "count", "account"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["count"] == 7
        assert env["data"]["entity"] == "account"
```

- [ ] **Step 5.2: Run, verify failure**

```powershell
pytest crm/tests/test_query_count.py -v
```

Expected: `ImportError` for `total_record_count`.

- [ ] **Step 5.3: Implement `total_record_count` in `crm/core/query.py`**

Append to `crm/core/query.py`:

```python
def total_record_count(backend: D365Backend, entity: str) -> int:
    """Call RetrieveTotalRecordCount for one entity logical name.

    Returns the integer count. D365 caches counts and refreshes them on a
    server-side schedule, so the value can lag inserts/deletes by minutes.
    """
    if not entity:
        raise D365Error("entity logical name is required")
    # URL-encoded inline param per OData v4 unbound-function spec
    path = f"RetrieveTotalRecordCount(EntityNames=['{entity}'])"
    result = as_dict(backend.get(path))
    coll = result.get("EntityRecordCountCollection") or {}
    keys = coll.get("Keys") or []
    values = coll.get("Values") or []
    if not keys or not values:
        raise D365Error(
            f"RetrieveTotalRecordCount returned no rows for {entity!r}",
            response_body=result,
        )
    return int(values[0])
```

- [ ] **Step 5.4: Add `query count` CLI command**

In `crm/commands/query.py`, add to the bottom (after `query_user`):

```python
from crm.core.query import total_record_count


@query_group.command("count")
@click.argument("entity")
@pass_ctx
def query_count(ctx: CLIContext, entity: str):
    """Count rows for an entity via RetrieveTotalRecordCount (cached server-side)."""
    try:
        n = total_record_count(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"entity": entity, "count": n})
```

Adjust imports at the top of `crm/commands/query.py` to include `_handle_d365_error` from `_helpers` if not already imported.

- [ ] **Step 5.5: Run tests**

```powershell
pytest crm/tests/test_query_count.py -v
```

Expected: all PASS.

- [ ] **Step 5.6: Run full suite**

```powershell
pytest crm/tests -q
```

Expected: all pass.

- [ ] **Step 5.7: Commit**

```powershell
git add crm/core/query.py crm/commands/query.py crm/tests/test_query_count.py
git commit -m "feat: add 'query count' (RetrieveTotalRecordCount)

Thin wrapper around the cached server-side row count. Returns
{entity, count} in JSON mode. Spec E."
```

---

## Task 6: Add `metadata list-actions` / `metadata list-functions`

**Files:**
- Modify: `crm/core/metadata.py` (append helpers)
- Modify: `crm/commands/metadata.py` (add subcommands)
- Create: `crm/tests/test_metadata_actions_functions.py`

**Note:** the D365 `$metadata` endpoint returns CSDL XML, not JSON. We parse the XML with stdlib `xml.etree.ElementTree`. Both `<Action>` and `<Function>` elements live under `<Schema Namespace="Microsoft.Dynamics.CRM">` with attributes `Name` and child `<Parameter Name="..." Type="..."/>` elements.

- [ ] **Step 6.1: Write the failing tests**

`crm/tests/test_metadata_actions_functions.py`:

```python
"""Unit tests for metadata list-actions / list-functions."""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock

from crm.core.metadata import list_actions, list_functions
from crm.utils.d365_backend import ConnectionProfile, D365Backend


_SAMPLE_METADATA_XML = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">
  <edmx:DataServices>
    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm"
            Namespace="Microsoft.Dynamics.CRM">
      <Action Name="PublishAllXml" />
      <Action Name="ImportSolution">
        <Parameter Name="CustomizationFile" Type="Edm.Binary" />
        <Parameter Name="PublishWorkflows" Type="Edm.Boolean" />
      </Action>
      <Function Name="RetrieveTotalRecordCount">
        <Parameter Name="EntityNames" Type="Collection(Edm.String)" />
      </Function>
      <Function Name="WhoAmI" />
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


@pytest.fixture
def backend():
    profile = ConnectionProfile(
        name="t", url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", verify_ssl=False,
    )
    return D365Backend(profile, password="pw")


def _mock_metadata(m: requests_mock.Mocker) -> None:
    m.get(
        "https://crm.contoso.local/contoso/api/data/v9.2/$metadata",
        text=_SAMPLE_METADATA_XML,
        headers={"Content-Type": "application/xml"},
    )


class TestListActions:
    def test_returns_action_names(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            actions = list_actions(backend)
        names = [a["name"] for a in actions]
        assert names == ["PublishAllXml", "ImportSolution"]

    def test_parameters_included(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            actions = list_actions(backend)
        import_solution = next(a for a in actions if a["name"] == "ImportSolution")
        assert import_solution["parameters"] == [
            {"name": "CustomizationFile", "type": "Edm.Binary"},
            {"name": "PublishWorkflows", "type": "Edm.Boolean"},
        ]


class TestListFunctions:
    def test_returns_function_names(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            functions = list_functions(backend)
        names = [f["name"] for f in functions]
        assert names == ["RetrieveTotalRecordCount", "WhoAmI"]

    def test_collection_parameter_type(self, backend):
        with requests_mock.Mocker() as m:
            _mock_metadata(m)
            functions = list_functions(backend)
        rtrc = next(f for f in functions if f["name"] == "RetrieveTotalRecordCount")
        assert rtrc["parameters"] == [
            {"name": "EntityNames", "type": "Collection(Edm.String)"},
        ]
```

- [ ] **Step 6.2: Run tests, verify failure**

```powershell
pytest crm/tests/test_metadata_actions_functions.py -v
```

Expected: `ImportError` for `list_actions` / `list_functions`.

- [ ] **Step 6.3: Implement helpers in `crm/core/metadata.py`**

Append to `crm/core/metadata.py`:

```python
import xml.etree.ElementTree as _ET

_EDM_NS = "http://docs.oasis-open.org/odata/ns/edm"


def _fetch_csdl(backend: D365Backend) -> _ET.Element:
    """GET $metadata and parse as XML. Returns the <Schema> element."""
    raw = backend.get("$metadata", expect_json=False)
    if not isinstance(raw, str):
        raise D365Error("$metadata response was not text/xml")
    root = _ET.fromstring(raw)
    schema = root.find(f".//{{{_EDM_NS}}}Schema")
    if schema is None:
        raise D365Error("No <Schema> element in $metadata response")
    return schema


def _extract_callable(schema: _ET.Element, tag: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for elem in schema.findall(f"{{{_EDM_NS}}}{tag}"):
        params: list[dict[str, str]] = []
        for p in elem.findall(f"{{{_EDM_NS}}}Parameter"):
            params.append({
                "name": p.attrib.get("Name", ""),
                "type": p.attrib.get("Type", ""),
            })
        items.append({"name": elem.attrib.get("Name", ""), "parameters": params})
    return items


def list_actions(backend: D365Backend) -> list[dict[str, Any]]:
    """List OData actions (POST verbs) declared by the D365 service.

    Returns `[{name, parameters: [{name, type}, ...]}, ...]`.
    Parses `$metadata` (CSDL XML); requires no special permissions.
    """
    return _extract_callable(_fetch_csdl(backend), "Action")


def list_functions(backend: D365Backend) -> list[dict[str, Any]]:
    """List OData functions (GET verbs) declared by the D365 service."""
    return _extract_callable(_fetch_csdl(backend), "Function")


def list_entity_names(backend: D365Backend) -> list[str]:
    """Return entity logical names. Powers REPL tab completion (Task 7)."""
    result = as_dict(backend.get(
        "EntityDefinitions",
        params={"$select": "LogicalName"},
    ))
    return [
        e.get("LogicalName", "") for e in result.get("value", [])
        if e.get("LogicalName")
    ]
```

(We add `list_entity_names` here too — it's used by Task 7's `MetadataCache` and the helper belongs alongside the other metadata helpers.)

Verify `backend.get(..., expect_json=False)` returns the raw response text. Confirm by checking `_parse_response` in `d365_backend.py` — when `expect_json=False`, it returns `resp.text`. If that contract differs, adjust the call site here to use `backend.request("GET", "$metadata", expect_json=False)`.

- [ ] **Step 6.4: Add CLI subcommands**

In `crm/commands/metadata.py`, after `metadata_relationships`, add:

```python
from crm.core.metadata import list_actions, list_functions


@metadata_group.command("list-actions")
@pass_ctx
def metadata_list_actions(ctx: CLIContext):
    """List OData actions advertised by the service ($metadata)."""
    try:
        items = list_actions(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["Name", "Parameters"]
    rows = [
        [a["name"], ", ".join(f"{p['name']}:{p['type']}" for p in a["parameters"])]
        for a in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@metadata_group.command("list-functions")
@pass_ctx
def metadata_list_functions(ctx: CLIContext):
    """List OData functions advertised by the service ($metadata)."""
    try:
        items = list_functions(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["Name", "Parameters"]
    rows = [
        [f["name"], ", ".join(f"{p['name']}:{p['type']}" for p in f["parameters"])]
        for f in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})
```

Confirm `_handle_d365_error` is imported in `crm/commands/metadata.py` (added in Task 1).

- [ ] **Step 6.5: Run tests**

```powershell
pytest crm/tests/test_metadata_actions_functions.py -v
pytest crm/tests -q
```

Expected: all pass.

- [ ] **Step 6.6: Commit**

```powershell
git add crm/core/metadata.py crm/commands/metadata.py crm/tests/test_metadata_actions_functions.py
git commit -m "feat: add metadata list-actions / list-functions

Parses \$metadata (CSDL XML) and emits OData Action/Function
declarations as a table or JSON. Also adds list_entity_names()
helper used by REPL tab completion. Spec E."
```

---

## Task 7: REPL metadata cache + readline tab completion

**Files:**
- Modify: `crm/commands/repl.py` (add `MetadataCache` + completer wiring)
- Create: `crm/tests/test_metadata_cache.py`

**Scope clarification:** Tab completion happens inside the REPL only; one-shot CLI mode is unaffected. Completion fires only when the cursor is on an argument that takes an entity logical name (the slot directly after a recognized verb like `entity get`, `query odata`, `query count`, `metadata attributes`, `metadata entity`). For Spec E we ship a narrow allowlist of slots; broader coverage is future work.

- [ ] **Step 7.1: Write failing tests**

`crm/tests/test_metadata_cache.py`:

```python
"""Unit tests for REPL metadata cache + completion logic."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.commands.repl import MetadataCache, complete_entity_token


class _FakeBackend:
    def __init__(self):
        self.calls = 0

    def get(self, path, params=None, **kw):
        self.calls += 1
        return {"value": [
            {"LogicalName": "account"},
            {"LogicalName": "contact"},
            {"LogicalName": "new_project"},
        ]}


class TestMetadataCache:
    def test_first_call_fetches_entity_names(self):
        b = _FakeBackend()
        cache = MetadataCache()
        names = cache.entities(b)
        assert names == ["account", "contact", "new_project"]
        assert b.calls == 1

    def test_repeated_call_uses_cache(self):
        b = _FakeBackend()
        cache = MetadataCache()
        cache.entities(b)
        cache.entities(b)
        cache.entities(b)
        assert b.calls == 1


class TestCompleteEntityToken:
    def test_no_match_when_prefix_unrecognized(self):
        names = ["account", "contact"]
        # cursor on the verb itself, not an entity slot
        assert complete_entity_token("ent", names) is None

    def test_returns_matches_after_entity_get(self):
        names = ["account", "contact", "new_project"]
        # cursor on the second token after "entity get"
        out = complete_entity_token("entity get acc", names)
        assert out == ["account"]

    def test_returns_matches_after_query_count(self):
        names = ["account", "contact", "new_project"]
        out = complete_entity_token("query count n", names)
        assert out == ["new_project"]

    def test_returns_all_when_no_prefix(self):
        names = ["account", "contact"]
        out = complete_entity_token("entity get ", names)
        assert out == names
```

- [ ] **Step 7.2: Run tests, verify failure**

```powershell
pytest crm/tests/test_metadata_cache.py -v
```

Expected: `ImportError`.

- [ ] **Step 7.3: Implement `MetadataCache` + `complete_entity_token`**

In `crm/commands/repl.py`, add at the top of the module (above `repl()`):

```python
from crm.core.metadata import list_entity_names

# Slot-aware completion table: (group, verb) -> argument index (0-based)
# at which the entity logical name appears.
_ENTITY_SLOTS: dict[tuple[str, str], int] = {
    ("entity",   "get"):        2,
    ("entity",   "create"):     2,
    ("entity",   "update"):     2,
    ("entity",   "upsert"):     2,
    ("entity",   "delete"):     2,
    ("query",    "odata"):      2,
    ("query",    "fetchxml"):   2,
    ("query",    "count"):      2,
    ("query",    "saved"):      2,
    ("query",    "user"):       2,
    ("metadata", "entity"):     2,
    ("metadata", "attributes"): 2,
}


class MetadataCache:
    """In-memory cache of entity logical names for the REPL session."""

    def __init__(self) -> None:
        self._entities: list[str] | None = None

    def entities(self, backend) -> list[str]:
        if self._entities is None:
            self._entities = list_entity_names(backend)
        return self._entities


def complete_entity_token(line: str, names: list[str]) -> list[str] | None:
    """Given the REPL line buffer, return entity-name completions or None.

    Returns None when the cursor isn't on an entity-name slot. Returns a
    (possibly empty) list when it is.
    """
    parts = line.split()
    # Determine the token index the cursor is currently on.
    # If the line ends with a space, the cursor is on the next (empty) token.
    if line.endswith(" "):
        token_index = len(parts)
        prefix = ""
    else:
        if not parts:
            return None
        token_index = len(parts) - 1
        prefix = parts[-1]

    if len(parts) < 2:
        return None
    group, verb = parts[0], parts[1]
    expected_idx = _ENTITY_SLOTS.get((group, verb))
    if expected_idx is None or expected_idx != token_index:
        return None
    return [n for n in names if n.startswith(prefix)]
```

- [ ] **Step 7.4: Wire `readline` into the REPL loop**

Modify `crm/commands/repl.py`. The current REPL uses `prompt_toolkit` via `ctx.skin.get_input`. `prompt_toolkit` ignores `readline` entirely, so tab-completion must use `prompt_toolkit`'s own completer API. Update the approach:

Add at the top of `repl.py`:

```python
from prompt_toolkit.completion import Completer, Completion
```

Define a completer class that delegates to `complete_entity_token`:

```python
class _EntityCompleter(Completer):
    """prompt_toolkit completer for entity-name slots."""

    def __init__(self, backend_getter, cache: "MetadataCache"):
        self._get_backend = backend_getter
        self._cache = cache

    def get_completions(self, document, complete_event):
        line = document.text_before_cursor
        try:
            names = self._cache.entities(self._get_backend())
        except Exception:  # noqa: BLE001 — completion must never raise
            return
        matches = complete_entity_token(line, names)
        if matches is None:
            return
        # Determine the prefix length to replace
        if line.endswith(" "):
            prefix_len = 0
        else:
            prefix_len = len(line.split()[-1]) if line.split() else 0
        for name in matches:
            yield Completion(name, start_position=-prefix_len)
```

In the `repl()` function, change the `pt_session = ctx.skin.create_prompt_session()` line. Inspect `crm/utils/repl_skin.py` to find `create_prompt_session` (or equivalent). The completer is wired via `prompt_toolkit.PromptSession(completer=...)`. If `ReplSkin.create_prompt_session` does not accept a `completer` argument, extend it minimally (additive parameter, default `None`).

Concretely:

1. In `crm/utils/repl_skin.py`, change `create_prompt_session(self)` → `create_prompt_session(self, completer=None)`, and forward `completer=completer` to the `PromptSession(...)` constructor inside the method body.
2. In `crm/commands/repl.py`, inside `repl()`:

```python
    cache = MetadataCache()
    completer = _EntityCompleter(ctx.backend, cache)
    pt_session = ctx.skin.create_prompt_session(completer=completer)
```

3. Remove the old `from readline import ...` / `set_completer` plan — we use prompt_toolkit instead.

- [ ] **Step 7.5: Run cache + completion tests**

```powershell
pytest crm/tests/test_metadata_cache.py -v
```

Expected: all PASS.

- [ ] **Step 7.6: Run full suite**

```powershell
pytest crm/tests -q
```

Expected: all pass.

- [ ] **Step 7.7: Manual smoke (not in CI)**

Document a smoke test in the PR description (not committed code). Manual instructions for the reviewer:

```
python -m crm
crm> connection connect --url ... (or use an existing profile)
crm> entity get <TAB>     # Should fetch entity list once and show options
crm> entity get acc<TAB>  # Should narrow to entities starting with 'acc'
crm> query count new<TAB> # Should narrow to entities starting with 'new'
crm> quit
```

- [ ] **Step 7.8: Commit**

```powershell
git add crm/commands/repl.py crm/utils/repl_skin.py crm/tests/test_metadata_cache.py
git commit -m "feat: REPL tab completion for entity names

MetadataCache fetches entity logical names lazily on first
completion and caches them for the REPL session.
_EntityCompleter wires it into prompt_toolkit's PromptSession.
Completion only activates on known entity-name argument slots.
Spec E."
```

---

## Task 8: Update REPL help + announce new commands

**Files:**
- Modify: `crm/commands/repl.py` (extend `_repl_help`)

- [ ] **Step 8.1: Extend the help table**

In `_repl_help` (the dict passed to `ctx.skin.help`), add four new entries near the relevant sections:

```python
        "init [--template]": "Bootstrap a workspace (.env.example or interactive profile)",
        "query count <entity>": "RetrieveTotalRecordCount via cached server-side count",
        "metadata list-actions": "List OData actions (POST verbs)",
        "metadata list-functions": "List OData functions (GET verbs)",
```

Place them in logical positions inside the existing dict literal.

- [ ] **Step 8.2: Smoke-test**

```powershell
python -m crm repl
crm> help
crm> quit
```

Expected: the four new lines appear in the help block.

- [ ] **Step 8.3: Commit**

```powershell
git add crm/commands/repl.py
git commit -m "docs: announce new commands in REPL help"
```

---

## Task 9: Run pyright on the strict zone

The strict zones (`crm/core/*`, `crm/utils/d365_backend.py`) are exercised by Task 2 (`logging_setup.py`), Task 3 (`d365_backend.py`), Task 5 (`core/query.py`), Task 6 (`core/metadata.py`).

- [ ] **Step 9.1: Run pyright**

```powershell
pyright
```

Expected: zero errors. If new errors appear (e.g. in `_make_auth` from Task 3 because `requests_negotiate_sspi` has no type stubs), suppress with `# type: ignore[import-untyped]` at the import line in `_make_auth`. Do not loosen the `pyrightconfig.json` strict zone.

- [ ] **Step 9.2: Commit any fixes if needed**

```powershell
git add <files>
git commit -m "chore: pyright cleanup for Spec E"
```

If nothing to commit, skip.

---

## Task 10: Version bump + CHANGELOG

**Files:**
- Modify: `crm/__init__.py` (`__version__ = "0.6.0"`)
- Modify: `setup.py` (`version="0.6.0"`)
- Modify: `CHANGELOG.md` (prepend 0.6.0 entry)

- [ ] **Step 10.1: Bump version strings**

Edit `crm/__init__.py`:

```python
__version__ = "0.6.0"
```

Edit `setup.py` line 6:

```python
    version="0.6.0",
```

(Note: `crm/__init__.py` was at `0.4.0` and `setup.py` was at `0.5.0` — drift fixed by this bump.)

- [ ] **Step 10.2: Prepend a CHANGELOG entry**

Open `CHANGELOG.md`, add at the top (after the title):

```markdown
## 0.6.0 — Spec E: DX Polish

**Refactor**
- Split `crm/cli.py` (2098 lines) into focused modules under `crm/commands/`
  (one Click group per file). Pure refactor — zero behavior change.

**Added**
- `--log-level debug|info|warning|error` + `--log-format text|json-line` on
  the root CLI group (env: `CRM_LOG_LEVEL`, `CRM_LOG_FORMAT`).
- `--verbose` flag (alias for `--log-level debug`).
- `--auth-scheme ntlm|kerberos|negotiate` on the root CLI group
  (env: `CRM_AUTH_SCHEME`). Kerberos/Negotiate via `requests_negotiate_sspi`
  (install with `pip install crm[kerberos]`).
- `crm init` command: `--template` writes `.env.example`; no args runs an
  interactive profile wizard.
- `query count <entity>` — calls `RetrieveTotalRecordCount`.
- `metadata list-actions` — parses `$metadata` and lists OData actions.
- `metadata list-functions` — parses `$metadata` and lists OData functions.
- REPL tab completion for entity-name argument slots, backed by a lazy
  in-memory `MetadataCache`.

**Changed**
- `ConnectionProfile` gains an `auth_scheme` field (default `"ntlm"`,
  backward compatible).
- `crm/utils/repl_skin.py::create_prompt_session` accepts an optional
  `completer` argument.
```

- [ ] **Step 10.3: Verify**

```powershell
python -m crm --version
```

Expected: `crm, version 0.6.0`.

- [ ] **Step 10.4: Run full suite one final time**

```powershell
pytest crm/tests -q
```

Expected: all pass.

- [ ] **Step 10.5: Commit**

```powershell
git add crm/__init__.py setup.py CHANGELOG.md
git commit -m "chore: bump to 0.6.0 (Spec E DX polish)"
```

---

## Task 11: Push branch and open the PR

- [ ] **Step 11.1: Push the branch**

```powershell
git push -u origin feat/spec-e-dx-polish
```

- [ ] **Step 11.2: Open the PR**

```powershell
gh pr create --title "Spec E: DX polish (0.6.0)" --body @'
## Summary

Single PR implementing all eight Spec E DX-polish items + version bump to 0.6.0.

- Split `cli.py` (2098 lines) into `crm/commands/` modules (one Click group per file).
- `--log-level` / `--log-format` / `--verbose` via stdlib `logging`.
- `--auth-scheme ntlm|kerberos|negotiate` (Kerberos via `requests_negotiate_sspi`, optional extra).
- `crm init` (template + interactive wizard).
- `query count` (RetrieveTotalRecordCount).
- `metadata list-actions` / `metadata list-functions`.
- REPL tab completion (lazy metadata cache).

Closes #7.
Spec: `docs/superpowers/specs/2026-05-25-spec-e-dx-polish-design.md`

## Test plan

- [ ] `pytest crm/tests -q` — full suite green
- [ ] `pyright` — zero errors
- [ ] `python -m crm --help` shows new root options
- [ ] `python -m crm init --template` writes `.env.example`
- [ ] Manual: REPL `entity get <TAB>` completes against live D365 (Windows)
- [ ] Manual: `--auth-scheme kerberos` with `crm[kerberos]` installed connects to on-prem D365

🤖 Generated with [Claude Code](https://claude.com/claude-code)
'@
```

Note: the heredoc uses single-quoted PowerShell here-string (`@'...'@`) so `$metadata` and backticks aren't expanded.

---

## Self-review checklist (for the engineer running this plan)

After all tasks:

- [ ] Every step shows code or a command — no "implement appropriate handling" placeholders.
- [ ] `MetadataCache.entities()` method called the same way in tests and in the completer (`cache.entities(backend)`).
- [ ] `list_entity_names()` is defined once (Task 6, in `crm/core/metadata.py`) and imported by Task 7's `repl.py`.
- [ ] `_make_auth(password)` signature matches both the call site in `__init__` and the test stub.
- [ ] All new commands wired into `crm/cli.py` via `cli.add_command(...)`.
- [ ] Version bump touches both `crm/__init__.py` and `setup.py` (was drifted before; now aligned).
- [ ] Heredoc / here-string syntax matches the shell (`@'...'@` for PowerShell, `<<'EOF'` for bash).
- [ ] No test file accidentally hits a live D365 — all use `requests_mock` or `CliRunner` with stubs.
