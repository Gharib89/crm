"""The v9 on-premises and Dataverse online reads behind `crm inventory extract` (ADR 0032).

`WebApiSource` answers the shared extractor in `crm.core.inventory_extract`
over the Web API: the extractor's FetchXML reads run as ``?fetchXml=`` queries,
paged by the FetchXML paging cookie, and every row and metadata answer is
reshaped to what the CRM 2011 SOAP reads return, so one extractor builds the
records of every platform. Every call is a GET, so a read-only profile
(ADR 0021) runs it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Collection, Iterator
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote

from crm.core import solution_transfer
from crm.core.inventory import ExtractionGap
from crm.core.inventory_extract import integration_gap, ssrs_gap
from crm.core.legacy import Page
from crm.core.legacy_reads import EXPORT_TIMEOUT, unzip_ribbon
from crm.core.usage import GroupUsage, probe_v9_group
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, is_cloud
from crm.utils.safe_xml import fromstring

# FetchXML entity -> (Web API entity set, primary id column), for the extractor's reads.
_ENTITY_SETS: dict[str, tuple[str, str]] = {
    "solution": ("solutions", "solutionid"),
    "webresource": ("webresourceset", "webresourceid"),
    "pluginassembly": ("pluginassemblies", "pluginassemblyid"),
    "plugintype": ("plugintypes", "plugintypeid"),
    "sdkmessageprocessingstep": ("sdkmessageprocessingsteps", "sdkmessageprocessingstepid"),
    "sdkmessageprocessingstepimage": (
        "sdkmessageprocessingstepimages",
        "sdkmessageprocessingstepimageid",
    ),
    "workflow": ("workflows", "workflowid"),
    "systemform": ("systemforms", "formid"),
    "savedquery": ("savedqueries", "savedqueryid"),
    "sitemap": ("sitemaps", "sitemapid"),
    "solutioncomponent": ("solutioncomponents", "solutioncomponentid"),
    "dependency": ("dependencies", "dependencyid"),
}
# Lookup names and logical names ride on annotations, which the server sends only on request.
_ANNOTATIONS = {"Prefer": 'odata.include-annotations="*"'}
_FORMATTED = "@OData.Community.Display.V1.FormattedValue"
_LOOKUP_ENTITY = "@Microsoft.Dynamics.CRM.lookuplogicalname"
_MORE = "@Microsoft.Dynamics.CRM.morerecords"
_COOKIE = "@Microsoft.Dynamics.CRM.fetchxmlpagingcookie"
_PAGING_COOKIE = re.compile(r'pagingcookie="([^"]*)"')
_NO_ID = "00000000-0000-0000-0000-000000000000"


def _label(value: object) -> str | None:
    """The user-localized text of a metadata ``Label``, or None."""
    if not isinstance(value, dict):
        return None
    localized = cast("dict[str, Any]", value).get("UserLocalizedLabel")
    if not isinstance(localized, dict):
        return None
    text = cast("dict[str, Any]", localized).get("Label")
    return text if isinstance(text, str) else None


def _dicts(value: object) -> list[dict[str, Any]]:
    """A JSON array of objects, typed; anything else is empty."""
    items = cast("list[object]", value) if isinstance(value, list) else []
    return [cast("dict[str, Any]", item) for item in items if isinstance(item, dict)]


def _option_set(meta: dict[str, Any]) -> dict[str, Any]:
    options = _dicts(meta.get("Options"))
    options += [
        cast("dict[str, Any]", meta[tag])
        for tag in ("FalseOption", "TrueOption")
        if isinstance(meta.get(tag), dict)
    ]
    return {
        "name": meta.get("Name") or "",
        "metadata_id": meta.get("MetadataId"),
        "global": bool(meta.get("IsGlobal")),
        "custom": bool(meta.get("IsCustomOptionSet")),
        "managed": bool(meta.get("IsManaged")),
        "type": meta.get("OptionSetType") or "",
        "options": [{"value": o.get("Value"), "label": _label(o.get("Label"))} for o in options],
    }


def _one_to_many(rel: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_name": rel.get("SchemaName") or "",
        "metadata_id": rel.get("MetadataId"),
        "custom": bool(rel.get("IsCustomRelationship")),
        "managed": bool(rel.get("IsManaged")),
        "referenced_entity": rel.get("ReferencedEntity") or "",
        "referenced_attribute": rel.get("ReferencedAttribute") or "",
        "referencing_entity": rel.get("ReferencingEntity") or "",
        "referencing_attribute": rel.get("ReferencingAttribute") or "",
    }


def _many_to_many(rel: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_name": rel.get("SchemaName") or "",
        "metadata_id": rel.get("MetadataId"),
        "custom": bool(rel.get("IsCustomRelationship")),
        "managed": bool(rel.get("IsManaged")),
        "entity1": rel.get("Entity1LogicalName") or "",
        "entity2": rel.get("Entity2LogicalName") or "",
        "intersect_entity": rel.get("IntersectEntityName") or "",
    }


def _reshape_entity(meta: dict[str, Any]) -> dict[str, Any]:
    """Reshape a Web API ``EntityMetadata`` to `crm.core.legacy_reads.entity_detail`'s dict."""
    attributes: list[dict[str, Any]] = []
    option_sets: dict[str, dict[str, Any]] = {}
    for attr in _dicts(meta.get("Attributes")):
        option_set = attr.get("OptionSet")
        set_name = None
        if isinstance(option_set, dict):
            parsed = _option_set(cast("dict[str, Any]", option_set))
            set_name = parsed["name"] or None
            if set_name and set_name not in option_sets:
                option_sets[set_name] = parsed
        required = attr.get("RequiredLevel")
        attributes.append(
            {
                "logical_name": attr.get("LogicalName") or "",
                "metadata_id": attr.get("MetadataId"),
                "type": attr.get("AttributeType") or "",
                "custom": bool(attr.get("IsCustomAttribute")),
                "managed": bool(attr.get("IsManaged")),
                "required_level": cast("dict[str, Any]", required).get("Value")
                if isinstance(required, dict)
                else None,
                "option_set": set_name,
            }
        )
    return {
        "logical_name": meta.get("LogicalName") or "",
        "metadata_id": meta.get("MetadataId"),
        "custom": bool(meta.get("IsCustomEntity")),
        "managed": bool(meta.get("IsManaged")),
        "object_type_code": meta.get("ObjectTypeCode") or 0,
        "primary_id_attribute": meta.get("PrimaryIdAttribute"),
        "primary_name_attribute": meta.get("PrimaryNameAttribute"),
        "attributes": attributes,
        "relationships": {
            "one_to_many": [_one_to_many(r) for r in _dicts(meta.get("OneToManyRelationships"))],
            "many_to_one": [_one_to_many(r) for r in _dicts(meta.get("ManyToOneRelationships"))],
            "many_to_many": [_many_to_many(r) for r in _dicts(meta.get("ManyToManyRelationships"))],
        },
        "option_sets": list(option_sets.values()),
    }


