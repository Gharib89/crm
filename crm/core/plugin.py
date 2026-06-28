"""Plug-in assembly (pluginassemblies) registration.

register_assembly POSTs the `pluginassemblies` entity; the assembly file's
bytes are base64-encoded into the `content` column. update is a plain PATCH of
only the `content` field (no retrieve-merge-write, but it does carry
`MSCRM.SolutionUniqueName` when a solution is given), resolving the assembly id
by name and forcing a real read even under dry-run so a PATCH preview targets
the live id.

Identity (name/version/culture/publickeytoken) is derived in pure Python —
filename stem plus documented defaults, with per-call overrides — NOT by .NET
reflection on the assembly. The four columns are SystemRequired on the API, so
they are always included in the create body.

Plug-in **type** rows are NOT created by register_assembly: a content-only Web
API `POST pluginassemblies` does not reflect the assembly, so it leaves zero
plugintype rows (only the Plug-in Registration Tool reflects an assembly
client-side to create them). Register each type explicitly with register_type.

Column values verified against MS Learn's pluginassembly entity reference
(learn.microsoft.com/power-apps/developer/data-platform/reference/entities/pluginassembly):
isolationmode 1=None / 2=Sandbox / 3=External; sourcetype 0=Database (default).
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any, Callable

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    odata_literal,
)
from crm.core import references as ref_mod

# Isolation mode -> pluginassembly isolationmode option set (verified MS Learn).
_ISOLATION_MODE: dict[str, int] = {
    "none": 1,      # None
    "sandbox": 2,   # Sandbox
}

# Documented defaults for an unsigned, single-file assembly registered without
# .NET reflection. publickeytoken "null" is the standard unsigned placeholder.
_DEFAULT_VERSION = "1.0.0.0"
_DEFAULT_CULTURE = "neutral"
_DEFAULT_PUBLIC_KEY_TOKEN = "null"

# sourcetype 0 = Database (default); the only mode v1 supports.
_SOURCE_TYPE_DATABASE = 0

# Match a bare 8-4-4-4-12 GUID so unregister-* can accept either a name or an id
# without an extra resolution GET when given an id. Deliberately rejects
# brace/paren-wrapped forms: the matched value is used un-stripped to build the
# entityset(<id>) URL, so a wrapped input must fall through to name-resolution
# (and fail cleanly) rather than build a malformed DELETE.
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _looks_like_guid(value: str) -> bool:
    """True if `value` is a bare GUID (so it can be used as an id directly)."""
    return bool(_GUID_RE.match(value.strip()))


# Step stage -> sdkmessageprocessingstep.stage option set (verified MS Learn):
# prevalidation=10, preoperation=20, postoperation=40.
_STAGE: dict[str, int] = {
    "prevalidation": 10,
    "preoperation": 20,
    "postoperation": 40,
}
# Step mode -> sdkmessageprocessingstep.mode option set (sync=0, async=1).
_MODE: dict[str, int] = {
    "sync": 0,
    "async": 1,
}
# Image type -> sdkmessageprocessingstepimage.imagetype option set
# (verified MS Learn: 0=PreImage, 1=PostImage, 2=Both).
_IMAGE_TYPE: dict[str, int] = {
    "pre": 0,
    "post": 1,
    "both": 2,
}
# Webhook auth scheme -> serviceendpoint.authtype option set (verified MS Learn:
# serviceendpoint entity reference). Only the three webhook-valid schemes are
# exposed; the Service Bus auth types (ACS / SAS*) don't apply to a webhook.
_WEBHOOK_AUTHTYPE: dict[str, int] = {
    "webhookkey": 4,       # Webhook Key (?code=<value>, e.g. Azure Functions)
    "httpheader": 5,       # Http Header (key:value pairs in the request header)
    "httpquerystring": 6,  # Http Query String (key=value query params)
}
# serviceendpoint option-set constants for a webhook (verified MS Learn):
# contract 8=Webhook, connectionmode 1=Normal, messageformat 2=Json (a webhook
# always receives the JSON RemoteExecutionContext payload).
_WEBHOOK_CONTRACT = 8
_CONNECTION_MODE_NORMAL = 1
_MESSAGE_FORMAT_JSON = 2
# Message name (lowercased) -> Request property the image snapshots, from
# MS Learn "Register a plug-in" (messages that support entity images). Send is
# deliberately absent: its property depends on what is sent (FaxId / EmailId /
# TemplateId), so it requires an explicit message_property_name. Merge also
# accepts SubordinateId; Target (the parent) is the default here.
_MESSAGE_PROPERTY: dict[str, str] = {
    "assign": "Target",
    "create": "Target",
    "delete": "Target",
    "merge": "Target",
    "route": "Target",
    "update": "Target",
    "deliverincoming": "EmailId",
    "deliverpromote": "EmailId",
    "setstate": "EntityMoniker",
}


def register_assembly(
    backend: D365Backend,
    *,
    path: str,
    name: str | None = None,
    version: str | None = None,
    culture: str | None = None,
    public_key_token: str | None = None,
    isolation_mode: str = "sandbox",
    description: str | None = None,
    solution: str | None = None,
    update: bool = False,
) -> dict[str, Any]:
    """Register (or update) a plug-in assembly from a file on disk.

    Reads the bytes at `path` and base64-encodes them into the `content` column.
    Identity is derived from the filename stem and documented defaults, with
    per-call overrides (no .NET reflection).

    `update=True` PATCHes only the `content` of the existing assembly (resolved
    by `name`, defaulting to the filename stem); returns `{updated, ...}`.
    Otherwise POSTs a new pluginassemblies row; returns `{created, ...}`.

    Raises D365Error on a missing file or an unknown isolation_mode.
    """
    if isolation_mode not in _ISOLATION_MODE:
        raise D365Error(
            f"Unknown isolation mode {isolation_mode!r}; "
            f"choose from {sorted(_ISOLATION_MODE)}."
        )
    src = Path(path)
    if not src.is_file():
        raise D365Error(f"Assembly file not found: {path}")
    raw = src.read_bytes()
    content_b64 = base64.b64encode(raw).decode("ascii")

    resolved_name = name or src.stem

    if update:
        return _update_assembly_content(
            backend, name=resolved_name, content_b64=content_b64,
            solution=solution)

    iso_int = _ISOLATION_MODE[isolation_mode]
    body: dict[str, Any] = {
        "name": resolved_name,
        "version": version or _DEFAULT_VERSION,
        "culture": culture or _DEFAULT_CULTURE,
        "publickeytoken": public_key_token or _DEFAULT_PUBLIC_KEY_TOKEN,
        "isolationmode": iso_int,
        "sourcetype": _SOURCE_TYPE_DATABASE,
        "content": content_b64,
    }
    if description is not None:
        body["description"] = description

    result = as_dict(backend.post(
        "pluginassemblies", json_body=body, solution=solution))
    if result.get("_dry_run"):
        return result

    pid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": resolved_name,
        "pluginassemblyid": pid,
        "isolationmode": iso_int,
        "version": body["version"],
        "solution": solution,
    }
    if not pid:
        entity_id_url = result.get("_entity_id_url") or ""
        out["pluginassembly_lookup_error"] = (
            f"Could not parse pluginassemblyid from response: {entity_id_url!r}"
        )
    return out


def list_types(
    backend: D365Backend, *, assembly: str | None = None,
) -> dict[str, Any]:
    """List registered plug-in types (the `plugintypes` entity set).

    A content-only Web API `POST pluginassemblies` does not create plugintype
    rows, so for assemblies registered via this CLI the listing is empty until
    each type is registered explicitly with register_type. This is a plain read
    of those rows.

    When `assembly` (an assembly NAME) is given, it is resolved to a
    `pluginassemblyid` via `_resolve_id_by_name` (a not-found name raises
    D365Error) and the listing is filtered server-side on
    `_pluginassemblyid_value`.

    Column names verified against MS Learn's plugintype entity reference:
    `typename` (fully qualified class name), `friendlyname` (display name),
    `assemblyname` (owning assembly name).
    """
    params: dict[str, str] = {
        "$select": "plugintypeid,typename,friendlyname,assemblyname",
    }
    if assembly is not None:
        pid = _resolve_id_by_name(backend, assembly)
        params["$filter"] = f"_pluginassemblyid_value eq {pid}"
    rows = backend.get_collection("plugintypes", params=params)
    return {"value": rows}


def register_type(
    backend: D365Backend,
    *,
    assembly: str,
    type_name: str,
    friendly_name: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Register one plug-in type (`plugintypes`) under an existing assembly.

    A content-only Web API `POST pluginassemblies` (what register_assembly does)
    does NOT create plugintype rows — only the Plug-in Registration Tool reflects
    an assembly client-side to create them. This verb creates a single plugintype
    row explicitly from a caller-supplied fully qualified `type_name` (no .NET
    reflection — the user names the type), bound to the assembly resolved by
    `assembly` NAME. After it, list_types shows the row and
    `register_step --plugin-type <type_name>` resolves it.

    `friendly_name` defaults to `type_name` (friendlyname is SystemRequired on
    the API). version/culture/publickeytoken are read-only — server-derived from
    the bound assembly — and are never sent. The assembly name->id resolution is
    a GET and runs live even under dry-run (mirrors register_assembly's
    force-read); the POST is short-circuited to a {_dry_run, would_create}
    preview.

    Raises D365Error if the assembly NAME is unknown.

    Column names verified against MS Learn's plugintype entity reference:
    `typename` (fully qualified class name, SystemRequired), `friendlyname`
    (display name, SystemRequired); the single-valued navigation property for the
    owning assembly lookup is `pluginassemblyid`.
    """
    pid = _resolve_id_by_name(backend, assembly)
    body: dict[str, Any] = {
        "typename": type_name,
        "friendlyname": friendly_name or type_name,
        "pluginassemblyid@odata.bind": f"/pluginassemblies({pid})",
    }
    result = as_dict(backend.post(
        "plugintypes", json_body=body, solution=solution))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result

    tid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "plugintypeid": tid,
        "typename": type_name,
        "friendlyname": friendly_name or type_name,
        "assembly": assembly,
        "pluginassemblyid": pid,
        "solution": solution,
    }
    if not tid:
        entity_id_url = result.get("_entity_id_url") or ""
        out["plugintype_lookup_error"] = (
            f"Could not parse plugintypeid from response: {entity_id_url!r}"
        )
    return out


