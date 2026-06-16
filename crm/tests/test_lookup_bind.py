"""Import-side lookup binding (#333): READ ``_<attr>_value`` → WRITE
``<nav>@odata.bind``, resolved from metadata.

Behaviour is exercised through the public ``lookup_bind`` interface against a
``FakeBackend`` wired with mocked entity / relationship metadata, so the tests
read as a spec of the transform rather than of its internals.
"""

from __future__ import annotations

from typing import Any

import pytest

from crm.core import lookup_bind
from crm.tests.conftest import FakeBackend
from crm.utils.d365_backend import D365Error

pytestmark = pytest.mark.usefixtures("isolated_home")


# --- mocked metadata ---------------------------------------------------------
# account: a system single-target lookup (primarycontactid → contact, lowercase
# nav), a custom lookup whose nav prop is PascalCase (cwx_widgetid →
# cwx_WidgetId), and a read-only system lookup (createdby).
# contact: a polymorphic Customer lookup (parentcustomerid → account|contact).
_ENTITY_DEFS = [
    {"LogicalName": "account", "EntitySetName": "accounts",
     "PrimaryIdAttribute": "accountid", "PrimaryNameAttribute": "name"},
    {"LogicalName": "contact", "EntitySetName": "contacts",
     "PrimaryIdAttribute": "contactid", "PrimaryNameAttribute": "fullname"},
    {"LogicalName": "cwx_widget", "EntitySetName": "cwx_widgets",
     "PrimaryIdAttribute": "cwx_widgetid", "PrimaryNameAttribute": "cwx_name"},
    {"LogicalName": "owner", "EntitySetName": "owners",
     "PrimaryIdAttribute": "ownerid", "PrimaryNameAttribute": "name"},
    {"LogicalName": "systemuser", "EntitySetName": "systemusers",
     "PrimaryIdAttribute": "systemuserid", "PrimaryNameAttribute": "fullname"},
]

_ATTRS: dict[str, list[dict[str, Any]]] = {
    "account": [
        {"LogicalName": "name", "AttributeType": "String",
         "IsValidForCreate": True, "IsValidForUpdate": True},
        {"LogicalName": "primarycontactid", "AttributeType": "Lookup",
         "IsValidForCreate": True, "IsValidForUpdate": True},
        {"LogicalName": "cwx_widgetid", "AttributeType": "Lookup",
         "IsValidForCreate": True, "IsValidForUpdate": True},
        {"LogicalName": "createdby", "AttributeType": "Lookup",
         "IsValidForCreate": False, "IsValidForUpdate": False},
        {"LogicalName": "ownerid", "AttributeType": "Owner",
         "IsValidForCreate": True, "IsValidForUpdate": True},
    ],
    "contact": [
        {"LogicalName": "lastname", "AttributeType": "String",
         "IsValidForCreate": True, "IsValidForUpdate": True},
        {"LogicalName": "parentcustomerid", "AttributeType": "Customer",
         "IsValidForCreate": True, "IsValidForUpdate": True},
    ],
}

_M2O: dict[str, list[dict[str, str]]] = {
    "account": [
        {"ReferencingAttribute": "primarycontactid", "ReferencedEntity": "contact",
         "ReferencingEntityNavigationPropertyName": "primarycontactid"},
        {"ReferencingAttribute": "cwx_widgetid", "ReferencedEntity": "cwx_widget",
         "ReferencingEntityNavigationPropertyName": "cwx_WidgetId"},
        {"ReferencingAttribute": "createdby", "ReferencedEntity": "systemuser",
         "ReferencingEntityNavigationPropertyName": "createdby"},
        # Owner lookups relate to the abstract `owner` base table; the concrete
        # target (systemuser|team) only comes from the value's annotation.
        {"ReferencingAttribute": "ownerid", "ReferencedEntity": "owner",
         "ReferencingEntityNavigationPropertyName": "ownerid"},
    ],
    "contact": [
        {"ReferencingAttribute": "parentcustomerid", "ReferencedEntity": "account",
         "ReferencingEntityNavigationPropertyName": "parentcustomerid_account"},
        {"ReferencingAttribute": "parentcustomerid", "ReferencedEntity": "contact",
         "ReferencingEntityNavigationPropertyName": "parentcustomerid_contact"},
    ],
}


def _meta_backend() -> FakeBackend:
    """A FakeBackend that serves the mocked entity/attribute/relationship metadata."""

    def _get_collection(path: str) -> Any:
        if path == "EntityDefinitions":
            return list(_ENTITY_DEFS)
        for logical, rows in _ATTRS.items():
            if f"LogicalName='{logical}'" in path and path.endswith("/Attributes"):
                return list(rows)
        return []

    def _get(path: str) -> Any:
        for logical, rows in _M2O.items():
            if f"LogicalName='{logical}'" in path and path.endswith("/ManyToOneRelationships"):
                return {"value": list(rows)}
        return {"value": []}

    return FakeBackend(responses={"get_collection": _get_collection, "get": _get})


def _bind(entity_set: str, record: dict[str, Any]) -> dict[str, Any]:
    backend = _meta_backend()
    resolver = lookup_bind.build_resolver(backend, entity_set)
    return lookup_bind.bind_lookups(record, resolver)


# --- tests -------------------------------------------------------------------


