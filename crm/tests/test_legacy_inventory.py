"""`crm.core.inventory_extract` on a crm2011 profile: CRM 2011 extraction, offline.

A fake client stands in for the SOAP transport (its paging is tested in
test_legacy_transport.py), so these tests drive the extractor's units,
checkpoint, record building and gaps over a small synthetic org.
"""

# pyright: basic
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory, inventory_extract, legacy_reads
from crm.core import session as session_mod
from crm.core.legacy import Page
from crm.utils.d365_backend import (
    _TRANSPORT_FAILURE_PREFIX,  # pyright: ignore[reportPrivateUsage]
    ConnectionProfile,
    D365Error,
)
from crm.utils.safe_xml import fromstring

_URL = "https://crm.contoso.local/contoso"
_SCHEMA = Path(inventory.__file__).parent.parent / "schemas" / "inventory-manifest-v1.schema.json"


def _id(n: int) -> str:
    return f"90000000-0000-0000-0000-{n:012d}"


def _ref(entity: str, n: int, name: str | None = None) -> dict:
    return {"id": _id(n), "logical_name": entity, "name": name}


_JS = b"function approve() { window.open('/ISV/ManagerApproval.aspx?id=' + id); }\r\n"
_DLL = b"MZ\x90\x00binary"
_SITEMAP = '<SiteMap><SubArea Url="/ISV/InstallationPackageSendOptions.aspx"/></SiteMap>'

# entity -> pages of rows, as the fake org answers RetrieveMultiple.
_ORG: dict[str, list[list[dict]]] = {
    "solution": [
        [
            {"id": _id(1), "uniquename": "System", "friendlyname": "System", "version": "5.0",
             "ismanaged": True, "isvisible": False, "publisherid": _ref("publisher", 90, "MS")},
            {"id": _id(2), "uniquename": "contoso", "friendlyname": "Contoso", "version": "1.0",
             "ismanaged": False, "isvisible": True,
             "publisherid": _ref("publisher", 91, "Contoso")},
        ]
    ],
    "webresource": [
        [{"id": _id(10), "name": "new_/scripts/project.js", "displayname": "project.js",
          "webresourcetype": 3, "ismanaged": False, "solutionid": _ref("solution", 2),
          "content": base64.b64encode(_JS).decode()}],
        [{"id": _id(11), "name": "new_/img/logo.png", "displayname": None, "webresourcetype": 5,
          "ismanaged": False, "solutionid": _ref("solution", 2),
          "content": base64.b64encode(b"\x89PNG").decode()}],
    ],
    "pluginassembly": [
        [
            {"id": _id(20), "name": "Contoso.Plugins", "version": "1.0.0.0", "culture": "neutral",
             "publickeytoken": "abc", "sourcetype": 0, "isolationmode": 2, "ismanaged": False,
             "solutionid": _ref("solution", 2), "content": base64.b64encode(_DLL).decode()},
            {"id": _id(21), "name": "Legacy.OnDisk", "version": "1.0.0.0", "culture": "neutral",
             "publickeytoken": "def", "sourcetype": 1, "isolationmode": 1, "ismanaged": False,
             "solutionid": _ref("solution", 2)},
        ]
    ],
    "plugintype": [
        [{"id": _id(30), "typename": "Contoso.Plugins.ProjectPlugin", "friendlyname": "Project",
          "pluginassemblyid": _ref("pluginassembly", 20, "Contoso.Plugins"),
          "isworkflowactivity": False, "ismanaged": False, "solutionid": _ref("solution", 2)}]
    ],
    "sdkmessageprocessingstep": [
        [{"id": _id(40), "name": "Project: Update", "stage": 40, "mode": 0, "rank": 1,
          "plugintypeid": _ref("plugintype", 30), "statecode": 0, "filteringattributes": None,
          "supporteddeployment": 0, "ismanaged": False, "solutionid": _ref("solution", 2),
          "message.name": "Update", "filter.primaryobjecttypecode": "new_project"}]
    ],
    "sdkmessageprocessingstepimage": [
        [{"id": _id(41), "name": "pre", "entityalias": "pre", "imagetype": 0, "attributes": None,
          "messagepropertyname": "Target", "sdkmessageprocessingstepid": _ref("step", 40),
          "ismanaged": False, "solutionid": _ref("solution", 2)}]
    ],
    "workflow": [
        [{"id": _id(50), "name": "Project approval", "category": 0, "primaryentity": "new_project",
          "statecode": 1, "ismanaged": False, "solutionid": _ref("solution", 2),
          "xaml": "<Activity/>"}]
    ],
    "systemform": [
        [{"id": _id(60), "name": "Account", "objecttypecode": "account", "type": 2,
          "ismanaged": False, "solutionid": _ref("solution", 1), "formxml": "<form/>"}]
    ],
    "savedquery": [
        [{"id": _id(70), "name": "Active Projects", "returnedtypecode": "new_project",
          "querytype": 0, "ismanaged": False, "solutionid": _ref("solution", 2),
          "fetchxml": "<fetch/>", "layoutxml": "<grid/>"}]
    ],
    "sitemap": [
        [{"id": _id(80), "ismanaged": False, "solutionid": _ref("solution", 1),
          "sitemapxml": _SITEMAP}]
    ],
    "solutioncomponent": [
        [
            {"id": _id(100), "solutionid": _ref("solution", 2), "componenttype": 61,
             "objectid": _id(10)},
            {"id": _id(101), "solutionid": _ref("solution", 2), "componenttype": 1,
             "objectid": _id(201)},
            {"id": _id(102), "solutionid": _ref("solution", 2), "componenttype": 31,
             "objectid": _id(999)},
        ]
    ],
    "dependency": [
        [{"id": _id(110), "dependentcomponenttype": 92, "dependentcomponentobjectid": _id(40),
          "requiredcomponenttype": 1, "requiredcomponentobjectid": _id(201),
          "dependencytype": 2}]
    ],
}  # fmt: skip

