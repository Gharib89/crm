"""The v6 inventory kinds (#1344): SLAs, model-driven apps, alternate keys, environment
variables and connection references, through extract, diff, assess and packages, offline.

None of them exists on CRM 2011, so they are read on the Web API path: this file runs over
the synthetic org of test_webapi_inventory.py, to which it adds the kinds' rows and keys.
A CRM 2011 extract over test_legacy_inventory.py's org reads each as unsupported.
"""

# pyright: basic
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from crm.core import inventory, inventory_diff, inventory_extract
from crm.core.inventory_assess import assess
from crm.core.inventory_packages import build_packages
from crm.core.inventory_source_map import map_source
from crm.core.webapi_inventory import _ENTITY_SETS  # pyright: ignore[reportPrivateUsage]
from crm.tests import test_legacy_inventory as legacy
from crm.tests.test_legacy_inventory import reads  # noqa: F401
from crm.tests.test_webapi_inventory import (
    _ORG,
    FakeBackend,
    _entity_metadata,
    _extract,
    _id,
    _keys,
    _lookup,
    _manifest,
    exports,  # noqa: F401
)

NEW_KINDS = ("sla", "app_module", "alternate_key", "environment_variable", "connection_reference")
_CUSTOM = {"ismanaged": False, "solutionid": _id(2)}
_STOCK = {"ismanaged": True, "solutionid": _id(1)}


def _key(schema: str, columns: list[str], *, managed: bool = False, n: int = 600) -> dict:
    return {"SchemaName": schema, "LogicalName": schema.lower(), "KeyAttributes": columns,
            "IsManaged": managed, "MetadataId": _id(n)}  # fmt: skip


_KEYS: dict[str, list[dict]] = {
    "new_project": [_key("new_ProjectCode", ["new_code", "new_due"])],
    "account": [
        _key("new_AccountNumber", ["accountnumber"], n=601),
        _key("KeyForNoSqlEntityWithPKPartitionId", ["accountid", "partitionid"], n=602),
        _key("msdyn_StockKey", ["name"], managed=True, n=603),
    ],
}
"""Maps each entity to the alternate keys RetrieveEntity answers in its ``Keys``."""


def _rows(**extra: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {
        "slas": [
            {"slaid": _id(700), "name": "Gold", "primaryentityotc": 10001, "slatype": 1,
             "slaversion": 100000001, "isdefault": True, "applicablefrom": "createdon",
             "statecode": 1, **_CUSTOM},
        ],
        "appmodules": [
            {"appmoduleid": _id(710), "uniquename": "new_Projects", "name": "Projects",
             "clienttype": 4, "statecode": 0, **_CUSTOM},
        ],
        "environmentvariabledefinitions": [
            {"environmentvariabledefinitionid": _id(720), "schemaname": "new_ApiUrl",
             "displayname": "API URL", "type": 100000000,
             "defaultvalue": "https://api.contoso.com", **_CUSTOM},
        ],
        "connectionreferences": [
            {"connectionreferenceid": _id(730), "connectionreferencelogicalname": "new_sharepoint",
             "connectionreferencedisplayname": "SharePoint",
             "connectorid": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline",
             **_CUSTOM},
        ],
    }  # fmt: skip
    rows.update(extra)
    return rows


def _org(**extra: list[dict[str, Any]]) -> dict[str, list[list[dict]]]:
    org = copy.deepcopy(_ORG)
    for entity_set, page in _rows(**extra).items():
        org[entity_set] = [page]
    return org


class KeyedBackend(FakeBackend):
    """`FakeBackend` whose RetrieveEntity answers carry the alternate keys of *keys*."""

    def __init__(self, keys: dict[str, list[dict]] | None = None, **kw):
        super().__init__(**kw)
        self.keys = _KEYS if keys is None else keys

    def get(self, path: str, *, params: dict | None = None, extra_headers: dict | None = None):
        if path.startswith("RetrieveEntity("):
            self.paths.append(path)
            name = (params or {})["@n"].strip("'")
            meta = _entity_metadata(name)
            meta["Keys"] = copy.deepcopy(self.keys.get(name, []))
            return {"EntityMetadata": meta}
        return super().get(path, params=params, extra_headers=extra_headers)


def _records(out: Path, kind: str) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in _manifest(out)["records"] if r["kind"] == kind}


