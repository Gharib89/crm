"""Single source of truth for D365 metadata constraint vocabularies.

Attribute / relationship / entity constraint rules — valid ``RequiredLevel``,
``FormatName``, ``CascadeType``, ownership models, precision ranges, and the
``kind`` ↔ ``@odata.type`` cast ↔ ``AttributeTypeName`` mappings — were
duplicated across five ``crm.core`` modules (``metadata_attrs``,
``metadata_update``, ``relationships``, ``metadata``, ``scaffold``) plus
``export_spec``, each with a hand-maintained "keep these in sync" comment.
Because the create path and the update path each carried their own copy, a rule
tightened on create silently kept the old value on update — a divergence bug
class that nothing warned about.

This module hoists those rules into one place. The two write *mechanisms*
(POST-build in ``metadata_attrs`` vs PUT retrieve-merge-write in
``metadata_update``) stay separate; only the *rules* converge here.

Per-constraint ``validate_*`` helpers own both the membership rule and the
rejection message, keeping the canonical ``"<subject> must be one of
{sorted(...)}."`` wording. ``subject`` lets each call site name the offending
input (``"required"``, ``"lookup_required"``, ``"cascade Assign"``, …) without
forking the message; ``echo=True`` appends ``", got <value>"`` where a call site
echoed the rejected value. All raise :class:`D365Error`, the universal error
type. Create-path builders keep their inline ``@odata.type`` cast literals — the
cast is a fixed protocol constant (wrong → loud create failure), not a
divergence-prone rule — so only the value-sets, precision ranges, and the
``cast → kind`` / ``type_name → kind`` maps live here.
"""

from __future__ import annotations

from dataclasses import dataclass

from crm.utils.d365_backend import D365Error

# ── value vocabularies ──────────────────────────────────────────────────────
REQUIRED_LEVELS: frozenset[str] = frozenset(
    {"None", "Recommended", "ApplicationRequired"}
)
OWNERSHIP_TYPES: frozenset[str] = frozenset({"UserOwned", "OrganizationOwned"})
# String formats add_attribute can create. Public: export_spec filters live
# FormatName values to this creatable set (a Json / RichText format is dropped).
STRING_FORMATS: frozenset[str] = frozenset(
    {"Text", "Email", "Url", "Phone", "TextArea", "TickerSymbol", "VersionNumber"}
)
DATETIME_FORMATS: frozenset[str] = frozenset({"DateOnly", "DateAndTime"})
# DateTimeBehavior values add_attribute can set on a datetime kind. Omitting the
# behavior leaves it off the payload so the server default (UserLocal) applies.
DATETIME_BEHAVIORS: frozenset[str] = frozenset(
    {"UserLocal", "DateOnly", "TimeZoneIndependent"}
)
CASCADE_TYPES: frozenset[str] = frozenset(
    {"NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"}
)
CASCADE_KEYS: frozenset[str] = frozenset(
    {"Assign", "Delete", "Merge", "Reparent", "Share", "Unshare", "RollupView"}
)
MENU_BEHAVIORS: frozenset[str] = frozenset(
    {"UseLabel", "UseCollectionName", "DoNotDisplay"}
)


# ── canonical kind table ────────────────────────────────────────────────────
@dataclass(frozen=True)
class KindInfo:
    """The protocol constants for one attribute ``kind``.

    ``cast`` is the ``@odata.type`` discriminator (no leading ``#``);
    ``type_name`` is the ``AttributeTypeName.Value`` discriminator a live read
    returns; ``precision_range`` is the inclusive ``(lo, hi)`` for the numeric
    kinds that carry a ``Precision`` (``None`` for every other kind).
    """

    cast: str
    type_name: str
    precision_range: tuple[int, int] | None = None


# The 14 attribute kinds add_attribute accepts (the 13 builder-backed kinds plus
# the special-cased "lookup"). Casts mirror the create builders' inline literals;
# type_names mirror the live AttributeTypeName.Value discriminators.
KINDS: dict[str, KindInfo] = {
    "string": KindInfo("Microsoft.Dynamics.CRM.StringAttributeMetadata", "StringType"),
    "memo": KindInfo("Microsoft.Dynamics.CRM.MemoAttributeMetadata", "MemoType"),
    "integer": KindInfo("Microsoft.Dynamics.CRM.IntegerAttributeMetadata", "IntegerType"),
    "bigint": KindInfo("Microsoft.Dynamics.CRM.BigIntAttributeMetadata", "BigIntType"),
    "decimal": KindInfo("Microsoft.Dynamics.CRM.DecimalAttributeMetadata", "DecimalType", (0, 10)),
    "double": KindInfo("Microsoft.Dynamics.CRM.DoubleAttributeMetadata", "DoubleType", (0, 5)),
    "money": KindInfo("Microsoft.Dynamics.CRM.MoneyAttributeMetadata", "MoneyType", (0, 4)),
    "boolean": KindInfo("Microsoft.Dynamics.CRM.BooleanAttributeMetadata", "BooleanType"),
    "datetime": KindInfo("Microsoft.Dynamics.CRM.DateTimeAttributeMetadata", "DateTimeType"),
    "picklist": KindInfo("Microsoft.Dynamics.CRM.PicklistAttributeMetadata", "PicklistType"),
    "multiselect": KindInfo(
        "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata",
        "MultiSelectPicklistType",
    ),
    "lookup": KindInfo("Microsoft.Dynamics.CRM.LookupAttributeMetadata", "LookupType"),
    "image": KindInfo("Microsoft.Dynamics.CRM.ImageAttributeMetadata", "ImageType"),
    "file": KindInfo("Microsoft.Dynamics.CRM.FileAttributeMetadata", "FileType"),
}