def register_step(
    backend: D365Backend,
    *,
    message: str,
    plugin_type: str | None = None,
    entity: str | None = None,
    stage: str = "postoperation",
    mode: str = "sync",
    rank: int = 1,
    filtering_attributes: str | None = None,
    name: str | None = None,
    configuration: str | None = None,
    asyncautodelete: bool = False,
    assembly: str | None = None,
    service_endpoint: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Register an `sdkmessageprocessingstep` (a plug-in step).

    The step's event handler is either a plug-in type (`plugin_type` typename,
    optionally scoped to `assembly` by name) or a service endpoint
    (`service_endpoint` by name, e.g. a webhook registered with
    `register_webhook`) — provide exactly one. The handler is bound via the
    polymorphic `eventhandler` lookup: `plugintypeid` for a plug-in type,
    `eventhandler_serviceendpoint` for a service endpoint. Resolves the SDK
    message (by `message` name) and the chosen handler to their ids, then POSTs
    a step bound to them. When `entity` is given, the message's
    `sdkmessagefilter` for that `primaryobjecttypecode` is resolved and bound so
    the step fires only for that entity; with no `entity` the step is
    message-level (fires for all entities). A missing message, handler, or
    (entity-given) filter raises D365Error, as does passing neither or both
    handlers.

    Option-set values verified against MS Learn's sdkmessageprocessingstep
    entity reference: stage prevalidation=10 / preoperation=20 /
    postoperation=40; mode sync=0 / async=1. Raises D365Error on an unknown
    stage or mode.

    Async (`mode="async"`) requires the postoperation stage (enforced here):
    other stages raise D365Error. The derived default `name` plus a very long
    fully-qualified typename can exceed the platform's 256-char `name` limit —
    pass `name` explicitly if so (the server enforces the limit, not this code).

    Dry-run returns the backend preview as-is (no real POST).
    """
    if (plugin_type is None) == (service_endpoint is None):
        raise D365Error(
            "Provide exactly one of plugin_type or service_endpoint "
            "(a step binds to a single event handler).",
            code="StepHandlerRequired")
    if stage not in _STAGE:
        raise D365Error(
            f"Unknown stage {stage!r}; choose from {sorted(_STAGE)}.")
    if mode not in _MODE:
        raise D365Error(
            f"Unknown mode {mode!r}; choose from {sorted(_MODE)}.")
    stage_int = _STAGE[stage]
    mode_int = _MODE[mode]
    # Async plug-ins can only run in the postoperation stage (MS Learn:
    # register-plug-in#step-registration). Guard before any HTTP call.
    if mode_int == 1 and stage_int != 40:
        raise D365Error(
            f"Asynchronous mode requires the postoperation stage (got {stage!r}).",
            code="AsyncRequiresPostOperation")

    # The same _resolve_* id lookups run for a real write and a dry-run preview
    # (reads-execute rule). For a real write a missing message/type/filter raises;
    # under dry-run we instead report each as a reference so a dangling one is a
    # pre-flight finding, not a server fault (#281). An id that does resolve is
    # reused so the preview body matches what the real write would POST.
    references: list[ref_mod.Reference] = []
    plugintype_id: str | None = None
    serviceendpoint_id: str | None = None
    if backend.dry_run:
        message_id = _resolve_or_none(
            lambda: _resolve_sdkmessage_id(backend, message), "SdkMessageNotFound")
        references.append(ref_mod.make_reference(
            "message", message, message_id is not None))
        if plugin_type is not None:
            plugintype_id = _resolve_or_none(
                lambda: _resolve_plugintype_id(backend, plugin_type, assembly),
                "PluginTypeNotFound")
            references.append(ref_mod.make_reference(
                "plugin_type", plugin_type, plugintype_id is not None))
        elif service_endpoint is not None:  # exactly-one guard above ensures this
            serviceendpoint_id = _resolve_or_none(
                lambda: _resolve_serviceendpoint_id(backend, service_endpoint),
                "ServiceEndpointNotFound")
            references.append(ref_mod.make_reference(
                "service_endpoint", service_endpoint,
                serviceendpoint_id is not None))
        filter_id: str | None = None
        if entity is not None:
            # The filter binds an entity to a message, so it can only resolve when
            # the message itself does.
            if message_id is not None:
                filter_id = _resolve_or_none(
                    lambda: _resolve_sdkmessagefilter_id(backend, entity, message_id),
                    "SdkMessageFilterNotFound")
            references.append(ref_mod.make_reference(
                "entity", entity, filter_id is not None))
    else:
        message_id = _resolve_sdkmessage_id(backend, message)
        if plugin_type is not None:
            plugintype_id = _resolve_plugintype_id(backend, plugin_type, assembly)
        elif service_endpoint is not None:  # exactly-one guard above ensures this
            serviceendpoint_id = _resolve_serviceendpoint_id(
                backend, service_endpoint)
        filter_id = (_resolve_sdkmessagefilter_id(backend, entity, message_id)
                     if entity is not None else None)

    handler_label = plugin_type or service_endpoint
    resolved_name = name or (
        f"{handler_label}: {message} of {entity or 'any entity'}")
    body: dict[str, Any] = {
        "name": resolved_name,
        "stage": stage_int,
        "mode": mode_int,
        "rank": rank,
    }
    if configuration is not None:
        body["configuration"] = configuration
    if asyncautodelete:
        body["asyncautodelete"] = True
    # sdkmessageprocessingstep nav-props are lowercase logical names in $metadata;
    # PascalCase is rejected with HTTP 400 (issue #159). For a real write the ids
    # are always present; under dry-run a dangling reference simply omits its bind
    # from the (echo-only) preview body.
    if message_id is not None:
        body["sdkmessageid@odata.bind"] = f"/sdkmessages({message_id})"
    if plugintype_id is not None:
        body["plugintypeid@odata.bind"] = f"/plugintypes({plugintype_id})"
    if serviceendpoint_id is not None:
        body["eventhandler_serviceendpoint@odata.bind"] = (
            f"/serviceendpoints({serviceendpoint_id})")
    if entity is not None and filter_id is not None:
        body["sdkmessagefilterid@odata.bind"] = (
            f"/sdkmessagefilters({filter_id})")
    if filtering_attributes is not None and message.lower() == "update":
        body["filteringattributes"] = filtering_attributes

    result = as_dict(backend.post(
        "sdkmessageprocessingsteps", json_body=body, solution=solution))
    if result.get("_dry_run"):
        if references:
            result["references"] = references
        return result

    sid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "sdkmessageprocessingstepid": sid,
        "name": resolved_name,
        "stage": stage_int,
        "mode": mode_int,
        "message": message,
        "entity": entity,
        "plugintype": plugin_type,
        "service_endpoint": service_endpoint,
        "solution": solution,
    }
    if not sid:
        entity_id_url = result.get("_entity_id_url") or ""
        out["sdkmessageprocessingstep_lookup_error"] = (
            "Could not parse sdkmessageprocessingstepid from response: "
            f"{entity_id_url!r}"
        )
    return out


def set_step_state(
    backend: D365Backend,
    *,
    step: str,
    enable: bool,
) -> dict[str, Any]:
    """Toggle the state of an sdkmessageprocessingstep (enable/disable)."""
    step_id, _, _ = _step_image_info(backend, step)
    # statecode 0=Enabled, 1=Disabled; statuscode 1=Enabled, 2=Disabled
    state_int = 0 if enable else 1
    status_int = 1 if enable else 2
    body = {"statecode": state_int, "statuscode": status_int}
    backend.patch(f"sdkmessageprocessingsteps({step_id})", json_body=body)
    return {
        "updated": True,
        "sdkmessageprocessingstepid": step_id,
        "enabled": enable,
    }


def register_webhook(
    backend: D365Backend,
    *,
    name: str,
    url: str,
    auth: str,
    auth_value: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """Register a webhook `serviceendpoint` (contract=8).

    POSTs a serviceendpoint row for an HTTP webhook: `url` is the endpoint the
    platform POSTs the JSON execution context to, `auth` selects the
    authentication scheme the endpoint expects, and `auth_value` is the
    corresponding secret (the `?code=` value for webhookkey, or the header /
    query-string key-value pairs). The auth value is write-only on the platform
    and is never echoed back. Raises D365Error on an unknown `auth` scheme.

    The serviceendpoint has no foreign keys to resolve, so a dry-run simply
    returns the backend preview (no reference probe, unlike register_step).

    Option-set values verified against MS Learn's serviceendpoint entity
    reference: authtype webhookkey=4 / httpheader=5 / httpquerystring=6;
    contract 8=Webhook, connectionmode 1=Normal, messageformat 2=Json.
    """
    if auth not in _WEBHOOK_AUTHTYPE:
        raise D365Error(
            f"Unknown auth scheme {auth!r}; choose from "
            f"{sorted(_WEBHOOK_AUTHTYPE)}.")
    authtype_int = _WEBHOOK_AUTHTYPE[auth]
    body: dict[str, Any] = {
        "name": name,
        "url": url,
        "contract": _WEBHOOK_CONTRACT,
        "connectionmode": _CONNECTION_MODE_NORMAL,
        "messageformat": _MESSAGE_FORMAT_JSON,
        "authtype": authtype_int,
        "authvalue": auth_value,
    }
    result = as_dict(backend.post(
        "serviceendpoints", json_body=body, solution=solution))
    if result.get("_dry_run"):
        return result

    sid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "serviceendpointid": sid,
        "name": name,
        "url": url,
        "contract": _WEBHOOK_CONTRACT,
        "authtype": authtype_int,
        "solution": solution,
    }
    if not sid:
        entity_id_url = result.get("_entity_id_url") or ""
        out["serviceendpoint_lookup_error"] = (
            f"Could not parse serviceendpointid from response: {entity_id_url!r}"
        )
    return out


def register_image(
    backend: D365Backend,
    *,
    step: str,
    image_type: str,
    alias: str,
    attributes: str | None = None,
    name: str | None = None,
    message_property_name: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Register an `sdkmessageprocessingstepimage` (a step entity image).

    `step` is used directly when it looks like a GUID; otherwise it is resolved
    by exact name. The step's message and stage are read to derive
    `messagepropertyname` (see `_MESSAGE_PROPERTY`) and to enforce the platform
    validity rules client-side.
    """
    if image_type not in _IMAGE_TYPE:
        raise D365Error(
            f"Unknown image type {image_type!r}; "
            f"choose from {sorted(_IMAGE_TYPE)}.")
    type_int = _IMAGE_TYPE[image_type]
    step_id, stage_int, message_id = _step_image_info(backend, step)
    message_name = _resolve_sdkmessage_name(backend, message_id)
    # Platform validity rules (MS Learn "Register a plug-in"), enforced
    # client-side so the failure is explanatory instead of raw server noise.
    if type_int == 1 and stage_int != _STAGE["postoperation"]:
        raise D365Error(
            "A post-image requires a step registered in the PostOperation "
            f"stage (step {step!r} is registered in stage {stage_int}).",
            code="PostImageRequiresPostOperation")
    if type_int == 0 and message_name.lower() == "create":
        raise D365Error(
            "A pre-image is not available on a Create-message step "
            "(the record does not exist before the operation).",
            code="PreImageInvalidForCreate")
    if type_int == 1 and message_name.lower() == "delete":
        raise D365Error(
            "A post-image is not available on a Delete-message step "
            "(the record no longer exists after the operation).",
            code="PostImageInvalidForDelete")
    mpn = message_property_name or _MESSAGE_PROPERTY.get(message_name.lower())
    if mpn is None:
        raise D365Error(
            f"Cannot derive the image's message property for message "
            f"{message_name!r}. Only "
            f"{sorted(_MESSAGE_PROPERTY)} (and Send, via an explicit "
            f"property) support entity images; pass message_property_name "
            f"(--message-property-name) to override.",
            code="MessagePropertyUnknown")

    resolved_name = name or alias
    body: dict[str, Any] = {
        "name": resolved_name,
        "entityalias": alias,
        "imagetype": type_int,
        "messagepropertyname": mpn,
        # sdkmessageprocessingstepimage nav-props are lowercase logical names
        # in $metadata; PascalCase is rejected with HTTP 400 (issue #159).
        "sdkmessageprocessingstepid@odata.bind": (
            f"/sdkmessageprocessingsteps({step_id})"),
    }
    # Omitting `attributes` means ALL columns are included in the image — a
    # documented performance anti-pattern, so callers should pass a filter.
    if attributes is not None:
        body["attributes"] = attributes

    result = as_dict(backend.post(
        "sdkmessageprocessingstepimages", json_body=body, solution=solution))
    if result.get("_dry_run"):
        return result

    iid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "sdkmessageprocessingstepimageid": iid,
        "name": resolved_name,
        "entityalias": alias,
        "imagetype": type_int,
        "messagepropertyname": mpn,
        "sdkmessageprocessingstepid": step_id,
        "attributes": attributes,
        "solution": solution,
    }
    if not iid:
        entity_id_url = result.get("_entity_id_url") or ""
        out["sdkmessageprocessingstepimage_lookup_error"] = (
            "Could not parse sdkmessageprocessingstepimageid from response: "
            f"{entity_id_url!r}"
        )
    return out


