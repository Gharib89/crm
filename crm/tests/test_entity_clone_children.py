"""Record clone with children — `entity clone <set> <guid> --with-children` (#256, phase 2).

Builds on the phase-1 single-record clone (test_entity_clone). Mocked-backend
metadata pattern: a real D365Backend driven by requests_mock so the exact
GET/POST paths are asserted and over-fetching surfaces as NoMockAddress.

Child selection is custom 1:N relationships where the source is the parent
(`IsCustomRelationship == true`); each child row is cloned with the same
never-copy/lookup rules as the parent, and every lookup whose value equals the
source parent repoints to the new parent. Failures continue (ADR 0007).
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Error

_SRC = "11111111-1111-1111-1111-111111111111"
_NEW_PARENT = "99999999-9999-9999-9999-999999999999"
_CHILD_SRC = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_NEW_CHILD = "dddddddd-dddd-dddd-dddd-dddddddddddd"

_LLN = "Microsoft.Dynamics.CRM.lookuplogicalname"
_ANP = "Microsoft.Dynamics.CRM.associatednavigationproperty"


pytestmark = pytest.mark.usefixtures("isolated_home")
# ── shared metadata ───────────────────────────────────────────────────────

_DEFS = {"value": [
    {"LogicalName": "account", "EntitySetName": "accounts"},
    {"LogicalName": "contact", "EntitySetName": "contacts"},
    {"LogicalName": "contoso_invoice", "EntitySetName": "contoso_invoices"},
    {"LogicalName": "contoso_note", "EntitySetName": "contoso_notes"},
    {"LogicalName": "systemuser", "EntitySetName": "systemusers"},
]}

# Parent (account): never-copy trio + two plain writable fields.
_ATTRS_PARENT = {"value": [
    {"LogicalName": "accountid", "AttributeType": "Uniqueidentifier", "IsValidForCreate": True},
    {"LogicalName": "statecode", "AttributeType": "State", "IsValidForCreate": True},
    {"LogicalName": "statuscode", "AttributeType": "Status", "IsValidForCreate": True},
    {"LogicalName": "ownerid", "AttributeType": "Owner", "IsValidForCreate": True},
    {"LogicalName": "name", "AttributeType": "String", "IsValidForCreate": True},
    {"LogicalName": "telephone1", "AttributeType": "String", "IsValidForCreate": True},
]}

_SOURCE_PARENT = {
    "accountid": _SRC,
    "statecode": 0,
    "statuscode": 1,
    "_ownerid_value": "00000000-0000-0000-0000-000000000001",
    "name": "Contoso",
    "telephone1": "555-0100",
}

# Child (contoso_invoice): primary id + name + the relationship lookup to account.
_ATTRS_INVOICE = [
    {"LogicalName": "contoso_invoiceid", "AttributeType": "Uniqueidentifier", "IsValidForCreate": True},
    {"LogicalName": "contoso_name", "AttributeType": "String", "IsValidForCreate": True},
    {"LogicalName": "contoso_account", "AttributeType": "Lookup", "IsValidForCreate": True},
]


def _u(b, rel: str) -> str:
    return b.url_for(rel)


def _stub_parent_reads(m, b):
    """Stub the parent's defs/attrs/source GETs (no create)."""
    m.get(_u(b, "EntityDefinitions"), json=_DEFS)
    m.get(_u(b, "EntityDefinitions(LogicalName='account')/Attributes"), json=_ATTRS_PARENT)
    m.get(_u(b, f"accounts({_SRC})"), json=_SOURCE_PARENT)


def _stub_parent(m, b):
    """Stub the parent reads and a successful create POST (id-only)."""
    _stub_parent_reads(m, b)
    return m.post(
        _u(b, "accounts"), status_code=204,
        headers={"OData-EntityId": _u(b, f"accounts({_NEW_PARENT})")},
    )


