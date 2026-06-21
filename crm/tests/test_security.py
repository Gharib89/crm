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

    def test_name_contains_adds_contains_filter(self, backend):
        """--name-contains should produce a server-side contains(name,'…') clause."""
        mock_roles = [{"roleid": _ROLE_ID, "name": "Salesperson"}]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": mock_roles})
            result = sec.list_roles(backend, name_contains="Sales")
        assert result == mock_roles
        # requests_mock .qs lowercases values; parse the raw URL to preserve case.
        from urllib.parse import urlparse, parse_qs, unquote
        raw_url = unquote(m.request_history[0].url)
        filt = parse_qs(urlparse(raw_url).query)["$filter"][0]
        assert filt == "contains(name,'Sales')"

    def test_name_contains_composes_with_business_unit(self, backend):
        """Both filters should AND-join in the $filter clause."""
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": []})
            sec.list_roles(backend, name_contains="Admin", business_unit=_BU_ID)
        from urllib.parse import urlparse, parse_qs, unquote
        raw_url = unquote(m.request_history[0].url)
        filt = parse_qs(urlparse(raw_url).query)["$filter"][0]
        assert "contains(name,'Admin')" in filt
        assert f"_businessunitid_value eq {_BU_ID}" in filt
        assert " and " in filt

    def test_name_contains_escapes_single_quotes(self, backend):
        """Single quotes in the search term must be doubled per OData escaping."""
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": []})
            sec.list_roles(backend, name_contains="it's")
        from urllib.parse import urlparse, parse_qs, unquote
        raw_url = unquote(m.request_history[0].url)
        filt = parse_qs(urlparse(raw_url).query)["$filter"][0]
        assert filt == "contains(name,'it''s')"


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


# ── create_role ────────────────────────────────────────────────────────────


# Opaque placeholder privilege ids — the mocked tests only assert the request
# shape, so these need not be real (project rule: no live-export GUIDs).
_PRV_READ = "11111111-0000-0000-0000-00000000000a"
_PRV_WRITE = "11111111-0000-0000-0000-00000000000b"
_PRV_CREATE = "11111111-0000-0000-0000-00000000000c"
_PRV_GLOBAL_ONLY = "11111111-0000-0000-0000-00000000000d"

_ADD_ACTION = "Microsoft.Dynamics.CRM.AddPrivilegesRole"
_REPLACE_ACTION = "Microsoft.Dynamics.CRM.ReplacePrivilegesRole"


def _meta_priv(name, ptype, *, basic=True, local=True, deep=True, glob=True, pid=None):
    """One EntityMetadata.Privileges entry (PascalCase metadata shape)."""
    return {
        "CanBeBasic": basic, "CanBeLocal": local, "CanBeDeep": deep, "CanBeGlobal": glob,
        "Name": name, "PrivilegeId": pid or _PRV_READ, "PrivilegeType": ptype,
    }


def _entity_priv(name, pid, *, basic=True, local=True, deep=True, glob=True):
    """One `privileges` entity row (lower-case entity shape)."""
    return {
        "name": name, "privilegeid": pid,
        "canbebasic": basic, "canbelocal": local, "canbedeep": deep, "canbeglobal": glob,
    }


