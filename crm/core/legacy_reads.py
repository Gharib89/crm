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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    results = client.execute_results("RetrieveAllEntities", _filters("Entity") + _AS_IF_PUBLISHED)
    return [
        {
            "logical_name": e.findtext("c:LogicalName", "", _MD),
            "display_name": e.findtext("c:DisplayName/a:UserLocalizedLabel/a:Label", None, _MD),
            "custom": _flag(e, "c:IsCustomEntity"),
            "object_type_code": int(e.findtext("c:ObjectTypeCode", "0", _MD)),
            "metadata_id": e.findtext("c:MetadataId", None, _MD),
            "managed": _flag(e, "c:IsManaged"),
        }
        for e in _result(results, "EntityMetadata", "RetrieveAllEntities").findall(
            "c:EntityMetadata", _MD
        )
    ]


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


def entity_detail(client: LegacyClient, name: str) -> dict[str, Any]:
    """One entity's attributes, relationships and the option sets its attributes use."""
    results = client.execute_results(
        "RetrieveEntity",
        _filters("Attributes Relationships")
        + parameter("LogicalName", "string", _XSD, name)
        + parameter("MetadataId", "guid", _SERIALIZATION, _NO_ID)
        + _AS_IF_PUBLISHED,
    )
    entity = _result(results, "EntityMetadata", "RetrieveEntity")
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
    """One component kind: the entity to page, its columns and an optional filter.

    *payload* names the column holding the large body (a web resource's content,
    a form's XML) that ``--out`` writes to a file with extension *suffix*.
    *filter* is a FetchXML fragment placed inside the entity.
    """

    entity: str
    attributes: tuple[str, ...]
    payload: str | None = None
    suffix: str = "xml"
    filter: str = ""


# Insertion order is the order `crm legacy components` runs when no kind is named.
KINDS: dict[str, Kind] = {
    "web-resources": Kind(
        "webresource",
        ("name", "displayname", "webresourcetype", "ismanaged", "content"),
        payload="content",
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
    ),
    "plugin-types": Kind(
        "plugintype", ("typename", "friendlyname", "pluginassemblyid", "isworkflowactivity")
    ),
    "plugin-steps": Kind(
        "sdkmessageprocessingstep",
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
    ),
    # type 1 is a process definition; activations (type 2) duplicate them.
    "processes": Kind(
        "workflow",
        ("name", "category", "type", "primaryentity", "statecode", "xaml"),
        payload="xaml",
        suffix="xaml",
        filter='<filter><condition attribute="type" operator="eq" value="1"/></filter>',
    ),
    "solutions": Kind(
        "solution",
        ("uniquename", "friendlyname", "version", "ismanaged", "publisherid", "isvisible"),
    ),
    "forms": Kind("systemform", ("name", "objecttypecode", "type", "formxml"), payload="formxml"),
    "views": Kind("savedquery", ("name", "returnedtypecode", "querytype", "fetchxml", "layoutxml")),
}

_BINARY_REASON = {
    1: "stored on disk, not in the database",
    2: "stored in the GAC, not in the database",
}
_WEB_RESOURCE_EXT = dict(
    enumerate(("htm", "css", "js", "xml", "png", "jpg", "gif", "xap", "xsl", "ico"), start=1)
)


def _fetch(kind: Kind) -> str:
    columns = "".join(f'<attribute name="{a}"/>' for a in kind.attributes)
    return f'<fetch><entity name="{kind.entity}">{columns}{kind.filter}</entity></fetch>'


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
    client: LegacyClient, kinds: list[str], out_dir: Path | None
) -> tuple[dict[str, list[dict[str, object]]], list[dict[str, Any]]]:
    """Page each component kind; a failed kind is reported and the next still runs.

    Rows read before a failure stay in the result. With *out_dir*, payloads are
    written per page so they are never all held in memory.
    """
    data: dict[str, list[dict[str, object]]] = {}
    errors: list[dict[str, Any]] = []
    for name in kinds:
        kind = KINDS[name]
        rows = data[name] = []
        try:
            for page in client.retrieve_multiple(_fetch(kind)):
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


def export_solution(client: LegacyClient, unique_name: str) -> bytes:
    """Export a solution unmanaged, as stored, and return the zip bytes.

    Nothing is published first: publishing would write, so the zip carries the
    org's published customizations only.
    """
    results = client.execute_results(
        "ExportSolution",
        parameter("SolutionName", "string", _XSD, unique_name)
        + parameter("Managed", "boolean", _XSD, "false"),
    )
    return base64.b64decode(_result(results, "ExportSolutionFile", "ExportSolution").text or "")


# Every ribbon location; RibbonLocationFilters is a flags enum, sent by member names.
_RIBBON_LOCATIONS = parameter(
    "RibbonLocationFilter",
    "RibbonLocationFilters",
    "http://schemas.microsoft.com/crm/2011/Contracts",
    "Form HomepageGrid SubGrid",
)


def _unzip_ribbon(node: ET.Element) -> bytes:
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(node.text or ""))) as archive:
        return archive.read("RibbonXml.xml")


def ribbon_xml(client: LegacyClient, entity: str | None) -> bytes:
    """The ribbon XML of *entity*, or of the application when *entity* is None."""
    if entity is None:
        results = client.execute_results("RetrieveApplicationRibbon")
        return _unzip_ribbon(
            _result(results, "CompressedApplicationRibbonXml", "RetrieveApplicationRibbon")
        )
    results = client.execute_results(
        "RetrieveEntityRibbon",
        parameter("EntityName", "string", _XSD, entity) + _RIBBON_LOCATIONS,
    )
    return _unzip_ribbon(_result(results, "CompressedEntityXml", "RetrieveEntityRibbon"))
