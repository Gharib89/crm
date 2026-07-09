"""Audit-enablement (--audit/--no-audit) tests for the metadata create/update verbs.

`IsAuditEnabled` is a Dataverse `BooleanManagedProperty` on both `EntityMetadata`
and `AttributeMetadata`. These lock in the managed-property body shape the CLI
writes: an object `{"Value": <bool>}` (never a bare boolean), on create bodies and
on the retrieve-merge-write update bodies. All HTTP is mocked; no live server.
"""
# pyright: basic
from __future__ import annotations

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as ma_mod
from crm.core import metadata_update as mu_mod


_ATTR_ID = "33333333-3333-3333-3333-333333333333"


def _post_body(m):
    for r in m.request_history:
        if r.method == "POST":
            return r.json()
    raise AssertionError("no POST request recorded")


# ── create-entity ────────────────────────────────────────────────────────


def _mock_create_entity(m, backend):
    probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
    md_url = backend.url_for("EntityDefinitions(11111111-1111-1111-1111-111111111111)")
    m.get(probe, status_code=404, json={"error": {"code": "0x", "message": "no"}})
    m.post(backend.url_for("EntityDefinitions"), status_code=204,
           headers={"OData-EntityId": md_url})
    m.get(md_url, json={"LogicalName": "new_widget", "EntitySetName": "new_widgets"})


class TestCreateEntityAudit:
    def test_audit_on_writes_managed_property_object(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create_entity(m, backend)
            meta_mod.create_entity(
                backend, schema_name="new_Widget", display_name="Widget",
                is_audit_enabled=True,
            )
        assert _post_body(m)["IsAuditEnabled"] == {"Value": True}

    def test_audit_off_writes_managed_property_object(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create_entity(m, backend)
            meta_mod.create_entity(
                backend, schema_name="new_Widget", display_name="Widget",
                is_audit_enabled=False,
            )
        assert _post_body(m)["IsAuditEnabled"] == {"Value": False}

    def test_flag_omitted_leaves_body_unchanged(self, backend):
        with requests_mock.Mocker() as m:
            _mock_create_entity(m, backend)
            meta_mod.create_entity(
                backend, schema_name="new_Widget", display_name="Widget",
            )
        assert "IsAuditEnabled" not in _post_body(m)


# ── add-attribute ────────────────────────────────────────────────────────


def _mock_add_attribute(m, backend, entity="new_widget", attr="new_label"):
    m.get(
        backend.url_for(
            f"EntityDefinitions(LogicalName='{entity}')"
            f"/Attributes(LogicalName='{attr}')"
        ),
        status_code=404, json={"error": {"code": "0x", "message": "no"}},
    )
    attr_url = backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes({_ATTR_ID})"
    )
    m.post(
        backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Attributes"),
        status_code=204, headers={"OData-EntityId": attr_url},
    )
    m.get(attr_url, json={"LogicalName": attr, "SchemaName": attr,
                          "AttributeType": "String"})


class TestAddAttributeAudit:
    def test_audit_on_writes_managed_property_object(self, backend):
        with requests_mock.Mocker() as m:
            _mock_add_attribute(m, backend)
            ma_mod.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Label", display_name="Label",
                is_audit_enabled=True,
            )
        assert _post_body(m)["IsAuditEnabled"] == {"Value": True}

    def test_audit_off_writes_managed_property_object(self, backend):
        with requests_mock.Mocker() as m:
            _mock_add_attribute(m, backend)
            ma_mod.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Label", display_name="Label",
                is_audit_enabled=False,
            )
        assert _post_body(m)["IsAuditEnabled"] == {"Value": False}

    def test_flag_omitted_leaves_body_unchanged(self, backend):
        with requests_mock.Mocker() as m:
            _mock_add_attribute(m, backend)
            ma_mod.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Label", display_name="Label",
            )
        assert "IsAuditEnabled" not in _post_body(m)


# ── update-entity ────────────────────────────────────────────────────────

# Retrieved definition carries a full BooleanManagedProperty; the merge must
# overwrite only Value and preserve CanBeChanged.
_ENTITY_WITH_AUDIT = {
    "@odata.type": "#Microsoft.Dynamics.CRM.EntityMetadata",
    "SchemaName": "new_Project",
    "LogicalName": "new_project",
    "IsAuditEnabled": {"Value": True, "CanBeChanged": True},
}


