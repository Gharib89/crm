"""`crm legacy metadata` / `crm legacy components` over a mocked CRM 2011 /web endpoint."""

# pyright: basic
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod
from crm.core.legacy import LegacyClient
from crm.core.legacy_reads import KINDS, read_components
from crm.utils.d365_backend import ConnectionProfile
from crm.utils.safe_xml import fromstring

_FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
_URL = "https://crm.contoso.local/contoso"
_ENDPOINT = _URL + "/XRMServices/2011/Organization.svc/web"
_CRM = "http://schemas.microsoft.com/xrm/2011/Contracts"
_XML = {"Content-Type": "text/xml; charset=utf-8"}

# entity name -> fixture, or None to fault: what a mocked org answers per component kind.
_ROWS = {
    "pluginassembly": "rm_pluginassembly.xml",
    "sdkmessageprocessingstep": "rm_sdkmessageprocessingstep.xml",
}


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
    return tmp_path


def _fault(context):
    context.status_code = 500
    return (_FIXTURES / "fault_entity_not_found.xml").read_text()


def _answer(rows=None, fault_entities=(), fault_kinds=()):
    """A requests_mock callback choosing the fixture the real org would answer."""
    rows = {**_ROWS, **(rows or {})}

    def respond(request, context):
        context.headers.update(_XML)
        root = fromstring(request.body)
        name = root.findtext(f".//{{{_CRM}}}RequestName")
        if name == "RetrieveAllEntities":
            return (_FIXTURES / "retrieve_all_entities.xml").read_text()
        if name == "RetrieveAllOptionSets":
            return (_FIXTURES / "retrieve_all_option_sets.xml").read_text()
        if name == "RetrieveEntity":
            (logical,) = re.findall(
                r"LogicalName</b:key><b:value[^>]*>([^<]*)<", request.body.decode()
            )
            if logical in fault_entities:
                return _fault(context)
            return (_FIXTURES / "retrieve_entity_account.xml").read_text()
        fetch = fromstring(root.findtext(f".//{{{_CRM}}}Query", default=""))
        entity = fetch[0].get("name")
        if entity in fault_kinds:
            return _fault(context)
        if entity == "webresource":
            page = fetch.get("page")
            return (_FIXTURES / f"rm_webresource_page{page}.xml").read_text()
        return (_FIXTURES / rows.get(entity, "rm_empty.xml")).read_text()

    return respond


def _run(*argv, **answer):
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=_answer(**answer))
        result = CliRunner().invoke(cli, ["--json", "--profile", "legacy", "legacy", *argv])
    return result, m


def test_metadata_lists_every_entity(crm_home):
    result, _ = _run("metadata")
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert [e["logical_name"] for e in env["data"]] == ["account", "contact", "new_project"]
    project = env["data"][2]
    assert project["custom"] is True
    assert project["object_type_code"] == 10001
    assert project["display_name"] == "new_Project"
    assert env["data"][0]["custom"] is False
    assert env["meta"]["profile"] == "legacy"


