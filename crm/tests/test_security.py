# pyright: basic
"""Unit tests for crm.core.security — role listing + assignment delegation.

All HTTP is mocked via `requests_mock`. No live D365 server needed.
"""

from __future__ import annotations

import json

import pytest
import requests_mock

from crm.utils.d365_backend import ConnectionProfile, D365Backend
from crm.core import security as sec

# ── Constants ────────────────────────────────────────────────────────────

_GUID = "11111111-2222-3333-4444-555555555555"
_ROLE_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_BU_ID = "cccccccc-dddd-eeee-ffff-000000000000"

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


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