def test_each_new_kind_is_recorded_with_its_key_fields_and_complete_coverage(tmp_path):
    summary = _extract(KeyedBackend(org=_org()), tmp_path / "inv")
    assert summary.complete, summary.errors
    data = _manifest(tmp_path / "inv")
    assert data["schema_version"] == 6
    for kind in NEW_KINDS:
        assert data["coverage"][kind]["status"] == "complete", kind

    (sla,) = _records(tmp_path / "inv", "sla").values()
    assert (sla["key"], sla["id"], sla["origin"]) == ("Gold", _id(700), "custom")
    assert sla["fields"] == {
        "name": "Gold", "primary_entity": "new_project", "sla_type": 1, "sla_version": 100000001,
        "default": True, "applicable_from": "createdon", "state": 1,
    }  # fmt: skip

    (app,) = _records(tmp_path / "inv", "app_module").values()
    assert app["key"] == "new_Projects"
    assert app["fields"] == {
        "unique_name": "new_Projects", "name": "Projects", "client_type": 4, "state": 0,
    }  # fmt: skip

    keys = _records(tmp_path / "inv", "alternate_key")
    project = keys[
        inventory.component_key(
            "alternate_key", {"entity": "new_project", "schema_name": "new_ProjectCode"}
        )
    ]
    assert project["fields"] == {
        "entity": "new_project", "schema_name": "new_ProjectCode",
        "columns": ["new_code", "new_due"],
    }  # fmt: skip
    assert project["id"] == _id(600)

    (variable,) = _records(tmp_path / "inv", "environment_variable").values()
    assert variable["key"] == "new_ApiUrl"
    assert variable["fields"] == {
        "schema_name": "new_ApiUrl", "display_name": "API URL", "type": 100000000,
        "default_value": "https://api.contoso.com",
    }  # fmt: skip

    (reference,) = _records(tmp_path / "inv", "connection_reference").values()
    assert reference["key"] == "new_sharepoint"
    assert reference["fields"] == {
        "logical_name": "new_sharepoint", "display_name": "SharePoint",
        "connector": "/providers/Microsoft.PowerApps/apis/shared_sharepointonline",
    }  # fmt: skip


def test_the_default_scope_leaves_out_stock_rows_and_platform_keys(tmp_path):
    """Measured on a Dataverse org: the platform leaves its own NoSql keys unmanaged."""
    org = _org(
        appmodules=[
            {"appmoduleid": _id(710), "uniquename": "new_Projects", "name": "Projects", **_CUSTOM},
            {"appmoduleid": _id(711), "uniquename": "msdyn_Hub", "name": "Hub", **_STOCK},
        ],
    )
    _extract(KeyedBackend(org=org), tmp_path / "inv", include_system=False)
    assert sorted(_records(tmp_path / "inv", "app_module")) == ["new_Projects"]
    assert sorted(
        r["fields"]["schema_name"] for r in _records(tmp_path / "inv", "alternate_key").values()
    ) == [
        "new_AccountNumber",
        "new_ProjectCode",
    ]


