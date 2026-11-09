"""The default migration scope of `crm inventory extract`: stock components are left out.

Runs over the synthetic CRM 2011 org of test_legacy_inventory.py, to which it adds managed
stock rows. The rule is shared by every source (`_READS`), so one fake org covers it.
"""

# pyright: basic
from __future__ import annotations

import base64
import copy
import json

import pytest

from crm.core import inventory, inventory_diff, inventory_extract, legacy_reads
from crm.core.inventory import InventoryError
from crm.tests.test_legacy_inventory import (
    _ORG,
    FakeClient,
    _extract,
    _id,
    _keys,
    _manifest,
    _ref,
    reads,  # noqa: F401  (a fixture, used through pytestmark)
)
from crm.utils.d365_backend import D365Error

pytestmark = pytest.mark.usefixtures("reads")

_STOCK_SOLUTION = {
    "id": _id(3), "uniquename": "msdyn_patch", "friendlyname": "Patch", "version": "9.0",
    "ismanaged": True, "isvisible": True, "publisherid": _ref("publisher", 90, "MS"),
}  # fmt: skip


def _org() -> dict:
    org = copy.deepcopy(_ORG)
    org["solution"][0].append(_STOCK_SOLUTION)
    owner = _ref("solution", 3)
    org["webresource"][0].append(
        {
            "id": _id(12),
            "name": "msdyn_/stock.js",
            "displayname": None,
            "webresourcetype": 3,
            "ismanaged": True,
            "solutionid": owner,
            "content": base64.b64encode(b"stock").decode(),
        }
    )
    org["pluginassembly"][0].append(
        {
            "id": _id(22),
            "name": "Microsoft.Stock",
            "version": "9.0.0.0",
            "culture": "neutral",
            "publickeytoken": "123",
            "sourcetype": 0,
            "isolationmode": 2,
            "ismanaged": True,
            "solutionid": owner,
            "content": base64.b64encode(b"MZstock").decode(),
        }
    )
    org["systemform"][0].append(
        {
            "id": _id(61),
            "name": "Custom form",
            "objecttypecode": "new_project",
            "type": 2,
            "ismanaged": False,
            "solutionid": _ref("solution", 2),
            "formxml": "<form/>",
        }
    )
    org["savedquery"][0].append(
        {
            "id": _id(71),
            "name": "Stock view",
            "returnedtypecode": "account",
            "querytype": 0,
            "ismanaged": True,
            "solutionid": owner,
            "fetchxml": "<fetch/>",
            "layoutxml": "<grid/>",
        }
    )
    org["solutioncomponent"][0].append(
        {
            "id": _id(103),
            "solutionid": _ref("solution", 2),
            "componenttype": 61,
            "objectid": _id(12),
        }
    )
    org["dependency"][0].append(
        {
            "id": _id(111),
            "dependentcomponenttype": 92,
            "dependentcomponentobjectid": _id(40),
            "requiredcomponenttype": 61,
            "requiredcomponentobjectid": _id(12),
            "dependencytype": 2,
        }
    )
    return org


def _keys_of(data: dict) -> set[tuple[str, str]]:
    return {(r["kind"], r["key"]) for r in data["records"]}


@pytest.fixture
def stock(tmp_path):
    """The default-scope and the --include-system manifests of the same org."""
    org = _org()
    default = tmp_path / "default"
    full = tmp_path / "full"
    _extract(FakeClient(org=org), default, include_system=False)
    _extract(FakeClient(org=org), full)
    return _manifest(default), _manifest(full), default, full


