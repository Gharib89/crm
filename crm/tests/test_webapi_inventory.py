"""`crm inventory extract` on a v9 on-prem or Dataverse online profile, offline.

A fake backend answers the Web API reads (FetchXML pages with their paging
cookie, the metadata functions, the ribbons) in the wire's own JSON shape, so
these tests drive `crm.core.webapi_inventory` and the shared extractor in
`crm.core.inventory_extract` together.
"""

# pyright: basic
from __future__ import annotations

import base64
import copy
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import quote

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory, inventory_extract, solution_transfer
from crm.core import session as session_mod
from crm.core.webapi_inventory import WebApiSource
from crm.tests.ribbon_zip import default_zip
from crm.utils.d365_backend import (
    _TRANSPORT_FAILURE_PREFIX,  # pyright: ignore[reportPrivateUsage]
    ConnectionProfile,
    D365Error,
)
from crm.utils.safe_xml import fromstring

_URL = "https://contoso.crm.dynamics.com"
_SECRET = "s3cr3t-client-secret-value"
_SCHEMA = Path(inventory.__file__).parent.parent / "schemas" / "inventory-manifest-v3.schema.json"
_FMT = "@OData.Community.Display.V1.FormattedValue"
_LOOKUP = "@Microsoft.Dynamics.CRM.lookuplogicalname"


def _id(n: int) -> str:
    return f"90000000-0000-0000-0000-{n:012d}"


def _lookup(column: str, entity: str, n: int, name: str | None = None) -> dict:
    ref = {f"_{column}_value": _id(n), f"_{column}_value{_LOOKUP}": entity}
    if name is not None:
        ref[f"_{column}_value{_FMT}"] = name
    return ref


def _label(text: str | None) -> dict:
    return {"UserLocalizedLabel": None if text is None else {"Label": text}}


_JS = b"function approve() { Xrm.Navigation.openUrl('/ISV/Approve.aspx'); }\r\n"
_DLL = b"MZ\x90\x00binary"

# entity set -> pages of rows, in the Web API's FetchXML response shape.
_ORG: dict[str, list[list[dict]]] = {
    "solutions": [[
        {"solutionid": _id(1), "uniquename": "System", "friendlyname": "System",
         "version": "9.2", "ismanaged": True, "isvisible": False,
         **_lookup("publisherid", "publisher", 90, "MS")},
        {"solutionid": _id(2), "uniquename": "contoso", "friendlyname": "Contoso",
         "version": "1.0", "ismanaged": False, "isvisible": True,
         **_lookup("publisherid", "publisher", 91, "Contoso")},
    ]],
    "webresourceset": [
        [{"webresourceid": _id(10), "name": "new_/scripts/project.js",
          "displayname": "project.js", "webresourcetype": 3, "ismanaged": False,
          "solutionid": _id(2), "content": base64.b64encode(_JS).decode()}],
        [{"webresourceid": _id(11), "name": "new_/img/logo.png", "webresourcetype": 5,
          "ismanaged": False, "solutionid": _id(2),
          "content": base64.b64encode(b"\x89PNG").decode()}],
    ],
    "pluginassemblies": [[
        {"pluginassemblyid": _id(20), "name": "Contoso.Plugins", "version": "1.0.0.0",
         "culture": "neutral", "publickeytoken": "abc", "sourcetype": 0, "ismanaged": False,
         "solutionid": _id(2), "content": base64.b64encode(_DLL).decode()},
        {"pluginassemblyid": _id(21), "name": "Contoso.InGac", "version": "1.0.0.0",
         "culture": "neutral", "publickeytoken": "def", "sourcetype": 2, "ismanaged": False,
         "solutionid": _id(2)},
        {"pluginassemblyid": _id(22), "name": "Contoso.OnDisk", "version": "1.0.0.0",
         "culture": "neutral", "publickeytoken": "fed", "sourcetype": 1, "ismanaged": False,
         "solutionid": _id(2)},
    ]],
    "plugintypes": [[
        {"plugintypeid": _id(30), "typename": "Contoso.Plugins.ProjectPlugin",
         "friendlyname": "Project", "isworkflowactivity": False, "ismanaged": False,
         "solutionid": _id(2), **_lookup("pluginassemblyid", "pluginassembly", 20,
                                         "Contoso.Plugins")},
    ]],
    "sdkmessageprocessingsteps": [[
        {"sdkmessageprocessingstepid": _id(40), "name": "Project: Update", "stage": 40,
         "mode": 0, "rank": 1, "statecode": 0, "supporteddeployment": 0, "ismanaged": False,
         "solutionid": _id(2), **_lookup("plugintypeid", "plugintype", 30),
         "message.name": "Update", "filter.primaryobjecttypecode": "new_project"},
    ]],
    "sdkmessageprocessingstepimages": [[
        {"sdkmessageprocessingstepimageid": _id(41), "name": "pre", "entityalias": "pre",
         "imagetype": 0, "messagepropertyname": "Target", "ismanaged": False,
         "solutionid": _id(2),
         **_lookup("sdkmessageprocessingstepid", "sdkmessageprocessingstep", 40)},
    ]],
    "workflows": [[
        {"workflowid": _id(50), "name": "Project approval", "category": 0,
         "primaryentity": "new_project", "statecode": 1, "ismanaged": False,
         "solutionid": _id(2), "xaml": "<Activity/>"},
    ]],
    "systemforms": [[
        {"formid": _id(60), "name": "Account", "objecttypecode": "account", "type": 2,
         "ismanaged": False, "solutionid": _id(1), "formxml": "<form/>"},
        {"formid": _id(61), "name": "Project", "objecttypecode": "new_project", "type": 2,
         "ismanaged": False, "solutionid": _id(2), "formxml": "<form/>"},
    ]],
    "savedqueries": [[
        {"savedqueryid": _id(70), "name": "Active Projects", "returnedtypecode": "new_project",
         "querytype": 0, "ismanaged": False, "solutionid": _id(2), "fetchxml": "<fetch/>",
         "layoutxml": "<grid/>"},
    ]],
    "sitemaps": [[
        {"sitemapid": _id(80), "ismanaged": False, "solutionid": _id(1),
         "sitemapxml": "<SiteMap/>"},
    ]],
    "solutioncomponents": [[
        {"solutioncomponentid": _id(100), "componenttype": 61, "objectid": _id(10),
         **_lookup("solutionid", "solution", 2)},
        {"solutioncomponentid": _id(101), "componenttype": 1, "objectid": _id(201),
         **_lookup("solutionid", "solution", 2)},
    ]],
    "dependencies": [[
        {"dependencyid": _id(110), "dependentcomponenttype": 92,
         "dependentcomponentobjectid": _id(40), "requiredcomponenttype": 1,
         "requiredcomponentobjectid": _id(201), "dependencytype": 2},
    ]],
}  # fmt: skip

