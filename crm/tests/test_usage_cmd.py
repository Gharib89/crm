"""`--in-use` / `--exclude-unknown` on the entity listings (spec #1091, slice #1092)."""

# pyright: basic
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile
from crm.utils.safe_xml import fromstring

_FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
_URL = "https://crm.contoso.local/contoso"
_ENDPOINT = _URL + "/XRMServices/2011/Organization.svc/web"
_CRM = "http://schemas.microsoft.com/xrm/2011/Contracts"
_XML = {"Content-Type": "text/xml; charset=utf-8"}

_ENTITIES = [
    {"LogicalName": n, "EntitySetName": f"{n}s", "SchemaName": n.title()}
    for n in ("account", "contact", "note", "ghost")
]
_TARGETS = [
    {
        **e,
        "PrimaryIdAttribute": f"{e['LogicalName']}id",
        "IsPrivate": False,
        "TableType": "Standard",
    }
    for e in _ENTITIES
]


def _v9_get(path):
    if path == "EntityDefinitions":
        return {"value": _ENTITIES}
    if path.startswith("RetrieveTotalRecordCount"):
        names = re.findall(r"'([^']+)'", unquote(path))
        # `contact` is zero in the snapshot but holds a row; `note` is truly empty
        return {
            "EntityRecordCountCollection": {
                "Keys": names,
                "Values": [3 if n == "account" else 0 for n in names],
            }
        }
    entity = path.removesuffix("s")
    if entity == "ghost":
        from crm.utils.d365_backend import D365Error

        raise D365Error("no such set", status=404)
    return {"value": [{"x": 1}] if entity == "contact" else []}


@pytest.fixture
def v9(inject_backend, make_fake_backend, monkeypatch):
    monkeypatch.setenv("CRM_HOME", "/nonexistent-crm-home")
    return inject_backend(make_fake_backend(responses={"get": _v9_get, "get_collection": _TARGETS}))


def _entities(*flags):
    result = CliRunner().invoke(cli, ["--json", "metadata", "entities", *flags])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


def test_without_the_flag_rows_are_unchanged_and_nothing_is_probed(v9):
    rows = _entities()
    assert [r["LogicalName"] for r in rows] == ["account", "contact", "note", "ghost"]
    assert all("usage" not in r for r in rows)
    assert not any(p.startswith("RetrieveTotalRecordCount") for _, p, _ in v9.calls)


def test_in_use_drops_unused_only_and_labels_every_row(v9):
    rows = _entities("--in-use")
    states = {r["LogicalName"]: r["usage"]["state"] for r in rows}
    assert states == {"account": "used", "contact": "used", "ghost": "unknown"}
    assert {r["LogicalName"]: r["usage"]["value"] for r in rows} == {
        "account": 3,
        "contact": None,
        "ghost": None,
    }
    assert set(rows[0]["usage"]) == {"state", "signal", "value", "as_of", "reason"}


def test_an_entity_missing_from_the_probe_targets_is_labelled_unknown(
    inject_backend, make_fake_backend
):
    targets = [t for t in _TARGETS if t["LogicalName"] != "note"]
    inject_backend(make_fake_backend(responses={"get": _v9_get, "get_collection": targets}))
    rows = {r["LogicalName"]: r["usage"] for r in _entities("--in-use")}
    assert rows["note"]["state"] == "unknown" and rows["note"]["reason"]


def test_exclude_unknown_keeps_proven_used_only(v9):
    rows = _entities("--in-use", "--exclude-unknown")
    assert [r["LogicalName"] for r in rows] == ["account", "contact"]


def test_exclude_unknown_alone_is_a_usage_error(v9):
    result = CliRunner().invoke(cli, ["metadata", "entities", "--exclude-unknown"])
    assert result.exit_code == 2
    assert "--in-use" in result.output


def test_in_use_prints_progress_on_stderr(v9):
    result = CliRunner().invoke(cli, ["--json", "metadata", "entities", "--in-use"])
    assert "[crm] usage stage=entities total=4" in result.stderr
    assert "[crm] usage stage=entities done=4/4" in result.stderr
    json.loads(result.stdout)


# ── CRM 2011 ─────────────────────────────────────────────────────────────


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


def _answer(request, context):
    context.headers.update(_XML)
    root = fromstring(request.body)
    name = root.findtext(f".//{{{_CRM}}}RequestName")
    if name == "RetrieveAllEntities":
        return (_FIXTURES / "retrieve_all_entities.xml").read_text()
    entity = fromstring(root.findtext(f".//{{{_CRM}}}Query"))[0].get("name")
    if entity == "new_project":
        context.status_code = 500
        return (_FIXTURES / "fault_request_not_supported.xml").read_text()
    # `account` holds rows, `contact` is empty
    return (_FIXTURES / ("rm_solution.xml" if entity == "account" else "rm_empty.xml")).read_text()


def _legacy(*flags):
    with requests_mock.Mocker() as m:
        m.post(_ENDPOINT, text=_answer)
        result = CliRunner().invoke(
            cli, ["--json", "--profile", "legacy", "legacy", "metadata", *flags]
        )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)["data"]


def test_legacy_in_use_uses_the_primary_id_from_metadata(crm_home):
    rows = _legacy("--in-use")
    assert {r["logical_name"]: r["usage"]["state"] for r in rows} == {
        "account": "used",
        "new_project": "unknown",
    }
    strict = _legacy("--in-use", "--exclude-unknown")
    assert [r["logical_name"] for r in strict] == ["account"]


def test_legacy_without_the_flag_has_no_usage(crm_home):
    assert all("usage" not in r for r in _legacy())