def _stub_relationships(m, b, parent_logical, rels):
    """Stub OneToManyRelationships. `rels` = [(child, attr, is_custom), ...]."""
    m.get(
        _u(b, f"EntityDefinitions(LogicalName='{parent_logical}')/OneToManyRelationships"),
        json={"value": [
            {"ReferencingEntity": c, "ReferencingAttribute": a,
             "ReferencedEntity": parent_logical, "IsCustomRelationship": cust}
            for c, a, cust in rels
        ]},
    )


def _stub_child_entitydef(m, b, logical, primary_id, attrs):
    """Stub the child entity-def GET (PrimaryIdAttribute + expanded Attributes)."""
    m.get(
        _u(b, f"EntityDefinitions(LogicalName='{logical}')"),
        json={"PrimaryIdAttribute": primary_id, "Attributes": attrs},
    )


def _stub_child_create(m, b, child_set, new_id):
    """Stub a child create POST: 204 + OData-EntityId (the return_record=False path)."""
    return m.post(
        _u(b, child_set), status_code=204,
        headers={"OData-EntityId": _u(b, f"{child_set}({new_id})")},
    )


def _invoice_row(account_value=_SRC, **extra):
    """A contoso_invoice row referencing the parent account via contoso_account."""
    row = {
        "contoso_invoiceid": _CHILD_SRC,
        "contoso_name": "Inv-1",
        "_contoso_account_value": account_value,
        f"_contoso_account_value@{_ANP}": "contoso_account",
        f"_contoso_account_value@{_LLN}": "account",
    }
    row.update(extra)
    return row


class TestTracer:
    def test_clones_parent_and_one_custom_child_skipping_system_rel(self, backend):
        with requests_mock.Mocker() as m:
            parent_post = _stub_parent(m, backend)
            _stub_relationships(m, backend, "account", [
                ("contoso_invoice", "contoso_account", True),   # custom → cloned
                ("contact", "parentaccountid", False),          # system → skipped
            ])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [_invoice_row()]})
            child_post = _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)

            result = entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)

            # Parent created once; one custom child created once.
            assert parent_post.call_count == 1
            assert child_post.call_count == 1
            # The system relationship's child (contact) was never touched.
            assert "contacts" not in {r.path.split("/")[-1] for r in m.request_history}

        assert result["created"]["parent"] == _NEW_PARENT
        assert result["created"]["children"] == {"contoso_invoice": [_NEW_CHILD]}
        assert result["failures"] == []


_OTHER = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"

# Child with three lookups: the relationship attribute + a second lookup both
# pointing at the source parent, and an unrelated lookup pointing elsewhere.
_ATTRS_INVOICE_3LOOKUP = [
    {"LogicalName": "contoso_invoiceid", "AttributeType": "Uniqueidentifier", "IsValidForCreate": True},
    {"LogicalName": "contoso_account", "AttributeType": "Lookup", "IsValidForCreate": True},
    {"LogicalName": "contoso_secondaccount", "AttributeType": "Lookup", "IsValidForCreate": True},
    {"LogicalName": "contoso_primarycontact", "AttributeType": "Lookup", "IsValidForCreate": True},
]


def _lookup(row, attr, guid, target):
    vk = f"_{attr}_value"
    row[vk] = guid
    row[f"{vk}@{_ANP}"] = attr
    row[f"{vk}@{_LLN}"] = target
    return row


class TestChildLookupRepoint:
    def test_every_lookup_at_source_parent_repoints_others_copy(self, backend):
        row = {"contoso_invoiceid": _CHILD_SRC}
        _lookup(row, "contoso_account", _SRC, "account")          # relationship attr → repoint
        _lookup(row, "contoso_secondaccount", _SRC, "account")    # also at source → repoint
        _lookup(row, "contoso_primarycontact", _OTHER, "contact")  # elsewhere → copy as-is
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid",
                                  _ATTRS_INVOICE_3LOOKUP)
            m.get(_u(backend, "contoso_invoices"), json={"value": [row]})
            child_post = _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)

            entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)
            body = child_post.last_request.json()

        # Both lookups pointing at the source parent now point at the new parent.
        assert body["contoso_account@odata.bind"] == f"/accounts({_NEW_PARENT})"
        assert body["contoso_secondaccount@odata.bind"] == f"/accounts({_NEW_PARENT})"
        # The unrelated lookup is preserved verbatim.
        assert body["contoso_primarycontact@odata.bind"] == f"/contacts({_OTHER})"
        # The child's own primary id is never copied.
        assert "contoso_invoiceid" not in body


