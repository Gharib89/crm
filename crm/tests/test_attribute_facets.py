"""Attribute type facets (#1156): `max_length`, `format`, `default_value`, `precision`,
`min_value`, `max_value` and `option_set_global` on both metadata readers, the inventory
`attribute` record and the diff rule for a manifest written before they existed.
"""

# pyright: basic
from __future__ import annotations

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from crm.core import inventory, inventory_extract, legacy_reads
from crm.core.inventory import ATTRIBUTE_FACET_FIELDS
from crm.core.inventory_diff import diff_inventories
from crm.core.legacy_reads import entity_detail
from crm.core.webapi_inventory import _reshape_entity  # pyright: ignore[reportPrivateUsage]

_FIXTURES = Path(__file__).parent / "fixtures" / "inventory"
_SCHEMA = Path(inventory.__file__).parent.parent / "schemas" / "inventory-manifest-v4.schema.json"
_MD = "http://schemas.microsoft.com/xrm/2011/Metadata"
_CT = "http://schemas.microsoft.com/xrm/2011/Contracts"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"

# One attribute per type the brief names, as the Web API sends it.
_JSON_ATTRS: list[dict[str, Any]] = [
    {"LogicalName": "pre_city", "AttributeType": "String", "MaxLength": 21, "Format": "Text"},
    {"LogicalName": "pre_notes", "AttributeType": "Memo", "MaxLength": 2000, "Format": "TextArea"},
    {"LogicalName": "pre_rate", "AttributeType": "Decimal", "Precision": 4,
     "MinValue": -100000000000.0, "MaxValue": 100000000000.0},
    {"LogicalName": "pre_amount", "AttributeType": "Money", "Precision": 2,
     "MinValue": 0.0, "MaxValue": 922337203685477.0},
    {"LogicalName": "pre_count", "AttributeType": "Integer", "Format": "None",
     "MinValue": -2147483648, "MaxValue": 2147483647},
    {"LogicalName": "pre_ratio", "AttributeType": "Double", "Precision": 2,
     "MinValue": 0.5, "MaxValue": 1000000000},
    {"LogicalName": "pre_flag", "AttributeType": "Boolean", "DefaultValue": False,
     "OptionSet": {"Name": "pre_flag", "IsGlobal": False, "OptionSetType": "Boolean"}},
    {"LogicalName": "pre_tier", "AttributeType": "Picklist", "DefaultFormValue": 2,
     "OptionSet": {"Name": "pre_tier", "IsGlobal": False, "OptionSetType": "Picklist"}},
    {"LogicalName": "pre_kind", "AttributeType": "Picklist", "DefaultFormValue": -1,
     "OptionSet": {"Name": "shared_kind", "IsGlobal": True, "OptionSetType": "Picklist"}},
    {"LogicalName": "pre_when", "AttributeType": "DateTime", "Format": "DateOnly"},
    {"LogicalName": "pre_owner", "AttributeType": "Lookup", "Format": "None", "MinValue": None},
    {"LogicalName": "pre_entityid", "AttributeType": "Uniqueidentifier"},
    # Kinds the brief leaves out record nothing, though the metadata carries values.
    {"LogicalName": "pre_big", "AttributeType": "BigInt", "MinValue": -9223372036854775808,
     "MaxValue": 9223372036854775807},
    {"LogicalName": "pre_state", "AttributeType": "State", "DefaultFormValue": 0,
     "OptionSet": {"Name": "pre_state", "IsGlobal": False, "OptionSetType": "State"}},
]  # fmt: skip

_EXPECTED: dict[str, dict[str, Any]] = {
    "pre_city": {"max_length": 21, "format": "Text"},
    "pre_notes": {"max_length": 2000, "format": "TextArea"},
    "pre_rate": {"precision": 4, "min_value": -100000000000, "max_value": 100000000000},
    "pre_amount": {"precision": 2, "min_value": 0, "max_value": 922337203685477},
    "pre_count": {"format": "None", "min_value": -2147483648, "max_value": 2147483647},
    "pre_ratio": {"precision": 2, "min_value": 0.5, "max_value": 1000000000},
    "pre_flag": {"default_value": False},
    "pre_tier": {"default_value": 2, "option_set_global": False},
    "pre_kind": {"default_value": -1, "option_set_global": True},
    "pre_when": {"format": "DateOnly"},
    "pre_owner": {},
    "pre_entityid": {},
    "pre_big": {},
    "pre_state": {},
}


