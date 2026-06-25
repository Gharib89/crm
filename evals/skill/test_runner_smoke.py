"""Smoke tests for the Machine B tracer harness.

These run offline — they parse the real task files and dry-run the runner *without
invoking an agent or touching a live org*. They are the harness's own regression
guard and the acceptance gate for issue #570's smoke-test criterion.

Not collected by the default suite (testpaths = crm/tests); run on demand:

    pytest evals/skill
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evals.skill import isolation, runner
from evals.skill.taskspec import evaluate_expect, parse_task_file

TASKS_DIR = Path(__file__).parent / "tasks"


def _task_files() -> list[Path]:
    return sorted(TASKS_DIR.glob("*.md"))


def test_at_least_one_task_exists():
    assert _task_files(), "no task specs found under tasks/"


@pytest.mark.parametrize("task_file", _task_files(), ids=lambda p: p.stem)
def test_task_file_parses(task_file: Path):
    spec = parse_task_file(task_file)
    assert spec.id
    assert spec.prompt.strip()
    assert spec.query  # non-empty argv for the scoring query
    assert spec.expect
    # cleanup steps are well-formed
    for step in spec.cleanup:
        assert step.entity and step.id_field and step.filter


def test_tracer_task_shape():
    spec = parse_task_file(TASKS_DIR / "records-create-verify.md")
    assert spec.id == "records-create-verify"
    assert spec.domain == "records"
    assert spec.target == "cloud"
    assert "EvalTracer570" in spec.prompt
    assert spec.expect["count"] == 1


def test_evaluate_expect_count_pass():
    ok, _ = evaluate_expect([{"firstname": "Tracer"}], {"count": 1})
    assert ok


def test_evaluate_expect_count_fail():
    ok, reason = evaluate_expect([], {"count": 1})
    assert not ok and "count" in reason


def test_evaluate_expect_row_match():
    data = [{"firstname": "Tracer", "lastname": "EvalTracer570"}]
    ok, _ = evaluate_expect(data, {"row": {"firstname": "Tracer"}})
    assert ok


def test_evaluate_expect_row_no_match():
    data = [{"firstname": "Someone"}]
    ok, reason = evaluate_expect(data, {"row": {"firstname": "Tracer"}})
    assert not ok and "row" in reason


def test_evaluate_expect_non_list_data():
    ok, reason = evaluate_expect({"not": "a list"}, {"count": 1})
    assert not ok and "list" in reason


def test_provision_and_verify_isolation():
    iso = isolation.provision_isolation()
    try:
        checks = isolation.verify_isolation(iso)
        # the skill landed in the fresh HOME, and no repo path leaks through
        assert (iso.skill_dir / "SKILL.md").is_file()
        assert "skill-installed" in checks
        assert "no-pythonpath" in checks
        assert iso.env["HOME"] == str(iso.home)
        assert isolation.repo_root() not in iso.work.resolve().parents
    finally:
        iso.cleanup()
    assert not iso.sandbox.exists()


def test_verify_isolation_detects_repo_leak():
    iso = isolation.provision_isolation()
    try:
        # Simulate a leak: a CLAUDE.md reachable from the agent's working dir.
        (iso.work / "CLAUDE.md").write_text("leaked project memory")
        with pytest.raises(isolation.IsolationError, match="repo markers"):
            isolation.verify_isolation(iso)
    finally:
        iso.cleanup()


def test_dry_run_proves_isolation_without_agent():
    result = runner.run_task(TASKS_DIR / "records-create-verify.md", dry_run=True)
    assert result.dry_run is True
    assert result.passed is None  # not scored on a dry run
    assert result.isolation_checks.get("skill-installed")
    assert result.transcript == ""  # no agent was invoked


def test_run_requires_agent_cmd_when_not_dry():
    # A real run needs an agent command; absent one, fail clearly before any live call.
    import os

    saved = os.environ.pop("CRM_EVAL_AGENT_CMD", None)
    try:
        with pytest.raises(runner.RunError, match="agent command"):
            runner.run_task(TASKS_DIR / "records-create-verify.md", agent_cmd=None)
    finally:
        if saved is not None:
            os.environ["CRM_EVAL_AGENT_CMD"] = saved
