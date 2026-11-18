# pyright: basic
"""A null lookup in an on-premises record write clears through `$ref` (#1325).

On-premises v9 answers 400 to a `<nav>@odata.bind: null` body and fails the
whole write, so on an on-premises profile `entity update` / `upsert` leave the
null bind out of the PATCH and clear the lookup with `DELETE <record>/<nav>/$ref`
in the same changeset, and `entity create` simply leaves it out. Dataverse
online keeps the null bind, which clears there.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import cli
from crm.core import entity as entity_mod
from crm.tests.test_lookup_bind import _meta_backend
from crm.utils.d365_backend import ConnectionProfile

GUID = "12345678-1234-1234-1234-123456789abc"
NULL_BIND = {"lastname": "Doe", "parentcustomerid_account@odata.bind": None}
CLEAR = {"method": "DELETE", "url": f"contacts({GUID})/parentcustomerid_account/$ref"}


def _ok(ops):
    return [
        {"method": op["method"], "url": op["url"], "status": 204, "headers": {}, "body": None}
        for op in ops
    ]


def _run(*args):
    return CliRunner().invoke(cli, ["--json", "entity", *args])


def _batch_ops(backend):
    (ops,) = [path for verb, path, _kw in backend.calls if verb == "batch"]
    return ops


def test_update_on_premises_patches_and_clears_in_one_changeset(make_fake_backend, inject_backend):
    backend = inject_backend(make_fake_backend(responses={"batch": _ok}))
    result = _run("update", "contacts", GUID, "--data", json.dumps(NULL_BIND))
    assert result.exit_code == 0, result.output
    assert _batch_ops(backend) == [
        {
            "method": "PATCH",
            "url": f"contacts({GUID})",
            "body": {"lastname": "Doe"},
            "headers": {"If-Match": "*"},
        },
        CLEAR,
    ]
    assert backend.calls[-1][2]["transactional"] is True
    assert backend.count("patch") == 0


def test_update_on_premises_rebinds_a_null_read_value_to_a_clear(inject_backend):
    backend = _meta_backend()
    backend._responses["batch"] = _ok
    inject_backend(backend)
    data = {"lastname": "Doe", "_parentcustomerid_value": None}
    result = _run("update", "contacts", GUID, "--data", json.dumps(data))
    assert result.exit_code == 0, result.output
    assert _batch_ops(backend)[1] == CLEAR


def test_upsert_on_premises_clears_without_if_match(make_fake_backend, inject_backend):
    backend = inject_backend(make_fake_backend(responses={"batch": _ok}))
    result = _run("upsert", "contacts", GUID, "--data", json.dumps(NULL_BIND))
    assert result.exit_code == 0, result.output
    assert _batch_ops(backend) == [
        {"method": "PATCH", "url": f"contacts({GUID})", "body": {"lastname": "Doe"}},
        CLEAR,
    ]


def test_upsert_by_key_on_premises_clears_on_the_key_address(
    make_fake_backend, inject_backend, monkeypatch
):
    backend = inject_backend(make_fake_backend(responses={"batch": _ok}))
    monkeypatch.setattr(entity_mod, "resolve_alternate_key", lambda _b, _s, attrs: attrs)
    data = {"emailaddress1": "a@contoso.com", **NULL_BIND}
    path = "contacts(emailaddress1='a%40contoso.com')"
    result = _run("upsert", "contacts", "--key", "emailaddress1", "--data", json.dumps(data))
    assert result.exit_code == 0, result.output
    assert _batch_ops(backend) == [
        {"method": "PATCH", "url": path, "body": {"lastname": "Doe"}},
        {"method": "DELETE", "url": f"{path}/parentcustomerid_account/$ref"},
    ]


def test_create_on_premises_leaves_the_null_lookup_out(make_fake_backend, inject_backend):
    backend = inject_backend(make_fake_backend())
    result = _run("create", "contacts", "--no-return", "--data", json.dumps(NULL_BIND))
    assert result.exit_code == 0, result.output
    assert backend.calls[-1][0] == "post"
    assert backend.calls[-1][2]["json_body"] == {"lastname": "Doe"}
    assert backend.count("batch") == backend.count("delete") == 0


def test_update_dry_run_previews_the_clears(make_fake_backend, inject_backend):
    backend = inject_backend(make_fake_backend(dry_run=True))
    result = _run("update", "contacts", GUID, "--data", json.dumps(NULL_BIND))
    assert result.exit_code == 0, result.output
    assert backend.count("batch") == 0
    assert [(v, p) for v, p, _k in backend.calls if v in ("patch", "delete")] == [
        ("patch", f"contacts({GUID})"),
        ("delete", f"contacts({GUID})/parentcustomerid_account/$ref"),
    ]


def test_update_on_cloud_keeps_the_null_bind(make_fake_backend, inject_backend):
    cloud = ConnectionProfile(
        name="c",
        url="https://org.crm.dynamics.com",
        domain="",
        username="",
        auth_scheme="oauth",
        tenant_id="tid",
        client_id="cid",
    )
    backend = inject_backend(make_fake_backend(profile=cloud))
    result = _run("update", "contacts", GUID, "--data", json.dumps(NULL_BIND))
    assert result.exit_code == 0, result.output
    assert backend.count("batch") == 0
    assert backend.calls[-1][2]["json_body"] == NULL_BIND