_ENTITIES = [
    {"LogicalName": "account", "DisplayName": _label("Account"), "IsCustomEntity": False,
     "DisplayCollectionName": _label("Accounts"), "EntitySetName": "accounts",
     "ObjectTypeCode": 1, "MetadataId": _id(200), "IsManaged": True},
    {"LogicalName": "new_project", "DisplayName": _label("Project"), "IsCustomEntity": True,
     "DisplayCollectionName": _label("Projects"), "EntitySetName": "new_projects",
     "ObjectTypeCode": 10001, "MetadataId": _id(201), "IsManaged": False},
    {"LogicalName": "new_project_account", "DisplayName": _label(None), "IsCustomEntity": True,
     "DisplayCollectionName": _label(None), "EntitySetName": None,
     "ObjectTypeCode": 10002, "MetadataId": _id(202), "IsManaged": False},
]  # fmt: skip
_OPTION_SETS = [
    {"Name": "new_tier", "MetadataId": _id(300), "IsGlobal": True, "IsCustomOptionSet": True,
     "OptionSetType": "Picklist",
     "Options": [{"Value": 1, "Label": _label("Gold")}]},
]  # fmt: skip


def _entity_metadata(name: str) -> dict:
    meta = next(e for e in _ENTITIES if e["LogicalName"] == name)
    attrs = [
        {"LogicalName": f"{name}id", "MetadataId": _id(400 + len(name)),
         "AttributeType": "Uniqueidentifier", "IsCustomAttribute": False, "IsManaged": True,
         "RequiredLevel": {"Value": "SystemRequired"}},
        {"LogicalName": "new_active", "MetadataId": _id(450 + len(name)),
         "AttributeType": "Boolean", "IsCustomAttribute": True, "IsManaged": False,
         "RequiredLevel": {"Value": "None"}, "DefaultValue": False,
         "OptionSet": {"Name": f"{name}_new_active", "MetadataId": _id(470 + len(name)),
                       "IsGlobal": False, "IsCustomOptionSet": True, "OptionSetType": "Boolean",
                       "TrueOption": {"Value": 1, "Label": _label("Yes")},
                       "FalseOption": {"Value": 0, "Label": _label("No")}}},
    ]  # fmt: skip
    rel = {
        "SchemaName": "account_new_project", "MetadataId": _id(500),
        "IsCustomRelationship": True, "IsManaged": False, "ReferencedEntity": "account",
        "ReferencedAttribute": "accountid", "ReferencingEntity": "new_project",
        "ReferencingAttribute": "new_accountid",
    }  # fmt: skip
    return {
        "LogicalName": name, "MetadataId": meta["MetadataId"],
        "IsCustomEntity": meta["IsCustomEntity"], "IsManaged": meta["IsManaged"],
        "ObjectTypeCode": meta["ObjectTypeCode"], "PrimaryIdAttribute": f"{name}id",
        "PrimaryNameAttribute": "name", "Attributes": attrs,
        "OneToManyRelationships": [rel] if name == "account" else [],
        "ManyToOneRelationships": [rel] if name == "new_project" else [],
        "ManyToManyRelationships": [],
    }  # fmt: skip


def _ribbon(entity: str | None) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("RibbonXml.xml", f"<RibbonDefinitions entity='{entity}'/>")
    return base64.b64encode(buf.getvalue()).decode()


def _cookie(entity_set: str, page: int) -> str:
    inner = f'<cookie page="{page}"><id last="{{X}}" first="{{Y}}" /></cookie>'
    return (
        f'<cookie pagenumber="{page + 1}" pagingcookie="{quote(quote(inner))}"'
        f' istracking="False" />'
    )


class FakeBackend:
    """Answers the Web API reads from `_ORG`; *fail* raises once at (entity set, page)."""

    def __init__(self, fail: dict | None = None, auth_scheme: str = "oauth", org=None):
        self.profile = ConnectionProfile(
            name="cloud", url=_URL, domain="", username="", auth_scheme=auth_scheme,
            tenant_id="t", client_id="c",
        )  # fmt: skip
        self.password = _SECRET  # what a real backend holds; it must never reach the output
        self.org = org or _ORG
        self.fail = dict(fail or {})
        self.requests: list[tuple[str, int, str | None]] = []
        self.paths: list[str] = []
        self.fetch_xml: dict[str, str] = {}  # entity set -> its last FetchXML

    def as_live(self):
        return self

    def get(self, path: str, *, params: dict | None = None, extra_headers: dict | None = None):
        self.paths.append(path)
        if path == "WhoAmI":
            return {"OrganizationId": _id(999_000)}
        if path == "RetrieveVersion()":
            return {"Version": "9.2.26094.166"}
        if path.startswith("RetrieveEntity("):
            name = (params or {})["@n"].strip("'")
            return {"EntityMetadata": _entity_metadata(name)}
        if path == "RetrieveApplicationRibbon()":
            return {"CompressedApplicationRibbonXml": _ribbon(None)}
        if path.startswith("RetrieveEntityRibbon("):
            return {"CompressedEntityXml": _ribbon((params or {})["@e"].strip("'"))}
        return self._fetch(path, (params or {})["fetchXml"], extra_headers or {})

    def get_collection(self, path: str, *, params: dict | None = None):
        self.paths.append(path)
        exc = self.fail.pop((path, 1), None)
        if exc is not None:
            raise exc
        rows = {"EntityDefinitions": _ENTITIES, "GlobalOptionSetDefinitions": _OPTION_SETS}[path]
        return copy.deepcopy(rows)

    def _fetch(self, entity_set: str, fetch_xml: str, headers: dict) -> dict:
        assert "odata.include-annotations" in headers.get("Prefer", "")
        self.fetch_xml[entity_set] = fetch_xml
        fetch = fromstring(fetch_xml)
        number = int(fetch.get("page", "1"))
        cookie = fetch.get("paging-cookie")
        self.requests.append((entity_set, number, cookie))
        exc = self.fail.pop((entity_set, number), None)
        if exc is not None:
            raise exc
        pages = self.org.get(entity_set, [[]])
        body: dict = {"value": copy.deepcopy(pages[number - 1])}
        if number < len(pages):
            body["@Microsoft.Dynamics.CRM.morerecords"] = True
            body["@Microsoft.Dynamics.CRM.fetchxmlpagingcookie"] = _cookie(entity_set, number)
        return body


@pytest.fixture(autouse=True)
def exports(monkeypatch):
    """Stand in for the solution export; return the names it was asked for."""
    names: list[str] = []

    def export(backend, name, dest, **kwargs):
        names.append(name)
        Path(dest).write_bytes(default_zip())

    monkeypatch.setattr(solution_transfer, "export_solution", export)
    return names


def _extract(backend, out: Path, solutions: tuple[str, ...] = (), include_system: bool = True):
    return inventory_extract.extract(
        WebApiSource(backend), out, cli_version="9.9.9", solutions=solutions,
        include_system=include_system, now=lambda: "2026-11-04T10:00:00Z",
    )  # fmt: skip


def _manifest(out: Path) -> dict:
    data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8"))).validate(data)
    inventory.read_inventory(out)
    return data


def _keys(data: dict, kind: str) -> list[str]:
    return sorted(r["key"] for r in data["records"] if r["kind"] == kind)


