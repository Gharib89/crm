"""Wire-level tests for `crm.core.connectionrole`.

Real ``D365Backend`` + ``requests_mock`` at the transport seam: assert the
request shape (URL, body, headers) the core functions issue and the dicts they
return — never private helpers.
"""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock as rm_module

from crm.core import connectionrole as cr
from crm.utils.d365_backend import D365Error

_ROLE_A = "11112222-3333-4444-5555-666677778888"
_ROLE_B = "99990000-1111-2222-3333-444455556666"
_OTC_ID = "aaaa1111-2222-3333-4444-555566667777"


def _entity_id_headers(backend, entity_set, rec_id):
    return {"OData-EntityId": backend.url_for(f"{entity_set}({rec_id})")}


class TestCreateRole:
    def test_posts_role_and_returns_id(self, backend):
        with rm_module.Mocker() as m:
            m.post(backend.url_for("connectionroles"), status_code=204,
                   headers=_entity_id_headers(backend, "connectionroles", _ROLE_A))
            out = cr.create_role(backend, name="Stakeholder")
        assert out["created"] is True
        assert out["connectionroleid"] == _ROLE_A
        body = m.last_request.json()
        assert body["name"] == "Stakeholder"
        assert "category" not in body

    def test_category_maps_to_option_value_and_solution_header(self, backend):
        with rm_module.Mocker() as m:
            m.post(backend.url_for("connectionroles"), status_code=204,
                   headers=_entity_id_headers(backend, "connectionroles", _ROLE_A))
            out = cr.create_role(
                backend, name="Sales Team", category="sales-team",
                description="d", solution="MySol",
            )
        assert out["category"] == 1001
        assert out["category_name"] == "sales-team"
        body = m.last_request.json()
        assert body["category"] == 1001
        assert body["description"] == "d"
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_unknown_category_raises(self, backend):
        with pytest.raises(D365Error):
            cr.create_role(backend, name="X", category="bogus")

    def test_dry_run_short_circuits(self, dry_backend):
        with rm_module.Mocker():
            out = cr.create_role(dry_backend, name="X")
        assert out["_dry_run"] is True
        assert out["would_create"] is True


class TestScope:
    def test_creates_objecttypecode_bound_to_role(self, backend):
        with rm_module.Mocker() as m:
            m.post(backend.url_for("connectionroleobjecttypecodes"), status_code=204,
                   headers=_entity_id_headers(
                       backend, "connectionroleobjecttypecodes", _OTC_ID))
            out = cr.scope(backend, role=_ROLE_A, entity="account", solution="MySol")
        assert out["created"] is True
        assert out["connectionroleobjecttypecodeid"] == _OTC_ID
        assert out["entity"] == "account"
        body = m.last_request.json()
        assert body["associatedobjecttypecode"] == "account"
        assert body["connectionroleid@odata.bind"] == f"/connectionroles({_ROLE_A})"
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_resolves_role_by_name(self, backend):
        with rm_module.Mocker() as m:
            m.get(
                backend.url_for("connectionroles"),
                json={"value": [{"connectionroleid": _ROLE_A}]},
            )
            m.post(backend.url_for("connectionroleobjecttypecodes"), status_code=204,
                   headers=_entity_id_headers(
                       backend, "connectionroleobjecttypecodes", _OTC_ID))
            out = cr.scope(backend, role="Stakeholder", entity="contact")
        assert out["role"] == _ROLE_A
        body = m.last_request.json()
        assert body["connectionroleid@odata.bind"] == f"/connectionroles({_ROLE_A})"

    def test_dry_run_short_circuits(self, dry_backend):
        with rm_module.Mocker():
            out = cr.scope(dry_backend, role=_ROLE_A, entity="account")
        assert out["_dry_run"] is True
        assert out["would_create"] is True


class TestMatch:
    def test_associates_roles_via_nav_property(self, backend):
        ref_url = backend.url_for(
            f"connectionroles({_ROLE_A})/connectionroleassociation_association/$ref")
        with rm_module.Mocker() as m:
            m.post(ref_url, status_code=204)
            out = cr.match(backend, role_a=_ROLE_A, role_b=_ROLE_B)
        assert out["matched"] is True
        assert out["role_a"] == _ROLE_A
        assert out["role_b"] == _ROLE_B
        assert m.last_request.json()["@odata.id"] == backend.url_for(
            f"connectionroles({_ROLE_B})")

    def test_dry_run_short_circuits(self, dry_backend):
        with rm_module.Mocker():
            out = cr.match(dry_backend, role_a=_ROLE_A, role_b=_ROLE_B)
        assert out["_dry_run"] is True
        assert out["would_match"] is True
