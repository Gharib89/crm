"""Plan-artifact serialization for approval-gated apply (ADR 0022, slice 1).

A **plan** is a self-contained JSON serialization of a ``--dry-run apply`` drift
report — the unit of approval an agent-driven apply pipeline reviews and later
executes. This slice serializes only; ``--from-plan`` execution is slice 2
(#747). A plan carries four parts:

* a **header** — plan-format version, target Web API base URL + ``organizationid``
  (WhoAmI), solution ``unique_name``, CLI version, timestamp, and the **plan
  intent** (``prune`` / ``allow_data_loss`` / ``stage_only`` as passed at plan
  time),
* the resolved **spec** embedded verbatim (not a path reference — content that
  could change after approval would reopen the hole the plan closes),
* **payload pins** — a ``sha256`` per referenced file payload (web-resource
  bodies, plug-in assemblies), pinning content without inlining it, and
* **verdict records** — one per component: its kind, name, verdict, and the
  engine-computed field-level diff where one exists.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, cast

from crm import __version__
from crm.utils.d365_backend import D365Backend, D365Error

# The plan on-disk contract version. Bumped only on an incompatible shape change;
# slice-2 ``--from-plan`` refuses an unknown/newer version rather than misreading it.
PLAN_FORMAT_VERSION = 1

# Drift-report buckets serialized as verdict records, each bucket name doubling as
# the recorded verdict. ``applied`` is excluded — a dry run never applies — and its
# omission is why a plan only ever carries the read-only verdicts. Order is stable
# for a deterministic, reviewable artifact.
_VERDICT_BUCKETS = ("planned", "updated", "skipped", "replace_blocked", "pruned", "failed")

# Per-entry detail the engine already computes, preserved verbatim on a verdict
# record when present: the field-level ``diff`` (the changed-field set on
# ``updated``), the ``reason`` a component was replace-blocked / skipped /
# prune-refused, the prune ``deleted`` / ``would_prune`` flags, and a ``failed``
# entry's ``error``. Nothing here is recomputed.
_DETAIL_KEYS = ("diff", "reason", "deleted", "would_prune", "error")


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a spec sub-collection to a list of dicts (empty when absent)."""
    return cast("list[dict[str, Any]]", value) if isinstance(value, list) else []


def _sha256_file(base_dir: str | None, file: str) -> str:
    """Hex sha256 of a referenced payload, resolved against the spec's directory.

    A relative ``file`` is joined to ``base_dir`` (the spec file's directory), the
    same resolution apply uses to read the payload. OSError maps to a D365Error so
    a missing/unreadable payload is a clean reported failure, not a crash.
    """
    path = os.path.join(base_dir or "", file)
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError as exc:
        raise D365Error(f"could not read referenced payload {file!r}: {exc}") from exc


def _payload_pins(spec: dict[str, Any], base_dir: str | None) -> dict[str, str]:
    """``{file: sha256}`` for every referenced file payload in the spec.

    Web resources may inline their body as base64 ``content`` — already embedded in
    the spec, so no pin is needed; only a ``file`` reference is pinned. Plug-in
    assemblies always reference a DLL ``file``. A path shared by several components
    is read and hashed once, keyed by the spec-relative string apply reads it by.
    """
    files: list[str] = []
    for block in (*_as_list(spec.get("webresources")), *_as_list(spec.get("plugins"))):
        file = block.get("file")
        if isinstance(file, str) and file not in files:
            files.append(file)
    return {file: _sha256_file(base_dir, file) for file in files}


def _verdict_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    """One ``{kind, name, verdict, ...}`` record per component in the drift report."""
    records: list[dict[str, Any]] = []
    for bucket in _VERDICT_BUCKETS:
        for entry in _as_list(report.get(bucket)):
            record: dict[str, Any] = {
                "kind": entry.get("kind"),
                "name": entry.get("name"),
                "verdict": bucket,
            }
            for key in _DETAIL_KEYS:
                if key in entry:
                    record[key] = entry[key]
            records.append(record)
    return records


def build_plan(
    *,
    spec: dict[str, Any],
    report: dict[str, Any],
    backend: D365Backend,
    organization_id: str | None,
    solution: str,
    base_dir: str | None,
    prune: bool,
    allow_data_loss: bool,
    stage_only: bool,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Serialize a ``--dry-run apply`` drift report into a self-contained plan dict.

    ``report`` is ``apply_spec``'s return value under ``backend.dry_run``; ``spec``
    is the resolved desired-state document, embedded verbatim. ``organization_id``
    is the WhoAmI ``OrganizationId`` of the planning target. ``created_at`` defaults
    to the current UTC time in ISO-8601 (injectable so a caller/test can pin it).
    """
    return {
        "plan_format": PLAN_FORMAT_VERSION,
        "header": {
            "url": backend.profile.api_base,
            "organization_id": organization_id,
            "solution": solution,
            "cli_version": __version__,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "intent": {
                "prune": prune,
                "allow_data_loss": allow_data_loss,
                "stage_only": stage_only,
            },
        },
        "spec": spec,
        "payloads": _payload_pins(spec, base_dir),
        "verdicts": _verdict_records(report),
    }


def write_plan(path: str, plan: dict[str, Any]) -> None:
    """Write a plan dict to ``path`` as indented JSON (trailing newline).

    OSError maps to a D365Error so an unwritable path is a clean reported failure.
    """
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(plan, fh, indent=2, default=str)
            fh.write("\n")
    except OSError as exc:
        raise D365Error(f"could not write plan to {path!r}: {exc}") from exc