def test_a_cloud_extraction_writes_a_schema_valid_manifest(tmp_path):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out)
    assert summary.complete and summary.errors == []
    data = _manifest(out)
    assert data["source"] == {
        "platform": "cloud",
        "org_url": _URL,
        "organization_id": _id(999_000),
        "version": "9.2.26094.166",
    }
    assert data["scope"] == {"mode": "org", "solutions": []}
    # The platform refuses to export the Default solution, so the ribbon diffs are unread.
    assert {k for k, c in data["coverage"].items() if c["status"] != "complete"} == {"ribbon_diff"}
    assert data["coverage"]["ribbon_diff"]["status"] == "unsupported"
    assert any(g["kind"] == "ribbon_diff" for g in data["gaps"])
    assert _keys(data, "step") == [
        "Update|new_project|40|0|Contoso.Plugins%7CContoso.Plugins.ProjectPlugin|Project: Update"
    ]
    assert _keys(data, "step_image") == [
        "Update%7Cnew_project%7C40%7C0%7CContoso.Plugins%257CContoso.Plugins.ProjectPlugin"
        "%7CProject: Update|pre|0"
    ]
    assert _keys(data, "form") == ["account|2|Account", "new_project|2|Project"]
    assert _keys(data, "relationship") == ["account_new_project"]
    assert _keys(data, "local_option_set") == [
        "account|new_active",
        "new_project_account|new_active",
        "new_project|new_active",
    ]
    local = next(
        r
        for r in data["records"]
        if r["kind"] == "local_option_set" and r["key"] == "account|new_active"
    )
    assert local["fields"]["options"] == [
        {"value": 0, "label": "No"},
        {"value": 1, "label": "Yes"},
    ]
    flag = next(r for r in data["records"] if r["key"] == "account|new_active")
    assert flag["kind"] == "attribute"
    assert flag["fields"]["default_value"] is False
    assert "option_set_global" not in flag["fields"]  # a picklist's flag, not a Boolean's
    key_column = next(r for r in data["records"] if r["key"] == "account|accountid")
    assert "default_value" not in key_column["fields"]
    assert _keys(data, "ribbon") == ["", "account", "new_project"]
    solution = next(r for r in data["records"] if r["key"] == "contoso")
    assert solution["fields"]["publisher"] == "Contoso"
    (dependency,) = [r for r in data["records"] if r["kind"] == "dependency"]
    assert dependency["fields"]["dependent_kind"] == "step"
    assert dependency["fields"]["required_key"] == "new_project"
    js = next(r for r in data["records"] if r["key"] == "new_/scripts/project.js")
    assert js["id"] == _id(10) and js["origin"] == "custom"
    assert (out / js["artifacts"][0]["path"]).read_bytes() == _JS
    (dll,) = next(r for r in data["records"] if r["key"] == "Contoso.Plugins")["artifacts"]
    assert dll["content"] == "binary" and (out / dll["path"]).read_bytes() == _DLL


def test_a_multi_page_read_follows_the_paging_cookie_to_the_end(tmp_path):
    backend = FakeBackend()
    out = tmp_path / "inv"
    _extract(backend, out)
    pages = [(n, c) for s, n, c in backend.requests if s == "webresourceset"]
    assert pages == [(1, None), (2, '<cookie page="1"><id last="{X}" first="{Y}" /></cookie>')]
    coverage = _manifest(out)["coverage"]["web_resource"]
    assert coverage == {
        "status": "complete", "pages": 2, "records": 2, "truncated": False, "errors": [],
    }  # fmt: skip


def test_a_read_stopped_early_is_partial_and_truncated(tmp_path):
    class NoCookie(FakeBackend):
        def _fetch(self, entity_set, fetch_xml, headers):
            body = super()._fetch(entity_set, fetch_xml, headers)
            body.pop("@Microsoft.Dynamics.CRM.fetchxmlpagingcookie", None)
            return body

    out = tmp_path / "inv"
    summary = _extract(NoCookie(), out)
    assert not summary.complete
    coverage = _manifest(out)["coverage"]["web_resource"]
    assert coverage["status"] == "partial" and coverage["truncated"] is True
    assert coverage["records"] == 1
    assert "carries no paging cookie" in coverage["errors"][0]


def test_a_mid_run_fault_is_recorded_and_the_run_continues(tmp_path):
    out = tmp_path / "inv"
    fault = D365Error("plug-in failed", status=500)
    summary = _extract(FakeBackend(fail={("systemforms", 1): fault}), out)
    assert summary.errors == ["systemform page 1: plug-in failed"]
    data = _manifest(out)
    assert data["coverage"]["form"]["status"] == "failed"
    assert data["coverage"]["view"]["status"] == "complete"  # later reads still ran
    assert (out / ".checkpoint").is_dir()


def test_a_connection_wide_failure_stops_the_run(tmp_path):
    reset = D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: connection reset")
    with pytest.raises(D365Error, match="connection reset"):
        _extract(FakeBackend(fail={("webresourceset", 2): reset}), tmp_path / "inv")


def test_disk_and_gac_assemblies_are_extraction_gaps(tmp_path):
    out = tmp_path / "inv"
    _extract(FakeBackend(), out)
    data = _manifest(out)
    for key, where in (("Contoso.InGac", "in the GAC"), ("Contoso.OnDisk", "on disk")):
        (gap,) = [g for g in data["gaps"] if g["key"] == key]
        assert gap["kind"] == "plugin_assembly" and where in gap["reason"]
    descriptions = [g["description"] or "" for g in data["gaps"]]
    # SSRS reports on cloud are FetchXML-based: no SQL data source gap.
    assert not any(d.startswith("SQL data sources") for d in descriptions)


def test_an_onprem_v9_profile_is_platform_onprem_with_the_ssrs_gap(tmp_path):
    out = tmp_path / "inv"
    _extract(FakeBackend(auth_scheme="ntlm"), out)
    data = _manifest(out)
    assert data["source"]["platform"] == "onprem"
    assert any((g["description"] or "").startswith("SQL data sources") for g in data["gaps"])


def test_no_credential_reaches_any_output_file(tmp_path):
    out = tmp_path / "inv"
    # A failed kind keeps the checkpoint, so its files are checked too.
    _extract(FakeBackend(fail={("systemforms", 1): D365Error("boom", status=500)}), out)
    files = [p for p in out.rglob("*") if p.is_file()]
    assert any(".checkpoint" in p.parts for p in files)
    for path in files:
        assert _SECRET.encode() not in path.read_bytes(), path


def test_solution_scope_keeps_the_named_solutions_and_their_members(tmp_path):
    backend = FakeBackend()
    out = tmp_path / "inv"
    summary = _extract(backend, out, solutions=("contoso",))
    assert summary.complete
    data = _manifest(out)
    assert data["scope"] == {"mode": "solutions", "solutions": ["contoso"]}
    assert _keys(data, "solution") == ["contoso"]
    assert _keys(data, "entity") == ["new_project"]
    assert _keys(data, "web_resource") == ["new_/scripts/project.js"]
    assert _keys(data, "form") == ["new_project|2|Project"]  # a member entity's subcomponent
    assert _keys(data, "view") == ["new_project|Active Projects"]
    assert _keys(data, "ribbon") == ["new_project"]
    assert _keys(data, "relationship") == ["account_new_project"]  # new_project is one side
    assert _keys(data, "plugin_assembly") == []
    # Entity details and ribbons are read for member entities only.
    assert sum(p.startswith("RetrieveEntity(") for p in backend.paths) == 1
    assert sum(p.startswith("RetrieveEntityRibbon(") for p in backend.paths) == 1
    assert "RetrieveApplicationRibbon()" not in backend.paths


