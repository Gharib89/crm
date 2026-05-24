"""Typed Web API response shapes consumed by crm.core.

These TypedDicts cover only the fields the codebase actually reads — they
are not a complete model of the Dataverse Web API. Use TypedDict.__total__
False for response shapes where the server may omit fields depending on
the operation.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/
"""

from __future__ import annotations

from typing import Any, Dict, Generic, List, TypedDict, TypeVar, Union

T = TypeVar("T")


class WhoAmIResponse(TypedDict, total=False):
    """Response from WhoAmI() — every field is optional from the consumer's POV."""

    UserId: str
    BusinessUnitId: str
    OrganizationId: str


class LocalizedLabel(TypedDict, total=False):
    Label: str
    LanguageCode: int


class LabelPayload(TypedDict, total=False):
    LocalizedLabels: list[LocalizedLabel]
    UserLocalizedLabel: LocalizedLabel


class EntityDefinition(TypedDict, total=False):
    LogicalName: str
    EntitySetName: str
    SchemaName: str
    MetadataId: str
    IsCustomEntity: bool
    DisplayName: LabelPayload


class AttributeDefinition(TypedDict, total=False):
    LogicalName: str
    SchemaName: str
    AttributeType: str
    IsCustomAttribute: bool


class OptionMetadata(TypedDict, total=False):
    Value: int
    Label: LabelPayload


class OptionSetPayload(TypedDict, total=False):
    Options: list[OptionMetadata]


class OptionSetResponse(TypedDict, total=False):
    LogicalName: str
    OptionSet: OptionSetPayload
    GlobalOptionSet: OptionSetPayload


class SolutionRow(TypedDict, total=False):
    solutionid: str
    uniquename: str
    friendlyname: str
    version: str
    ismanaged: bool
    installedon: str


class SolutionComponent(TypedDict, total=False):
    componenttype: int
    objectid: str
    rootcomponentbehavior: int


class WorkflowRow(TypedDict, total=False):
    workflowid: str
    name: str
    category: int
    primaryentity: str
    statecode: int
    statuscode: int
    ondemand: bool
    type: int


class RelationshipRow(TypedDict, total=False):
    SchemaName: str
    ReferencedEntity: str
    ReferencingEntity: str
    ReferencingAttribute: str
    Entity1LogicalName: str
    Entity2LogicalName: str
    IntersectEntityName: str


class ODataCollection(TypedDict, Generic[T], total=False):
    """Generic OData collection envelope: `{ "value": [...], "@odata.nextLink": "..." }`."""

    value: list[T]


# Raw response unions used at the wire boundary. Backend methods widen to these;
# core/* callers narrow before use (helper: `as_dict` in d365_backend.py).
JsonValue = Union[Dict[str, Any], List[Any], str, int, float, bool, None]
JsonObject = Dict[str, Any]
