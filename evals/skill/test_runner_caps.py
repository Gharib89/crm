"""Offline tests for the agent turn/wall-clock cap (issue #890, ADR 0028).

ADR 0028 caps each trial at ``--max-turns 50`` + a 10-minute wall clock and treats a
cap-hit as a **distinct outcome that scores as a fail**. The turn cap is a ``claude``
flag; the wall-clock cap is the runner's job. This pins the wall-clock seam directly
with a fast sleeping command — no agent, no org — so the timeout path is proven without
the 10-minute real budget.

    pytest evals/skill/test_runner_caps.py
"""

from __future__ import annotations

from evals.skill.runner import _run_agent


def test_wall_clock_cap_trips_on_overrun(tmp_path):
    transcript, capped = _run_agent(
        "prompt", ["sleep", "5"], cwd=str(tmp_path), env={}, wall_clock_s=1
    )
    assert capped is True
    assert "cap" in transcript.lower()


def test_normal_completion_is_not_capped(tmp_path):
    transcript, capped = _run_agent(
        "prompt", ["printf", "hello-world"], cwd=str(tmp_path), env={}, wall_clock_s=30
    )
    assert capped is False
    assert "hello-world" in transcript


def test_no_wall_clock_never_caps(tmp_path):
    transcript, capped = _run_agent(
        "prompt", ["printf", "ok"], cwd=str(tmp_path), env={}, wall_clock_s=None
    )
    assert capped is False
    assert "ok" in transcript
