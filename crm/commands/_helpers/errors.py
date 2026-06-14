"""D365 error translation + the `d365_errors` envelope seam (#264)."""
# pyright: basic
from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from crm.cli import CLIContext
    from crm.utils.d365_backend import D365Error


def _handle_d365_error(
    ctx: "CLIContext",
    exc: D365Error,
    *,
    hint: str | None = None,
    extra_meta: dict | None = None,
    warnings: list[str] | None = None,
) -> None:
    # Local import: this only runs after the backend already raised a D365Error,
    # so d365_backend is loaded — keeps it off the `crm --version` fast path.
    from crm.utils.d365_backend import classify_d365_error
    category, retryable = classify_d365_error(exc.status, exc.code, str(exc))
    meta: dict[str, Any] = {
        "status": exc.status,
        "code": exc.code,
        "category": category,
        "retryable": retryable,
    }
    # Auto-derive an auth fix-it hint on a 401 when the caller gave none, so a
    # rejected/stale secret steers the user to re-store it. The active profile
    # name comes from the resolved backend, else the --profile flag.
    if hint is None and exc.status == 401:
        backend = getattr(ctx, "_backend", None)
        pname = (getattr(getattr(backend, "profile", None), "name", None)
                 or ctx.profile_name or "<name>")
        hint = _auth_error_hint(exc.status, pname)
    # Partial-failure context (#64): only the non-transactional optionset update
    # path sets these. Guarded is-not-None so every other error site keeps
    # emitting an identical {status, code, category, retryable} envelope.
    if exc.completed_steps is not None:
        meta["completed_steps"] = exc.completed_steps
    if exc.stage is not None:
        meta["failed_stage"] = exc.stage
    if hint and ctx.json_mode:
        meta["hint"] = hint
    if extra_meta:
        meta.update(extra_meta)
    message = f"{exc}\nHint: {hint}" if hint else str(exc)
    ctx.emit(False, error=message, meta=meta, warnings=warnings)


@contextmanager
def d365_errors(ctx: "CLIContext", *, hint: str | None = None,
                warnings: list[str] | None = None):
    """Translate a `D365Error` from a single fallible core call into the standard
    failure envelope — the boilerplate that wrapped ~130 verbs by hand (#264).

    Wrap exactly one core call; a verb with two sequential core calls gets two
    `with` blocks. On `D365Error` it routes through `_handle_d365_error`, which
    emits `ok=False` and raises `Exit(1)` — so control never falls through the
    block on error and the call site needs no `except ...: return`. Carries only
    a STATIC `hint`/`warnings` (known before the call); a site that derives the
    hint or `extra_meta` FROM the caught exception (entity dup-key enrichment,
    workflow/entity activation hints) keeps its hand-written `try/except`.
    """
    from crm.utils.d365_backend import D365Error
    try:
        yield
    except D365Error as exc:
        _handle_d365_error(ctx, exc, hint=hint, warnings=warnings)


def _auth_error_hint(status: int | None, profile_name: str) -> str:
    """Map an auth failure to a copy-paste fix command, or '' when none applies.

    A 401 (rejected secret) steers the user to re-store the secret for the
    active profile."""
    if status == 401:
        return f"run: crm profile set-password --profile {profile_name}"
    return ""