_CAST_TO_KIND: dict[str, str] = {info.cast: kind for kind, info in KINDS.items()}
_TYPE_NAME_TO_KIND: dict[str, str] = {info.type_name: kind for kind, info in KINDS.items()}


def kind_for_cast(cast: str) -> str | None:
    """Return the attribute ``kind`` for an ``@odata.type`` cast, or ``None``.

    The leading ``#`` of a live ``@odata.type`` value is tolerated.
    """
    return _CAST_TO_KIND.get(cast.lstrip("#"))


def kind_for_type_name(type_name: str) -> str | None:
    """Return the attribute ``kind`` for an ``AttributeTypeName.Value``, or ``None``.

    ``None`` means a system kind ``apply`` cannot create (Owner, State, Status,
    Uniqueidentifier, …); the caller skips it.
    """
    return _TYPE_NAME_TO_KIND.get(type_name)


# ── validators ──────────────────────────────────────────────────────────────
def _reject(subject: str, allowed: frozenset[str], value: str, echo: bool) -> D365Error:
    tail = f", got {value!r}" if echo else ""
    return D365Error(f"{subject} must be one of {sorted(allowed)}{tail}.")


def validate_required(value: str, *, subject: str = "required", echo: bool = False) -> None:
    """Validate a ``RequiredLevel`` value (None / Recommended / ApplicationRequired)."""
    if value not in REQUIRED_LEVELS:
        raise _reject(subject, REQUIRED_LEVELS, value, echo)


def validate_ownership(value: str, *, subject: str = "ownership", echo: bool = False) -> None:
    """Validate an ownership model (UserOwned / OrganizationOwned)."""
    if value not in OWNERSHIP_TYPES:
        raise _reject(subject, OWNERSHIP_TYPES, value, echo)


def validate_menu_behavior(
    value: str, *, subject: str = "menu_behavior", echo: bool = False
) -> None:
    """Validate an associated-menu behavior."""
    if value not in MENU_BEHAVIORS:
        raise _reject(subject, MENU_BEHAVIORS, value, echo)


def validate_cascade(value: str, *, subject: str = "cascade", echo: bool = False) -> None:
    """Validate a single cascade action value."""
    if value not in CASCADE_TYPES:
        raise _reject(subject, CASCADE_TYPES, value, echo)


_FORMAT_SETS: dict[str, frozenset[str]] = {
    "string": STRING_FORMATS,
    "datetime": DATETIME_FORMATS,
}


def validate_format(kind: str, value: str, *, subject: str = "format_name") -> None:
    """Validate a ``FormatName`` value for a ``string`` or ``datetime`` kind.

    ``kind`` must be ``"string"`` or ``"datetime"`` — the only kinds with a
    user-settable format. Any other kind is a programming error.
    """
    allowed = _FORMAT_SETS.get(kind)
    if allowed is None:
        raise D365Error(f"format validation is not defined for kind {kind!r}.")
    if value not in allowed:
        raise D365Error(f"{subject} for {kind} must be one of {sorted(allowed)}.")


def validate_behavior(value: str, *, subject: str = "behavior", echo: bool = False) -> None:
    """Validate a ``DateTimeBehavior`` value (UserLocal / DateOnly / TimeZoneIndependent)."""
    if value not in DATETIME_BEHAVIORS:
        raise _reject(subject, DATETIME_BEHAVIORS, value, echo)


def validate_precision(kind: str, value: int, *, subject: str = "precision") -> None:
    """Validate a ``Precision`` value against ``kind``'s inclusive range.

    Only the numeric kinds with a ``precision_range`` (decimal / double / money)
    accept a precision; calling this for any other kind is a programming error.
    ``subject`` names the offending input (``"precision"`` on the create path,
    ``"--precision"`` on the CLI update path), mirroring :func:`validate_format`.
    """
    info = KINDS.get(kind)
    precision_range = info.precision_range if info else None
    if precision_range is None:
        raise D365Error(f"{subject} is not valid for kind {kind!r}.")
    lo, hi = precision_range
    if not lo <= value <= hi:
        raise D365Error(f"{subject} for {kind} must be in [{lo}, {hi}].")