class TestCreateRole:
    def test_defaults_business_unit_to_caller(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("WhoAmI"), json={"BusinessUnitId": _BU_ID})
            m.get(backend.url_for("roles"), json={"value": []})
            m.post(backend.url_for("roles"),
                   json={"roleid": _ROLE_ID, "name": "Agent Read-Only"})
            result = sec.create_role(backend, "Agent Read-Only")
        assert result == {"roleid": _ROLE_ID, "name": "Agent Read-Only",
                          "businessunitid": _BU_ID}
        body = json.loads(m.request_history[-1].body)
        assert body["name"] == "Agent Read-Only"
        assert body["businessunitid@odata.bind"] == f"/businessunits({_BU_ID})"

    def test_explicit_business_unit_is_normalized(self, backend):
        braced = "{CCCCCCCC-DDDD-EEEE-FFFF-000000000000}"
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": []})
            m.post(backend.url_for("roles"), json={"roleid": _ROLE_ID, "name": "R"})
            sec.create_role(backend, "R", business_unit=braced)
        body = json.loads(m.request_history[-1].body)
        assert body["businessunitid@odata.bind"] == f"/businessunits({_BU_ID})"

    def test_invalid_business_unit_raises(self, backend):
        with pytest.raises(D365Error, match="business_unit must be a GUID"):
            sec.create_role(backend, "R", business_unit="nope")

    def test_existing_name_errors_by_default(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": [{"roleid": _ROLE_ID}]})
            with pytest.raises(D365Error, match="already exists"):
                sec.create_role(backend, "Dup", business_unit=_BU_ID)

    def test_if_exists_skip_returns_existing(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": [{"roleid": _ROLE_ID}]})
            result = sec.create_role(backend, "Dup", business_unit=_BU_ID,
                                     if_exists="skip")
        assert result == {"roleid": _ROLE_ID, "name": "Dup",
                          "businessunitid": _BU_ID, "existed": True}

    def test_dry_run_returns_would_create_without_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("WhoAmI"), json={"BusinessUnitId": _BU_ID})
            m.get(dry_backend.url_for("roles"), json={"value": []})
            result = sec.create_role(dry_backend, "Agent Read-Only")
        assert result["_dry_run"] is True
        assert result["would_create"]["entity_set"] == "roles"
        assert result["would_create"]["body"]["name"] == "Agent Read-Only"
        assert all(h.method != "POST" for h in m.request_history)


# ── set_role_privileges ──────────────────────────────────────────────────────


def _action_url(backend, action):
    return backend.url_for(f"roles({_ROLE_ID})/{action}")


class TestSetRolePrivileges:
    def test_access_entities_builds_clamped_body(self, backend):
        privs = [
            _meta_priv("prvReadAccount", "Read", pid=_PRV_READ),
            _meta_priv("prvWriteAccount", "Write", pid=_PRV_WRITE),
        ]
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": privs})
            m.post(_action_url(backend, _ADD_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, _ROLE_ID, access=["read", "write"], entities=["account"],
                depth="global",
            )
        assert result["mode"] == "add"
        assert result["count"] == 2
        body = json.loads(m.request_history[-1].body)
        sent = {p["PrivilegeId"]: p["Depth"] for p in body["Privileges"]}
        assert sent == {_PRV_READ: "Global", _PRV_WRITE: "Global"}

    def test_replace_uses_replace_action(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": [_meta_priv("prvReadAccount", "Read", pid=_PRV_READ)]})
            m.post(_action_url(backend, _REPLACE_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, _ROLE_ID, access=["read"], entities=["account"],
                depth="global", replace=True,
            )
        assert result["mode"] == "replace"
        assert _REPLACE_ACTION in m.request_history[-1].url

    def test_depth_clamped_to_supported_with_warning(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("privileges"),
                  json={"value": [_entity_priv("prvCreateEntity", _PRV_GLOBAL_ONLY,
                                               basic=False, local=False, deep=False,
                                               glob=True)]})
            m.post(_action_url(backend, _ADD_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, _ROLE_ID, privilege_names=["prvCreateEntity"], depth="basic",
            )
        body = json.loads(m.request_history[-1].body)
        assert body["Privileges"][0]["Depth"] == "Global"
        assert any("clamped" in w for w in result["warnings"])

    def test_all_entities_filters_by_accessright_bitmask(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("privileges"),
                  json={"value": [_entity_priv("prvReadAccount", _PRV_READ),
                                  _entity_priv("prvReadContact", _PRV_WRITE)]})
            m.post(_action_url(backend, _ADD_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, _ROLE_ID, access=["read"], all_entities=True, depth="global",
            )
        assert result["count"] == 2
        qs = m.request_history[0].qs
        assert qs["$filter"][0] == "accessright eq 1"

    def test_missing_access_for_entity_is_skipped_with_warning(self, backend):
        # account metadata exposes only Read here — requesting assign skips + warns.
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": [_meta_priv("prvReadAccount", "Read", pid=_PRV_READ)]})
            m.post(_action_url(backend, _ADD_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, _ROLE_ID, access=["read", "assign"], entities=["account"],
                depth="global",
            )
        assert result["count"] == 1
        assert any("no assign privilege" in w for w in result["warnings"])

    def test_all_skipped_raises_empty(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": [_meta_priv("prvReadAccount", "Read", pid=_PRV_READ)]})
            with pytest.raises(D365Error, match="no privileges resolved"):
                sec.set_role_privileges(
                    backend, _ROLE_ID, access=["assign"], entities=["account"],
                    depth="global",
                )

    def test_unknown_privilege_name_raises(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("privileges"), json={"value": []})
            with pytest.raises(D365Error, match="unknown privilege"):
                sec.set_role_privileges(
                    backend, _ROLE_ID, privilege_names=["prvNope"], depth="global",
                )

    def test_unknown_entity_raises(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions(LogicalName='nope')"),
                  status_code=404, json={"error": {"message": "Not found"}})
            with pytest.raises(D365Error, match="unknown entity"):
                sec.set_role_privileges(
                    backend, _ROLE_ID, access=["read"], entities=["nope"], depth="global",
                )

    def test_access_without_entity_scope_raises(self, backend):
        with pytest.raises(D365Error, match="requires an entity scope"):
            sec.set_role_privileges(backend, _ROLE_ID, access=["read"], depth="global")

    def test_entities_and_all_entities_mutually_exclusive(self, backend):
        with pytest.raises(D365Error, match="not both"):
            sec.set_role_privileges(
                backend, _ROLE_ID, access=["read"], entities=["account"],
                all_entities=True, depth="global",
            )

    def test_no_selectors_raises(self, backend):
        with pytest.raises(D365Error, match="at least one selector"):
            sec.set_role_privileges(backend, _ROLE_ID, depth="global")

    def test_unknown_access_raises(self, backend):
        with pytest.raises(D365Error, match="unknown access type"):
            sec.set_role_privileges(
                backend, _ROLE_ID, access=["fly"], entities=["account"], depth="global",
            )

    def test_unknown_depth_raises(self, backend):
        with pytest.raises(D365Error, match="unknown depth"):
            sec.set_role_privileges(
                backend, _ROLE_ID, access=["read"], entities=["account"], depth="cosmic",
            )

    def test_role_name_resolved_to_id(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"), json={"value": [{"roleid": _ROLE_ID}]})
            m.get(backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": [_meta_priv("prvReadAccount", "Read", pid=_PRV_READ)]})
            m.post(_action_url(backend, _ADD_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, "Agent Read-Only", access=["read"], entities=["account"],
                depth="global",
            )
        assert result["roleid"] == _ROLE_ID

    def test_ambiguous_role_name_raises(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("roles"),
                  json={"value": [{"roleid": _ROLE_ID}, {"roleid": _GUID}]})
            with pytest.raises(D365Error, match="ambiguous"):
                sec.set_role_privileges(
                    backend, "Dup Role", access=["read"], entities=["account"],
                    depth="global",
                )

    def test_dry_run_returns_would_apply_without_post(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": [_meta_priv("prvReadAccount", "Read", pid=_PRV_READ)]})
            result = sec.set_role_privileges(
                dry_backend, _ROLE_ID, access=["read"], entities=["account"],
                depth="global",
            )
        assert result["count"] == 1
        assert result["_dry_run"] is True
        assert result["would_apply"] == {"action": "add", "count": 1}
        assert "applied" not in result
        assert all(h.method != "POST" for h in m.request_history)

    def test_depth_alias_organization_maps_to_global(self, backend):
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"Privileges": [_meta_priv("prvReadAccount", "Read", pid=_PRV_READ)]})
            m.post(_action_url(backend, _ADD_ACTION), status_code=204)
            result = sec.set_role_privileges(
                backend, _ROLE_ID, access=["read"], entities=["account"],
                depth="organization",
            )
        assert result["depth"] == "Global"
        assert json.loads(m.request_history[-1].body)["Privileges"][0]["Depth"] == "Global"
