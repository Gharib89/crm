"""Read-only metadata and component reads for a CRM 2011 org (ADR 0031).

Builds on :class:`crm.core.legacy.LegacyClient`. Metadata comes from the three
allowlisted metadata ``Execute`` requests; components (web resources, plug-ins,
processes, forms, views, solutions) come from FetchXML ``RetrieveMultiple``,
paged to the last page. A failed entity or kind is recorded and the run goes on
(continue-and-report, ADR 0007).
"""

from __future__ import annotations

import base64
import binascii
import io
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from crm.core.legacy import LegacyClient, entity_records, parameter
from crm.utils.d365_backend import D365Error, classify_d365_error

_MD = {
    "a": "http://schemas.microsoft.com/xrm/2011/Contracts",
    "c": "http://schemas.microsoft.com/xrm/2011/Metadata",
}
_XSD = "http://www.w3.org/2001/XMLSchema"
_SERIALIZATION = "http://schemas.microsoft.com/2003/10/Serialization/"
_NO_ID = "00000000-0000-0000-0000-000000000000"
_AS_IF_PUBLISHED = parameter("RetrieveAsIfPublished", "boolean", _XSD, "false")


def _filters(value: str) -> str:
    return parameter("EntityFilters", "EntityFilters", _MD["c"], value)


def _flag(el: ET.Element, path: str) -> bool:
    return el.findtext(path, "", _MD) == "true"


#: Attribute record key -> the `AttributeMetadata` properties that carry it, first one present
#: winning; the Web API and the SOAP service name them alike. A type defines only its own
#: (Boolean `DefaultValue`, Picklist `DefaultFormValue`), so an attribute omits the rest.
ATTRIBUTE_FACETS: tuple[tuple[str, str], ...] = (
    ("max_length", "MaxLength"),
    ("format", "Format"),
    ("default_value", "DefaultValue"),
    ("default_value", "DefaultFormValue"),
    ("precision", "Precision"),
    ("min_value", "MinValue"),
    ("max_value", "MaxValue"),
)


_FORMAT_TYPES = frozenset(("String", "Memo", "Integer", "DateTime"))


def facet_props(attr_type: str) -> Iterator[tuple[str, str]]:
    """`ATTRIBUTE_FACETS` for an attribute of *attr_type*: the Web API also sends a
    meaningless ``Format`` on a Lookup that CRM 2011 does not, so only the types that
    define one record it.
    """
    for key, prop in ATTRIBUTE_FACETS:
        if key != "format" or attr_type in _FORMAT_TYPES:
            yield key, prop


