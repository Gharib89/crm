# pyright: basic
"""Unit tests for crm.core.security — role listing + assignment delegation.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""

from __future__ import annotations

import json

import pytest
import requests_mock

from crm.utils.d365_backend import D365Error
from crm.core import security as sec
from crm.core.entity_names import NameMap

# ── Constants ────────────────────────────────────────────────────────────

_GUID = "11111111-2222-3333-4444-555555555555"
_ROLE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_BU_ID = "cccccccc-dddd-eeee-ffff-000000000000"
_RECORD_ID = "dddddddd-1111-2222-3333-444444444444"
_PRINCIPAL_ID = "eeeeeeee-1111-2222-3333-555555555555"


def _stub_name_map(monkeypatch):
    """Resolve the ``accounts`` entity set without a live EntityDefinitions GET."""
    name_map = NameMap(
        logical_to_set={"account": "accounts"},
        set_to_logical={"accounts": "account"},
        primary_id={"account": "accountid"},
    )
    monkeypatch.setattr(sec.entity_names, "load_name_map", lambda backend, **kw: name_map)

# ── list_roles ───────────────────────────────────────────────────────────


class TestListRoles:
    def test_no_filter_requests_select_and_orderby(self, backend):
        mock_roles = [{"roleid": _ROLE_ID, "name": "System Administrator"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": mock_roles})
            result = sec.list_roles(backend)
        assert result == mock_roles
        qs = m.request_history[0].qs
        assert "roleid,name,_businessunitid_value" in qs["$select"][0]
        assert qs["$orderby"] == ["name"]
        assert "$filter" not in qs

    def test_with_business_unit_adds_filter(self, backend):
        mock_roles = [{"roleid": _ROLE_ID, "name": "Salesperson"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": mock_roles})
            result = sec.list_roles(backend, business_unit=_BU_ID)
        assert result == mock_roles
        qs = m.request_history[0].qs
        assert qs["$filter"][0] == f"_businessunitid_value eq {_BU_ID}"

    def test_business_unit_non_canonical_normalized_in_filter(self, backend):
        """Braced/uppercase GUIDs must be normalised to canonical form in the OData filter."""
        braced_upper = "{CCCCCCCC-DDDD-EEEE-FFFF-000000000000}"
        canonical = "cccccccc-dddd-eeee-ffff-000000000000"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": []})
            sec.list_roles(backend, business_unit=braced_upper)
        qs = m.request_history[0].qs
        assert qs["$filter"][0] == f"_businessunitid_value eq {canonical}"

    def test_invalid_business_unit_raises_d365error(self, backend):
        with pytest.raises(D365Error, match="business_unit must be a GUID"):
            sec.list_roles(backend, business_unit="not-a-guid")

    def test_returns_empty_list_when_no_value(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={})
            result = sec.list_roles(backend)
        assert result == []


# ── list_user_roles ──────────────────────────────────────────────────────


class TestListUserRoles:
    def test_hits_correct_nav_path(self, backend):
        expected_path = f"systemusers({_GUID})/systemuserroles_association"
        mock_roles = [{"roleid": _ROLE_ID, "name": "Salesperson"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(expected_path), json={"value": mock_roles})
            result = sec.list_user_roles(backend, _GUID)
        assert result == mock_roles
        assert expected_path in m.request_history[0].url

    def test_sends_select_and_orderby(self, backend):
        path = f"systemusers({_GUID})/systemuserroles_association"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path), json={"value": []})
            sec.list_user_roles(backend, _GUID)
        qs = m.request_history[0].qs
        assert "roleid" in qs["$select"][0]
        assert qs["$orderby"] == ["name"]

    def test_returns_value_list(self, backend):
        path = f"systemusers({_GUID})/systemuserroles_association"
        mock_roles = [{"roleid": _ROLE_ID, "name": "Admin"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path), json={"value": mock_roles})
            result = sec.list_user_roles(backend, _GUID)
        assert result == mock_roles


# ── list_team_roles ──────────────────────────────────────────────────────


class TestListTeamRoles:
    def test_hits_correct_nav_path(self, backend):
        expected_path = f"teams({_GUID})/teamroles_association"
        mock_roles = [{"roleid": _ROLE_ID, "name": "Sales Team Role"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(expected_path), json={"value": mock_roles})
            result = sec.list_team_roles(backend, _GUID)
        assert result == mock_roles
        assert expected_path in m.request_history[0].url

    def test_sends_select_and_orderby(self, backend):
        path = f"teams({_GUID})/teamroles_association"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path), json={"value": []})
            sec.list_team_roles(backend, _GUID)
        qs = m.request_history[0].qs
        assert "roleid" in qs["$select"][0]
        assert qs["$orderby"] == ["name"]

    def test_returns_value_list(self, backend):
        path = f"teams({_GUID})/teamroles_association"
        mock_roles = [{"roleid": _ROLE_ID, "name": "Team Admin"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path), json={"value": mock_roles})
            result = sec.list_team_roles(backend, _GUID)
        assert result == mock_roles


# ── list_user_privileges ─────────────────────────────────────────────────


_PRIVILEGE = {
    "Depth": "Global",
    "PrivilegeId": "99999999-8888-7777-6666-555555555555",
    "BusinessUnitId": _BU_ID,
    "PrivilegeName": "prvReadAccount",
    "RecordFilterId": "00000000-0000-0000-0000-000000000000",
    "RecordFilterUniqueName": "",
}

_PRIVILEGES_FN = "Microsoft.Dynamics.CRM.RetrieveUserPrivileges"


class TestListUserPrivileges:
    def test_hits_bound_function_path(self, backend):
        path = f"systemusers({_GUID})/{_PRIVILEGES_FN}"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path), json={"RolePrivileges": [_PRIVILEGE]})
            result = sec.list_user_privileges(backend, _GUID)
        assert result == [_PRIVILEGE]
        assert path in m.request_history[0].url

    def test_unwraps_roleprivileges(self, backend):
        path = f"systemusers({_GUID})/{_PRIVILEGES_FN}"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path),
                  json={"RolePrivileges": [_PRIVILEGE, _PRIVILEGE]})
            result = sec.list_user_privileges(backend, _GUID)
        assert result == [_PRIVILEGE, _PRIVILEGE]

    def test_returns_empty_list_when_absent(self, backend):
        path = f"systemusers({_GUID})/{_PRIVILEGES_FN}"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(path), json={})
            result = sec.list_user_privileges(backend, _GUID)
        assert result == []

    def test_invalid_user_id_raises_d365error(self, backend):
        with pytest.raises(D365Error):
            sec.list_user_privileges(backend, "not-a-guid")


# ── assign_role_to_user ──────────────────────────────────────────────────


class TestAssignRoleToUser:
    def test_posts_to_ref_url(self, backend):
        ref_url = backend.url_for(
            f"systemusers({_GUID})/systemuserroles_association/$ref"
        )
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            sec.assign_role_to_user(backend, _GUID, _ROLE_ID)
        assert "/systemuserroles_association/$ref" in m.request_history[0].url

    def test_body_odata_id_ends_with_role(self, backend):
        ref_url = backend.url_for(
            f"systemusers({_GUID})/systemuserroles_association/$ref"
        )
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            sec.assign_role_to_user(backend, _GUID, _ROLE_ID)
        body = json.loads(m.request_history[0].body)
        assert body["@odata.id"].endswith(f"roles({_ROLE_ID})")

    def test_returns_dict(self, backend):
        ref_url = backend.url_for(
            f"systemusers({_GUID})/systemuserroles_association/$ref"
        )
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            result = sec.assign_role_to_user(backend, _GUID, _ROLE_ID)
        assert result.get("associated") is True


# ── assign_role_to_team ──────────────────────────────────────────────────


class TestAssignRoleToTeam:
    def test_posts_to_ref_url(self, backend):
        ref_url = backend.url_for(
            f"teams({_GUID})/teamroles_association/$ref"
        )
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            sec.assign_role_to_team(backend, _GUID, _ROLE_ID)
        assert "/teamroles_association/$ref" in m.request_history[0].url

    def test_body_odata_id_ends_with_role(self, backend):
        ref_url = backend.url_for(
            f"teams({_GUID})/teamroles_association/$ref"
        )
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            sec.assign_role_to_team(backend, _GUID, _ROLE_ID)
        body = json.loads(m.request_history[0].body)
        assert body["@odata.id"].endswith(f"roles({_ROLE_ID})")

    def test_returns_dict(self, backend):
        ref_url = backend.url_for(
            f"teams({_GUID})/teamroles_association/$ref"
        )
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            result = sec.assign_role_to_team(backend, _GUID, _ROLE_ID)
        assert result.get("associated") is True


# ── grant_access ───────────────────────────────────────────────────────────


class TestGrantAccess:
    def test_posts_to_grantaccess_with_target_principal_and_mask(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("GrantAccess"), status_code=204)
            sec.grant_access(
                backend, "accounts", _RECORD_ID,
                principal_type="user", principal_id=_PRINCIPAL_ID, rights="Read,Write",
            )
        body = json.loads(m.request_history[0].body)
        assert body["Target"] == {
            "accountid": _RECORD_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.account",
        }
        assert body["PrincipalAccess"]["Principal"] == {
            "systemuserid": _PRINCIPAL_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.systemuser",
        }
        assert body["PrincipalAccess"]["AccessMask"] == "ReadAccess, WriteAccess"

    def test_team_principal_uses_team_logical_and_key(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("GrantAccess"), status_code=204)
            sec.grant_access(
                backend, "accounts", _RECORD_ID,
                principal_type="team", principal_id=_PRINCIPAL_ID, rights="Read",
            )
        principal = json.loads(m.request_history[0].body)["PrincipalAccess"]["Principal"]
        assert principal == {
            "teamid": _PRINCIPAL_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.team",
        }

    def test_returns_granted_true_on_204(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("GrantAccess"), status_code=204)
            result = sec.grant_access(
                backend, "accounts", _RECORD_ID,
                principal_type="user", principal_id=_PRINCIPAL_ID, rights="Read",
            )
        assert result == {"granted": True}

    def test_rights_are_case_insensitive_and_deduped(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("GrantAccess"), status_code=204)
            sec.grant_access(
                backend, "accounts", _RECORD_ID,
                principal_type="user", principal_id=_PRINCIPAL_ID,
                rights="read, WRITE, ReadAccess",
            )
        mask = json.loads(m.request_history[0].body)["PrincipalAccess"]["AccessMask"]
        assert mask == "ReadAccess, WriteAccess"

    def test_unknown_right_raises_d365error(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with pytest.raises(D365Error, match="unknown access right 'Fly'"):
            sec.grant_access(
                backend, "accounts", _RECORD_ID,
                principal_type="user", principal_id=_PRINCIPAL_ID, rights="Read,Fly",
            )

    def test_unknown_principal_type_raises_d365error(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with pytest.raises(D365Error, match="unknown principal type 'robot'"):
            sec.grant_access(
                backend, "accounts", _RECORD_ID,
                principal_type="robot", principal_id=_PRINCIPAL_ID, rights="Read",
            )

    def test_invalid_record_id_raises_d365error(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with pytest.raises(D365Error, match="record id must be a GUID"):
            sec.grant_access(
                backend, "accounts", "not-a-guid",
                principal_type="user", principal_id=_PRINCIPAL_ID, rights="Read",
            )


# ── revoke_access ──────────────────────────────────────────────────────────


class TestRevokeAccess:
    def test_posts_to_revokeaccess_with_target_and_revokee(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("RevokeAccess"), status_code=204)
            sec.revoke_access(
                backend, "accounts", _RECORD_ID,
                principal_type="user", principal_id=_PRINCIPAL_ID,
            )
        body = json.loads(m.request_history[0].body)
        assert body["Target"] == {
            "accountid": _RECORD_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.account",
        }
        assert body["Revokee"] == {
            "systemuserid": _PRINCIPAL_ID,
            "@odata.type": "Microsoft.Dynamics.CRM.systemuser",
        }
        assert "PrincipalAccess" not in body

    def test_returns_revoked_true_on_204(self, backend, monkeypatch):
        _stub_name_map(monkeypatch)
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("RevokeAccess"), status_code=204)
            result = sec.revoke_access(
                backend, "accounts", _RECORD_ID,
                principal_type="user", principal_id=_PRINCIPAL_ID,
            )
        assert result == {"revoked": True}


# ── list_access ────────────────────────────────────────────────────────────


_SHARED_RESPONSE = {
    "PrincipalAccesses": [
        {
            "AccessMask": "ReadAccess, WriteAccess",
            "Principal": {
                "@odata.type": "#Microsoft.Dynamics.CRM.systemuser",
                "ownerid": _PRINCIPAL_ID,
            },
        },
    ]
}


class TestListAccess:
    def test_calls_function_with_target_alias(self, backend):
        url = backend.url_for("RetrieveSharedPrincipalsAndAccess(Target=@p1)")
        with requests_mock.Mocker() as m:
            m.get(url, json=_SHARED_RESPONSE)
            sec.list_access(backend, "accounts", _RECORD_ID)
        alias = m.request_history[0].qs["@p1"][0]
        assert json.loads(alias) == {"@odata.id": f"accounts({_RECORD_ID})"}

    def test_normalizes_principal_type_id_and_mask(self, backend):
        url = backend.url_for("RetrieveSharedPrincipalsAndAccess(Target=@p1)")
        with requests_mock.Mocker() as m:
            m.get(url, json=_SHARED_RESPONSE)
            result = sec.list_access(backend, "accounts", _RECORD_ID)
        assert result == [{
            "principalType": "systemuser",
            "principalId": _PRINCIPAL_ID,
            "accessMask": "ReadAccess, WriteAccess",
        }]

    def test_returns_empty_list_when_no_shares(self, backend):
        url = backend.url_for("RetrieveSharedPrincipalsAndAccess(Target=@p1)")
        with requests_mock.Mocker() as m:
            m.get(url, json={})
            result = sec.list_access(backend, "accounts", _RECORD_ID)
        assert result == []

    def test_invalid_record_id_raises_d365error(self, backend):
        with pytest.raises(D365Error, match="record id must be a GUID"):
            sec.list_access(backend, "accounts", "not-a-guid")
