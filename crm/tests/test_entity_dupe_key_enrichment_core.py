"""Tests for the relocated alternate-key enrichment in crm.core.entity (#347).

Covers the presentation-agnostic core seam — `is_alternate_key_error`,
`lookup_alternate_key_schema`, `dupe_key_hint`, and `enrich_dupe_key` — directly
against the real backend + requests_mock (no CliRunner). The command-layer
end-to-end wiring is asserted unchanged in test_entity_duplicate_key_enrichment.py.
"""
# pyright: basic

from __future__ import annotations

import pytest
import requests_mock

from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Error


# ── is_alternate_key_error detection ─────────────────────────────────────────


def test_is_alternate_key_error_true_for_known_code():
    exc = D365Error("Entity Key violated.", status=412, code="0x80060892")
    assert entity_mod.is_alternate_key_error(exc) is True


def test_is_alternate_key_error_false_for_precondition_failed():
    exc = D365Error("ETag mismatch", status=412, code="PreconditionFailed")
    assert entity_mod.is_alternate_key_error(exc) is False


def test_is_alternate_key_error_fallback_via_response_body():
    exc = D365Error("Entity Key violated.", status=412, code="PreconditionFailed",
                    response_body={"error": {"code": "0x80060892", "message": "..."}})
    assert entity_mod.is_alternate_key_error(exc) is True


# ── enrich_dupe_key (lookup + build) ─────────────────────────────────────────


def _entity_defs_url(backend):
    return backend.url_for("EntityDefinitions")


def _keys_url(backend, logical_name: str):
    return backend.url_for(f"EntityDefinitions(LogicalName='{logical_name}')/Keys")


def _mock_account_key(m, backend, key_rows):
    m.get(_entity_defs_url(backend), json={"value": [
        {"LogicalName": "account", "PrimaryIdAttribute": "accountid"}
    ]})
    m.get(_keys_url(backend, "account"), json={"value": key_rows})


_COMPOSITE_KEY = [{
    "LogicalName": "account_code_ak",
    "SchemaName": "Account_Code_AK",
    "KeyAttributes": ["accountnumber", "name"],
    "EntityKeyIndexStatus": "Active",
}]


def test_enrich_returns_alternate_keys_with_payload_values(backend):
    with requests_mock.Mocker() as m:
        _mock_account_key(m, backend, _COMPOSITE_KEY)
        result = entity_mod.enrich_dupe_key(
            backend, "accounts",
            {"name": "Contoso", "accountnumber": "ACC-001"},
            code="0x80060892",
        )
    k = result["alternate_keys"][0]
    assert k["name"] == "account_code_ak"
    assert k["schema_name"] == "Account_Code_AK"
    assert k["attributes"] == ["accountnumber", "name"]
    assert k["payload_values"] == {"accountnumber": "ACC-001", "name": "Contoso"}


def test_enrich_empty_payload_values_when_no_intersection(backend):
    with requests_mock.Mocker() as m:
        _mock_account_key(m, backend, _COMPOSITE_KEY)
        result = entity_mod.enrich_dupe_key(
            backend, "accounts", {"telephone1": "555"}, code="0x80060892",
        )
    assert result["alternate_keys"][0]["payload_values"] == {}


def test_enrich_primary_id_collision_hint(backend):
    with requests_mock.Mocker() as m:
        _mock_account_key(m, backend, [])  # no alt keys, but payload has the PK
        result = entity_mod.enrich_dupe_key(
            backend, "accounts",
            {"accountid": "11111111-1111-1111-1111-111111111111"},
            code="0x80060892",
        )
    assert result["alternate_keys"] == []
    assert "primary_id_hint" in result
    assert "accountid" in result["primary_id_hint"]


def test_enrich_no_primary_id_hint_when_not_in_payload(backend):
    with requests_mock.Mocker() as m:
        _mock_account_key(m, backend, [])
        result = entity_mod.enrich_dupe_key(
            backend, "accounts", {"name": "Contoso"}, code="0x80060892",
        )
    assert "primary_id_hint" not in result


def test_enrich_non_alt_key_code_returns_empty_without_lookup(backend):
    """A non-alternate-key code short-circuits — no metadata reads at all."""
    with requests_mock.Mocker() as m:
        # No mocks registered: any backend GET would raise NoMockAddress.
        result = entity_mod.enrich_dupe_key(
            backend, "accounts", {"name": "x"}, code="PreconditionFailed",
        )
    assert result == {}
    assert m.call_count == 0


def test_enrich_lookup_failure_returns_empty(backend):
    """A backend failure during lookup is swallowed (original error unmasked)."""
    with requests_mock.Mocker() as m:
        m.get(_entity_defs_url(backend), status_code=500,
              json={"error": {"message": "Server error"}})
        result = entity_mod.enrich_dupe_key(
            backend, "accounts", {"name": "x"}, code="0x80060892",
        )
    assert result == {}


def test_enrich_unknown_entity_set_returns_empty(backend):
    with requests_mock.Mocker() as m:
        m.get(_entity_defs_url(backend), json={"value": []})
        result = entity_mod.enrich_dupe_key(
            backend, "unknownsets", {"name": "x"}, code="0x80060892",
        )
    assert result == {}


def test_lookup_alternate_key_schema_reused_across_payloads(backend):
    """The schema is fetched once and `dupe_key_hint` applied per payload — the
    bulk-import path's single-lookup-N-rows shape."""
    with requests_mock.Mocker() as m:
        _mock_account_key(m, backend, _COMPOSITE_KEY)
        schema = entity_mod.lookup_alternate_key_schema(backend, "accounts")
        assert schema is not None
        h1 = entity_mod.dupe_key_hint(schema, {"accountnumber": "A", "name": "X"})
        h2 = entity_mod.dupe_key_hint(schema, {"accountnumber": "B", "name": "Y"})
    # One EntityDefinitions GET + one Keys GET — not per payload.
    assert m.call_count == 2
    assert h1["alternate_keys"][0]["payload_values"] == {"accountnumber": "A", "name": "X"}
    assert h2["alternate_keys"][0]["payload_values"] == {"accountnumber": "B", "name": "Y"}
