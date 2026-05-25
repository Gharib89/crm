"""Global option set CRUD.

`update_optionset` is granular: insert/update/delete/reorder dispatch
to `InsertOptionValue`, `UpdateOptionValue`, `DeleteOptionValue`,
`OrderOption` bound actions in that order. Partial failure stops and
returns `{stage, completed_steps, error}` — no rollback.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish


def _parse_optionset_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"GlobalOptionSetDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def list_optionsets(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List global option set definitions. Client-side $top slice."""
    result = as_dict(backend.get(
        "GlobalOptionSetDefinitions",
        params={"$select": "Name,DisplayName,IsCustomOptionSet,IsGlobal,IsManaged"},
    ))
    items = result.get("value", [])
    if custom_only:
        items = [it for it in items if it.get("IsCustomOptionSet") is True]
    if top is not None:
        if top < 1:
            raise D365Error("--top must be >= 1")
        items = items[:top]
    return items


def get_optionset(backend: D365Backend, name: str) -> dict[str, Any]:
    """Retrieve a global option set with its options expanded."""
    if not name:
        raise D365Error("name is required.")
    return as_dict(backend.get(
        f"GlobalOptionSetDefinitions(Name='{name}')",
        params={"$expand": "Options"},
    ))


def create_optionset(
    backend: D365Backend,
    *,
    name: str,
    display_name: str,
    description: str | None = None,
    options: list[tuple[int | None, str]] | None = None,
    is_global: bool = True,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a global option set. Returns `{created, name, metadata_id_url, ...}`."""
    if not name or "_" not in name:
        raise D365Error("name must include a publisher prefix, e.g. 'new_priority'.")

    option_list: list[dict[str, Any]] = []
    if options:
        seen: set[int] = set()
        for value, lbl in options:
            if value is not None:
                if value in seen:
                    raise D365Error(f"Duplicate option value: {value}.")
                seen.add(value)
            if not lbl:
                raise D365Error("Option label must not be empty.")
            opt: dict[str, Any] = {"Label": label(lbl)}
            if value is not None:
                opt["Value"] = value
            option_list.append(opt)

    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
        "Name": name,
        "DisplayName": label(display_name),
        "IsGlobal": is_global,
        "OptionSetType": "Picklist",
        "Options": option_list,
    }
    if description:
        body["Description"] = label(description)

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "GlobalOptionSetDefinitions",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    os_id = _parse_optionset_id(entity_id_url)
    lookup_error: str | None = None
    name_readback: str | None = None
    if not os_id:
        lookup_error = (
            f"Could not parse MetadataId from response: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"GlobalOptionSetDefinitions({os_id})",
                params={"$select": "Name,IsCustomOptionSet"},
            ))
            name_readback = rb.get("Name")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "name": name_readback or name,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["optionset_lookup_error"] = lookup_error
    maybe_publish(backend, out, publish)
    return out
