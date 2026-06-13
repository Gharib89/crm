# pyright: basic
"""Unit tests for crm/core/entity_names.py — the single entity-name resolution
seam (#261).

Two surfaces under test:
- ``load_name_map``: bidirectional logical <-> entity-set resolution, served
  read-through from ``metadata_cache`` (a warm cache is served without a live
  GET; the underlying fetch builds on #263's ``get_collection``).
- ``attribute_specs``: the one home for the ``IsValidForCreate`` /
  ``IsValidForUpdate`` walk, normalising raw attribute-metadata rows.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crm.core import entity_names
from crm.utils.d365_backend import ConnectionProfile, D365Error


@pytest.fixture(autouse=True)
def _isolate_crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point CRM_HOME at a temp dir so the read-through cache never touches ~/.crm."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))


def _profile(name: str = "testp") -> ConnectionProfile:
    return ConnectionProfile(
        name=name,
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


class _FakeBackend:
    """Records ``get_collection`` paths and serves canned rows per endpoint."""

    def __init__(
        self,
        *,
        defs: list[dict[str, Any]] | None = None,
        attrs: list[dict[str, Any]] | None = None,
        profile: ConnectionProfile | None = None,
    ) -> None:
        self.profile = profile or _profile()
        self._defs = defs or []
        self._attrs = attrs or []
        self.calls: list[str] = []

    def get_collection(self, path: str, *, params: Any = None, **_kw: Any) -> list[dict[str, Any]]:
        self.calls.append(path)
        if path == "EntityDefinitions":
            return self._defs
        if path.endswith("/Attributes"):
            return self._attrs
        raise AssertionError(f"unexpected get_collection: {path!r}")


_DEFS = [
    {"LogicalName": "account", "EntitySetName": "accounts"},
    {"LogicalName": "contact", "EntitySetName": "contacts"},
]


# ── load_name_map: bidirectional resolution ─────────────────────────────────

def test_load_name_map_resolves_both_directions():
    backend = _FakeBackend(defs=_DEFS)
    name_map = entity_names.load_name_map(backend)  # type: ignore[arg-type]
    assert name_map.set_for("account") == "accounts"
    assert name_map.logical_for("contacts") == "contact"


def test_load_name_map_unknown_names_return_none():
    name_map = entity_names.load_name_map(_FakeBackend(defs=_DEFS))  # type: ignore[arg-type]
    assert name_map.set_for("nope") is None
    assert name_map.logical_for("nopes") is None


def test_load_name_map_drops_rows_without_set_name():
    """A row with no EntitySetName is not OData-addressable → dropped both ways."""
    defs = _DEFS + [{"LogicalName": "noset", "EntitySetName": ""}]
    name_map = entity_names.load_name_map(_FakeBackend(defs=defs))  # type: ignore[arg-type]
    assert name_map.set_for("noset") is None
    assert "noset" not in name_map.logical_to_set


def test_load_name_map_warm_cache_served_without_live_get():
    """Second load within the TTL is a cache hit — no second collection GET."""
    backend = _FakeBackend(defs=_DEFS)
    entity_names.load_name_map(backend)  # type: ignore[arg-type]  # cold → fetch + write
    entity_names.load_name_map(backend)  # type: ignore[arg-type]  # warm → no fetch
    assert backend.calls.count("EntityDefinitions") == 1


def test_load_name_map_refresh_forces_live_get():
    backend = _FakeBackend(defs=_DEFS)
    entity_names.load_name_map(backend)  # type: ignore[arg-type]  # cold
    entity_names.load_name_map(backend, refresh=True)  # type: ignore[arg-type]  # forced
    assert backend.calls.count("EntityDefinitions") == 2


# ── attribute_specs: the one IsValidForCreate/IsValidForUpdate walk ──────────

_ATTRS = [
    {"LogicalName": "name", "AttributeType": "String",
     "RequiredLevel": {"Value": "ApplicationRequired"},
     "IsValidForCreate": True, "IsValidForUpdate": True},
    {"LogicalName": "createdon", "AttributeType": "DateTime",
     "RequiredLevel": {"Value": "None"},
     "IsValidForCreate": False, "IsValidForUpdate": False},
]


def test_attribute_specs_normalises_validity_and_type():
    backend = _FakeBackend(attrs=_ATTRS)
    specs = entity_names.attribute_specs(backend, "account")  # type: ignore[arg-type]
    by_name = {s.logical_name: s for s in specs}
    assert by_name["name"].attribute_type == "String"
    assert by_name["name"].required_level == "ApplicationRequired"
    assert by_name["name"].valid_for_create is True
    assert by_name["name"].valid_for_update is True
    assert by_name["createdon"].valid_for_create is False
    assert by_name["createdon"].valid_for_update is False


def test_attribute_specs_skips_rows_without_logical_name():
    backend = _FakeBackend(attrs=_ATTRS + [{"AttributeType": "String"}])
    specs = entity_names.attribute_specs(backend, "account")  # type: ignore[arg-type]
    assert len(specs) == 2


def test_attribute_specs_requires_logical_name():
    with pytest.raises(D365Error):
        entity_names.attribute_specs(_FakeBackend(), "")  # type: ignore[arg-type]