def _facets(attr: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attr.items() if k in ATTRIBUTE_FACET_FIELDS}


def _by_name(detail: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["logical_name"]: a for a in detail["attributes"]}


def _xml_attr(attr: dict[str, Any]) -> str:
    """The CRM 2011 SOAP shape of one `_JSON_ATTRS` row: each property an element."""
    parts = [
        f"<c:LogicalName>{attr['LogicalName']}</c:LogicalName>",
        f"<c:AttributeType>{attr['AttributeType']}</c:AttributeType>",
    ]
    for prop in ("MaxLength", "Format", "DefaultValue", "DefaultFormValue", "Precision",
                 "MinValue", "MaxValue"):  # fmt: skip
        if attr.get(prop) is None:
            if prop in attr:
                parts.append(f'<c:{prop} xsi:nil="true"/>')
            continue
        value = attr[prop]
        text = str(value).lower() if isinstance(value, bool) else str(value)
        parts.append(f"<c:{prop}>{text}</c:{prop}>")
    option_set = attr.get("OptionSet")
    if option_set:
        parts.append(
            f"<c:OptionSet><c:Name>{option_set['Name']}</c:Name>"
            f"<c:IsGlobal>{str(option_set['IsGlobal']).lower()}</c:IsGlobal></c:OptionSet>"
        )
    return f"<c:AttributeMetadata>{''.join(parts)}</c:AttributeMetadata>"


class _SoapClient:
    def execute_results(self, name: str, parameters: str = "", **_: Any) -> dict[str, ET.Element]:
        attrs = "".join(_xml_attr(a) for a in _JSON_ATTRS)
        entity = ET.fromstring(
            f'<c:EntityMetadata xmlns:c="{_MD}" xmlns:a="{_CT}" xmlns:xsi="{_XSI}">'
            "<c:LogicalName>pre_entity</c:LogicalName>"
            f"<c:Attributes>{attrs}</c:Attributes></c:EntityMetadata>"
        )
        return {"EntityMetadata": entity}


def test_the_web_api_reader_yields_each_types_facets_and_omits_the_rest() -> None:
    detail = _reshape_entity(
        {"LogicalName": "pre_entity", "Attributes": copy.deepcopy(_JSON_ATTRS)}
    )
    got = _by_name(detail)
    assert {n: _facets(a) for n, a in got.items()} == _EXPECTED
    assert not [k for k in got["pre_entityid"] if k in ATTRIBUTE_FACET_FIELDS]


def test_the_crm2011_reader_yields_the_same_facets_in_the_same_units() -> None:
    detail = entity_detail(_SoapClient(), "pre_entity")  # pyright: ignore[reportArgumentType]
    got = _by_name(detail)
    assert {n: _facets(a) for n, a in got.items()} == _EXPECTED
    # An integral Decimal bound is an int on both platforms, never 100000000000.0.
    assert type(got["pre_rate"]["max_value"]) is int
    assert type(got["pre_ratio"]["min_value"]) is float


def test_both_readers_agree_attribute_for_attribute() -> None:
    web = _by_name(_reshape_entity({"LogicalName": "e", "Attributes": copy.deepcopy(_JSON_ATTRS)}))
    soap = _by_name(entity_detail(_SoapClient(), "e"))  # pyright: ignore[reportArgumentType]
    assert {n: _facets(a) for n, a in web.items()} == {n: _facets(a) for n, a in soap.items()}


def test_the_facet_fields_are_the_ones_the_readers_name() -> None:
    assert set(ATTRIBUTE_FACET_FIELDS) == {k for k, _ in legacy_reads.ATTRIBUTE_FACETS} | {
        "option_set_global"
    }


def _attribute_fields(tmp_path: Path, attr: dict[str, Any]) -> dict[str, Any]:
    builder = inventory_extract._Builder(  # pyright: ignore[reportPrivateUsage]
        inventory_extract.Checkpoint(tmp_path), tmp_path, {}, "web-api", True
    )
    detail = {
        "logical_name": "pre_entity", "attributes": [attr], "option_sets": [],
        "relationships": {"one_to_many": [], "many_to_one": [], "many_to_many": []},
    }  # fmt: skip
    builder.metadata([], [], [detail])
    (record,) = [p for p in builder.pending if p.kind == "attribute"]
    return record.fields


