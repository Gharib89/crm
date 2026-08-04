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

from evals.skill.paired import agent_argv, gate_tasks, run_pair
from evals.skill.results import aggregate_task
from evals.skill.runner import RunResult

#: A real corpus task — the judge path parses the task file for its prompt, so this must
#: exist on disk (the fake-run_one path below still never executes it).
_REAL_TASK = Path(__file__).parent / "tasks" / "records-create-verify.md"


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
    events: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        events.append("skill" if install_skill else "bare")
        return _fake_result("records-create", passed=install_skill)

    def fake_reset():
        events.append("reset")

    trials = run_pair("records-create.md", run_one=fake_run_one, reset_org=fake_reset, k=1)
    # Both legs start from a reseeded org → reset fires before each leg, so leg B can't
    # inherit leg A's mutations. Order is the whole point of the keystone.
    assert events == ["reset", "skill", "reset", "bare"]
    legs = {t.leg for t in trials}
    assert legs == {"skill", "bare"}
    skill = next(t for t in trials if t.leg == "skill")
    bare = next(t for t in trials if t.leg == "bare")
    assert skill.passed is True and bare.passed is False


def test_run_pair_k_produces_k_trials_per_leg():
    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=3)
    assert sum(1 for t in trials if t.leg == "skill") == 3
    assert sum(1 for t in trials if t.leg == "bare") == 3
    # trial indices are 0..k-1 per leg
    assert sorted(t.trial for t in trials if t.leg == "skill") == [0, 1, 2]


def test_run_pair_skips_bare_leg_when_not_paired():
    events: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        events.append("skill" if install_skill else "bare")
        return _fake_result("t1", passed=True)

    def fake_reset():
        events.append("reset")

    # smoke / regression-check presets run with-skill only — the bare leg is skipped, so
    # only the skill leg is reset + run (a single-condition run has no lift to measure).
    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=fake_reset, k=2, paired=False)
    assert events == ["reset", "skill", "reset", "skill"]
    assert {t.leg for t in trials} == {"skill"}
    assert len(trials) == 2


def test_run_pair_marks_capped_leg():
    def fake_run_one(task_file, *, install_skill, **kw):
        # skill leg overruns the wall clock; bare leg completes.
        return _fake_result("t1", passed=False, capped=install_skill)

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1)
    skill = next(t for t in trials if t.leg == "skill")
    assert skill.capped is True and skill.passed is False


def test_run_pair_emits_stderr_progress_per_leg():
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
    # a start + a resolve line per leg → both legs visible during a long run.
    assert any("skill leg" in line for line in lines)
    assert any("bare leg" in line for line in lines)
    assert any("pass" in line for line in lines)


def test_run_pair_attaches_blind_judgment_per_trial():
    calls: list[tuple[str, str]] = []

    def fake_judge(prompt: str, transcript: str) -> dict:
        calls.append((prompt, transcript))
        return {"rubric_version": "1", "model": "opus", "scores": {"elegance": {"score": 4}}}

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("records-create-verify", passed=install_skill)

    trials = run_pair(
        _REAL_TASK, run_one=fake_run_one, reset_org=lambda: None, k=1, judge=fake_judge
    )
    # every leg-trial carries a judge verdict, isolated from its L1 `passed`
    assert all(t.judge is not None for t in trials)
    assert all(t.judge["rubric_version"] == "1" for t in trials)
    # judge fires once per leg; it only ever receives (prompt, transcript) — never the leg,
    # so it is blind to condition by construction. Both legs get the SAME prompt.
    assert len(calls) == 2
    assert {p for p, _ in calls} == {calls[0][0]}


def test_run_pair_no_judge_leaves_field_none():
    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1)
    assert all(t.judge is None for t in trials)


def test_judge_output_never_affects_aggregates():
    # Criterion 3 (issue #894): lift stats are provably unaffected by the judge. Run the
    # same task twice — once with a judge, once without — and prove the aggregate (pass
    # rates + Hake gain) is byte-identical either way.
    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("records-create-verify", passed=install_skill)

    def loud_judge(prompt: str, transcript: str) -> dict:
        # A judge that "hindered" everything must not move any number.
        return {"rubric_version": "1", "model": "opus", "scores": {"elegance": {"score": 1}}}

    judged = run_pair(
        _REAL_TASK, run_one=fake_run_one, reset_org=lambda: None, k=2, judge=loud_judge
    )
    plain = run_pair(_REAL_TASK, run_one=fake_run_one, reset_org=lambda: None, k=2)
    agg_judged = aggregate_task("records-create-verify", judged).to_dict()
    agg_plain = aggregate_task("records-create-verify", plain).to_dict()
    assert agg_judged == agg_plain