def _step_image_info(
    backend: D365Backend, step: str,
) -> tuple[str, int, str]:
    """Resolve a step (GUID or exact name) to (id, stage, sdkmessage id).

    Force-reads so image registration can derive messagepropertyname and
    validate stage rules even under dry-run.
    """
    step = step.strip()
    if _looks_like_guid(step):
        filt = f"sdkmessageprocessingstepid eq {step}"
    else:
        filt = f"name eq {odata_literal(step)}"
    rows = _force_read_rows(
        backend, "sdkmessageprocessingsteps",
        {"$filter": filt,
         "$select": "sdkmessageprocessingstepid,stage,_sdkmessageid_value"})
    if not rows or not rows[0].get("sdkmessageprocessingstepid"):
        raise D365Error(
            f"Plug-in step not found: {step}", code="SdkStepNotFound")
    if len(rows) > 1:
        raise D365Error(
            f"Multiple plug-in steps match name {step!r}; "
            f"pass the step's GUID instead.",
            code="AmbiguousStepName")
    row = rows[0]
    return (
        str(row["sdkmessageprocessingstepid"]),
        int(row.get("stage") or 0),
        str(row.get("_sdkmessageid_value") or ""),
    )


def _resolve_sdkmessage_name(backend: D365Backend, message_id: str) -> str:
    """Resolve an SDK message's name by id (force-reads)."""
    rows = _force_read_rows(
        backend, "sdkmessages",
        {"$filter": f"sdkmessageid eq {message_id}", "$select": "name"})
    if not rows or not rows[0].get("name"):
        raise D365Error(
            f"SDK message not found: {message_id}", code="SdkMessageNotFound")
    return str(rows[0]["name"])


