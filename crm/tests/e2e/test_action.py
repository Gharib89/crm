# pyright: basic
"""E2E tests for the action group (OData function/action calls)."""
from __future__ import annotations

import json

from crm.tests.e2e.coverage import covers
from crm.tests.e2e.conftest import _safe_delete


@covers("action function")
def test_action_function_whoami(cli):
    """WhoAmI is a zero-side-effect unbound OData function present on every D365 org."""
    result = cli(["--json", "action", "function", "WhoAmI"])
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"action function WhoAmI failed: {data}"
    assert "UserId" in data["data"], f"UserId missing from WhoAmI response: {data['data']}"


@covers("action function")
def test_action_function_bound_retrieve_user_privileges(cli):
    """A record-bound function: GET systemusers(<id>)/Ns.RetrieveUserPrivileges().

    RetrieveUserPrivileges is a zero-side-effect function bound to a systemuser
    record, present on every org. The current user's id comes from WhoAmI so no
    org-specific GUID is embedded in the test.
    """
    who = json.loads(cli(["--json", "action", "function", "WhoAmI"]).stdout)
    user_id = who["data"]["UserId"]
    result = cli(
        [
            "--json", "action", "function", "RetrieveUserPrivileges",
            "--bind-set", "systemusers", "--bind-id", user_id,
        ]
    )
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"bound RetrieveUserPrivileges failed: {data}"
    assert "RolePrivileges" in data["data"], (
        f"RolePrivileges missing from bound function response: {data['data']}"
    )


@covers("action function")
def test_action_function_record_reference_param(cli, backend, request, unique):
    """A record-reference param ({"@odata.id": ...}) is passed as a parameter
    alias so a reference-taking function is invocable (issue 365).

    RetrievePrincipalAccess is bound to a principal (the current user) and takes
    a Target record reference. A throwaway contact is the Target; both the user
    id and the contact id are resolved at run time so no org GUID is embedded.
    """
    who = json.loads(cli(["--json", "action", "function", "WhoAmI"]).stdout)
    user_id = who["data"]["UserId"]
    created = backend.post(
        "contacts",
        json_body={"firstname": "CLI", "lastname": f"Ref-{unique}"},
        extra_headers={"If-None-Match": "null", "Prefer": "return=representation"},
    )
    contact_id = created["contactid"]
    request.addfinalizer(lambda: _safe_delete(backend, f"contacts({contact_id})"))
    result = cli(
        [
            "--json", "action", "function", "RetrievePrincipalAccess",
            "--bind-set", "systemusers", "--bind-id", user_id,
            "--params", json.dumps({"Target": {"@odata.id": f"contacts({contact_id})"}}),
        ]
    )
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"RetrievePrincipalAccess with record-ref param failed: {data}"
    assert "AccessRights" in data["data"], (
        f"AccessRights missing from RetrievePrincipalAccess response: {data['data']}"
    )


@covers("action invoke")
def test_action_invoke_publish_all_xml(cli):
    """PublishAllXml is a zero-side-effect* unbound OData action on every D365 org.

    `action invoke` issues a POST; PublishAllXml accepts an empty body and
    returns 204 No Content (mapped to ok=True, data={}). It is the safest
    always-present POST-accepting action: it runs a publish even with no pending
    changes (idempotent), returns no payload, and works on both on-prem v9.1
    and Dataverse cloud.

    *Side effect is negligible: publishing with nothing pending is a no-op.
    """
    result = cli(["--json", "action", "invoke", "PublishAllXml"])
    data = json.loads(result.stdout)
    assert data["ok"] is True, f"action invoke PublishAllXml failed: {data}"
    # 204 No Content maps to an empty dict; asserting ok=True is sufficient
    assert isinstance(data["data"], dict), (
        f"expected dict data from PublishAllXml, got: {data['data']}"
    )
