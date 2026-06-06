"""Tests for `crm.core.scaffold.build_table_spec` — pure spec-builder (#90).

`build_table_spec` converts `scaffold table` CLI shorthand into an entity-spec
dict that `apply_spec` can consume directly. No backend, no IO, no network.
"""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core import apply as apply_mod
from crm.core.scaffold import build_table_spec
from crm.utils.d365_backend import D365Error


# ── Helpers ──────────────────────────────────────────────────────────────────


def _entity(spec: dict) -> dict:
    """Pull the single entity dict out of the spec."""
    return spec["entities"][0]


def _attr(spec: dict, idx: int) -> dict:
    return _entity(spec)["attributes"][idx]


# ── Happy path: multi-kind columns ───────────────────────────────────────────


def test_build_table_spec_happy_path():
    spec = build_table_spec(
        display_name="Project",
        prefix="new",
        columns=[
            "Code:string:max_length=50",
            "Notes:memo",
            "Count:integer",
            "Owner:lookup:target_entity=account",
            "Status:picklist:optionset_name=new_status",
        ],
    )
    ent = _entity(spec)
    assert ent["schema_name"] == "new_Project"
    assert ent["display_name"] == "Project"
    assert ent["ownership"] == "UserOwned"
    assert "display_collection_name" not in ent
    assert len(ent["attributes"]) == 5

    code = _attr(spec, 0)
    assert code["kind"] == "string"
    assert code["schema_name"] == "new_Code"
    assert code["display_name"] == "Code"
    assert code["max_length"] == 50

    notes = _attr(spec, 1)
    assert notes["kind"] == "memo"
    assert notes["schema_name"] == "new_Notes"
    assert notes["max_length"] == 2000  # default

    count = _attr(spec, 2)
    assert count["kind"] == "integer"
    assert count["schema_name"] == "new_Count"
    assert "max_length" not in count

    owner = _attr(spec, 3)
    assert owner["kind"] == "lookup"
    assert owner["schema_name"] == "new_Owner"
    assert owner["target_entity"] == "account"

    status = _attr(spec, 4)
    assert status["kind"] == "picklist"
    assert status["schema_name"] == "new_Status"
    assert status["optionset_name"] == "new_status"
    assert "options" not in status


# ── string / memo default max_length + override ──────────────────────────────


def test_string_default_max_length():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Name:string"])
    assert _attr(spec, 0)["max_length"] == 100


def test_memo_default_max_length():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Body:memo"])
    assert _attr(spec, 0)["max_length"] == 2000


def test_string_explicit_max_length_overrides_default():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=200"])
    assert _attr(spec, 0)["max_length"] == 200


def test_memo_explicit_max_length_overrides_default():
    spec = build_table_spec(display_name="T", prefix="x", columns=["Body:memo:max_length=500"])
    assert _attr(spec, 0)["max_length"] == 500


# ── Optional opts: required, description ─────────────────────────────────────


def test_required_opt_parsed_correctly():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Name:string:required=ApplicationRequired"],
    )
    assert _attr(spec, 0)["required"] == "ApplicationRequired"


def test_required_recommended_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Name:string:required=Recommended"],
    )
    assert _attr(spec, 0)["required"] == "Recommended"


def test_required_none_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Name:string:required=None"],
    )
    assert _attr(spec, 0)["required"] == "None"


def test_description_opt_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Name:string:description=A short text field"],
    )
    assert _attr(spec, 0)["description"] == "A short text field"


def test_target_entity_opt_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Owner:lookup:target_entity=systemuser"],
    )
    assert _attr(spec, 0)["target_entity"] == "systemuser"


def test_optionset_name_opt_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Stage:picklist:optionset_name=my_stage"],
    )
    assert _attr(spec, 0)["optionset_name"] == "my_stage"


def test_multiselect_optionset_name_parsed():
    spec = build_table_spec(
        display_name="T", prefix="x",
        columns=["Tags:multiselect:optionset_name=my_tags"],
    )
    assert _attr(spec, 0)["optionset_name"] == "my_tags"


# ── Multi-word display → PascalCase schema ───────────────────────────────────


def test_multiword_entity_pascal_schema():
    spec = build_table_spec(
        display_name="Project Task",
        prefix="new",
        columns=[],
    )
    ent = _entity(spec)
    assert ent["schema_name"] == "new_ProjectTask"
    assert ent["display_name"] == "Project Task"


def test_multiword_column_pascal_schema():
    spec = build_table_spec(
        display_name="T", prefix="new",
        columns=["Project Task:string"],
    )
    assert _attr(spec, 0)["schema_name"] == "new_ProjectTask"
    assert _attr(spec, 0)["display_name"] == "Project Task"


def test_hyphenated_display_pascal():
    """Hyphens are word separators — each word is Pascal-cased."""
    spec = build_table_spec(
        display_name="T", prefix="new",
        columns=["Due-Date:datetime"],
    )
    assert _attr(spec, 0)["schema_name"] == "new_DueDate"


