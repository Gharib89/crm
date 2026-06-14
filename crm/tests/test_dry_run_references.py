"""Dry-run reference resolution (#281).

Under ``--dry-run`` the name-taking structured writes resolve every server-side
object they would dereference and report each one's existence in the preview
envelope under ``data.references[]`` (``{kind, value, _exists}``). A dangling
reference is reported (envelope stays ``ok: true``) and surfaced as a
``meta.warnings`` advisory — never attempted as a live write.

These exercise the real ``D365Backend`` in ``dry_run`` mode: the mutating POST
short-circuits to an echo (no wire call), while the resolution GETs fall through
to ``requests_mock`` per the reads-execute rule.
"""
# pyright: basic

from __future__ import annotations

import json

import requests_mock

from crm.cli import CLIContext
from crm.commands._helpers import _emit_with_warning


def _ref(refs, kind):
    """Return the single reference entry of the given kind (or None)."""
    matches = [r for r in refs if r["kind"] == kind]
    assert len(matches) <= 1, f"expected at most one {kind!r} ref, got {matches}"
    return matches[0] if matches else None


def _exists(refs, kind):
    """Existence flag of the (required) reference of the given kind."""
    r = _ref(refs, kind)
    assert r is not None, f"expected a {kind!r} reference in {refs}"
    return r["_exists"]


class TestCreateOneToManyReferences:
    def test_dry_run_reports_both_entities_exist(self, dry_backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            # Relationship does not exist yet (so the create proceeds, not skip).
            m.get(
                dry_backend.url_for(
                    "RelationshipDefinitions(SchemaName='new_account_new_project')"),
                status_code=404,
                json={"error": {"code": "0x", "message": "not found"}},
            )
            # Both referenced + referencing entities resolve.
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"MetadataId": "11111111-1111-1111-1111-111111111111"})
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='new_project')"),
                  json={"MetadataId": "22222222-2222-2222-2222-222222222222"})
            out = rel.create_one_to_many(
                dry_backend,
                schema_name="new_account_new_project",
                referenced_entity="account",
                referencing_entity="new_project",
                lookup_schema="new_AccountId",
                lookup_display="Account",
            )
        assert out["_dry_run"] is True
        refs = out["references"]
        assert _ref(refs, "referenced_entity") == {
            "kind": "referenced_entity", "value": "account", "_exists": True}
        assert _ref(refs, "referencing_entity") == {
            "kind": "referencing_entity", "value": "new_project", "_exists": True}

    def test_dry_run_dangling_referenced_entity(self, dry_backend):
        from crm.core import relationships as rel
        with requests_mock.Mocker() as m:
            m.get(
                dry_backend.url_for(
                    "RelationshipDefinitions(SchemaName='new_ghost_new_project')"),
                status_code=404, json={"error": {"code": "0x", "message": "no"}})
            # Referenced entity is absent (404); referencing entity resolves.
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='ghost')"),
                  status_code=404, json={"error": {"code": "0x", "message": "no"}})
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='new_project')"),
                  json={"MetadataId": "22222222-2222-2222-2222-222222222222"})
            out = rel.create_one_to_many(
                dry_backend,
                schema_name="new_ghost_new_project",
                referenced_entity="ghost",
                referencing_entity="new_project",
                lookup_schema="new_GhostId",
                lookup_display="Ghost",
            )
        assert _exists(out["references"], "referenced_entity") is False
        assert _exists(out["references"], "referencing_entity") is True


class TestAddAttributeReferences:
    def _mock_attr_absent(self, m, backend, *, entity="account", logical="new_status"):
        m.get(backend.url_for(
            f"EntityDefinitions(LogicalName='{entity}')"
            f"/Attributes(LogicalName='{logical}')"),
            status_code=404, json={"error": {"code": "0x", "message": "no"}})

    def test_picklist_dry_run_reports_optionset_exists(self, dry_backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            self._mock_attr_absent(m, dry_backend)
            m.get(dry_backend.url_for(
                "GlobalOptionSetDefinitions(Name='contoso_status')"),
                json={"MetadataId": "33333333-3333-3333-3333-333333333333"})
            out = ma.add_attribute(
                dry_backend, entity="account", kind="picklist",
                schema_name="new_Status", display_name="Status",
                optionset_name="contoso_status")
        assert out["_dry_run"] is True
        assert _ref(out["references"], "optionset") == {
            "kind": "optionset", "value": "contoso_status", "_exists": True}

    def test_picklist_dry_run_dangling_optionset_no_raise(self, dry_backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            self._mock_attr_absent(m, dry_backend)
            # Option set absent: the probe 404s, but dry-run must NOT raise.
            m.get(dry_backend.url_for(
                "GlobalOptionSetDefinitions(Name='ghost_set')"),
                status_code=404, json={"error": {"code": "0x", "message": "no"}})
            out = ma.add_attribute(
                dry_backend, entity="account", kind="picklist",
                schema_name="new_Status", display_name="Status",
                optionset_name="ghost_set")
        assert out["_dry_run"] is True
        assert _exists(out["references"], "optionset") is False

    def test_lookup_dry_run_reports_target_entity(self, dry_backend):
        from crm.core import metadata_attrs as ma
        with requests_mock.Mocker() as m:
            # Relationship (auto-derived schema) absent → create proceeds.
            m.get(dry_backend.url_for(
                "RelationshipDefinitions(SchemaName='account_new_ownerid')"),
                status_code=404, json={"error": {"code": "0x", "message": "no"}})
            # target entity (referenced) exists; host entity (referencing) exists.
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='contact')"),
                  json={"MetadataId": "44444444-4444-4444-4444-444444444444"})
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"MetadataId": "55555555-5555-5555-5555-555555555555"})
            out = ma.add_attribute(
                dry_backend, entity="account", kind="lookup",
                schema_name="new_OwnerId", display_name="Owner",
                target_entity="contact")
        # The lookup's user-facing reference is the target entity (kind relabeled
        # from the underlying relationship's referenced_entity).
        assert _ref(out["references"], "target_entity") == {
            "kind": "target_entity", "value": "contact", "_exists": True}
        # No internal referenced/referencing kinds leak through.
        assert _ref(out["references"], "referenced_entity") is None
        assert _ref(out["references"], "referencing_entity") is None


