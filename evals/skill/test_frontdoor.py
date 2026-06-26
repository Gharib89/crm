"""Offline tests for the `python -m evals.skill run` front door (issue #585, Part 2).

The front door is a thin convenience wrapper over the existing set/both runners: it
routes ``--target`` to the right runner, defaults the agent command away from the
permission-gate footgun, derives the cloud allow-host, and writes ``result.json`` +
``run.log`` automatically. The runners and the host resolution are injectable so this
drives the wiring with no agent, no live org, and no real profile store.

    pytest evals/skill
"""
from __future__ import annotations

import json

import pytest

from evals.skill import __main__ as frontdoor
from evals.skill.__main__ import DEFAULT_MODEL, FrontDoorError, build_agent_cmd, run
from evals.skill.set_runner import PASS, SetResult, TaskOutcome


def _set_result() -> SetResult:
    return SetResult(outcomes=[TaskOutcome("t", PASS, "cloud")], active_target="cloud", dry_run=False)


# --- agent-cmd defaulting / override (AC2) -------------------------------------------

def test_default_agent_cmd_is_skip_permissions_sonnet():
    assert build_agent_cmd(None, None) == "claude -p --dangerously-skip-permissions --model sonnet"
    assert DEFAULT_MODEL == "sonnet"


def test_model_swaps_the_default_model():
    assert build_agent_cmd(None, "opus") == "claude -p --dangerously-skip-permissions --model opus"


def test_agent_cmd_overrides_entirely():
    assert build_agent_cmd("my-agent --foo", None) == "my-agent --foo"


def test_model_with_agent_cmd_is_rejected():
    with pytest.raises(FrontDoorError, match="--model"):
        build_agent_cmd("my-agent", "opus")


def test_invalid_target_rejected_cleanly(tmp_path):
    # run() is importable/tested directly; a bad target is a readable FrontDoorError,
    # not a KeyError from the internal profile lookup.
    with pytest.raises(FrontDoorError, match="target"):
        run(target="foo", out_dir=tmp_path,
            run_set_fn=lambda **k: _set_result(), run_both_fn=lambda *a, **k: None)


def test_update_baseline_rejected_for_single_target(tmp_path, monkeypatch):
    # The baseline trend is a both-targets concept; --update-baseline with a single
    # target is rejected rather than silently doing nothing (issue #585 anti-footgun).
    monkeypatch.setenv("D365_E2E_ALLOW_HOST", "x")
    with pytest.raises(FrontDoorError, match="both"):
        run(target="cloud", update_baseline=True, out_dir=tmp_path,
            run_set_fn=lambda **k: _set_result(), run_both_fn=lambda *a, **k: None)


# --- target → runner routing (AC1) ---------------------------------------------------

def test_target_both_routes_to_both_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("D365_E2E_ALLOW_HOST", "x")  # skip host derivation
    calls = {}

    def fake_both(profiles, *, repeat, agent_cmd, progress):
        calls["both"] = {"profiles": list(profiles), "agent_cmd": agent_cmd}

        class _R:
            def to_dict(self):
                return {"both": True}

            def exit_code(self):
                return 0
        return _R()

    def fake_set(**kw):  # must not be called
        raise AssertionError("set runner used for --target both")

    rc = run(target="both", out_dir=tmp_path, run_set_fn=fake_set, run_both_fn=fake_both)
    assert rc == 0
    assert "both" in calls
    assert json.loads((tmp_path / "result.json").read_text())["both"] is True


@pytest.mark.parametrize("target,profile", [("cloud", "agent-cloud"), ("onprem", "agent-on-prem")])
def test_single_target_routes_to_set_runner(target, profile, tmp_path, monkeypatch):
    monkeypatch.setenv("D365_E2E_ALLOW_HOST", "x")
    seen = {}

    def fake_set(*, active_target, repeat, agent_cmd, progress):
        seen["active_target"] = active_target
        seen["profile_env"] = __import__("os").environ.get("D365_E2E_PROFILE")
        return _set_result()

    def fake_both(*a, **k):
        raise AssertionError("both runner used for a single target")

    rc = run(target=target, out_dir=tmp_path, run_set_fn=fake_set, run_both_fn=fake_both)
    assert rc == 0
    assert seen["active_target"] == target
    assert seen["profile_env"] == profile  # front door points D365_E2E_PROFILE at the profile


