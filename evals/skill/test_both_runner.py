"""Offline tests for the both-targets runner (issue #573).

``run_both`` loops the configured profiles, probes each for reachability, runs the set
against the reachable ones, and unions the coverage. The live pieces — the set run and
the probe — are injectable, so this drives the orchestration (skip-on-unreachable,
union, env handling, baseline rows) deterministically with no agent and no live org.

    pytest evals/skill
"""
from __future__ import annotations

import io
import os

import pytest

from evals.skill import both_runner
from evals.skill.both_runner import TargetRun, append_baseline, run_both
from evals.skill.set_runner import FAIL, PASS, ProgressEvent, SetResult, StderrProgress, TaskOutcome


def _set_result(target: str, *, passes: int, trials: int) -> SetResult:
    """A SetResult with one scored task carrying the given trials/passes fraction."""
    status = PASS if passes == trials else FAIL
    return SetResult(
        outcomes=[TaskOutcome(f"{target}-task", status, target, "stub", trials=trials, passes=passes)],
        active_target=target,
        dry_run=False,
    )


def _fakes(reachable: dict[str, bool], targets: dict[str, str]):
    """Build (run_set_fn, probe_fn, target_fn) stubs keyed off the active profile env."""
    def target_fn() -> str:
        return targets[os.environ["D365_E2E_PROFILE"]]

    def probe_fn(name: str) -> bool:
        return reachable[name]

    def run_set_fn(*, repeat, agent_cmd, active_target, progress=None):
        # one task, all trials pass — proves repeat is threaded through
        return _set_result(active_target, passes=repeat, trials=repeat)

    return run_set_fn, probe_fn, target_fn


def test_both_targets_run_when_reachable():
    run_set_fn, probe_fn, target_fn = _fakes(
        {"agent-cloud": True, "agent-on-prem": True},
        {"agent-cloud": "cloud", "agent-on-prem": "onprem"},
    )
    res = run_both(
        ["agent-cloud", "agent-on-prem"], repeat=2,
        run_set_fn=run_set_fn, probe_fn=probe_fn, target_fn=target_fn,
    )
    assert [e.target for e in res.entries] == ["cloud", "onprem"]
    assert all(e.result is not None and e.skipped_reason is None for e in res.entries)
    # union coverage = the scored tasks from both legs
    assert res.union_scored() == {"cloud-task", "onprem-task"}


def test_unreachable_target_skips_with_message_not_failure():
    run_set_fn, probe_fn, target_fn = _fakes(
        {"agent-cloud": True, "agent-on-prem": False},  # VPN down for on-prem
        {"agent-cloud": "cloud", "agent-on-prem": "onprem"},
    )
    res = run_both(
        ["agent-cloud", "agent-on-prem"],
        run_set_fn=run_set_fn, probe_fn=probe_fn, target_fn=target_fn,
    )
    cloud, onprem = res.entries
    assert cloud.result is not None and cloud.skipped_reason is None
    assert onprem.result is None
    assert onprem.skipped_reason and "unreachable" in onprem.skipped_reason
    # an unreachable target is a skip, not a failure: the run is still a success
    assert res.exit_code() == 0
    # union coverage is just the reachable leg
    assert res.union_scored() == {"cloud-task"}


def test_active_profile_env_is_restored():
    os.environ["D365_E2E_PROFILE"] = "sentinel"
    run_set_fn, probe_fn, target_fn = _fakes(
        {"agent-cloud": True}, {"agent-cloud": "cloud"},
    )
    try:
        run_both(["agent-cloud"], run_set_fn=run_set_fn, probe_fn=probe_fn, target_fn=target_fn)
        assert os.environ["D365_E2E_PROFILE"] == "sentinel"
    finally:
        del os.environ["D365_E2E_PROFILE"]


def test_run_both_rejects_bad_repeat():
    # repeat is validated at run_both entry, not only inside run_set — so a bad value
    # fails deterministically even when every target is skipped and run_set never runs.
    run_set_fn, probe_fn, target_fn = _fakes({"agent-cloud": False}, {"agent-cloud": "cloud"})
    with pytest.raises(ValueError, match="repeat must be >= 1"):
        run_both(["agent-cloud"], repeat=0,
                 run_set_fn=run_set_fn, probe_fn=probe_fn, target_fn=target_fn)


