# pyright: basic
"""Canonical `--order-by` for OData `$orderby`, with hidden aliases (#711).

OData result ordering was spelled three ways — `--orderby` (query), `--order`
(view create), `--order-by` (async ops). `--order-by` is now the single canonical
spelling everywhere `$orderby` is expressed; the old spellings keep working as
**hidden** aliases (identical output, off the public catalogue). `view`'s
list-reorder `--order` (multiple, on `view set-order`) is a distinct flag and
stays untouched.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli

CTX = "https://crm.contoso.local/contoso/api/data/v9.2/$metadata#accounts"


# --- query odata: canonical + alias, per site ---------------------------------


def _query_orderby(flag: str, value: str, make_fake_backend, inject_backend) -> str:
    """Invoke `query odata` with `flag value` and return the emitted $orderby."""
    backend = inject_backend(
        make_fake_backend(responses={"get": {"@odata.context": CTX, "value": []}})
    )
    result = CliRunner().invoke(cli, ["--json", "query", "odata", "accounts", flag, value])
    assert result.exit_code == 0, result.output
    return backend.calls[-1][2]["params"]["$orderby"]


def test_query_order_by_canonical_sends_orderby(make_fake_backend, inject_backend):
    assert (
        _query_orderby("--order-by", "name desc", make_fake_backend, inject_backend) == "name desc"
    )


def test_query_orderby_alias_identical(make_fake_backend, inject_backend):
    # The deprecated spelling still produces the identical $orderby.
    assert (
        _query_orderby("--orderby", "name desc", make_fake_backend, inject_backend) == "name desc"
    )


# --- view create: canonical + alias -------------------------------------------


def _view_create_order(flag: str, value: str, monkeypatch) -> dict:
    """Invoke `view create` with `flag value`; return the create_view kwargs."""
    captured: dict = {}
    monkeypatch.setattr(
        "crm.core.views.create_view",
        lambda backend, **kw: captured.update(kw) or {"created": True, "name": kw["name"]},
    )
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "view",
            "create",
            "cwx_ticket",
            "--name",
            "X",
            "--otc",
            "1",
            "--column",
            "cwx_name:220",
            flag,
            value,
            "--solution",
            "TestSol",
            "--no-publish",
        ],
    )
    assert result.exit_code == 0, result.output
    return captured


def test_view_create_order_by_canonical(monkeypatch):
    captured = _view_create_order("--order-by", "createdon desc", monkeypatch)
    assert captured["order_by"] == "createdon"
    assert captured["order_desc"] is True


def test_view_create_order_alias_identical(monkeypatch):
    captured = _view_create_order("--order", "createdon desc", monkeypatch)
    assert captured["order_by"] == "createdon"
    assert captured["order_desc"] is True


def test_view_create_empty_order_by_is_usage_error(monkeypatch):
    # Precedence is by presence, not truthiness: an explicit empty --order-by
    # stays "provided" and reaches _parse_order (a usage error), as pre-#711.
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: object())
    result = CliRunner().invoke(
        cli,
        [
            "--json",
            "view",
            "create",
            "cwx_ticket",
            "--name",
            "X",
            "--otc",
            "1",
            "--column",
            "cwx_name:220",
            "--order-by",
            "",
            "--no-publish",
        ],
    )
    assert result.exit_code == 2, result.output


# --- vocabulary regression: catalogue advertises only the canonical spelling ---


def _catalogue() -> dict:
    result = CliRunner().invoke(cli, ["--json", "describe"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["data"]


def _opts(cmd: dict) -> set:
    return {o for p in cmd["params"] for o in p["opts"]}


def test_order_by_is_the_only_canonical_ordering_spelling():
    """Derived from the `describe` catalogue: `--order-by` is the sole visible
    OData-ordering spelling. `--orderby` never appears; the only visible `--order`
    is the list-reorder flag on `view set-order` (distinct semantics).
    """
    data = _catalogue()
    by_path = {c["path"]: c for c in data["commands"]}

    # Canonical spelling present on every OData-$orderby site.
    for path in ["query odata", "view create", "async list"]:
        assert "--order-by" in _opts(by_path[path]), f"{path} missing canonical --order-by"

    # Deprecated `--orderby` is hidden — it must not resurface anywhere.
    for cmd in data["commands"]:
        assert "--orderby" not in _opts(cmd), f"{cmd['path']} exposes deprecated --orderby"

    # `--order` is allowed only where it is the list-reorder flag (not $orderby).
    ORDER_REORDER_ONLY = {"view set-order"}
    for cmd in data["commands"]:
        if "--order" in _opts(cmd):
            assert cmd["path"] in ORDER_REORDER_ONLY, (
                f"{cmd['path']} exposes non-canonical --order for OData ordering"
            )
