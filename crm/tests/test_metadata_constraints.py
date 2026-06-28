"""Unit tests for crm.core.metadata_constraints — the single constraint seam.

This is the payoff of #293: the constraint vocabulary is now a directly
testable surface (accept/reject per validator, KINDS round-trips) instead of
being asserted only indirectly through the create and update code paths.
"""
# pyright: basic

from __future__ import annotations

import pytest

from crm.core import metadata_constraints as mc
from crm.utils.d365_backend import D365Error



def test_known_membership():
    assert "ApplicationRequired" in mc.REQUIRED_LEVELS
    assert "OrganizationOwned" in mc.OWNERSHIP_TYPES
    assert "TickerSymbol" in mc.STRING_FORMATS
    assert "DateOnly" in mc.DATETIME_FORMATS
    assert "TimeZoneIndependent" in mc.DATETIME_BEHAVIORS
    assert "RemoveLink" in mc.CASCADE_TYPES
    assert "RollupView" in mc.CASCADE_KEYS
    assert "UseCollectionName" in mc.MENU_BEHAVIORS


# ── validators: accept the valid, reject the invalid with canonical wording ──

def test_validate_required_accepts_and_rejects():
    for v in mc.REQUIRED_LEVELS:
        mc.validate_required(v)  # no raise
    with pytest.raises(D365Error, match=r"required must be one of \["):
        mc.validate_required("Bogus")


def test_validate_required_subject_overrides_prefix():
    with pytest.raises(D365Error, match="lookup_required must be one of"):
        mc.validate_required("Bogus", subject="lookup_required")


def test_validate_required_echo_appends_got():
    with pytest.raises(D365Error, match=r"required must be one of \[.*\], got 'Bogus'\."):
        mc.validate_required("Bogus", echo=True)


def test_validate_ownership_accepts_and_rejects():
    mc.validate_ownership("UserOwned")
    mc.validate_ownership("OrganizationOwned")
    with pytest.raises(D365Error, match="ownership must be one of"):
        mc.validate_ownership("Nobody")


def test_validate_menu_behavior():
    for v in mc.MENU_BEHAVIORS:
        mc.validate_menu_behavior(v)
    with pytest.raises(D365Error, match="menu_behavior must be one of"):
        mc.validate_menu_behavior("Nope")
    with pytest.raises(D365Error, match="entity1_menu_behavior must be one of"):
        mc.validate_menu_behavior("Nope", subject="entity1_menu_behavior")


def test_validate_cascade():
    for v in mc.CASCADE_TYPES:
        mc.validate_cascade(v)
    with pytest.raises(D365Error, match="cascade_assign must be one of"):
        mc.validate_cascade("Wrong", subject="cascade_assign")
    with pytest.raises(D365Error, match="cascade Assign must be one of"):
        mc.validate_cascade("Wrong", subject="cascade Assign")


def test_validate_format_string_and_datetime():
    mc.validate_format("string", "Email")
    mc.validate_format("datetime", "DateOnly")
    with pytest.raises(D365Error, match="format_name for string must be one of"):
        mc.validate_format("string", "Json")
    with pytest.raises(D365Error, match="format_name for datetime must be one of"):
        mc.validate_format("datetime", "Bogus")


def test_validate_format_subject_overrides():
    with pytest.raises(D365Error, match="--format for string must be one of"):
        mc.validate_format("string", "Json", subject="--format")


def test_validate_format_unknown_kind_raises():
    with pytest.raises(D365Error):
        mc.validate_format("integer", "anything")


def test_validate_behavior_accepts_and_rejects():
    for v in mc.DATETIME_BEHAVIORS:
        mc.validate_behavior(v)  # no raise
    with pytest.raises(D365Error, match=r"behavior must be one of \["):
        mc.validate_behavior("Bogus")


# ── precision ──

@pytest.mark.parametrize("kind,lo,hi", [
    ("decimal", 0, 10), ("double", 0, 5), ("money", 0, 4),
])
def test_validate_precision_bounds(kind, lo, hi):
    mc.validate_precision(kind, lo)
    mc.validate_precision(kind, hi)
    with pytest.raises(D365Error, match=f"precision for {kind} must be in"):
        mc.validate_precision(kind, hi + 1)
    with pytest.raises(D365Error, match=f"precision for {kind} must be in"):
        mc.validate_precision(kind, lo - 1)


def test_validate_precision_subject_overrides_prefix():
    with pytest.raises(D365Error, match="--precision for decimal must be in"):
        mc.validate_precision("decimal", 99, subject="--precision")


def test_validate_precision_non_precision_kind_raises():
    # integer/string have no precision_range — calling validate_precision is a
    # programming error, surfaced as D365Error rather than a silent pass.
    with pytest.raises(D365Error):
        mc.validate_precision("integer", 2)
    with pytest.raises(D365Error):
        mc.validate_precision("string", 2)


# ── KINDS table + the two lookups round-trip ──

def test_kinds_has_fourteen_kinds():
    assert len(mc.KINDS) == 14


def test_kind_round_trips_through_cast_and_type_name():
    for kind, info in mc.KINDS.items():
        assert mc.kind_for_cast(info.cast) == kind
        assert mc.kind_for_type_name(info.type_name) == kind


def test_kind_for_cast_tolerates_leading_hash():
    info = mc.KINDS["string"]
    assert mc.kind_for_cast(info.cast) == "string"
    assert mc.kind_for_cast("#" + info.cast) == "string"


def test_kind_for_cast_and_type_name_unknown_return_none():
    assert mc.kind_for_cast("Microsoft.Dynamics.CRM.NopeAttributeMetadata") is None
    assert mc.kind_for_type_name("NopeType") is None
    assert mc.kind_for_type_name("OwnerType") is None  # a system kind apply can't create


def test_casts_follow_the_protocol_shape():
    for info in mc.KINDS.values():
        assert info.cast.startswith("Microsoft.Dynamics.CRM.")
        assert info.cast.endswith("AttributeMetadata")


def test_only_numeric_kinds_carry_precision_range():
    precision_kinds = {k for k, info in mc.KINDS.items() if info.precision_range is not None}
    assert precision_kinds == {"decimal", "double", "money"}


def test_validate_schema_name_accepts_prefixed_name():
    mc.validate_schema_name("contoso_Code")  # has underscore → no raise


@pytest.mark.parametrize("bad", ["", "Code", "noprefix"])
def test_validate_schema_name_rejects_unprefixed(bad):
    with pytest.raises(D365Error, match="must include a publisher prefix"):
        mc.validate_schema_name(bad)


def test_validate_schema_name_subject_and_example_in_message():
    with pytest.raises(D365Error, match=r"lookup_schema must include a publisher prefix, e.g. 'new_x'"):
        mc.validate_schema_name("nope", subject="lookup_schema", example="new_x")
