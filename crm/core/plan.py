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
from datetime import UTC, datetime
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


def _sha256_file(base_dir: str | None, file: str) -> str | None:
    """Hex sha256 of a referenced payload, or ``None`` when it cannot be read.

    A relative ``file`` is joined to ``base_dir`` (the spec file's directory), the
    same resolution apply uses to read the payload. An unreadable payload pins as
    ``None`` rather than raising: the plan is written on *every* dry-run (ADR 0022),
    and apply already routes the identical read failure to the drift report's
    ``failed`` bucket — which this plan serializes. A ``None`` pin records
    "unpinnable at plan time" without aborting the write; slice-2 ``--from-plan``
    refuses any plan carrying a ``failed`` entry, so a ``None`` pin never reaches a
    present-and-matching check.
    """
    path = os.path.join(base_dir or "", file)
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            # Stream in chunks so a large plug-in DLL / web-resource body is not
            # read wholesale into memory just to hash it.
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _payload_pins(spec: dict[str, Any], base_dir: str | None) -> dict[str, str | None]:
    """``{file: sha256}`` for every referenced file payload in the spec.

    Web resources may inline their body as base64 ``content`` — already embedded in
    the spec, so no pin is needed; only a ``file`` reference is pinned. Plug-in
    assemblies always reference a DLL ``file``. A path shared by several components
    is read and hashed once, keyed by the spec-relative string apply reads it by. An
    unreadable payload maps to ``None`` (see ``_sha256_file``).
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
            "created_at": created_at or datetime.now(UTC).isoformat(),
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


# ── slice 2 (#747): approval-gated execution (`apply --from-plan`) ─────────────
#
# A plan is executed **only if it is still exactly true**. `run_plan` recomputes
# the drift report from live reads on a dry-run twin, compares it to the plan at
# the *action* level (component set + verdict + changed-field set), and — unless
# in verify mode or the plan is stale — executes it for real via the same apply
# engine. `preflight_plan` runs the refusals that must precede any read of the
# spec; `diff_plan` is the whole-run divergence gate. See ADR 0022.


def load_plan(path: str) -> dict[str, Any]:
    """Read and parse a plan JSON file into a dict.

    A read error, malformed JSON, or a non-object top level maps to a D365Error
    so the command reports a clean failure. ``utf-8-sig`` tolerates a leading BOM,
    matching crm's file-boundary read policy (#683).
    """
    try:
        with open(path, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise D365Error(f"could not read plan {path!r}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise D365Error(f"plan {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise D365Error(f"plan {path!r} must be a JSON object.")
    plan = cast("dict[str, Any]", doc)
    _validate_plan_shape(plan, path)
    return plan


def _validate_plan_shape(plan: dict[str, Any], path: str) -> None:
    """Reject a JSON-valid but structurally-wrong plan as a clean D365Error.

    ``load_plan`` only guarantees a JSON object; a hand-edited or corrupt plan
    could still carry the wrong *types* for the keys preflight / run_plan
    dereference (``.get``/``.items``/``os.path.join``). Validating the shape here,
    at the parse boundary, keeps those a reported failure rather than an
    AttributeError/TypeError escaping the error envelope downstream.
    """
    label = f"plan {path!r}"
    header = plan.get("header", {})
    if not isinstance(header, dict):
        raise D365Error(f"{label}: 'header' must be an object.")
    if not isinstance(cast("dict[str, Any]", header).get("intent", {}), dict):
        raise D365Error(f"{label}: 'header.intent' must be an object.")
    verdicts = plan.get("verdicts", [])
    if not isinstance(verdicts, list):
        raise D365Error(f"{label}: 'verdicts' must be a list.")
    if not all(isinstance(v, dict) for v in cast("list[Any]", verdicts)):
        raise D365Error(f"{label}: each verdict must be an object.")
    payloads = plan.get("payloads", {})
    if not isinstance(payloads, dict):
        raise D365Error(f"{label}: 'payloads' must be an object.")
    for key, val in cast("dict[Any, Any]", payloads).items():
        if not isinstance(key, str) or not (val is None or isinstance(val, str)):
            raise D365Error(f"{label}: 'payloads' must map file paths to sha256 strings (or null).")
    if not isinstance(plan.get("spec", {}), dict):
        raise D365Error(f"{label}: 'spec' must be an object.")


def plan_intent(plan: dict[str, Any]) -> dict[str, bool]:
    """The plan's fixed intent — ``{prune, allow_data_loss, stage_only}``.

    Replayed at execution, never re-specified (ADR 0022). Missing/absent flags
    default to ``False`` so a hand-trimmed plan header degrades safely.
    """
    header = cast("dict[str, Any]", plan.get("header") or {})
    intent = cast("dict[str, Any]", header.get("intent") or {})
    return {
        "prune": bool(intent.get("prune")),
        "allow_data_loss": bool(intent.get("allow_data_loss")),
        "stage_only": bool(intent.get("stage_only")),
    }


def _payload_mismatches(payloads: dict[str, Any], base_dir: str | None) -> list[str]:
    """Files whose on-disk sha256 no longer matches the plan's pin.

    Each referenced payload must be present and byte-identical to what was pinned
    at plan time (ADR 0022). A pin recorded as ``None`` (unpinnable at plan time)
    can never satisfy present-and-matching, so it is reported too — though a clean
    plan never carries one (an unreadable payload routes its component to the
    ``failed`` bucket, which the clean-plan rule already refuses).
    """
    out: list[str] = []
    for file, pinned in payloads.items():
        if not isinstance(pinned, str):
            out.append(f"{file} (could not be pinned at plan time)")
            continue
        actual = _sha256_file(base_dir, file)
        if actual is None:
            out.append(f"{file} (missing)")
        elif actual != pinned:
            out.append(f"{file} (content changed)")
    return out


def preflight_plan(
    plan: dict[str, Any],
    backend: D365Backend,
    *,
    organization_id: str | None,
    base_dir: str | None,
) -> list[str]:
    """Refuse an un-executable plan before any read of its spec; return warnings.

    Refusals (raise D365Error, no write): an unknown/newer ``plan_format``; an
    ``organization_id`` that does not match the live target (WhoAmI); a plan that
    carries ``replace_blocked`` / ``failed`` components (the clean-plan rule — such
    a plan approves an outcome apply will never converge to); any pinned payload
    that is missing or content-changed. A mismatched target URL or CLI version is
    a **warning**, not a refusal — aliased hostnames are legitimate, and pinning
    the CLI version would let every release invalidate every pending plan.
    """
    warnings: list[str] = []
    fmt = plan.get("plan_format")
    if fmt != PLAN_FORMAT_VERSION:
        raise D365Error(
            f"plan format {fmt!r} is not supported (this CLI writes and reads "
            f"format {PLAN_FORMAT_VERSION}); re-create the plan."
        )
    header = cast("dict[str, Any]", plan.get("header") or {})
    plan_org = header.get("organization_id")
    if plan_org and organization_id and plan_org != organization_id:
        raise D365Error(
            f"plan targets organization {plan_org!r} but the active connection is "
            f"organization {organization_id!r}; refusing to apply it here."
        )
    plan_url = header.get("url")
    if plan_url and plan_url != backend.profile.api_base:
        warnings.append(
            f"plan was built against {plan_url!r}; the active connection is "
            f"{backend.profile.api_base!r} — proceeding (hostnames may be aliased)."
        )
    plan_cli = header.get("cli_version")
    if plan_cli and plan_cli != __version__:
        warnings.append(
            f"plan was built with CLI version {plan_cli}; this is {__version__} — proceeding."
        )
    blocking = [
        v
        for v in _as_list(plan.get("verdicts"))
        if v.get("verdict") in ("replace_blocked", "failed")
    ]
    if blocking:
        names = "; ".join(f"{v.get('kind')} {v.get('name')} ({v.get('verdict')})" for v in blocking)
        raise D365Error(
            "plan is not executable — it records replace_blocked/failed components "
            f"({names}); fix the spec and re-plan."
        )
    mismatches = _payload_mismatches(cast("dict[str, Any]", plan.get("payloads") or {}), base_dir)
    if mismatches:
        raise D365Error(
            "plan payload(s) no longer match what was pinned at plan time: "
            + "; ".join(mismatches)
            + "; re-create the plan."
        )
    return warnings


def _changed_fields(record: dict[str, Any]) -> frozenset[str]:
    """The set of field names an ``updated`` verdict record would change.

    The engine records drift in one of two shapes: a ``{field: {old, new}}``
    mapping (metadata/view updates) or a ``{"fields": [...]}`` list (web
    resource / plug-in / security-role updates). Either way the *set of field
    names* is the action-level identity — the live ``old`` values are ignored, so
    a shifted live value does not read as a divergence (ADR 0022).
    """
    diff = record.get("diff")
    if not isinstance(diff, dict):
        return frozenset()
    diff = cast("dict[str, Any]", diff)
    fields = diff.get("fields")
    if isinstance(fields, list):
        return frozenset(str(f) for f in cast("list[Any]", fields))
    return frozenset(diff.keys())


def _action_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """The action-level comparison key for one verdict record.

    ``(kind, name, verdict)`` for most verdicts; ``updated`` also carries its
    changed-field set, and ``pruned`` whether it would actually delete
    (``would_prune``) versus was refused. Live field values are deliberately
    excluded — only the *action* is compared.
    """
    verdict = record.get("verdict")
    base: tuple[Any, ...] = (record.get("kind"), record.get("name"), verdict)
    if verdict == "updated":
        return base + (tuple(sorted(_changed_fields(record))),)
    if verdict == "pruned":
        return base + (bool(record.get("would_prune")),)
    return base


def _describe(record: dict[str, Any] | None) -> str:
    """A short human-readable rendering of a verdict record for a stale report."""
    if record is None:
        return "absent"
    verdict = record.get("verdict")
    if verdict == "updated":
        fields = sorted(_changed_fields(record))
        return f"updated ({', '.join(fields)})" if fields else "updated"
    if verdict == "pruned":
        return "pruned (would delete)" if record.get("would_prune") else "pruned (refused)"
    return str(verdict)


def diff_plan(
    plan: dict[str, Any],
    live_report: dict[str, Any],
    *,
    prune_intent: bool | None = None,
) -> list[dict[str, Any]]:
    """The whole-run divergence gate: how a recomputed report departs from the plan.

    Compares the plan's verdict records to those a live dry-run reconcile now
    computes (``live_report`` is an ``apply_spec`` return under dry-run), keyed by
    ``(kind, name)``, at the action level (``_action_key``). Returns one entry per
    diverged component — ``{kind, name, plan, live}`` in drift-report shape — or
    ``[]`` when the plan is still exactly true.

    ``pruned`` records participate only under prune intent (``prune_intent``,
    defaulting to the plan's own intent); without it they are informational, so a
    stray new solution component surfacing live never invalidates the plan.
    """
    if prune_intent is None:
        prune_intent = plan_intent(plan)["prune"]

    def participates(record: dict[str, Any]) -> bool:
        return record.get("verdict") != "pruned" or bool(prune_intent)

    def by_id(records: list[dict[str, Any]]) -> dict[tuple[Any, Any], dict[str, Any]]:
        return {(r.get("kind"), r.get("name")): r for r in records if participates(r)}

    plan_map = by_id(_as_list(plan.get("verdicts")))
    live_map = by_id(_verdict_records(live_report))
    out: list[dict[str, Any]] = []
    for key in sorted(set(plan_map) | set(live_map), key=lambda k: (str(k[0]), str(k[1]))):
        p, live = plan_map.get(key), live_map.get(key)
        p_key = _action_key(p) if p is not None else None
        live_key = _action_key(live) if live is not None else None
        if p_key != live_key:
            out.append(
                {"kind": key[0], "name": key[1], "plan": _describe(p), "live": _describe(live)}
            )
    return out


def run_plan(
    backend: D365Backend,
    plan: dict[str, Any],
    *,
    base_dir: str | None,
    verify_only: bool,
    include_referenced_optionsets: bool = True,
) -> dict[str, Any]:
    """Verify a plan against the live org and, unless stale or in verify mode, run it.

    Recomputes the drift report read-only on a dry-run twin of ``backend`` using
    the plan's fixed intent, and compares it to the plan (``diff_plan``). Any
    divergence is a **stale plan** — zero writes, ``{status: "stale"}``. When the
    plan still holds: in verify mode (``verify_only``) it reports ``"valid"`` and
    writes nothing (the CI pre-check); otherwise it executes the embedded spec for
    real on ``backend`` and returns ``{status: "executed", result}``.

    A residual TOCTOU window survives between this verify pass and the writes —
    metadata writes are not transactional — so a concurrent customization could
    still slip in; ADR 0022 documents this honestly rather than engineering around
    it. The gate shrinks the window from preview-to-apply to verify-to-write.
    """
    from crm.core.apply import apply_spec  # lazy: avoids a core import cycle

    intent = plan_intent(plan)
    spec = cast("dict[str, Any]", plan.get("spec") or {})
    apply_kwargs: dict[str, Any] = {
        "stage_only": intent["stage_only"],
        "include_referenced_optionsets": include_referenced_optionsets,
        "base_dir": base_dir,
        "prune": intent["prune"],
        "allow_data_loss": intent["allow_data_loss"],
    }
    # Verify pass: recompute the drift report with writes suppressed.
    report = apply_spec(backend.as_dry_run(), spec, **apply_kwargs)
    divergences = diff_plan(plan, report, prune_intent=intent["prune"])
    if divergences:
        return {"status": "stale", "ok": False, "divergences": divergences}
    if verify_only:
        return {"status": "valid", "ok": True}
    # The plan is still exactly true → execute the approved actions for real.
    result = apply_spec(backend, spec, **apply_kwargs)
    return {"status": "executed", "ok": result["ok"], "result": result}
