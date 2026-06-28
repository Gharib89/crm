"""Rollup & calculated fields via `metadata add-attribute --type` (#427).

Covers the core wiring (SourceType + FormulaDefinition on the typed body),
the validation matrix, the dry-run preview echo, and the CLI plumbing.
All HTTP mocked via requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error
from crm.core import metadata_attrs as ma_mod

FORMULA = "<formula><xaml/></formula>"


def _posts(m) -> list:
    return [r for r in m.request_history if r.method == "POST"]


def _mock_create(m, backend, *, entity="account", logical="new_amount", schema="new_Amount"):
    """Mock the absent-probe → POST → read-back trio for a successful create."""
    attr_id = "33333333-3333-3333-3333-333333333333"
    attr_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes({attr_id})"
    )
    m.get(backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{logical}')"),
        status_code=404, json={"error": {"code": "0x", "message": "nf"}})
    m.post(backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes"),
           status_code=204, headers={"OData-EntityId": attr_url})
    m.get(attr_url, json={"LogicalName": logical, "SchemaName": schema,
                          "AttributeType": "Integer"})


class TestRollupCalculatedCore:
    def test_calculated_sets_source_type_1_and_formula(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create(m, backend)
            info = ma_mod.add_attribute(
                backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
                source_type="calculated", formula_definition=FORMULA,
            )
        body = _posts(m)[0].json()
        assert body["SourceType"] == 1
        assert body["FormulaDefinition"] == FORMULA
        assert body["@odata.type"] == "Microsoft.Dynamics.CRM.IntegerAttributeMetadata"
        assert info["created"] is True
        assert info["source_type"] == "calculated"

    def test_rollup_sets_source_type_2_and_formula(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create(m, backend)
            info = ma_mod.add_attribute(
                backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
                source_type="rollup", formula_definition=FORMULA,
            )
        body = _posts(m)[0].json()
        assert body["SourceType"] == 2
        assert body["FormulaDefinition"] == FORMULA
        assert info["source_type"] == "rollup"

    def test_solution_header_plumbed_on_rollup_write(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create(m, backend)
            ma_mod.add_attribute(
                backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
                source_type="rollup", formula_definition=FORMULA,
                solution="contoso_test",
            )
        post = _posts(m)[0]
        assert post.headers.get("MSCRM.SolutionUniqueName") == "contoso_test"
        assert post.json()["SourceType"] == 2

    def test_simple_default_omits_source_type(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create(m, backend)
            info = ma_mod.add_attribute(
                backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
            )
        body = _posts(m)[0].json()
        assert "SourceType" not in body
        assert "FormulaDefinition" not in body
        assert "source_type" not in info

    def test_rollup_without_formula_raises(self, backend):
        with pytest.raises(D365Error, match="formula-file is required"):
            ma_mod.add_attribute(
                backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
                source_type="rollup",
            )

    def test_formula_with_simple_raises(self, backend):
        with pytest.raises(D365Error, match="only valid with --type"):
            ma_mod.add_attribute(
                backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
                formula_definition=FORMULA,
            )

    @pytest.mark.parametrize("kind", ["lookup", "customer"])
    def test_calculated_on_relationship_kind_raises(self, backend, kind):
        with pytest.raises(D365Error, match="not valid for kind"):
            ma_mod.add_attribute(
                backend, entity="account", kind=kind,
                schema_name="new_Ref", display_name="Ref",
                target_entity="contact",
                source_type="calculated", formula_definition=FORMULA,
            )

    def test_dry_run_previews_source_type_and_formula(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for(
                "EntityDefinitions(LogicalName='account')"
                "/Attributes(LogicalName='new_amount')"),
                status_code=404, json={"error": {"code": "0x", "message": "no"}})
            out = ma_mod.add_attribute(
                dry_backend, entity="account", kind="integer",
                schema_name="new_Amount", display_name="Amount",
                source_type="rollup", formula_definition=FORMULA,
            )
        assert out["_dry_run"] is True
        assert out["body"]["SourceType"] == 2
        assert out["body"]["FormulaDefinition"] == FORMULA
        assert _posts(m) == []


class TestRollupCalculatedCli:
    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_rollup_reads_formula_file_and_creates(self, monkeypatch, backend, tmp_path):
        self._stub(monkeypatch, backend)
        f = tmp_path / "formula.xaml"
        f.write_text(FORMULA, encoding="utf-8")
        with requests_mock.Mocker() as m:
            _mock_create(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "metadata", "add-attribute", "account",
                "--kind", "integer", "--schema-name", "new_Amount",
                "--display", "Amount", "--type", "rollup",
                "--formula-file", str(f), "--no-publish",
            ])
            body = _posts(m)[0].json()
        assert result.exit_code == 0, result.output
        assert body["SourceType"] == 2
        assert body["FormulaDefinition"] == FORMULA
        env = json.loads(result.output)
        assert env["ok"] is True

    def test_rollup_without_formula_file_errors(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "integer", "--schema-name", "new_Amount",
            "--display", "Amount", "--type", "rollup", "--no-publish",
        ])
        assert result.exit_code == 2
        assert "formula-file is required" in result.output

    def test_type_with_lookup_kind_errors_exit_2(self, monkeypatch, backend, tmp_path):
        # Invalid flag combination is rejected at the CLI layer (exit 2) before
        # any backend call — not as a core D365Error (exit 1).
        self._stub(monkeypatch, backend)
        f = tmp_path / "formula.xaml"
        f.write_text(FORMULA, encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "lookup", "--schema-name", "new_Ref",
            "--display", "Ref", "--target-entity", "contact",
            "--type", "rollup", "--formula-file", str(f), "--no-publish",
        ])
        assert result.exit_code == 2
        assert "not valid for kind 'lookup'" in result.output

    def test_formula_file_with_simple_errors(self, monkeypatch, backend, tmp_path):
        self._stub(monkeypatch, backend)
        f = tmp_path / "formula.xaml"
        f.write_text(FORMULA, encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "add-attribute", "account",
            "--kind", "integer", "--schema-name", "new_Amount",
            "--display", "Amount", "--formula-file", str(f), "--no-publish",
        ])
        assert result.exit_code == 2
        assert "only valid with --type" in result.output