def test_baseline_rows_one_per_target_with_fraction_and_skip_note():
    run_set_fn, probe_fn, target_fn = _fakes(
        {"agent-cloud": True, "agent-on-prem": False},
        {"agent-cloud": "cloud", "agent-on-prem": "onprem"},
    )
    res = run_both(
        ["agent-cloud", "agent-on-prem"], repeat=3,
        run_set_fn=run_set_fn, probe_fn=probe_fn, target_fn=target_fn,
    )
    rows = res.baseline_rows(today="2026-06-25")
    assert len(rows) == 2  # one row per attempted target
    cloud_row, onprem_row = rows
    assert cloud_row["date"] == "2026-06-25"
    assert cloud_row["target"] == "cloud"
    assert cloud_row["scored"] == "3/3"   # repeat threaded → fraction recorded
    assert cloud_row["repeat"] == 3
    assert "—" in onprem_row["pass_rate"] and "unreachable" in onprem_row["notes"]


def test_progress_prints_per_leg_header_and_forwards_to_run_set():
    # #585 AC4: a per-leg header (target, profile, reachable/skip, runnable count) is
    # printed before each leg, and the progress reporter is forwarded into run_set so
    # the per-task lines stream under their leg.
    buf = io.StringIO()
    reporter = StderrProgress(stream=buf)
    forwarded: list[object] = []

    def run_set_fn(*, repeat, agent_cmd, active_target, progress=None):
        forwarded.append(progress)
        # simulate the set emitting one resolved task line through the forwarded reporter
        if progress is not None:
            progress(ProgressEvent(done=1, total=1, task_id=f"{active_target}-t",
                                   target=active_target, status=PASS, runnable=1))
        return _set_result(active_target, passes=repeat, trials=repeat)

    def probe_fn(name):
        return name == "agent-cloud"  # on-prem unreachable

    def target_fn():
        return {"agent-cloud": "cloud", "agent-on-prem": "onprem"}[os.environ["D365_E2E_PROFILE"]]

    run_both(
        ["agent-cloud", "agent-on-prem"],
        run_set_fn=run_set_fn, probe_fn=probe_fn, target_fn=target_fn,
        progress=reporter, runnable_fn=lambda tgt: 7,
    )
    out = buf.getvalue()
    assert "── cloud (agent-cloud) ──  reachable, 7 runnable" in out
    assert "── onprem (agent-on-prem) ──  SKIPPED — unreachable" in out
    assert "[ 1/1] PASS  cloud-t  (cloud)" in out  # forwarded per-task line
    assert forwarded == [reporter]  # reachable leg got the reporter; skipped leg never ran


def test_no_progress_means_no_header_and_no_runnable_computation():
    # Default (no progress) stays silent and never calls runnable_fn — guards the
    # existing imported-call contract and avoids parsing tasks when progress is off.
    run_set_fn, probe_fn, target_fn = _fakes(
        {"agent-cloud": True}, {"agent-cloud": "cloud"},
    )

    def boom(_tgt):
        raise AssertionError("runnable_fn must not be called when progress is off")

    res = run_both(["agent-cloud"], run_set_fn=run_set_fn, probe_fn=probe_fn,
                   target_fn=target_fn, runnable_fn=boom)
    assert res.union_scored() == {"cloud-task"}


def test_append_baseline_adds_rows_after_table(tmp_path):
    path = tmp_path / "baseline.md"
    path.write_text(
        "# Baseline\n\n| date | target | profile | pass-rate | scored | repeat | notes |\n"
        "|------|--------|---------|-----------|--------|--------|-------|\n",
        encoding="utf-8",
    )
    rows = [
        {"date": "2026-06-25", "target": "cloud", "profile": "agent-cloud",
         "pass_rate": "90%", "scored": "9/10", "repeat": 3, "notes": ""},
    ]
    append_baseline(path, rows)
    text = path.read_text(encoding="utf-8")
    assert text.rstrip().endswith("| 2026-06-25 | cloud | agent-cloud | 90% | 9/10 | 3 |  |")
    # appending again keeps the table contiguous (no blank line splitting it)
    append_baseline(path, rows)
    assert text.count("\n\n") == path.read_text(encoding="utf-8").count("\n\n")  # no new blank-line breaks