def unregister_image(backend: D365Backend, image: str) -> dict[str, Any]:
    """Unregister (delete) an `sdkmessageprocessingstepimage` by name or id.

    `image` is used directly when it looks like a GUID; otherwise it is
    resolved by exact name (force-reads, mirrors unregister_step). Dry-run
    passes through the backend's delete preview (no real DELETE).
    """
    image = image.strip()
    image_id = image if _looks_like_guid(image) else (
        _resolve_image_id(backend, image))
    result = as_dict(backend.delete(
        f"sdkmessageprocessingstepimages({image_id})"))
    if result.get("_dry_run"):
        return result
    return {"deleted": True, "sdkmessageprocessingstepimageid": image_id}


def _resolve_image_id(backend: D365Backend, name: str) -> str:
    """Resolve an sdkmessageprocessingstepimage id by exact name (force-reads)."""
    rows = _force_read_rows(
        backend, "sdkmessageprocessingstepimages",
        {"$filter": f"name eq {odata_literal(name)}",
         "$select": "sdkmessageprocessingstepimageid"})
    if not rows or not rows[0].get("sdkmessageprocessingstepimageid"):
        raise D365Error(
            f"Plug-in step image not found: {name}", code="SdkImageNotFound")
    if len(rows) > 1:
        # Image names are not unique across steps; refuse to guess.
        raise D365Error(
            f"Multiple plug-in step images match name {name!r}; "
            f"pass the image's GUID instead.",
            code="AmbiguousImageName")
    return str(rows[0]["sdkmessageprocessingstepimageid"])


