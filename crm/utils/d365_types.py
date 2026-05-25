"""Typed Web API response shapes consumed by crm.core.

These TypedDicts cover only the fields the codebase actually reads — they
are not a complete model of the Dataverse Web API. Use TypedDict.__total__
False for response shapes where the server may omit fields depending on
the operation.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypedDict, TypeVar, Union

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


class BatchOperation(TypedDict, total=False):
    """One operation inside a $batch request.

    `method` and `url` are required. `body` is required on POST/PATCH and
    rejected on GET/DELETE. `headers` is optional (per-op overrides such
    as `If-Match` or `MSCRMCallerID`). `content_id` is optional;
    consumed only inside changesets for `$<n>` back-references.
    """

    method: str
    url: str
    body: dict[str, Any]
    headers: dict[str, str]
    content_id: Union[str, int]


class BatchResult(TypedDict):
    """One result inside a $batch response, aligned to input order."""

    method: str
    url: str
    status: int
    headers: dict[str, str]
    body: Union[dict[str, Any], str, None]
    error: Union[str, None]


class AsyncOperationRow(TypedDict, total=False):
    """Subset of asyncoperation fields the CLI reads + displays."""

    asyncoperationid: str
    name: str
    messagename: str
    statecode: int
    statuscode: int
    createdon: str
    startedon: str
    completedon: str
    _ownerid_value: str
    errorcode: int
    message: str
    friendlymessage: str


AttributeKind = Literal[
    "string", "memo", "integer", "bigint", "decimal", "double", "money",
    "boolean", "datetime", "picklist", "multiselect", "lookup", "image", "file",
]


class AddAttributeResult(TypedDict, total=False):
    """Producers always include every key; values may be None where the
    server response was unparseable or the read-back failed. Reflect
    that with `str | None` for fields populated from the response.
    """

    created: bool
    entity: str
    schema_name: str
    logical_name: str
    attribute_type: str | None
    attribute_logical_name: str | None
    metadata_id_url: str | None
    solution: str | None
    published: bool
    attribute_lookup_error: str


class CreateRelationshipResult(TypedDict, total=False):
    created: bool
    kind: Literal["OneToMany", "ManyToMany"]
    schema_name: str
    referenced_entity: str
    referencing_entity: str
    referencing_attribute: str | None
    intersect_entity: str | None
    relationship_id: str | None
    metadata_id_url: str | None
    solution: str | None
    published: bool
    relationship_lookup_error: str


class OptionSetRow(TypedDict, total=False):
    Name: str
    DisplayName: LabelPayload
    IsCustomOptionSet: bool
    IsGlobal: bool
    IsManaged: bool


class OptionSetCreateResult(TypedDict, total=False):
    created: bool
    name: str
    metadata_id_url: str | None
    solution: str | None
    published: bool
    optionset_lookup_error: str


# Raw response unions used at the wire boundary. Backend methods widen to these;
# core/* callers narrow before use (helper: `as_dict` in d365_backend.py).
JsonValue = Union[dict[str, Any], list[Any], str, int, float, bool, None]
JsonObject = dict[str, Any]
