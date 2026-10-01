"""OData function and action commands."""

# pyright: basic
from __future__ import annotations

import json
from typing import Any

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _journal,
    _load_payload,
    d365_errors,
)

# Characters that are structurally significant in a URL path segment: an inline
# OData function-parameter literal carrying any of them 400/404s. Per the Web
# API docs they must be passed as a query-string parameter alias instead, where
# the value is percent-encoded safely.
# https://learn.microsoft.com/power-apps/developer/data-platform/webapi/use-web-api-functions#passing-parameters-to-a-function
_ODATA_RESERVED = set("/<>*%&:\\?+#")


def _needs_alias(value: Any) -> bool:
    """A function-param value must be passed as a parameter alias (not inline)
    when it is a record reference (a dict) or a string carrying URL-reserved or
    whitespace characters that would break an inline path-segment literal.
    """
    if isinstance(value, dict):
        return True
    if isinstance(value, str):
        return any(c in _ODATA_RESERVED or c.isspace() for c in value)
    return False


def _record_reference_value(name: str, value: dict[str, Any]) -> str:
    """Validate a record-reference param (``{"@odata.id": "set(guid)"}``) and
    render its alias value as an ``@odata.id`` JSON object. Raises ValueError on
    a malformed reference so the command can report it as an operational
    failure.
    """
    ref = value.get("@odata.id")
    if set(value) != {"@odata.id"} or not isinstance(ref, str) or not ref:
        raise ValueError(
            f"parameter {name!r} must be a record reference of the form "
            '{"@odata.id": "<entityset>(<guid>)"}'
        )
    return json.dumps({"@odata.id": ref})


def encode_function_params(params: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Encode ``action function`` params into ``(inline_args, aliases)``.

    ``inline_args`` is the comma-joined ``Name=...`` body for ``Fn(...)``;
    scalars render inline per OData v4. Record references
    (``{"@odata.id": "set(guid)"}``) and reserved-char/whitespace strings instead
    become parameter aliases (``Name=@pN``), with ``aliases`` mapping each ``@pN``
    to its query-string value. The caller passes ``aliases`` as the request
    ``params=`` kwarg so the values land in the query string — the only place the
    server accepts a record reference or a reserved character. Raises ValueError
    on a malformed record reference.
    """
    # Local import keeps d365_backend off the `crm --version` fast path: this
    # only runs once an action is being built, by which point the backend is loaded.
    from crm.utils.d365_backend import odata_literal

    parts: list[str] = []
    aliases: dict[str, str] = {}
    for name, value in params.items():
        if _needs_alias(value):
            alias = f"@p{len(aliases) + 1}"
            aliases[alias] = (
                _record_reference_value(name, value)
                if isinstance(value, dict)
                else odata_literal(value)
            )
            parts.append(f"{name}={alias}")
        else:
            parts.append(f"{name}={odata_literal(value)}")
    return ",".join(parts), aliases


@click.group("action")
def action_group():
    """Invoke OData functions and actions (unbound or bound)."""


@action_group.command("function")
@click.argument("name")
@click.option("--params", "params_json", help="JSON dict of function parameters.")
@click.option(
    "--bind-set",
    help="Entity set to bind the function to (e.g. 'systemusers'). "
    "Alone → collection-bound; with --bind-id → record-bound.",
)
@click.option(
    "--bind-id",
    help="Record id to bind the function to. Requires --bind-set.",
)
@click.option(
    "--cast",
    default="Microsoft.Dynamics.CRM",
    show_default=True,
    help="Namespace for the function when bound. Override only for custom namespaces.",
)
@pass_ctx
def action_function(ctx: CLIContext, name, params_json, bind_set, bind_id, cast):
    """Call an OData function — unbound by default, bound when --bind-set is given.

    Functions issue a GET. Scalar params are encoded inline per OData v4; a
    record-reference param ({"@odata.id": "<set>(<guid>)"}) or a value with
    URL-reserved characters is passed as a parameter alias (Fn(P=@p1)?@p1=...).
    --bind-set alone binds to a collection (set/Ns.Fn(...)); adding --bind-id
    binds to a single record (set(id)/Ns.Fn(...)). --bind-id requires --bind-set.
    """
    if bind_id and not bind_set:
        raise click.UsageError("--bind-id requires --bind-set.")
    try:
        params = json.loads(params_json) if params_json else None
        if params is not None and not isinstance(params, dict):
            raise ValueError("--params must be a JSON object of parameter name/value pairs.")
        inline, aliases = encode_function_params(params) if params else ("", {})
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    call = f"{name}({inline})"
    if bind_set and bind_id:
        path = f"{bind_set}({bind_id})/{cast}.{call}"
    elif bind_set:
        path = f"{bind_set}/{cast}.{call}"
    else:
        path = call
    with d365_errors(ctx):
        result = ctx.backend().get(path, params=aliases or None)
    ctx.emit(True, data=result or {})


@action_group.command("invoke")
@click.argument("name")
@click.option("--body", "body_json", help="JSON body for the action.")
@click.option("--body-file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--bind-set",
    help="Entity set name to bind the action to (e.g. 'workflows'). Requires --bind-id.",
)
@click.option(
    "--bind-id",
    help="Record id to bind the action to. Requires --bind-set.",
)
@click.option(
    "--cast",
    default="Microsoft.Dynamics.CRM",
    show_default=True,
    help="Namespace for the action when bound. Override only for custom namespaces.",
)
@pass_ctx
def action_invoke(ctx: CLIContext, name, body_json, body_file, bind_set, bind_id, cast):
    """POST an OData action — unbound by default, bound when --bind-set/--bind-id given."""
    if bool(bind_set) ^ bool(bind_id):
        raise click.UsageError("--bind-set and --bind-id must be used together.")
    payload = _load_payload(body_json, body_file) if (body_json or body_file) else {}
    if bind_set and bind_id:
        path = f"{bind_set}({bind_id})/{cast}.{name}"
    else:
        path = name
    with d365_errors(ctx):
        result = ctx.backend().post(path, json_body=payload)
    data = result or {}
    ctx.emit(True, data=data)
    _journal(ctx, name, data)