_ENTITIES = [
    {"logical_name": "account", "custom": False, "object_type_code": 1,
     "metadata_id": _id(200), "managed": True, "display_name": "Account"},
    {"logical_name": "new_project", "custom": True, "object_type_code": 10001,
     "metadata_id": _id(201), "managed": False, "display_name": "Project"},
    # An intersect entity: no display label, no UI, so no ribbon (the server faults the read).
    {"logical_name": "new_project_account", "custom": True, "object_type_code": 10002,
     "metadata_id": _id(202), "managed": False, "display_name": None},
]  # fmt: skip
_OPTION_SETS = [
    {"name": "new_tier", "metadata_id": _id(300), "global": True, "custom": True,
     "type": "Picklist", "options": [{"value": 1, "label": "Gold"}]},
]  # fmt: skip


def _detail(name: str) -> dict:
    attrs = [
        {"logical_name": f"{name}id", "metadata_id": _id(400 + len(name)),
         "type": "Uniqueidentifier",
         "custom": False, "managed": True, "required_level": "SystemRequired", "option_set": None},
    ]  # fmt: skip
    rels = {"one_to_many": [], "many_to_one": [], "many_to_many": []}
    if name == "new_project":
        rels["many_to_one"] = [
            {"schema_name": "account_new_project", "metadata_id": _id(500), "custom": True,
             "managed": False, "referenced_entity": "account", "referenced_attribute": "accountid",
             "referencing_entity": "new_project", "referencing_attribute": "new_accountid"}
        ]  # fmt: skip
    else:
        rels["one_to_many"] = [
            {"schema_name": "account_new_project", "metadata_id": _id(500), "custom": True,
             "managed": False, "referenced_entity": "account", "referenced_attribute": "accountid",
             "referencing_entity": "new_project", "referencing_attribute": "new_accountid"}
        ]  # fmt: skip
    return {
        "logical_name": name, "metadata_id": None, "custom": name.startswith("new_"),
        "managed": False, "object_type_code": 0, "primary_id_attribute": f"{name}id",
        "primary_name_attribute": "name", "attributes": attrs, "relationships": rels,
        "option_sets": [],
    }  # fmt: skip