def facet_number(value: float) -> float:
    """An integral float as an int, so ``1.0`` and ``1`` are one value on every platform."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _facet_value(text: str) -> object:
    if text in ("true", "false"):
        return text == "true"
    for parse in (int, float):
        try:
            return facet_number(parse(text))
        except ValueError:
            continue
    return text


def _facets(
    attr: ET.Element, option_set: ET.Element | None, set_name: str | None
) -> dict[str, Any]:
    """The type facets *attr* defines; a nil or absent property is not recorded."""
    out: dict[str, Any] = {}
    for key, prop in facet_props(attr.findtext("c:AttributeType", "", _MD)):
        text = attr.findtext(f"c:{prop}", "", _MD)
        if text and key not in out:
            out[key] = _facet_value(text)
    if option_set is not None and set_name:
        out["option_set_global"] = _flag(option_set, "c:IsGlobal")
    return out


def _result(results: dict[str, ET.Element], key: str, request: str) -> ET.Element:
    if key not in results:
        raise D365Error(f"{request}: the response carries no {key}")
    return results[key]


def _option_set(el: ET.Element) -> dict[str, Any]:
    options = el.findall("c:Options/c:OptionMetadata", _MD)
    for tag in ("FalseOption", "TrueOption"):
        boolean = el.find(f"c:{tag}", _MD)
        if boolean is not None and boolean.find("c:Value", _MD) is not None:
            options.append(boolean)
    return {
        "name": el.findtext("c:Name", "", _MD),
        "metadata_id": el.findtext("c:MetadataId", None, _MD),
        "global": _flag(el, "c:IsGlobal"),
        "custom": _flag(el, "c:IsCustomOptionSet"),
        "managed": _flag(el, "c:IsManaged"),
        "type": el.findtext("c:OptionSetType", "", _MD),
        "options": [
            {
                "value": int(o.findtext("c:Value", "0", _MD)),
                "label": o.findtext("c:Label/a:UserLocalizedLabel/a:Label", None, _MD),
            }
            for o in options
        ],
    }


def list_entities(client: LegacyClient) -> list[dict[str, Any]]:
    """Every entity in server order: logical name, display label, custom flag, type code.

    `display_name` is None for an entity with no label (intersect and internal
    entities): it has no UI, so no form, view or ribbon.
    """
    return _entity_rows(_entity_metadata(client))


def list_entities_with_primary_ids(
    client: LegacyClient,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """`list_entities` plus each entity's primary id attribute by logical name, from one read.

    The primary id is the metadata's own (never `<name>id`: `systemform` has
    none), which the usage probe needs to read one record per entity.
    """
    elements = _entity_metadata(client)
    primary_ids = {
        e.findtext("c:LogicalName", "", _MD): e.findtext("c:PrimaryIdAttribute", "", _MD)
        for e in elements
    }
    return _entity_rows(elements), primary_ids


def _entity_rows(elements: list[ET.Element]) -> list[dict[str, Any]]:
    return [
        {
            "logical_name": e.findtext("c:LogicalName", "", _MD),
            "display_name": e.findtext("c:DisplayName/a:UserLocalizedLabel/a:Label", None, _MD),
            "custom": _flag(e, "c:IsCustomEntity"),
            "object_type_code": int(e.findtext("c:ObjectTypeCode", "0", _MD)),
            "metadata_id": e.findtext("c:MetadataId", None, _MD),
            "managed": _flag(e, "c:IsManaged"),
        }
        for e in elements
    ]


def _entity_metadata(client: LegacyClient) -> list[ET.Element]:
    results = client.execute_results("RetrieveAllEntities", _filters("Entity") + _AS_IF_PUBLISHED)
    return _result(results, "EntityMetadata", "RetrieveAllEntities").findall(
        "c:EntityMetadata", _MD
    )


def list_option_sets(client: LegacyClient) -> list[dict[str, Any]]:
    """Every global option set with its options."""
    results = client.execute_results("RetrieveAllOptionSets", _AS_IF_PUBLISHED)
    return [
        _option_set(el)
        for el in _result(results, "OptionSetMetadata", "RetrieveAllOptionSets").findall(
            "c:OptionSetMetadataBase", _MD
        )
    ]


def _one_to_many(rel: ET.Element) -> dict[str, Any]:
    return {
        "schema_name": rel.findtext("c:SchemaName", "", _MD),
        "metadata_id": rel.findtext("c:MetadataId", None, _MD),
        "custom": _flag(rel, "c:IsCustomRelationship"),
        "managed": _flag(rel, "c:IsManaged"),
        "referenced_entity": rel.findtext("c:ReferencedEntity", "", _MD),
        "referenced_attribute": rel.findtext("c:ReferencedAttribute", "", _MD),
        "referencing_entity": rel.findtext("c:ReferencingEntity", "", _MD),
        "referencing_attribute": rel.findtext("c:ReferencingAttribute", "", _MD),
    }


def _many_to_many(rel: ET.Element) -> dict[str, Any]:
    return {
        "schema_name": rel.findtext("c:SchemaName", "", _MD),
        "metadata_id": rel.findtext("c:MetadataId", None, _MD),
        "custom": _flag(rel, "c:IsCustomRelationship"),
        "managed": _flag(rel, "c:IsManaged"),
        "entity1": rel.findtext("c:Entity1LogicalName", "", _MD),
        "entity2": rel.findtext("c:Entity2LogicalName", "", _MD),
        "intersect_entity": rel.findtext("c:IntersectEntityName", "", _MD),
    }


def _retrieve_entity(client: LegacyClient, name: str, filters: str) -> ET.Element:
    """RetrieveEntity *name* with the EntityFilters *filters*; its EntityMetadata."""
    results = client.execute_results(
        "RetrieveEntity",
        _filters(filters)
        + parameter("LogicalName", "string", _XSD, name)
        + parameter("MetadataId", "guid", _SERIALIZATION, _NO_ID)
        + _AS_IF_PUBLISHED,
    )
    return _result(results, "EntityMetadata", "RetrieveEntity")


def entity_detail(client: LegacyClient, name: str) -> dict[str, Any]:
    """One entity's attributes, relationships and the option sets its attributes use."""
    entity = _retrieve_entity(client, name, "Attributes Relationships")
    attributes: list[dict[str, Any]] = []
    option_sets: dict[str, dict[str, Any]] = {}
    for attr in entity.findall("c:Attributes/c:AttributeMetadata", _MD):
        option_set = attr.find("c:OptionSet", _MD)
        set_name = option_set.findtext("c:Name", None, _MD) if option_set is not None else None
        if option_set is not None and set_name and set_name not in option_sets:
            option_sets[set_name] = _option_set(option_set)
        attributes.append(
            {
                "logical_name": attr.findtext("c:LogicalName", "", _MD),
                "metadata_id": attr.findtext("c:MetadataId", None, _MD),
                "type": attr.findtext("c:AttributeType", "", _MD),
                "custom": _flag(attr, "c:IsCustomAttribute"),
                "managed": _flag(attr, "c:IsManaged"),
                "required_level": attr.findtext("c:RequiredLevel/a:Value", None, _MD),
                "option_set": set_name,
                **_facets(attr, option_set, set_name),
            }
        )
    return {
        "logical_name": entity.findtext("c:LogicalName", "", _MD),
        "metadata_id": entity.findtext("c:MetadataId", None, _MD),
        "custom": _flag(entity, "c:IsCustomEntity"),
        "managed": _flag(entity, "c:IsManaged"),
        "object_type_code": int(entity.findtext("c:ObjectTypeCode", "0", _MD)),
        "primary_id_attribute": entity.findtext("c:PrimaryIdAttribute", None, _MD),
        "primary_name_attribute": entity.findtext("c:PrimaryNameAttribute", None, _MD),
        "attributes": attributes,
        "relationships": {
            "one_to_many": [
                _one_to_many(r)
                for r in entity.findall(
                    "c:OneToManyRelationships/c:OneToManyRelationshipMetadata", _MD
                )
            ],
            "many_to_one": [
                _one_to_many(r)
                for r in entity.findall(
                    "c:ManyToOneRelationships/c:OneToManyRelationshipMetadata", _MD
                )
            ],
            "many_to_many": [
                _many_to_many(r)
                for r in entity.findall(
                    "c:ManyToManyRelationships/c:ManyToManyRelationshipMetadata", _MD
                )
            ],
        },
        "option_sets": list(option_sets.values()),
    }