class TestUpdateEntityAudit:
    def test_merge_sets_value_and_preserves_can_be_changed(self, backend):
        path = backend.url_for("EntityDefinitions(LogicalName='new_project')")
        with requests_mock.Mocker() as m:
            m.get(path, json=_ENTITY_WITH_AUDIT)
            m.put(path, status_code=204)
            mu_mod.update_entity(backend, "new_project", is_audit_enabled=False)
        body = m.request_history[-1].json()
        assert body["IsAuditEnabled"] == {"Value": False, "CanBeChanged": True}

    def test_audit_alone_satisfies_nothing_to_update_guard(self, backend):
        path = backend.url_for("EntityDefinitions(LogicalName='new_project')")
        with requests_mock.Mocker() as m:
            m.get(path, json=_ENTITY_WITH_AUDIT)
            m.put(path, status_code=204)
            # Must not raise "nothing to update".
            out = mu_mod.update_entity(backend, "new_project", is_audit_enabled=True)
        assert out["updated"] is True


# ── update-attribute ─────────────────────────────────────────────────────

_STRING_ATTR_WITH_AUDIT = {
    "@odata.type": "#Microsoft.Dynamics.CRM.StringAttributeMetadata",
    "SchemaName": "new_Code",
    "LogicalName": "new_code",
    "MaxLength": 100,
    "FormatName": {"Value": "Text"},
    "IsAuditEnabled": {"Value": True, "CanBeChanged": True},
}


class TestUpdateAttributeAudit:
    def test_merge_sets_value_and_preserves_other_props(self, backend):
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_code')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_STRING_ATTR_WITH_AUDIT)
            m.get(cast, json=_STRING_ATTR_WITH_AUDIT)
            m.put(cast, status_code=204)
            mu_mod.update_attribute(backend, "new_project", "new_code",
                                    is_audit_enabled=False)
        body = m.request_history[-1].json()
        assert body["IsAuditEnabled"] == {"Value": False, "CanBeChanged": True}
        # Type-specific props survive the merge.
        assert body["MaxLength"] == 100
        assert body["FormatName"] == {"Value": "Text"}

    def test_audit_alone_satisfies_nothing_to_update_guard(self, backend):
        base = backend.url_for(
            "EntityDefinitions(LogicalName='new_project')"
            "/Attributes(LogicalName='new_code')"
        )
        cast = base + "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        with requests_mock.Mocker() as m:
            m.get(base, json=_STRING_ATTR_WITH_AUDIT)
            m.get(cast, json=_STRING_ATTR_WITH_AUDIT)
            m.put(cast, status_code=204)
            out = mu_mod.update_attribute(backend, "new_project", "new_code",
                                          is_audit_enabled=True)
        assert out["updated"] is True


# ── CLI flag forwarding ──────────────────────────────────────────────────

# Each case: (core function attribute on its module, argv, expected value the
# CLI must forward as is_audit_enabled). Confirms --audit/--no-audit parses on
# every verb and reaches core with the right boolean.
_SOL = ["--solution", "Default"]
_FORWARD_CASES = [
    (meta_mod, "create_entity",
     ["metadata", "create-entity", "--schema-name", "new_Widget",
      "--display", "Widget", "--audit", *_SOL], True),
    (ma_mod, "add_attribute",
     ["metadata", "add-attribute", "new_widget", "--kind", "string",
      "--schema-name", "new_Label", "--display", "Label", "--no-audit", *_SOL],
     False),
    (mu_mod, "update_entity",
     ["metadata", "update-entity", "new_widget", "--audit", *_SOL], True),
    (mu_mod, "update_attribute",
     ["metadata", "update-attribute", "new_widget", "new_code", "--no-audit", *_SOL],
     False),
]


@pytest.mark.parametrize("module, func_name, argv, expected", _FORWARD_CASES)
def test_audit_flag_forwarded_to_core(module, func_name, argv, expected,
                                      inject_backend, make_fake_backend, monkeypatch):
    inject_backend(make_fake_backend())
    captured: dict = {}

    def _stub(*_args, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(module, func_name, _stub)
    # maybe_publish runs on the returned dict for create verbs; make it a no-op.
    monkeypatch.setattr(meta_mod, "maybe_publish", lambda *a, **k: None)
    result = CliRunner().invoke(cli, ["--json", *argv])
    assert "no such option" not in result.output.lower(), result.output
    assert captured.get("is_audit_enabled") is expected, result.output
