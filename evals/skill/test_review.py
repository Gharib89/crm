"""Offline tests for the skill-efficacy review (issue #588, ADR 0016).

The review reads saved run records (no agent, no live org), routes each to a reviewer
command (Claude) for a structured judgment, writes the judgment back, and emits a
report. These tests drive the prompt assembly, the structured-output parse, the
org-fingerprint guard on the tracked ``efficacy.md``, and the batch orchestration with
a **stub reviewer** — Claude is never invoked.

    pytest evals/skill
"""
from __future__ import annotations

import json

import pytest

from evals.skill import review
from evals.skill.record import TaskRunRecord


def _rec(task_id="records-create-verify", *, status="pass", passed=True,
         commands=None, counterfactual=False) -> TaskRunRecord:
    return TaskRunRecord(
        task_id=task_id, prompt=f"Do {task_id}.", raw_trace="...",
        commands=commands if commands is not None else ["crm whoami", "crm entity create contacts ..."],
        metrics={"num_turns": 5, "total_cost_usd": 0.1, "duration_ms": 1000},
        correctness_verdict={"passed": passed, "reason": "r", "status": status},
        skill_sha="abc", counterfactual=counterfactual,
    )


_GOOD_REVIEW = {
    "axes": {
        "goal_reached": {"grade": "good", "note": "reached the end state"},
        "command_economy": {"grade": "weak", "note": "one redundant whoami"},
        "skill_adherence": {"grade": "good", "note": "followed the create workflow"},
    },
    "skill_lift": "helped",
    "skill_fix": "none",
}


# --- reviewer command resolution -----------------------------------------------------

def test_default_review_cmd_is_opus():
    assert review.resolve_review_cmd() == ["claude", "-p", "--model", "opus"]


def test_review_cmd_env_and_arg_override(monkeypatch):
    monkeypatch.setenv("CRM_EVAL_REVIEW_CMD", "my-judge --x")
    assert review.resolve_review_cmd() == ["my-judge", "--x"]
    assert review.resolve_review_cmd("other -p") == ["other", "-p"]  # explicit arg wins


# --- skill read ----------------------------------------------------------------------

def test_read_skill_text_concatenates_skill_and_reference(tmp_path):
    (tmp_path / "SKILL.md").write_text("ROUTER BODY", encoding="utf-8")
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "query.md").write_text("QUERY REF", encoding="utf-8")
    text = review.read_skill_text(tmp_path)
    assert "ROUTER BODY" in text and "QUERY REF" in text
    assert text.index("ROUTER BODY") < text.index("QUERY REF")  # SKILL.md first


# --- prompt assembly -----------------------------------------------------------------

def test_build_review_prompt_carries_the_judgable_inputs():
    prompt = review.build_review_prompt(rec=_rec(), skill_text="SKILL TEXT HERE")
    for needle in ("Do records-create-verify.", "crm entity create contacts", "SKILL TEXT HERE",
                   "goal_reached", "command_economy", "skill_adherence", "skill_lift", "skill_fix",
                   "helped", "neutral", "hindered"):
        assert needle in prompt, f"prompt missing {needle!r}"


def test_build_review_prompt_includes_counterfactual_leg_when_present():
    cf = _rec(commands=["crm whoami", "crm help", "crm entity create ...", "crm query ..."],
              counterfactual=True)
    prompt = review.build_review_prompt(rec=_rec(), skill_text="S", counterfactual=cf)
    assert "skill-absent" in prompt.lower() or "counterfactual" in prompt.lower()
    assert "crm help" in prompt  # the absent leg's commands are shown for comparison


# --- structured-output parse ---------------------------------------------------------

def test_parse_review_extracts_plain_json():
    assert review.parse_review(json.dumps(_GOOD_REVIEW)) == _GOOD_REVIEW


def test_parse_review_extracts_fenced_json_with_prose():
    text = f"Here is my read.\n```json\n{json.dumps(_GOOD_REVIEW)}\n```\nThanks."
    assert review.parse_review(text)["skill_lift"] == "helped"


def test_parse_review_rejects_bad_grade():
    bad = json.loads(json.dumps(_GOOD_REVIEW))
    bad["axes"]["goal_reached"]["grade"] = "excellent"
    with pytest.raises(review.ReviewError):
        review.parse_review(json.dumps(bad))


def test_parse_review_rejects_bad_lift_and_missing_json():
    bad = json.loads(json.dumps(_GOOD_REVIEW))
    bad["skill_lift"] = "amazing"
    with pytest.raises(review.ReviewError):
        review.parse_review(json.dumps(bad))
    with pytest.raises(review.ReviewError):
        review.parse_review("no json here at all")


# --- org-fingerprint guard on the tracked efficacy.md --------------------------------

def test_guard_blocks_guids_and_fingerprint():
    with pytest.raises(review.ReviewError, match="GUID"):
        review.guard_org_agnostic("fix: account 3f2504e0-4f89-41d3-9a0c-0305e82c3301 needs a flag")
    with pytest.raises(review.ReviewError, match="00155d|fingerprint"):
        review.guard_org_agnostic("trace touched mac 00155d467b90")


def test_guard_passes_org_agnostic_text():
    review.guard_org_agnostic("Axis tallies: goal_reached good=3 weak=1. Fix: clarify the query verb.")


