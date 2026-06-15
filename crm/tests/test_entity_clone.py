"""Record clone — `entity clone <set> <guid>` (#255, phase 1).

Mirrors the mocked-backend metadata pattern (see test_entity_validate): a real
D365Backend driven by requests_mock so the exact GET/POST paths are asserted and
over-fetching surfaces as NoMockAddress. The clone resolves everything (never-copy
filtering, lookup binds, override/unset) before the single create write.
"""
# pyright: basic

from __future__ import annotations

import json
import time

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.commands.entity import _parse_overrides
from crm.core import entity as entity_mod
from crm.core import metadata_cache
from crm.utils.d365_backend import D365Error

_SRC = "11111111-1111-1111-1111-111111111111"
_NEW = "99999999-9999-9999-9999-999999999999"

_LLN = "Microsoft.Dynamics.CRM.lookuplogicalname"
_ANP = "Microsoft.Dynamics.CRM.associatednavigationproperty"


pytestmark = pytest.mark.usefixtures("isolated_home")
# ── mocked endpoints ──────────────────────────────────────────────────────


def _defs_url(b) -> str:
    return b.url_for("EntityDefinitions")


def _attrs_url(b) -> str:
    return b.url_for("EntityDefinitions(LogicalName='account')/Attributes")


def _record_url(b) -> str:
    return b.url_for(f"accounts({_SRC})")


def _post_url(b) -> str:
    return b.url_for("accounts")


_DEFS = {"value": [
    {"LogicalName": "account", "EntitySetName": "accounts",
     "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name"},
    {"LogicalName": "contact", "EntitySetName": "contacts",
     "PrimaryIdAttribute": "contactid", "PrimaryNameAttribute": "fullname"},
    {"LogicalName": "systemuser", "EntitySetName": "systemusers",
     "PrimaryIdAttribute": "systemuserid", "PrimaryNameAttribute": "fullname"},
]}

# A simple account: a primary id, two Uniqueidentifier columns (primary +
# address child id), the state/status/owner never-copy trio, one read-only
# system column, and two plain writable fields.
_ATTRS_SIMPLE = {"value": [
    {"LogicalName": "accountid", "AttributeType": "Uniqueidentifier", "IsValidForCreate": True},
    {"LogicalName": "address1_addressid", "AttributeType": "Uniqueidentifier", "IsValidForCreate": True},
    {"LogicalName": "statecode", "AttributeType": "State", "IsValidForCreate": True},
    {"LogicalName": "statuscode", "AttributeType": "Status", "IsValidForCreate": True},
    {"LogicalName": "ownerid", "AttributeType": "Owner", "IsValidForCreate": True},
    {"LogicalName": "createdon", "AttributeType": "DateTime", "IsValidForCreate": False},
    {"LogicalName": "name", "AttributeType": "String", "IsValidForCreate": True},
    {"LogicalName": "telephone1", "AttributeType": "String", "IsValidForCreate": True},
]}

_SOURCE_SIMPLE = {
    "@odata.etag": 'W/"1001"',
    "accountid": _SRC,
    "address1_addressid": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "statecode": 0,
    "statuscode": 1,
    "_ownerid_value": "00000000-0000-0000-0000-000000000001",
    "createdon": "2020-01-01T00:00:00Z",
    "name": "Contoso",
    "telephone1": "555-0100",
}


class TestTracer:
    def test_clones_plain_attrs_dropping_never_copy_set(self, backend):
        created = {"accountid": _NEW, "name": "Contoso", "telephone1": "555-0100"}
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            post = m.post(_post_url(backend), json=created, status_code=201)

            result = entity_mod.clone_record(backend, "accounts", _SRC)

            # Exactly one write, and it is the create POST.
            assert post.call_count == 1
            body = post.last_request.json()

        # Never-copy set gone: primary id, the address child id (both
        # Uniqueidentifier), statecode/statuscode/ownerid; createdon is not
        # valid-for-create. Only the two plain writable fields survive.
        assert body == {"name": "Contoso", "telephone1": "555-0100"}
        # Default echoes the created record back (matches `entity create`).
        assert result == created


