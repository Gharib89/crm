"""Command-layer tests for three metadata verbs: can-relate, update-relationship,
and add-attribute — focused on branches not covered by existing test modules.
All tests use FakeBackend injected at the CLIContext.backend seam (no HTTP).
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.tests.conftest import FakeBackend


def _use(monkeypatch, backend):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


# --------------------------------------------------------------------------- #
# metadata can-relate
# --------------------------------------------------------------------------- #

class TestMetadataCanRelate:
    """Lines 740-754: json branch, valid_partners branch, eligibility table."""

    def test_json_mode_eligibility(self, fake_backend, monkeypatch):
        # can_relate uses backend.post for the eligibility check (Can* action)
        # and the FakeBackend post response goes through core which builds
        # {"entity": ..., "as": ..., "eligible": bool(resp.get(action))}.
        # Return a truthy value at the action key "CanBeReferenced".
        fake_backend._responses["post"] = {"CanBeReferenced": True}
        _use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "can-relate", "account",
            "--as", "referenced",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["eligible"] is True

    def test_valid_partners_json_mode(self, fake_backend, monkeypatch):
        # valid_partners path uses backend.get; core builds {"valid_partners": names,
        # "count": n} from the "EntityNames" key of the GET response.
        fake_backend._responses["get"] = {"EntityNames": ["contact", "lead"]}
        _use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "can-relate", "account",
            "--as", "referenced", "--valid-partners",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["count"] == 2
        assert "contact" in env["data"]["valid_partners"]

    def test_valid_partners_human_mode_emits_table(self, fake_backend, monkeypatch):
        # human mode + valid_partners → table branch (lines 747-751)
        fake_backend._responses["get"] = {"EntityNames": ["contact"]}
        _use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "metadata", "can-relate", "account",
            "--as", "referenced", "--valid-partners",
        ])
        assert result.exit_code == 0, result.output
        # table has ValidPartner header; contact should appear
        assert "contact" in result.output

    def test_eligibility_human_mode_table(self, fake_backend, monkeypatch):
        # human mode, no valid_partners → eligibility table (line 753-754)
        fake_backend._responses["post"] = {"CanBeReferencing": False}
        _use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "metadata", "can-relate", "contact",
            "--as", "referencing",
        ])
        assert result.exit_code == 0, result.output
        assert "False" in result.output

    def test_backend_called_once_for_eligibility(self, fake_backend, monkeypatch):
        fake_backend._responses["post"] = {"CanBeReferenced": True}
        _use(monkeypatch, fake_backend)
        CliRunner().invoke(cli, [
            "--json", "metadata", "can-relate", "account", "--as", "referenced",
        ])
        assert fake_backend.count("post") == 1
        assert fake_backend.count("get") == 0


# --------------------------------------------------------------------------- #
# metadata update-relationship
# --------------------------------------------------------------------------- #

class TestMetadataUpdateRelationship:
    """Lines 663-685: cascade dict building, backend call, emit.

    update_relationship in core makes: GET to resolve MetadataId, GET for merge
    base, PUT for write.  FakeBackend.get needs to return a MetadataId-bearing
    dict on the first call.
    """

    _META_ID = "11111111-1111-1111-1111-111111111111"

    def _resolve_response(self):
        """First GET: resolve RelationshipDefinitions(SchemaName=...) → MetadataId."""
        return {
            "MetadataId": self._META_ID,
            "SchemaName": "new_rel",
            "RelationshipType": "OneToManyRelationship",
            "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        }

    def _merge_base_response(self):
        """Second GET: typed cast path for merge base."""
        return {
            "MetadataId": self._META_ID,
            "SchemaName": "new_rel",
            "@odata.type": "#Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "CascadeConfiguration": {},
            "AssociatedMenuConfiguration": {
                "Behavior": "UseCollectionName",
                "Group": "Details",
                "Order": 10000,
                "Label": {"UserLocalizedLabel": {"Label": ""}},
            },
        }

    def _fake(self, make_fake_backend):
        call_count = [0]
        responses_list = [self._resolve_response(), self._merge_base_response()]

        def get_side_effect(path):
            idx = call_count[0]
            call_count[0] += 1
            if idx < len(responses_list):
                return responses_list[idx]
            return {}

        return make_fake_backend(responses={
            "get": get_side_effect,
            "put": {"updated": True, "schema_name": "new_rel"},
        })

    def test_cascade_flags_populate_dict(self, make_fake_backend, monkeypatch):
        b = self._fake(make_fake_backend)
        _use(monkeypatch, b)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "update-relationship", "new_rel",
            "--cascade-assign", "Cascade",
            "--cascade-delete", "RemoveLink",
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True

    def test_no_cascade_flags_exits_1_not_2(self, make_fake_backend, monkeypatch):
        # Core rejects "nothing to update" — this should be exit 1 (D365Error),
        # not exit 2 (UsageError) because the validation is in core, not the cmd.
        b = self._fake(make_fake_backend)
        _use(monkeypatch, b)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "update-relationship", "new_rel",
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 1, result.output
        assert "nothing to update" in result.output

    def test_menu_behavior_forwarded(self, make_fake_backend, monkeypatch):
        b = self._fake(make_fake_backend)
        _use(monkeypatch, b)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "update-relationship", "new_rel",
            "--menu-behavior", "UseLabel", "--menu-label", "My Rel",
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 0, result.output

    def test_hierarchical_forwarded(self, make_fake_backend, monkeypatch):
        b = self._fake(make_fake_backend)
        _use(monkeypatch, b)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "update-relationship", "new_rel",
            "--hierarchical", "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 0, result.output

    def test_d365_error_on_not_found(self, make_fake_backend, monkeypatch):
        # When get returns no MetadataId core raises D365Error (exit 1)
        b = make_fake_backend(responses={"get": {}, "put": {}})
        _use(monkeypatch, b)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "update-relationship", "new_rel",
            "--cascade-assign", "Cascade", "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 1, result.output


# --------------------------------------------------------------------------- #
# metadata add-attribute — uncovered branches
# --------------------------------------------------------------------------- #

class TestMetadataAddAttributeBranches:
    """Lines 857->874, 871-872, 876-891: formula/source_type validation,
    boolean default-value parsing, int default-value parsing."""

    def _use(self, monkeypatch, backend):
        _use(monkeypatch, backend)

    # ---- formula_file / source_type validation (lines 856-872) ----

    def test_formula_file_with_simple_type_is_exit_2(self, fake_backend, monkeypatch,
                                                     tmp_path):
        # --formula-file is only valid with rollup/calculated (exit 2 before backend)
        self._use(monkeypatch, fake_backend)
        f = tmp_path / "formula.xaml"
        f.write_text("<x/>", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "string", "--schema-name", "new_Foo", "--display", "Foo",
            "--formula-file", str(f), "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        assert "only valid with --type" in result.output
        assert not fake_backend.called

    def test_rollup_without_formula_file_is_exit_2(self, fake_backend, monkeypatch):
        # --type rollup without --formula-file is exit 2
        self._use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "integer", "--schema-name", "new_Amt", "--display", "Amt",
            "--type", "rollup", "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        assert "formula-file is required" in result.output
        assert not fake_backend.called

    def test_rollup_on_lookup_kind_is_exit_2(self, fake_backend, monkeypatch, tmp_path):
        # --type rollup + --kind lookup is forbidden (exit 2)
        self._use(monkeypatch, fake_backend)
        f = tmp_path / "f.xaml"
        f.write_text("<x/>", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "lookup", "--schema-name", "new_Ref", "--display", "Ref",
            "--target-entity", "contact",
            "--type", "rollup", "--formula-file", str(f), "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        assert "not valid for kind 'lookup'" in result.output
        assert not fake_backend.called

    # ---- boolean default-value parsing (lines 876-891) ----

    def test_invalid_boolean_default_is_exit_2(self, fake_backend, monkeypatch):
        # garbage value for boolean kind → UsageError (exit 2) before backend
        self._use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "boolean", "--schema-name", "new_Active",
            "--display", "Active", "--default-value", "maybe",
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        assert "true/false" in result.output
        assert not fake_backend.called

    @pytest.mark.parametrize("val", ["true", "1", "yes", "on", "false", "0", "no", "off"])
    def test_valid_boolean_default_passes_validation(self, fake_backend, monkeypatch,
                                                     val):
        # Valid boolean strings must not exit 2 (validation passes).
        # Backend will error later (no HTTP mock) → exit 1 is fine.
        self._use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "boolean", "--schema-name", "new_Active",
            "--display", "Active", "--default-value", val,
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code != 2, result.output

    def test_non_boolean_non_int_default_is_exit_2(self, fake_backend, monkeypatch):
        # Non-boolean kind with non-int default → UsageError (exit 2)
        self._use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "integer", "--schema-name", "new_Count",
            "--display", "Count", "--default-value", "not_a_number",
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        assert "must be int" in result.output
        assert not fake_backend.called

    @pytest.mark.parametrize("kind", ["integer", "picklist"])
    def test_valid_int_default_passes_validation(self, fake_backend, monkeypatch, kind):
        # Valid int string must not exit 2.
        self._use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", kind, "--schema-name", "new_Count",
            "--display", "Count", "--default-value", "42",
            "--solution", "MySol", "--no-publish",
        ])
        assert result.exit_code != 2, result.output

    def test_formula_file_read_success_then_backend(self, make_fake_backend,
                                                    monkeypatch, tmp_path):
        # Cover lines 869-870 (successful formula file read) and 875->895 branch
        # (default_value is None so branch is skipped; execution reaches backend).
        # Backend GET returns None → core raises D365Error → exit 1, not exit 2.
        b = make_fake_backend(responses={"get": None, "post": None})
        self._use(monkeypatch, b)
        f = tmp_path / "formula.xaml"
        f.write_text("<formula/>", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "integer", "--schema-name", "new_Calc",
            "--display", "Calc", "--type", "rollup",
            "--formula-file", str(f), "--solution", "MySol", "--no-publish",
        ])
        # Formula file was read successfully (not exit 2 for formula errors)
        assert result.exit_code != 2, result.output


# --------------------------------------------------------------------------- #
# update-optionset --reorder ParamType (#595): comma-separated int parsing moved
# from an inline command-body try/except to a Click ParamType — exit 2 at parse,
# unit-testable without CliRunner.
# --------------------------------------------------------------------------- #

class TestReorderParamType:
    def test_parses_comma_separated_ints(self):
        from crm.commands.metadata import _CommaIntListType
        assert _CommaIntListType().convert("1,2,7", None, None) == [1, 2, 7]

    def test_strips_whitespace_and_empty(self):
        from crm.commands.metadata import _CommaIntListType
        assert _CommaIntListType().convert(" 1 , 2 ,7 ", None, None) == [1, 2, 7]

    def test_bad_value_raises_usage_error(self):
        import click
        from crm.commands.metadata import _CommaIntListType
        with pytest.raises(click.UsageError, match="comma-separated list of integers"):
            _CommaIntListType().convert("1,x,3", None, None)

    def test_cli_bad_reorder_is_exit_2(self, fake_backend, monkeypatch):
        _use(monkeypatch, fake_backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "update-optionset", "new_pick",
            "--reorder", "1,nope,3", "--no-publish",
        ])
        assert result.exit_code == 2, result.output
        assert not fake_backend.called
