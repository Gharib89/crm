"""Offline tests for live progress (issue #585, Part 1).

The set runner drives progress through an injectable ``progress`` callback — never
bare prints — so imported callers stay silent and the both-targets runner can own
the stream. These tests exercise the callback firing per task, the trial ticks under
``--repeat``, the rolling per-target tally, and the TTY/``--quiet``/``--progress``
gating, all with a list-appending fake or a ``StringIO`` stream — no agent, no org.

    pytest evals/skill
"""
from __future__ import annotations

import io
from pathlib import Path

from evals.skill import set_runner
from evals.skill.runner import RunResult
from evals.skill.set_runner import (
    FAIL,
    PASS,
    ProgressEvent,
    StderrProgress,
    discover_tasks,
    run_set,
)
from evals.skill.taskspec import parse_task_file

TASKS_DIR = Path(__file__).parent / "tasks"


def _stub(verdicts: dict[str, bool]):
    def run_one(path, *, dry_run, agent_cmd, crm_bin):
        spec = parse_task_file(path)
        return RunResult(
            task_id=spec.id, dry_run=False, isolation_checks={},
            passed=verdicts.get(spec.id, True), reason="stubbed",
        )

    return run_one


def test_progress_fires_one_resolution_event_per_task():
    events: list[ProgressEvent] = []
    run_set(TASKS_DIR, active_target="cloud", run_one=_stub({}), progress=events.append)
    resolutions = [e for e in events if e.status is not None]
    assert len(resolutions) == len(discover_tasks(TASKS_DIR))
    # done counts every task (skips included), 1-based, monotonic to total
    assert [e.done for e in resolutions] == list(range(1, len(resolutions) + 1))
    assert all(e.total == len(resolutions) for e in resolutions)


def test_imported_run_set_is_silent_by_default():
    # No progress arg → no callback, nothing written. Guards the existing tests /
    # both_runner imported calls against stray stderr noise.
    events: list[ProgressEvent] = []
    run_set(TASKS_DIR, active_target="cloud", run_one=_stub({}))  # no progress=
    assert events == []


def test_repeat_emits_a_trial_tick_per_trial_with_index():
    events: list[ProgressEvent] = []
    run_set(TASKS_DIR, active_target="cloud", repeat=3, run_one=_stub({}), progress=events.append)
    ticks = [e for e in events if e.status is None]
    # one scored task contributes 3 trial ticks (1/3, 2/3, 3/3); skips contribute none
    scored = {e.task_id for e in events if e.status in (PASS, FAIL)}
    assert ticks, "expected trial ticks under --repeat"
    assert all(e.trials == 3 for e in ticks)
    by_task: dict[str, list[int]] = {}
    for e in ticks:
        by_task.setdefault(e.task_id, []).append(e.trial)
    for tid in scored:
        assert by_task.get(tid) == [1, 2, 3], f"{tid} trial indices {by_task.get(tid)}"


def test_single_run_emits_no_trial_ticks():
    events: list[ProgressEvent] = []
    run_set(TASKS_DIR, active_target="cloud", run_one=_stub({}), progress=events.append)
    assert all(e.status is not None for e in events)  # repeat=1 → resolution ticks only


def test_stderr_progress_rolling_tally_accumulates_per_target():
    buf = io.StringIO()
    rep = StderrProgress(stream=buf)
    rep(ProgressEvent(done=1, total=2, task_id="a", target="cloud", status=PASS, runnable=2))
    rep(ProgressEvent(done=2, total=2, task_id="b", target="cloud", status=FAIL, runnable=2))
    out = buf.getvalue()
    assert "[ 1/2] PASS  a  (cloud)" in out
    assert "cloud: 1 pass / 0 fail  (of 2 runnable)" in out
    assert "cloud: 1 pass / 1 fail  (of 2 runnable)" in out  # rolling, not reset


def test_stderr_progress_leg_header():
    buf = io.StringIO()
    rep = StderrProgress(stream=buf)
    rep.leg(target="cloud", profile="agent-cloud", reachable=True, runnable=10)
    rep.leg(target="onprem", profile="agent-on-prem", reachable=False, reason="unreachable (VPN down)")
    out = buf.getvalue()
    assert "── cloud (agent-cloud) ──  reachable, 10 runnable" in out
    assert "── onprem (agent-on-prem) ──  SKIPPED — unreachable (VPN down)" in out


def test_want_progress_gating():
    from evals.skill.set_runner import want_progress

    # default follows the TTY: on at a terminal, off under redirect / non-TTY
    assert want_progress(quiet=False, progress=False, isatty=True) is True
    assert want_progress(quiet=False, progress=False, isatty=False) is False
    # --quiet forces off even at a TTY; --progress forces on even under redirect
    assert want_progress(quiet=True, progress=False, isatty=True) is False
    assert want_progress(quiet=False, progress=True, isatty=False) is True


def test_stdout_json_is_identical_with_progress_on_and_off():
    # The stderr progress display must not perturb the returned result (the > result.json
    # contract): same outcomes/pass-rate whether or not a progress callback is attached.
    off = run_set(TASKS_DIR, active_target="cloud", run_one=_stub({}))
    on = run_set(TASKS_DIR, active_target="cloud", run_one=_stub({}), progress=lambda e: None)
    assert off.to_dict() == on.to_dict()
