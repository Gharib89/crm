"""Unit tests for `metadata describe <entity>` — the one-shot write-readiness
brief (#68). Mirrors the mocked-backend metadata-update test pattern: a real
D365Backend driven by requests_mock so the exact GET paths are asserted.

The brief is built from pure read-only GETs and over-fetching is itself a bug:
requests_mock raises NoMockAddress for any endpoint a test does not register, so
each test mocks ONLY the round-trips its scenario should make.
"""
# pyright: basic

from __future__ import annotations

import json

import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli


def _entity_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='new_project')")


def _attrs_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='new_project')/Attributes")


def _m2o_url(backend) -> str:
    return backend.url_for(
        "EntityDefinitions(LogicalName='new_project')/ManyToOneRelationships"
    )


def _cast_url(backend, cast: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='new_project')/Attributes/"
        f"Microsoft.Dynamics.CRM.{cast}"
    )


def _opt(value: int, lbl: str) -> dict:
    return {"Value": value, "Label": {"UserLocalizedLabel": {"Label": lbl}}}


_ENTITY = {
    "LogicalName": "new_project",
    "EntitySetName": "new_projects",
    "PrimaryIdAttribute": "new_projectid",
    "PrimaryNameAttribute": "new_name",
}


def _attr(logical, attr_type, *, required="None", create=True, update=True):
    return {
        "LogicalName": logical,
        "AttributeType": attr_type,
        "RequiredLevel": {"Value": required},
        "IsValidForCreate": create,
        "IsValidForUpdate": update,
    }