def test_an_unknown_solution_is_an_error(tmp_path):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out, solutions=("nope",))
    assert summary.errors == ["solution nope: no solution with this unique name"]
    assert _manifest(out)["coverage"]["solution"]["status"] == "failed"  # no record kept


def test_a_resume_with_another_scope_is_refused(tmp_path):
    out = tmp_path / "inv"
    _extract(FakeBackend(fail={("systemforms", 1): D365Error("boom", status=500)}), out)
    with pytest.raises(D365Error, match="another scope"):
        _extract(FakeBackend(), out, solutions=("contoso",))


_GONE_MESSAGES = [
    ("Could not find an entity with name 'new_project'.", 404),
    (
        "The entity with a name = 'new_project' with namemapping = 'Logical' was not found"
        " in the MetadataCache.",
        500,
    ),
]


class GoneBackend(FakeBackend):
    """Faults each read of `gone`'s metadata (or ribbon) as a table deleted since the listing."""

    def __init__(self, fault: D365Error, read: str = "RetrieveEntity(", gone="new_project"):
        super().__init__()
        self.fault, self.read, self.gone = fault, read, gone
        self.faulted = 0

    def get(self, path: str, *, params: dict | None = None, extra_headers: dict | None = None):
        named = {str(v).strip("'") for v in (params or {}).values()}
        if path.startswith(self.read) and self.gone in named:
            self.faulted += 1
            raise self.fault
        return super().get(path, params=params, extra_headers=extra_headers)


@pytest.mark.parametrize("read", ["RetrieveEntity(", "RetrieveEntityRibbon("])
@pytest.mark.parametrize(("message", "status"), _GONE_MESSAGES)
def test_a_table_deleted_since_the_listing_is_a_gap_not_an_error(tmp_path, message, status, read):
    out = tmp_path / "inv"
    summary = _extract(GoneBackend(D365Error(message, status=status), read), out)
    assert summary.complete and summary.errors == []
    assert not (out / ".checkpoint").exists()
    data = _manifest(out)
    assert {k for k, c in data["coverage"].items() if c["status"] != "complete"} == {"ribbon_diff"}
    gone = {g["kind"]: g for g in data["gaps"] if "no longer exists" in (g["description"] or "")}
    assert set(gone) == {"entity", "attribute", "relationship", "local_option_set", "ribbon"}
    assert (
        gone["entity"]["description"] == "new_project: no longer exists, deleted since the listing"
    )


def test_a_table_deleted_since_the_listing_leaves_no_record_and_no_dangling_edge(tmp_path):
    out = tmp_path / "inv"
    fault = D365Error(_GONE_MESSAGES[0][0], status=404)
    summary = _extract(GoneBackend(fault), out)
    assert summary.complete
    data = _manifest(out)
    assert _keys(data, "entity") == ["account", "new_project_account"]
    assert _keys(data, "relationship") == []  # its one side is the deleted table
    assert _keys(data, "ribbon") == ["", "account"]
    # The rows it owns, whichever read listed them, are left out and counted.
    assert not any(r["fields"].get("entity") == "new_project" for r in data["records"])
    assert _keys(data, "form") == ["account|2|Account"]
    assert _keys(data, "view") == []
    assert any(
        (g["description"] or "") == "1 form row(s) of a table deleted since the listing"
        for g in data["gaps"]
    )
    # The membership naming the table is counted by the existing unresolved accounting.
    assert any(
        (g["description"] or "").startswith("1 solution_membership row(s)")
        and "type 1 " in g["description"]
        for g in data["gaps"]
    )
    assert not any(r["kind"] == "solution_membership" and "new_project" in r["key"]
                   for r in data["records"])  # fmt: skip


def test_a_resume_reuses_the_outcome_recorded_for_a_deleted_table(tmp_path):
    out = tmp_path / "inv"
    fault = D365Error(_GONE_MESSAGES[0][0], status=404)
    first = GoneBackend(fault)
    first.fail = {("systemforms", 1): D365Error("boom", status=500)}  # keeps the checkpoint
    assert not _extract(first, out).complete
    assert first.faulted == 1

    second = GoneBackend(fault)
    summary = _extract(second, out)
    assert summary.resumed and summary.complete and summary.errors == []
    assert second.faulted == 0  # the recorded outcome is reused, no read for the gone table
    assert _keys(_manifest(out), "entity") == ["account", "new_project_account"]


@pytest.mark.parametrize(
    "fault",
    [
        D365Error("Principal user is missing prvReadEntity privilege", status=403),
        D365Error("An unexpected error occurred", status=500),
        D365Error("Resource not found for the segment 'RetrieveEntity'", status=404),
        D365Error("Could not find an entity with name 'account'.", status=404),
        D365Error("Could not find an entity with name 'new_project_account'.", status=404),
        D365Error(
            "The entity with a name = 'account' with namemapping = 'Logical' was not found"
            " in the MetadataCache.",
            status=500,
        ),
        D365Error("The entity with a name = 'new_project' was not found", status=500),
    ],
    ids=[
        "permission",
        "server-fault",
        "bare-404",
        "another-entity",
        "near-miss-name",
        "cache-another-entity",
        "cache-no-tail",
    ],  # fmt: skip
)
def test_any_other_fault_on_a_table_read_is_still_an_error(tmp_path, fault):
    out = tmp_path / "inv"
    summary = _extract(GoneBackend(fault), out)
    assert not summary.complete
    assert {e.split(":")[0] for e in summary.errors} == {"entity/new_project"}  # one per kind
    data = _manifest(out)
    assert data["coverage"]["attribute"]["status"] == "partial"
    assert not any("no longer exists" in (g["description"] or "") for g in data["gaps"])
    assert (out / ".checkpoint").is_dir()


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    session_mod.save_profile(ConnectionProfile(name="v9", url=_URL, domain="D", username="u"))
    return tmp_path


