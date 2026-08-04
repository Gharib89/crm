"""Offline tests for the counterfactual (skill-absent) isolation leg (#588, ADR 0016).

The hybrid counterfactual measures lift by running a task a second time with the skill
*absent*: ``provision_isolation(install_skill=False)`` skips ``crm skill install`` and
``verify_isolation(expect_skill=False)`` flips check 5 to assert the skill is *not*
present. These prove that flip without an agent or a live org — provisioning a
skill-absent sandbox needs no ``crm`` binary at all.

    pytest evals/skill
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from evals.skill import isolation


def test_real_claude_config_dir_honors_override(monkeypatch):
    # The credential passthrough reads the real config dir; an explicit override wins.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/custom/cfg")
    assert isolation._real_claude_config_dir() == Path("/custom/cfg")


def test_real_claude_config_dir_defaults_to_home(monkeypatch):
    # Rootless (#906): no override → the maintainer's own ~/.claude, no sudo/SUDO_UID dance.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert isolation._real_claude_config_dir() == Path.home() / ".claude"


def test_provision_skips_install_for_absent_leg():
    iso = isolation.provision_isolation(install_skill=False)
    try:
        # No skill was installed, and the sandbox is otherwise valid.
        assert not (iso.skill_dir / "SKILL.md").exists()
        checks = isolation.verify_isolation(iso, expect_skill=False)
        assert checks["skill-absent"]  # the flipped positive check
        assert "skill-installed" not in checks
    finally:
        iso.cleanup()


def test_absent_leg_fails_if_skill_is_actually_present():
    iso = isolation.provision_isolation(install_skill=False)
    try:
        # A skill leaking into the "absent" leg invalidates the lift measurement.
        iso.skill_dir.mkdir(parents=True, exist_ok=True)
        (iso.skill_dir / "SKILL.md").write_text("leaked", encoding="utf-8")
        with pytest.raises(isolation.IsolationError, match="skill"):
            isolation.verify_isolation(iso, expect_skill=False)
        # ...and with the default expectation the same sandbox now verifies as skill-present.
        checks = isolation.verify_isolation(iso, expect_skill=True)
        assert checks["skill-installed"].endswith("SKILL.md")
    finally:
        iso.cleanup()


def test_provision_puts_the_crm_bin_first_on_agent_path(tmp_path):
    # The agent invokes bare `crm`, so the binary under test must lead its PATH. Without
    # this, a host with no global crm sends the agent hunting the filesystem (where it can
    # find the repo checkout's editable install — an isolation leak), and a host WITH a
    # global crm silently swaps the binary under test away from the session wheel.
    fake = tmp_path / "venv" / "bin" / "crm"
    fake.parent.mkdir(parents=True)
    fake.touch()
    iso = isolation.provision_isolation(str(fake), install_skill=False)
    try:
        assert iso.env["PATH"].split(os.pathsep)[0] == str(fake.parent.resolve())
    finally:
        iso.cleanup()


def test_provision_without_crm_bin_leaves_path_alone():
    # The bin-less counterfactual provisioning has nothing to prepend — PATH is inherited.
    iso = isolation.provision_isolation(install_skill=False)
    try:
        assert iso.env["PATH"] == os.environ["PATH"]
    finally:
        iso.cleanup()
