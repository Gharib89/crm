"""`crm inventory convert`: OData v2 calls to the Web API through the adapter (ADR 0034).

One source inventory holds a web resource per case and one target inventory names the Web API
entity sets and attribute types. Everything runs through `assess`, `packages` and the `convert`
CLI once, then each case asserts what landed under `--out`. The adapter's v2 shapes are checked by
running the generated module under Node over fixture v4 payloads.
"""

# pyright: basic

from __future__ import annotations

import json
import re
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
    "dynamic-filter": (
        "var req = new XMLHttpRequest();\n"
        f'req.open("GET", {HEAD} + "{SVC}/AccountSet?$select=Name&$filter=" + build(), false);\n',
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
    # A URL built in a variable (#1280): the follow reaches literals assigned once.
    "follow-base": (
        f'var base = {HEAD} + "{SVC}";\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", base + "/AccountSet?$select=Name", false);\n',
        f'var base = {HEAD} + "{NEW}";\n'
        "var req = new XMLHttpRequest();\n"
        'CrmODataV2.open(req, "GET", base + "/accounts?$select=name", false);\n',
        0,
    ),
    "follow-local-url": (
        "function load() {\n"
        "  var req = new XMLHttpRequest();\n"
        f"  var url = \"{SVC}/AccountSet?$select=Name,Revenue&$filter=Name eq 'Acme'\";\n"
        '  req.open("GET", url, false);\n'
        "}\n",
        "function load() {\n"
        "  var req = new XMLHttpRequest();\n"
        f"  var url = \"{NEW}/accounts?$select=name,revenue&$filter=name eq 'Acme'\";\n"
        '  CrmODataV2.open(req, "GET", url, false);\n'
        "}\n",
        0,
    ),
    "follow-entity-set": (
        f'var base = {HEAD} + "{SVC}";\n'
        'var set = "AccountSet";\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", base + "/" + set + "?$select=Name", false);\n',
        f'var base = {HEAD} + "{NEW}";\n'
        'var set = "accounts";\n'
        "var req = new XMLHttpRequest();\n"
        'CrmODataV2.open(req, "GET", base + "/" + set + "?$select=name", false);\n',
        0,
    ),
    "follow-shared-base": (
        f'var base = {HEAD} + "{SVC}";\n'
        "var a = new XMLHttpRequest(), b = new XMLHttpRequest();\n"
        'a.open("GET", base + "/AccountSet?$select=Name", false);\n'
        'b.open("GET", base + "/AccountSet?$select=Revenue", false);\n',
        f'var base = {HEAD} + "{NEW}";\n'
        "var a = new XMLHttpRequest(), b = new XMLHttpRequest();\n"
        'CrmODataV2.open(a, "GET", base + "/accounts?$select=name", false);\n'
        'CrmODataV2.open(b, "GET", base + "/accounts?$select=revenue", false);\n',
        0,
    ),
    # A literal filter value held in a variable stays as written, as an unbound one would.
    "follow-filter-value-kept": (
        'var name = "Acme";\nvar req = new XMLHttpRequest();\n'
        f'req.open("GET", "{SVC}/AccountSet?$select=Name&$filter=Name eq \'" + name + "\'",'
        " false);\n",
        'var name = "Acme";\nvar req = new XMLHttpRequest();\n'
        f'CrmODataV2.open(req, "GET", "{NEW}/accounts?$select=name&$filter=name eq \'" + name +'
        ' "\'", false);\n',
        0,
    ),
    # The same, one variable deeper: the URL stays followed and only `name` is kept.
    "follow-filter-value-nested": (
        'var name = "Acme";\nvar req = new XMLHttpRequest();\n'
        f'var url = "{SVC}/AccountSet?$select=Name&$filter=Name eq \'" + name + "\'";\n'
        'req.open("GET", url, false);\n',
        'var name = "Acme";\nvar req = new XMLHttpRequest();\n'
        f'var url = "{NEW}/accounts?$select=name&$filter=name eq \'" + name + "\'";\n'
        'CrmODataV2.open(req, "GET", url, false);\n',
        0,
    ),
    "stop-parameter": (
        f'var base = {HEAD} + "{SVC}";\n'
        "function get(set) {\n"
        "  var req = new XMLHttpRequest();\n"
        '  req.open("GET", base + "/" + set + "?$select=Name", false);\n'
        "}\n",
        None,
        1,
    ),
    "stop-returned": (
        f'function url() {{ return {HEAD} + "{SVC}/AccountSet?$select=Name"; }}\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", url(), false);\n',
        None,
        1,
    ),
    "stop-reassigned": (
        f'var url = "{SVC}/AccountSet?$select=Name";\n'
        'url = url + "&$top=1";\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", url, false);\n',
        None,
        1,
    ),
    "stop-augmented": (
        f'var url = "{SVC}/AccountSet?$select=Name";\n'
        'url += "&$top=1";\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", url, false);\n',
        None,
        1,
    ),
    "stop-entity-set-not-literal": (
        f'var base = {HEAD} + "{SVC}";\n'
        "var set = pick();\n"
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", base + "/" + set + "?$select=Name", false);\n',
        None,
        1,
    ),
    "stop-shared-with-refused": (
        f'var base = {HEAD} + "{SVC}";\n'
        "var a = new XMLHttpRequest(), b = new XMLHttpRequest();\n"
        'a.open("GET", base + "/AccountSet?$select=Name", false);\n'
        'b.open("GET", base + "/AccountSet?$select=Name&$expand=Account_Contacts", false);\n',
        None,
        2,
    ),
    "stop-destructured": (
        f'var url = "{SVC}/AccountSet?$select=Name";\n'
        "[url] = other;\n"
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", url, false);\n',
        None,
        1,
    ),
    "stop-block-scoped": (
        f'if (x) {{ let base = "{SVC}"; }}\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", base + "/AccountSet?$select=Name", false);\n',
        None,
        1,
    ),
    "stop-block-scoped-bare": (
        f'{{ let base = "{SVC}"; }}\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", base + "/AccountSet?$select=Name", false);\n',
        None,
        1,
    ),
    "stop-block-scoped-for": (
        f'for (let base = "{SVC}"; ; ) {{ break; }}\n'
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", base + "/AccountSet?$select=Name", false);\n',
        None,
        1,
    ),
    "stop-loop-reuse": (
        f'var url = "{SVC}/AccountSet?$select=Name";\n'
        "for (var url in o) {}\n"
        "var req = new XMLHttpRequest();\n"
        'req.open("GET", url, false);\n',
        None,
        1,
    ),
    "stop-other-function": (
        "var base;\n"
        f'function init() {{ base = "{SVC}"; }}\n'
        "function get() {\n"
        "  var req = new XMLHttpRequest();\n"
        '  req.open("GET", base + "/AccountSet?$select=Name", false);\n'
        "}\n",
        None,
        1,
    ),
}
#: What each follow stop's manual-review reasons must name.
ID = "11111111-1111-1111-1111-111111111111"
BY_KEY = f"{SVC}/AccountSet(guid'{ID}')"
XHR = "var req = new XMLHttpRequest();\n"