def test_metadata_entity_detail(crm_home):
    result, _ = _run("metadata", "account")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["errors"] == []
    (account,) = data["entities"]
    assert account["logical_name"] == "account"
    assert account["primary_id_attribute"] == "accountid"
    assert account["primary_name_attribute"] == "name"
    attrs = {a["logical_name"]: a for a in account["attributes"]}
    assert attrs["accountid"]["type"] == "Uniqueidentifier"
    assert attrs["name"]["required_level"] == "ApplicationRequired"
    assert attrs["name"]["option_set"] is None
    assert attrs["new_tier"]["custom"] is True
    assert attrs["new_tier"]["option_set"] == "new_tier"
    assert attrs["address1_addresstypecode"]["option_set"] == "account_address1_addresstypecode"
    rels = account["relationships"]
    assert rels["one_to_many"] == [
        {
            "schema_name": "account_new_project",
            "metadata_id": "40000000-0000-0000-0000-000000000019",
            "custom": True,
            "managed": True,
            "referenced_entity": "account",
            "referenced_attribute": "accountid",
            "referencing_entity": "new_project",
            "referencing_attribute": "new_accountid",
        }
    ]
    assert rels["many_to_one"][0]["schema_name"] == "account_parent_account"
    assert rels["many_to_many"] == [
        {
            "schema_name": "listaccount_association",
            "metadata_id": "40000000-0000-0000-0000-000000000023",
            "custom": False,
            "managed": True,
            "entity1": "list",
            "entity2": "account",
            "intersect_entity": "listmember",
        }
    ]
    sets = {s["name"]: s for s in account["option_sets"]}
    assert list(sets) == [
        "account_address1_addresstypecode",
        "new_tier",
        "account_donotemail",
    ]
    assert sets["new_tier"]["global"] is True
    assert sets["new_tier"]["options"][0] == {"value": 100000000, "label": "Gold"}
    assert sets["account_address1_addresstypecode"]["global"] is False
    assert sets["account_donotemail"]["type"] == "Boolean"
    assert [o["value"] for o in sets["account_donotemail"]["options"]] == [0, 1]


def test_metadata_option_sets_lists_the_global_ones(crm_home):
    result, _ = _run("metadata", "--option-sets")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    by_name = {s["name"]: s for s in data}
    assert by_name["new_tier"]["type"] == "Picklist"
    assert by_name["new_tier"]["global"] is True
    assert [o["label"] for o in by_name["new_tier"]["options"]] == ["Gold", "Silver"]
    boolean = by_name["isregularactivity"]
    assert boolean["type"] == "Boolean"
    assert len(boolean["options"]) == 2


def test_metadata_option_sets_with_an_entity_is_a_usage_error(crm_home):
    result, m = _run("metadata", "account", "--option-sets")
    assert result.exit_code == 2, result.output
    assert m.call_count == 0


def test_a_missing_entity_is_reported_and_the_rest_still_read(crm_home):
    result, _ = _run("metadata", "account", "zz_missing", "contact", fault_entities={"zz_missing"})
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert len(env["data"]["entities"]) == 2
    (err,) = env["data"]["errors"]
    assert err["entity"] == "zz_missing"
    assert err["code"] == "0x80040217"
    assert "1 of 3 entities failed" in env["error"]
    assert "zz_missing" in env["error"]


def test_components_pages_through_web_resources(crm_home):
    result, m = _run("components", "web-resources")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert data["errors"] == []
    rows = data["web-resources"]
    assert len(rows) == 3
    assert rows[0]["content"] == "ZnVuY3Rpb24gb25Mb2FkKCkge30K"
    assert m.call_count == 2


def test_plugin_assemblies_never_skip_a_non_database_binary(crm_home):
    result, _ = _run("components", "plugin-assemblies")
    assert result.exit_code == 0, result.output
    rows = {r["name"]: r for r in json.loads(result.output)["data"]["plugin-assemblies"]}
    assert rows["Contoso.Plugins"]["binary"] == "database"
    assert "binary_reason" not in rows["Contoso.Plugins"]
    disk = rows["Contoso.DiskPlugins"]
    assert disk["binary"] == "unavailable"
    assert disk["binary_reason"] == "stored on disk, not in the database"
    gac = rows["Contoso.GacPlugins"]
    assert gac["binary"] == "unavailable"
    assert gac["binary_reason"] == "stored in the GAC, not in the database"


def test_components_out_writes_payload_files(crm_home, tmp_path):
    out = tmp_path / "dump"
    result, _ = _run("components", "web-resources", "--out", str(out))
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)["data"]["web-resources"]
    assert all("content" not in r for r in rows)
    first = rows[0]
    assert first["content_file"] == "web-resources/50000000-0000-0000-0000-000000000001.js"
    assert (out / first["content_file"]).read_bytes() == b"function onLoad() {}\n"
    assert (out / rows[1]["content_file"]).suffix == ".css"
    assert (out / rows[2]["content_file"]).is_file()