# --- allow-host derivation (AC3) -----------------------------------------------------

def test_cloud_allow_host_derived_from_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("D365_E2E_ALLOW_HOST", raising=False)
    seen = {}

    def fake_host(profile_name):
        assert profile_name == "agent-cloud"
        return "org.crm.dynamics.com"

    def fake_set(*, active_target, repeat, agent_cmd, progress):
        seen["allow_host"] = __import__("os").environ.get("D365_E2E_ALLOW_HOST")
        return _set_result()

    run(target="cloud", out_dir=tmp_path, host_fn=fake_host,
        run_set_fn=fake_set, run_both_fn=lambda *a, **k: None)
    # derived host is visible to the runner during the run...
    assert seen["allow_host"] == "org.crm.dynamics.com"
    # ...and restored (here: cleared) afterwards so an in-process caller isn't leaked into.
    assert __import__("os").environ.get("D365_E2E_ALLOW_HOST") is None


def test_single_target_restores_profile_env(tmp_path, monkeypatch):
    # run() points D365_E2E_PROFILE at the profile during the run, then restores the
    # caller's prior value (Copilot round-1 finding: no env leak to in-process callers).
    monkeypatch.setenv("D365_E2E_ALLOW_HOST", "x")
    monkeypatch.setenv("D365_E2E_PROFILE", "sentinel")
    seen = {}

    def fake_set(*, active_target, repeat, agent_cmd, progress):
        seen["during"] = __import__("os").environ.get("D365_E2E_PROFILE")
        return _set_result()

    run(target="cloud", out_dir=tmp_path, run_set_fn=fake_set, run_both_fn=lambda *a, **k: None)
    assert seen["during"] == "agent-cloud"  # pointed at the profile during the run
    assert __import__("os").environ["D365_E2E_PROFILE"] == "sentinel"  # restored after


def test_existing_allow_host_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setenv("D365_E2E_ALLOW_HOST", "preset.example.com")

    def fake_host(profile_name):
        raise AssertionError("host derivation must be skipped when allow-host is preset")

    run(target="cloud", out_dir=tmp_path, host_fn=fake_host,
        run_set_fn=lambda **k: _set_result(), run_both_fn=lambda *a, **k: None)
    assert frontdoor.os.environ["D365_E2E_ALLOW_HOST"] == "preset.example.com"


def test_onprem_target_skips_host_derivation(tmp_path, monkeypatch):
    monkeypatch.delenv("D365_E2E_ALLOW_HOST", raising=False)

    def fake_host(profile_name):
        raise AssertionError("on-prem target needs no cloud allow-host")

    run(target="onprem", out_dir=tmp_path, host_fn=fake_host,
        run_set_fn=lambda **k: _set_result(), run_both_fn=lambda *a, **k: None)


# --- artifacts written (AC4) ---------------------------------------------------------

def test_writes_result_json_and_run_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("D365_E2E_ALLOW_HOST", "x")

    def fake_set(*, active_target, repeat, agent_cmd, progress):
        progress(  # one line to run.log
            __import__("evals.skill.set_runner", fromlist=["ProgressEvent"]).ProgressEvent(
                done=1, total=1, task_id="t", target="cloud", status=PASS, runnable=1)
        )
        return _set_result()

    rc = run(target="cloud", out_dir=tmp_path, run_set_fn=fake_set,
             run_both_fn=lambda *a, **k: None)
    assert rc == 0
    assert (tmp_path / "result.json").exists()
    log = (tmp_path / "run.log").read_text()
    assert "[ 1/1] PASS  t  (cloud)" in log  # progress captured to run.log
    out = capsys.readouterr().out
    assert "result.json" in out and "run.log" in out  # paths printed