def test_inventory_extract_runs_the_v9_path_on_a_v9_profile(crm_home, monkeypatch, tmp_path):
    fake = FakeBackend()
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: fake)
    out = tmp_path / "inv"
    argv = ["--json", "--profile", "v9", "inventory", "extract", "--out", str(out)]
    result = CliRunner().invoke(cli, [*argv, "--solution", "contoso"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["complete"] is True
    assert data["kinds"]["solution"] == {"status": "complete", "records": 1}
    assert _manifest(out)["scope"]["solutions"] == ["contoso"]


def test_inventory_extract_on_a_v9_profile_fails_on_a_failed_kind(crm_home, monkeypatch, tmp_path):
    fake = FakeBackend(fail={("systemforms", 1): D365Error("plug-in failed", status=500)})
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: fake)
    argv = ["--json", "--profile", "v9", "inventory", "extract", "--out", str(tmp_path / "i")]
    result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 1
    env = json.loads(result.stdout)
    assert env["ok"] is False and "systemform page 1: plug-in failed" in env["error"]


def test_a_row_with_an_unkeyable_field_is_a_gap_and_the_run_goes_on(tmp_path):
    # Measured on a Dataverse trial org: a system step whose name is empty.
    step = {**_ORG["sdkmessageprocessingsteps"][0][0], "sdkmessageprocessingstepid": _id(42)}
    org = {**_ORG, "sdkmessageprocessingsteps": [[*_ORG["sdkmessageprocessingsteps"][0],
                                                  {**step, "name": ""}]]}  # fmt: skip
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(org=org), out)
    assert summary.complete
    data = _manifest(out)
    assert _keys(data, "step") == [
        "Update|new_project|40|0|Contoso.Plugins%7CContoso.Plugins.ProjectPlugin|Project: Update"
    ]
    (gap,) = [g for g in data["gaps"] if g["kind"] == "step" and g["key"] is None]
    assert gap["description"].startswith("1 step row(s)")
    assert "step.name must be a non-empty string" in gap["description"]


def test_an_entity_named_application_keeps_its_own_ribbon_file(tmp_path, monkeypatch):
    # Measured on a Dataverse trial org: an `application` entity, whose ribbon is
    # keyed `application`; the application ribbon is keyed "" (#1070), so no key repeats.
    app = {"LogicalName": "application", "DisplayName": _label("Application"),
           "DisplayCollectionName": _label("Applications"),
           "IsCustomEntity": False, "ObjectTypeCode": 1204, "MetadataId": _id(203),
           "IsManaged": True}  # fmt: skip
    monkeypatch.setattr(f"{__name__}._ENTITIES", [*_ENTITIES, app])
    out = tmp_path / "inv"
    _extract(FakeBackend(), out)
    ribbons = [r for r in _manifest(out)["records"] if r["kind"] == "ribbon"]
    assert sorted(r["key"] for r in ribbons if r["key"] in ("", "application")) == [
        "",
        "application",
    ]
    paths = [a["path"] for r in ribbons if r["key"] in ("", "application") for a in r["artifacts"]]
    assert len(paths) == 2
    assert len(set(paths)) == 2
    assert not inventory.read_inventory(out).ambiguous()


def test_a_membership_and_a_dependency_of_the_default_sitemap_keep_its_empty_key(
    tmp_path, monkeypatch
):
    member = {"solutioncomponentid": _id(102), "componenttype": 62, "objectid": _id(80),
              **_lookup("solutionid", "solution", 2)}  # fmt: skip
    dep = {"dependencyid": _id(111), "dependentcomponenttype": 62,
           "dependentcomponentobjectid": _id(80), "requiredcomponenttype": 1,
           "requiredcomponentobjectid": _id(201), "dependencytype": 2}  # fmt: skip
    monkeypatch.setitem(_ORG, "solutioncomponents", [[*_ORG["solutioncomponents"][0], member]])
    monkeypatch.setitem(_ORG, "dependencies", [[*_ORG["dependencies"][0], dep]])
    out = tmp_path / "inv"
    _extract(FakeBackend(), out)
    data = _manifest(out)
    refs = [r["fields"] for r in data["records"] if r["kind"] == "solution_membership"]
    assert {"solution": "contoso", "member_kind": "sitemap", "member_key": ""} in refs
    deps = [r["fields"] for r in data["records"] if r["kind"] == "dependency"]
    assert any(d["dependent_kind"] == "sitemap" and d["dependent_key"] == "" for d in deps)


def test_a_scoped_dependency_on_a_component_outside_the_scope_is_a_scope_gap(tmp_path, monkeypatch):
    # The member entity's form requires an attribute of account, which is outside
    # the solution; a scoped run reads no account details, so it cannot record it.
    outside = {"dependencyid": _id(111), "dependentcomponenttype": 60,
               "dependentcomponentobjectid": _id(61), "requiredcomponenttype": 2,
               "requiredcomponentobjectid": _id(407), "dependencytype": 2}  # fmt: skip
    # The System solution's membership of an account attribute is outside the scope too.
    other = {"solutioncomponentid": _id(102), "componenttype": 2, "objectid": _id(407),
             **_lookup("solutionid", "solution", 1)}  # fmt: skip
    monkeypatch.setitem(_ORG, "dependencies", [[*_ORG["dependencies"][0], outside]])
    monkeypatch.setitem(_ORG, "solutioncomponents", [[*_ORG["solutioncomponents"][0], other]])
    out = tmp_path / "inv"
    _extract(FakeBackend(), out, solutions=("contoso",))
    gaps = [g for g in _manifest(out)["gaps"] if g["kind"] in ("dependency", "solution_membership")]
    assert len(gaps) == 1  # the other solution's membership is not counted
    assert gaps[0]["kind"] == "dependency"
    assert gaps[0]["description"].startswith("1 dependency row(s)")
    assert "outside the named solutions" in gaps[0]["reason"]


def test_a_repeated_solution_is_recorded_once_and_resumes(tmp_path):
    out = tmp_path / "inv"
    _extract(FakeBackend(fail={("systemforms", 1): D365Error("boom", status=500)}), out,
             solutions=("contoso", "contoso"))  # fmt: skip
    assert _manifest(out)["scope"]["solutions"] == ["contoso"]
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert summary.resumed


def test_each_app_sitemap_keeps_its_unique_name(tmp_path, monkeypatch):
    # A v9 org holds one sitemap per model-driven app; only the default has no unique name.
    app = {"sitemapid": _id(81), "sitemapnameunique": "new_projectapp", "ismanaged": False,
           "solutionid": _id(2), "sitemapxml": "<SiteMap/>"}  # fmt: skip
    monkeypatch.setitem(_ORG, "sitemaps", [[*_ORG["sitemaps"][0], app]])
    out = tmp_path / "inv"
    backend = FakeBackend()
    _extract(backend, out)
    assert 'name="sitemapnameunique"' in backend.fetch_xml["sitemaps"]
    assert _keys(_manifest(out), "sitemap") == ["", "new_projectapp"]


def test_an_entity_held_without_its_subcomponents_keeps_only_the_listed_ones(tmp_path, monkeypatch):
    # new_project is a member at rootcomponentbehavior 1 (subcomponents not included),
    # with one attribute listed on its own: only that attribute comes along.
    shell = {**_ORG["solutioncomponents"][0][1], "rootcomponentbehavior": 1}
    attr = {"solutioncomponentid": _id(103), "componenttype": 2,
            "objectid": _id(400 + len("new_project")),
            **_lookup("solutionid", "solution", 2)}  # fmt: skip
    rows = [_ORG["solutioncomponents"][0][0], shell, attr]
    monkeypatch.setitem(_ORG, "solutioncomponents", [rows])
    backend = FakeBackend()
    out = tmp_path / "inv"
    _extract(backend, out, solutions=("contoso",))
    data = _manifest(out)
    assert 'name="rootcomponentbehavior"' in backend.fetch_xml["solutioncomponents"]
    assert _keys(data, "entity") == ["new_project"]
    assert _keys(data, "attribute") == ["new_project|new_projectid"]
    assert _keys(data, "form") == _keys(data, "view") == _keys(data, "ribbon") == []
    assert _keys(data, "relationship") == []


