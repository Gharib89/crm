"""Pre-write field-name validation for `entity create/update --validate` (#72, #233).

Mirrors the mocked-backend metadata pattern (see test_metadata_describe): a real
D365Backend driven by requests_mock so the exact GET paths are asserted and
over-fetching surfaces as NoMockAddress. Validation is FIELD-NAME only (v1): no
picklist-value checking.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error


def _sets_url(backend) -> str:
    return backend.url_for("EntityDefinitions")


def _attrs_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='account')/Attributes")


def _m2o_url(backend) -> str:
    return backend.url_for(
        "EntityDefinitions(LogicalName='account')/ManyToOneRelationships"
    )


_SETS = {"value": [{"LogicalName": "account", "EntitySetName": "accounts"}]}
_ATTRS = {"value": [
    {"LogicalName": "name"},
    {"LogicalName": "telephone1"},
    {"LogicalName": "accountid"},
]}
_M2O = {"value": [
    {"ReferencingEntityNavigationPropertyName": "primarycontactid_account"},
]}


def _mock_three(m, backend) -> None:
    m.get(_sets_url(backend), json=_SETS)
    m.get(_attrs_url(backend), json=_ATTRS)
    m.get(_m2o_url(backend), json=_M2O)


class TestTracer:
    def test_unknown_field_flagged_with_did_you_mean(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(backend, "accounts", {"naem": "Contoso"})
            assert {r.method for r in m.request_history} == {"GET"}

        assert result["ok"] is False
        assert result["meta"]["unknown_fields"] == ["naem"]
        assert result["meta"]["did_you_mean"] == {"naem": "name"}


class TestNavBindUnion:
    def test_valid_odata_bind_nav_not_flagged(self, backend):
        from crm.core import entity as ent
        payload = {
            "name": "Contoso",
            "primarycontactid_account@odata.bind": "/contacts(<guid>)",
        }
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(backend, "accounts", payload)
        # Nav property resolves via the ManyToOne union, so the bound lookup is
        # a known field — nothing flagged.
        assert result == {"ok": True}


class TestGetCost:
    def test_no_bind_keys_skips_relationships_get(self, backend):
        from crm.core import entity as ent
        # Payload has no @odata.bind keys, so nav-property names cannot matter:
        # only the set→logical and attributes GETs should fire (2, not 3).
        with requests_mock.Mocker() as m:
            m.get(_sets_url(backend), json=_SETS)
            m.get(_attrs_url(backend), json=_ATTRS)
            result = ent.validate_payload(
                backend, "accounts", {"name": "Contoso", "telephone1": "555"}
            )
            paths = [r.path for r in m.request_history]
        assert result == {"ok": True}
        assert not any("ManyToOneRelationships" in p for p in paths)


class TestDidYouMean:
    def test_no_suggestion_when_nothing_close(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(
                backend, "accounts", {"zzzqqq": "x"}
            )
        # Flagged unknown, but no valid key is close enough to suggest.
        assert result["ok"] is False
        assert result["meta"]["unknown_fields"] == ["zzzqqq"]
        assert result["meta"]["did_you_mean"] == {}


_ATTRS_PHONE = {"value": [
    {"LogicalName": "name"},
    {"LogicalName": "telephone1"},
    {"LogicalName": "telephone2"},
    {"LogicalName": "telephone3"},
    {"LogicalName": "accountnumber"},
    {"LogicalName": "accountid"},
]}


def _mock_phone(m, backend) -> None:
    m.get(_sets_url(backend), json=_SETS)
    m.get(_attrs_url(backend), json=_ATTRS_PHONE)


class TestNumberedFamilyRanking:
    def test_number_word_resolves_to_matching_member(self, backend):
        # `telephoneone` must suggest `telephone1`, not the lexicographically
        # largest fuzzy tie `telephone3` (#198).
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_phone(m, backend)
            result = ent.validate_payload(backend, "accounts", {"telephoneone": "555"})
        assert result["ok"] is False
        assert result["meta"]["did_you_mean"] == {"telephoneone": "telephone1"}

    def test_equal_ratio_tie_picks_lowest_member(self, backend):
        # A typo equidistant from a numbered family resolves to the lowest member.
        from crm.core import entity as ent
        attrs = {"value": [{"LogicalName": f"field{n}"} for n in (1, 2, 3)]}
        with requests_mock.Mocker() as m:
            m.get(_sets_url(backend), json=_SETS)
            m.get(_attrs_url(backend), json=attrs)
            result = ent.validate_payload(backend, "accounts", {"fieldz": "x"})
        assert result["meta"]["did_you_mean"] == {"fieldz": "field1"}

    def test_unique_typo_not_regressed(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_phone(m, backend)
            result = ent.validate_payload(backend, "accounts", {"accountnumbr": "x"})
        assert result["meta"]["did_you_mean"] == {"accountnumbr": "accountnumber"}


class TestControlAnnotations:
    def test_control_annotation_key_ignored_and_skips_relationships(self, backend):
        from crm.core import entity as ent
        payload = {"@odata.etag": 'W/"123"', "name": "Contoso"}
        with requests_mock.Mocker() as m:
            # A bare control annotation is not an @odata.bind, so the nav probe
            # is skipped — only set→logical + attributes fire.
            m.get(_sets_url(backend), json=_SETS)
            m.get(_attrs_url(backend), json=_ATTRS)
            result = ent.validate_payload(backend, "accounts", payload)
            paths = [r.path for r in m.request_history]
        # A bare control annotation strips to "" and is never treated as a field.
        assert result == {"ok": True}
        assert not any("ManyToOneRelationships" in p for p in paths)


class TestFilterEscaping:
    def test_entity_set_with_apostrophe_is_escaped(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            m.get(_sets_url(backend), json={"value": []})
            with pytest.raises(D365Error, match="Unknown entity set"):
                ent.validate_payload(backend, "o'brien", {"name": "x"})
            # OData literal escaping doubles the quote in the $filter expression.
            qs = m.request_history[0].qs
            assert qs["$filter"] == ["entitysetname eq 'o''brien'"]


class TestDuplicateUnknown:
    def test_base_and_annotated_unknown_dedup_to_one_entry(self, backend):
        from crm.core import entity as ent
        # `foo` and `foo@odata.bind` both strip to the same unknown field name.
        payload = {"foo": 1, "foo@odata.bind": "/x(1)"}
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(backend, "accounts", payload)
        assert result["ok"] is False
        assert result["meta"]["unknown_fields"] == ["foo"]


class TestUnknownEntitySet:
    def test_unresolvable_set_raises(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            m.get(_sets_url(backend), json={"value": []})
            with pytest.raises(D365Error, match="Unknown entity set"):
                ent.validate_payload(backend, "nopes", {"name": "x"})


class TestCommandGate:
    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_create_validate_blocks_write_on_unknown_field(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "create", "accounts",
                "--data", json.dumps({"naem": "Contoso"}), "--validate",
            ])
            # Validation read-only GETs ran; the POST never fired.
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["unknown_fields"] == ["naem"]
        assert env["meta"]["did_you_mean"] == {"naem": "name"}

    def test_create_validate_dry_run_composes(self, monkeypatch, dry_backend):
        self._stub(monkeypatch, dry_backend)
        with requests_mock.Mocker() as m:
            _mock_three(m, dry_backend)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "entity", "create", "accounts",
                "--data", json.dumps({"name": "Contoso"}), "--validate",
            ])
            # Validation forces real GETs even under dry-run; the write itself is
            # previewed, never issued.
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["_dry_run"] is True
        assert env["data"]["method"] == "POST"

    def test_update_validate_blocks_write_on_unknown_field(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "update", "accounts", guid,
                "--data", json.dumps({"naem": "x"}), "--validate",
            ])
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["unknown_fields"] == ["naem"]


# ── Primary-id warning (#233) ─────────────────────────────────────────────────

_SETS_PID = {"value": [
    {"LogicalName": "account", "EntitySetName": "accounts", "PrimaryIdAttribute": "accountid"},
]}


def _mock_two_pid(m, backend) -> None:
    """Two GETs: set→logical (with PrimaryIdAttribute) + attributes. No nav."""
    m.get(_sets_url(backend), json=_SETS_PID)
    m.get(_attrs_url(backend), json=_ATTRS)


class TestPrimaryIdWarning:
    """validate_payload(is_create=True) warns when primary id is in the payload (#233)."""

    def test_primary_id_in_create_payload_warns(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            result = ent.validate_payload(
                backend, "accounts",
                {"accountid": "11111111-1111-1111-1111-111111111111", "name": "Contoso"},
                is_create=True,
            )
        assert result["ok"] is True
        assert result["meta"]["warnings"] == [
            "payload contains primary id 'accountid' — "
            "remove it unless you intend to create with an explicit GUID"
        ]

    def test_primary_id_absent_from_payload_no_warn(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            result = ent.validate_payload(
                backend, "accounts", {"name": "Contoso"},
                is_create=True,
            )
        assert result == {"ok": True}

    def test_is_create_false_primary_id_no_warn(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            result = ent.validate_payload(
                backend, "accounts",
                {"accountid": "11111111-1111-1111-1111-111111111111", "name": "Contoso"},
                is_create=False,
            )
        assert result == {"ok": True}

    def test_no_extra_get_for_primary_id_check(self, backend):
        """PrimaryIdAttribute comes from widened $select on existing sets GET — no new round-trip."""
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            ent.validate_payload(
                backend, "accounts", {"name": "Contoso"},
                is_create=True,
            )
            paths = [r.path for r in m.request_history]
        assert len(paths) == 2
        assert not any("ManyToOneRelationships" in p for p in paths)


class TestPrimaryIdWarnCommand:
    """Command-level: entity create --validate warns on primary id (#233)."""

    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda _self: backend)

    def test_create_with_primary_id_warns_in_json_mode(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            post_url = backend.url_for("accounts")
            m.post(post_url, json={"accountid": guid, "name": "Contoso"})
            result = CliRunner().invoke(cli, [
                "--json", "entity", "create", "accounts",
                "--data", json.dumps({"accountid": guid, "name": "Contoso"}),
                "--validate",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert "warnings" in env.get("meta", {})
        assert any("accountid" in w for w in env["meta"]["warnings"])

    def test_create_with_primary_id_warns_in_human_mode(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            post_url = backend.url_for("accounts")
            m.post(post_url, json={"accountid": guid, "name": "Contoso"})
            result = CliRunner().invoke(cli, [
                "entity", "create", "accounts",
                "--data", json.dumps({"accountid": guid, "name": "Contoso"}),
                "--validate",
            ])
        assert result.exit_code == 0, result.output
        # skin.warning writes via print(file=sys.stderr); CliRunner captures it in
        # result.stderr (Click 8.2+ separates streams) and also in result.output.
        assert result.stderr is not None
        assert "payload contains primary id 'accountid'" in result.stderr

    def test_create_with_primary_id_warns_even_on_server_error(self, monkeypatch, backend):
        """Warning appears in meta even when the POST fails (e.g. duplicate-key)."""
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        error_body = {
            "error": {
                "code": "0x80040237",
                "message": "A record with matching key values already exists.",
            }
        }
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            post_url = backend.url_for("accounts")
            m.post(post_url, status_code=412, json=error_body)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "create", "accounts",
                "--data", json.dumps({"accountid": guid, "name": "Contoso"}),
                "--validate",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "warnings" in env.get("meta", {})
        assert any("accountid" in w for w in env["meta"]["warnings"])

    def test_create_with_primary_id_warns_in_human_mode_on_server_error(self, monkeypatch, backend):
        """Warning appears on stderr even when the POST fails in human mode."""
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        error_body = {"error": {"code": "0x80040237", "message": "A record with matching key values already exists."}}
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            m.post(backend.url_for("accounts"), status_code=412, json=error_body)
            result = CliRunner().invoke(cli, [
                "entity", "create", "accounts",
                "--data", json.dumps({"accountid": guid, "name": "Contoso"}),
                "--validate",
            ])
        assert result.exit_code != 0
        assert "payload contains primary id 'accountid'" in result.stderr

    def test_update_with_primary_id_no_warn(self, monkeypatch, backend):
        """entity update --validate never warns about primary id (update path unchanged)."""
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            _mock_two_pid(m, backend)
            patch_url = backend.url_for(f"accounts({guid})")
            m.patch(patch_url, status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "update", "accounts", guid,
                "--data", json.dumps({"accountid": guid, "name": "x"}), "--validate",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env.get("meta", {}).get("warnings") is None