def _write_case(header: str, url: str = BY_KEY, method: str = "POST") -> str:
    return f'{XHR}req.open("{method}", "{url}", false);\n{header}req.send(body);\n'


#: OData v2 writes (#1320): the method from X-HTTP-Method, the body left to the adapter.
CASES |= {
    "write-create": (
        _write_case('req.setRequestHeader("Accept", "application/json");\n', f"{SVC}/AccountSet"),
        f'{XHR}CrmODataV2.open(req, "POST", "{NEW}/accounts", false);\n'
        'req.setRequestHeader("Accept", "application/json");\nreq.send(body);\n',
        0,
    ),
    "write-merge": (
        _write_case('req.setRequestHeader("X-HTTP-Method", "MERGE");\n'),
        f'{XHR}CrmODataV2.open(req, "PATCH", "{NEW}/accounts({ID})", false);\n\nreq.send(body);\n',
        0,
    ),
    "write-delete": (
        _write_case("  req.setRequestHeader('X-HTTP-Method', 'DELETE');\n"),
        f'{XHR}CrmODataV2.open(req, "DELETE", "{NEW}/accounts({ID})", false);\n'
        "  \nreq.send(body);\n",
        0,
    ),
    "write-header-variable": (
        _write_case('req.setRequestHeader("X-HTTP-Method", verb);\n'),
        None,
        1,
    ),
    "write-header-unknown": (
        _write_case('req.setRequestHeader("X-HTTP-Method", "PUT");\n'),
        None,
        1,
    ),
    "write-header-twice": (
        _write_case(
            'req.setRequestHeader("X-HTTP-Method", "MERGE");\n'
            'req.setRequestHeader("X-HTTP-Method", "MERGE");\n'
        ),
        None,
        1,
    ),
    "write-header-other-function": (
        _write_case(
            'function verb() { req.setRequestHeader("X-HTTP-Method", "MERGE"); }\nverb();\n'
        ),
        None,
        1,
    ),
    # Each function its own request: a header on another function's `req` is not this one's.
    "write-sibling-functions": (
        "function update(id) {\n"
        f'{XHR}req.open("POST", "{SVC}/AccountSet(guid\'" + id + "\')", false);\n'
        'req.setRequestHeader("X-HTTP-Method", "MERGE");\nreq.send(body);\n}\n'
        "function remove(id) {\n"
        f'{XHR}req.open("POST", "{SVC}/AccountSet(guid\'" + id + "\')", false);\n'
        'req.setRequestHeader("X-HTTP-Method", "DELETE");\nreq.send();\n}\n',
        "function update(id) {\n"
        f'{XHR}CrmODataV2.open(req, "PATCH", "{NEW}/accounts(" + id + ")", false);\n'
        "\nreq.send(body);\n}\n"
        "function remove(id) {\n"
        f'{XHR}CrmODataV2.open(req, "DELETE", "{NEW}/accounts(" + id + ")", false);\n'
        "\nreq.send();\n}\n",
        0,
    ),
    # The adapter compares the method as written: a lowercase literal is emitted upper-case.
    "write-create-lowercase": (
        _write_case("", f"{SVC}/AccountSet", "post"),
        f'{XHR}CrmODataV2.open(req, "POST", "{NEW}/accounts", false);\nreq.send(body);\n',
        0,
    ),
    "get-lowercase": (
        f"{XHR}req.open('get', \"{SVC}/AccountSet?$select=Name\", false);\n",
        f"{XHR}CrmODataV2.open(req, 'GET', \"{NEW}/accounts?$select=name\", false);\n",
        0,
    ),
    "write-merge-no-key": (
        _write_case('req.setRequestHeader("X-HTTP-Method", "MERGE");\n', f"{SVC}/AccountSet"),
        None,
        1,
    ),
    "write-header-in-expression": (
        _write_case('ok && req.setRequestHeader("X-HTTP-Method", "MERGE");\n'),
        None,
        1,
    ),
    "write-create-by-key": (_write_case(""), None, 1),
    "write-query": (_write_case("", f"{SVC}/AccountSet?$select=Name"), None, 1),
    "write-stale-target": (_write_case("", f"{SVC}/LeadSet"), None, 1),
}
WRITE_REASONS = {
    "write-header-variable": "the X-HTTP-Method value is not a literal",
    "write-header-unknown": "the X-HTTP-Method PUT is not MERGE or DELETE",
    "write-header-twice": "X-HTTP-Method is set on the request more than once",
    "write-header-other-function": "X-HTTP-Method is set on the request in another function",
    "write-create-by-key": "a create posts to <Set>",
    "write-merge-no-key": "an update or a delete names <Set>(guid'...')",
    "write-header-in-expression": "the X-HTTP-Method call is not a statement of its own",
    "write-query": "a v2 write URL with a query option is not translated",
    "write-stale-target": "re-extract it",
}