def unregister_step(backend: D365Backend, step: str) -> dict[str, Any]:
    """Unregister (delete) an `sdkmessageprocessingstep` by name or id.

    `step` is used directly when it looks like a GUID; otherwise it is resolved
    by exact name (the lookup force-reads even under dry-run so a dry-run delete
    still targets the live id). A name that matches no step raises D365Error.

    Deleting the step cascades its registered entity images (sdkmessageprocessingstepimage)
    automatically — no separate image delete is needed (MS Learn). Dry-run
    passes through the backend's delete preview (no real DELETE).
    """
    step = step.strip()
    step_id = step if _looks_like_guid(step) else _resolve_step_id(backend, step)
    result = as_dict(backend.delete(f"sdkmessageprocessingsteps({step_id})"))
    if result.get("_dry_run"):
        return result
    return {"deleted": True, "sdkmessageprocessingstepid": step_id}


def unregister_assembly(
    backend: D365Backend, assembly: str,
) -> dict[str, Any]:
    """Unregister (delete) a plug-in assembly and its dependent steps.

    `assembly` is used directly when it looks like a GUID; otherwise it is
    resolved by exact name. Because the platform refuses to delete an assembly
    while any sdkmessageprocessingstep still depends on it, this first collects
    the dependent steps (assembly -> plugintypes -> steps) and DELETEs each one,
    THEN DELETEs the assembly. Deleting a step cascades its entity images, and
    deleting the assembly cascades its plugintypes — neither is deleted here
    explicitly (MS Learn).

    Under dry-run no real DELETE is issued: the resolution GETs still force-read
    (mirrors the rest of this module) and a `_dry_run` preview naming the
    assembly, the dependent step count, and the step ids is returned.

    Raises D365Error when an assembly name resolves to nothing.
    """
    assembly = assembly.strip()
    aid = assembly if _looks_like_guid(assembly) else (
        _resolve_id_by_name(backend, assembly))
    step_ids = _dependent_step_ids(backend, aid)

    if backend.dry_run:
        return {
            "_dry_run": True,
            "deleted": True,
            "pluginassemblyid": aid,
            "steps_deleted": len(step_ids),
            "deleted_step_ids": step_ids,
        }

    # Dependent steps first (images cascade with each step), then the assembly
    # (plugintypes cascade with it).
    for sid in step_ids:
        backend.delete(f"sdkmessageprocessingsteps({sid})")
    backend.delete(f"pluginassemblies({aid})")
    return {
        "deleted": True,
        "pluginassemblyid": aid,
        "steps_deleted": len(step_ids),
        "deleted_step_ids": step_ids,
    }