def test_a_payload_that_cannot_be_written_fails_its_kind_only(crm_home, tmp_path):
    out = tmp_path / "dump"
    # A folder where the second row's file goes: the first row is written and kept.
    (out / "web-resources" / "50000000-0000-0000-0000-000000000002.css").mkdir(parents=True)
    result, _ = _run("components", "web-resources", "plugin-assemblies", "--out", str(out))
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)["data"]
    (err,) = data["errors"]
    assert err["kind"] == "web-resources"
    assert err["code"] is None
    assert [r["content_file"] for r in data["web-resources"]] == [
        "web-resources/50000000-0000-0000-0000-000000000001.js"
    ]
    assert len(data["plugin-assemblies"]) == 3


def test_an_out_dir_that_cannot_be_created_is_one_error_envelope(crm_home, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    result, _ = _run("components", "web-resources", "--out", str(blocker / "dump"))
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"].startswith("--out ")


def test_all_kinds_run_when_none_is_named_and_a_fault_does_not_stop_the_rest(crm_home):
    result, m = _run("components", fault_kinds={"plugintype"})
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    data = env["data"]
    assert [k for k in data if k != "errors"] == list(KINDS)
    assert len(data["web-resources"]) == 3
    assert len(data["plugin-assemblies"]) == 3
    assert len(data["plugin-steps"]) == 1
    assert data["plugin-types"] == []
    assert data["forms"] == []
    (err,) = data["errors"]
    assert err["kind"] == "plugin-types"
    assert err["code"] == "0x80040217"
    assert "1 of 9 kinds failed" in env["error"]
    assert "profile" not in env.get("meta", {})


def test_processes_ask_for_definitions_only(crm_home):
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=_answer())
        CliRunner().invoke(
            cli, ["--json", "--profile", "legacy", "legacy", "components", "processes"]
        )
    root = fromstring(m.request_history[0].body)
    fetch = fromstring(root.findtext(f".//{{{_CRM}}}Query", default=""))
    (condition,) = fetch.findall("entity/filter/condition")
    assert (condition.get("attribute"), condition.get("operator"), condition.get("value")) == (
        "type",
        "eq",
        "1",
    )