class TestMultiRelationship:
    def test_same_child_via_two_custom_relationships_is_cloned_once(self, backend):
        # A child entity with two custom 1:N lookups back to the parent matches
        # both relationship fetches; the source row must be cloned once, not
        # duplicated (dedup by entity + source primary id).
        row = {"contoso_invoiceid": _CHILD_SRC}
        _lookup(row, "contoso_account", _SRC, "account")
        _lookup(row, "contoso_secondaccount", _SRC, "account")
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account", [
                ("contoso_invoice", "contoso_account", True),
                ("contoso_invoice", "contoso_secondaccount", True),
            ])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid",
                                  _ATTRS_INVOICE_3LOOKUP)
            m.get(_u(backend, "contoso_invoices"), json={"value": [row]})
            child_post = _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)

            result = entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)

        assert child_post.call_count == 1
        assert result["created"]["children"] == {"contoso_invoice": [_NEW_CHILD]}


class TestSkipChildEntity:
    def test_skip_prunes_a_custom_child(self, backend):
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account", [
                ("contoso_invoice", "contoso_account", True),
                ("contoso_note", "contoso_noteaccount", True),   # pruned by skip
            ])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [_invoice_row()]})
            _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)
            # No mocks for contoso_note: if skip failed, the run would NoMockAddress.

            result = entity_mod.clone_record(
                backend, "accounts", _SRC,
                with_children=True, skip_child_entities=["contoso_note"],
            )

            paths = {r.path for r in m.request_history}
        assert not any("contoso_note" in p for p in paths)
        assert result["created"]["children"] == {"contoso_invoice": [_NEW_CHILD]}
        assert result["failures"] == []


_CHILD_SRC2 = "c2c2c2c2-c2c2-c2c2-c2c2-c2c2c2c2c2c2"


class TestFailureBehavior:
    def test_child_failure_continues_and_is_reported(self, backend):
        # Two child rows; the first create succeeds, the second fails. The verb
        # continues (ADR 0007) — parent + the good child land, the bad row is
        # named in failures, nothing rolls back.
        good = _invoice_row()
        bad = _invoice_row(contoso_invoiceid=_CHILD_SRC2, contoso_name="Inv-2")
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [good, bad]})
            m.post(_u(backend, "contoso_invoices"), [
                {"status_code": 204,
                 "headers": {"OData-EntityId": _u(backend, f"contoso_invoices({_NEW_CHILD})")}},
                {"status_code": 400,
                 "json": {"error": {"code": "0x0", "message": "duplicate key"}}},
            ])

            result = entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)

        assert result["created"]["parent"] == _NEW_PARENT
        assert result["created"]["children"] == {"contoso_invoice": [_NEW_CHILD]}
        assert result["failures"] == [
            {"entity": "contoso_invoice", "source_id": _CHILD_SRC2,
             "reason": result["failures"][0]["reason"]},
        ]
        assert "duplicate key" in result["failures"][0]["reason"]

    def test_parent_failure_raises_without_touching_children(self, backend):
        with requests_mock.Mocker() as m:
            _stub_parent_reads(m, backend)
            m.post(_u(backend, "accounts"), status_code=400,
                   json={"error": {"code": "0x0", "message": "parent rejected"}})
            # Relationship + child mocks deliberately absent: none must be reached.

            with pytest.raises(D365Error):
                entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)

            paths = {r.path for r in m.request_history}
        # No child enumeration happened — the parent create failure ended it.
        assert not any("OneToManyRelationships" in p for p in paths)

    def test_child_without_parsable_id_is_a_failure_not_a_created_id(self, backend):
        # A child create whose response carries no id must not be recorded as a
        # created "" — it is a per-row failure (meta.created stays trustworthy).
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [_invoice_row()]})
            m.post(_u(backend, "contoso_invoices"), status_code=204)  # no OData-EntityId

            result = entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)

        assert result["created"]["children"] == {}
        assert result["failures"][0]["entity"] == "contoso_invoice"
        assert result["failures"][0]["source_id"] == _CHILD_SRC

    def test_unreadable_parent_id_fails_fast_before_children(self, backend):
        # Parent create "succeeds" but the response carries no parsable id (no
        # OData-EntityId). Binding children to /accounts() would fail every row;
        # instead fail fast with a clear error before any child is attempted.
        with requests_mock.Mocker() as m:
            _stub_parent_reads(m, backend)
            m.post(_u(backend, "accounts"), status_code=204)   # no OData-EntityId
            # No relationship/child mocks: children must never be reached.
            with pytest.raises(D365Error, match="id"):
                entity_mod.clone_record(backend, "accounts", _SRC, with_children=True)
            paths = {r.path for r in m.request_history}
        assert not any("OneToManyRelationships" in p for p in paths)


