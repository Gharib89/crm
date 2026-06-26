"""Offline tests for the counterfactual (skill-absent) isolation leg (#588, ADR 0016).

The hybrid counterfactual measures lift by running a task a second time with the skill
*absent*: ``provision_isolation(install_skill=False)`` skips ``crm skill install`` and
``verify_isolation(expect_skill=False)`` flips check 5 to assert the skill is *not*
present. These prove that flip without an agent or a live org — provisioning a
skill-absent sandbox needs no ``crm`` binary at all.

    pytest evals/skill
"""
from __future__ import annotations

import pytest

from evals.skill import isolation


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