class TestTracer:
    def test_brief_carries_entity_set_primary_ids_and_writable_attrs(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_code", "String"),
            # Not writable: system column valid for neither create nor update.
            _attr("createdon", "DateTime", create=False, update=False),
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            brief = meta.describe_entity(backend, "new_project")

        assert brief["entity_set_name"] == "new_projects"
        assert brief["primary_id"] == "new_projectid"
        assert brief["primary_name"] == "new_name"

        by_name = {a["logical_name"]: a for a in brief["writable_attributes"]}
        # Non-writable system column is filtered out entirely.
        assert "createdon" not in by_name
        assert set(by_name) == {"new_name", "new_code"}
        assert by_name["new_name"]["attribute_type"] == "String"
        assert by_name["new_name"]["required_level"] == "ApplicationRequired"
        assert by_name["new_code"]["required_level"] == "None"


class TestLookupBindEnrichment:
    def test_lookup_exposes_bind_key_and_targets_with_set_name(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_accountid", "Lookup"),
        ]}
        # All three names differ so asserting the wrong field cannot pass:
        #  - attribute logical name      : new_accountid
        #  - referencing nav prop (CORRECT, single-valued, case-preserved,
        #    used in @odata.bind)         : new_AccountId
        #  - referenced nav prop (WRONG, collection-valued on the OTHER entity,
        #    rejected by the server)      : new_project_AccountId
        m2o = {"value": [{
            "ReferencingAttribute": "new_accountid",
            "ReferencedEntity": "account",
            "ReferencingEntityNavigationPropertyName": "new_AccountId",
            "ReferencedEntityNavigationPropertyName": "new_project_AccountId",
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_m2o_url(backend), json=m2o)
            # Per-referenced-entity set-name resolution.
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')"),
                json={"EntitySetName": "accounts"},
            )
            brief = meta.describe_entity(backend, "new_project")

        by_name = {a["logical_name"]: a for a in brief["writable_attributes"]}
        lookup = by_name["new_accountid"]
        # Bind key is the single-valued navigation property on THIS entity
        # (ReferencingEntityNavigationPropertyName) + @odata.bind, self-derived
        # from the ManyToOne relationship joined on ReferencingAttribute. The
        # case is preserved exactly (server rejects a lower-cased nav name).
        assert lookup["bind_key"] == "new_AccountId@odata.bind"
        assert lookup["targets"] == [{"logical": "account", "set_name": "accounts"}]
        # A non-lookup attribute carries neither enrichment key.
        assert "bind_key" not in by_name["new_name"]
        assert "targets" not in by_name["new_name"]

    def test_describe_bind_key_round_trips_through_entity_validate(self, backend):
        """describe's `bind_key` must be accepted by `entity create --validate`.

        Both code paths key off `ReferencingEntityNavigationPropertyName`; this
        drives them off ONE mocked metadata view and asserts the bind key
        describe emits is in the valid-key set validate builds. The nav name
        (`new_AccountId`) is deliberately distinct from the attribute logical
        name and the collection (referenced) nav name, so the pre-#228 code —
        which emitted the referenced name — would produce a key validate
        rejects, failing this test. Guards against the two paths drifting.
        """
        from crm.core import entity as ent
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_accountid", "Lookup"),
        ]}
        m2o = {"value": [{
            "ReferencingAttribute": "new_accountid",
            "ReferencedEntity": "account",
            "ReferencingEntityNavigationPropertyName": "new_AccountId",
            "ReferencedEntityNavigationPropertyName": "new_project_AccountId",
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_m2o_url(backend), json=m2o)
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')"),
                json={"EntitySetName": "accounts"},
            )
            brief = meta.describe_entity(backend, "new_project")
            lookup = next(
                a for a in brief["writable_attributes"]
                if a["logical_name"] == "new_accountid"
            )
            # Set-name resolution for validate's entity-set -> logical lookup.
            m.get(
                backend.url_for("EntityDefinitions"),
                json={"value": [{
                    "LogicalName": "new_project",
                    "EntitySetName": "new_projects",
                }]},
            )
            payload = {lookup["bind_key"]: "/accounts(00000000-0000-0000-0000-000000000000)"}
            result = ent.validate_payload(backend, "new_projects", payload)

        assert result == {"ok": True}


class TestPicklistLocalOptions:
    def test_local_picklist_exposes_inline_options(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_stage", "Picklist"),
        ]}
        picklists = {"value": [{
            "LogicalName": "new_stage",
            "OptionSet": {
                "MetadataId": "55555555-5555-5555-5555-555555555555",
                "IsGlobal": False,
                "Options": [_opt(1, "New"), _opt(2, "Done")],
            },
            "GlobalOptionSet": None,
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_cast_url(backend, "PicklistAttributeMetadata"), json=picklists)
            brief = meta.describe_entity(backend, "new_project")

        stage = {a["logical_name"]: a for a in brief["writable_attributes"]}["new_stage"]
        assert stage["options"] == [
            {"value": 1, "label": "New"},
            {"value": 2, "label": "Done"},
        ]
        # A local option set carries no global option set id.
        assert "global_optionset_id" not in stage


class TestPicklistGlobalOptionSet:
    def test_global_bound_picklist_emits_options_and_optionset_id(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [_attr("new_priority", "Picklist")]}
        # Global-bound: OptionSet is null, GlobalOptionSet carries the options
        # AND the MetadataId GUID (on-prem 9.1 needs the GUID to bind on create).
        gos_id = "99999999-9999-9999-9999-999999999999"
        picklists = {"value": [{
            "LogicalName": "new_priority",
            "OptionSet": None,
            "GlobalOptionSet": {
                "MetadataId": gos_id,
                "IsGlobal": True,
                "Options": [_opt(10, "Low"), _opt(20, "High")],
            },
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_cast_url(backend, "PicklistAttributeMetadata"), json=picklists)
            brief = meta.describe_entity(backend, "new_project")

        prio = {a["logical_name"]: a
                for a in brief["writable_attributes"]}["new_priority"]
        assert prio["options"] == [
            {"value": 10, "label": "Low"},
            {"value": 20, "label": "High"},
        ]
        assert prio["global_optionset_id"] == gos_id


class TestStateStatusOptions:
    def test_state_and_status_carry_inline_options(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("statecode", "State"),
            _attr("statuscode", "Status"),
        ]}
        states = {"value": [{
            "LogicalName": "statecode",
            "OptionSet": {"Options": [_opt(0, "Active"), _opt(1, "Inactive")]},
        }]}
        statuses = {"value": [{
            "LogicalName": "statuscode",
            "OptionSet": {"Options": [_opt(1, "Active"), _opt(2, "Inactive")]},
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_cast_url(backend, "StateAttributeMetadata"), json=states)
            m.get(_cast_url(backend, "StatusAttributeMetadata"), json=statuses)
            brief = meta.describe_entity(backend, "new_project")

        by_name = {a["logical_name"]: a for a in brief["writable_attributes"]}
        assert by_name["statecode"]["options"] == [
            {"value": 0, "label": "Active"},
            {"value": 1, "label": "Inactive"},
        ]
        assert by_name["statuscode"]["options"] == [
            {"value": 1, "label": "Active"},
            {"value": 2, "label": "Inactive"},
        ]


class TestCommand:
    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_describe_emits_brief_via_pure_gets(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "describe", "new_project"]
            )
            # The brief is built from read-only GETs alone.
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["entity_set_name"] == "new_projects"
        assert env["data"]["primary_id"] == "new_projectid"
        assert env["meta"]["writable_attributes"] == 1

    def test_describe_help_lists_command(self):
        result = CliRunner().invoke(cli, ["metadata", "describe", "--help"])
        assert result.exit_code == 0
        assert "write-readiness" in result.output.lower()


class TestPicklistMetaOptions:
    """`metadata picklist` JSON mode flattens options to `meta.options` (#76)."""

    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def _attr_info_url(self, backend) -> str:
        return backend.url_for(
            "EntityDefinitions(LogicalName='account')"
            "/Attributes(LogicalName='industrycode')"
        )

    def _picklist_url(self, backend) -> str:
        return backend.url_for(
            "EntityDefinitions(LogicalName='account')"
            "/Attributes(LogicalName='industrycode')"
            "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        )

    def test_local_picklist_meta_options_from_optionset(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        raw = {
            "LogicalName": "industrycode",
            "OptionSet": {"Options": [_opt(1, "Accounting"), _opt(2, "Retail")]},
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(self._attr_info_url(backend),
                  json={"LogicalName": "industrycode", "AttributeType": "Picklist"})
            m.get(self._picklist_url(backend), json=raw)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "industrycode"]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["options"] == [
            {"value": 1, "label": "Accounting"},
            {"value": 2, "label": "Retail"},
        ]
        # Raw data is untouched — no contract break.
        assert env["data"]["OptionSet"]["Options"] == raw["OptionSet"]["Options"]
        assert env["data"]["GlobalOptionSet"] is None

    def test_global_bound_picklist_meta_options_from_fallback(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        # OptionSet null/empty → options live under GlobalOptionSet.
        raw = {
            "LogicalName": "industrycode",
            "OptionSet": None,
            "GlobalOptionSet": {"Options": [_opt(10, "Low"), _opt(20, "High")]},
        }
        with requests_mock.Mocker() as m:
            m.get(self._attr_info_url(backend),
                  json={"LogicalName": "industrycode", "AttributeType": "Picklist"})
            m.get(self._picklist_url(backend), json=raw)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "industrycode"]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["options"] == [
            {"value": 10, "label": "Low"},
            {"value": 20, "label": "High"},
        ]
        assert env["data"]["GlobalOptionSet"]["Options"] == raw["GlobalOptionSet"]["Options"]


class TestPicklistTableLabels:
    """`metadata picklist` human (table) mode resolves labels via the full fallback."""

    def _attr_info_url(self, backend) -> str:
        return backend.url_for(
            "EntityDefinitions(LogicalName='account')"
            "/Attributes(LogicalName='industrycode')"
        )

    def _picklist_url(self, backend) -> str:
        return backend.url_for(
            "EntityDefinitions(LogicalName='account')"
            "/Attributes(LogicalName='industrycode')"
            "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        )

    def test_table_mode_uses_localizedlabels_fallback(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
        # Label carried ONLY via LocalizedLabels (no UserLocalizedLabel) — the
        # table branch previously rendered this blank; it must now resolve it.
        raw = {
            "LogicalName": "industrycode",
            "OptionSet": {"Options": [
                {"Value": 5, "Label": {"LocalizedLabels": [{"Label": "Localized Only"}]}},
            ]},
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(self._attr_info_url(backend),
                  json={"LogicalName": "industrycode", "AttributeType": "Picklist"})
            m.get(self._picklist_url(backend), json=raw)
            result = CliRunner().invoke(
                cli, ["metadata", "picklist", "account", "industrycode"]
            )
        assert result.exit_code == 0, result.output
        assert "Localized Only" in result.output


class TestGetOptionsetMetaOptions:
    """`metadata get-optionset` JSON mode flattens root Options (#76)."""

    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_get_optionset_meta_options_from_root(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        # Label carried via LocalizedLabels (no UserLocalizedLabel) — the robust
        # fallback path must still resolve it.
        raw = {
            "Name": "new_priority",
            "Options": [
                {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}},
                {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "High"}]}},
            ],
        }
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json=raw,
            )
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "get-optionset", "new_priority"]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["options"] == [
            {"value": 1, "label": "Low"},
            {"value": 2, "label": "High"},
        ]
        # Raw data is untouched.
        assert env["data"]["Options"] == raw["Options"]

    def test_get_optionset_meta_options_human_mode_absent(self, monkeypatch, backend):
        """Human mode (no --json) must NOT render the flattened meta options (#76).

        `ctx.emit` prints `meta` in human mode too, so the flattened list is
        gated on JSON mode. The flattened repr (`{'value': 1, 'label': 'Low'}`)
        and the lowercase `options:` meta status line are produced ONLY by the
        meta rendering — raw data uses capitalized `Value`/`Label` keys — so
        their absence proves the regression can't return.
        """
        self._stub(monkeypatch, backend)
        raw = {
            "Name": "new_priority",
            "Options": [
                {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}},
                {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "High"}]}},
            ],
        }
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json=raw,
            )
            result = CliRunner().invoke(
                cli, ["metadata", "get-optionset", "new_priority"]
            )
        assert result.exit_code == 0, result.output
        # The flattened-options repr is emitted only via the meta status line.
        assert "{'value': 1, 'label': 'Low'}" not in result.output
        assert "options:" not in result.output


