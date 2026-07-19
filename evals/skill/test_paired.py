"""Offline tests for the paired-leg orchestrator (issue #890, ADR 0028).

The paired run is the attribution keystone: one task run with-skill and bare against
**identical org state**, lift = the delta. The two org-touching legs need a live org, so
here we inject the leg runner and the org-reset hook to pin the *orchestration* — that
both legs run, that the org is reset **between** them (no leg-to-leg leakage), and that a
cap-hit leg scores as a fail — with no agent and no org.

    pytest evals/skill/test_paired.py
"""

from __future__ import annotations

from evals.skill import paired as paired_mod
from evals.skill.paired import agent_argv, resolve_agent_bin, run_pair
from evals.skill.runner import RunError, RunResult


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


def test_agent_argv_pins_allowed_tools_and_turn_cap():
    argv = agent_argv(model="sonnet", max_turns=50)
    joined = " ".join(argv)
    # web tools are absent from the allowlist; only these five are permitted.
    assert "--allowedTools Bash,Read,Grep,Glob,Skill" in joined
    assert "--max-turns 50" in joined
    assert "--model sonnet" in joined
    # stream-json trace is what trace.py parses for the command sequence + metrics.
    assert "stream-json" in joined


def test_agent_argv_emits_resolved_bin():
    # sudo resets PATH (drops ~/.local/bin), so a bare "claude" exec fails; the front door
    # resolves the binary once and bakes the absolute path into argv[0].
    argv = agent_argv(model="sonnet", claude_bin="/opt/claude/bin/claude")
    assert argv[0] == "/opt/claude/bin/claude"


def test_agent_argv_defaults_bin_to_claude():
    assert agent_argv()[0] == "claude"


def test_agent_argv_survives_shlex_roundtrip_with_spaced_bin():
    import shlex

    # The front door joins argv into a string that runner._resolve_agent_cmd shlex.splits
    # back; now that argv[0] can be a resolved path, a space in it must survive the roundtrip
    # (shlex.join, not " ".join) or the agent won't launch.
    argv = agent_argv(claude_bin="/opt/my claude/claude")
    assert shlex.split(shlex.join(argv)) == argv


def test_resolve_agent_bin_honors_override_env(monkeypatch, tmp_path):
    # Mirrors runner's CRM_EVAL_AGENT_CMD knob — an explicit executable path always wins.
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("CRM_EVAL_CLAUDE_BIN", str(fake))
    assert resolve_agent_bin() == str(fake)


def test_resolve_agent_bin_rejects_non_executable_override(monkeypatch, tmp_path):
    import pytest

    # A typo'd override must fail in the ~1s preflight, not after the 60s venv build + resets.
    monkeypatch.setenv("CRM_EVAL_CLAUDE_BIN", str(tmp_path / "nope"))
    with pytest.raises(RunError, match="not an executable file"):
        resolve_agent_bin()


def test_resolve_agent_bin_falls_back_to_which(monkeypatch):
    monkeypatch.delenv("CRM_EVAL_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(paired_mod.shutil, "which", lambda name: "/usr/local/bin/claude")
    assert resolve_agent_bin() == "/usr/local/bin/claude"


def test_resolve_agent_bin_raises_when_unresolved(monkeypatch):
    import pytest

    # sudo drops ~/.local/bin from PATH → which() finds nothing; fail loudly and legibly
    # (naming the escape hatch) before the ~60s venv build + live org resets.
    monkeypatch.delenv("CRM_EVAL_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(paired_mod.shutil, "which", lambda name: None)
    with pytest.raises(RunError, match="CRM_EVAL_CLAUDE_BIN"):
        resolve_agent_bin()


def test_chown_results_hands_back_to_invoker(monkeypatch, tmp_path, capsys):
    # Under sudo the root parent wrote run_dir as root; every path is chowned to the invoking
    # uid so the maintainer isn't left with a root-owned tree.
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "transcripts" / "t.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")
    chowned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(paired_mod.os, "chown", lambda p, u, g: chowned.append((str(p), u, g)))
    paired_mod._chown_results_to_invoker(tmp_path)
    assert (str(tmp_path), 1000, 1000) in chowned
    assert (str(tmp_path / "transcripts" / "t.txt"), 1000, 1000) in chowned
    assert capsys.readouterr().err == ""  # clean run is silent


def test_chown_results_noop_without_sudo(monkeypatch, tmp_path):
    # Not sudo-elevated → the results are already the invoker's; chown is never called.
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.delenv("SUDO_GID", raising=False)

    def _boom(*_args):
        raise AssertionError("chown must not run without sudo")

    monkeypatch.setattr(paired_mod.os, "chown", _boom)
    paired_mod._chown_results_to_invoker(tmp_path)  # no raise


def test_chown_results_surfaces_failure(monkeypatch, tmp_path, capsys):
    # A chown failure is surfaced (not swallowed) with the manual fallback, so the
    # "owned by the invoking user" acceptance is observably met-or-not.
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")

    def _deny(*_args):
        raise OSError("EPERM")

    monkeypatch.setattr(paired_mod.os, "chown", _deny)
    paired_mod._chown_results_to_invoker(tmp_path)  # does not raise
    err = capsys.readouterr().err
    assert "could not chown results" in err
    assert "sudo chown -R $USER" in err