class FakeClient:
    """Answers the extractor's reads from `_ORG`; *fail* raises once at (entity, page)."""

    def __init__(self, fail: dict | None = None, org: dict | None = None):
        self.profile = ConnectionProfile(
            name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
        )
        self.endpoint = _URL + "/XRMServices/2011/Organization.svc/web"
        self.org = org or _ORG
        self.fail = dict(fail or {})
        self.requests: list[tuple[str, int, str | None]] = []

    def execute(self, name: str) -> dict:
        return {
            "WhoAmI": {"OrganizationId": _id(999_000)},
            "RetrieveVersion": {"Version": "5.0.9690.3557"},
        }[name]

    def pages(self, fetch_xml: str, *, page_size: int = 5000, start: Page | None = None):
        entity = fromstring(fetch_xml)[0].get("name", "")
        pages = self.org.get(entity, [[]])
        number = start.number + 1 if start else 1
        cookie = start.cookie if start else None
        while number <= len(pages):
            self.requests.append((entity, number, cookie))
            exc = self.fail.pop((entity, number), None)
            if exc is not None:
                raise exc
            more = number < len(pages)
            next_cookie = f"cookie-{entity}-{number}" if more else None
            yield Page(entity, number, copy.deepcopy(pages[number - 1]), more, next_cookie)
            cookie = next_cookie
            number += 1


@pytest.fixture
def reads(monkeypatch):
    """Patch the metadata and ribbon reads; return the per-read call counts."""
    calls: dict[str, int] = {}

    def count(name, value):
        def fn(*args):
            calls[name] = calls.get(name, 0) + 1
            return value(*args) if callable(value) else copy.deepcopy(value)

        return fn

    monkeypatch.setattr(legacy_reads, "list_entities", count("entities", _ENTITIES))
    monkeypatch.setattr(legacy_reads, "list_option_sets", count("option_sets", _OPTION_SETS))
    monkeypatch.setattr(legacy_reads, "entity_detail", count("detail", lambda c, n: _detail(n)))
    monkeypatch.setattr(
        legacy_reads,
        "ribbon_xml",
        count("ribbon", lambda c, e: f"<RibbonDefinitions entity='{e}'/>".encode()),
    )
    return calls


def _extract(client, out: Path, *, include_system: bool = True):
    return inventory_extract.extract(
        inventory_extract.LegacySource(client),
        out,
        cli_version="9.9.9",
        include_system=include_system,
        now=lambda: "2026-11-04T10:00:00Z",
    )


def _manifest(out: Path) -> dict:
    data = json.loads((out / "manifest.json").read_text())
    jsonschema.Draft202012Validator(json.loads(_SCHEMA.read_text())).validate(data)
    inventory.read_inventory(out)
    return data


def _keys(data: dict, kind: str) -> list[str]:
    return sorted(r["key"] for r in data["records"] if r["kind"] == kind)


def test_a_full_extraction_writes_a_valid_manifest_and_drops_the_checkpoint(reads, tmp_path):
    out = tmp_path / "inv"
    summary = _extract(FakeClient(), out)
    assert summary.complete and not summary.resumed and summary.errors == []
    assert not (out / ".checkpoint").exists()
    data = _manifest(out)
    assert data["source"] == {
        "platform": "crm2011",
        "org_url": _URL,
        "organization_id": _id(999_000),
        "version": "5.0.9690.3557",
    }
    assert data["extracted_at"] == "2026-11-04T10:00:00Z"
    assert all(c["status"] == "complete" for c in data["coverage"].values())
    assert data["coverage"]["web_resource"]["pages"] == 2
    assert _keys(data, "plugin_type") == ["Contoso.Plugins|Contoso.Plugins.ProjectPlugin"]
    assert _keys(data, "step") == [
        "Update|new_project|40|0|Contoso.Plugins%7CContoso.Plugins.ProjectPlugin|Project: Update"
    ]
    assert _keys(data, "relationship") == ["account_new_project"]
    assert _keys(data, "ribbon") == ["account", "application", "new_project"]
    assert {r["origin"] for r in data["records"] if r["kind"] == "ribbon"} == {"unknown"}
    entities = {r["key"]: r for r in data["records"] if r["kind"] == "entity"}
    assert entities["account"]["display_name"] == "Account"
    assert entities["new_project_account"]["display_name"] is None
    assert _keys(data, "sitemap") == ["default"]
    assert _keys(data, "solution_membership") == [
        "contoso|entity|new_project",
        "contoso|web_resource|new_/scripts/project.js",
    ]
    (dependency,) = [r for r in data["records"] if r["kind"] == "dependency"]
    assert dependency["fields"]["required_key"] == "new_project"
    assert dependency["fields"]["dependent_kind"] == "step"
    by_kind = {r["kind"]: r for r in data["records"]}
    js = next(r for r in data["records"] if r["key"] == "new_/scripts/project.js")
    (artifact,) = js["artifacts"]
    assert artifact["content"] == "text"
    assert (out / artifact["path"]).read_bytes() == _JS
    assert artifact["normalized_sha256"] != artifact["sha256"]  # CRLF normalized
    assert by_kind["form"]["origin"] == "system"
    assert js["origin"] == "custom"
    (dll,) = next(r for r in data["records"] if r["key"] == "Contoso.Plugins")["artifacts"]
    assert dll["content"] == "binary" and (out / dll["path"]).read_bytes() == _DLL
    assert summary.records == len(data["records"])


