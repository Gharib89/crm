"""D365 error translation + the `d365_errors` envelope seam (#264)."""

# pyright: basic
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from crm.cli import CLIContext
    from crm.utils.d365_backend import D365Error


_RESERVED_META_KEYS = frozenset({"status", "code", "category", "retryable"})
"""The pure-error envelope keys, always built from the raw D365Error first. An
enrich(exc) callback's extra_meta is additive only and may not name these."""


def _handle_d365_error(
    ctx: CLIContext,
    exc: D365Error,
    *,
    hint: str | None = None,
    extra_meta: dict | None = None,
    warnings: list[str] | None = None,
) -> None:
    """Emit the failure envelope for a `D365Error` and exit.

    `classify_d365_error` is imported here because this only runs after the
    backend already raised a D365Error, so `d365_backend` is loaded; a module-top
    import would put it on the `crm --version` fast path. See ADR 0029.

    On a 401 with no caller hint, an auth fix-it hint is derived so a
    rejected or stale secret steers the user to re-store it. The profile name
    comes from the resolved backend, else the profile `crm legacy` resolved, else
    the --profile flag. A delegated (oauth-user) profile has no secret to set, so
    the user signs in again instead.

    `completed_steps` and `failed_stage` (#64) are set only by the
    non-transactional optionset update path, so they are added only when present
    and every other error site keeps an identical {status, code, category,
    retryable} envelope.

    The pure error is reserved: an enrich(exc) callback is strictly additive and
    can never overwrite it, so naming a reserved key in `extra_meta` is a
    programming error (it mirrors the read_back-without-publish guard in
    `xml_edit.commit_xml_patches`).
    """
    from crm.utils.d365_backend import classify_d365_error

    category, retryable = classify_d365_error(exc.status, exc.code, str(exc))
    meta: dict[str, Any] = {
        "status": exc.status,
        "code": exc.code,
        "category": category,
        "retryable": retryable,
    }
    if hint is None and exc.status == 401:
        backend = getattr(ctx, "_backend", None)
        pname = (
            getattr(getattr(backend, "profile", None), "name", None)
            or getattr(ctx, "legacy_profile_name", None)
            or ctx.profile_name
            or "<name>"
        )
        delegated = getattr(getattr(backend, "profile", None), "auth_scheme", None) == "oauth-user"
        if delegated or exc.code in ("reauth_required", "login_failed", "wrong_tenant"):
            hint = f"Run crm profile login {pname}"
        else:
            hint = f"run: crm profile set-password --profile {pname}"
    if exc.completed_steps is not None:
        meta["completed_steps"] = exc.completed_steps
    if exc.stage is not None:
        meta["failed_stage"] = exc.stage
    if hint and ctx.json_mode:
        meta["hint"] = hint
    if extra_meta:
        collision = _RESERVED_META_KEYS & extra_meta.keys()
        if collision:
            raise ValueError(
                f"extra_meta may not overwrite reserved error keys: {sorted(collision)}"
            )
        meta.update(extra_meta)
    message = f"{exc}\nHint: {hint}" if hint else str(exc)
    ctx.emit(False, error=message, meta=meta, warnings=warnings)


@contextmanager
def d365_errors(
    ctx: CLIContext,
    *,
    hint: str | None = None,
    warnings: list[str] | None = None,
    enrich: Callable[[D365Error], tuple[str | None, dict | None]] | None = None,
):
    """Translate a `D365Error` from a single fallible core call into the standard
    failure envelope — the boilerplate that wrapped ~130 verbs by hand (#264).

    Wrap exactly one core call; a verb with two sequential core calls gets two
    `with` blocks. On `D365Error` it routes through `_handle_d365_error`, which
    emits `ok=False` and raises `Exit(1)` — so control never falls through the
    block on error and the call site needs no `except ...: return`.

    `hint`/`warnings` are STATIC (known before the call). `enrich(exc)` derives a
    hint and/or extra `meta` FROM the caught exception — returning `(hint, meta)`,
    either of which may be `None`. It is **strictly additive**: the pure error
    envelope is always built first and `meta` may not name a reserved key (see
    `_handle_d365_error`). The seam calls `enrich` unconditionally and is
    mode-agnostic — expensive enrichment (extra GETs) self-gates on
    `ctx.json_mode` inside the callback, exactly as the hand-written sites did.
    A derived hint (when non-None) wins over the static `hint=`.
    """
    from crm.utils.d365_backend import D365Error

    try:
        yield
    except D365Error as exc:
        e_hint, e_meta = enrich(exc) if enrich else (None, None)
        _handle_d365_error(
            ctx,
            exc,
            hint=e_hint if e_hint is not None else hint,
            extra_meta=e_meta,
            warnings=warnings,
        )


@contextmanager
def usage_guard():
    """Translate a status-less (client-side validation) `D365Error` raised by a
    core predicate into a `click.UsageError` (exit 2), before the backend is
    touched (#595).

    This lets a core validation predicate be the *single rule authority* while the
    CLI keeps its usage-error contract: a bad flag combination caught here exits
    `2` just as if Click had rejected it. A status-bearing error (transport /
    server) is left to propagate to `d365_errors` → exit `1`.
    """
    from crm.utils.d365_backend import D365Error

    try:
        yield
    except D365Error as exc:
        if exc.status is None:
            raise click.UsageError(str(exc)) from exc
        raise
