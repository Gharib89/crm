"""Unit tests for crm.core.dup (duplicate-detection rules)."""
# pyright: basic
from __future__ import annotations

from urllib.parse import unquote, unquote_plus

import pytest
import requests_mock

from crm.core import dup
from crm.utils.d365_backend import D365Error

_RULE_ID = "11112222-3333-4444-5555-666677778888"
_COND_ID = "aaaa1111-2222-3333-4444-555566667777"
_RULE_ROW = {
    "duplicateruleid": _RULE_ID,
    "name": "Accounts by name",
    "baseentityname": "account",
    "matchingentityname": "account",
    "statuscode": 0,
    "statecode": 0,
}


def _rules_url(backend) -> str:
    return backend.url_for("duplicaterules")


def _conditions_url(backend) -> str:
    return backend.url_for("duplicateruleconditions")


def _entity_id_headers(backend, entity_set: str, rec_id: str) -> dict[str, str]:
    return {"OData-EntityId": backend.url_for(f"{entity_set}({rec_id})")}


# ── resolve_rule_id ──────────────────────────────────────────────────────────


class TestResolveRuleId:
    def test_guid_passes_through_without_a_lookup(self, backend):
        with requests_mock.Mocker():  # no GET registered → a lookup would 404
            rid = dup.resolve_rule_id(backend, _RULE_ID)
        assert rid == _RULE_ID

    def test_name_is_resolved_by_exact_match(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_rules_url(backend), json={"value": [{"duplicateruleid": _RULE_ID}]})
            rid = dup.resolve_rule_id(backend, "Accounts by name")
        assert rid == _RULE_ID
        assert "name eq 'Accounts by name'" in unquote_plus(m.last_request.url)

    def test_unknown_name_raises_not_found(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_rules_url(backend), json={"value": []})
            with pytest.raises(D365Error) as exc:
                dup.resolve_rule_id(backend, "Nope")
        assert exc.value.code == "NotFound"


# ── list / get ──────────────────────────────────────────────────────────────


class TestListRules:
    def test_lists_rules(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_rules_url(backend), json={"value": [_RULE_ROW]})
            rows = dup.list_rules(backend)
        assert len(rows) == 1
        assert rows[0]["duplicateruleid"] == _RULE_ID
        assert "$orderby=name" in unquote(m.last_request.url)

    def test_filters_by_base_entity(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_rules_url(backend), json={"value": [_RULE_ROW]})
            dup.list_rules(backend, entity="account")
        assert "baseentityname eq 'account'" in unquote_plus(m.last_request.url)


class TestGetRule:
    def test_returns_rule_with_conditions(self, backend):
        cond_row = {
            "duplicateruleconditionid": _COND_ID,
            "baseattributename": "name",
            "matchingattributename": "name",
            "operatorcode": 0,
            "operatorparam": None,
        }
        with requests_mock.Mocker() as m:
            m.get(backend.url_for(f"duplicaterules({_RULE_ID})"), json=_RULE_ROW)
            m.get(_conditions_url(backend), json={"value": [cond_row]})
            out = dup.get_rule(backend, _RULE_ID)
        assert out["name"] == "Accounts by name"
        assert out["conditions"][0]["baseattributename"] == "name"
        # conditions GET filters on the rule lookup (the regardingobjectid lookup)
        assert "_regardingobjectid_value" in m.last_request.url


# ── create_rule ──────────────────────────────────────────────────────────────