def test_needs_binding_detects_read_format_keys() -> None:
    # The lazy guard: a record carrying a READ lookup or any OData annotation
    # needs the transform; a plain record (no metadata fetch) does not.
    assert lookup_bind.needs_binding({"_primarycontactid_value": "x"})
    assert lookup_bind.needs_binding({"@odata.etag": 'W/"1"'})
    assert not lookup_bind.needs_binding({"name": "Acme", "revenue": 10})
    # A hand-written write directive is already in write shape — no fetch needed.
    assert not lookup_bind.needs_binding({"primarycontactid@odata.bind": "/contacts(x)"})


def test_single_target_lookup_binds_via_metadata() -> None:
    out = _bind("accounts", {
        "name": "Acme",
        "_primarycontactid_value": "11111111-1111-1111-1111-111111111111",
    })
    assert out == {
        "name": "Acme",
        "primarycontactid@odata.bind": "/contacts(11111111-1111-1111-1111-111111111111)",
    }


def test_read_only_lookup_is_dropped() -> None:
    # createdby is a lookup but not valid for create/update — it cannot be
    # written, so its READ value must be dropped rather than rebound (the server
    # rejects a direct write to an entity-reference property).
    out = _bind("accounts", {
        "name": "Acme",
        "_createdby_value": "22222222-2222-2222-2222-222222222222",
    })
    assert out == {"name": "Acme"}


def test_custom_lookup_uses_case_sensitive_nav_from_metadata() -> None:
    # A custom lookup surfaces a PascalCase nav prop (cwx_WidgetId) that is NOT a
    # transform of the attribute name — it must come from metadata verbatim.
    out = _bind("accounts", {
        "_cwx_widgetid_value": "33333333-3333-3333-3333-333333333333",
    })
    assert out == {
        "cwx_WidgetId@odata.bind": "/cwx_widgets(33333333-3333-3333-3333-333333333333)",
    }


def test_polymorphic_lookup_binds_target_from_annotation() -> None:
    # parentcustomerid is polymorphic (account|contact). The lookuplogicalname
    # annotation selects the target table, hence the case-sensitive nav prop.
    guid = "55555555-5555-5555-5555-555555555555"
    out = _bind("contacts", {
        "lastname": "Doe",
        "_parentcustomerid_value": guid,
        f"_parentcustomerid_value@{lookup_bind.LOOKUP_LOGICAL_ANNOTATION}": "contact",
    })
    assert out == {
        "lastname": "Doe",
        "parentcustomerid_contact@odata.bind": f"/contacts({guid})",
    }


def test_owner_lookup_binds_concrete_target_from_annotation() -> None:
    # ownerid's only relationship is to the abstract `owner` base table; the
    # annotation names the concrete target (systemuser), so the bind URL uses
    # the systemusers set while the nav prop stays `ownerid`.
    guid = "66666666-6666-6666-6666-666666666666"
    out = _bind("accounts", {
        "_ownerid_value": guid,
        f"_ownerid_value@{lookup_bind.LOOKUP_LOGICAL_ANNOTATION}": "systemuser",
    })
    assert out == {"ownerid@odata.bind": f"/systemusers({guid})"}


def test_polymorphic_lookup_without_annotation_is_dropped() -> None:
    # A plain export carries no annotations, and polymorphic lookups (ownerid is
    # on every record) cannot be resolved to a concrete target — drop them so the
    # rest of the record still round-trips, rather than bind the abstract table.
    out = _bind("accounts", {
        "name": "Acme",
        "_ownerid_value": "66666666-6666-6666-6666-666666666666",
    })
    assert out == {"name": "Acme"}


def test_polymorphic_annotation_with_unknown_target_errors() -> None:
    # An annotation is present but names an entity that cannot be resolved to a
    # set — fail clearly rather than emit a broken bind URL.
    with pytest.raises(D365Error, match="parentcustomerid"):
        _bind("contacts", {
            "_parentcustomerid_value": "55555555-5555-5555-5555-555555555555",
            f"_parentcustomerid_value@{lookup_bind.LOOKUP_LOGICAL_ANNOTATION}": "nope",
        })


def test_null_value_clears_the_lookup() -> None:
    # A null READ value clears the relationship on update: the navigation
    # property is bound to null (any participating nav prop clears it).
    out = _bind("accounts", {"_primarycontactid_value": None})
    assert out == {"primarycontactid@odata.bind": None}


def test_read_only_annotation_keys_are_stripped() -> None:
    # @odata.* and formatted-value / per-value lookup annotations are read-only
    # and rejected on write — drop them. A caller-supplied <nav>@odata.bind is a
    # write directive and must be preserved.
    out = _bind("accounts", {
        "@odata.etag": 'W/"123"',
        "name": "Acme",
        "name@OData.Community.Display.V1.FormattedValue": "Acme",
        "_primarycontactid_value": "11111111-1111-1111-1111-111111111111",
        "_primarycontactid_value@OData.Community.Display.V1.FormattedValue": "Jane Doe",
        "cwx_WidgetId@odata.bind": "/cwx_widgets(44444444-4444-4444-4444-444444444444)",
    })
    assert out == {
        "name": "Acme",
        "primarycontactid@odata.bind": "/contacts(11111111-1111-1111-1111-111111111111)",
        "cwx_WidgetId@odata.bind": "/cwx_widgets(44444444-4444-4444-4444-444444444444)",
    }