def connection_wide(exc: D365Error) -> bool:
    """A 401 or no HTTP response fails every later read too, so it is never per-item."""
    category, _ = classify_d365_error(exc.status, exc.code, str(exc))
    return category in ("auth_failed", "transport_error")


def read_entities(
    client: LegacyClient, names: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read each named entity; a failed one is reported and the rest still read."""
    entities: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for name in names:
        try:
            entities.append(entity_detail(client, name))
        except D365Error as exc:
            if connection_wide(exc):
                raise
            errors.append({"entity": name, "error": str(exc), "code": exc.code})
    return entities, errors


@dataclass(frozen=True)
class Kind:
    """One component kind: the entity to page, its columns and how a scope narrows it.

    *payload* names the column holding the large body (a web resource's content,
    a form's XML) that ``--out`` writes to a file with extension *suffix*.
    *conditions* are FetchXML conditions every read of the kind carries.

    Solution membership (`ComponentScope.solution_id`): *member_via* is the column whose
    value is a ``solutioncomponent`` row's ``objectid``, the kind's own *key* or
    its parent's id. A kind with a *carried_type* is carried by its entity: a
    member is held directly (a row of that component type) or through its entity.

    Entity scope (`ComponentScope.entity_code`): *entity_attr* holds the entity's object
    type code, reached through the link-entities *entity_hops*, each joined on
    its own ``<name>id`` column. A kind with no *entity_attr* names no entity.
    """

    entity: str
    attributes: tuple[str, ...]
    key: str
    payload: str | None = None
    suffix: str = "xml"
    conditions: str = ""
    member_via: str | None = None
    carried_type: int | None = None
    entity_attr: str | None = None
    entity_hops: tuple[str, ...] = ()


_MESSAGE_FILTER = ("sdkmessagefilter",)
_STEP = "sdkmessageprocessingstep"

# Insertion order is the order `crm legacy components` runs when no kind is named.
KINDS: dict[str, Kind] = {
    "web-resources": Kind(
        "webresource",
        ("name", "displayname", "webresourcetype", "ismanaged", "content"),
        "webresourceid",
        payload="content",
        member_via="webresourceid",
    ),
    "plugin-assemblies": Kind(
        "pluginassembly",
        (
            "name",
            "version",
            "culture",
            "publickeytoken",
            "isolationmode",
            "sourcetype",
            "ismanaged",
        ),
        "pluginassemblyid",
        member_via="pluginassemblyid",
    ),
    "plugin-types": Kind(
        "plugintype",
        ("typename", "friendlyname", "pluginassemblyid", "isworkflowactivity"),
        "plugintypeid",
        member_via="pluginassemblyid",
    ),
    "plugin-steps": Kind(
        _STEP,
        (
            "name",
            "stage",
            "mode",
            "rank",
            "sdkmessageid",
            "sdkmessagefilterid",
            "plugintypeid",
            "statecode",
            "filteringattributes",
            "supporteddeployment",
        ),
        f"{_STEP}id",
        member_via=f"{_STEP}id",
        entity_attr="primaryobjecttypecode",
        entity_hops=_MESSAGE_FILTER,
    ),
    "step-images": Kind(
        "sdkmessageprocessingstepimage",
        (
            "name",
            "entityalias",
            "imagetype",
            "attributes",
            "messagepropertyname",
            "sdkmessageprocessingstepid",
        ),
        "sdkmessageprocessingstepimageid",
        member_via=f"{_STEP}id",
        entity_attr="primaryobjecttypecode",
        entity_hops=(_STEP, *_MESSAGE_FILTER),
    ),
    # type 1 is a process definition; activations (type 2) duplicate them.
    "processes": Kind(
        "workflow",
        ("name", "category", "type", "primaryentity", "statecode", "xaml"),
        "workflowid",
        payload="xaml",
        suffix="xaml",
        conditions='<condition attribute="type" operator="eq" value="1"/>',
        member_via="workflowid",
        entity_attr="primaryentity",
    ),
    "solutions": Kind(
        "solution",
        ("uniquename", "friendlyname", "version", "ismanaged", "publisherid", "isvisible"),
        "solutionid",
    ),
    "forms": Kind(
        "systemform",
        ("name", "objecttypecode", "type", "formxml"),
        "formid",
        payload="formxml",
        carried_type=60,
        entity_attr="objecttypecode",
    ),
    "views": Kind(
        "savedquery",
        ("name", "returnedtypecode", "querytype", "fetchxml", "layoutxml"),
        "savedqueryid",
        carried_type=26,
        entity_attr="returnedtypecode",
    ),
}

#: The kinds `--entity` narrows, in `KINDS` order.
ENTITY_KINDS = [name for name, kind in KINDS.items() if kind.entity_attr is not None]

_ENTITY_COMPONENT = 1  # a solutioncomponent row's componenttype for an entity


@dataclass(frozen=True)
class ComponentScope:
    """What `read_components` narrows each kind to; the default reads every row.

    *member_codes* are the object type codes of the solution's member entities
    and *members* its direct members by component type, both read only for the
    entity-carried kinds (forms, views). `resolve_scope` builds it.
    """

    solution_id: str | None = None
    member_codes: frozenset[int] = frozenset()
    members: Mapping[int, frozenset[str]] = field(default_factory=dict[int, frozenset[str]])
    entity_code: int | None = None


#: The unscoped read: every row of each kind.
EVERYTHING = ComponentScope()


_BINARY_REASON = {
    1: "stored on disk, not in the database",
    2: "stored in the GAC, not in the database",
}
_WEB_RESOURCE_EXT = dict(
    enumerate(("htm", "css", "js", "xml", "png", "jpg", "gif", "xap", "xsl", "ico"), start=1)
)


def _eq(attribute: str, value: object) -> str:
    return (
        f'<condition attribute={quoteattr(attribute)} operator="eq" value={quoteattr(str(value))}/>'
    )


def _in(attribute: str, values: Iterable[object]) -> str:
    """An ``in`` condition, or nothing for no values (FetchXML refuses an empty ``in``)."""
    items = "".join(f"<value>{escape(str(v))}</value>" for v in sorted(values, key=str))
    return (
        f'<condition attribute={quoteattr(attribute)} operator="in">{items}</condition>'
        if items
        else ""
    )


def _link(entity: str, column: str, inner: str) -> str:
    return (
        f'<link-entity name="{entity}" from="{column}" to="{column}" link-type="inner">'
        f"{inner}</link-entity>"
    )


def _fetch(kind: Kind, scope: ComponentScope) -> str | None:
    """The FetchXML for *kind* under *scope*, or None when the scope admits no row."""
    conditions = [kind.conditions]
    links: list[str] = []
    if scope.solution_id is not None:
        in_solution = f"<filter>{_eq('solutionid', scope.solution_id)}</filter>"
        if kind.member_via is not None:
            links.append(
                f'<link-entity name="solutioncomponent" from="objectid" to="{kind.member_via}"'
                f' link-type="inner">{in_solution}</link-entity>'
            )
        elif kind.carried_type is not None and kind.entity_attr is not None:
            either = _in(kind.key, scope.members.get(kind.carried_type, ())) + _in(
                kind.entity_attr, scope.member_codes
            )
            if not either:
                return None
            conditions.append(f'<filter type="or">{either}</filter>')
        elif kind.entity == "solution":  # the solution's own row
            conditions.append(_eq(kind.key, scope.solution_id))
        else:
            raise ValueError(f"{kind.entity}: no solution membership rule")
    if scope.entity_code is not None and kind.entity_attr is not None:
        condition = _eq(kind.entity_attr, scope.entity_code)
        if kind.entity_hops:
            chain = f"<filter>{condition}</filter>"
            for hop in reversed(kind.entity_hops):
                chain = _link(hop, f"{hop}id", chain)
            links.append(chain)
        else:
            conditions.append(condition)
    columns = "".join(f'<attribute name="{a}"/>' for a in kind.attributes)
    where = "".join(conditions)
    filter_ = f"<filter>{where}</filter>" if where else ""
    return (
        f'<fetch><entity name="{kind.entity}">{columns}{filter_}{"".join(links)}</entity></fetch>'
    )


def _rows(client: LegacyClient, fetch_xml: str) -> list[dict[str, object]]:
    return [row for page in client.retrieve_multiple(fetch_xml) for row in page]


def _object_type_code(client: LegacyClient, name: str) -> int:
    """The object type code of entity *name*: 2011 entity conditions take it, never the name."""
    try:
        code = _retrieve_entity(client, name, "Entity").findtext("c:ObjectTypeCode", None, _MD)
    except D365Error as exc:
        if connection_wide(exc):
            raise
        raise D365Error(
            f"entity {name!r}: {exc}",
            status=exc.status,
            code=exc.code,
            response_body=exc.response_body,
        ) from exc
    if code is None:
        raise D365Error(f"entity {name!r}: RetrieveEntity carries no ObjectTypeCode")
    return int(code)


def _solution_id(client: LegacyClient, unique_name: str) -> str:
    fetch = (
        '<fetch><entity name="solution"><attribute name="solutionid"/>'
        f"<filter>{_eq('uniquename', unique_name)}</filter></entity></fetch>"
    )
    rows = _rows(client, fetch)
    if not rows or not isinstance(rows[0]["id"], str):
        raise D365Error(f"no solution has the unique name {unique_name!r}")
    return rows[0]["id"]


def resolve_scope(
    client: LegacyClient, kinds: list[str], solution: str | None, entity: str | None
) -> ComponentScope:
    """Resolve ``--solution`` and ``--entity`` for *kinds*, before any kind is read.

    An unknown solution or entity raises a D365Error naming it. Solution
    membership is as exported: a kind's own ``solutioncomponent`` row, or its
    carrying parent's (see `Kind`). CRM 2011 keeps no row for views and most
    entity forms, so those are read through the solution's member entities,
    whose MetadataIds `list_entities` maps to object type codes.
    """
    entity_code = None if entity is None else _object_type_code(client, entity)
    if solution is None:
        return ComponentScope(entity_code=entity_code)
    solution_id = _solution_id(client, solution)
    carried = sorted({t for n in kinds if (t := KINDS[n].carried_type) is not None})
    if not carried:
        return ComponentScope(solution_id, entity_code=entity_code)
    fetch = (
        '<fetch><entity name="solutioncomponent">'
        '<attribute name="componenttype"/><attribute name="objectid"/>'
        f"<filter>{_eq('solutionid', solution_id)}"
        f"{_in('componenttype', [_ENTITY_COMPONENT, *carried])}</filter></entity></fetch>"
    )
    members: dict[int, set[str]] = {}
    for row in _rows(client, fetch):
        kind_code, object_id = row.get("componenttype"), row.get("objectid")
        if isinstance(kind_code, int) and isinstance(object_id, str):
            members.setdefault(kind_code, set()).add(object_id.lower())
    entity_ids = members.pop(_ENTITY_COMPONENT, set())
    codes = (
        frozenset(
            e["object_type_code"]
            for e in list_entities(client)
            if (e["metadata_id"] or "").lower() in entity_ids
        )
        if entity_ids
        else frozenset[int]()
    )
    return ComponentScope(
        solution_id, codes, {t: frozenset(ids) for t, ids in members.items()}, entity_code
    )


def _mark_binary(row: dict[str, object]) -> None:
    """Say whether a plug-in assembly's binary is in the database; never skip the row."""
    source = row.get("sourcetype")
    if source == 0:
        row["binary"] = "database"
    else:
        row["binary"] = "unavailable"
        row["binary_reason"] = _BINARY_REASON.get(
            source if isinstance(source, int) else -1, "not stored in the database"
        )


def _write_payload(out_dir: Path, name: str, kind: Kind, row: dict[str, object]) -> None:
    """Move *row*'s payload into ``out_dir/<name>/<id>.<ext>``, leaving its path."""
    column = kind.payload
    if column is None:
        return
    value = row.get(column)
    if not isinstance(value, str):
        return
    if kind.entity == "webresource":
        # Web resource content is base64, its type picks the extension.
        kind_code = row.get("webresourcetype")
        ext = _WEB_RESOURCE_EXT.get(kind_code if isinstance(kind_code, int) else 0, "bin")
        data = base64.b64decode(value, validate=True)
    else:
        ext = kind.suffix
        data = value.encode("utf-8")
    relative = f"{name}/{row['id']}.{ext}"
    target = out_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    del row[column]
    row[f"{column}_file"] = relative


def read_components(
    client: LegacyClient, kinds: list[str], out_dir: Path | None, scope: ComponentScope = EVERYTHING
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, Any]]]:
    """Page each component kind under *scope*; a failed kind is reported and the next still runs.

    Rows read before a failure stay in the result. With *out_dir*, payloads are
    written per page so they are never all held in memory. A kind the scope
    admits no row of is empty and sends no request.
    """
    data: dict[str, list[dict[str, object]]] = {}
    errors: list[dict[str, Any]] = []
    for name in kinds:
        kind = KINDS[name]
        rows = data[name] = []
        fetch = _fetch(kind, scope)
        if fetch is None:
            continue
        try:
            for page in client.retrieve_multiple(fetch):
                for row in page:
                    if name == "plugin-assemblies":
                        _mark_binary(row)
                    if out_dir is not None:
                        _write_payload(out_dir, name, kind, row)
                    rows.append(row)
        except (D365Error, OSError, binascii.Error) as exc:
            # A fault, a payload that cannot be written (OSError) or web resource
            # content that is not valid base64 (binascii.Error) fails this kind only.
            if isinstance(exc, D365Error) and connection_wide(exc):
                raise
            code = exc.code if isinstance(exc, D365Error) else None
            errors.append({"kind": name, "error": str(exc), "code": code})
    return data, errors