class TestWarmCacheServesResolution:
    """AC4 (#261): core name-resolution reads go through the read-through metadata
    cache, so clone does not re-fetch entity definitions live when the cache is warm.
    (--with-children reuses the same in-memory map, so a plain clone demonstrates it.)"""

    def test_clone_does_not_refetch_definitions_when_cache_warm(self, backend):
        # Warm the read-through cache for this profile (CRM_HOME is isolated to a
        # temp dir by the autouse fixture).
        metadata_cache.write_definitions(
            backend.profile,
            [{"logical": "account", "set_name": "accounts"},
             {"logical": "contact", "set_name": "contacts"},
             {"logical": "systemuser", "set_name": "systemusers"}],
            now=time.time(),
        )
        with requests_mock.Mocker() as m:
            defs = m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            entity_mod.clone_record(backend, "accounts", _SRC, return_record=False)

        # The name map was served from the warm cache — no live collection GET.
        assert defs.call_count == 0


# Account with a single-target lookup (primarycontactid -> contact) and a
# polymorphic one (customerid -> account|contact). Both are valid for create.
_ATTRS_LOOKUP = {"value": [
    {"LogicalName": "accountid", "AttributeType": "Uniqueidentifier", "IsValidForCreate": True},
    {"LogicalName": "name", "AttributeType": "String", "IsValidForCreate": True},
    {"LogicalName": "primarycontactid", "AttributeType": "Lookup", "IsValidForCreate": True},
    {"LogicalName": "customerid", "AttributeType": "Customer", "IsValidForCreate": True},
]}

_CONTACT = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _source_with_lookups(**lookups) -> dict:
    """Build a source record carrying `name` plus the given lookup columns.

    Each `lookups` entry is `attr=(guid, nav, target_logical)`; pass `nav`/target
    as None to omit that annotation (to exercise the unresolved path).
    """
    src = {"accountid": _SRC, "name": "Contoso"}
    for attr, (guid, nav, target) in lookups.items():
        vk = f"_{attr}_value"
        src[vk] = guid
        if nav is not None:
            src[f"{vk}@{_ANP}"] = nav
        if target is not None:
            src[f"{vk}@{_LLN}"] = target
    return src


class TestLookupConversion:
    def test_single_target_lookup_becomes_odata_bind(self, backend):
        source = _source_with_lookups(
            primarycontactid=(_CONTACT, "primarycontactid", "contact"),
        )
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_LOOKUP)
            m.get(_record_url(backend), json=source)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            entity_mod.clone_record(backend, "accounts", _SRC, return_record=False)
            body = post.last_request.json()

        # nav property comes from the record's own annotation (case-sensitive),
        # value is the deep-link to the same parent.
        assert body["primarycontactid@odata.bind"] == f"/contacts({_CONTACT})"
        assert body["name"] == "Contoso"
        # The raw lookup property is never sent as a plain field.
        assert "_primarycontactid_value" not in body
        assert "primarycontactid" not in body

    def test_polymorphic_lookup_binds_to_annotated_target(self, backend):
        # customerid is polymorphic (account|contact); the per-value nav and
        # target come from the record's annotations, so it binds to account.
        source = _source_with_lookups(
            customerid=(_SRC, "customerid_account", "account"),
        )
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_LOOKUP)
            m.get(_record_url(backend), json=source)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            entity_mod.clone_record(backend, "accounts", _SRC, return_record=False)
            body = post.last_request.json()

        assert body["customerid_account@odata.bind"] == f"/accounts({_SRC})"
        assert "customerid" not in body

    def test_unresolved_lookup_is_batched_preflight_failure(self, backend):
        # Lookup value present but no annotations to resolve nav/target: must
        # fail before any write, naming the field — never silently dropped.
        source = _source_with_lookups(
            customerid=(_SRC, None, None),
        )
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_LOOKUP)
            m.get(_record_url(backend), json=source)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            with pytest.raises(D365Error) as exc:
                entity_mod.clone_record(backend, "accounts", _SRC)

            # Org untouched: the create never fired.
            assert post.call_count == 0
        assert "customerid" in str(exc.value)