def test_a_whole_org_failure_is_one_error_envelope(crm_home):
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, status_code=401, text="", headers={"Content-Type": "text/plain"})
        result = CliRunner().invoke(cli, ["--json", "--profile", "legacy", "legacy", "metadata"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["meta"]["category"] == "auth_failed"


@pytest.mark.parametrize(
    "argv", [("components",), ("metadata", "account", "contact")], ids=["kinds", "entities"]
)
def test_an_auth_failure_stops_the_run_instead_of_failing_each_item(crm_home, argv):
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, status_code=401, text="", headers={"Content-Type": "text/plain"})
        result = CliRunner().invoke(cli, ["--json", "--profile", "legacy", "legacy", *argv])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["meta"]["category"] == "auth_failed"
    assert m.call_count == 1


def test_web_resource_content_that_is_not_base64_fails_its_kind_only(tmp_path):
    class Client:
        def retrieve_multiple(self, fetch_xml):
            yield [{"id": "w1", "webresourcetype": 3, "content": "not base64!"}]

    data, errors = read_components(cast(LegacyClient, Client()), ["web-resources"], tmp_path)
    assert data["web-resources"] == []
    (err,) = errors
    assert (err["kind"], err["code"]) == ("web-resources", None)


# ── --solution / --entity scope ────────────────────────────────────────────

_SOLUTION_ID = "50000000-0000-0000-0000-000000000001"
_SCOPED = {"solution": "rm_solution.xml", "solutioncomponent": "rm_solutioncomponent.xml"}
_ENTITY_KINDS = ["plugin-steps", "step-images", "processes", "forms", "views"]


def _sent(m) -> tuple[list[str], dict]:
    """Each request's name (Execute's RequestName, else the operation) and each entity's fetches."""
    names, fetches = [], {}
    for request in m.request_history:
        root = fromstring(request.body)
        name = root.findtext(f".//{{{_CRM}}}RequestName")
        names.append(name or request.headers["SOAPAction"].rpartition("/")[2])
        if name is None:
            fetch = fromstring(root.findtext(f".//{{{_CRM}}}Query", default=""))
            fetches.setdefault(fetch[0].get("name"), []).append(fetch)
    return names, fetches


def _conditions(el) -> list[tuple]:
    return [
        (c.get("attribute"), c.get("operator"), c.get("value") or [v.text for v in c])
        for c in el.findall("condition")
    ]


def _link(el, name):
    (link,) = [x for x in el.findall("link-entity") if x.get("name") == name]
    return link


def _membership(entity, to):
    """Assert *entity* joins solutioncomponent on *to* within the solution."""
    link = _link(entity, "solutioncomponent")
    assert (link.get("from"), link.get("to"), link.get("link-type")) == ("objectid", to, "inner")
    assert _conditions(link.find("filter")) == [("solutionid", "eq", _SOLUTION_ID)]


def test_solution_scope_joins_each_member_kind_on_its_own_or_parent_id(crm_home):
    result, m = _run("components", "--solution", "contoso_core", rows=_SCOPED)
    assert result.exit_code == 0, result.output
    _, fetches = _sent(m)
    (resolve, own_row) = fetches["solution"]
    assert _conditions(resolve.find("entity/filter")) == [("uniquename", "eq", "contoso_core")]
    for entity, to in [
        ("webresource", "webresourceid"),
        ("pluginassembly", "pluginassemblyid"),
        ("plugintype", "pluginassemblyid"),  # its assembly is the member
        ("sdkmessageprocessingstep", "sdkmessageprocessingstepid"),
        ("sdkmessageprocessingstepimage", "sdkmessageprocessingstepid"),  # its step is the member
        ("workflow", "workflowid"),
    ]:
        for fetch in fetches[entity]:  # web resources answer in two pages
            _membership(fetch.find("entity"), to)
    assert _conditions(fetches["workflow"][0].find("entity/filter")) == [("type", "eq", "1")]
    # The solutions kind is that solution's own row.
    assert _conditions(own_row.find("entity/filter")) == [("solutionid", "eq", _SOLUTION_ID)]
    assert [s["uniquename"] for s in json.loads(result.output)["data"]["solutions"]] == [
        "contoso_core"
    ]


def test_solution_scope_reads_forms_and_views_through_their_member_entities(crm_home):
    result, m = _run("components", "forms", "views", "--solution", "contoso_core", rows=_SCOPED)
    assert result.exit_code == 0, result.output
    names, fetches = _sent(m)
    (members,) = fetches["solutioncomponent"]
    assert _conditions(members.find("entity/filter")) == [
        ("solutionid", "eq", _SOLUTION_ID),
        ("componenttype", "in", ["1", "26", "60"]),
    ]
    # The member entity's MetadataId maps to its object type code (account, 1).
    assert "RetrieveAllEntities" in names
    (forms,) = fetches["systemform"]
    either = forms.find("entity/filter/filter")
    assert either.get("type") == "or"
    assert _conditions(either) == [
        ("formid", "in", ["60000000-0000-0000-0000-000000000001"]),
        ("objecttypecode", "in", ["1"]),
    ]
    (views,) = fetches["savedquery"]
    assert _conditions(views.find("entity/filter/filter")) == [("returnedtypecode", "in", ["1"])]


def test_a_solution_with_no_member_entity_reads_no_forms_or_views(crm_home):
    result, m = _run(
        "components",
        "forms",
        "views",
        "--solution",
        "contoso_core",
        rows={"solution": "rm_solution.xml"},
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert (data["forms"], data["views"]) == ([], [])
    names, fetches = _sent(m)
    assert "systemform" not in fetches and "savedquery" not in fetches
    assert "RetrieveAllEntities" not in names


def test_entity_scope_with_no_kind_reads_the_five_entity_kinds_by_type_code(crm_home):
    result, m = _run("components", "--entity", "account")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert [k for k in data if k != "errors"] == _ENTITY_KINDS
    names, fetches = _sent(m)
    assert names[0] == "RetrieveEntity"
    assert "EntityFilters</b:key><b:value" in m.request_history[0].body.decode()
    assert ">Entity</b:value>" in m.request_history[0].body.decode()
    # Every entity condition carries the integer type code, never the logical name.
    assert _conditions(fetches["systemform"][0].find("entity/filter")) == [
        ("objecttypecode", "eq", "1")
    ]
    assert _conditions(fetches["savedquery"][0].find("entity/filter")) == [
        ("returnedtypecode", "eq", "1")
    ]
    assert _conditions(fetches["workflow"][0].find("entity/filter")) == [
        ("type", "eq", "1"),
        ("primaryentity", "eq", "1"),
    ]
    step_filter = _link(fetches["sdkmessageprocessingstep"][0].find("entity"), "sdkmessagefilter")
    assert (step_filter.get("from"), step_filter.get("to")) == ("sdkmessagefilterid",) * 2
    assert _conditions(step_filter.find("filter")) == [("primaryobjecttypecode", "eq", "1")]
    step = _link(
        fetches["sdkmessageprocessingstepimage"][0].find("entity"), "sdkmessageprocessingstep"
    )
    assert (step.get("from"), step.get("to")) == ("sdkmessageprocessingstepid",) * 2
    assert _conditions(_link(step, "sdkmessagefilter").find("filter")) == [
        ("primaryobjecttypecode", "eq", "1")
    ]


def test_solution_and_entity_scope_apply_both(crm_home):
    result, m = _run(
        "components",
        "forms",
        "plugin-steps",
        "--solution",
        "contoso_core",
        "--entity",
        "account",
        rows=_SCOPED,
    )
    assert result.exit_code == 0, result.output
    _, fetches = _sent(m)
    filter_ = fetches["systemform"][0].find("entity/filter")
    assert _conditions(filter_) == [("objecttypecode", "eq", "1")]
    assert filter_.find("filter").get("type") == "or"
    step = fetches["sdkmessageprocessingstep"][0].find("entity")
    _membership(step, "sdkmessageprocessingstepid")
    assert _conditions(_link(step, "sdkmessagefilter").find("filter")) == [
        ("primaryobjecttypecode", "eq", "1")
    ]


def test_an_unknown_solution_fails_naming_it_before_any_kind_is_read(crm_home):
    result, m = _run("components", "--solution", "contoso_nosuch")
    assert result.exit_code == 1, result.output
    assert "contoso_nosuch" in json.loads(result.output)["error"]
    _, fetches = _sent(m)
    assert list(fetches) == ["solution"]


def test_an_unknown_entity_fails_naming_it_before_any_kind_is_read(crm_home):
    result, m = _run("components", "--entity", "contoso_nosuch", fault_entities=("contoso_nosuch",))
    assert result.exit_code == 1, result.output
    assert "contoso_nosuch" in json.loads(result.output)["error"]
    assert m.call_count == 1


def test_entity_scope_with_a_kind_that_has_no_entity_is_a_usage_error(crm_home):
    result, m = _run("components", "web-resources", "forms", "--entity", "account")
    assert result.exit_code == 2, result.output
    assert "web-resources" in result.output
    assert m.call_count == 0


def test_scoped_reads_send_only_allowlisted_requests(crm_home):
    from crm.core.legacy import READ_ONLY_REQUESTS

    _, m = _run("components", "--solution", "contoso_core", "--entity", "account", rows=_SCOPED)
    names, _ = _sent(m)
    assert set(names) == {"RetrieveEntity", "RetrieveAllEntities", "RetrieveMultiple"}
    assert set(names) - {"RetrieveMultiple"} <= READ_ONLY_REQUESTS