# ── Dependencies, solution export, ribbons ─────────────────────────────────

#: `crm legacy dependencies --for` choice -> the Execute request it sends.
DEPENDENCY_REQUESTS = {
    "required": "RetrieveRequiredComponents",
    "dependents": "RetrieveDependentComponents",
    "delete": "RetrieveDependenciesForDelete",
}
_DEPENDENCY_COLUMNS = (
    "dependentcomponentobjectid",
    "dependentcomponenttype",
    "dependentcomponentparentid",
    "requiredcomponentobjectid",
    "requiredcomponenttype",
    "requiredcomponentparentid",
    "dependencytype",
)


def dependency_row(record: dict[str, object]) -> dict[str, object]:
    """One dependency, its two sides named by component type and object id."""
    return {column: record.get(column) for column in _DEPENDENCY_COLUMNS}


def dependencies(
    client: LegacyClient, object_id: str, component_type: int, for_: str
) -> list[dict[str, object]]:
    """Read one component's dependencies with the request *for_* names (`DEPENDENCY_REQUESTS`)."""
    request = DEPENDENCY_REQUESTS[for_]
    results = client.execute_results(
        request,
        parameter("ObjectId", "guid", _SERIALIZATION, object_id)
        + parameter("ComponentType", "int", _XSD, str(component_type)),
    )
    collection = _result(results, "EntityCollection", request)
    return [dependency_row(r) for r in entity_records(collection)]