def test_a_solution_run_keeps_only_the_member_records_of_each_new_kind(tmp_path):
    """Component type codes: sla 152, app module 80, entity key 14, environment variable 380.

    A connection reference's type is assigned per org (measured 10167 on a Dataverse org).
    A solution holding a key of a table it does not hold whole lists the table as a shell,
    so new_project (held whole) brings its key with it, while account (a shell) brings only
    the member key.
    """
    org = _org(
        slas=[
            {"slaid": _id(700), "name": "Gold", **_CUSTOM},
            {"slaid": _id(701), "name": "Silver", **_CUSTOM},
        ],
        connectionreferences=[
            {
                "connectionreferenceid": _id(730),
                "connectionreferencelogicalname": "new_a",
                **_CUSTOM,
            },
            {
                "connectionreferenceid": _id(731),
                "connectionreferencelogicalname": "new_b",
                **_CUSTOM,
            },
        ],
    )
    for code, n in ((152, 700), (80, 710), (14, 601), (380, 720), (10167, 730)):
        org["solutioncomponents"][0].append(
            {"solutioncomponentid": _id(900 + n), "componenttype": code, "objectid": _id(n),
             **_lookup("solutionid", "solution", 2)}
        )  # fmt: skip
    org["solutioncomponents"][0].append(
        {"solutioncomponentid": _id(899), "componenttype": 1, "objectid": _id(200),
         "rootcomponentbehavior": 2, **_lookup("solutionid", "solution", 2)}
    )  # fmt: skip
    _extract(KeyedBackend(org=org), tmp_path / "inv", solutions=("contoso",))
    data = _manifest(tmp_path / "inv")
    assert _keys(data, "sla") == ["Gold"]
    assert _keys(data, "app_module") == ["new_Projects"]
    assert _keys(data, "environment_variable") == ["new_ApiUrl"]
    assert _keys(data, "connection_reference") == ["new_a"]
    assert sorted(_keys(data, "alternate_key")) == sorted(
        inventory.component_key("alternate_key", {"entity": e, "schema_name": s})
        for e, s in (("new_project", "new_ProjectCode"), ("account", "new_AccountNumber"))
    )
    memberships = _keys(data, "solution_membership")
    assert "contoso|connection_reference|new_a" in memberships
    assert "contoso|sla|Gold" in memberships


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("sla", {"slas": [{"slaid": _id(700), "name": "Twin", **_CUSTOM},
                          {"slaid": _id(701), "name": "Twin", **_CUSTOM}]}),
        ("app_module", {"appmodules": [
            {"appmoduleid": _id(710), "uniquename": "Twin", **_CUSTOM},
            {"appmoduleid": _id(711), "uniquename": "Twin", **_CUSTOM}]}),
        ("environment_variable", {"environmentvariabledefinitions": [
            {"environmentvariabledefinitionid": _id(720), "schemaname": "Twin", **_CUSTOM},
            {"environmentvariabledefinitionid": _id(722), "schemaname": "Twin", **_CUSTOM}]}),
        ("connection_reference", {"connectionreferences": [
            {"connectionreferenceid": _id(730), "connectionreferencelogicalname": "Twin",
             **_CUSTOM},
            {"connectionreferenceid": _id(731), "connectionreferencelogicalname": "Twin",
             **_CUSTOM}]}),
    ],
)  # fmt: skip
def test_two_records_sharing_a_key_are_ambiguous_never_matched(tmp_path, kind, extra):
    _extract(KeyedBackend(org=_org(**extra)), tmp_path / "a")
    _extract(KeyedBackend(org=_org(**extra)), tmp_path / "b")
    inv = inventory.read_inventory(tmp_path / "a")
    (pair,) = [v for (k, _), v in inv.ambiguous().items() if k == kind]
    assert len(pair) == 2
    diff = inventory_diff.diff_inventories(
        *inventory_diff.read_pair(tmp_path / "a", tmp_path / "b")
    )
    (entry,) = [e for e in diff.entries if e.kind == kind]
    assert entry.verdict == "ambiguous"


def test_a_changed_key_column_and_a_changed_variable_default_diff_as_changed(tmp_path):
    _extract(KeyedBackend(org=_org()), tmp_path / "a")
    keys = copy.deepcopy(_KEYS)
    keys["new_project"][0]["KeyAttributes"] = ["new_code"]
    variables = _rows()["environmentvariabledefinitions"]
    variables[0]["defaultvalue"] = "https://prod.contoso.com"
    _extract(KeyedBackend(keys, org=_org(environmentvariabledefinitions=variables)), tmp_path / "b")
    diff = inventory_diff.diff_inventories(
        *inventory_diff.read_pair(tmp_path / "a", tmp_path / "b")
    )
    changed = {(e.kind, e.key): e for e in diff.entries if e.verdict == "changed"}
    project = inventory.component_key(
        "alternate_key", {"entity": "new_project", "schema_name": "new_ProjectCode"}
    )
    assert [f.name for f in changed[("alternate_key", project)].fields] == ["fields.columns"]
    assert [f.name for f in changed[("environment_variable", "new_ApiUrl")].fields] == [
        "fields.default_value"
    ]


