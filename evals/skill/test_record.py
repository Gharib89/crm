"""Offline tests for the durable run-record persistence (issue #588, ADR 0016).

A run record is the per-task artifact every run writes under
``evals/skill/runs/<UTC-ts>/`` so the skill-efficacy ``review`` step can judge it
later with no agent and no live org. These tests cover the record shape, the
on-disk layout (incl. the counterfactual leg's separate file), discovery of the
latest run, and the skill-SHA provenance stamp — all offline.

    pytest evals/skill
"""
from __future__ import annotations

import json
import re

from evals.skill import record
from evals.skill.runner import RunResult
from evals.skill.taskspec import TaskSpec

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _spec(task_id: str = "records-create-verify") -> TaskSpec:
    return TaskSpec(
        id=task_id, domain="records", target="cloud", prompt="Create a contact.",
        query=["query", "odata", "contacts"], expect={"count": 1}, cleanup=[],
    )


def _result(transcript: str) -> RunResult:
    return RunResult(
        task_id="records-create-verify", dry_run=False, isolation_checks={},
        passed=True, reason="all expectations met", transcript=transcript,
    )


_TRACE = "\n".join([
    json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "crm entity create contacts ..."}}]}}),
    json.dumps({"type": "result", "num_turns": 3, "total_cost_usd": 0.04, "duration_ms": 900}),
])


def test_record_round_trips_through_dict():
    rec = record.TaskRunRecord(
        task_id="t", prompt="p", raw_trace="r", commands=["crm whoami"],
        metrics={"num_turns": 2}, correctness_verdict={"passed": True, "reason": "", "status": "pass"},
        skill_sha="abc",
    )
    back = record.TaskRunRecord.from_dict(rec.to_dict())
    assert back == rec
    assert back.efficacy_review is None and back.counterfactual is False


def test_build_record_parses_commands_and_metrics_from_transcript():
    rec = record.build_record(_spec(), _result(_TRACE), "pass", "deadbeef")
    assert rec.task_id == "records-create-verify"
    assert rec.prompt == "Create a contact."
    assert rec.commands == ["crm entity create contacts ..."]
    assert rec.metrics == {"num_turns": 3, "total_cost_usd": 0.04, "duration_ms": 900}
    assert rec.correctness_verdict == {"passed": True, "reason": "all expectations met", "status": "pass"}
    assert rec.skill_sha == "deadbeef"
    assert rec.counterfactual is False


def test_write_and_load_round_trip(tmp_path):
    rec = record.build_record(_spec(), _result(_TRACE), "pass", "sha1")
    path = record.write_record(tmp_path, rec)
    assert path.name == "records-create-verify.json"
    loaded = record.load_records(tmp_path)
    assert len(loaded) == 1 and loaded[0].commands == rec.commands


def test_counterfactual_leg_writes_a_separate_file(tmp_path):
    # The skill-present and skill-absent legs of one task must not collide on disk.
    present = record.build_record(_spec(), _result(_TRACE), "pass", "sha1")
    absent = record.build_record(_spec(), _result(_TRACE), "pass", "sha1", counterfactual=True)
    record.write_record(tmp_path, present)
    cf_path = record.write_record(tmp_path, absent)
    assert cf_path.name == "records-create-verify.counterfactual.json"
    assert {p.name for p in tmp_path.glob("*.json")} == {
        "records-create-verify.json", "records-create-verify.counterfactual.json",
    }


def test_latest_run_dir_picks_newest_utc_stamp(tmp_path):
    for ts in ("20260601T000000Z", "20260626T120000Z", "20260610T000000Z"):
        (tmp_path / ts).mkdir()
    assert record.latest_run_dir(tmp_path).name == "20260626T120000Z"


def test_latest_run_dir_none_when_empty(tmp_path):
    assert record.latest_run_dir(tmp_path) is None
    assert record.latest_run_dir(tmp_path / "does-not-exist") is None


def test_skill_sha_is_the_git_tree_sha_of_the_skill():
    # In the repo, the skill tree has a stable git object SHA (changes only when the
    # skill changes) — the provenance stamp the review uses for run/review divergence.
    sha = record.skill_sha()
    assert _HEX40.match(sha), f"expected a 40-hex tree SHA, got {sha!r}"


def test_skill_sha_unknown_outside_a_repo(tmp_path):
    assert record.skill_sha(tmp_path) == "unknown"


def test_skill_sha_marks_dirty_on_uncommitted_skill_edits(tmp_path):
    # The committed tree SHA can't see uncommitted edits, yet the reviewer reads the skill
    # live — so a dirty skill tree gets a `-dirty` suffix to keep the provenance honest.
    import subprocess

    skills = tmp_path / "crm" / "skills"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("router", encoding="utf-8")
    git = ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "init"], check=True)

    clean = record.skill_sha(tmp_path)
    assert _HEX40.match(clean), f"committed tree SHA should be clean 40-hex, got {clean!r}"

    (skills / "SKILL.md").write_text("router edited", encoding="utf-8")  # uncommitted
    assert record.skill_sha(tmp_path) == f"{clean}-dirty"