def test_assembly_records_carry_their_storage_and_isolation(reads, tmp_path):
    out = tmp_path / "inv"
    _extract(FakeClient(), out)
    fields = {
        r["key"]: r["fields"] for r in _manifest(out)["records"] if r["kind"] == "plugin_assembly"
    }
    assert (
        fields["Contoso.Plugins"]["source_type"],
        fields["Contoso.Plugins"]["isolation_mode"],
    ) == (0, 2)
    assert (fields["Legacy.OnDisk"]["source_type"], fields["Legacy.OnDisk"]["isolation_mode"]) == (
        1,
        1,
    )


def test_the_gaps_name_what_soap_cannot_see(reads, tmp_path):
    out = tmp_path / "inv"
    _extract(FakeClient(), out)
    gaps = _manifest(out)["gaps"]
    by_text = {g["description"] or g["key"]: g for g in gaps}
    assert by_text["Legacy.OnDisk"]["kind"] == "plugin_assembly"
    assert "on disk" in by_text["Legacy.OnDisk"]["reason"]
    isv = [g["description"] for g in gaps if (g["description"] or "").startswith("ISV page")]
    assert isv == [
        "ISV page /ISV/InstallationPackageSendOptions.aspx, referenced from sitemap default",
        "ISV page /ISV/ManagerApproval.aspx, referenced from web_resource new_/scripts/project.js",
    ]
    descriptions = [g["description"] or "" for g in gaps]
    assert any(d.startswith("external integrations") for d in descriptions)
    assert any(d.startswith("SQL data sources of SSRS reports") for d in descriptions)
    assert any(
        d.startswith("1 solution_membership row(s)") and "type 31" in d for d in descriptions
    )
    # The unlabelled entity gets no ribbon read, and the gap says so.
    ribbon = [g for g in gaps if g["kind"] == "ribbon"]
    assert len(ribbon) == 1 and "new_project_account" in ribbon[0]["description"]


def test_a_resume_after_a_mid_run_failure_reads_each_page_once(reads, tmp_path):
    out = tmp_path / "inv"
    reset = D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: connection reset")
    first = FakeClient(fail={("webresource", 2): reset})
    with pytest.raises(D365Error, match="connection reset"):
        _extract(first, out)
    assert ("webresource", 1, None) in first.requests
    assert (out / ".checkpoint").is_dir() and not (out / "manifest.json").exists()
    before = dict(reads)

    second = FakeClient()
    summary = _extract(second, out)
    assert summary.resumed and summary.complete
    # Finished units are reused: the web resources resume at page 2 from page 1's cookie.
    assert ("webresource", 1, None) not in second.requests
    assert ("webresource", 2, "cookie-webresource-1") in second.requests
    assert not any(entity == "solution" for entity, _, _ in second.requests)
    assert reads["entities"] == before["entities"]
    assert reads["detail"] == len(_ENTITIES)  # each entity's detail is read once in all
    assert summary.reused["web_resource"] == 1 and summary.reused["solution"] == 1
    data = _manifest(out)
    assert _keys(data, "web_resource") == ["new_/img/logo.png", "new_/scripts/project.js"]
    assert data["extracted_at"] == "2026-11-04T10:00:00Z"


def test_a_failed_kind_is_reported_and_keeps_the_checkpoint(reads, tmp_path):
    out = tmp_path / "inv"
    fault = D365Error("plug-in failed", status=500, code="0x80040265")
    summary = _extract(FakeClient(fail={("systemform", 1): fault}), out)
    assert not summary.complete
    assert summary.errors == ["systemform page 1: plug-in failed"]
    data = _manifest(out)
    assert data["coverage"]["form"] == {
        "status": "failed", "pages": 0, "records": 0, "truncated": False,
        "errors": ["systemform page 1: plug-in failed"],
    }  # fmt: skip
    assert data["coverage"]["view"]["status"] == "complete"
    assert (out / ".checkpoint").is_dir()
    # The re-run reads only the failed kind and completes.
    again = FakeClient()
    assert _extract(again, out).complete
    assert {entity for entity, _, _ in again.requests} == {"systemform"}