def _resolve_step_id(backend: D365Backend, name: str) -> str:
    """Resolve an sdkmessageprocessingstep id by exact name (force-reads)."""
    rows = _force_read_rows(
        backend, "sdkmessageprocessingsteps",
        {"$filter": f"name eq {odata_literal(name)}",
         "$select": "sdkmessageprocessingstepid"})
    if not rows or not rows[0].get("sdkmessageprocessingstepid"):
        raise D365Error(
            f"Plug-in step not found: {name}", code="SdkStepNotFound")
    if len(rows) > 1:
        # The platform does not enforce unique step names; refuse to guess which
        # one to delete. The caller must disambiguate with the step's GUID.
        raise D365Error(
            f"Multiple plug-in steps match name {name!r}; "
            f"pass the step's GUID instead.",
            code="AmbiguousStepName")
    return str(rows[0]["sdkmessageprocessingstepid"])


def _dependent_step_ids(backend: D365Backend, assembly_id: str) -> list[str]:
    """Collect ids of every step that depends on `assembly_id`.

    Walks the dependency chain assembly -> plugintypes -> steps. Both GETs
    force-read so the set is correct even under dry-run.
    """
    type_rows = _force_read_rows(
        backend, "plugintypes",
        {"$filter": f"_pluginassemblyid_value eq {assembly_id}",
         "$select": "plugintypeid"})
    step_ids: list[str] = []
    for tr in type_rows:
        ptid = tr.get("plugintypeid")
        if not ptid:
            continue
        step_rows = _force_read_rows(
            backend, "sdkmessageprocessingsteps",
            {"$filter": f"_plugintypeid_value eq {ptid}",
             "$select": "sdkmessageprocessingstepid"})
        step_ids.extend(
            str(sr["sdkmessageprocessingstepid"]) for sr in step_rows
            if sr.get("sdkmessageprocessingstepid"))
    return step_ids


