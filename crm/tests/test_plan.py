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

import pytest

from crm import __version__
from crm.core import plan as plan_mod
from crm.utils.d365_backend import D365Error

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


# ── slice 2 (#747): load / pre-flight / divergence gate for `--from-plan` ──────
#
# These exercise the pure seams of plan execution — no network. `preflight_plan`
# reads only `backend.profile.api_base`; `diff_plan` compares two in-memory
# dicts; `load_plan` is file IO. The recompute+execute orchestration (`run_plan`,
# which drives `apply_spec`) is covered against the wire in test_apply.py.


def _header(**over):
    """A plan header matching the `backend` fixture's identity by default."""
    base = {
        "url": "https://crm.contoso.local/contoso/api/data/v9.2/",
        "organization_id": "org-guid-123",
        "solution": "ContosoCore",
        "cli_version": __version__,
        "created_at": "2026-07-09T00:00:00+00:00",
        "intent": {"prune": False, "allow_data_loss": False, "stage_only": False},
    }
    base.update(over)
    return base


def _plan(*, verdicts=None, payloads=None, header=None, plan_format=None):
    return {
        "plan_format": plan_mod.PLAN_FORMAT_VERSION if plan_format is None else plan_format,
        "header": _header() if header is None else header,
        "spec": _SPEC,
        "payloads": payloads or {},
        "verdicts": verdicts or [],
    }


# ── load_plan ────────────────────────────────────────────────────────────────


def test_load_plan_reads_json(tmp_path):
    p = tmp_path / "p.json"
    plan = _plan(verdicts=[{"kind": "entity", "name": "x", "verdict": "planned"}])
    plan_mod.write_plan(str(p), plan)
    assert plan_mod.load_plan(str(p)) == plan


def test_load_plan_rejects_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(D365Error, match="not valid JSON"):
        plan_mod.load_plan(str(p))


def test_load_plan_rejects_non_object(tmp_path):
    p = tmp_path / "arr.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(D365Error, match="must be a JSON object"):
        plan_mod.load_plan(str(p))


# ── preflight_plan: refusals + warnings ────────────────────────────────────────


def test_preflight_clean_plan_returns_no_warnings(backend):
    plan = _plan(verdicts=[{"kind": "entity", "name": "x", "verdict": "planned"}])
    assert plan_mod.preflight_plan(
        plan, backend, organization_id="org-guid-123", base_dir=None) == []


def test_preflight_refuses_unknown_plan_format(backend):
    plan = _plan(plan_format=plan_mod.PLAN_FORMAT_VERSION + 1)
    with pytest.raises(D365Error, match="plan format"):
        plan_mod.preflight_plan(plan, backend, organization_id="org-guid-123", base_dir=None)


def test_preflight_refuses_org_mismatch(backend):
    plan = _plan()
    with pytest.raises(D365Error, match="organization"):
        plan_mod.preflight_plan(
            plan, backend, organization_id="a-different-org", base_dir=None)


def test_preflight_url_mismatch_is_a_warning_not_a_refusal(backend):
    plan = _plan(header=_header(url="https://other.crm.dynamics.com/api/data/v9.2/"))
    warnings = plan_mod.preflight_plan(
        plan, backend, organization_id="org-guid-123", base_dir=None)
    assert any("other.crm.dynamics.com" in w for w in warnings)


def test_preflight_cli_version_mismatch_is_a_warning_not_a_refusal(backend):
    plan = _plan(header=_header(cli_version="0.0.1-ancient"))
    warnings = plan_mod.preflight_plan(
        plan, backend, organization_id="org-guid-123", base_dir=None)
    assert any("0.0.1-ancient" in w for w in warnings)


def test_preflight_refuses_replace_blocked_plan(backend):
    plan = _plan(verdicts=[{"kind": "entity", "name": "x", "verdict": "replace_blocked",
                            "reason": "ownership"}])
    with pytest.raises(D365Error, match="not executable"):
        plan_mod.preflight_plan(plan, backend, organization_id="org-guid-123", base_dir=None)


def test_preflight_refuses_failed_plan(backend):
    plan = _plan(verdicts=[{"kind": "webresource", "name": "x", "verdict": "failed",
                            "error": "cannot read file"}])
    with pytest.raises(D365Error, match="not executable"):
        plan_mod.preflight_plan(plan, backend, organization_id="org-guid-123", base_dir=None)


def test_preflight_refuses_missing_payload(backend, tmp_path):
    plan = _plan(payloads={"scripts/x.js": _SHA256_ABC})
    with pytest.raises(D365Error, match="missing"):
        plan_mod.preflight_plan(
            plan, backend, organization_id="org-guid-123", base_dir=str(tmp_path))


