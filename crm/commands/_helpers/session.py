"""Session / journal / no-retry-scope helpers."""
# pyright: basic
from __future__ import annotations
import os
from contextlib import contextmanager
from typing import TYPE_CHECKING
import click
from crm.core import session as session_mod
if TYPE_CHECKING:
    from crm.cli import CLIContext


def _journal(ctx, target, result, *, solution=None, staged=None):
    """Best-effort audit-journal a successful mutation (issue #89). Never raises.

    The command name is derived from the live Click context — `command_path`
    minus the root prog-name (e.g. "crm entity create" -> "entity create") — not
    hand-typed at the call site, so the journal name can never drift from the
    actual Click command. The root prog-name is stripped by its actual value
    (`find_root().info_name`), not by splitting on the first space, so a
    multi-token prog-name still yields "<group> <verb>": `crm` (the binary) and
    the REPL both give "crm", while `python -m crm` gives "python -m crm" — all
    three reduce to the same recorded name. A missing Click context makes the
    derivation raise, which the best-effort `except Exception: pass` swallows.
    """
    try:
        from crm.core import audit
        cctx = click.get_current_context()
        root_prog = cctx.find_root().info_name or ""
        command = cctx.command_path[len(root_prog):].lstrip()
        # Prefer the RESOLVED profile name from the backend that just ran the
        # mutation — ctx.profile_name is only the explicit --profile override and
        # is None for active-profile runs. The backend is already built (the
        # command called ctx.backend()), so this needs no extra I/O.
        backend = getattr(ctx, "_backend", None)
        profile = getattr(getattr(backend, "profile", None), "name", None) or ctx.profile_name
        audit.record(
            session=ctx.session_name,
            profile=profile,
            command=command,
            target=target,
            result=result,
            solution=solution,
            staged=ctx.stage_only if staged is None else staged,
            dry_run=ctx.dry_run,
        )
    except Exception:
        pass


def _touch_session(ctx: "CLIContext", entity_set: str, *,
                   last_query: dict | None = None) -> None:
    state = session_mod.load_session(ctx.session_name)
    state["current_entity_set"] = entity_set
    if last_query is not None:
        state["last_query"] = last_query
    session_mod.save_session(state, ctx.session_name)


@contextmanager
def _no_retry_scope(ctx: "CLIContext", enabled: bool):
    """Scope CRM_NO_RETRY=1 to the command body and rebuild the cached backend.

    Without rebuilding, D365Backend's retry config (captured at construction)
    misses the flag. Without restoring, the env var leaks into later REPL
    commands.
    """
    if not enabled:
        yield
        return
    prev = os.environ.get("CRM_NO_RETRY")
    os.environ["CRM_NO_RETRY"] = "1"
    ctx.invalidate_backend()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("CRM_NO_RETRY", None)
        else:
            os.environ["CRM_NO_RETRY"] = prev
        ctx.invalidate_backend()
