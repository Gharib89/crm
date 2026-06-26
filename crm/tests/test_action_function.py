# pyright: basic
"""`action function` path construction — unbound and bound (issue 352).

Mirrors the bind semantics `action invoke` already has, but issued as a GET:
--bind-set alone → collection-bound, --bind-set + --bind-id → record-bound.
"""
from __future__ import annotations

import json

import pytest
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


def _last_params(backend):
    return backend.calls[-1][2].get("params")


def test_record_reference_param_emitted_as_alias(inject_backend, make_fake_backend):
    """A {"@odata.id": ...} param value becomes a parameter alias carrying the
    @odata.id reference in the query string, not an inline literal (issue 365)."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        [
            "RetrievePrincipalAccess",
            "--bind-set", "systemusers",
            "--bind-id", "11111111-1111-1111-1111-111111111111",
            "--params",
            '{"Target": {"@odata.id": "accounts(22222222-2222-2222-2222-222222222222)"}}',
        ],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == (
        "systemusers(11111111-1111-1111-1111-111111111111)"
        "/Microsoft.Dynamics.CRM.RetrievePrincipalAccess(Target=@p1)"
    )
    assert _last_params(backend) == {
        "@p1": '{"@odata.id": "accounts(22222222-2222-2222-2222-222222222222)"}'
    }


def test_reserved_char_string_param_aliased(inject_backend, make_fake_backend):
    """A string with URL-reserved chars is moved to a query alias (inline 400/404s)."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        ["GetTimeZoneCodeByLocalizedName", "--params", '{"Name": "Pacific/Standard"}'],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "GetTimeZoneCodeByLocalizedName(Name=@p1)"
    assert _last_params(backend) == {"@p1": "'Pacific/Standard'"}


def test_whitespace_string_param_aliased(inject_backend, make_fake_backend):
    """A value containing whitespace is aliased (an inline space breaks the URL)."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        ["GetTzCode", "--params", '{"Name": "Pacific Standard Time"}'],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "GetTzCode(Name=@p1)"
    assert _last_params(backend) == {"@p1": "'Pacific Standard Time'"}


def test_clean_string_param_stays_inline(inject_backend, make_fake_backend):
    """A reserved-char-free string is still encoded inline, no alias (no regression)."""
    result, backend = _run(
        inject_backend, make_fake_backend, ["CalcSomething", "--params", '{"Name": "Acme"}']
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "CalcSomething(Name='Acme')"
    assert _last_params(backend) is None


def test_mixed_inline_and_alias_params(inject_backend, make_fake_backend):
    """Inline scalars and aliased refs compose; only aliased params consume @pN."""
    result, backend = _run(
        inject_backend,
        make_fake_backend,
        [
            "CalculateRollupField",
            "--params",
            '{"FieldName": "new_rollup", '
            '"Target": {"@odata.id": "opportunities(33333333-3333-3333-3333-333333333333)"}}',
        ],
    )
    assert result.exit_code == 0, result.output
    assert backend.last_path == "CalculateRollupField(FieldName='new_rollup',Target=@p1)"
    assert _last_params(backend) == {
        "@p1": '{"@odata.id": "opportunities(33333333-3333-3333-3333-333333333333)"}'
    }


@pytest.mark.parametrize("bad", [{"id": "x"}, {"@odata.id": "accounts(1111)", "x": 1}])
def test_malformed_reference_missing_odata_id_fails(inject_backend, make_fake_backend, bad):
    """A dict param that isn't exactly {'@odata.id': ...} is a malformed reference: exit 1, no GET."""
    result, backend = _run(
        inject_backend, make_fake_backend, ["CalcRollup", "--params", json.dumps({"Target": bad})]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False
    assert backend.count("get") == 0


def test_bind_id_without_bind_set_is_operational_failure(inject_backend, make_fake_backend):
    """--bind-id alone is invalid: a record needs its collection (exit 1, ADR 0001)."""
    result, backend = _run(
        inject_backend, make_fake_backend, ["RetrieveUserPrivileges", "--bind-id", "x"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False
    assert backend.count("get") == 0