#: Read timeout of an ExportSolution call, in seconds. The org builds the whole zip before it
#: answers: the `Default` solution of a large org took 258 s (measured, #1100), past the
#: profile's 120 s default.
EXPORT_TIMEOUT = 1800


def export_solution(client: LegacyClient, unique_name: str) -> bytes:
    """Export a solution unmanaged, as stored, and return the zip bytes.

    Nothing is published first: publishing would write, so the zip carries the
    org's published customizations only. The call waits up to `EXPORT_TIMEOUT`.
    """
    results = client.execute_results(
        "ExportSolution",
        parameter("SolutionName", "string", _XSD, unique_name)
        + parameter("Managed", "boolean", _XSD, "false"),
        timeout=EXPORT_TIMEOUT,
    )
    try:
        data = base64.b64decode(
            _result(results, "ExportSolutionFile", "ExportSolution").text or "", validate=True
        )
    except ValueError as exc:  # binascii.Error
        raise D365Error(f"ExportSolution: the solution file is not base64: {exc}") from exc
    if not data:
        raise D365Error("ExportSolution: the response carries no solution file")
    return data


# Every ribbon location; RibbonLocationFilters is a flags enum, sent by member names.
_RIBBON_LOCATIONS = parameter(
    "RibbonLocationFilter",
    "RibbonLocationFilters",
    "http://schemas.microsoft.com/crm/2011/Contracts",
    "Form HomepageGrid SubGrid",
)