def test_agent_argv_pins_allowed_tools_and_turn_cap():
    argv = agent_argv(model="sonnet", max_turns=50)
    joined = " ".join(argv)
    # web tools are absent from the allowlist; only these five are permitted.
    assert "--allowedTools Bash,Read,Grep,Glob,Skill" in joined
    assert "--max-turns 50" in joined
    assert "--model sonnet" in joined
    # stream-json trace is what trace.py parses for the command sequence + metrics.
    assert "stream-json" in joined


def test_agent_argv_emits_bin():
    # The rootless run uses "claude" on PATH by default, but the binary is still a seam.
    argv = agent_argv(model="sonnet", claude_bin="/opt/claude/bin/claude")
    assert argv[0] == "/opt/claude/bin/claude"


def test_agent_argv_defaults_bin_to_claude():
    assert agent_argv()[0] == "claude"


def test_agent_argv_survives_shlex_roundtrip_with_spaced_bin():
    import shlex

    # The front door joins argv into a string that runner._resolve_agent_cmd shlex.splits
    # back; a space in argv[0] must survive the roundtrip (shlex.join, not " ".join).
    argv = agent_argv(claude_bin="/opt/my claude/claude")
    assert shlex.split(shlex.join(argv)) == argv


def _api_error_result(task_id: str) -> RunResult:
    # The driver died on an API error (e.g. 529 Overloaded): agent exit 1 and a terminal
    # result event flagged is_error — the shape run_pair must retry, not score (#943).
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
    events: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        leg = "skill" if install_skill else "bare"
        events.append(leg)
        # first skill-leg attempt dies driver-side; the retry (and the bare leg) complete.
        if leg == "skill" and events.count("skill") == 1:
            return _api_error_result("t1")
        return _fake_result("t1", passed=True)

    def fake_reset():
        events.append("reset")

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=fake_reset, k=1)
    # The retry replaces the poisoned attempt — still exactly one trial per leg, and the
    # org is reset before the retry so it can't inherit the dead attempt's mutations.
    assert events == ["reset", "skill", "reset", "skill", "reset", "bare"]
    assert [t.leg for t in trials] == ["skill", "bare"]
    assert all(t.passed for t in trials)


def test_run_pair_persistent_api_error_scores_fail_after_bounded_retries():
    calls = {"n": 0}

    def fake_run_one(task_file, *, install_skill, **kw):
        calls["n"] += 1
        return _api_error_result("t1")

    trials = run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1, paired=False)
    # initial attempt + the bounded retries, then the outage lands as a recorded fail —
    # bounded so a persistent outage can't loop a 10-hour run forever.
    assert calls["n"] == 3
    assert len(trials) == 1 and trials[0].passed is False


def test_gate_tasks_skips_offtarget_and_diagnostic(tmp_path):
    # The paired path must gate the corpus like the set runner: an off-target task
    # (seed_target raises) or a diagnostic one (run_task refuses without --analyze)
    # would otherwise crash the run at that task — after hours of finished trials,
    # none of them yet written (results land only at the end).
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
    diagnostic = _write("diag", "cloud")  # no end_state.expect → diagnostic

    runnable, skipped = gate_tasks([cloud_ok, either_ok, onprem, diagnostic], "cloud")
    assert runnable == [cloud_ok, either_ok]
    assert [p.stem for p, _ in skipped] == ["onprem-only", "diag"]
    assert "target" in skipped[0][1]
    assert "diagnostic" in skipped[1][1]


def test_run_pair_verdict_lines_carry_color_and_duration():
    lines: list[str] = []

    def fake_run_one(task_file, *, install_skill, **kw):
        return _fake_result("t1", passed=install_skill)

    run_pair("t1.md", run_one=fake_run_one, reset_org=lambda: None, k=1, progress=lines.append)
    verdicts = [ln for ln in lines if "(" in ln and "leg ·" in ln]
    assert len(verdicts) == 2  # one resolve line per leg
    skill_line = next(ln for ln in verdicts if "skill leg" in ln)
    bare_line = next(ln for ln in verdicts if "bare leg" in ln)
    # green pass / red fail, and a wall-time suffix per leg — the live skill-vs-bare
    # timing read the maintainer watches during a long run.
    assert "\033[32mpass\033[0m" in skill_line
    assert "\033[31mfail\033[0m" in bare_line
    assert re.search(r"\((\d+m)?\d{1,2}s\)", skill_line)
