"""Offline smoke tests for the set runner (issue #571).

These exercise discovery, target gating, pass-rate maths, and the set-level
aggregation **without** provisioning isolation or touching a live org: ``run_set``
takes an injectable single-task ``run_one``, so the aggregation is driven by a stub.
The real per-task dry path is covered by ``test_runner_smoke.py``.

Run on demand (not collected by the default suite):

    pytest evals/skill
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evals.skill import set_runner
from evals.skill.runner import RunError, RunResult
from evals.skill.set_runner import (
    DRY,
    ERROR,
    FAIL,
    PASS,
    SKIP,
    SetResult,
    TaskOutcome,
    discover_tasks,
    pass_rate,
    run_set,
    should_skip,
)
from evals.skill.taskspec import parse_task_file

TASKS_DIR = Path(__file__).parent / "tasks"

# Every reference domain the set is expected to sample with at least one task
# (issue #571 AC1). Domains intentionally not sampled here are documented in
# evals/skill/README.md (setup → local; troubleshooting → diagnostic/#572; etc.).
EXPECTED_DOMAINS = {
    "records", "metadata", "customizations", "solutions", "automation",
    "security", "dup", "connectionrole", "fieldsec", "feedback", "authoring",
}


def _specs():
    return [parse_task_file(p) for p in discover_tasks(TASKS_DIR)]


def test_set_task_spec_count():
    # AC1 allows ~12–15 specs; pin to the actual 15 so an accidental task removal
    # fails CI instead of silently passing the lower end of the band. Diagnostic
    # tasks (#572) are scored by the --analyze pass, not this deterministic set, so
    # they don't count toward it.
    predicate = [s for s in _specs() if not s.is_diagnostic]
    assert len(predicate) == 15, f"expected 15 predicate task specs, found {len(predicate)}"


def test_eight_trials_formalized():
    # AC3: all 8 trials present as specs (none dropped). TRIAL-3 and TRIAL-7 are
    # host-agnostic, so they were formalized as cloud specs under behavioural names;
    # the other six keep the trial- prefix and on-prem gating.
    ids = {s.id for s in _specs()}
    trials = {
        "trial-customization-workflow",  # TRIAL-1
        "trial-global-optionset",        # TRIAL-2
        "customizations-view-edit",      # TRIAL-3 (cloud)
        "trial-webresource-iterate",     # TRIAL-4
        "trial-process-state",           # TRIAL-5
        "trial-bulk-load",               # TRIAL-6
        "records-validate-write",        # TRIAL-7 (cloud)
        "trial-import-diagnosis",        # TRIAL-8
    }
    missing = trials - ids
    assert not missing, f"trials not formalized: {missing}"


def test_every_expected_domain_covered():
    # AC1: at least one task per (sampled) reference domain.
    domains = {s.domain for s in _specs()}
    missing = EXPECTED_DOMAINS - domains
    assert not missing, f"reference domains with no task: {missing}"


def test_should_skip_truth_table():
    assert should_skip("cloud", "onprem") is True
    assert should_skip("onprem", "cloud") is True
    assert should_skip("cloud", "cloud") is False
    assert should_skip("onprem", "onprem") is False
    assert should_skip("either", "cloud") is False
    assert should_skip("either", "onprem") is False


def test_pass_rate_excludes_unscored():
    outcomes = [
        TaskOutcome("a", PASS, "cloud"),
        TaskOutcome("b", PASS, "cloud"),
        TaskOutcome("c", FAIL, "cloud"),
        TaskOutcome("d", SKIP, "onprem"),
        TaskOutcome("e", ERROR, "cloud"),
    ]
    # 2 pass / 3 scored (skip + error excluded)
    assert pass_rate(outcomes) == pytest.approx(2 / 3)


def test_pass_rate_none_when_nothing_scored():
    assert pass_rate([TaskOutcome("a", SKIP, "onprem"), TaskOutcome("b", DRY, "cloud")]) is None


def _stub(verdicts: dict[str, bool], *, raises: set[str] | None = None):
    """Build a run_one stub: maps task id → passed, or raises for ids in ``raises``."""
    raises = raises or set()

    def run_one(path, *, dry_run, agent_cmd, crm_bin):
        spec = parse_task_file(path)
        if spec.id in raises:
            raise RunError(f"boom: {spec.id}")
        if dry_run:
            return RunResult(task_id=spec.id, dry_run=True, isolation_checks={"skill-installed": "x"})
        return RunResult(
            task_id=spec.id, dry_run=False, isolation_checks={"skill-installed": "x"},
            passed=verdicts.get(spec.id, False), reason="stubbed",
        )

    return run_one


def test_dry_run_marks_every_task_dry_regardless_of_gate():
    result = run_set(TASKS_DIR, dry_run=True, run_one=_stub({}))
    assert result.dry_run is True
    assert result.active_target is None  # not resolved on a dry run
    assert all(o.status == DRY for o in result.outcomes)
    assert len(result.outcomes) == len(discover_tasks(TASKS_DIR))
    assert result.pass_rate() is None  # nothing scored


def test_live_run_skips_off_target_and_scores_the_rest():
    # Inject active_target=cloud so no live profile is needed; cloud + either run,
    # onprem tasks skip, diagnostic tasks skip. Mark one as failing for both verdicts.
    specs = _specs()
    scored_ids = [s.id for s in specs if s.target in ("cloud", "either") and not s.is_diagnostic]
    verdicts = {tid: True for tid in scored_ids}
    a_failing = scored_ids[0]
    verdicts[a_failing] = False

    result = run_set(TASKS_DIR, active_target="cloud", run_one=_stub(verdicts))

    by_id = {o.task_id: o for o in result.outcomes}
    for s in specs:
        if s.is_diagnostic or s.target == "onprem":
            assert by_id[s.id].status == SKIP
        else:
            assert by_id[s.id].status in (PASS, FAIL)
    assert by_id[a_failing].status == FAIL
    # pass-rate is over scored (cloud/either, non-diagnostic) tasks only.
    scored = len(scored_ids)
    assert result.pass_rate() == pytest.approx((scored - 1) / scored)


def test_live_run_skips_diagnostic_tasks():
    # A diagnostic task (no predicate, #572) is skipped by the set — reported, never
    # errored on the runner's "diagnostic needs --analyze" guard.
    specs = _specs()
    diagnostic = [s for s in specs if s.is_diagnostic]
    assert diagnostic, "expected at least one diagnostic task in the set"
    result = run_set(TASKS_DIR, active_target="cloud", run_one=_stub({s.id: True for s in specs}))
    by_id = {o.task_id: o for o in result.outcomes}
    for s in diagnostic:
        assert by_id[s.id].status == SKIP
        assert "diagnostic" in by_id[s.id].reason


def test_harness_error_is_isolated_not_fatal():
    specs = _specs()
    cloud_ids = [s.id for s in specs if s.target in ("cloud", "either")]
    boom = cloud_ids[0]
    result = run_set(
        TASKS_DIR, active_target="cloud",
        run_one=_stub({tid: True for tid in cloud_ids}, raises={boom}),
    )
    by_id = {o.task_id: o for o in result.outcomes}
    assert by_id[boom].status == ERROR
    assert "boom" in by_id[boom].reason
    # the other cloud tasks still ran
    assert any(o.status == PASS for o in result.outcomes)


def test_set_result_shapes():
    result = SetResult(
        outcomes=[TaskOutcome("a", PASS, "cloud"), TaskOutcome("b", SKIP, "onprem")],
        active_target="cloud", dry_run=False,
    )
    d = result.to_dict()
    assert d["counts"][PASS] == 1 and d["counts"][SKIP] == 1
    assert d["pass_rate"] == pytest.approx(1.0)
    assert any("pass-rate" in line for line in result.summary_lines())


def test_run_set_raises_on_empty_dir(tmp_path):
    with pytest.raises(RunError, match="no task specs"):
        run_set(tmp_path, dry_run=True, run_one=_stub({}))
