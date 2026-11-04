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
from crm.core import inventory, inventory_extract
from crm.core import session as session_mod
from crm.core.webapi_inventory import WebApiSource
from crm.utils.d365_backend import (
    _TRANSPORT_FAILURE_PREFIX,  # pyright: ignore[reportPrivateUsage]
    ConnectionProfile,
    D365Error,
)
from crm.utils.safe_xml import fromstring

_URL = "https://contoso.crm.dynamics.com"
_SECRET = "s3cr3t-client-secret-value"
_SCHEMA = Path(inventory.__file__).parent.parent / "schemas" / "inventory-manifest-v1.schema.json"
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
     "ObjectTypeCode": 1, "MetadataId": _id(200), "IsManaged": True},
    {"LogicalName": "new_project", "DisplayName": _label("Project"), "IsCustomEntity": True,
     "ObjectTypeCode": 10001, "MetadataId": _id(201), "IsManaged": False},
    {"LogicalName": "new_project_account", "DisplayName": _label(None), "IsCustomEntity": True,
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
         "RequiredLevel": {"Value": "None"},
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
        rows = {"EntityDefinitions": _ENTITIES, "GlobalOptionSetDefinitions": _OPTION_SETS}[path]
        return copy.deepcopy(rows)

    def _fetch(self, entity_set: str, fetch_xml: str, headers: dict) -> dict:
        assert "odata.include-annotations" in headers.get("Prefer", "")
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


def _extract(backend, out: Path, solutions: tuple[str, ...] = ()):
    return inventory_extract.extract(
        WebApiSource(backend), out, cli_version="9.9.9", solutions=solutions,
        now=lambda: "2026-11-04T10:00:00Z",
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
    assert all(c["status"] == "complete" for c in data["coverage"].values())
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
    assert _keys(data, "ribbon") == ["account", "application", "new_project"]
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
    data = json.loads(result.output)["data"]
    assert data["complete"] is True
    assert data["kinds"]["solution"] == {"status": "complete", "records": 1}
    assert _manifest(out)["scope"]["solutions"] == ["contoso"]


def test_inventory_extract_on_a_v9_profile_fails_on_a_failed_kind(crm_home, monkeypatch, tmp_path):
    fake = FakeBackend(fail={("systemforms", 1): D365Error("plug-in failed", status=500)})
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: fake)
    argv = ["--json", "--profile", "v9", "inventory", "extract", "--out", str(tmp_path / "i")]
    result = CliRunner().invoke(cli, argv)
    assert result.exit_code == 1
    env = json.loads(result.output)
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