class TestRegisterStepReferences:
    def _mock(self, m, backend, *, message_rows, plugintype_rows, filter_rows):
        m.get(backend.url_for("sdkmessages"), json={"value": message_rows})
        m.get(backend.url_for("plugintypes"), json={"value": plugintype_rows})
        m.get(backend.url_for("sdkmessagefilters"), json={"value": filter_rows})

    def test_dry_run_all_resolve(self, dry_backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            self._mock(
                m, dry_backend,
                message_rows=[{"sdkmessageid": "a1", "name": "Create"}],
                plugintype_rows=[{"plugintypeid": "b2", "typename": "X"}],
                filter_rows=[{"sdkmessagefilterid": "c3"}])
            out = plugin.register_step(
                dry_backend, message="Create", plugin_type="X", entity="account")
        assert out["_dry_run"] is True
        assert _ref(out["references"], "message") == {
            "kind": "message", "value": "Create", "_exists": True}
        assert _exists(out["references"], "plugin_type") is True
        assert _ref(out["references"], "entity") == {
            "kind": "entity", "value": "account", "_exists": True}

    def test_dry_run_dangling_message_and_type_no_raise(self, dry_backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            self._mock(
                m, dry_backend,
                message_rows=[],          # message absent
                plugintype_rows=[],        # plug-in type absent
                filter_rows=[])
            out = plugin.register_step(
                dry_backend, message="Nope", plugin_type="Ghost", entity="account")
        assert out["_dry_run"] is True
        assert _exists(out["references"], "message") is False
        assert _exists(out["references"], "plugin_type") is False
        # Entity filter cannot resolve without a valid message → reported absent.
        assert _exists(out["references"], "entity") is False

    def test_dry_run_message_level_step_omits_entity_ref(self, dry_backend):
        from crm.core import plugin
        with requests_mock.Mocker() as m:
            self._mock(
                m, dry_backend,
                message_rows=[{"sdkmessageid": "a1", "name": "Create"}],
                plugintype_rows=[{"plugintypeid": "b2", "typename": "X"}],
                filter_rows=[])
            out = plugin.register_step(dry_backend, message="Create", plugin_type="X")
        assert _exists(out["references"], "message") is True
        assert _ref(out["references"], "entity") is None


class TestResolveSpecReferences:
    """The scaffold/apply spec walker that resolves a table's column references
    (picklist option sets + lookup target entities) under dry-run."""

    def test_resolves_optionset_and_target_entity(self, dry_backend):
        from crm.core import references as refs
        from crm.core.scaffold import build_table_spec
        spec = build_table_spec(
            display_name="Project", prefix="new",
            columns=[
                "Owner:lookup:target_entity=account",
                "Status:picklist:optionset_name=contoso_status",
            ],
        )
        with requests_mock.Mocker() as m:
            m.get(dry_backend.url_for("EntityDefinitions(LogicalName='account')"),
                  json={"MetadataId": "11111111-1111-1111-1111-111111111111"})
            m.get(dry_backend.url_for(
                "GlobalOptionSetDefinitions(Name='contoso_status')"),
                status_code=404, json={"error": {"code": "0x", "message": "no"}})
            out = refs.resolve_spec_references(dry_backend, spec)
        assert _ref(out, "target_entity") == {
            "kind": "target_entity", "value": "account", "_exists": True}
        assert _ref(out, "optionset") == {
            "kind": "optionset", "value": "contoso_status", "_exists": False}

    def test_no_references_for_plain_columns(self, dry_backend):
        from crm.core import references as refs
        from crm.core.scaffold import build_table_spec
        spec = build_table_spec(
            display_name="Project", prefix="new",
            columns=["Code:string:max_length=50", "Count:integer"],
        )
        with requests_mock.Mocker():
            out = refs.resolve_spec_references(dry_backend, spec)
        assert out == []


class TestEmitWithWarningReferences:
    """A dangling reference surfaces as a meta.warnings advisory while the
    references array (with _exists flags) stays under data — envelope ok:true."""

    def _json_ctx(self):
        ctx = CLIContext()
        ctx.json_mode = True
        return ctx

    def test_dangling_reference_becomes_warning(self, capsys):
        ctx = self._json_ctx()
        data = {"_dry_run": True, "references": [
            {"kind": "referenced_entity", "value": "ghost", "_exists": False},
            {"kind": "referencing_entity", "value": "new_project", "_exists": True},
        ]}
        _emit_with_warning(ctx, data, None)
        env = json.loads(capsys.readouterr().out)
        assert env["ok"] is True
        assert env["meta"]["warnings"] == ["reference not found: referenced_entity='ghost'"]
        # references stay in data, flags intact
        assert env["data"]["references"][0]["_exists"] is False

    def test_all_references_resolve_no_warning(self, capsys):
        ctx = self._json_ctx()
        data = {"_dry_run": True, "references": [
            {"kind": "referenced_entity", "value": "account", "_exists": True},
        ]}
        _emit_with_warning(ctx, data, None)
        env = json.loads(capsys.readouterr().out)
        assert "warnings" not in env.get("meta", {})