def test_a_solution_name_matches_case_insensitively(tmp_path):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out, solutions=("CONTOSO",))
    assert summary.errors == []
    data = _manifest(out)
    assert data["scope"]["solutions"] == ["contoso"]  # as the org spells it
    assert _keys(data, "entity") == ["new_project"]


def test_a_failed_entity_list_leaves_the_entity_detail_kinds_incomplete(tmp_path):
    out = tmp_path / "inv"
    _extract(FakeBackend(fail={("EntityDefinitions", 1): D365Error("boom", status=500)}), out)
    coverage = _manifest(out)["coverage"]
    for kind in ("entity", "attribute", "relationship", "local_option_set", "ribbon"):
        assert coverage[kind]["status"] != "complete", kind


@pytest.mark.parametrize("entity_set", ["solutions", "solutioncomponents"])
def test_a_failed_membership_read_leaves_every_scoped_kind_incomplete(tmp_path, entity_set):
    out = tmp_path / "inv"
    fault = D365Error("boom", status=500)
    _extract(FakeBackend(fail={(entity_set, 1): fault}), out, solutions=("contoso",))
    coverage = _manifest(out)["coverage"]
    kinds = ("entity", "attribute", "global_option_set", "web_resource", "ribbon")
    for kind in (*kinds, "solution_membership"):
        assert coverage[kind]["status"] != "complete", kind
    if entity_set == "solutioncomponents":
        assert coverage["solution"]["status"] == "complete"


def test_an_image_of_an_unknown_step_is_counted_as_a_gap(tmp_path, monkeypatch):
    orphan = {**_ORG["sdkmessageprocessingstepimages"][0][0],
              "sdkmessageprocessingstepimageid": _id(42),
              **_lookup("sdkmessageprocessingstepid", "sdkmessageprocessingstep", 49)}  # fmt: skip
    images = [[*_ORG["sdkmessageprocessingstepimages"][0], orphan]]
    monkeypatch.setitem(_ORG, "sdkmessageprocessingstepimages", images)
    out = tmp_path / "inv"
    _extract(FakeBackend(), out)
    gaps = [g for g in _manifest(out)["gaps"] if g["kind"] == "step_image"]
    assert [g["reason"] for g in gaps] == [inventory_extract._UNRESOLVED_REASON]


def test_an_entity_with_no_collection_label_has_its_ribbon_recorded_as_a_gap(tmp_path, monkeypatch):
    # The Web API faults RetrieveEntityRibbon for an entity labelled in the user's
    # language but with no collection label there (seen live on a cloud org).
    odd = {"LogicalName": "new_syncmap", "DisplayName": _label("Sync Map"),
           "DisplayCollectionName": _label(None), "IsCustomEntity": True,
           "ObjectTypeCode": 10003, "MetadataId": _id(203), "IsManaged": False}  # fmt: skip
    monkeypatch.setattr(f"{__name__}._ENTITIES", [*_ENTITIES, odd])
    backend = FakeBackend()
    out = tmp_path / "inv"
    summary = _extract(backend, out)
    assert summary.complete
    # account and new_project only: no read for new_syncmap.
    assert sum(p.startswith("RetrieveEntityRibbon(") for p in backend.paths) == 2
    gaps = [g for g in _manifest(out)["gaps"] if g["kind"] == "ribbon"]
    assert len(gaps) == 1
    assert "new_syncmap" in gaps[0]["description"]


_PAYLOAD_COLUMNS = {"content", "xaml", "formxml", "fetchxml", "layoutxml", "sitemapxml"}


