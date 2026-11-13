"""The default scope keeps a stock component the org customised (#1284).

On a CRM 2011 source a customised stock row keeps ``ismanaged = true`` and moves to the
``Active`` solution (the unmanaged layer). Such a row is not stock; a row a managed solution
owns still is. Offline at the extract seam, over the CRM 2011 and the Web API sources.
"""

# pyright: basic
from __future__ import annotations

import base64
import copy

import pytest

from crm.core import inventory_extract
from crm.core.webapi_inventory import WebApiSource
from crm.tests import test_webapi_inventory as wa
from crm.tests.test_inventory_report import _FETCH
from crm.tests.test_legacy_inventory import (  # noqa: F401
    _ORG,
    FakeClient,
    _id,
    _keys,
    _manifest,
    _ref,
    reads,
)
from crm.tests.test_legacy_inventory import _extract as legacy_extract
from crm.tests.test_webapi_inventory import exports  # noqa: F401  (autouse: stubs the export)

pytestmark = pytest.mark.usefixtures("reads")

_ACTIVE = {
    "id": _id(4), "uniquename": "Active", "friendlyname": "Active Solution", "version": "1.0",
    "ismanaged": False, "isvisible": False, "publisherid": _ref("publisher", 91, "Contoso"),
}  # fmt: skip
_PATCH = {
    "id": _id(3), "uniquename": "msdyn_patch", "friendlyname": "Patch", "version": "9.0",
    "ismanaged": True, "isvisible": True, "publisherid": _ref("publisher", 90, "MS"),
}  # fmt: skip
_BODY = _FETCH


def _legacy_org() -> dict:
    org = copy.deepcopy(_ORG)
    org["solution"][0] += [_ACTIVE, _PATCH]
    active, system, patch = _ref("solution", 4), _ref("solution", 1), _ref("solution", 3)

    def row(n: int, owner: dict, **fields) -> dict:
        return {"id": _id(n), "ismanaged": True, "solutionid": owner, **fields}

    # The org's one site map row, customised: managed flag kept, owner moved to Active.
    org["sitemap"] = [[row(80, active, sitemapxml="<SiteMap><Area Id='new_a'/></SiteMap>")]]
    org["webresource"][0] += [
        row(12, active, name="msdyn_/custom.js", webresourcetype=3, displayname=None,
            content=base64.b64encode(b"active").decode()),
        row(13, system, name="msdyn_/stock.js", webresourcetype=3, displayname=None,
            content=base64.b64encode(b"stock").decode()),
        row(14, patch, name="msdyn_/patch.js", webresourcetype=3, displayname=None,
            content=base64.b64encode(b"patch").decode()),
    ]  # fmt: skip
    org["systemform"][0].append(
        row(62, active, name="Active form", objecttypecode="account", type=2, formxml="<form/>")
    )
    org["savedquery"][0].append(
        row(72, active, name="Active view", returnedtypecode="account", querytype=0,
            fetchxml="<fetch/>", layoutxml="<grid/>")
    )  # fmt: skip
    org["report"] = [[
        row(n, owner, name=name, reporttypecode=1, ispersonal=False, iscustomreport=False,
            languagecode=1033, bodytext=_BODY)
        for n, owner, name in ((300, active, "Active report"), (301, system, "Stock report"))
    ]]  # fmt: skip
    org["pluginassembly"][0].append(
        row(23, active, name="Active.Assembly", version="1.0.0.0", culture="neutral",
            publickeytoken="1", sourcetype=0, isolationmode=2,
            content=base64.b64encode(b"MZactive").decode())
    )  # fmt: skip
    org["pluginassembly"][0].append(
        row(24, system, name="Stock.Assembly", version="1.0.0.0", culture="neutral",
            publickeytoken="2", sourcetype=0, isolationmode=2,
            content=base64.b64encode(b"MZstock").decode())
    )  # fmt: skip
    org["plugintype"][0] += [
        {"id": _id(31), "typename": "Active.Type", "friendlyname": "T",
         "pluginassemblyid": _ref("pluginassembly", 23), "isworkflowactivity": False,
         "ismanaged": False, "solutionid": active},
        {"id": _id(32), "typename": "Stock.Type", "friendlyname": "T",
         "pluginassemblyid": _ref("pluginassembly", 24), "isworkflowactivity": False,
         "ismanaged": False, "solutionid": active},
    ]  # fmt: skip
    return org


