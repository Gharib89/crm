"""Tests for `crm.core.plan` — plan-artifact serialization (ADR 0022 slice 1, #746).

`build_plan` serializes a `--dry-run apply` drift report into a self-contained
plan dict: a header (identity + intent), the resolved spec verbatim, sha256
payload pins, and a verdict record per component. `write_plan` renders it to disk.
These exercise the pure serialization seam — no network; the drift report is
passed in as the dict `apply_spec` returns.
"""
# pyright: basic
from __future__ import annotations

import json

from crm import __version__
from crm.core import plan as plan_mod

# NIST SHA-256 test vector: sha256(b"abc"). An independent source of truth for the
# payload-pin assertion, not a value recomputed the way the code computes it.
_SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

_SPEC = {
    "solution": {"unique_name": "ContosoCore"},
    "entities": [{"schema_name": "contoso_Project", "display_name": "Project"}],
}


def _report(**buckets):
    """A drift report with all buckets present (empty unless overridden)."""
    base = {"ok": True, "applied": [], "updated": [], "skipped": [],
            "replace_blocked": [], "pruned": [], "planned": [], "failed": [],
            "staged": False}
    base.update(buckets)
    return base


def test_build_plan_header_captures_identity_and_intent(backend):
    plan = plan_mod.build_plan(
        spec=_SPEC, report=_report(), backend=backend,
        organization_id="org-guid-123", solution="ContosoCore", base_dir=None,
        prune=True, allow_data_loss=False, stage_only=True,
        created_at="2026-07-09T00:00:00+00:00")
    assert plan["plan_format"] == plan_mod.PLAN_FORMAT_VERSION
    header = plan["header"]
    assert header["url"] == backend.profile.api_base
    assert header["organization_id"] == "org-guid-123"
    assert header["solution"] == "ContosoCore"
    assert header["cli_version"] == __version__
    assert header["created_at"] == "2026-07-09T00:00:00+00:00"
    # Intent is captured exactly as passed at plan time (all three flags).
    assert header["intent"] == {"prune": True, "allow_data_loss": False,
                                "stage_only": True}


def test_build_plan_created_at_defaults_to_a_timestamp(backend):
    plan = plan_mod.build_plan(
        spec=_SPEC, report=_report(), backend=backend, organization_id=None,
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    # Defaulted (not injected) — present and ISO-8601-ish (date + T + time).
    created = plan["header"]["created_at"]
    assert isinstance(created, str) and created[:4].isdigit() and "T" in created


def test_build_plan_embeds_spec_verbatim(backend):
    plan = plan_mod.build_plan(
        spec=_SPEC, report=_report(), backend=backend, organization_id=None,
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    assert plan["spec"] == _SPEC


def test_build_plan_verdict_records_map_buckets_and_carry_diff(backend):
    report = _report(
        planned=[{"kind": "entity", "name": "contoso_Project"}],
        updated=[{"kind": "attribute", "name": "contoso_Code",
                  "diff": {"display_name": {"old": "Code", "new": "Code #"}}}],
        replace_blocked=[{"kind": "entity", "name": "contoso_Old",
                          "reason": "ownership change requires drop-and-recreate"}],
        pruned=[{"kind": "view", "name": "Stale", "deleted": False,
                 "would_prune": True}],
        skipped=[{"kind": "optionset", "name": "contoso_priority"}])
    verdicts = plan_mod.build_plan(
        spec=_SPEC, report=report, backend=backend, organization_id=None,
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)["verdicts"]
    by_name = {v["name"]: v for v in verdicts}
    assert by_name["contoso_Project"]["verdict"] == "planned"
    # `updated` carries the engine's field-level diff verbatim (the changed-field set).
    assert by_name["contoso_Code"]["verdict"] == "updated"
    assert by_name["contoso_Code"]["diff"] == {
        "display_name": {"old": "Code", "new": "Code #"}}
    # `replace_blocked` keeps its reason; `pruned` its flags.
    assert by_name["contoso_Old"]["verdict"] == "replace_blocked"
    assert "drop-and-recreate" in by_name["contoso_Old"]["reason"]
    assert by_name["Stale"]["verdict"] == "pruned"
    assert by_name["Stale"]["would_prune"] is True
    assert by_name["contoso_priority"]["verdict"] == "skipped"
    # A skipped entry with no engine detail carries only kind/name/verdict.
    assert set(by_name["contoso_priority"]) == {"kind", "name", "verdict"}


def test_build_plan_pins_referenced_payloads_by_sha256(backend, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "project.js").write_bytes(b"abc")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "Plugins.dll").write_bytes(b"abc")
    spec = {
        "solution": {"unique_name": "ContosoCore"},
        "webresources": [
            {"name": "contoso_/scripts/project.js", "file": "scripts/project.js"},
            # Inline-content web resource: already embedded, so no pin.
            {"name": "contoso_/inline.css", "content": "aGk=", "webresourcetype": 2},
        ],
        "plugins": [{"assembly": "Plugins", "file": "bin/Plugins.dll"}],
    }
    plan = plan_mod.build_plan(
        spec=spec, report=_report(), backend=backend, organization_id=None,
        solution="ContosoCore", base_dir=str(tmp_path),
        prune=False, allow_data_loss=False, stage_only=False)
    assert plan["payloads"] == {
        "scripts/project.js": _SHA256_ABC,
        "bin/Plugins.dll": _SHA256_ABC,
    }
    # The inline-content web resource contributes no payload pin.
    assert "contoso_/inline.css" not in plan["payloads"]


def test_build_plan_unreadable_payload_pins_none_and_still_builds(backend):
    # ADR 0022: -o always writes the plan. A referenced payload that can't be read
    # pins as None instead of aborting the build — apply routes the same read
    # failure to the drift report's `failed` bucket, which the plan serializes.
    spec = {
        "solution": {"unique_name": "ContosoCore"},
        "webresources": [{"name": "contoso_/missing.js", "file": "does/not/exist.js"}],
    }
    report = _report(failed=[{"kind": "webresource", "name": "contoso_/missing.js",
                              "error": "cannot read file 'does/not/exist.js'"}])
    plan = plan_mod.build_plan(
        spec=spec, report=report, backend=backend, organization_id=None,
        solution="ContosoCore", base_dir="/nonexistent-base",
        prune=False, allow_data_loss=False, stage_only=False)
    assert plan["payloads"] == {"does/not/exist.js": None}
    # The failed component is serialized, so the plan doubles as the drift report.
    assert [v["verdict"] for v in plan["verdicts"]] == ["failed"]


def test_build_plan_no_payloads_when_no_file_references(backend):
    plan = plan_mod.build_plan(
        spec=_SPEC, report=_report(), backend=backend, organization_id=None,
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    assert plan["payloads"] == {}


def test_write_plan_roundtrips_json(tmp_path):
    plan = {"plan_format": 1, "header": {"solution": "ContosoCore"}, "verdicts": []}
    out = tmp_path / "plan.json"
    plan_mod.write_plan(str(out), plan)
    assert json.loads(out.read_text(encoding="utf-8")) == plan
    # Written with a trailing newline (POSIX text file).
    assert out.read_text(encoding="utf-8").endswith("\n")
