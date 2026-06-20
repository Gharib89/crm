"""Unit tests for crm.core.fieldsec (field-level / column security)."""
# pyright: basic
from __future__ import annotations

from urllib.parse import unquote, unquote_plus

import pytest
import requests_mock

from crm.core import fieldsec
from crm.utils.d365_backend import D365Error

_PROFILE_ID = "11112222-3333-4444-5555-666677778888"
_PROFILE_ROW = {
    "fieldsecurityprofileid": _PROFILE_ID,
    "name": "Comp Profile",
    "description": "Salary access",
}
_USER_ID = "aaaa1111-2222-3333-4444-555566667777"
_TEAM_ID = "bbbb1111-2222-3333-4444-555566667777"
_NEW_PERM_ID = "cccc1111-2222-3333-4444-555566667777"


def _profiles_url(backend) -> str:
    return backend.url_for("fieldsecurityprofiles")


def _perms_url(backend) -> str:
    return backend.url_for("fieldpermissions")


def _entity_id_headers(backend, entity_set: str, rec_id: str) -> dict[str, str]:
    return {"OData-EntityId": backend.url_for(f"{entity_set}({rec_id})")}


# ── resolve_profile_id ──────────────────────────────────────────────────────


class TestResolveProfileId:
    def test_guid_passes_through_without_a_lookup(self, backend):
        with requests_mock.Mocker():  # no GET registered → a lookup would 404
            rid = fieldsec.resolve_profile_id(backend, _PROFILE_ID)
        assert rid == _PROFILE_ID

    def test_name_is_resolved_by_exact_match(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_profiles_url(backend), json={"value": [{"fieldsecurityprofileid": _PROFILE_ID}]})
            rid = fieldsec.resolve_profile_id(backend, "Comp Profile")
        assert rid == _PROFILE_ID
        assert "name eq 'Comp Profile'" in unquote_plus(m.last_request.url)

    def test_unknown_name_raises_not_found(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_profiles_url(backend), json={"value": []})
            with pytest.raises(D365Error) as exc:
                fieldsec.resolve_profile_id(backend, "Nope")
        assert exc.value.code == "NotFound"


# ── list / get ──────────────────────────────────────────────────────────────


class TestListProfiles:
    def test_lists_profiles(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_profiles_url(backend), json={"value": [_PROFILE_ROW]})
            rows = fieldsec.list_profiles(backend)
        assert len(rows) == 1
        assert rows[0]["fieldsecurityprofileid"] == _PROFILE_ID
        assert "$orderby=name" in unquote(m.last_request.url)


class TestGetProfile:
    def test_returns_profile_with_permissions(self, backend):
        perm_row = {
            "fieldpermissionid": _NEW_PERM_ID,
            "entityname": "account",
            "attributelogicalname": "creditlimit",
            "canread": 4, "cancreate": 0, "canupdate": 4,
        }
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"fieldsecurityprofiles({_PROFILE_ID})"), json=_PROFILE_ROW)
            m.get(_perms_url(backend), json={"value": [perm_row]})
            out = fieldsec.get_profile(backend, _PROFILE_ID)
        assert out["name"] == "Comp Profile"
        assert out["permissions"][0]["attributelogicalname"] == "creditlimit"
        # permission GET filters on the profile lookup
        assert "_fieldsecurityprofileid_value" in m.last_request.url


# ── create_profile ──────────────────────────────────────────────────────────


