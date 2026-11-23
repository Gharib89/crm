"""Offline tests for the paired-leg orchestrator (issue #890, ADR 0028).

The paired run is the attribution keystone: one task run with-skill and bare against
**identical org state**, lift = the delta. The two org-touching legs need a live org, so
here we inject the leg runner and the org-reset hook to pin the *orchestration* — that
both legs run, that the org is reset **between** them (no leg-to-leg leakage), and that a
cap-hit leg scores as a fail — with no agent and no org.

    pytest evals/skill/test_paired.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from evals.skill.paired import PairedRunAborted, agent_argv, gate_tasks, run_pair
from evals.skill.results import aggregate_task
from evals.skill.runner import RunResult

_REAL_TASK = Path(__file__).parent / "tasks" / "records-create-verify.md"
"""A real corpus task — the judge path parses the task file for its prompt, so this must
exist on disk (the fake-run_one path below still never executes it)."""


def _fake_result(task_id: str, passed: bool, *, capped: bool = False) -> RunResult:
    return RunResult(
        task_id=task_id,
        dry_run=False,
        isolation_checks={},
        passed=passed,
        reason="capped" if capped else "ok",
        transcript="[agent exit 0]\n",
        capped=capped,
    )


def test_run_pair_resets_org_before_each_leg():
    """Both legs start from a reseeded org, so reset fires before each leg and leg B can't
    inherit leg A's mutations. Order is the whole point of the keystone.
    """
    events: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        events.append("skill" if install_skill else "bare")
        return _fake_result("records-create", passed=install_skill)

    def fake_reset():
        events.append("reset")

    trials = run_pair("records-create.md", run_one=fake_run_one, reset_org=fake_reset, k=1)
    assert events == ["reset", "skill", "reset", "bare"]
    legs = {t.leg for t in trials}
    assert legs == {"skill", "bare"}
    skill = next(t for t in trials if t.leg == "skill")
    bare = next(t for t in trials if t.leg == "bare")
    assert skill.passed is True and bare.passed is False


def _skill_trace(skill: str) -> str:
    import json

    event = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": skill}}]},
    }
    return json.dumps(event) + "\n" + json.dumps({"type": "result", "num_turns": 1}) + "\n"


def test_run_pair_records_invocation_of_the_tasks_own_skill():
    """A crm-migration trial that invoked `crm-migration` is invoked=True, not False (#1088).

    With the default skill ``crm``, the other skill is not a match.
    """

    def fake_run_one(task_file, *, install_skill, **kw):
        res = _fake_result("t1", passed=True)
        res.transcript = _skill_trace("crm-migration")
        return res

    kwargs = {"run_one": fake_run_one, "reset_org": lambda: None, "paired": False}
    mig = run_pair("t1.md", skill="crm-migration", **kwargs)
    assert [t.invoked for t in mig] == [True]
    default = run_pair("t1.md", **kwargs)
    assert [t.invoked for t in default] == [False]


def test_run_pair_k_produces_k_trials_per_leg():
    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=3)
    assert sum(1 for t in trials if t.leg == "skill") == 3
    assert sum(1 for t in trials if t.leg == "bare") == 3
    assert sorted(t.trial for t in trials if t.leg == "skill") == [0, 1, 2]


def test_run_pair_skips_bare_leg_when_not_paired():
    """The smoke and regression-check presets run with-skill only: the bare leg is skipped,
    so only the skill leg is reset and run (a single-condition run has no lift to measure).
    """
    events: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        events.append("skill" if install_skill else "bare")
        return _fake_result("t1", passed=True)

    def fake_reset():
        events.append("reset")

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=fake_reset, k=2, paired=False)
    assert events == ["reset", "skill", "reset", "skill"]
    assert {t.leg for t in trials} == {"skill"}
    assert len(trials) == 2


def test_run_pair_marks_capped_leg():
    """The skill leg overruns the wall clock while the bare leg completes."""

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=False, capped=install_skill)

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1)
    skill = next(t for t in trials if t.leg == "skill")
    assert skill.capped is True and skill.passed is False


def test_run_pair_emits_stderr_progress_per_leg():
    """A start and a resolve line per leg keep both legs visible during a long run."""
    lines: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    run_pair(
        "t1.md",
        run_one=fake_run_one,
        reset_org=lambda: None,
        k=1,
        progress=lines.append,
    )
    assert any("skill leg" in line for line in lines)
    assert any("bare leg" in line for line in lines)
    assert any("pass" in line for line in lines)


def test_run_pair_attaches_blind_judgment_per_trial():
    """Every leg-trial carries a judge verdict, isolated from its L1 ``passed``. The judge
    fires once per leg and only ever receives (prompt, transcript), never the leg, so it is
    blind to condition by construction; both legs get the SAME prompt.
    """
    calls: list[tuple[str, str]] = []

    def fake_judge(prompt: str, transcript: str) -> dict:
        calls.append((prompt, transcript))
        return {"rubric_version": "1", "model": "opus", "scores": {"elegance": {"score": 4}}}

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("records-create-verify", passed=install_skill)

    trials = run_pair(
        _REAL_TASK, run_one=fake_run_one, reset_org=lambda: None, k=1, judge=fake_judge
    )
    assert all(t.judge is not None for t in trials)
    assert all(t.judge["rubric_version"] == "1" for t in trials)
    assert len(calls) == 2
    assert {p for p, _ in calls} == {calls[0][0]}


def test_run_pair_no_judge_leaves_field_none():
    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1)
    assert all(t.judge is None for t in trials)


def test_judge_output_never_affects_aggregates():
    """Criterion 3 (issue #894): lift stats are provably unaffected by the judge. Run the
    same task twice, once with a judge and once without, and prove the aggregate (pass
    rates + Hake gain) is byte-identical either way. The judge used here "hinders"
    everything, and must not move any number.
    """

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("records-create-verify", passed=install_skill)

    def loud_judge(prompt: str, transcript: str) -> dict:
        return {"rubric_version": "1", "model": "opus", "scores": {"elegance": {"score": 1}}}

    judged = run_pair(
        _REAL_TASK, run_one=fake_run_one, reset_org=lambda: None, k=2, judge=loud_judge
    )
    plain = run_pair(_REAL_TASK, run_one=fake_run_one, reset_org=lambda: None, k=2)
    agg_judged = aggregate_task("records-create-verify", judged).to_dict()
    agg_plain = aggregate_task("records-create-verify", plain).to_dict()
    assert agg_judged == agg_plain


def test_agent_argv_pins_allowed_tools_and_turn_cap():
    """Web tools are absent from the allowlist; only these five are permitted. The
    ``stream-json`` trace is what trace.py parses for the command sequence + metrics.
    """
    argv = agent_argv(model="sonnet", max_turns=50)
    joined = " ".join(argv)
    assert "--allowedTools Bash,Read,Grep,Glob,Skill" in joined
    assert "--max-turns 50" in joined
    assert "--model sonnet" in joined
    assert "stream-json" in joined


def test_agent_argv_emits_bin():
    """The rootless run uses "claude" on PATH by default, but the binary is still a seam."""
    argv = agent_argv(model="sonnet", claude_bin="/opt/claude/bin/claude")
    assert argv[0] == "/opt/claude/bin/claude"


def test_agent_argv_defaults_bin_to_claude():
    assert agent_argv()[0] == "claude"


def test_agent_argv_survives_shlex_roundtrip_with_spaced_bin():
    """The front door joins argv into a string that runner._resolve_agent_cmd shlex.splits
    back; a space in argv[0] must survive the roundtrip (shlex.join, not " ".join).
    """
    import shlex

    argv = agent_argv(claude_bin="/opt/my claude/claude")
    assert shlex.split(shlex.join(argv)) == argv


def _api_error_result(task_id: str) -> RunResult:
    """The driver died on an API error (e.g. 529 Overloaded): agent exit 1 and a terminal
    result event flagged is_error — the shape run_pair must retry, not score (#943).
    """
    return RunResult(
        task_id=task_id,
        dry_run=False,
        isolation_checks={},
        passed=False,
        reason="agent error",
        transcript='[agent exit 1]\n{"type": "result", "is_error": true, "num_turns": 1}\n',
        capped=False,
    )


def test_run_pair_retries_leg_on_agent_api_error():
    """The first skill-leg attempt dies driver-side; the retry (and the bare leg) complete.
    The retry replaces the poisoned attempt, so there is still exactly one trial per leg,
    and the org is reset before the retry so it can't inherit the dead attempt's mutations.
    """
    events: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        leg = "skill" if install_skill else "bare"
        events.append(leg)
        if leg == "skill" and events.count("skill") == 1:
            return _api_error_result("t1")
        return _fake_result("t1", passed=True)

    def fake_reset():
        events.append("reset")

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=fake_reset, k=1)
    assert events == ["reset", "skill", "reset", "skill", "reset", "bare"]
    assert [t.leg for t in trials] == ["skill", "bare"]
    assert all(t.passed for t in trials)


def test_run_pair_persistent_api_error_aborts_after_bounded_retries():
    """A persistent infra failure (usage-limit window, auth expiry, outage) survives the
    bounded per-leg retries; scoring it a fail would poison every following trial (#943),
    so run_pair aborts the whole run instead of recording a trial for this task. The call
    count is the initial attempt plus the bounded retries, then the abort: bounded, no
    infinite loop. The aborting task is named so the operator's resume targets it.
    """
    calls = {"n": 0}

    def fake_run_one(task_file, *, install_skill, **kw):
        calls["n"] += 1
        return _api_error_result("t1")

    with pytest.raises(PairedRunAborted) as excinfo:
        run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1, paired=False)
    assert calls["n"] == 3
    assert excinfo.value.task_id == "t1"


def test_run_pair_abort_carries_infra_cause_from_transcript():
    """The transcript's cause is surfaced (as far as it reveals it) so the abort is actionable."""

    def fake_run_one(task_file, *, install_skill, **kw):
        return RunResult(
            task_id="t1",
            dry_run=False,
            isolation_checks={},
            passed=False,
            reason="agent error",
            transcript=(
                '{"type": "result", "is_error": true, '
                '"subtype": "error_during_execution", '
                '"result": "Claude AI usage limit reached", "num_turns": 1}\n'
            ),
            capped=False,
        )

    with pytest.raises(PairedRunAborted) as excinfo:
        run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1, paired=False)
    assert "usage limit reached" in str(excinfo.value)


def test_run_pair_abort_writes_no_trial_for_aborting_task():
    """Criterion: no trial record for the aborting task is produced — run_pair raises before
    returning, so the front door never appends a poisoned block for it (the task stays
    incomplete and --resume reruns it).
    """

    def fake_run_one(task_file, *, install_skill, **kw):
        return _api_error_result("t1")

    captured: list = []
    try:
        captured = run_pair(
            "t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1, paired=False
        )
    except PairedRunAborted:
        pass
    assert captured == []


def _resume_args(**overrides):
    import argparse

    from evals.skill.presets import DEFAULT_SEED
    from evals.skill.results import RESULTS_ROOT

    base = dict(
        preset="full",
        model="sonnet",
        k=3,
        tasks=None,
        sample=None,
        seed=DEFAULT_SEED,
        no_sandbox=False,
        judge=False,
        judge_cmd=None,
        results_dir=str(RESULTS_ROOT),
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_resume_command_reconstructs_the_originating_flags():
    """A resumable run re-reads run.json and validates model/target/k/preset/subset, so the
    printed command must replay the flags the run was started with (#943). Non-default
    knobs are omitted when unset so the hint stays minimal.
    """
    from evals.skill.paired import _resume_command

    cmd = _resume_command("20260804T101010Z-abcd", _resume_args())
    assert "--resume 20260804T101010Z-abcd" in cmd
    assert "--preset full" in cmd
    assert "--model sonnet" in cmd
    assert "--k 3" in cmd
    assert "D365_E2E=1" in cmd
    assert "--sample" not in cmd
    assert "--no-sandbox" not in cmd
    assert "--judge" not in cmd


def test_resume_command_carries_subset_and_sandbox_selectors():
    """Subset and sandbox selectors change the stamped meta resume validates, so they must
    survive into the hint verbatim.
    """
    from evals.skill.paired import _resume_command

    cmd = _resume_command(
        "run-2", _resume_args(tasks="a,b", no_sandbox=True, judge=True, sample=5, seed=7)
    )
    assert "--tasks a,b" in cmd
    assert "--sample 5" in cmd
    assert "--seed 7" in cmd
    assert "--no-sandbox" in cmd
    assert "--judge" in cmd


def test_gate_tasks_skips_offtarget_and_diagnostic(tmp_path):
    """The paired path must gate the corpus like the set runner: an off-target task
    (seed_target raises) or a diagnostic one (run_task refuses without --analyze)
    would otherwise crash the run at that task — after hours of finished trials,
    none of them yet written (results land only at the end). The ``diag`` task has no
    ``end_state.expect``, which is what makes it diagnostic.
    """

    def _write(name: str, target: str, body: str = "") -> Path:
        p = tmp_path / f"{name}.md"
        p.write_text(
            f"---\nid: {name}\ndomain: d\ntarget: {target}\ncleanup: []\n{body}---\n"
            "Do the thing.\n",
            encoding="utf-8",
        )
        return p

    expect = "end_state:\n  query: [query, odata, accounts]\n  expect: {count: 1}\n"
    cloud_ok = _write("cloud-ok", "cloud", expect)
    either_ok = _write("either-ok", "either", expect)
    onprem = _write("onprem-only", "onprem", expect)
    diagnostic = _write("diag", "cloud")

    runnable, skipped = gate_tasks([cloud_ok, either_ok, onprem, diagnostic], "cloud")
    assert runnable == [cloud_ok, either_ok]
    assert [p.stem for p, _ in skipped] == ["onprem-only", "diag"]
    assert "target" in skipped[0][1]
    assert "diagnostic" in skipped[1][1]


def test_run_pair_verdict_lines_carry_color_and_duration():
    """Each leg's resolve line is a green pass or red fail with a wall-time suffix: the live
    skill-vs-bare timing read the maintainer watches during a long run.
    """
    lines: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1, progress=lines.append)
    verdicts = [ln for ln in lines if "(" in ln and "leg ·" in ln]
    assert len(verdicts) == 2
    skill_line = next(ln for ln in verdicts if "skill leg" in ln)
    bare_line = next(ln for ln in verdicts if "bare leg" in ln)
    assert "\033[32mpass\033[0m" in skill_line
    assert "\033[31mfail\033[0m" in bare_line
    assert re.search(r"\((\d+m)?\d{1,2}s\)", skill_line)
