"""The v5 inventory kinds (#1340): security roles, field security profiles, duplicate rules,
email templates and connection roles, through extract, diff, assess and packages, offline.

Runs over the synthetic CRM 2011 org of test_legacy_inventory.py, to which it adds the
kinds' rows. The reads are shared by every source (`_READS`), so one fake org covers both;
the Web API's entity sets and cloud-only columns are checked against `WebApiSource`.
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
from crm.tests.test_legacy_inventory import (
    _ORG,
    FakeClient,
    _extract,
    _id,
    _keys,
    _manifest,
    _ref,
    reads,  # noqa: F401  (a fixture, used through pytestmark)
)
from crm.utils.safe_xml import fromstring

pytestmark = pytest.mark.usefixtures("reads")

NEW_KINDS = (
    "security_role",
    "field_security_profile",
    "duplicate_rule",
    "email_template",
    "connection_role",
)
_BODY = (
    '<?xml version="1.0" ?><xsl:stylesheet version="1.0">'
    '<xsl:template match="/data"/></xsl:stylesheet>'
)


def _custom(n: int, **row: Any) -> dict[str, Any]:
    return {"id": _id(n), "ismanaged": False, "solutionid": _ref("solution", 2), **row}


def _role(n: int, name: str, **kw: Any) -> dict[str, Any]:
    return _custom(n, name=name, businessunitid=_ref("businessunit", 50, "Contoso"), **kw)


def _privilege(n: int, role: int, name: str, depth: int) -> dict[str, Any]:
    # An intersect row names its role by a bare id, on SOAP and Web API alike (measured).
    return {"id": _id(n), "roleid": _id(role), "privilegeid": _id(n + 5000),
            "privilegedepthmask": depth, "privilege.name": name}  # fmt: skip


def _rows(**extra: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {
        "role": [_role(400, "Sales Manager"), _role(401, "Auditor")],
        "roleprivileges": [
            _privilege(410, 400, "prvReadAccount", 8),
            _privilege(411, 400, "prvWriteAccount", 2),
            _privilege(412, 401, "prvReadAccount", 1),
        ],
        "fieldsecurityprofile": [_custom(420, name="Salary readers")],
        "fieldpermission": [
            {
                "id": _id(421),
                "fieldsecurityprofileid": _ref("fieldsecurityprofile", 420),
                "entityname": "contact",
                "attributelogicalname": "new_salary",
                "canread": 4,
                "cancreate": 0,
                "canupdate": 0,
            },
        ],  # fmt: skip
        "duplicaterule": [
            {
                "id": _id(430),
                "name": "Same email",
                "baseentityname": "contact",
                "matchingentityname": "contact",
                "statecode": 1,
                "statuscode": 2,
                "iscasesensitive": False,
                "excludeinactiverecords": True,
            },
        ],  # fmt: skip
        "duplicaterulecondition": [
            {
                "id": _id(431),
                "regardingobjectid": _ref("duplicaterule", 430),
                "baseattributename": "emailaddress1",
                "matchingattributename": "emailaddress1",
                "operatorcode": 0,
                "operatorparam": None,
                "ignoreblankvalues": True,
            },
        ],  # fmt: skip
        "template": [
            _custom(
                440,
                title="Welcome",
                templatetypecode="contact",
                subject="<xsl>Hi</xsl>",
                languagecode=1033,
                ispersonal=False,
                body=_BODY,
            ),
        ],  # fmt: skip
        "connectionrole": [_custom(450, name="Mentor", category=1, statecode=0)],
    }
    rows.update(extra)
    return rows


def _org(**extra: list[dict[str, Any]]) -> dict[str, Any]:
    org = copy.deepcopy(_ORG)
    for entity, page in _rows(**extra).items():
        org[entity] = [page]
    return org


def _records(out: Path, kind: str) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in _manifest(out)["records"] if r["kind"] == kind}


class _Spy(FakeClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.fetches: list[str] = []

    def pages(self, fetch_xml, **kw):
        self.fetches.append(fetch_xml)
        return super().pages(fetch_xml, **kw)


def test_each_new_kind_is_recorded_with_its_key_fields_and_complete_coverage(tmp_path):
    summary = _extract(FakeClient(org=_org()), tmp_path / "inv")
    assert summary.complete
    data = _manifest(tmp_path / "inv")
    assert data["schema_version"] == 5
    for kind in NEW_KINDS:
        assert data["coverage"][kind]["status"] == "complete", kind

    roles = _records(tmp_path / "inv", "security_role")
    assert roles["Sales Manager"]["fields"] == {
        "name": "Sales Manager",
        "privileges": {"prvReadAccount": 8, "prvWriteAccount": 2},
    }
    assert roles["Auditor"]["fields"]["privileges"] == {"prvReadAccount": 1}
    assert (roles["Auditor"]["id"], roles["Auditor"]["origin"]) == (_id(401), "custom")
    assert data["coverage"]["security_role"]["records"] == 2

    (profile,) = _records(tmp_path / "inv", "field_security_profile").values()
    assert profile["key"] == "Salary readers"
    assert profile["fields"]["permissions"] == {
        "contact.new_salary": {"read": 4, "create": 0, "update": 0}
    }

    (rule,) = _records(tmp_path / "inv", "duplicate_rule").values()
    assert rule["key"] == inventory.component_key(
        "duplicate_rule", {"base_entity": "contact", "name": "Same email"}
    )
    assert rule["fields"] == {
        "base_entity": "contact", "name": "Same email", "matching_entity": "contact",
        "state": 1, "status": 2, "case_sensitive": False, "exclude_inactive": True,
        "conditions": [
            {"base_attribute": "emailaddress1", "matching_attribute": "emailaddress1",
             "operator": 0, "operator_param": None, "ignore_blank": True},
        ],
    }  # fmt: skip
    # CRM 2011 has no ismanaged on a duplicate rule: kept, with nothing to call it stock by.
    assert (rule["managed"], rule["origin"]) == (None, "unknown")

    (template,) = _records(tmp_path / "inv", "email_template").values()
    assert template["key"] == inventory.component_key(
        "email_template", {"template_type": "contact", "title": "Welcome"}
    )
    assert template["fields"] == {
        "template_type": "contact", "title": "Welcome", "subject": "<xsl>Hi</xsl>",
        "language": 1033, "personal": False,
    }  # fmt: skip
    (artifact,) = template["artifacts"]
    assert (tmp_path / "inv" / artifact["path"]).read_text(encoding="utf-8") == _BODY

    (connection,) = _records(tmp_path / "inv", "connection_role").values()
    assert connection["fields"] == {"name": "Mentor", "category": 1, "state": 0}


def test_only_root_business_unit_roles_and_their_privileges_are_read(tmp_path):
    client = _Spy(org=_org())
    _extract(client, tmp_path / "inv")
    for entity in ("role", "roleprivileges"):
        (fetch,) = [f for f in client.fetches if f'entity name="{entity}"' in f]
        bu = fromstring(fetch).find(".//link-entity[@name='businessunit']")
        assert bu is not None, entity
        condition = bu.find("filter/condition")
        assert condition is not None
        assert (condition.get("attribute"), condition.get("operator")) == (
            "parentbusinessunitid",
            "null",
        )


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        ("security_role", {"role": [_role(400, "Twin"), _role(401, "Twin")]}),
        ("field_security_profile",
         {"fieldsecurityprofile": [_custom(420, name="Twin"), _custom(421, name="Twin")]}),
        ("connection_role",
         {"connectionrole": [_custom(450, name="Twin"), _custom(451, name="Twin")]}),
        ("email_template",
         {"template": [_custom(440, title="Twin", templatetypecode="contact"),
                       _custom(441, title="Twin", templatetypecode="contact")]}),
        ("duplicate_rule",
         {"duplicaterule": [{"id": _id(430), "name": "Twin", "baseentityname": "contact"},
                            {"id": _id(431), "name": "Twin", "baseentityname": "contact"}]}),
    ],
)  # fmt: skip
def test_two_records_sharing_a_key_are_ambiguous_never_matched(tmp_path, kind, extra):
    _extract(FakeClient(org=_org(**extra)), tmp_path / "a")
    _extract(FakeClient(org=_org(**extra)), tmp_path / "b")
    inv = inventory.read_inventory(tmp_path / "a")
    (pair,) = [v for (k, _), v in inv.ambiguous().items() if k == kind]
    assert len(pair) == 2
    diff = inventory_diff.diff_inventories(
        *inventory_diff.read_pair(tmp_path / "a", tmp_path / "b")
    )
    (entry,) = [e for e in diff.entries if e.kind == kind]
    assert entry.verdict == "ambiguous"


def test_a_changed_privilege_depth_diffs_as_changed(tmp_path):
    _extract(FakeClient(org=_org()), tmp_path / "a")
    deeper = _rows()["roleprivileges"]
    deeper[1] = _privilege(411, 400, "prvWriteAccount", 4)
    _extract(FakeClient(org=_org(roleprivileges=deeper)), tmp_path / "b")
    diff = inventory_diff.diff_inventories(
        *inventory_diff.read_pair(tmp_path / "a", tmp_path / "b")
    )
    roles = {e.key: e for e in diff.entries if e.kind == "security_role"}
    assert roles["Auditor"].verdict == "unchanged"
    changed = roles["Sales Manager"]
    assert changed.verdict == "changed"
    (field,) = changed.fields
    assert field.name == "fields.privileges"
    assert (field.a["prvWriteAccount"], field.b["prvWriteAccount"]) == (2, 4)


def test_the_default_scope_leaves_out_stock_rows_and_their_template_bodies(tmp_path):
    stock = {"ismanaged": True, "solutionid": _ref("solution", 1)}
    client = _Spy(
        org=_org(
            role=[_role(400, "Sales Manager"), _role(402, "System Administrator", **stock)],
            template=[_custom(440, title="Welcome", templatetypecode="contact", body=_BODY),
                      _custom(442, title="Stock", templatetypecode="contact", body=_BODY,
                              **stock)],
        )
    )  # fmt: skip
    _extract(client, tmp_path / "inv", include_system=False)
    assert sorted(_records(tmp_path / "inv", "security_role")) == ["Sales Manager"]
    templates = _records(tmp_path / "inv", "email_template")
    assert [t["fields"]["title"] for t in templates.values()] == ["Welcome"]
    by_id = [f for f in client.fetches if 'entity name="template"' in f and "<value>" in f]
    assert by_id and all(_id(440) in f and _id(442) not in f for f in by_id)


def test_a_solution_run_keeps_only_the_member_records_of_each_new_kind(tmp_path):
    org = _org(
        role=[_role(400, "Sales Manager"), _role(401, "Auditor")],
        connectionrole=[_custom(450, name="Mentor"), _custom(451, name="Other")],
    )
    # role 20, field security profile 70, duplicate rule 44, email template 36, connection role 63
    for code, n in ((20, 400), (70, 420), (44, 430), (36, 440), (63, 450)):
        org["solutioncomponent"][0].append(
            {"id": _id(900 + n), "solutionid": _ref("solution", 2), "componenttype": code,
             "objectid": _id(n)}
        )  # fmt: skip
    _extract(FakeClient(org=org), tmp_path / "inv", solutions=("contoso",))
    data = _manifest(tmp_path / "inv")
    assert _keys(data, "security_role") == ["Sales Manager"]
    assert _keys(data, "connection_role") == ["Mentor"]
    for kind in NEW_KINDS:
        assert len(_keys(data, kind)) == 1, kind
    memberships = _keys(data, "solution_membership")
    assert "contoso|security_role|Sales Manager" in memberships


def _v4(tmp_path: Path) -> Path:
    """The extraction of the synthetic org rewritten as a schema_version 4 manifest."""
    folder = tmp_path / "older"
    _extract(FakeClient(org=_org()), folder)
    data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    data["schema_version"] = 4
    data["records"] = [r for r in data["records"] if r["kind"] not in NEW_KINDS]
    for kind in NEW_KINDS:
        del data["coverage"][kind]
    (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_a_v4_manifest_reads_each_new_kind_as_unsupported_and_diffs_it_not_comparable(tmp_path):
    older = _v4(tmp_path)
    _extract(FakeClient(org=_org()), tmp_path / "newer")
    inv_a, inv_b = inventory_diff.read_pair(older, tmp_path / "newer")
    assert inv_a.schema_version == 5
    for kind in NEW_KINDS:
        coverage = inv_a.coverage[kind]
        assert (coverage.status, coverage.records) == ("unsupported", 0)
        assert coverage.errors == (
            f"not read: a schema_version 4 extraction predates the {kind} kind",
        )
    diff = inventory_diff.diff_inventories(inv_a, inv_b)
    roles = [e for e in diff.entries if e.kind == "security_role"]
    assert roles and all(e.verdict == "not_comparable" for e in roles)


def test_assess_lists_each_new_kind_as_uncovered_with_its_count(tmp_path):
    _extract(FakeClient(org=_org()), tmp_path / "inv")
    result = assess(tmp_path / "inv", "online")
    uncovered = {e["kind"]: e["records"] for e in result.uncovered_kinds()}
    assert {k: uncovered.get(k) for k in NEW_KINDS} == {
        "security_role": 2,
        "field_security_profile": 1,
        "duplicate_rule": 1,
        "email_template": 1,
        "connection_role": 1,
    }
    assert not [f for f in result.findings if f.kind in NEW_KINDS]


def test_packages_places_every_record_of_the_new_kinds_in_a_package(tmp_path):
    _extract(FakeClient(org=_org()), tmp_path / "inv")
    (tmp_path / "repo").mkdir()
    source_map = map_source(tmp_path / "inv", tmp_path / "repo")
    result = build_packages(tmp_path / "inv", source_map)
    placed = {m for p in result.packages for m in p.members}
    inv = inventory.read_inventory(tmp_path / "inv")
    wanted = {f"{r.kind}:{r.key}" for r in inv.records if r.kind in NEW_KINDS}
    assert len(wanted) == 6
    assert wanted <= placed


def test_the_web_api_reads_the_new_tables_and_the_rule_columns_only_online():
    sets = {
        "role": "roles",
        "roleprivileges": "roleprivilegescollection",
        "fieldsecurityprofile": "fieldsecurityprofiles",
        "fieldpermission": "fieldpermissions",
        "duplicaterule": "duplicaterules",
        "duplicaterulecondition": "duplicateruleconditions",
        "template": "templates",
        "connectionrole": "connectionroles",
    }
    assert {e: _ENTITY_SETS[e][0] for e in sets} == sets

    def rule(platform: str) -> tuple[str, ...]:
        (read,) = [r for r in inventory_extract.reads_for(platform) if r.entity == "duplicaterule"]
        return read.attributes

    # Measured: CRM 2011 and on-premises v9 fault on duplicaterule.ismanaged; online has it.
    assert "ismanaged" not in rule("crm2011") and "ismanaged" not in rule("onprem")
    assert {"ismanaged", "solutionid"} <= set(rule("cloud"))