class TestCreateProfile:
    def test_posts_name_and_returns_id(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_profiles_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldsecurityprofiles", _PROFILE_ID))
            out = fieldsec.create_profile(backend, name="Comp Profile", description="x")
        body = m.last_request.json()
        assert body["name"] == "Comp Profile"
        assert body["description"] == "x"
        assert out["created"] is True
        assert out["fieldsecurityprofileid"] == _PROFILE_ID

    def test_omits_description_when_not_given(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_profiles_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldsecurityprofiles", _PROFILE_ID))
            fieldsec.create_profile(backend, name="P")
        assert "description" not in m.last_request.json()

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_profiles_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldsecurityprofiles", _PROFILE_ID))
            fieldsec.create_profile(backend, name="P", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_empty_name_raises(self, backend):
        with pytest.raises(D365Error):
            fieldsec.create_profile(backend, name="")

    def test_dry_run_previews_without_posting(self, dry_backend):
        with requests_mock.Mocker():  # no POST registered → a real call would 404
            out = fieldsec.create_profile(dry_backend, name="P")
        assert out["_dry_run"] is True
        assert out["would_create"] is True


# ── add_permission ──────────────────────────────────────────────────────────


class TestAddPermission:
    def test_maps_grants_to_levels_and_binds_profile(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_perms_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldpermissions", _NEW_PERM_ID))
            out = fieldsec.add_permission(
                backend, profile=_PROFILE_ID, entity="account",
                attribute="creditlimit", read=True, update=True,
            )
        body = m.last_request.json()
        assert body["entityname"] == "account"
        assert body["attributelogicalname"] == "creditlimit"
        assert body["canread"] == 4
        assert body["canupdate"] == 4
        assert body["cancreate"] == 0  # not granted → Not Allowed
        assert body["fieldsecurityprofileid@odata.bind"] == f"/fieldsecurityprofiles({_PROFILE_ID})"
        assert out["created"] is True
        assert out["fieldpermissionid"] == _NEW_PERM_ID

    def test_requires_at_least_one_grant(self, backend):
        with pytest.raises(D365Error):
            fieldsec.add_permission(
                backend, profile=_PROFILE_ID, entity="account", attribute="creditlimit",
            )

    def test_resolves_profile_by_name(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_profiles_url(backend), json={"value": [{"fieldsecurityprofileid": _PROFILE_ID}]})
            m.post(_perms_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldpermissions", _NEW_PERM_ID))
            out = fieldsec.add_permission(
                backend, profile="Comp Profile", entity="account",
                attribute="creditlimit", read=True,
            )
        assert out["profile"] == _PROFILE_ID

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_perms_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldpermissions", _NEW_PERM_ID))
            fieldsec.add_permission(
                backend, profile=_PROFILE_ID, entity="account",
                attribute="creditlimit", read=True, solution="MySol",
            )
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_dry_run_previews_without_posting(self, dry_backend):
        with requests_mock.Mocker():
            out = fieldsec.add_permission(
                dry_backend, profile=_PROFILE_ID, entity="account",
                attribute="creditlimit", read=True,
            )
        assert out["_dry_run"] is True
        assert out["would_create"] is True


# ── assign ──────────────────────────────────────────────────────────────────


class TestAssign:
    def test_assigns_to_user_via_user_nav(self, backend):
        ref_url = backend.url_for(
            f"fieldsecurityprofiles({_PROFILE_ID})/systemuserprofiles_association/$ref")
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            out = fieldsec.assign(backend, profile=_PROFILE_ID, user_id=_USER_ID)
        body = m.last_request.json()
        assert body["@odata.id"] == backend.url_for(f"systemusers({_USER_ID})")
        assert out["assigned"] is True
        assert out["principal_type"] == "user"
        assert out["principal_id"] == _USER_ID

    def test_assigns_to_team_via_team_nav(self, backend):
        ref_url = backend.url_for(
            f"fieldsecurityprofiles({_PROFILE_ID})/teamprofiles_association/$ref")
        with requests_mock.Mocker() as m:
            m.post(ref_url, status_code=204)
            out = fieldsec.assign(backend, profile=_PROFILE_ID, team_id=_TEAM_ID)
        assert m.last_request.json()["@odata.id"] == backend.url_for(f"teams({_TEAM_ID})")
        assert out["principal_type"] == "team"

    def test_rejects_both_user_and_team(self, backend):
        with pytest.raises(D365Error):
            fieldsec.assign(backend, profile=_PROFILE_ID, user_id=_USER_ID, team_id=_TEAM_ID)

    def test_rejects_neither_user_nor_team(self, backend):
        with pytest.raises(D365Error):
            fieldsec.assign(backend, profile=_PROFILE_ID)