class TestOverrideUnset:
    def test_override_wins_and_passes_keys_raw(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            entity_mod.clone_record(
                backend, "accounts", _SRC,
                overrides={
                    "name": "Renamed",            # wins over the cloned value
                    "creditlimit": 5000,          # not a known attr — passes raw
                    "ownerid@odata.bind": "/systemusers(00000000-0000-0000-0000-000000000002)",
                },
                return_record=False,
            )
            body = post.last_request.json()

        assert body["name"] == "Renamed"
        assert body["creditlimit"] == 5000
        # A never-copy field is re-addable via an explicit override key.
        assert body["ownerid@odata.bind"] == "/systemusers(00000000-0000-0000-0000-000000000002)"

    def test_unset_drops_plain_field_and_lookup_by_logical_name(self, backend):
        source = _source_with_lookups(
            primarycontactid=(_CONTACT, "primarycontactid", "contact"),
        )
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_LOOKUP)
            m.get(_record_url(backend), json=source)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            entity_mod.clone_record(
                backend, "accounts", _SRC,
                unset=["name", "primarycontactid"],
                return_record=False,
            )
            body = post.last_request.json()

        assert "name" not in body
        # Unsetting the lookup by its LOGICAL name drops the produced bind key.
        assert "primarycontactid@odata.bind" not in body

    def test_unset_unknown_field_is_batched_preflight_failure(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)

            with pytest.raises(D365Error) as exc:
                entity_mod.clone_record(backend, "accounts", _SRC, unset=["nosuchfield"])

            assert post.call_count == 0
        assert "nosuchfield" in str(exc.value)


class TestDryRunAndReturn:
    def test_dry_run_previews_resolved_payload_without_writing(self, dry_backend):
        with requests_mock.Mocker() as m:
            m.get(_defs_url(dry_backend), json=_DEFS)
            m.get(_attrs_url(dry_backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(dry_backend), json=_SOURCE_SIMPLE)
            post = m.post(_post_url(dry_backend), json={"accountid": _NEW}, status_code=201)

            result = entity_mod.clone_record(dry_backend, "accounts", _SRC)

            # Reads execute in full (pre-flight runs); the write does not.
            assert post.call_count == 0
            methods = {r.method for r in m.request_history}
        assert methods == {"GET"}
        assert result["_dry_run"] is True
        # Preview carries the exact body that would hit the wire.
        assert result["would_create"] == {
            "entity_set": "accounts",
            "body": {"name": "Contoso", "telephone1": "555-0100"},
        }

    def test_no_return_yields_just_the_id(self, backend):
        entity_url = backend.url_for(f"accounts({_NEW})")
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            m.post(_post_url(backend), status_code=204,
                   headers={"OData-EntityId": entity_url})

            result = entity_mod.clone_record(backend, "accounts", _SRC, return_record=False)

        # --no-return uses the normalized id key (ADR 0008), not a bare `id`.
        assert result["_entity_id"] == _NEW
        assert result["_entity_id_url"] == entity_url

    def test_unknown_entity_set_errors_clean(self, backend):
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)

            with pytest.raises(D365Error) as exc:
                entity_mod.clone_record(backend, "widgets", _SRC)
        assert "widgets" in str(exc.value)

    def test_invalid_guid_fails_fast_without_backend_calls(self, backend):
        # A bad record id is validate-before-backend: it must raise before any
        # metadata GET, so a typo never costs a round-trip (mirrors count_children).
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)

            with pytest.raises(D365Error):
                entity_mod.clone_record(backend, "accounts", "not-a-guid")
            assert m.call_count == 0


