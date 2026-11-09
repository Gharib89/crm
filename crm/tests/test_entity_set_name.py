"""`entity_set_name` on the extract's entity record (#1167): the v9 reader records it, the
CRM 2011 reader never does, `inventory diff` ignores a one-sided value, and `inventory
convert` resolves an OData v2 site from an extract's own entity records.
"""

# pyright: basic
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory
from crm.core import inventory_assess as assess
from crm.core.inventory_diff import diff_inventories
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import map_source
from crm.core.webapi_inventory import WebApiSource
from crm.tests import test_legacy_inventory as legacy
from crm.tests.test_inventory_convert import _record
from crm.tests.test_inventory_convert_odata import SVC, _write
from crm.tests.test_legacy_inventory import reads  # noqa: F401
from crm.tests.test_webapi_inventory import FakeBackend, _extract, _manifest, exports  # noqa: F401

# The two imported fixtures stub each platform's solution export and metadata reads.
pytestmark = pytest.mark.usefixtures("exports", "reads")

_FIXTURES = Path(__file__).parent / "fixtures" / "inventory"


def _entities(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["key"]: r["fields"] for r in data["records"] if r["kind"] == "entity"}


def test_a_v9_extract_records_each_entitys_set_name_and_none_for_an_empty_one(tmp_path):
    out = tmp_path / "inv"
    assert _extract(FakeBackend(), out).complete
    got = _entities(_manifest(out))
    assert got["account"]["entity_set_name"] == "accounts"
    assert got["new_project"]["entity_set_name"] == "new_projects"
    assert "entity_set_name" not in got["new_project_account"]
    # Entity identity and the other fields are as before.
    assert set(got["account"]) == {"logical_name", "object_type_code", "entity_set_name"}


def test_the_v9_entity_listing_selects_entity_set_name() -> None:
    seen: list[dict[str, str]] = []

    class Backend:
        def get_collection(self, path, params):
            seen.append(params)
            return [{"LogicalName": "account", "EntitySetName": ""}]

    (row,) = WebApiSource.list_entities(SimpleNamespace(backend=Backend()))  # type: ignore[arg-type]
    assert "EntitySetName" in seen[0]["$select"].split(",")
    assert not row.get("entity_set_name")


def test_a_crm2011_extract_succeeds_and_records_no_set_name(tmp_path) -> None:
    out = tmp_path / "inv"
    assert legacy._extract(legacy.FakeClient(), out).complete
    got = _entities(json.loads((out / "manifest.json").read_text(encoding="utf-8")))
    assert got and all("entity_set_name" not in f for f in got.values())


def _manifest_fixture(**set_names: str) -> dict[str, Any]:
    data = json.loads((_FIXTURES / "v9" / "manifest.json").read_text(encoding="utf-8"))
    for r in data["records"]:
        if r["kind"] == "entity" and r["key"] in set_names:
            r["fields"]["entity_set_name"] = set_names[r["key"]]
    return data


def _entity_verdicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, tuple[str, list]]:
    diff = diff_inventories(inventory.parse_manifest(a), inventory.parse_manifest(b))
    return {
        e.key: (e.verdict, [(f.name, f.a, f.b) for f in e.fields])
        for e in diff.entries
        if e.kind == "entity"
    }


def _first_entity(data: dict[str, Any]) -> str:
    return next(r["key"] for r in data["records"] if r["kind"] == "entity")


def test_a_one_sided_set_name_is_not_a_difference_either_way() -> None:
    plain = _manifest_fixture()
    key = _first_entity(plain)
    named = _manifest_fixture(**{key: "things"})
    assert _entity_verdicts(plain, named)[key] == ("unchanged", [])
    assert _entity_verdicts(named, plain)[key] == ("unchanged", [])


def test_a_set_name_both_sides_hold_still_differs() -> None:
    key = _first_entity(_manifest_fixture())
    got = _entity_verdicts(_manifest_fixture(**{key: "a"}), _manifest_fixture(**{key: "b"}))
    assert got[key] == ("changed", [("fields.entity_set_name", "a", "b")])


def test_another_one_sided_entity_field_is_still_a_change() -> None:
    a = _manifest_fixture()
    b = copy.deepcopy(a)
    key = _first_entity(b)
    next(r for r in b["records"] if r["key"] == key and r["kind"] == "entity")["fields"][
        "something_new"
    ] = 1
    assert _entity_verdicts(a, b)[key] == ("changed", [("fields.something_new", None, 1)])


def test_convert_patches_a_site_from_an_extract_produced_target(tmp_path) -> None:
    target = tmp_path / "target"
    assert _extract(FakeBackend(), target).complete  # no hand-editing of the inventory
    source = tmp_path / "source"
    code = (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=new_active", false);\n'
    )
    _write(source, "crm2011", [_record("new_/scripts/a.js", code, source)], ("web_resource",))
    findings = tmp_path / "findings.json"
    findings.write_text(assess.dump_findings(assess.assess(source, "online")), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    built = build_packages(source, map_source(source, repo), None, assess.read_findings(findings))
    write_packages(built, tmp_path / "packages")
    out = tmp_path / "out"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "convert", str(source), "--findings", str(findings),
         "--packages", str(tmp_path / "packages"), "--target-inventory", str(target),
         "--out", str(out)],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    (entry,) = [e for e in ledger["entries"] if e["rule"] == "web-resource-odata-v2"]
    assert entry["status"] == "patched" and entry["manual_review"] == []
    assert "/api/data/v9.0/accounts?$select=new_active" in (
        out / entry["files"]["patched"]
    ).read_text(encoding="utf-8")