def test_the_attribute_record_carries_the_facets_the_reader_returned(tmp_path: Path) -> None:
    base = {"logical_name": "pre_city", "metadata_id": "m", "type": "String", "custom": True,
            "managed": False, "required_level": "None", "option_set": None}  # fmt: skip
    fields = _attribute_fields(tmp_path, {**base, "max_length": 21, "format": "Text",
                                          "default_value": False})  # fmt: skip
    assert fields == {
        "entity": "pre_entity", "logical_name": "pre_city", "type": "String",
        "required_level": "None", "option_set": None,
        "max_length": 21, "format": "Text", "default_value": False,
    }  # fmt: skip


def test_an_attribute_with_no_facets_records_none(tmp_path: Path) -> None:
    base = {"logical_name": "pre_id", "metadata_id": "m", "type": "Uniqueidentifier",
            "custom": False, "managed": True, "required_level": "SystemRequired",
            "option_set": None}  # fmt: skip
    assert set(_attribute_fields(tmp_path, base)) == {
        "entity",
        "logical_name",
        "type",
        "required_level",
        "option_set",
    }


def _manifest() -> dict[str, Any]:
    return json.loads((_FIXTURES / "v9" / "manifest.json").read_text(encoding="utf-8"))


def _attribute(manifest: dict[str, Any]) -> dict[str, Any]:
    return next(r for r in manifest["records"] if r["kind"] == "attribute")


def _faceted(**facets: Any) -> dict[str, Any]:
    manifest = _manifest()
    _attribute(manifest)["fields"].update(facets)
    return manifest


def _verdict(a: dict[str, Any], b: dict[str, Any]) -> tuple[str, list[tuple[str, Any, Any]]]:
    diff = diff_inventories(inventory.parse_manifest(a), inventory.parse_manifest(b))
    entry = next(e for e in diff.entries if e.kind == "attribute" and e.verdict != "unchanged")
    return entry.verdict, [(f.name, f.a, f.b) for f in entry.fields]


def test_a_changed_max_length_is_one_fields_max_length_change() -> None:
    verdict, changes = _verdict(_faceted(max_length=21), _faceted(max_length=100))
    assert (verdict, changes) == ("changed", [("fields.max_length", 21, 100)])


def test_a_facet_the_older_manifest_never_recorded_is_not_a_change() -> None:
    plain = _manifest()
    diff = diff_inventories(
        inventory.parse_manifest(plain),
        inventory.parse_manifest(_faceted(max_length=100, format="Text", precision=2)),
    )
    assert [e for e in diff.entries if e.kind == "attribute" and e.verdict != "unchanged"] == []
    # Reading the other way round gives the same answer.
    back = diff_inventories(
        inventory.parse_manifest(_faceted(max_length=100)), inventory.parse_manifest(plain)
    )
    assert [e for e in back.entries if e.kind == "attribute" and e.verdict != "unchanged"] == []


def test_a_facet_both_sides_hold_still_differs_when_one_is_null() -> None:
    verdict, changes = _verdict(_faceted(default_value=None), _faceted(default_value=False))
    assert (verdict, changes) == ("changed", [("fields.default_value", None, False)])


def test_a_non_facet_field_missing_on_one_side_is_still_a_change() -> None:
    a, b = _manifest(), _manifest()
    _attribute(b)["fields"]["something_new"] = 1
    assert _verdict(a, b) == ("changed", [("fields.something_new", None, 1)])


def test_the_schema_accepts_old_and_new_manifests_and_types_the_facets() -> None:
    validator = jsonschema.Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))
    validator.validate(_manifest())
    validator.validate(
        _faceted(max_length=21, format="Text", default_value=True, precision=2, min_value=0,
                 max_value=1.5, option_set_global=False)
    )  # fmt: skip
    for bad in ({"max_length": "21"}, {"option_set_global": "no"}, {"precision": 2.5}):
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(_faceted(**bad))