class TestDryRunChildren:
    def test_preview_counts_children_and_marks_skipped_without_writing(self, dry_backend):
        with requests_mock.Mocker() as m:
            _stub_parent_reads(m, dry_backend)
            _stub_relationships(m, dry_backend, "account", [
                ("contoso_invoice", "contoso_account", True),
                ("contoso_note", "contoso_noteaccount", True),   # skipped
            ])
            # Real count GET (the on-prem-safe $count=true form) for the cloned child.
            m.get(_u(dry_backend, "contoso_invoices"),
                  json={"@odata.count": 3, "value": [{}]})

            result = entity_mod.clone_record(
                dry_backend, "accounts", _SRC,
                with_children=True, skip_child_entities=["contoso_note"],
            )

            methods = {r.method for r in m.request_history}
            paths = {r.path for r in m.request_history}
        # Read-only: no writes at all, and the skipped child was never counted.
        assert "POST" not in methods
        assert not any("contoso_notes" in p for p in paths)
        assert result["_dry_run"] is True
        assert result["would_create"] == {
            "entity_set": "accounts",
            "body": {"name": "Contoso", "telephone1": "555-0100"},
        }
        assert result["children"] == [
            {"entity": "contoso_invoice", "would_create": 3},
            {"entity": "contoso_note", "skipped": True},
        ]

    def test_preview_surfaces_uncountable_child_error(self, dry_backend):
        # A child that rejects the count read (permissions / RetrieveMultiple-
        # unsupported) is reported as would_create:null + the error, not a bare
        # null the caller can't explain (mirrors `entity children`).
        with requests_mock.Mocker() as m:
            _stub_parent_reads(m, dry_backend)
            _stub_relationships(m, dry_backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            m.get(_u(dry_backend, "contoso_invoices"), status_code=400,
                  json={"error": {"code": "0x0",
                                  "message": "RetrieveMultiple not supported"}})
            result = entity_mod.clone_record(dry_backend, "accounts", _SRC, with_children=True)
        child = result["children"][0]
        assert child["would_create"] is None
        assert "RetrieveMultiple" in child["error"]


class TestCommand:
    def _bind(self, monkeypatch, b):
        monkeypatch.setattr(CLIContext, "backend", lambda self: b)

    def test_with_children_success_envelope(self, backend, monkeypatch):
        self._bind(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [_invoice_row()]})
            _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)
            res = CliRunner().invoke(
                cli, ["--json", "entity", "clone", "accounts", _SRC, "--with-children"])
        assert res.exit_code == 0, res.output
        env = json.loads(res.output)
        assert env["ok"] is True
        assert env["meta"]["created"] == {
            "parent": _NEW_PARENT, "children": {"contoso_invoice": [_NEW_CHILD]}}
        assert env["data"]["failures"] == []

    def test_with_children_partial_failure_exits_one(self, backend, monkeypatch):
        self._bind(monkeypatch, backend)
        good = _invoice_row()
        bad = _invoice_row(contoso_invoiceid=_CHILD_SRC2, contoso_name="Inv-2")
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [good, bad]})
            m.post(_u(backend, "contoso_invoices"), [
                {"status_code": 204,
                 "headers": {"OData-EntityId": _u(backend, f"contoso_invoices({_NEW_CHILD})")}},
                {"status_code": 400, "json": {"error": {"code": "0x0", "message": "dup"}}},
            ])
            res = CliRunner().invoke(
                cli, ["--json", "entity", "clone", "accounts", _SRC, "--with-children"])
        assert res.exit_code == 1
        env = json.loads(res.output)
        assert env["ok"] is False
        assert env["meta"]["created"]["children"] == {"contoso_invoice": [_NEW_CHILD]}
        assert env["data"]["failures"][0]["source_id"] == _CHILD_SRC2

    def test_with_children_dry_run_envelope(self, dry_backend, monkeypatch):
        self._bind(monkeypatch, dry_backend)
        with requests_mock.Mocker() as m:
            _stub_parent_reads(m, dry_backend)
            _stub_relationships(m, dry_backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            m.get(_u(dry_backend, "contoso_invoices"), json={"@odata.count": 2, "value": [{}]})
            res = CliRunner().invoke(
                cli, ["--json", "--dry-run", "entity", "clone", "accounts", _SRC, "--with-children"])
        assert res.exit_code == 0, res.output
        env = json.loads(res.output)
        assert env["meta"]["dry_run"] is True
        assert env["data"]["would_create"]["entity_set"] == "accounts"
        assert env["data"]["children"] == [{"entity": "contoso_invoice", "would_create": 2}]

    def test_override_applies_to_parent_only(self, backend, monkeypatch):
        self._bind(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            parent_post = _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [_invoice_row()]})
            child_post = _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)
            res = CliRunner().invoke(cli, [
                "--json", "entity", "clone", "accounts", _SRC,
                "--with-children", "--override", "name=Renamed"])
            parent_body = parent_post.last_request.json()
            child_body = child_post.last_request.json()
        assert res.exit_code == 0, res.output
        assert parent_body["name"] == "Renamed"
        # The override never leaks onto the child rows.
        assert "name" not in child_body

    def test_human_mode_surfaces_created_ids_without_json_meta(self, backend, monkeypatch):
        # meta.created is a JSON-contract field, but emit() renders meta in human
        # mode too — so in human mode the created ids are surfaced via data, and
        # the new parent id is visible to the user.
        self._bind(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _stub_parent(m, backend)
            _stub_relationships(m, backend, "account",
                                [("contoso_invoice", "contoso_account", True)])
            _stub_child_entitydef(m, backend, "contoso_invoice", "contoso_invoiceid", _ATTRS_INVOICE)
            m.get(_u(backend, "contoso_invoices"), json={"value": [_invoice_row()]})
            _stub_child_create(m, backend, "contoso_invoices", _NEW_CHILD)
            res = CliRunner().invoke(  # no --json
                cli, ["entity", "clone", "accounts", _SRC, "--with-children"])
        assert res.exit_code == 0, res.output
        assert _NEW_PARENT in res.output

    def test_skip_child_entity_without_with_children_is_usage_error(self, monkeypatch):
        # --skip-child-entity is meaningless without --with-children; rather than
        # silently ignore it, fail as a usage error (exit 2) before any backend.
        def _boom(self):
            raise AssertionError("backend must not be built for a usage error")
        monkeypatch.setattr(CLIContext, "backend", _boom)
        res = CliRunner().invoke(cli, [
            "--json", "entity", "clone", "accounts", _SRC,
            "--skip-child-entity", "contoso_note"])
        assert res.exit_code == 2
        assert "with-children" in res.output