def test_the_default_drops_stock_rows_and_keeps_their_unmanaged_counterparts(stock):
    data, full, *_ = stock
    assert _keys(data, "web_resource") == ["new_/img/logo.png", "new_/scripts/project.js"]
    assert _keys(full, "web_resource") == [
        "msdyn_/stock.js", "new_/img/logo.png", "new_/scripts/project.js",
    ]  # fmt: skip
    assert _keys(data, "plugin_assembly") == ["Contoso.Plugins", "Legacy.OnDisk"]
    assert "Microsoft.Stock" in _keys(full, "plugin_assembly")
    # form 60 is unmanaged but owned by the managed System solution: stock.
    assert _keys(data, "form") == ["new_project|2|Custom form"]
    assert _keys(data, "view") == ["new_project|Active Projects"]
    assert _keys(full, "view") == ["account|Stock view", "new_project|Active Projects"]
    assert _keys(data, "sitemap") == []
    assert _keys(data, "solution") == ["System", "contoso", "msdyn_patch"]


def test_a_stock_entity_holding_a_kept_relationship_stays_as_a_reference_row(stock):
    data, full, *_ = stock
    # account is stock, but the custom relationship names it; new_project_account is custom.
    assert _keys(data, "entity") == ["account", "new_project", "new_project_account"]
    assert _keys(data, "attribute") == []
    assert _keys(data, "relationship") == ["account_new_project"]
    assert len(_keys(full, "attribute")) == 3


def test_a_kept_rows_edge_to_a_left_out_row_is_counted_never_dropped(stock):
    data, full, *_ = stock
    deps = [r["fields"] for r in data["records"] if r["kind"] == "dependency"]
    assert [d["required_key"] for d in deps] == ["new_project"]
    assert len([r for r in full["records"] if r["kind"] == "dependency"]) == 2
    platform = {
        g["description"]: g["reason"] for g in data["gaps"] if g["reason"].startswith("platform:")
    }
    assert list(platform) == [
        "1 dependency row(s) naming a stock web_resource that this inventory leaves out",
        "1 solution_membership row(s) naming a stock web_resource that this inventory leaves out",
    ]
    assert not [g for g in full["gaps"] if g["reason"].startswith("platform:")]
    assert _keys(data, "solution_membership") == [
        "contoso|entity|new_project",
        "contoso|web_resource|new_/scripts/project.js",
    ]


def test_include_system_keeps_today_s_manifest_and_the_default_records_its_scope(stock):
    data, full, default, fulldir = stock
    assert full["scope"] == {"mode": "org", "solutions": []}
    assert data["scope"] == {"mode": "org", "solutions": [], "system": "excluded"}
    assert inventory.read_inventory(fulldir).scope.system == "included"
    assert inventory.read_inventory(default).scope.system == "excluded"
    assert _keys_of(data) < _keys_of(full)


def test_a_manifest_without_the_system_field_loads_as_full(stock):
    _, _, _, fulldir = stock
    manifest = json.loads((fulldir / "manifest.json").read_text())
    assert "system" not in manifest["scope"]
    assert inventory.parse_manifest(manifest).scope.system == "included"


def test_diff_refuses_a_scoped_manifest_against_a_full_one(stock):
    _, _, default, full = stock
    with pytest.raises(InventoryError, match="differ in stock scope"):
        inventory_diff.read_pair(default, full)
    inventory_diff.read_pair(default, default)


def test_a_stock_row_s_payload_is_never_fetched(tmp_path):
    class Spy(FakeClient):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.fetches: list[str] = []

        def pages(self, fetch_xml, **kw):
            self.fetches.append(fetch_xml)
            return super().pages(fetch_xml, **kw)

    client = Spy(org=_org())
    _extract(client, tmp_path / "inv", include_system=False)
    by_id = [f for f in client.fetches if "<value>" in f]
    assert any(_id(10) in f for f in by_id)
    # Each by-id read filters on the entity's own primary key: systemform's is formid.
    assert any('attribute="formid" operator="in"' in f for f in by_id)
    assert not any("systemformid" in f for f in by_id)
    assert not any(_id(12) in f or _id(22) in f or _id(71) in f for f in by_id)
    assert not any(
        '<attribute name="content"/>' in f and "<value>" not in f for f in client.fetches
    )