# --- report + efficacy block ---------------------------------------------------------

def test_build_report_tables_axes_and_clusters_fixes():
    recs = [
        _rec("a"), _rec("b"),
    ]
    recs[0].efficacy_review = dict(_GOOD_REVIEW)
    recs[1].efficacy_review = {**_GOOD_REVIEW, "skill_lift": "hindered",
                               "skill_fix": "document the --validate flag default"}
    report = review.build_report(recs)
    assert "| a |" in report and "| b |" in report
    assert "helped" in report and "hindered" in report
    assert "document the --validate flag default" in report  # fix clustered, "none" omitted


def test_efficacy_block_is_tallies_and_fixes_only():
    recs = [_rec("a"), _rec("b")]
    recs[0].efficacy_review = dict(_GOOD_REVIEW)
    recs[1].efficacy_review = {**_GOOD_REVIEW, "skill_lift": "hindered", "skill_fix": "add an example"}
    block = review.build_efficacy_block(recs, date="2026-06-26")
    assert "2026-06-26" in block
    assert "helped" in block and "hindered" in block  # lift tally
    assert "add an example" in block
    # guard must accept it (org-agnostic by construction)
    review.guard_org_agnostic(block)


# --- batch orchestration with a stub reviewer ----------------------------------------

def _stub_reviewer(captured: list[str]):
    def reviewer(prompt: str) -> str:
        captured.append(prompt)
        return json.dumps(_GOOD_REVIEW)
    return reviewer


def test_review_records_attaches_review_to_each_task():
    recs = [_rec("a"), _rec("b")]
    reviewed = review.review_records(recs, skill_text="S", reviewer=_stub_reviewer([]))
    assert len(reviewed) == 2
    assert all(r.efficacy_review and r.efficacy_review["skill_lift"] == "helped" for r in reviewed)


def test_review_records_task_filter():
    recs = [_rec("a"), _rec("b")]
    reviewed = review.review_records(recs, skill_text="S", reviewer=_stub_reviewer([]), task="b")
    assert [r.task_id for r in reviewed] == ["b"]


def test_review_records_failed_only_filter():
    recs = [_rec("a", status="pass", passed=True), _rec("b", status="fail", passed=False)]
    reviewed = review.review_records(recs, skill_text="S", reviewer=_stub_reviewer([]), failed_only=True)
    assert [r.task_id for r in reviewed] == ["b"]


def test_review_records_pairs_counterfactual_leg_into_prompt():
    present = _rec("a")
    absent = _rec("a", commands=["crm help", "crm help query"], counterfactual=True)
    captured: list[str] = []
    reviewed = review.review_records([present, absent], skill_text="S", reviewer=_stub_reviewer(captured))
    # only the skill-present leg is reviewed; the absent leg is folded into its prompt.
    assert [r.task_id for r in reviewed] == ["a"]
    assert len(captured) == 1 and "crm help query" in captured[0]


def test_review_records_unparseable_reviewer_is_recorded_not_raised():
    recs = [_rec("a")]
    reviewed = review.review_records(recs, skill_text="S", reviewer=lambda p: "garbage, no json")
    assert reviewed[0].efficacy_review["unparsed"] is True


# --- orchestration: writeback + report.md + efficacy.md ------------------------------

def test_run_review_cmd_writes_back_records_and_report(tmp_path):
    from evals.skill import record as record_mod
    run_dir = tmp_path / "20260626T000000Z"
    record_mod.write_record(run_dir, _rec("a"))
    record_mod.write_record(run_dir, _rec("b", status="fail", passed=False))

    rc = review.run_review_cmd(
        run_dir=run_dir, reviewer=_stub_reviewer([]), skill_reader=lambda d: "SKILL",
    )
    assert rc == 0
    # each record now carries its efficacy review on disk...
    reloaded = {r.task_id: r for r in record_mod.load_records(run_dir)}
    assert reloaded["a"].efficacy_review["skill_lift"] == "helped"
    # ...and a report.md was emitted into the run dir.
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "| a |" in report and "| b |" in report


def test_run_review_cmd_record_flag_appends_guarded_efficacy(tmp_path):
    from evals.skill import record as record_mod
    run_dir = tmp_path / "20260626T010000Z"
    record_mod.write_record(run_dir, _rec("a"))
    efficacy = tmp_path / "efficacy.md"
    efficacy.write_text("# Efficacy trend\n", encoding="utf-8")

    rc = review.run_review_cmd(
        run_dir=run_dir, reviewer=_stub_reviewer([]), skill_reader=lambda d: "SKILL",
        record_efficacy=True, efficacy_path=efficacy, today="2026-06-26",
    )
    assert rc == 0
    text = efficacy.read_text(encoding="utf-8")
    assert "# Efficacy trend" in text and "2026-06-26" in text  # appended, header preserved


def test_run_review_cmd_no_run_dir_is_an_error(tmp_path):
    # No run dir given and none on disk → a clean non-zero, not a crash.
    rc = review.run_review_cmd(
        runs_root=tmp_path / "empty", reviewer=_stub_reviewer([]), skill_reader=lambda d: "S",
    )
    assert rc != 0