def test_preflight_refuses_changed_payload(backend, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.js").write_bytes(b"different-content")
    plan = _plan(payloads={"scripts/x.js": _SHA256_ABC})
    with pytest.raises(D365Error, match="changed"):
        plan_mod.preflight_plan(
            plan, backend, organization_id="org-guid-123", base_dir=str(tmp_path))


def test_preflight_accepts_matching_payload(backend, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "x.js").write_bytes(b"abc")  # sha256 == _SHA256_ABC
    plan = _plan(payloads={"scripts/x.js": _SHA256_ABC})
    assert plan_mod.preflight_plan(
        plan, backend, organization_id="org-guid-123", base_dir=str(tmp_path)) == []


# ── diff_plan: the whole-run divergence gate ───────────────────────────────────


def test_diff_plan_identical_report_is_not_stale(backend):
    report = _report(planned=[{"kind": "entity", "name": "contoso_Project"}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    assert plan_mod.diff_plan(plan, report) == []


def test_diff_plan_flags_added_component(backend):
    plan_report = _report(planned=[{"kind": "entity", "name": "contoso_Project"}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    # Live now also computes a second planned component the plan never approved.
    live = _report(planned=[{"kind": "entity", "name": "contoso_Project"},
                            {"kind": "attribute", "name": "contoso_Code"}])
    div = plan_mod.diff_plan(plan, live)
    assert [(d["kind"], d["name"]) for d in div] == [("attribute", "contoso_Code")]
    assert div[0]["plan"] == "absent"
    assert div[0]["live"] == "planned"


def test_diff_plan_flags_removed_component(backend):
    plan_report = _report(planned=[{"kind": "entity", "name": "contoso_Project"},
                                   {"kind": "attribute", "name": "contoso_Code"}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    live = _report(planned=[{"kind": "entity", "name": "contoso_Project"}])
    div = plan_mod.diff_plan(plan, live)
    assert [(d["kind"], d["name"]) for d in div] == [("attribute", "contoso_Code")]
    assert div[0]["plan"] == "planned"
    assert div[0]["live"] == "absent"


def test_diff_plan_flags_verdict_change(backend):
    plan_report = _report(planned=[{"kind": "entity", "name": "contoso_Project"}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    # The entity now exists and matches → live computes `skipped`, not `planned`.
    live = _report(skipped=[{"kind": "entity", "name": "contoso_Project"}])
    div = plan_mod.diff_plan(plan, live)
    assert len(div) == 1
    assert div[0]["plan"] == "planned"
    assert div[0]["live"] == "skipped"


def test_diff_plan_flags_changed_field_set_on_update(backend):
    plan_report = _report(updated=[{"kind": "entity", "name": "contoso_Project",
                                    "diff": {"display_name": {"old": "P", "new": "Project"}}}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    # Same verdict, but live now also drifts `description` → different field set.
    live = _report(updated=[{"kind": "entity", "name": "contoso_Project",
                             "diff": {"display_name": {"old": "P", "new": "Project"},
                                      "description": {"old": "", "new": "d"}}}])
    div = plan_mod.diff_plan(plan, live)
    assert len(div) == 1
    assert "display_name" in div[0]["plan"] and "description" not in div[0]["plan"]
    assert "description" in div[0]["live"]


def test_diff_plan_update_same_field_set_different_live_value_is_not_stale(backend):
    # Action level: field VALUES need no byte equality — only the changed-field
    # SET must match. A live `old` value that shifted (someone edited it and back)
    # does not invalidate the plan when the same field would still be updated.
    plan_report = _report(updated=[{"kind": "entity", "name": "contoso_Project",
                                    "diff": {"display_name": {"old": "P", "new": "Project"}}}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    live = _report(updated=[{"kind": "entity", "name": "contoso_Project",
                             "diff": {"display_name": {"old": "SHIFTED", "new": "Project"}}}])
    assert plan_mod.diff_plan(plan, live) == []


def test_diff_plan_pruned_ignored_without_prune_intent(backend):
    # Without prune intent, `pruned` entries are informational — a stray new
    # solution component surfacing live does not invalidate the plan.
    plan_report = _report()
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=False, allow_data_loss=False, stage_only=False)
    live = _report(pruned=[{"kind": "view", "name": "Stray", "deleted": False}])
    assert plan_mod.diff_plan(plan, live) == []


def test_diff_plan_pruned_participates_under_prune_intent(backend):
    plan_report = _report(pruned=[{"kind": "view", "name": "Stale", "deleted": False,
                                   "would_prune": True}])
    plan = plan_mod.build_plan(
        spec=_SPEC, report=plan_report, backend=backend, organization_id="org-guid-123",
        solution="ContosoCore", base_dir=None,
        prune=True, allow_data_loss=False, stage_only=False)
    # Under prune intent, a pruned candidate the plan approved that live no longer
    # sees (deleted out of band) is a divergence.
    live = _report()
    div = plan_mod.diff_plan(plan, live)
    assert [(d["kind"], d["name"]) for d in div] == [("view", "Stale")]
