"""`crm inventory convert`: OData v2 calls to the Web API through the adapter (ADR 0034).

One source inventory holds a web resource per case and one target inventory names the Web API
entity sets and attribute types. Everything runs through `assess`, `packages` and the `convert`
CLI once, then each case asserts what landed under `--out`. The adapter's v2 shapes are checked by
running the generated module under Node over fixture v4 payloads.
"""

# pyright: basic

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_packages import build_packages, write_packages
from crm.tests.test_inventory_convert import ORG, _record

SVC = "/XRMServices/2011/OrganizationData.svc"
HEAD = "Xrm.Utility.getGlobalContext().getClientUrl()"
NEW = "/api/data/v9.0"

#: name: (before, after or None when the code must stay untouched, manual-review count)
CASES: dict[str, tuple[str, str | None, int]] = {
    "get-select-filter": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=Name,Revenue&$filter=Name eq \'Acme\'",'
        f" false);\n"
        "req.send();\n",
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", "{NEW}/accounts?$select=name,revenue&$filter=name eq'
        f" 'Acme'\", false);\n"
        "req.send();\n",
        0,
    ),
    "every-type": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=Name,Revenue,StatusCode,'
        'ParentCustomerId,CreatedOn", false);\n',
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", "{NEW}/accounts?$select=name,revenue,statuscode,'
        '_parentcustomerid_value,createdon", false);\n',
        0,
    ),
    "dynamic-value": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", {HEAD} + "{SVC}/AccountSet?$select=Name&$filter=Name eq \'" + name +'
        f' "\'", false);\n',
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", {HEAD} + "{NEW}/accounts?$select=name&$filter=name eq \'" +'
        f' name + "\'", false);\n',
        0,
    ),
    "by-key": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", {HEAD} + "{SVC}/AccountSet(guid\'" + id + "\')?$select=Name,Revenue",'
        f" false);\n",
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", {HEAD} + "{NEW}/accounts(" + id + ")?$select=name,revenue",'
        f" false);\n",
        0,
    ),
    "query-options": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=Name,ParentCustomerId&'
        "$filter=substringof('Acme', Name) and StatusCode/Value eq 1 and "
        "ParentCustomerId/Id eq guid'11111111-1111-1111-1111-111111111111' and "
        "CreatedOn gt datetime'2020-01-01T00:00:00'&$orderby=Name desc&$top=5&"
        '$inlinecount=allpages", false);\n',
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", "{NEW}/accounts?$select=name,_parentcustomerid_value&'
        "$filter=contains(name,'Acme') and statuscode eq 1 and "
        "_parentcustomerid_value eq 11111111-1111-1111-1111-111111111111 and "
        "createdon gt 2020-01-01T00:00:00Z&$orderby=name desc&$top=5&"
        '$count=true", false);\n',
        0,
    ),
    "grouped-filter": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=Name&$filter=StatusCode/Value eq 1 and '
        "(Name eq 'a' or not (Name eq 'b'))\", false);\n",
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", "{NEW}/accounts?$select=name&$filter=statuscode eq 1 and '
        "(name eq 'a' or not (name eq 'b'))\", false);\n",
        0,
    ),
    "split-literals": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", {HEAD} + "{SVC}/AccountSet?$select=Name" +\n'
        '    "&$top=1", false);\n',
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", {HEAD} + "{NEW}/accounts?$select=name&$top=1"\n'
        ", false);\n",
        0,
    ),
    "async": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=Name", true);\n'
        "req.onreadystatechange = function () { if (req.readyState === 4) {"
        " use(req.responseText); } };\n"
        "req.send();\n",
        "var req = new XMLHttpRequest();\n"
        f'CrmODataV2.open(req, "GET", "{NEW}/accounts?$select=name", true);\n'
        "req.onreadystatechange = function () { if (req.readyState === 4) {"
        " use(req.responseText); } };\n"
        "req.send();\n",
        0,
    ),
    "unknown-entity-set": (
        f'req.open("GET", "{SVC}/FooSet?$select=Name", false);\n',
        None,
        1,
    ),
    "no-entity-set-name": (
        f'req.open("GET", "{SVC}/ContactSet?$select=FullName", false);\n',
        None,
        1,
    ),
    "unknown-attribute": (
        f'req.open("GET", "{SVC}/AccountSet?$select=Name,Bogus", false);\n',
        None,
        1,
    ),
    "no-select": (
        f'req.open("GET", "{SVC}/AccountSet?$filter=Name eq \'a\'", false);\n',
        None,
        1,
    ),
    "write": (
        f'req.open("POST", "{SVC}/AccountSet", false);\n',
        None,
        1,
    ),
    "expand": (
        f'req.open("GET", "{SVC}/AccountSet?$select=Name&$expand=Account_Contacts", false);\n',
        None,
        1,
    ),
    "unsupported-filter-function": (
        f'req.open("GET", "{SVC}/AccountSet?$select=Name&$filter=tolower(Name) eq \'a\'",'
        f" false);\n",
        None,
        1,
    ),
    "foreign-receiver": (
        f'var req = customTransport;\nreq.open("GET", "{SVC}/AccountSet?$select=Name", false);\n',
        None,
        1,
    ),
    "reassigned-receiver": (
        "var req = new XMLHttpRequest();\nreq = other();\n"
        f'req.open("GET", "{SVC}/AccountSet?$select=Name", false);\n',
        None,
        1,
    ),
    "parameter-receiver": (
        f'function f(req) {{ req.open("GET", "{SVC}/AccountSet?$select=Name", false); }}\n',
        None,
        1,
    ),
    "other-function-receiver": (
        "function a() { var req = new XMLHttpRequest(); }\n"
        f'function b() {{ req.open("GET", "{SVC}/AccountSet?$select=Name", false); }}\n',
        None,
        1,
    ),
    "skip": (
        f'req.open("GET", "{SVC}/AccountSet?$select=Name&$skip=10", false);\n',
        None,
        1,
    ),
    "ajax-shape": (
        f'$.ajax({{ url: {HEAD} + "{SVC}/AccountSet?$select=Name", async: false }});\n',
        None,
        1,
    ),
}
MIXED = (
    "var a = new XMLHttpRequest(), b = new XMLHttpRequest();\n"
    f'a.open("GET", "{SVC}/AccountSet?$select=Name", false);\n'
    f'b.open("GET", "{SVC}/FooSet?$select=Name", false);\n'
)
MIXED_AFTER = (
    "var a = new XMLHttpRequest(), b = new XMLHttpRequest();\n"
    f'CrmODataV2.open(a, "GET", "{NEW}/accounts?$select=name", false);\n'
    f'b.open("GET", "{SVC}/FooSet?$select=Name", false);\n'
)
COMMENT_ONLY = f"// {SVC}/AccountSet?$select=Name\nvar x = 1;\n"