def test_a_page_with_more_records_and_no_cookie_is_truncated(reads, tmp_path):
    class NoCookie(FakeClient):
        def pages(self, fetch_xml, *, page_size=5000, start=None):
            for page in super().pages(fetch_xml, page_size=page_size, start=start):
                if page.entity == "webresource":
                    yield Page(page.entity, 1, page.records, True, None)
                    return
                yield page

    out = tmp_path / "inv"
    summary = _extract(NoCookie(), out)
    assert not summary.complete
    coverage = _manifest(out)["coverage"]["web_resource"]
    assert coverage["status"] == "partial" and coverage["truncated"] is True
    assert coverage["records"] == 1


def test_an_out_folder_with_files_and_no_checkpoint_is_refused(reads, tmp_path):
    (tmp_path / "notes.txt").write_text("mine")
    with pytest.raises(D365Error, match="no checkpoint"):
        _extract(FakeClient(), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]


def test_a_checkpoint_of_another_org_is_refused(reads, tmp_path):
    out = tmp_path / "inv"
    with pytest.raises(D365Error):
        _extract(
            FakeClient(fail={("webresource", 1): D365Error(f"{_TRANSPORT_FAILURE_PREFIX}: x")}), out
        )
    other = FakeClient()
    other.profile = ConnectionProfile(
        name="o", url="https://other.local/org", domain="D", username="u", platform="crm2011"
    )
    with pytest.raises(D365Error, match="another org"):
        _extract(other, out)


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    import crm.core.keyring_store as ks

    monkeypatch.setattr(ks, "is_available", lambda: False)
    session_mod.save_profile(
        ConnectionProfile(
            name="legacy", url=_URL, domain="CONTOSO", username="alice", platform="crm2011"
        )
    )
    session_mod.save_profile_secret_plaintext("legacy", "pw")
    session_mod.save_profile(ConnectionProfile(name="v9", url=_URL, domain="D", username="u"))
    return tmp_path


def test_inventory_extract_returns_a_short_summary(crm_home, reads, monkeypatch, tmp_path):
    fake = FakeClient()
    monkeypatch.setattr("crm.cli.CLIContext.legacy_client", lambda self: fake)
    out = tmp_path / "inv"
    result = CliRunner().invoke(
        cli, ["--json", "--profile", "legacy", "inventory", "extract", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.stdout)
    assert env["ok"] is True
    data = env["data"]
    assert data["manifest"] == str(out / "manifest.json")
    assert data["complete"] is True
    assert data["kinds"]["step"] == {"status": "complete", "records": 1}
    assert env["meta"] == {"profile": "legacy", "url": fake.endpoint}


def test_inventory_extract_failure_is_a_failure_envelope_with_the_summary(
    crm_home, reads, monkeypatch, tmp_path
):
    fake = FakeClient(fail={("systemform", 1): D365Error("plug-in failed", status=500)})
    monkeypatch.setattr("crm.cli.CLIContext.legacy_client", lambda self: fake)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "legacy", "inventory", "extract", "--out", str(tmp_path / "i")],
    )
    assert result.exit_code == 1
    env = json.loads(result.stdout)
    assert env["ok"] is False
    assert "systemform page 1: plug-in failed" in env["error"]
    assert env["data"]["kinds"]["form"]["status"] == "failed"


@pytest.mark.parametrize(
    ("argv", "error"),
    [
        ((), "Run `crm profile add`"),
        (("--profile", "nope"), "Profile 'nope' not found."),
    ],
)
def test_inventory_extract_with_no_saved_profile_gives_the_profile_error(
    tmp_path, monkeypatch, argv, error
):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    result = CliRunner().invoke(
        cli, ["--json", *argv, "inventory", "extract", "--out", str(tmp_path / "i")]
    )
    assert result.exit_code == 1
    assert error in json.loads(result.output)["error"]
    assert not (tmp_path / "i").exists()