class TestSuggestLogicalName:
    """Unit tests for suggest_logical_name recovery helper (#203)."""

    def _list_url(self, backend) -> str:
        return backend.url_for("EntityDefinitions")

    def _filter_url(self, backend, set_name: str) -> str:
        import urllib.parse
        filt = f"EntitySetName eq '{set_name}'"
        params = urllib.parse.urlencode({
            "$select": "LogicalName,EntitySetName",
            "$filter": filt,
        })
        return backend.url_for("EntityDefinitions") + "?" + params

    def test_exact_set_name_match_returns_logical_name(self, backend):
        from crm.core.metadata import suggest_logical_name
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions"),
                json={"value": [{"LogicalName": "account", "EntitySetName": "accounts"}]},
            )
            result = suggest_logical_name(backend, "accounts")
        assert result is not None
        assert result["logical_name"] == "account"
        assert result["reason"] == "exact-set"

    def test_fuzzy_match_returns_close_logical_name(self, backend):
        from crm.core.metadata import suggest_logical_name
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions"),
                json={"value": [
                    {"LogicalName": "webresource", "EntitySetName": "webresourceset"},
                ]},
            )
            result = suggest_logical_name(backend, "webresources")
        assert result is not None
        assert result["logical_name"] == "webresource"
        assert result["reason"] == "close-match"

    def test_no_match_returns_none(self, backend):
        from crm.core.metadata import suggest_logical_name
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions"),
                json={"value": [
                    {"LogicalName": "account", "EntitySetName": "accounts"},
                    {"LogicalName": "contact", "EntitySetName": "contacts"},
                ]},
            )
            result = suggest_logical_name(backend, "zzzznotanentity")
        assert result is None

    def test_recovery_get_raises_returns_none(self, backend):
        from crm.core.metadata import suggest_logical_name
        from crm.utils.d365_backend import D365Error
        with requests_mock.Mocker() as m:
            m.get(backend.url_for("EntityDefinitions"), status_code=500)
            result = suggest_logical_name(backend, "accounts")
        assert result is None

    def test_exact_set_wins_over_fuzzy_when_both_match(self, backend):
        """EntitySetName exact match takes priority over any fuzzy hit."""
        from crm.core.metadata import suggest_logical_name
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("EntityDefinitions"),
                json={"value": [
                    {"LogicalName": "account", "EntitySetName": "accounts"},
                    {"LogicalName": "accountbase", "EntitySetName": "accountbaseset"},
                ]},
            )
            result = suggest_logical_name(backend, "accounts")
        assert result is not None
        assert result["logical_name"] == "account"
        assert result["reason"] == "exact-set"