class PayloadBackend(FakeBackend):
    """A FakeBackend that answers an id-`in` filter and drops payload columns not asked for."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # (entity set, requested columns, ids of the `in` filter or None), one per request
        self.fetched: list[tuple[str, set[str], list[str] | None]] = []
        self.fail_content: set[str] = set()  # entity sets whose id-`in` fetch raises

    def _fetch(self, entity_set: str, fetch_xml: str, headers: dict) -> dict:
        fetch = fromstring(fetch_xml)
        entity = fetch.find("entity")
        assert entity is not None
        columns = {a.get("name") or "" for a in entity.findall("attribute")}
        cond = entity.find("filter/condition[@operator='in']")
        ids = [v.text or "" for v in cond.findall("value")] if cond is not None else None
        self.fetched.append((entity_set, columns, ids))
        if cond is not None and entity_set in self.fail_content:
            raise D365Error("content boom", status=500)
        body = super()._fetch(entity_set, fetch_xml, headers)
        rows = body["value"]
        if cond is not None:
            rows = [r for r in rows if r[cond.get("attribute")] in ids]
        drop = _PAYLOAD_COLUMNS - columns
        body["value"] = [{k: v for k, v in r.items() if k not in drop} for r in rows]
        return body

    def payload_fetches(self, entity_set: str) -> list[list[str] | None]:
        return [ids for s, cols, ids in self.fetched if s == entity_set and "content" in cols]


def _fault_in_forms() -> dict:
    return {("systemforms", 1): D365Error("boom", status=500)}


def _tree(out: Path) -> dict[str, bytes]:
    return {
        p.relative_to(out).as_posix(): p.read_bytes()
        for p in sorted(out.rglob("*"))
        if p.is_file() and ".checkpoint" not in p.parts
    }


def test_a_saved_page_holds_no_payload_body(tmp_path):
    out = tmp_path / "inv"
    _extract(PayloadBackend(fail=_fault_in_forms()), out)
    for kind in ("web_resource", "plugin_assembly", "process", "view"):
        pages = sorted((out / ".checkpoint" / "rm" / kind).glob("*.json"))
        assert pages, kind
        for page in pages:
            for row in json.loads(page.read_text(encoding="utf-8"))["rows"]:
                assert not _PAYLOAD_COLUMNS & row.keys(), (kind, row)
    saved = json.loads((out / ".checkpoint/rm/web_resource/00001.json").read_text("utf-8"))
    assert saved["rows"][0]["_artifacts"] == ["content.js"]


def test_an_artifact_shares_its_inode_with_the_staged_payload(tmp_path):
    out = tmp_path / "inv"
    _extract(PayloadBackend(fail=_fault_in_forms()), out)  # the fault keeps the checkpoint
    artifact = next(out.glob("artifacts/web_resource/**/*.js"))
    staged = out / ".checkpoint/payload/web_resource" / f"{_id(10)}.content.js"
    assert artifact.read_bytes() == _JS
    assert artifact.stat().st_ino == staged.stat().st_ino


def test_a_copy_replaces_the_link_where_links_fail(tmp_path, monkeypatch):
    linked = tmp_path / "linked"
    _extract(PayloadBackend(), linked)

    def no_link(*args, **kwargs):
        raise OSError("cross-device link")

    monkeypatch.setattr(inventory_extract.os, "link", no_link)
    copied = tmp_path / "copied"
    _extract(PayloadBackend(fail=_fault_in_forms()), copied)
    artifact = next(copied.glob("artifacts/web_resource/**/*.js"))
    staged = copied / ".checkpoint/payload/web_resource" / f"{_id(10)}.content.js"
    assert artifact.stat().st_ino != staged.stat().st_ino
    assert artifact.read_bytes() == _JS
    # Every artifact the faulted run wrote matches the linked run's bytes.
    written = {k: v for k, v in _tree(copied).items() if k.startswith("artifacts/")}
    assert written and all(_tree(linked)[k] == v for k, v in written.items())


def test_a_scoped_run_fetches_content_for_member_rows_only(tmp_path):
    backend = PayloadBackend()
    out = tmp_path / "inv"
    assert _extract(backend, out, solutions=("contoso",)).complete
    fetches = backend.payload_fetches("webresourceset")
    assert fetches and all(ids == [_id(10)] for ids in fetches)  # one id, whatever the pages
    assert backend.payload_fetches("pluginassemblies") == []  # no member assembly
    # The org-wide read still names every row, so keys and gaps resolve as before.
    metadata = [c for s, c, ids in backend.fetched if s == "webresourceset" and ids is None]
    assert metadata and all("content" not in c for c in metadata)
    data = _manifest(out)
    assert _keys(data, "web_resource") == ["new_/scripts/project.js"]
    assert (next(out.glob("artifacts/web_resource/**/*.js"))).read_bytes() == _JS


def test_an_unscoped_run_still_reads_content_with_each_row(tmp_path):
    backend = PayloadBackend()
    assert _extract(backend, tmp_path / "inv").complete
    assert [ids for ids in backend.payload_fetches("webresourceset")] == [None, None]


def test_a_resume_of_a_scoped_run_reuses_the_content_pass(tmp_path):
    out = tmp_path / "inv"
    first = PayloadBackend(fail=_fault_in_forms())
    assert not _extract(first, out, solutions=("contoso",)).complete
    assert first.payload_fetches("webresourceset")
    second = PayloadBackend()
    summary = _extract(second, out, solutions=("contoso",))
    assert summary.complete and summary.resumed
    assert second.payload_fetches("webresourceset") == []
    assert _keys(_manifest(out), "web_resource") == ["new_/scripts/project.js"]


def test_a_content_batch_saved_for_other_ids_is_fetched_again(tmp_path):
    out = tmp_path / "inv"
    first = PayloadBackend(fail=_fault_in_forms())
    assert not _extract(first, out, solutions=("contoso",)).complete
    unit = next(out.glob(".checkpoint/content/web_resource/*.json"))
    saved = json.loads(unit.read_text())
    saved["ids"] = [*saved["ids"], _id(999)]  # the batch was cut for other members
    unit.write_text(json.dumps(saved))
    second = PayloadBackend()
    assert _extract(second, out, solutions=("contoso",)).complete
    assert second.payload_fetches("webresourceset")


def test_a_scoped_run_fetches_a_member_assemblys_binary_only(tmp_path):
    org = copy.deepcopy(_ORG)
    org["solutioncomponents"][0].append(
        {"solutioncomponentid": _id(102), "componenttype": 91, "objectid": _id(20),
         **_lookup("solutionid", "solution", 2)}
    )  # fmt: skip
    backend = PayloadBackend(org=org)
    out = tmp_path / "inv"
    assert _extract(backend, out, solutions=("contoso",)).complete
    fetches = backend.payload_fetches("pluginassemblies")
    assert fetches and all(ids == [_id(20)] for ids in fetches)  # not the GAC or on-disk rows
    assert next(out.glob("artifacts/plugin_assembly/**/*.dll")).read_bytes() == _DLL
    assert _keys(_manifest(out), "plugin_assembly") == ["Contoso.Plugins"]


def test_a_failed_content_batch_is_a_partial_kind_and_the_run_goes_on(tmp_path):
    backend = PayloadBackend()
    backend.fail_content = {"webresourceset"}
    out = tmp_path / "inv"
    summary = _extract(backend, out, solutions=("contoso",))
    assert not summary.complete
    assert any("webresource content batch 1: content boom" in e for e in summary.errors)
    data = _manifest(out)
    assert data["coverage"]["web_resource"]["status"] == "partial"
    assert _keys(data, "web_resource") == ["new_/scripts/project.js"]  # the record, no artifact


def _diff_records(data: dict) -> dict[str, dict]:
    return {r["key"]: r for r in data["records"] if r["kind"] == "ribbon_diff"}


def test_a_cloud_org_wide_run_exports_nothing_and_says_so(tmp_path, exports):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out)
    assert exports == [] and summary.complete
    assert summary.kinds["ribbon_diff"] == {"status": "unsupported", "records": 0}
    (gap,) = [g for g in _manifest(out)["gaps"] if g["kind"] == "ribbon_diff"]
    assert "Default" in gap["reason"] and "--solution" in gap["reason"]


def test_a_scoped_run_exports_each_named_solution_and_keeps_the_diffs_with_content(
    tmp_path, exports
):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert exports == ["contoso"] and summary.complete
    data = _manifest(out)
    assert data["coverage"]["ribbon_diff"]["status"] == "complete"
    records = _diff_records(data)
    assert sorted(records) == ["", "new_project"]  # the application's key is empty
    (artifact,) = records["new_project"]["artifacts"]
    body = (out / artifact["path"]).read_bytes()
    assert body.startswith(b"<RibbonDiffXml>") and b"new.Project.Do" in body
    assert not list(out.rglob("*.zip")) and not (out / ".checkpoint").exists()


def test_a_failed_export_is_a_failed_kind_never_complete(tmp_path, monkeypatch):
    def refuse(backend, name, dest, **kwargs):
        raise D365Error("export failed", status=500)

    monkeypatch.setattr(solution_transfer, "export_solution", refuse)
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert not summary.complete
    assert summary.kinds["ribbon_diff"] == {"status": "failed", "records": 0}
    assert _manifest(out)["coverage"]["ribbon_diff"]["status"] == "failed"


def test_an_unreadable_export_is_a_failed_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(
        solution_transfer, "export_solution", lambda b, n, dest, **kw: Path(dest).write_bytes(b"x")
    )
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert summary.kinds["ribbon_diff"]["status"] == "failed" and not summary.complete


def test_a_damaged_export_stream_is_a_failed_kind(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "customizations.xml", "<ImportExportXml>" + "<a>b</a>" * 4000 + "</ImportExportXml>"
        )
    damaged = bytearray(buf.getvalue())
    damaged[60:80] = bytes(range(200, 220))  # inside the deflate stream
    monkeypatch.setattr(
        solution_transfer,
        "export_solution",
        lambda b, n, dest, **kw: Path(dest).write_bytes(damaged),
    )
    summary = _extract(FakeBackend(), tmp_path / "inv", solutions=("contoso",))
    assert summary.kinds["ribbon_diff"]["status"] == "failed" and not summary.complete


def test_a_resume_reuses_the_exported_diffs(tmp_path, exports, monkeypatch):
    out = tmp_path / "inv"
    _extract(
        FakeBackend(fail={("systemforms", 1): D365Error("boom", status=500)}), out, ("contoso",)
    )
    assert exports == ["contoso"]
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert exports == ["contoso"]  # the second run read no export
    assert summary.reused["ribbon_diff"] == 1 and summary.complete
    assert sorted(_diff_records(_manifest(out))) == ["", "new_project"]


def test_an_onprem_org_wide_run_exports_the_default_solution(tmp_path, exports):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(auth_scheme="ntlm"), out)
    assert exports == ["Default"] and summary.complete
    assert sorted(_diff_records(_manifest(out))) == ["", "new_project"]


_MANAGED_FAULT = (
    "ExportManagedSolutionError: Managed solutions cannot be exported."
    "<OrganizationServiceFault xmlns='x'><Message>Managed solutions cannot be exported."
    "</Message>" + "<Detail>" + "frame " * 300 + "</Detail></OrganizationServiceFault>"
)


def _org_with_managed_solution() -> dict:
    org = copy.deepcopy(_ORG)
    org["solutions"][0].append(
        {"solutionid": _id(3), "uniquename": "fabrikam", "friendlyname": "Fabrikam",
         "version": "2.0", "ismanaged": True, "isvisible": True,
         **_lookup("publisherid", "publisher", 92, "Fabrikam")}
    )  # fmt: skip
    return org


def test_a_managed_solution_is_not_exported_and_its_ribbon_diff_is_unsupported(tmp_path, exports):
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(org=_org_with_managed_solution()), out, solutions=("fabrikam",))
    assert exports == [] and summary.complete and summary.errors == []
    assert summary.kinds["ribbon_diff"] == {"status": "unsupported", "records": 0}
    data = _manifest(out)
    assert data["coverage"]["ribbon_diff"]["status"] == "unsupported"
    (gap,) = [g for g in data["gaps"] if g["kind"] == "ribbon_diff"]
    assert "fabrikam" in gap["description"]
    assert "managed" in gap["reason"] and "\n" not in gap["reason"] and "<" not in gap["reason"]
    assert "ExportManagedSolutionError" not in (out / "manifest.json").read_text(encoding="utf-8")


def test_a_mixed_scope_reads_the_unmanaged_diffs_and_gaps_the_managed_solution(tmp_path, exports):
    out = tmp_path / "inv"
    summary = _extract(
        FakeBackend(org=_org_with_managed_solution()), out, solutions=("contoso", "fabrikam")
    )
    assert exports == ["contoso"] and summary.complete
    data = _manifest(out)
    assert data["coverage"]["ribbon_diff"]["status"] == "complete"
    assert sorted(_diff_records(data)) == ["", "new_project"]
    (gap,) = [g for g in data["gaps"] if g["kind"] == "ribbon_diff"]
    assert "fabrikam" in gap["description"]


@pytest.mark.parametrize(
    "fault",
    [_MANAGED_FAULT, "ExportManagedSolutionError", "Managed solutions cannot be exported."],
)
def test_a_managed_export_fault_on_an_unflagged_solution_is_a_one_line_gap(
    tmp_path, monkeypatch, fault
):
    def refuse(backend, name, dest, **kwargs):
        raise D365Error(fault, status=400)

    monkeypatch.setattr(solution_transfer, "export_solution", refuse)
    out = tmp_path / "inv"
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert summary.complete and summary.errors == []
    assert summary.kinds["ribbon_diff"] == {"status": "unsupported", "records": 0}
    data = _manifest(out)
    (gap,) = [g for g in data["gaps"] if g["kind"] == "ribbon_diff"]
    assert "\n" not in gap["reason"] and "<" not in gap["reason"] and len(gap["reason"]) < 200
    assert "Fault" not in (out / "manifest.json").read_text(encoding="utf-8")


def _org_with_plugin_types() -> dict:
    org = copy.deepcopy(_ORG)
    org["plugintypes"][0] += [
        {
            "plugintypeid": _id(31),
            "typename": "Contoso.Plugins.Other",
            "friendlyname": "Other",
            "isworkflowactivity": True,
            "ismanaged": False,
            "solutionid": _id(2),
            **_lookup("pluginassemblyid", "pluginassembly", 20, "Contoso.Plugins"),
        },
        {
            "plugintypeid": _id(32),
            "typename": "Contoso.InGac.Hook",
            "friendlyname": "Hook",
            "isworkflowactivity": False,
            "ismanaged": False,
            "solutionid": _id(2),
            **_lookup("pluginassemblyid", "pluginassembly", 21, "Contoso.InGac"),
        },
    ]
    org["solutioncomponents"][0] += [
        {
            "solutioncomponentid": _id(102),
            "componenttype": 91,
            "objectid": _id(20),
            **_lookup("solutionid", "solution", 2),
        },
        {
            "solutioncomponentid": _id(103),
            "componenttype": 92,
            "objectid": _id(40),
            **_lookup("solutionid", "solution", 2),
        },
    ]
    return org


def test_a_scoped_run_keeps_the_types_of_the_assemblies_it_keeps(tmp_path):
    out = tmp_path / "inv"
    assert _extract(FakeBackend(org=_org_with_plugin_types()), out, solutions=("contoso",)).complete
    data = _manifest(out)
    assert _keys(data, "plugin_assembly") == ["Contoso.Plugins"]
    assert _keys(data, "plugin_type") == [
        "Contoso.Plugins|Contoso.Plugins.Other",
        "Contoso.Plugins|Contoso.Plugins.ProjectPlugin",
    ]  # the type of the assembly outside the scope is left out
    (step,) = [r for r in data["records"] if r["kind"] == "step"]
    assert step["fields"]["plugin_type"] == "Contoso.Plugins|Contoso.Plugins.ProjectPlugin"


def test_a_resume_keeps_the_gap_of_a_solution_the_server_refused_to_export(tmp_path, monkeypatch):
    calls: list[str] = []

    def refuse(backend, name, dest, **kwargs):
        calls.append(name)
        raise D365Error(_MANAGED_FAULT, status=400)

    monkeypatch.setattr(solution_transfer, "export_solution", refuse)
    out = tmp_path / "inv"
    first = FakeBackend(fail={("systemforms", 1): D365Error("boom", status=500)})
    assert not _extract(first, out, solutions=("contoso",)).complete
    summary = _extract(FakeBackend(), out, solutions=("contoso",))
    assert calls == ["contoso"] and summary.complete  # the second run did not export again
    assert summary.kinds["ribbon_diff"] == {"status": "unsupported", "records": 0}
    (gap,) = [g for g in _manifest(out)["gaps"] if g["kind"] == "ribbon_diff"]
    assert "contoso" in gap["description"]


def test_a_scoped_run_keeps_a_type_the_solution_lists_without_its_assembly(tmp_path):
    org = _org_with_plugin_types()
    org["solutioncomponents"][0] = [
        r for r in org["solutioncomponents"][0] if r["componenttype"] != 91
    ]
    org["solutioncomponents"][0].append(
        {"solutioncomponentid": _id(104), "componenttype": 90, "objectid": _id(32),
         **_lookup("solutionid", "solution", 2)}
    )  # fmt: skip
    out = tmp_path / "inv"
    _extract(FakeBackend(org=org), out, solutions=("contoso",))
    assert _keys(_manifest(out), "plugin_type") == ["Contoso.InGac|Contoso.InGac.Hook"]