def test_a_customised_stock_row_owned_by_active_is_kept_with_its_payload(tmp_path):
    legacy_extract(FakeClient(org=_legacy_org()), tmp_path / "inv", include_system=False)
    data = _manifest(tmp_path / "inv")
    assert _keys(data, "sitemap") == [""]
    (site_map,) = [r for r in data["records"] if r["kind"] == "sitemap"]
    assert site_map["artifacts"]
    assert _keys(data, "web_resource")[0] == "msdyn_/custom.js"
    assert "msdyn_/custom.js" in _keys(data, "web_resource")
    assert "Active form" in "".join(_keys(data, "form"))
    assert "Active view" in "".join(_keys(data, "view"))
    assert _keys(data, "report") == ["Active report"]
    assert all(r["artifacts"] for r in data["records"] if r["kind"] in {"report", "form", "view"}
               and ("Active" in r["key"]))  # fmt: skip
    assert data["coverage"]["sitemap"]["status"] == "complete"


def test_a_managed_row_owned_by_system_or_a_managed_solution_is_still_left_out(tmp_path):
    legacy_extract(FakeClient(org=_legacy_org()), tmp_path / "inv", include_system=False)
    data = _manifest(tmp_path / "inv")
    assert "msdyn_/stock.js" not in _keys(data, "web_resource")
    assert "msdyn_/patch.js" not in _keys(data, "web_resource")
    assert "Stock report" not in _keys(data, "report")


def test_an_active_owned_assembly_is_kept_and_the_chain_rule_still_holds(tmp_path):
    legacy_extract(FakeClient(org=_legacy_org()), tmp_path / "inv", include_system=False)
    data = _manifest(tmp_path / "inv")
    assert "Active.Assembly" in _keys(data, "plugin_assembly")
    assert "Stock.Assembly" not in _keys(data, "plugin_assembly")
    types = _keys(data, "plugin_type")
    assert "Active.Assembly|Active.Type" in types
    assert (
        "Stock.Assembly|Stock.Type" not in types
    )  # unmanaged and Active-owned, but its assembly is stock


def test_include_system_keeps_every_row_as_before(tmp_path):
    legacy_extract(FakeClient(org=_legacy_org()), tmp_path / "inv", include_system=True)
    data = _manifest(tmp_path / "inv")
    assert {"msdyn_/stock.js", "msdyn_/custom.js", "msdyn_/patch.js"} <= set(
        _keys(data, "web_resource")
    )
    assert _keys(data, "report") == ["Active report", "Stock report"]


def test_the_web_api_source_keeps_a_customised_stock_row_owned_by_active(tmp_path):
    org = copy.deepcopy(wa._ORG)
    org["solutions"][0].append(
        {"solutionid": _id(4), "uniquename": "Active", "friendlyname": "Active",
         "version": "1.0", "ismanaged": False, "isvisible": False,
         **wa._lookup("publisherid", "publisher", 91, "Contoso")}
    )  # fmt: skip
    org["sitemaps"] = [[
        {"sitemapid": _id(80), "ismanaged": True, "solutionid": _id(4),
         "sitemapxml": "<SiteMap><Area Id='new_a'/></SiteMap>"}
    ]]  # fmt: skip
    org["systemforms"][0].append(
        {"formid": _id(62), "name": "Active form", "objecttypecode": "account", "type": 2,
         "ismanaged": True, "solutionid": _id(4), "formxml": "<form/>", "formactivationstate": 1}
    )  # fmt: skip
    org["systemforms"][0].append(
        {"formid": _id(63), "name": "Stock form", "objecttypecode": "account", "type": 2,
         "ismanaged": True, "solutionid": _id(1), "formxml": "<form/>", "formactivationstate": 1}
    )  # fmt: skip
    inventory_extract.extract(
        WebApiSource(wa.FakeBackend(org=org)),  # pyright: ignore[reportArgumentType]
        tmp_path / "inv", cli_version="9.9.9",
        include_system=False, now=lambda: "2026-11-04T10:00:00Z",
    )  # fmt: skip
    data = wa._manifest(tmp_path / "inv")
    (site_map,) = [r for r in data["records"] if r["kind"] == "sitemap"]
    assert site_map["artifacts"]
    forms = wa._keys(data, "form")
    assert any("Active form" in k for k in forms)
    assert not any("Stock form" in k for k in forms)