class TestDescribeHint:
    """describe command enriches 404 with did_you_mean / hint (#203)."""

    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def _entity_url(self, backend, name: str) -> str:
        return backend.url_for(f"EntityDefinitions(LogicalName='{name}')")

    def _list_url(self, backend) -> str:
        return backend.url_for("EntityDefinitions")

    def test_set_name_404_emits_did_you_mean_and_hint(self, monkeypatch, backend):
        """`metadata describe accounts` → exit 1, meta.did_you_mean=account, hint present."""
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(self._entity_url(backend, "accounts"), status_code=404)
            m.get(self._list_url(backend), json={"value": [
                {"LogicalName": "account", "EntitySetName": "accounts"},
            ]})
            # attrs GET must NOT be called (404 short-circuits)
            result = CliRunner().invoke(cli, ["--json", "metadata", "describe", "accounts"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["did_you_mean"] == "account"
        assert "hint" in env["meta"]
        assert "logical" in env["meta"]["hint"].lower() or "singular" in env["meta"]["hint"].lower()

    def test_fuzzy_404_emits_did_you_mean(self, monkeypatch, backend):
        """`metadata describe webresources` → exit 1, meta.did_you_mean=webresource."""
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(self._entity_url(backend, "webresources"), status_code=404)
            m.get(self._list_url(backend), json={"value": [
                {"LogicalName": "webresource", "EntitySetName": "webresourceset"},
            ]})
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "describe", "webresources"]
            )
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["did_you_mean"] == "webresource"

    def test_no_match_404_plain_envelope(self, monkeypatch, backend):
        """`metadata describe zzzznotanentity` → exit 1, no did_you_mean."""
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(self._entity_url(backend, "zzzznotanentity"), status_code=404)
            m.get(self._list_url(backend), json={"value": [
                {"LogicalName": "account", "EntitySetName": "accounts"},
            ]})
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "describe", "zzzznotanentity"]
            )
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "did_you_mean" not in env["meta"]

    def test_happy_path_no_recovery_get(self, monkeypatch, backend):
        """`metadata describe account` success → no recovery GET fired."""
        self._stub(monkeypatch, backend)
        attrs = {"value": [
            {"LogicalName": "name", "AttributeType": "String",
             "RequiredLevel": {"Value": "None"},
             "IsValidForCreate": True, "IsValidForUpdate": True},
        ]}
        with requests_mock.Mocker() as m:
            m.get(self._entity_url(backend, "account"), json={
                "LogicalName": "account", "EntitySetName": "accounts",
                "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name",
            })
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')/Attributes"),
                json=attrs,
            )
            result = CliRunner().invoke(cli, ["--json", "metadata", "describe", "account"])
            list_calls = [r for r in m.request_history
                          if r.url == self._list_url(backend) + "?$select=LogicalName%2CEntitySetName"]
        assert result.exit_code == 0, result.output
        assert list_calls == []

    def test_recovery_get_failure_emits_original_404(self, monkeypatch, backend):
        """If recovery GET raises, original 404 is emitted unchanged."""
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(self._entity_url(backend, "accounts"), status_code=404)
            m.get(self._list_url(backend), status_code=500)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "describe", "accounts"]
            )
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["status"] == 404
        assert "did_you_mean" not in env["meta"]

    def test_non_404_error_no_recovery(self, monkeypatch, backend):
        """A 401 (or any non-404) does not trigger the recovery GET."""
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(self._entity_url(backend, "account"), status_code=401)
            # No list mock registered — requests_mock raises if it fires.
            result = CliRunner().invoke(cli, ["--json", "metadata", "describe", "account"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["meta"]["status"] == 401
        assert "did_you_mean" not in env["meta"]
