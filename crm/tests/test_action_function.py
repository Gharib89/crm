# pyright: basic
"""`action function` path construction — unbound and bound (issue 352).

Mirrors the bind semantics `action invoke` already has, but issued as a GET:
--bind-set alone → collection-bound, --bind-set + --bind-id → record-bound.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli


def _run(inject_backend, make_fake_backend, args):
    backend = inject_backend(make_fake_backend())
    result = CliRunner().invoke(cli, ["--json", "action", "function", *args])
    return result, backend


def test_collection_bound_builds_set_namespace_path(inject_backend, make_fake_backend):
    """--bind-set alone binds to a collection: set/Ns.Fn()."""
    result, backend = _run(
        inject_backend, make_fake_backend, ["RetrieveTotalRecordCount", "--bind-set", "systemusers"]
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "systemusers/Microsoft.Dynamics.CRM.RetrieveTotalRecordCount()"


def test_record_bound_builds_set_id_namespace_path(inject_backend, make_fake_backend):
    """--bind-set + --bind-id binds to a single record: set(id)/Ns.Fn()."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        ["RetrieveUserPrivileges", "--bind-set", "systemusers", "--bind-id", "11111111-1111-1111-1111-111111111111"],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == (
        "systemusers(11111111-1111-1111-1111-111111111111)"
        "/Microsoft.Dynamics.CRM.RetrieveUserPrivileges()"
    )


def test_cast_overrides_namespace(inject_backend, make_fake_backend):
    """--cast replaces the default Microsoft.Dynamics.CRM namespace segment."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        ["new_DoThing", "--bind-set", "accounts", "--cast", "Contoso.Custom"],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "accounts/Contoso.Custom.new_DoThing()"


def test_bound_function_encodes_params_inline(inject_backend, make_fake_backend):
    """Params are encoded inline in the bound path, same as unbound."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        ["GetWidgets", "--bind-set", "accounts", "--params", '{"Active": true}'],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "accounts/Microsoft.Dynamics.CRM.GetWidgets(Active=true)"


def test_unbound_unchanged_without_params(inject_backend, make_fake_backend):
    """No bind flags → unbound GET path, unchanged from before."""
    result, backend = _run(inject_backend, make_fake_backend, ["WhoAmI"])
    assert result.exit_code == 0, result.output
    assert backend.last_path == "WhoAmI()"


def test_unbound_unchanged_with_params(inject_backend, make_fake_backend):
    """Unbound params encoding is unchanged."""
    result, backend = _run(
        inject_backend, make_fake_backend, ["CalcSomething", "--params", '{"n": 5}']
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "CalcSomething(n=5)"


def test_bind_id_without_bind_set_is_operational_failure(inject_backend, make_fake_backend):
    """--bind-id alone is invalid: a record needs its collection (exit 1, ADR 0001)."""
    result, backend = _run(
        inject_backend, make_fake_backend, ["RetrieveUserPrivileges", "--bind-id", "x"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False
    assert backend.count("get") == 0