def _force_read_rows(
    backend: D365Backend, entity_set: str, params: dict[str, str],
) -> list[dict[str, Any]]:
    """GET `entity_set` rows, forcing a real read even under dry-run.

    A step is POSTed only after its bound ids are resolved, so the resolution
    GETs must run for real even in dry-run (mirrors `_resolve_id_by_name`).
    """
    return backend.get_collection(entity_set, params=params, max_pages=1)


def _resolve_or_none(
    resolve: Callable[[], str], not_found_code: str,
) -> str | None:
    """Run a raising ``_resolve_*`` id lookup, returning None for its documented
    not-found code instead of raising — the dry-run reference-probe form (#281).
    Any other ``D365Error`` (a real transport/auth failure) still propagates.
    """
    try:
        return resolve()
    except D365Error as exc:
        if exc.code == not_found_code:
            return None
        raise


def _resolve_sdkmessage_id(backend: D365Backend, message: str) -> str:
    """Resolve an SDK message id by exact name (e.g. Create/Update/Delete)."""
    mid = backend.resolve_id_by_name(
        "sdkmessages", filter_field="name", id_field="sdkmessageid",
        value=message)
    if not mid:
        raise D365Error(
            f"SDK message not found: {message}", code="SdkMessageNotFound")
    return mid


def _resolve_plugintype_id(
    backend: D365Backend, typename: str, assembly: str | None,
) -> str:
    """Resolve a plug-in type id by typename, optionally scoped to an assembly.

    When `assembly` (an assembly NAME) is given it is resolved to a
    `pluginassemblyid` and ANDed into the filter to disambiguate. Without it the
    first matching row is used.
    """
    filt = f"typename eq {odata_literal(typename)}"
    if assembly is not None:
        pid = _resolve_id_by_name(backend, assembly)
        filt += f" and _pluginassemblyid_value eq {pid}"
    rows = _force_read_rows(
        backend, "plugintypes",
        {"$filter": filt, "$select": "plugintypeid,typename"})
    if not rows or not rows[0].get("plugintypeid"):
        raise D365Error(
            f"Plug-in type not found: {typename}", code="PluginTypeNotFound")
    return str(rows[0]["plugintypeid"])


def _resolve_serviceendpoint_id(backend: D365Backend, name: str) -> str:
    """Resolve a service endpoint id by exact name.

    Forces a real read even under dry-run (mirrors the other step-handler
    resolvers — a step is POSTed only after its bound id is known).
    """
    sid = backend.resolve_id_by_name(
        "serviceendpoints", filter_field="name",
        id_field="serviceendpointid", value=name)
    if not sid:
        raise D365Error(
            f"Service endpoint not found: {name}",
            code="ServiceEndpointNotFound")
    return sid


def _resolve_sdkmessagefilter_id(
    backend: D365Backend, entity: str, message_id: str,
) -> str:
    """Resolve the sdkmessagefilter id for an entity + message pair.

    Raises D365Error when the message does not support the entity (no filter
    row), since binding one is required for an entity-scoped step.
    """
    filt = (f"primaryobjecttypecode eq {odata_literal(entity)} "
            f"and _sdkmessageid_value eq {message_id}")
    rows = _force_read_rows(
        backend, "sdkmessagefilters",
        {"$filter": filt, "$select": "sdkmessagefilterid"})
    if not rows or not rows[0].get("sdkmessagefilterid"):
        raise D365Error(
            f"No SDK message filter for entity {entity!r} on this message "
            "(that message does not support that entity).",
            code="SdkMessageFilterNotFound")
    return str(rows[0]["sdkmessagefilterid"])