class TestParseOverrides:
    def test_value_parsed_as_json_with_string_fallback(self):
        out = _parse_overrides((
            "creditlimit=5000",
            "donotemail=true",
            "name=Acme Corp",
            "ownerid@odata.bind=/systemusers(00000000-0000-0000-0000-000000000002)",
        ))
        assert out == {
            "creditlimit": 5000,
            "donotemail": True,
            "name": "Acme Corp",
            "ownerid@odata.bind": "/systemusers(00000000-0000-0000-0000-000000000002)",
        }

    def test_missing_equals_is_usage_error(self):
        import click
        with pytest.raises(click.UsageError):
            _parse_overrides(("nofield",))


class TestCommand:
    def _patch_backend(self, monkeypatch, b):
        monkeypatch.setattr(CLIContext, "backend", lambda self: b)

    def test_cli_clone_emits_created_record(self, backend, monkeypatch):
        self._patch_backend(monkeypatch, backend)
        created = {"accountid": _NEW, "name": "Contoso", "telephone1": "555-0100"}
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            m.post(_post_url(backend), json=created, status_code=201)
            result = CliRunner().invoke(cli, ["--json", "entity", "clone", "accounts", _SRC])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        # Envelope parity with `entity create` (ADR 0008): full created record
        # plus the normalized id keys.
        assert env["ok"] is True
        assert {k: v for k, v in env["data"].items() if not k.startswith("_entity_id")} == created
        assert env["data"]["_entity_id"] == _NEW
        assert env["data"]["_entity_id_url"].endswith(f"accounts({_NEW})")

    def test_cli_override_reaches_create_body(self, backend, monkeypatch):
        self._patch_backend(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            post = m.post(_post_url(backend), json={"accountid": _NEW}, status_code=201)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "clone", "accounts", _SRC,
                "--override", "name=Renamed", "--no-return",
            ])
            body = post.last_request.json()
        assert result.exit_code == 0, result.output
        assert body["name"] == "Renamed"

    def test_cli_dry_run_envelope_has_meta_and_would_create(self, dry_backend, monkeypatch):
        self._patch_backend(monkeypatch, dry_backend)
        with requests_mock.Mocker() as m:
            m.get(_defs_url(dry_backend), json=_DEFS)
            m.get(_attrs_url(dry_backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(dry_backend), json=_SOURCE_SIMPLE)
            post = m.post(_post_url(dry_backend), json={"accountid": _NEW}, status_code=201)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "entity", "clone", "accounts", _SRC,
            ])
            assert post.call_count == 0
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["dry_run"] is True
        # Emitted as-is: the {_dry_run, would_create} preview shape (sentinel
        # retained per ADR 0002), not unwrapped to bare would_create.
        assert env["data"]["_dry_run"] is True
        assert env["data"]["would_create"]["entity_set"] == "accounts"
        assert env["data"]["would_create"]["body"] == {
            "name": "Contoso", "telephone1": "555-0100",
        }

    def test_cli_bad_guid_fails_before_backend(self, monkeypatch):
        # Validate-before-backend: a malformed GUID must fail without ever
        # constructing a backend (no credential resolution / token acquisition).
        def _boom(self):
            raise AssertionError("backend() must not be called for a bad GUID")
        monkeypatch.setattr(CLIContext, "backend", _boom)
        result = CliRunner().invoke(cli, ["--json", "entity", "clone", "accounts", "not-a-guid"])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False

    def test_cli_preflight_failure_exits_one(self, backend, monkeypatch):
        self._patch_backend(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_defs_url(backend), json=_DEFS)
            m.get(_attrs_url(backend), json=_ATTRS_SIMPLE)
            m.get(_record_url(backend), json=_SOURCE_SIMPLE)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "clone", "accounts", _SRC, "--unset", "nosuchfield",
            ])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False
