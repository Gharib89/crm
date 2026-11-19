"""Load window: turn off the custom logic on the tables a data load touches, then restore it.

``begin`` finds, on the named tables, the enabled custom plug-in steps, the
activated classic workflows and the published duplicate-detection rules, writes
them to a state file, then turns each off through the same core operations as
``plugin set-step-state``, ``workflow deactivate`` and ``dup unpublish``.
``end`` reads that file back and turns on only what ``begin`` turned off.

A plug-in step is custom when its assembly is not signed with Microsoft's key.
``customizationlevel`` does not tell them apart: measured on on-prem v9.1 and on
Dataverse online, thousands of Microsoft-signed steps carry level 1, the level a
custom step carries. Every Microsoft plug-in assembly on both carried the
public key token below.

The state file is the restore record, so it is written before the first change
and rewritten after each: a component reads ``attempted`` just before its write
and ``turned_off`` or ``failed`` after it. An interrupted ``begin`` leaves a file
``end`` can restore from.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from xml.sax.saxutils import escape

from crm.core import dup as dup_mod
from crm.core import entity_names
from crm.core import plugin as plugin_mod
from crm.core import workflow as workflow_mod
from crm.core.connection import whoami
from crm.core.state_home import atomic_write_json
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

MICROSOFT_PUBLIC_KEY_TOKEN = "31bf3856ad364e35"
STATE_FORMAT = "crm-load-window"
STATE_VERSION = 1

# Outcomes ``end`` restores: ``attempted`` is a write begin started but did not
# record the answer to, so the component may be off.
_RESTORABLE = frozenset({"turned_off", "attempted"})

# kind -> (entity set, statecode when active, prior-state word)
_KINDS: dict[str, tuple[str, int, str]] = {
    "plugin_step": ("sdkmessageprocessingsteps", 0, "enabled"),
    "workflow": ("workflows", workflow_mod.STATE_ACTIVATED[0], "activated"),
    "duplicate_rule": (dup_mod.RULES_SET, 1, "published"),
}


def _refuse_read_only(backend: D365Backend) -> None:
    # Refused here, before the state file is written, rather than at the first
    # write: a begin refused mid-way would leave a file naming components it
    # never turned off. A dry run previews, as the backend's own guard allows.
    if backend.read_only and not backend.dry_run:
        name = backend.profile.name
        raise D365Error(
            f"Profile {name!r} is read-only; writes are blocked. "
            f"Clear it interactively: crm profile edit {name} --no-read-only"
        )


def _steps_fetch(tables: list[str]) -> str:
    """Enabled steps on *tables* whose plug-in assembly is not Microsoft-signed."""
    values = "".join(f"<value>{escape(t)}</value>" for t in tables)
    return (
        '<fetch><entity name="sdkmessageprocessingstep">'
        '<attribute name="sdkmessageprocessingstepid"/><attribute name="name"/>'
        '<filter><condition attribute="statecode" operator="eq" value="0"/></filter>'
        '<link-entity name="sdkmessagefilter" from="sdkmessagefilterid" '
        'to="sdkmessagefilterid" alias="f"><attribute name="primaryobjecttypecode"/>'
        f'<filter><condition attribute="primaryobjecttypecode" operator="in">{values}'
        "</condition></filter></link-entity>"
        '<link-entity name="plugintype" from="plugintypeid" to="plugintypeid">'
        '<link-entity name="pluginassembly" from="pluginassemblyid" to="pluginassemblyid">'
        '<filter type="or"><condition attribute="publickeytoken" operator="ne" '
        f'value="{MICROSOFT_PUBLIC_KEY_TOKEN}"/>'
        '<condition attribute="publickeytoken" operator="null"/></filter>'
        "</link-entity></link-entity></entity></fetch>"
    )


def _any_of(field: str, tables: list[str]) -> str:
    return "(" + " or ".join(f"{field} eq {odata_literal(t)}" for t in tables) + ")"


def _component(kind: str, cid: Any, name: Any, table: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": cid,
        "name": name,
        "table": table,
        "prior_state": _KINDS[kind][2],
        "outcome": "pending",
    }


def find_components(backend: D365Backend, tables: list[str]) -> list[dict[str, Any]]:
    """The components ``begin`` turns off on *tables*, each with outcome ``pending``.

    Raises :class:`D365Error` naming any table the org does not know, since a
    misspelt table would otherwise select nothing and turn nothing off.
    """
    names = entity_names.load_name_map(backend)
    unknown = [t for t in tables if not names.set_for(t)]
    if unknown:
        raise D365Error(
            f"Unknown table logical name(s): {', '.join(unknown)}.",
            code="UnknownEntityLogicalName",
        )
    found: list[dict[str, Any]] = []
    steps = as_dict(
        backend.get("sdkmessageprocessingsteps", params={"fetchXml": _steps_fetch(tables)})
    ).get("value")
    for row in cast("list[dict[str, Any]]", steps or []):
        found.append(
            _component(
                "plugin_step",
                row.get("sdkmessageprocessingstepid"),
                row.get("name"),
                row.get("f.primaryobjecttypecode"),
            )
        )
    workflows = backend.get_collection(
        "workflows",
        params={
            # category 0 is the classic workflow, type 1 its definition (not an
            # activation record); business rules and BPFs carry other categories.
            "$filter": "category eq 0 and type eq 1 and statecode eq 1 and "
            + _any_of("primaryentity", tables),
            "$select": "workflowid,name,primaryentity",
        },
    )
    for row in workflows:
        found.append(
            _component("workflow", row.get("workflowid"), row.get("name"), row.get("primaryentity"))
        )
    rules = backend.get_collection(
        dup_mod.RULES_SET,
        params={
            "$filter": "statecode eq 1 and " + _any_of("baseentityname", tables),
            "$select": "duplicateruleid,name,baseentityname",
        },
    )
    for row in rules:
        found.append(
            _component(
                "duplicate_rule",
                row.get("duplicateruleid"),
                row.get("name"),
                row.get("baseentityname"),
            )
        )
    return found


def _counts(components: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, int]:
    return {k: sum(1 for c in components if c["outcome"] == k) for k in keys}


def begin(backend: D365Backend, tables: list[str], state_file: Path) -> dict[str, Any]:
    """Turn off the custom logic on *tables*, recording it in *state_file*.

    Refuses an existing *state_file* (it is the restore record of an earlier
    window) and a read-only profile. A dry run reads the org, lists what would be
    turned off, and writes neither the org nor the file. A component that fails
    to turn off is recorded ``failed`` with the reason, and the rest carry on.
    """
    if state_file.exists():
        raise D365Error(
            f"State file {state_file} already exists; it may be the restore record "
            "of an earlier load window. Run `crm data load-window end` on it, or "
            "name another file."
        )
    _refuse_read_only(backend)
    components = find_components(backend, tables)
    org = whoami(backend).get("OrganizationId")
    state: dict[str, Any] = {
        "format": STATE_FORMAT,
        "version": STATE_VERSION,
        "org": {
            "organization_id": org,
            "url": backend.profile.url,
            "profile": backend.profile.name,
        },
        "begun_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tables": tables,
        "components": components,
    }
    if backend.dry_run:
        for c in components:
            c["outcome"] = "would_turn_off"
        return {
            "_dry_run": True,
            **state,
            "counts": {"would_turn_off": len(components)},
        }
    atomic_write_json(state_file, state)
    for c in components:
        c["outcome"] = "attempted"
        atomic_write_json(state_file, state)
        try:
            _turn(backend, c, on=False)
        except D365Error as exc:
            c["outcome"] = "failed"
            c["error"] = str(exc)
        else:
            c["outcome"] = "turned_off"
        atomic_write_json(state_file, state)
    return {
        "state_file": str(state_file),
        **state,
        "counts": _counts(components, ("turned_off", "failed")),
    }


def _turn(backend: D365Backend, component: dict[str, Any], *, on: bool) -> None:
    kind, cid = component["kind"], component["id"]
    if kind == "plugin_step":
        plugin_mod.set_step_state(backend, step=cid, enable=on)
    elif kind == "workflow":
        workflow_mod.set_workflow_state(backend, cid, activate=on)
    elif on:
        # Wait for the publish job, so `restored` means published, not submitted.
        dup_mod.publish_rule(backend, cid, wait=True)
    else:
        dup_mod.unpublish_rule(backend, cid)


def _read_state_file(state_file: Path) -> dict[str, Any]:
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise D365Error(f"Cannot read {state_file} as a load-window state file: {exc}") from exc
    if not isinstance(state, dict) or cast("dict[str, Any]", state).get("format") != STATE_FORMAT:
        raise D365Error(f"{state_file} is not a load-window state file.")
    return cast("dict[str, Any]", state)


def end(backend: D365Backend, state_file: Path) -> dict[str, Any]:
    """Turn back on what ``begin`` turned off, as *state_file* records it.

    Refuses a file taken from another org (by WhoAmI organization id) and a
    read-only profile. Each restorable component's live state is read first: one
    already active is skipped and counted, so a re-run finishes a partial
    restore. A failure is recorded with its reason and the rest carry on. The
    file is only read, never rewritten.
    """
    state = _read_state_file(state_file)
    _refuse_read_only(backend)
    recorded = str(cast("dict[str, Any]", state.get("org") or {}).get("organization_id") or "")
    connected = str(whoami(backend).get("OrganizationId") or "")
    if recorded.lower() != connected.lower():
        raise D365Error(
            f"{state_file} was taken from org {recorded or '(none recorded)'}, but this "
            f"profile is connected to org {connected}. Run end against the org begin ran on.",
            code="LoadWindowOrgMismatch",
        )
    results: list[dict[str, Any]] = []
    for c in cast("list[dict[str, Any]]", state.get("components") or []):
        result = {k: c.get(k) for k in ("kind", "id", "name", "table")}
        results.append(result)
        if c.get("outcome") not in _RESTORABLE:
            result["outcome"] = "not_turned_off"
            continue
        entity_set, active_state, _ = _KINDS[c["kind"]]
        try:
            current = as_dict(
                backend.get(f"{entity_set}({c['id']})", params={"$select": "statecode"})
            )
            if current.get("statecode") == active_state:
                result["outcome"] = "already_active"
            elif backend.dry_run:
                result["outcome"] = "would_restore"
            else:
                _turn(backend, c, on=True)
                result["outcome"] = "restored"
        except D365Error as exc:
            result["outcome"] = "failed"
            result["error"] = str(exc)
    keys = ("restored", "already_active", "failed", "not_turned_off")
    if backend.dry_run:
        keys = ("would_restore", "already_active", "failed", "not_turned_off")
    out: dict[str, Any] = {
        "state_file": str(state_file),
        "org": state.get("org"),
        "components": results,
        "counts": _counts(results, keys),
    }
    if backend.dry_run:
        out = {"_dry_run": True, **out}
    return out