def _v5(tmp_path: Path) -> Path:
    """The extraction of the synthetic org rewritten as a schema_version 5 manifest."""
    folder = tmp_path / "older"
    _extract(KeyedBackend(org=_org()), folder)
    data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    data["schema_version"] = 5
    data["records"] = [r for r in data["records"] if r["kind"] not in NEW_KINDS]
    for kind in NEW_KINDS:
        del data["coverage"][kind]
    (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_a_v5_manifest_reads_each_new_kind_as_unsupported_and_diffs_it_not_comparable(tmp_path):
    older = _v5(tmp_path)
    _extract(KeyedBackend(org=_org()), tmp_path / "newer")
    inv_a, inv_b = inventory_diff.read_pair(older, tmp_path / "newer")
    assert inv_a.schema_version == 6
    for kind in NEW_KINDS:
        coverage = inv_a.coverage[kind]
        assert (coverage.status, coverage.records) == ("unsupported", 0)
        assert coverage.errors == (
            f"not read: a schema_version 5 extraction predates the {kind} kind",
        )
    diff = inventory_diff.diff_inventories(inv_a, inv_b)
    slas = [e for e in diff.entries if e.kind == "sla"]
    assert slas and all(e.verdict == "not_comparable" for e in slas)


class _LegacySpy(legacy.FakeClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.fetches: list[str] = []

    def pages(self, fetch_xml, **kw):
        self.fetches.append(fetch_xml)
        return super().pages(fetch_xml, **kw)


@pytest.mark.usefixtures("reads")
def test_a_crm2011_extract_reads_no_new_kind_and_records_each_unsupported(tmp_path):
    client = _LegacySpy(org=copy.deepcopy(legacy._ORG))
    summary = legacy._extract(client, tmp_path / "inv")
    assert summary.complete
    data = legacy._manifest(tmp_path / "inv")
    for kind in NEW_KINDS:
        coverage = data["coverage"][kind]
        assert (coverage["status"], coverage["records"]) == ("unsupported", 0), kind
        assert coverage["errors"] == [f"not read: CRM 2011 has no {kind} component"], kind
    entities = ("sla", "appmodule", "environmentvariabledefinition", "connectionreference")
    assert not [f for f in client.fetches for e in entities if f'entity name="{e}"' in f]


def test_assess_lists_each_new_kind_as_uncovered_with_its_count(tmp_path):
    _extract(KeyedBackend(org=_org()), tmp_path / "inv")
    result = assess(tmp_path / "inv", "online")
    uncovered = {e["kind"]: e["records"] for e in result.uncovered_kinds()}
    assert {k: uncovered.get(k) for k in NEW_KINDS} == {
        "sla": 1,
        "app_module": 1,
        "alternate_key": 4,
        "environment_variable": 1,
        "connection_reference": 1,
    }
    assert not [f for f in result.findings if f.kind in NEW_KINDS]


def test_packages_places_every_record_of_the_new_kinds(tmp_path):
    _extract(KeyedBackend(org=_org()), tmp_path / "inv")
    (tmp_path / "repo").mkdir()
    source_map = map_source(tmp_path / "inv", tmp_path / "repo")
    result = build_packages(tmp_path / "inv", source_map)
    inv = inventory.read_inventory(tmp_path / "inv")
    wanted = {f"{r.kind}:{r.key}" for r in inv.records if r.kind in NEW_KINDS}
    placed = {m for p in result.packages for m in p.members}
    assert wanted and wanted <= placed


def test_the_web_api_reads_the_new_tables():
    sets = {
        "sla": "slas",
        "appmodule": "appmodules",
        "environmentvariabledefinition": "environmentvariabledefinitions",
        "connectionreference": "connectionreferences",
    }
    assert {e: _ENTITY_SETS[e][0] for e in sets} == sets
    crm2011 = {r.entity for r in inventory_extract.reads_for("crm2011")}
    for platform in ("onprem", "cloud"):
        assert set(sets) <= {r.entity for r in inventory_extract.reads_for(platform)} - crm2011