# ── Explicit schema_name honored verbatim ────────────────────────────────────


def test_explicit_schema_name_honored():
    spec = build_table_spec(
        display_name="Widget",
        prefix="new",
        schema_name="contoso_Widget",
        columns=[],
    )
    assert _entity(spec)["schema_name"] == "contoso_Widget"


def test_display_collection_included_when_given():
    spec = build_table_spec(
        display_name="Project",
        prefix="new",
        display_collection="Projects",
        columns=[],
    )
    assert _entity(spec)["display_collection_name"] == "Projects"


def test_display_collection_absent_when_not_given():
    spec = build_table_spec(display_name="Project", prefix="new", columns=[])
    assert "display_collection_name" not in _entity(spec)


def test_ownership_passed_through():
    spec = build_table_spec(
        display_name="T", prefix="x",
        ownership="OrgOwned",
        columns=[],
    )
    assert _entity(spec)["ownership"] == "OrgOwned"


# ── Empty columns list ────────────────────────────────────────────────────────


def test_empty_columns_produces_empty_attributes():
    spec = build_table_spec(display_name="Empty", prefix="new", columns=[])
    assert _entity(spec)["attributes"] == []


# ── Error: unknown kind ───────────────────────────────────────────────────────


def test_error_unknown_kind():
    with pytest.raises(D365Error, match="unknown kind"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:frobnicate"])


# ── Error: missing kind (no second segment) ───────────────────────────────────


def test_error_missing_kind():
    with pytest.raises(D365Error, match="kind"):
        build_table_spec(display_name="T", prefix="x", columns=["NameOnly"])


# ── Error: empty display name ─────────────────────────────────────────────────


def test_error_empty_column_display():
    with pytest.raises(D365Error, match="empty"):
        build_table_spec(display_name="T", prefix="x", columns=[":string"])


def test_error_whitespace_only_column_display():
    with pytest.raises(D365Error, match="empty"):
        build_table_spec(display_name="T", prefix="x", columns=["   :string"])


# ── Error: unknown opt key ────────────────────────────────────────────────────


def test_error_unknown_opt_key():
    with pytest.raises(D365Error, match="unknown opt"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:bogus=hi"])


# ── Error: malformed opt (no '=') ────────────────────────────────────────────


def test_error_malformed_opt_no_equals():
    with pytest.raises(D365Error, match="malformed"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:noequalssign"])


# ── Error: non-int max_length ─────────────────────────────────────────────────


def test_error_non_int_max_length():
    with pytest.raises(D365Error, match="max_length"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=abc"])


def test_error_zero_max_length():
    with pytest.raises(D365Error, match="max_length"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=0"])


def test_error_negative_max_length():
    with pytest.raises(D365Error, match="max_length"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:max_length=-5"])


# ── Error: bad required value ─────────────────────────────────────────────────


def test_error_bad_required_value():
    with pytest.raises(D365Error, match="required"):
        build_table_spec(display_name="T", prefix="x", columns=["Name:string:required=bad"])


# ── Error: lookup without target_entity ──────────────────────────────────────


def test_error_lookup_without_target_entity():
    with pytest.raises(D365Error, match="target_entity"):
        build_table_spec(display_name="T", prefix="x", columns=["Owner:lookup"])


# ── Error: picklist without optionset_name ────────────────────────────────────


def test_error_picklist_without_optionset_name():
    with pytest.raises(D365Error, match="optionset_name"):
        build_table_spec(display_name="T", prefix="x", columns=["Stage:picklist"])


# ── Error: multiselect without optionset_name ─────────────────────────────────


def test_error_multiselect_without_optionset_name():
    with pytest.raises(D365Error, match="optionset_name"):
        build_table_spec(display_name="T", prefix="x", columns=["Tags:multiselect"])


# ── No-drift cross-check: validate_spec accepts the output ───────────────────


def test_no_drift_validate_spec_accepts_multi_kind_output():
    """apply.validate_spec must not raise for a representative multi-kind spec."""
    spec = build_table_spec(
        display_name="Contoso Project",
        prefix="contoso",
        display_collection="Contoso Projects",
        columns=[
            "Code:string",
            "Notes:memo",
            "Count:integer",
            "Budget:money",
            "Active:boolean",
            "DueDate:datetime",
            "Owner:lookup:target_entity=account",
            "Status:picklist:optionset_name=contoso_status",
        ],
    )
    # Must not raise — this is the drift guard.
    apply_mod.validate_spec(spec)


# ── Error: max_length on non-string/memo kinds ────────────────────────────────


def test_error_max_length_on_money():
    with pytest.raises(D365Error, match="max_length is only valid"):
        build_table_spec(display_name="T", prefix="x", columns=["Amount:money:max_length=10"])


def test_error_max_length_on_integer():
    with pytest.raises(D365Error, match="max_length is only valid"):
        build_table_spec(display_name="T", prefix="x", columns=["Count:integer:max_length=5"])