class TestCreateRule:
    def test_posts_rule_and_returns_id(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_rules_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicaterules", _RULE_ID))
            out = dup.create_rule(backend, name="Accounts by name", entity="account")
        body = m.last_request.json()
        assert body["name"] == "Accounts by name"
        assert body["baseentityname"] == "account"
        # matching entity defaults to the base entity
        assert body["matchingentityname"] == "account"
        assert out["created"] is True
        assert out["duplicateruleid"] == _RULE_ID

    def test_matching_entity_override(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_rules_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicaterules", _RULE_ID))
            dup.create_rule(
                backend, name="Lead↔Contact", entity="lead",
                matching_entity="contact",
            )
        body = m.last_request.json()
        assert body["baseentityname"] == "lead"
        assert body["matchingentityname"] == "contact"

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_rules_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicaterules", _RULE_ID))
            dup.create_rule(backend, name="R", entity="account", solution="MySol")
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_empty_name_raises(self, backend):
        with pytest.raises(D365Error):
            dup.create_rule(backend, name="", entity="account")

    def test_empty_entity_raises(self, backend):
        with pytest.raises(D365Error):
            dup.create_rule(backend, name="R", entity="")

    def test_unparsable_id_surfaces_lookup_error(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_rules_url(backend), status_code=204)  # no OData-EntityId header
            out = dup.create_rule(backend, name="R", entity="account")
        assert out["created"] is True
        assert out["duplicateruleid"] is None
        assert "duplicaterule_lookup_error" in out

    def test_dry_run_previews_without_posting(self, dry_backend):
        with requests_mock.Mocker():  # no POST registered → a real call would 404
            out = dup.create_rule(dry_backend, name="R", entity="account")
        assert out["_dry_run"] is True
        assert out["would_create"] is True


# ── add_condition ─────────────────────────────────────────────────────────────


class TestAddCondition:
    def test_posts_condition_and_binds_rule(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_conditions_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID))
            out = dup.add_condition(
                backend, rule=_RULE_ID, attribute="name", operator="exact",
            )
        body = m.last_request.json()
        assert body["baseattributename"] == "name"
        assert body["matchingattributename"] == "name"
        assert body["operatorcode"] == 0
        assert body["regardingobjectid@odata.bind"] == f"/duplicaterules({_RULE_ID})"
        # ExactMatch must not carry an operatorparam
        assert "operatorparam" not in body
        assert out["created"] is True
        assert out["duplicateruleconditionid"] == _COND_ID

    def test_matching_attribute_override(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_conditions_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID))
            dup.add_condition(
                backend, rule=_RULE_ID, attribute="emailaddress1",
                matching_attribute="emailaddress2", operator="exact",
            )
        body = m.last_request.json()
        assert body["baseattributename"] == "emailaddress1"
        assert body["matchingattributename"] == "emailaddress2"

    def test_same_first_requires_operator_param(self, backend):
        with pytest.raises(D365Error):
            dup.add_condition(
                backend, rule=_RULE_ID, attribute="name", operator="same-first",
            )

    def test_same_first_sends_operator_param(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_conditions_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID))
            dup.add_condition(
                backend, rule=_RULE_ID, attribute="name", operator="same-first",
                operator_param=5,
            )
        body = m.last_request.json()
        assert body["operatorcode"] == 1
        assert body["operatorparam"] == 5

    def test_exact_rejects_operator_param(self, backend):
        with pytest.raises(D365Error):
            dup.add_condition(
                backend, rule=_RULE_ID, attribute="name", operator="exact",
                operator_param=5,
            )

    def test_unknown_operator_raises(self, backend):
        with pytest.raises(D365Error):
            dup.add_condition(
                backend, rule=_RULE_ID, attribute="name", operator="bogus",
            )

    def test_resolves_rule_by_name(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_rules_url(backend), json={"value": [{"duplicateruleid": _RULE_ID}]})
            m.post(_conditions_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID))
            out = dup.add_condition(
                backend, rule="Accounts by name", attribute="name", operator="exact",
            )
        assert out["rule"] == _RULE_ID

    def test_adds_solution_header(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_conditions_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID))
            dup.add_condition(
                backend, rule=_RULE_ID, attribute="name", operator="exact",
                solution="MySol",
            )
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_dry_run_previews_without_posting(self, dry_backend):
        with requests_mock.Mocker():
            out = dup.add_condition(
                dry_backend, rule=_RULE_ID, attribute="name", operator="exact",
            )
        assert out["_dry_run"] is True
        assert out["would_create"] is True


# ── publish_rule (async) ──────────────────────────────────────────────────────


_JOB_ID = "dddd1111-2222-3333-4444-555566667777"


def _publish_url(backend) -> str:
    return backend.url_for(
        f"duplicaterules({_RULE_ID})/Microsoft.Dynamics.CRM.PublishDuplicateRule")