def _name(case: str) -> str:
    return f"new_/scripts/{case}.js"


def _entity(name: str, set_name: str | None, attrs: dict[str, str]) -> list[inv.Record]:
    fields: dict = {"logical_name": name, "object_type_code": 1}
    if set_name:
        fields["entity_set_name"] = set_name
    out = [inv.Record("entity", name, None, None, "system", False, "published", fields, ())]
    for attr, kind in attrs.items():
        f = {"entity": name, "logical_name": attr, "type": kind, "required_level": None}
        key = inv.component_key("attribute", f)
        out.append(inv.Record("attribute", key, None, None, "system", False, "published", f, ()))
    return out


def _write(folder: Path, platform: str, records: list[inv.Record], kinds: tuple[str, ...]) -> None:
    folder.mkdir(exist_ok=True)
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source(platform, ORG, None, "9.2.0.0" if platform != "crm2011" else "5.0.9690"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.98.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={
            k: inv.KindCoverage("complete", 1, sum(r.kind == k for r in records), False, ())
            for k in kinds
        },
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("odata")
    folder = root / "inv"
    folder.mkdir()
    sources = {c: v[0] for c, v in CASES.items()} | {"mixed": MIXED, "comment-only": COMMENT_ONLY}
    records = [_record(_name(c), text, folder) for c, text in sources.items()]
    _write(folder, "crm2011", records, ("web_resource",))
    target_records = _entity(
        "account",
        "accounts",
        {
            "name": "String",
            "accountid": "Uniqueidentifier",
            "revenue": "Money",
            "parentcustomerid": "Customer",
            "statuscode": "Status",
            "createdon": "DateTime",
        },
    ) + _entity("contact", None, {"fullname": "String"})
    _write(root / "target", "onprem", target_records, ("entity", "attribute"))
    findings_path = root / "findings.json"
    findings = assess.assess(folder, "online")
    findings_path.write_text(assess.dump_findings(findings), encoding="utf-8")
    packages = root / "packages"
    repo = root / "repo"
    repo.mkdir()
    from crm.core.inventory_source_map import map_source

    built = build_packages(
        folder, map_source(folder, repo), None, assess.read_findings(findings_path)
    )
    write_packages(built, packages)
    base = ["--json", "inventory", "convert", str(folder), "--findings", str(findings_path)]
    base += ["--packages", str(packages)]
    out = root / "out"
    res = CliRunner().invoke(
        cli, [*base, "--target-inventory", str(root / "target"), "--out", str(out)]
    )
    assert res.exit_code == 0, res.output
    bare = root / "bare"
    res = CliRunner().invoke(cli, [*base, "--out", str(bare)])
    assert res.exit_code == 0, res.output
    return {
        "out": out,
        "bare": bare,
        "entries": json.loads((out / "ledger.json").read_text(encoding="utf-8")),
        "bare_entries": json.loads((bare / "ledger.json").read_text(encoding="utf-8")),
    }


def _entries(ledger: dict, case: str) -> list[dict]:
    return [
        e
        for e in ledger["entries"]
        if e["key"] == _name(case) and e["rule"] == "web-resource-odata-v2"
    ]


def _patched(run: dict, entry: dict) -> str:
    return (run["out"] / entry["files"]["patched"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("case", sorted(c for c, v in CASES.items() if v[1] is not None))
def test_a_supported_call_converts_with_names_from_the_target_inventory(run, case):
    _, after, _ = CASES[case]
    (entry,) = _entries(run["entries"], case)
    assert entry["status"] == "patched" and entry["manual_review"] == []
    assert _patched(run, entry) == after
    assert entry["files"]["adapter"].endswith("crm-odata-v2-adapter.js")


@pytest.mark.parametrize("case", sorted(c for c, v in CASES.items() if v[1] is None))
def test_a_call_that_cannot_convert_is_manual_review_and_untouched(run, case):
    _, _, count = CASES[case]
    (entry,) = _entries(run["entries"], case)
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert len(entry["manual_review"]) == count
    assert all(m["line"] is not None and m["reason"] for m in entry["manual_review"])


def test_the_unresolved_entity_set_names_the_entity(run):
    (entry,) = _entries(run["entries"], "unknown-entity-set")
    assert "entity Foo is not in the target inventory" in entry["manual_review"][0]["reason"]
    (entry,) = _entries(run["entries"], "no-entity-set-name")
    assert "no entity set name for contact" in entry["manual_review"][0]["reason"]


def test_a_file_converts_the_calls_it_can_and_lists_the_others(run):
    (entry,) = _entries(run["entries"], "mixed")
    assert entry["status"] == "patched" and entry["edits"] == 2
    assert _patched(run, entry) == MIXED_AFTER
    assert len(entry["manual_review"]) == 1 and entry["manual_review"][0]["line"] == 3


def test_an_endpoint_in_a_comment_is_not_a_site(run):
    assert _entries(run["entries"], "comment-only") == []


def test_without_a_target_inventory_every_call_is_manual_review(run):
    for case in ("get-select-filter", "async"):
        (entry,) = _entries(run["bare_entries"], case)
        assert entry["status"] == "manual-review"
        assert "--target-inventory" in entry["manual_review"][0]["reason"]
    assert run["bare_entries"]["adapters"] == {}


def test_the_ledger_lists_one_adapter_per_package_with_its_hash(run):
    (entry,) = _entries(run["entries"], "get-select-filter")
    adapter = run["entries"]["adapters"][entry["package"]]
    assert entry["files"]["adapter"] == adapter["file"]
    text = (run["out"] / adapter["file"]).read_bytes()
    import hashlib

    assert adapter["sha256"] == hashlib.sha256(text).hexdigest()
    assert adapter["entity_sets"] == ["accounts"]


def test_the_async_call_keeps_its_third_argument(run):
    (entry,) = _entries(run["entries"], "async")
    assert ", true);" in _patched(run, entry)


NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
class FakeXHR {
  constructor() { this.readyState = 0; this.status = 0; this.headers = {}; this._text = ""; }
  open(method, url, async) { this.method = method; this.url = url; this.async = async; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  get responseText() { return this._text; }
}
globalThis.XMLHttpRequest = FakeXHR;
eval(fs.readFileSync(process.argv[2], "utf8"));
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = cases.map((c) => {
  const x = new XMLHttpRequest();
  CrmODataV2.open(x, "GET", c.url, c.async);
  x.readyState = 4; x.status = c.status || 200; x._text = JSON.stringify(c.body);
  return { json: JSON.parse(x.responseText), async: x.async, headers: x.headers };
});
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_adapter_returns_v2_shapes_for_v4_payloads(run, tmp_path):
    (entry,) = _entries(run["entries"], "every-type")
    adapter = run["out"] / entry["files"]["adapter"]
    assert NODE is not None
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    ann = "@OData.Community.Display.V1.FormattedValue"
    lookup = "@Microsoft.Dynamics.CRM.lookuplogicalname"
    cases = [
        {
            "url": f"{NEW}/accounts?$select=name&$count=true",
            "async": False,
            "body": {
                "@odata.context": "x",
                "@odata.count": 2,
                "value": [
                    {
                        "accountid": "a1",
                        "name": "Acme",
                        "revenue": 1500.5,
                        "statuscode": 1,
                        "_parentcustomerid_value": "p1",
                        f"_parentcustomerid_value{lookup}": "account",
                        f"_parentcustomerid_value{ann}": "Parent",
                        "createdon": "2020-01-01T00:00:00Z",
                        f"revenue{ann}": "$1,500.50",
                    },
                    {"accountid": "a2", "name": None, "_parentcustomerid_value": None},
                ],
            },
        },
        {"url": f"{NEW}/accounts(a1)?$select=name", "async": True, "body": {"name": "Acme"}},
        {
            "url": f"{NEW}/accounts?$select=name",
            "async": False,
            "status": 400,
            "body": {"error": {"code": "0x1", "message": "bad"}},
        },
    ]
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    res = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(adapter), str(tmp_path / "cases.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    many, one, error = json.loads(res.stdout)
    d = many["json"]["d"]
    assert d["__count"] == "2"
    first, second = d["results"]
    assert first["Name"] == "Acme" and first["accountid"] == "a1"
    assert first["Revenue"] == {"Value": "1500.5"}
    assert first["StatusCode"] == {"Value": 1}
    assert first["ParentCustomerId"] == {"Id": "p1", "LogicalName": "account", "Name": "Parent"}
    assert first["CreatedOn"] == "/Date(1577836800000)/"
    assert second["Name"] is None and second["ParentCustomerId"] is None
    assert one["json"] == {"d": {"Name": "Acme"}} and one["async"] is True
    assert error["json"] == {"error": {"code": "0x1", "message": {"lang": "en-US", "value": "bad"}}}
    assert many["headers"]["OData-Version"] == "4.0"
    assert "include-annotations" in many["headers"]["Prefer"]