def test_the_ribbon_rule_keeps_a_custom_entry_and_drops_a_stock_only_ribbon(
    monkeypatch,
    tmp_path,
):
    xml = {
        "new_project": "<R/>",  # a custom entity's ribbon is kept whatever it holds
        "account": '<R><Button Id="Mscrm.Form.account.Save"/><Tab Id="AIBuilder.a.Dummy"/></R>',
        None: (
            '<R><HideCustomAction HideActionId="Mscrm.Form.account.Save.HideAction"'
            ' Location="Mscrm.Form.account.Save"/></R>'
        ),
    }
    monkeypatch.setattr(legacy_reads, "ribbon_xml", lambda c, e: xml.get(e, "<R/>").encode())
    _extract(FakeClient(org=_org()), tmp_path / "inv", include_system=False)
    assert _keys(_manifest(tmp_path / "inv"), "ribbon") == ["", "new_project"]


def test_a_global_option_set_that_is_managed_is_stock(monkeypatch, tmp_path):
    sets = [
        {"name": "new_tier", "metadata_id": _id(300), "global": True, "custom": True,
         "managed": False, "type": "Picklist", "options": []},
        {"name": "msdyn_stage", "metadata_id": _id(301), "global": True, "custom": True,
         "managed": True, "type": "Picklist", "options": []},
    ]  # fmt: skip
    monkeypatch.setattr(legacy_reads, "list_option_sets", lambda c: sets)
    _extract(FakeClient(org=_org()), tmp_path / "inv", include_system=False)
    assert _keys(_manifest(tmp_path / "inv"), "global_option_set") == ["new_tier"]
    _extract(FakeClient(org=_org()), tmp_path / "full")
    full = [r for r in _manifest(tmp_path / "full")["records"] if r["kind"] == "global_option_set"]
    assert {r.get("managed") for r in full} <= {None}  # --include-system records no new field


def test_an_edge_to_a_key_a_kept_row_also_holds_is_not_a_stock_edge(tmp_path):
    org = _org()
    base = tmp_path / "base"
    _extract(FakeClient(org=org), base, include_system=False)
    clash = copy.deepcopy(org)
    clash["webresource"][0].append(
        {
            "id": _id(13),
            "name": "new_/scripts/project.js",
            "displayname": None,
            "webresourcetype": 3,
            "ismanaged": True,
            "solutionid": _ref("solution", 3),
            "content": base64.b64encode(b"stock").decode(),
        }
    )
    _extract(FakeClient(org=clash), tmp_path / "clash", include_system=False)
    for kind in ("web_resource", "solution_membership", "dependency"):
        assert _keys(_manifest(tmp_path / "clash"), kind) == _keys(_manifest(base), kind)


def test_a_kind_with_no_ismanaged_is_kept_and_recorded_as_a_gap(tmp_path):
    org = _org()
    for row in org["savedquery"][0]:
        row.pop("ismanaged")
    _extract(FakeClient(org=org), tmp_path / "inv", include_system=False)
    data = _manifest(tmp_path / "inv")
    assert _keys(data, "view") == ["account|Stock view", "new_project|Active Projects"]
    assert [g for g in data["gaps"] if g["kind"] == "view" and "no ismanaged" in g["description"]]


def test_a_solution_run_keeps_stock_and_records_it(tmp_path):
    inventory_extract.extract(
        inventory_extract.LegacySource(FakeClient(org=_org())),  # pyright: ignore[reportArgumentType]
        tmp_path / "sol",
        cli_version="9.9.9",
        solutions=("msdyn_patch",),
        now=lambda: "2026-11-04T10:00:00Z",
    )
    data = _manifest(tmp_path / "sol")
    assert data["scope"] == {"mode": "solutions", "solutions": ["msdyn_patch"]}