class TestPublishRule:
    def test_submits_and_returns_job_id(self, backend):
        # PublishDuplicateRule returns the asyncoperation record inline; the job
        # id is its asyncoperationid.
        with requests_mock.Mocker() as m:
            m.post(_publish_url(backend), status_code=200,
                   json={"asyncoperationid": _JOB_ID, "statecode": 0, "statuscode": 0})
            out = dup.publish_rule(backend, _RULE_ID)
        # submitted only — the async build job has not completed, so not yet active
        assert out["published"] is False
        assert out["job_id"] == _JOB_ID
        assert out["status"] == "submitted"

    def test_wait_polls_async_operation(self, backend):
        with requests_mock.Mocker() as m:
            m.post(_publish_url(backend), status_code=200,
                   json={"asyncoperationid": _JOB_ID})
            m.get(backend.url_for(f"asyncoperations({_JOB_ID})"),
                  json={"statecode": 3, "statuscode": 30})
            out = dup.publish_rule(backend, _RULE_ID, wait=True)
        assert out["status"] == "completed"
        assert out["published"] is True

    def test_resolves_rule_by_name(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_rules_url(backend), json={"value": [{"duplicateruleid": _RULE_ID}]})
            m.post(_publish_url(backend), status_code=200,
                   json={"asyncoperationid": _JOB_ID})
            out = dup.publish_rule(backend, "Accounts by name")
        assert out["duplicateruleid"] == _RULE_ID

    def test_dry_run_previews_without_posting(self, dry_backend):
        with requests_mock.Mocker():
            out = dup.publish_rule(dry_backend, _RULE_ID)
        assert out["_dry_run"] is True
        assert out["would_submit"] == "PublishDuplicateRule"


# ── unpublish_rule (synchronous, unbound) ─────────────────────────────────────


class TestUnpublishRule:
    def test_posts_unbound_action_with_rule_id(self, backend):
        with requests_mock.Mocker() as m:
            m.post(backend.url_for("UnpublishDuplicateRule"), status_code=204)
            out = dup.unpublish_rule(backend, _RULE_ID)
        # unbound action: rule id travels in the body, not the URL
        assert m.last_request.json()["DuplicateRuleId"] == _RULE_ID
        assert out["unpublished"] is True
        assert out["duplicateruleid"] == _RULE_ID
        # synchronous — no job_id / status
        assert "job_id" not in out

    def test_dry_run_previews_without_posting(self, dry_backend):
        with requests_mock.Mocker():
            out = dup.unpublish_rule(dry_backend, _RULE_ID)
        assert out["_dry_run"] is True
        assert out["would_submit"] == "UnpublishDuplicateRule"


# ── check (RetrieveDuplicates) ────────────────────────────────────────────────


def _retrieve_dupes_url(backend) -> str:
    return backend.url_for(
        "RetrieveDuplicates(BusinessEntity=@p1,MatchingEntityName=@p2,PagingInfo=@p3)")


class TestCheck:
    def test_returns_duplicates(self, backend):
        match = {"accountid": "99998888-7777-6666-5555-444433332222", "name": "Contoso"}
        with requests_mock.Mocker() as m:
            m.get(_retrieve_dupes_url(backend), json={"value": [match]})
            out = dup.check(backend, entity="account", record={"name": "Contoso"})
        assert out["count"] == 1
        assert out["duplicates"][0]["name"] == "Contoso"
        assert out["entity"] == "account"
        assert out["matching_entity"] == "account"

    def test_passes_candidate_as_typed_business_entity(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_retrieve_dupes_url(backend), json={"value": []})
            dup.check(backend, entity="account", record={"name": "Contoso"})
        # BusinessEntity (@p1) carries the @odata.type cast + the candidate values;
        # PagingInfo (@p3) is required by the server.
        url = unquote(m.last_request.url)
        assert '"@odata.type":"Microsoft.Dynamics.CRM.account"' in url
        assert '"name":"Contoso"' in url
        assert "PagingInfo=@p3" in url
        assert "PageNumber" in url

    def test_matching_entity_override(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_retrieve_dupes_url(backend), json={"value": []})
            dup.check(
                backend, entity="lead", record={"lastname": "Orton"},
                matching_entity="contact",
            )
        url = unquote_plus(m.last_request.url)
        assert "MatchingEntityName=@p2" in m.last_request.url
        assert "'contact'" in url
        assert "Microsoft.Dynamics.CRM.lead" in url

    def test_empty_count_when_no_duplicates(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_retrieve_dupes_url(backend), json={"value": []})
            out = dup.check(backend, entity="account", record={"name": "Nope"})
        assert out["count"] == 0
        assert out["duplicates"] == []

    def test_empty_record_raises(self, backend):
        with pytest.raises(D365Error):
            dup.check(backend, entity="account", record={})
