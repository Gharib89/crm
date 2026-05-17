"""Solution lifecycle: list / info / export / import."""

from __future__ import annotations

import base64
from pathlib import Path

from crm.utils.d365_backend import D365Backend, D365Error


def list_solutions(backend: D365Backend, *, managed: bool | None = None) -> list[dict]:
    params = {
        "$select": "uniquename,friendlyname,version,ismanaged,installedon,solutionid",
        "$orderby": "uniquename",
    }
    if managed is not None:
        params["$filter"] = f"ismanaged eq {'true' if managed else 'false'}"
    result = backend.get("solutions", params=params) or {}
    return result.get("value", [])


def solution_info(backend: D365Backend, unique_name: str) -> dict:
    if not unique_name:
        raise D365Error("solution unique name required.")
    params = {"$filter": f"uniquename eq '{unique_name}'"}
    result = backend.get("solutions", params=params) or {}
    items = result.get("value", [])
    if not items:
        raise D365Error(f"Solution not found: {unique_name}")
    return items[0]


def solution_components(backend: D365Backend, unique_name: str) -> list[dict]:
    sol = solution_info(backend, unique_name)
    solution_id = sol["solutionid"]
    params = {
        "$select": "componenttype,objectid,rootcomponentbehavior",
        "$filter": f"_solutionid_value eq {solution_id}",
        "$top": "5000",
    }
    result = backend.get("solutioncomponents", params=params) or {}
    return result.get("value", [])


def export_solution(
    backend: D365Backend,
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool = False,
) -> dict:
    """Call ExportSolution action and write the returned ZIP to disk."""
    body = {
        "SolutionName": unique_name,
        "Managed": managed,
        "ExportAutoNumberingSettings": False,
        "ExportCalendarSettings": False,
        "ExportCustomizationSettings": False,
        "ExportEmailTrackingSettings": False,
        "ExportGeneralSettings": False,
        "ExportIsvConfig": False,
        "ExportMarketingSettings": False,
        "ExportOutlookSynchronizationSettings": False,
        "ExportRelationshipRoles": False,
        "ExportSales": False,
    }
    result = backend.post("ExportSolution", json_body=body) or {}
    if "_dry_run" in result:
        return result
    encoded = result.get("ExportSolutionFile")
    if not encoded:
        raise D365Error("ExportSolution returned no ExportSolutionFile payload.")
    data = base64.b64decode(encoded)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {
        "output": str(out),
        "bytes": len(data),
        "managed": managed,
        "solution": unique_name,
    }


def import_solution(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    publish_workflows: bool = True,
    overwrite_unmanaged_customizations: bool = True,
) -> dict:
    """Call ImportSolution action with the contents of a solution ZIP."""
    p = Path(zip_path)
    if not p.is_file():
        raise D365Error(f"Solution file not found: {zip_path}")
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    body = {
        "CustomizationFile": encoded,
        "PublishWorkflows": publish_workflows,
        "OverwriteUnmanagedCustomizations": overwrite_unmanaged_customizations,
        "ImportJobId": _new_guid(),
    }
    result = backend.post("ImportSolution", json_body=body) or {}
    return result


def publish_all(backend: D365Backend) -> dict:
    """Call PublishAllXml — publishes all unpublished customizations.

    Action returns 204 No Content on success, so we synthesize a confirmation dict.
    """
    result = backend.post("PublishAllXml")
    if isinstance(result, dict) and result:
        return result
    return {"published": True, "action": "PublishAllXml"}


def publish_xml(backend: D365Backend, parameter_xml: str) -> dict:
    """Call PublishXml with a Publish Request Schema XML payload.

    Example parameter_xml:
        '<importexportxml><entities><entity>account</entity></entities></importexportxml>'

    Reference: https://learn.microsoft.com/power-apps/developer/model-driven-apps/publish-customizations
    """
    if not parameter_xml or "<" not in parameter_xml:
        raise D365Error("parameter_xml must be a Publish Request XML document.")
    result = backend.post(
        "PublishXml",
        json_body={"ParameterXml": parameter_xml},
    )
    if isinstance(result, dict) and result:
        return result
    return {"published": True, "action": "PublishXml"}


def service_document(backend: D365Backend) -> dict:
    """GET the root service document — lists all entity sets exposed by the server."""
    return backend.get("") or {}


def _new_guid() -> str:
    import uuid
    return str(uuid.uuid4())