def test_a_failed_source_read_is_an_error_and_keeps_the_checkpoint(reads, tmp_path):
    out = tmp_path / "inv"
    client = FakeClient()

    def execute(name: str) -> dict:
        raise D365Error("WhoAmI failed", status=500, code="0x80040216")

    client.execute = execute
    summary = _extract(client, out)
    assert not summary.complete
    assert summary.errors == ["source: WhoAmI failed"]
    source_gaps = [
        g for g in _manifest(out)["gaps"] if g["kind"] is None and "WhoAmI" in g["reason"]
    ]
    assert source_gaps == [
        {
            "kind": None,
            "key": None,
            "description": "source identity and build (WhoAmI, RetrieveVersion)",
            "reason": "the read failed: WhoAmI failed",
        }
    ]
    assert (out / ".checkpoint").is_dir()
    assert _extract(FakeClient(), out).complete


def test_a_ribbon_that_is_not_a_zip_is_a_d365_error(monkeypatch):
    from typing import cast
    from xml.etree import ElementTree as ET

    from crm.core.legacy import LegacyClient

    node = ET.Element("value")
    node.text = base64.b64encode(b"not a zip").decode()
    stub = type("Stub", (), {"execute_results": lambda self, *a: {"CompressedEntityXml": node}})
    with pytest.raises(D365Error, match="not a readable ribbon zip"):
        legacy_reads.ribbon_xml(cast("LegacyClient", stub()), "account")


def test_a_resume_drops_artifacts_an_earlier_run_wrote(reads, tmp_path):
    out = tmp_path / "inv"
    fault = D365Error("plug-in failed", status=500, code="0x80040265")
    _extract(FakeClient(fail={("systemform", 1): fault}), out)
    stale = out / "artifacts" / "form" / "stale.form.xml"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("<form/>")
    assert _extract(FakeClient(), out).complete
    assert not stale.exists()


def test_inventory_extract_checks_the_out_folder_before_connecting(crm_home, monkeypatch, tmp_path):
    def no_client(self):
        raise AssertionError("connected before the folder check")

    monkeypatch.setattr("crm.cli.CLIContext.legacy_client", no_client)
    (tmp_path / "notes.txt").write_text("mine")
    result = CliRunner().invoke(
        cli, ["--json", "--profile", "legacy", "inventory", "extract", "--out", str(tmp_path)]
    )
    assert result.exit_code == 1, result.output
    assert "no checkpoint" in json.loads(result.output)["error"]


def test_inventory_extract_reports_a_write_failure_as_an_envelope(
    crm_home, reads, monkeypatch, tmp_path
):
    monkeypatch.setattr("crm.cli.CLIContext.legacy_client", lambda self: FakeClient())

    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(tmp_path / "inv"))

    monkeypatch.setattr(inventory_extract, "extract", denied)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "legacy", "inventory", "extract", "--out", str(tmp_path / "inv")],
    )
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False and "Permission denied" in env["error"]


def test_a_ribbon_with_a_corrupt_entry_is_a_d365_error(monkeypatch):
    import io
    import zipfile
    import zlib
    from typing import cast
    from xml.etree import ElementTree as ET

    from crm.core.legacy import LegacyClient

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("RibbonXml.xml", "<RibbonDefinitions/>")
    node = ET.Element("value")
    node.text = base64.b64encode(buf.getvalue()).decode()

    def corrupt(self, name):
        raise zlib.error("invalid stored block lengths")

    monkeypatch.setattr(zipfile.ZipFile, "read", corrupt)
    stub = type("Stub", (), {"execute_results": lambda self, *a: {"CompressedEntityXml": node}})
    with pytest.raises(D365Error, match="not a readable ribbon zip"):
        legacy_reads.ribbon_xml(cast("LegacyClient", stub()), "account")


def test_inventory_extract_reports_an_unreadable_out_folder_as_an_envelope(
    crm_home, monkeypatch, tmp_path
):
    def denied(out):
        raise PermissionError(13, "Permission denied", str(out))

    monkeypatch.setattr(inventory_extract, "check_out", denied)
    result = CliRunner().invoke(
        cli,
        ["--json", "--profile", "legacy", "inventory", "extract", "--out", str(tmp_path / "inv")],
    )
    assert result.exit_code == 1, result.output
    assert "Permission denied" in json.loads(result.output)["error"]