def _update_assembly_content(
    backend: D365Backend, *, name: str, content_b64: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """PATCH only the `content` of an existing assembly resolved by name.

    When `solution` is set, the PATCH carries `MSCRM.SolutionUniqueName` so the
    update lands in that solution (mirrors webresource.update_webresource).
    """
    pid = _resolve_id_by_name(backend, name)
    result = as_dict(backend.patch(
        f"pluginassemblies({pid})", json_body={"content": content_b64},
        solution=solution))
    if result.get("_dry_run"):
        return result
    return {
        "updated": True,
        "name": name,
        "pluginassemblyid": pid,
        "fields": ["content"],
        "solution": solution,
    }


def _resolve_id_by_name(backend: D365Backend, name: str) -> str:
    """Resolve a plug-in assembly's id by exact name.

    Forces a real read even under dry-run (a PATCH preview needs the real id;
    mirrors webresource._resolve_id_by_name).
    """
    pid = backend.resolve_id_by_name(
        "pluginassemblies", filter_field="name", id_field="pluginassemblyid",
        value=name)
    if not pid:
        raise D365Error(
            f"Plug-in assembly not found: {name}", code="PluginAssemblyNotFound")
    return pid


# ── Convergent apply support (#552) ──────────────────────────────────────────
#
# `apply` declares a plug-in (assembly + types + steps + images) in a spec and
# drives the register_* verbs above to converge an org to it. These read helpers
# let apply decide create-vs-reconcile and diff a live component against the
# spec, and `update_step` PATCHes a step's runtime config in place. All reads
# force a real GET even under dry-run (the reads-execute rule), so a dry-run
# previews accurate drift.

# Read-only views of the stage/mode option-set maps so apply's plug-in reconcile
# can compare a live step's stored ints against a desired stage/mode NAME without
# duplicating the mapping (single source of truth: _STAGE / _MODE).
STAGE_VALUES: dict[str, int] = dict(_STAGE)
MODE_VALUES: dict[str, int] = dict(_MODE)


def find_assembly(backend: D365Backend, name: str) -> dict[str, Any] | None:
    """Find a plug-in assembly by exact name; return its row or None.

    Selects the base64 `content` so apply can diff a live assembly against a
    rebuilt DLL. Returns the first match (assembly names are unique per org).
    """
    rows = backend.get_collection(
        "pluginassemblies",
        params={"$filter": f"name eq {odata_literal(name)}",
                "$select": "pluginassemblyid,name,content"},
        max_pages=1)
    return rows[0] if rows else None


def find_step(backend: D365Backend, name: str) -> dict[str, Any] | None:
    """Find an sdkmessageprocessingstep by exact name; return its row or None.

    Selects the in-place-updatable config columns (stage / mode / rank /
    filteringattributes / configuration) and expands the binding identity —
    message name, plug-in typename, and the entity filter's
    primaryobjecttypecode — so apply can diff a step in a single GET (the
    expand set MS Learn uses to inspect a registered step). Raises D365Error
    when the name matches more than one step: step names are not unique, so a
    declared step must use a unique name to be reconciled unambiguously.
    """
    rows = backend.get_collection(
        "sdkmessageprocessingsteps",
        params={
            "$filter": f"name eq {odata_literal(name)}",
            "$select": ("sdkmessageprocessingstepid,stage,mode,rank,"
                        "filteringattributes,configuration"),
            "$expand": ("sdkmessageid($select=name),"
                        "plugintypeid($select=typename),"
                        "sdkmessagefilterid($select=primaryobjecttypecode)"),
        },
        max_pages=1)
    if not rows:
        return None
    if len(rows) > 1:
        raise D365Error(
            f"Multiple plug-in steps match name {name!r}; step names are not "
            "unique — declare a unique name per step so apply can reconcile it.",
            code="AmbiguousStepName")
    return rows[0]


def find_step_image(
    backend: D365Backend, step_id: str, alias: str,
) -> dict[str, Any] | None:
    """Find a step's entity image by alias; return its row or None.

    An image's identity within a step is its `entityalias`, so apply probes by
    (step, alias) to decide whether to register a declared image.
    """
    rows = backend.get_collection(
        "sdkmessageprocessingstepimages",
        params={
            "$filter": (f"_sdkmessageprocessingstepid_value eq {step_id} "
                        f"and entityalias eq {odata_literal(alias)}"),
            "$select": ("sdkmessageprocessingstepimageid,entityalias,"
                        "imagetype,attributes"),
        },
        max_pages=1)
    return rows[0] if rows else None


def update_step(
    backend: D365Backend,
    *,
    step_id: str,
    stage: str | None = None,
    mode: str | None = None,
    rank: int | None = None,
    filtering_attributes: str | None = None,
    configuration: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """PATCH a step's runtime config in place (no delete-and-recreate).

    Only the provided fields are sent. `stage`/`mode` are mapped to their
    option-set ints (_STAGE / _MODE); an unknown value raises D365Error. Carries
    `MSCRM.SolutionUniqueName` when `solution` is given (mirrors the other write
    verbs). Dry-run safe — the PATCH short-circuits to a preview. Returns
    {updated, fields}.

    apply classifies a message / entity / plug-in-type change as
    replace-blocked (the platform fixes the binding at creation); this verb
    handles only the updatable runtime config — MS Learn explicitly recommends
    updating an existing step rather than deleting and recreating it.
    """
    body: dict[str, Any] = {}
    if stage is not None:
        if stage not in _STAGE:
            raise D365Error(
                f"Unknown stage {stage!r}; choose from {sorted(_STAGE)}.")
        body["stage"] = _STAGE[stage]
    if mode is not None:
        if mode not in _MODE:
            raise D365Error(
                f"Unknown mode {mode!r}; choose from {sorted(_MODE)}.")
        body["mode"] = _MODE[mode]
    if rank is not None:
        body["rank"] = rank
    if filtering_attributes is not None:
        body["filteringattributes"] = filtering_attributes
    if configuration is not None:
        body["configuration"] = configuration
    result = as_dict(backend.patch(
        f"sdkmessageprocessingsteps({step_id})", json_body=body,
        solution=solution))
    if result.get("_dry_run"):
        return result
    return {
        "updated": True,
        "sdkmessageprocessingstepid": step_id,
        "fields": sorted(body),
    }