def unzip_ribbon(compressed: str | None) -> bytes:
    """Unzip a compressed ribbon (base64 over a zip of ``RibbonXml.xml``), SOAP or Web API.

    A malformed one is a per-item D365Error, not a crash.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(compressed or ""))) as archive:
            return archive.read("RibbonXml.xml")
    # zlib.error, RuntimeError (encrypted) and NotImplementedError (compression)
    # are what a damaged entry raises past BadZipFile.
    except (ValueError, KeyError, RuntimeError, zlib.error, zipfile.BadZipFile) as exc:
        raise D365Error(f"not a readable ribbon zip: {exc}") from exc


def ribbon_xml(client: LegacyClient, entity: str | None) -> bytes:
    """The ribbon XML of *entity*, or of the application when *entity* is None."""
    if entity is None:
        results = client.execute_results("RetrieveApplicationRibbon")
        return unzip_ribbon(
            _result(results, "CompressedApplicationRibbonXml", "RetrieveApplicationRibbon").text
        )
    results = client.execute_results(
        "RetrieveEntityRibbon",
        parameter("EntityName", "string", _XSD, entity) + _RIBBON_LOCATIONS,
    )
    return unzip_ribbon(_result(results, "CompressedEntityXml", "RetrieveEntityRibbon").text)