def _row(row: dict[str, Any], primary: str) -> dict[str, object]:
    """Reshape a FetchXML row to the SOAP reader's: ``id``, and each lookup as a reference dict."""
    out: dict[str, object] = {"id": row.get(primary)}
    for name, value in row.items():
        if "@" in name or name == primary:
            continue
        if name.startswith("_") and name.endswith("_value"):
            out[name[1:-6]] = {
                "id": value,
                "logical_name": row.get(name + _LOOKUP_ENTITY),
                "name": row.get(name + _FORMATTED),
            }
        else:
            out[name] = value
    return out


def _cookie(annotation: object) -> str | None:
    """The paging cookie to send back, from the ``fetchxmlpagingcookie`` annotation.

    The annotation wraps the cookie, URL-encoded twice, in its ``pagingcookie`` attribute.
    """
    if not isinstance(annotation, str):
        return None
    match = _PAGING_COOKIE.search(annotation)
    return unquote(unquote(match.group(1))) if match and match.group(1) else None


class WebApiSource:
    """A v9 on-premises or Dataverse online org, read over the Web API."""

    api = "the Web API"

    def __init__(self, backend: D365Backend):
        self.backend = backend
        # Client-credentials OAuth reaches only Dataverse online; every other scheme is on-prem.
        self.platform = "cloud" if is_cloud(backend.profile) else "onprem"
        gaps = [integration_gap(self.api)]
        if self.platform == "onprem":
            gaps.append(ssrs_gap(self.api))
        self.static_gaps: tuple[ExtractionGap, ...] = tuple(gaps)
        # Dataverse online refuses to export the Default solution; on-prem allows it.
        self.default_export_supported = self.platform == "onprem"

    @property
    def org_url(self) -> str:
        return self.backend.profile.url

    def identity(self) -> dict[str, Any]:
        return {
            "organization_id": as_dict(self.backend.get("WhoAmI")).get("OrganizationId"),
            "version": as_dict(self.backend.get("RetrieveVersion()")).get("Version"),
        }

    def list_entities(self) -> list[dict[str, Any]]:
        rows = self.backend.get_collection(
            "EntityDefinitions",
            params={
                "$select": "LogicalName,DisplayName,DisplayCollectionName,IsCustomEntity,"
                "ObjectTypeCode,MetadataId,IsManaged"
            },
        )
        return [
            {
                "logical_name": e.get("LogicalName") or "",
                "display_name": _label(e.get("DisplayName")),
                "collection_name": _label(e.get("DisplayCollectionName")),
                "custom": bool(e.get("IsCustomEntity")),
                "object_type_code": e.get("ObjectTypeCode") or 0,
                "metadata_id": e.get("MetadataId"),
                "managed": bool(e.get("IsManaged")),
            }
            for e in rows
        ]

    def list_option_sets(self) -> list[dict[str, Any]]:
        return [_option_set(s) for s in self.backend.get_collection("GlobalOptionSetDefinitions")]

    def entity_detail(self, name: str) -> dict[str, Any]:
        body = as_dict(
            self.backend.get(
                "RetrieveEntity(EntityFilters=@f,LogicalName=@n,MetadataId=@m,"
                "RetrieveAsIfPublished=false)",
                params={
                    "@f": "Microsoft.Dynamics.CRM.EntityFilters'Attributes,Relationships'",
                    "@n": f"'{name}'",
                    "@m": _NO_ID,
                },
            )
        )
        meta = body.get("EntityMetadata")
        if not isinstance(meta, dict):
            raise D365Error(f"RetrieveEntity {name}: the response carries no EntityMetadata")
        return _reshape_entity(cast("dict[str, Any]", meta))

    def export_solution(self, unique_name: str, dest: Path) -> None:
        # A read: --dry-run must not preview it away (reads-execute rule).
        solution_transfer.export_solution(
            self.backend.as_live(), unique_name, dest, timeout=EXPORT_TIMEOUT
        )

    def ribbon_xml(self, entity: str | None) -> bytes:
        if entity is None:
            body = as_dict(self.backend.get("RetrieveApplicationRibbon()"))
            return unzip_ribbon(body.get("CompressedApplicationRibbonXml"))
        body = as_dict(
            self.backend.get(
                "RetrieveEntityRibbon(EntityName=@e,RibbonLocationFilter="
                "Microsoft.Dynamics.CRM.RibbonLocationFilters'Form,HomepageGrid,SubGrid')",
                params={"@e": f"'{entity}'"},
            )
        )
        return unzip_ribbon(body.get("CompressedEntityXml"))

    def pages(self, fetch_xml: str, *, page_size: int, start: Page | None) -> Iterator[Page]:
        """Page a FetchXML query over the Web API, as `crm.core.legacy.LegacyClient.pages` does.

        Stops after the last page, or after a page that reports more records
        but carries no paging cookie (the extractor reads that as truncation).
        """
        fetch = fromstring(fetch_xml)
        entity = fetch.find("entity")
        name = entity.get("name", "") if entity is not None else ""
        entity_set, primary = _ENTITY_SETS[name]
        fetch.set("count", str(page_size))
        number = 1
        if start is not None:
            if not start.more or start.cookie is None:
                return
            number = start.number + 1
            fetch.set("paging-cookie", start.cookie)
        while True:
            fetch.set("page", str(number))
            body = as_dict(
                self.backend.get(
                    entity_set,
                    params={"fetchXml": ET.tostring(fetch, encoding="unicode")},
                    extra_headers=_ANNOTATIONS,
                )
            )
            more = body.get(_MORE) is True
            cookie = _cookie(body.get(_COOKIE))
            rows = [_row(r, primary) for r in _dicts(body.get("value"))]
            page = Page(name, number, rows, more, cookie if more else None)
            yield page
            if not page.more or page.cookie is None:
                return
            fetch.set("paging-cookie", page.cookie)
            number += 1

    def probe_usage(
        self,
        group: str,
        *,
        names: Collection[str],
        time_cap: float,
        progress: Callable[[int, int], None] | None,
    ) -> GroupUsage:
        # *time_cap* bounds only the CRM 2011 async reads; the Web API probes read in bulk.
        return probe_v9_group(self.backend, group, names, progress=progress)