def test_a_checkpoint_of_another_stock_scope_is_refused(tmp_path):
    out = tmp_path / "inv"
    fault = D365Error("x", status=500)
    _extract(FakeClient(org=_org(), fail={("systemform", 1): fault}), out, include_system=False)
    with pytest.raises(D365Error, match="another stock scope"):
        _extract(FakeClient(org=_org()), out)
    assert _extract(FakeClient(org=_org()), out, include_system=False).complete


@pytest.mark.parametrize(
    ("xml", "custom"),
    [
        ('<R><Button Id="new_project.form.Approve"/></R>', True),
        ('<R><Button Id="Mscrm.Form.account.Save"/><Tab Id="AIBuilder.a.Dummy"/></R>', False),
        ("<R/>", False),
    ],
)
def test_ribbon_custom_reads_the_entry_ids(tmp_path, xml, custom):
    path = tmp_path / "ribbon.xml"
    path.write_text(xml, encoding="utf-8")
    assert inventory_extract._ribbon_custom(path) is custom


def _platform_chain_org() -> dict:
    """The org plus a platform plug-in chain whose own rows read unmanaged (as on Dataverse)."""
    org = _org()
    unmanaged = _ref("solution", 2)
    org["plugintype"][0].append(
        {"id": _id(32), "typename": "Microsoft.Stock.Plugin", "friendlyname": "Stock",
         "pluginassemblyid": _ref("pluginassembly", 22, "Microsoft.Stock"),
         "isworkflowactivity": False, "ismanaged": False, "solutionid": unmanaged}
    )  # fmt: skip
    org["sdkmessageprocessingstep"][0].append(
        {"id": _id(42), "name": "Stock: Update", "stage": 40, "mode": 0, "rank": 1,
         "plugintypeid": _ref("plugintype", 32), "statecode": 0, "filteringattributes": None,
         "supporteddeployment": 0, "ismanaged": False, "solutionid": unmanaged,
         "message.name": "Update", "filter.primaryobjecttypecode": "new_project"}
    )  # fmt: skip
    org["sdkmessageprocessingstepimage"][0].append(
        {"id": _id(43), "name": "pre", "entityalias": "stockpre", "imagetype": 0,
         "attributes": None, "messagepropertyname": "Target",
         "sdkmessageprocessingstepid": _ref("step", 42), "ismanaged": False,
         "solutionid": unmanaged}
    )  # fmt: skip
    return org


def test_a_step_on_a_managed_assembly_is_stock_with_its_type_and_assembly(tmp_path):
    out = tmp_path / "default"
    _extract(FakeClient(org=_platform_chain_org()), out, include_system=False)
    data = _manifest(out)
    assert _keys(data, "plugin_assembly") == ["Contoso.Plugins", "Legacy.OnDisk"]
    assert _keys(data, "plugin_type") == ["Contoso.Plugins|Contoso.Plugins.ProjectPlugin"]
    assert [k.split("|")[-1] for k in _keys(data, "step")] == ["Project: Update"]
    assert len(_keys(data, "step_image")) == 1
    assert not [
        r
        for r in data["records"]
        if r["kind"] != "solution" and r["origin"] == "custom" and r["managed"]
    ]


def test_a_managed_entity_kept_as_a_parent_reads_origin_system(tmp_path):
    cp = inventory_extract.Checkpoint(tmp_path)
    builder = inventory_extract._Builder(cp, tmp_path, {}, "web-api", True)
    builder.add("entity", "e1", {"logical_name": "msdyn_thing", "object_type_code": 1},
                None, "custom", True, [], 1)  # fmt: skip
    builder.add("attribute", "a1", {"entity": "msdyn_thing", "logical_name": "new_x",
                                    "type": "String", "required_level": "None",
                                    "option_set": None},
                None, "custom", False, [], 2)  # fmt: skip
    builder.drop_stock()
    entity = next(p for p in builder.pending if p.kind == "entity")
    assert (entity.managed, entity.origin) == (True, "system")