STOPS = {
    "stop-destructured": ["`url` is assigned more than once"],
    "stop-block-scoped": ["`base`"],
    "stop-block-scoped-bare": ["`base`"],
    "stop-block-scoped-for": ["`base`"],
    "stop-loop-reuse": ["`url` is not a plain variable"],
    "stop-other-function": ["`base` is assigned in a function that does not enclose the call"],
    "stop-parameter": ["`set` is a function parameter"],
    "stop-returned": ["returned from a function"],
    "stop-reassigned": ["`url` is assigned more than once"],
    "stop-augmented": ["`url` is assigned more than once"],
    "stop-entity-set-not-literal": ["the entity set name is not a literal", "`set`"],
    "stop-shared-with-refused": ["`base`"],
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


def _entity(
    name: str, set_name: str | None, attrs: dict[str, str], write: dict[str, dict] | None = None
) -> list[inv.Record]:
    """An entity and its attributes; *write* holds each attribute's write fields (#1320)."""
    fields: dict = {"logical_name": name, "object_type_code": 1}
    if set_name:
        fields["entity_set_name"] = set_name
    out = [inv.Record("entity", name, None, None, "system", False, "published", fields, ())]
    for attr, kind in attrs.items():
        f = {"entity": name, "logical_name": attr, "type": kind, "required_level": None}
        f |= (write or {}).get(attr, {})
        key = inv.component_key("attribute", f)
        out.append(inv.Record("attribute", key, None, None, "system", False, "published", f, ()))
    return out


def _lookup(schema: str, to: str, entity: str, attribute: str, nav: str) -> inv.Record:
    """A one-to-many relationship record carrying its navigation property (#1320)."""
    f = {
        "schema_name": schema,
        "type": "one_to_many",
        "referenced_entity": to,
        "referenced_attribute": f"{to}id",
        "referencing_entity": entity,
        "referencing_attribute": attribute,
        "navigation_property": nav,
    }
    return inv.Record("relationship", schema, None, None, "system", False, "published", f, ())


#: The account attributes of the target inventory: type, and schema name and date behaviour.
ACCOUNT = {
    "name": ("String", "Name", None),
    "accountid": ("Uniqueidentifier", "AccountId", None),
    "revenue": ("Money", "Revenue", None),
    "parentcustomerid": ("Customer", "ParentCustomerId", None),
    "primarycontactid": ("Lookup", "PrimaryContactId", None),
    "ownerid": ("Owner", "OwnerId", None),
    "statecode": ("State", "StateCode", None),
    "statuscode": ("Status", "StatusCode", None),
    "industrycode": ("Picklist", "IndustryCode", None),
    "createdon": ("DateTime", "CreatedOn", "UserLocal"),
    "new_opendate": ("DateTime", "new_OpenDate", "DateOnly"),
    "numberofemployees": ("Integer", "NumberOfEmployees", None),
    "donotemail": ("Boolean", "DoNotEMail", None),
    "new_tags": ("Virtual", "new_Tags", None),
}


def _target_records() -> list[inv.Record]:
    write = {
        k: {"schema_name": s, **({"date_behavior": d} if d else {})}
        for k, (_, s, d) in ACCOUNT.items()
    }
    return (
        _entity("account", "accounts", {k: v[0] for k, v in ACCOUNT.items()}, write)
        + _entity("contact", None, {"fullname": "String"})
        # Extracted before #1320: no schema names, so a write to it stays manual review.
        + _entity("lead", "leads", {"subject": "String"})
        + _entity("systemuser", "systemusers", {})
        + _entity("team", "teams", {})
        + [
            _lookup("cust_account", "account", "account", "parentcustomerid", "pc_account"),
            _lookup("cust_lead", "lead", "account", "parentcustomerid", "pc_lead"),
            _lookup("primary_lead", "lead", "account", "primarycontactid", "primarycontactid"),
            _lookup("owner_accounts", "owner", "account", "ownerid", "ownerid"),
        ]
    )


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
    return _convert(tmp_path_factory.mktemp("odata"), "onprem")


@pytest.fixture(scope="module")
def cloud_run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _convert(tmp_path_factory.mktemp("odata-cloud"), "cloud")


def _convert(root: Path, platform: str) -> dict:
    folder = root / "inv"
    folder.mkdir()
    sources = {c: v[0] for c, v in CASES.items()} | {"mixed": MIXED, "comment-only": COMMENT_ONLY}
    records = [_record(_name(c), text, folder) for c, text in sources.items()]
    _write(folder, "crm2011", records, ("web_resource",))
    kinds = ("entity", "attribute", "relationship")
    _write(root / "target", platform, _target_records(), kinds)
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


@pytest.mark.parametrize("case", sorted(STOPS))
def test_a_follow_stop_names_why_on_every_entry(run, case):
    (entry,) = _entries(run["entries"], case)
    for review in entry["manual_review"]:
        for fragment in STOPS[case]:
            assert fragment in review["reason"], review["reason"]


def test_a_url_in_a_local_variable_converts_like_the_inline_url(run):
    def url(case: str) -> str:
        (entry,) = _entries(run["entries"], case)
        return re.findall(r'"(/api/data/[^"]*)"', _patched(run, entry))[0]

    assert url("follow-local-url") == url("get-select-filter")
    assert url("follow-filter-value-nested") == url("follow-filter-value-kept")


@pytest.mark.parametrize("case", ["follow-base", "follow-entity-set", "follow-shared-base"])
def test_a_followed_url_reassesses_clean(run, case):
    (entry,) = _entries(run["entries"], case)
    assert SVC not in _patched(run, entry)


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
                    {
                        "accountid": "a2",
                        "name": None,
                        "revenue": None,
                        "statuscode": None,
                        "createdon": None,
                        "_parentcustomerid_value": None,
                    },
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
    assert second["Name"] is None and second["CreatedOn"] is None
    assert second["Revenue"] == {"Value": None} and second["StatusCode"] == {"Value": None}
    assert second["ParentCustomerId"] == {"Id": None, "LogicalName": None, "Name": None}
    assert one["json"] == {"d": {"Name": "Acme"}} and one["async"] is True
    assert error["json"] == {"error": {"code": "0x1", "message": {"lang": "en-US", "value": "bad"}}}
    assert many["headers"]["OData-Version"] == "4.0"
    assert "include-annotations" in many["headers"]["Prefer"]


@pytest.mark.parametrize("case", sorted(WRITE_REASONS))
def test_a_write_convert_cannot_prove_names_why(run, case):
    (entry,) = _entries(run["entries"], case)
    (review,) = entry["manual_review"]
    assert WRITE_REASONS[case] in review["reason"], review["reason"]


def test_a_converted_write_puts_the_written_entity_in_the_adapter(run):
    (entry,) = _entries(run["entries"], "write-merge")
    text = (run["out"] / entry["files"]["adapter"]).read_text(encoding="utf-8")
    table = json.loads(text[text.index("var TABLE = ") + 12 : text.index(";\n  var SET")])
    write = table["accounts"]["write"]
    assert set(write) == {s for _, s, _ in ACCOUNT.values()}
    assert write["ParentCustomerId"] == [
        "parentcustomerid",
        "customer",
        {"account": ["pc_account", "accounts"], "lead": ["pc_lead", "leads"]},
    ]
    assert write["OwnerId"][2] == {
        "systemuser": ["ownerid", "systemusers"],
        "team": ["ownerid", "teams"],
    }
    assert write["new_OpenDate"][1] == "dateonly" and write["CreatedOn"][1] == "date"
    assert write["new_Tags"][1] == "none"


WRITE_HARNESS = r"""
const fs = require("fs");
class FakeXHR {
  constructor() { this.readyState = 0; this.status = 0; this.headers = {}; this._text = "";
    this.sent = []; }
  open(method, url, async) { this.method = method; this.url = url; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  send(body) { this.sent.push(body === undefined ? null : body); }
  get responseText() { return this._text; }
}
globalThis.XMLHttpRequest = FakeXHR;
eval(fs.readFileSync(process.argv[2], "utf8"));
const cases = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const out = cases.map((c) => {
  const x = new XMLHttpRequest();
  let error = null;
  try {
    CrmODataV2.open(x, c.method, c.url, false);
    // `caught`: the script swallows a header throw and sends anyway.
    (c.headers || []).forEach((h) => {
      try { x.setRequestHeader(h[0], h[1]); } catch (e) { if (!c.caught) { throw e; } }
    });
    x.send(c.body === undefined ? undefined : JSON.stringify(c.body));
  } catch (e) { error = e.message; }
  let json = null;
  if (c.response) { x.readyState = 4; x._text = JSON.stringify(c.response);
    json = JSON.parse(x.responseText); }
  return { error, json, method: x.method, headers: x.headers,
    sent: x.sent.map((s) => (s === null ? null : JSON.parse(s))) };
});
console.log(JSON.stringify(out));
"""

G = [f"2222222{i}-2222-2222-2222-222222222222" for i in range(5)]
ONE = f"{NEW}/accounts({ID})"
#: name: (method, body, the body the native send got, or the fragment of the error thrown)
WRITES: dict[str, tuple[str, object, object]] = {
    "every mapping": (
        "POST",
        {
            "__metadata": {"type": "Microsoft.Crm.Sdk.Data.Services.Account"},
            "Name": "Acme",
            "ParentCustomerId": {"Id": G[0], "LogicalName": "account", "Name": "Parent"},
            "PrimaryContactId": {"Id": "{" + G[1] + "}", "LogicalName": "lead"},
            "OwnerId": {"Id": G[2], "LogicalName": "team"},
            "IndustryCode": {"Value": 3},
            "StateCode": {"Value": 0},
            "StatusCode": {"Value": 1},
            "Revenue": {"Value": "1500.50"},
            "CreatedOn": "/Date(1577836800000)/",
            # 02:00 UTC on 1 January is 31 December in New York, the harness's zone.
            "new_OpenDate": "/Date(1577844000000)/",
            "NumberOfEmployees": 5,
            "DoNotEMail": True,
        },
        {
            "name": "Acme",
            "pc_account@odata.bind": f"/accounts({G[0]})",
            "primarycontactid@odata.bind": f"/leads({G[1]})",
            "ownerid@odata.bind": f"/teams({G[2]})",
            "industrycode": 3,
            "statecode": 0,
            "statuscode": 1,
            "revenue": 1500.5,
            "createdon": "2020-01-01T00:00:00.000Z",
            "new_opendate": "2019-12-31",
            "numberofemployees": 5,
            "donotemail": True,
        },
    ),
    "customer per target entity": (
        "PATCH",
        {
            "ParentCustomerId": {"Id": G[3], "LogicalName": "lead"},
            "OwnerId": {"Id": G[4], "LogicalName": "systemuser"},
            "new_OpenDate": "2020-02-03",
        },
        {
            "pc_lead@odata.bind": f"/leads({G[3]})",
            "ownerid@odata.bind": f"/systemusers({G[4]})",
            "new_opendate": "2020-02-03",
        },
    ),
    "nulls": (
        "PATCH",
        {
            "Name": None,
            "PrimaryContactId": None,
            "ParentCustomerId": None,
            "IndustryCode": None,
            "Revenue": {"Value": None},
            "CreatedOn": None,
            "StatusCode": {"Value": None},
        },
        {
            "name": None,
            "primarycontactid@odata.bind": None,
            "pc_account@odata.bind": None,
            "industrycode": None,
            "revenue": None,
            "createdon": None,
            "statuscode": None,
        },
    ),
    "nulls without a lookup": (
        "PATCH",
        {"Name": None, "Revenue": {"Value": None}},
        {"name": None, "revenue": None},
    ),
    "unknown attribute": ("PATCH", {"Bogus": 1}, "account.Bogus is not an attribute"),
    "option set as a bare number": ("PATCH", {"IndustryCode": 3}, "{Value: ...}"),
    "state as a bare number": ("PATCH", {"StateCode": 1}, "{Value: ...}"),
    "money as a bare number": ("PATCH", {"Revenue": 5}, "{Value: ...}"),
    "option set value not an integer": ("PATCH", {"StatusCode": {"Value": "1"}}, "integer"),
    "lookup without Id": ("PATCH", {"PrimaryContactId": {"LogicalName": "lead"}}, "{Id, Logical"),
    "lookup at an entity it cannot point at": (
        "PATCH",
        {"PrimaryContactId": {"Id": G[0], "LogicalName": "contact"}},
        "cannot point at contact",
    ),
    "date in neither form": ("PATCH", {"CreatedOn": "yesterday"}, "neither /Date(n)/"),
    "null owner": ("PATCH", {"OwnerId": None}, "no single navigation property clears"),
    "multi-select option set": ("PATCH", {"new_Tags": "1,2"}, "a type a write does not"),
    "string of the wrong type": ("PATCH", {"Name": 5}, "is not a string"),
    "number of the wrong shape": ("PATCH", {"NumberOfEmployees": "x"}, "is not a number"),
    "boolean as a string": ("PATCH", {"DoNotEMail": "true"}, "is not a boolean"),
    "money past a double's precision": (
        "PATCH",
        {"Revenue": {"Value": "1234567890123456.78"}},
        "more than 15 significant digits",
    ),
    "body not a JSON object": ("POST", [1], "not a JSON object"),
}


#: On an on-premises target a null lookup throws (`Target.clears_lookups`).
ONPREM_WRITES = {"nulls": "on-premises v9 clears no lookup in a write body"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.parametrize("platform", ["cloud", "onprem"])
def test_the_adapter_translates_a_v2_write_body_or_throws_before_send(request, platform, tmp_path):
    run = request.getfixturevalue("cloud_run" if platform == "cloud" else "run")
    writes = {
        name: (m, body, ONPREM_WRITES.get(name, want) if platform == "onprem" else want)
        for name, (m, body, want) in WRITES.items()
    }
    (entry,) = _entries(run["entries"], "write-merge")
    adapter = run["out"] / entry["files"]["adapter"]
    assert NODE is not None
    (tmp_path / "harness.js").write_text(WRITE_HARNESS, encoding="utf-8")
    ann = "@Microsoft.Dynamics.CRM.lookuplogicalname"
    cases = [
        {"method": m, "url": f"{NEW}/accounts" if m == "POST" else ONE, "body": body}
        for m, body, _ in writes.values()
    ]
    cases += [
        # A create's representation comes back as v2 `{ d: ... }`, spelled by schema name.
        {
            "method": "POST",
            "url": f"{NEW}/accounts",
            "body": {"Name": "Acme"},
            "response": {
                "accountid": G[0],
                "name": "Acme",
                "_ownerid_value": G[2],
                f"_ownerid_value{ann}": "team",
                "new_opendate": "2020-02-03",
            },
        },
        # The header convert could not see turns no update into a create.
        {
            "method": "PATCH",
            "url": ONE,
            "body": {"Name": "x"},
            "headers": [["X-HTTP-Method", "MERGE"]],
        },
        # The same header's throw swallowed: the send throws too, and nothing goes out.
        {
            "method": "PATCH",
            "url": ONE,
            "body": {"Name": "x"},
            "headers": [["X-HTTP-Method", "MERGE"]],
            "caught": True,
        },
        {"method": "DELETE", "url": ONE},
    ]
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    res = subprocess.run(
        [NODE, str(tmp_path / "harness.js"), str(adapter), str(tmp_path / "cases.json")],
        capture_output=True,
        text=True,
        check=False,
        env={"TZ": "America/New_York", "PATH": "/usr/bin:/bin"},
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    for (name, (method, _, want)), got in zip(writes.items(), out, strict=False):
        if isinstance(want, str):
            assert got["sent"] == [], name
            assert want in (got["error"] or ""), (name, got["error"])
        else:
            assert got["error"] is None, (name, got["error"])
            assert got["sent"] == [want], name
        assert got["method"] == method
    created, header, caught, deleted = out[len(writes) :]
    assert "return=representation" in created["headers"]["Prefer"]
    assert "include-annotations" in created["headers"]["Prefer"]
    d = created["json"]["d"]
    assert d["AccountId"] == G[0] and d["Name"] == "Acme"
    assert d["OwnerId"] == {"Id": G[2], "LogicalName": "team"}
    assert header["sent"] == [] and "X-HTTP-Method" in header["error"]
    assert caught["sent"] == [] and "X-HTTP-Method" in caught["error"]
    assert deleted["sent"] == [None] and deleted["error"] is None
    assert "If-Match" not in deleted["headers"]
    patch = out[list(writes).index("nulls without a lookup")]
    assert patch["headers"]["If-Match"] == "*"
    assert "return=representation" not in patch["headers"]["Prefer"]
