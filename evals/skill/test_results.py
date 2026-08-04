"""Offline tests for the paired-run results model (issue #890, ADR 0028).

Pins the pure metric + reportability logic and the on-disk ``run.json`` / ``trials.jsonl``
schema — no agent, no live org. The live paired run that produces real trials is a
maintainer hand-back; here we drive the aggregation with hand-built trial records.

    pytest evals/skill/test_results.py
"""

from __future__ import annotations

import json

import pytest

from evals.skill.results import (
    TrialRecord,
    aggregate_task,
    append_trials,
    complete_task_ids,
    hake_gain,
    is_reportable,
    load_trials,
    rewrite_trials,
    stamp_run_start,
    write_results,
)


class TestHakeGain:
    def test_full_lift_from_zero_baseline(self):
        # skill turns a total failure into a total pass → maximal normalized gain.
        assert hake_gain(1.0, 0.0) == 1.0

    def test_partial_lift(self):
        # (0.75 − 0.25) / (1 − 0.25) = 0.5 / 0.75 (worked by hand, not by the formula).
        assert hake_gain(0.75, 0.25) == pytest.approx(2 / 3)

    def test_no_headroom_is_none(self):
        # pass_bare == 1 → denominator 0, gain undefined → N/A (excluded from any mean).
        assert hake_gain(1.0, 1.0) is None
        assert hake_gain(0.5, 1.0) is None

    def test_negative_gain_is_kept(self):
        # skill made it worse: a real, reportable signal, not clamped to zero.
        assert hake_gain(0.0, 0.5) == pytest.approx(-1.0)


class TestReportable:
    def test_full_paired_k3_is_reportable(self):
        assert is_reportable(preset="full", paired=True, k=3) is True

    def test_k_below_3_not_reportable(self):
        # the walking-skeleton default (k=1) is never a reportable, quotable run.
        assert is_reportable(preset="full", paired=True, k=1) is False

    def test_single_condition_not_reportable(self):
        # a bare-only smoke/regression run has no lift → never reportable.
        assert is_reportable(preset="smoke", paired=False, k=3) is False

    def test_subset_full_run_not_reportable(self):
        # a full paired k≥3 run narrowed by --tasks/--sample is not the whole corpus → not
        # reportable, so it can't pollute the baseline pool (#892).
        assert is_reportable(preset="full", paired=True, k=3, subset=True) is False

    def test_unsandboxed_run_not_reportable(self):
        # --no-sandbox is a wiring check with unrestricted agent egress — a full paired
        # k≥3 run under it must never be quotable or become a baseline.
        assert is_reportable(preset="full", paired=True, k=3, sandbox=False) is False


class TestAggregateTask:
    def test_rates_and_gain_over_k_trials(self):
        trials = [
            TrialRecord("t1", leg="skill", trial=0, passed=True, reason="", capped=False),
            TrialRecord("t1", leg="skill", trial=1, passed=True, reason="", capped=False),
            TrialRecord("t1", leg="bare", trial=0, passed=False, reason="", capped=False),
            TrialRecord("t1", leg="bare", trial=1, passed=True, reason="", capped=False),
        ]
        agg = aggregate_task("t1", trials)
        assert agg.pass_skill_rate == 1.0
        assert agg.pass_bare_rate == 0.5
        assert agg.hake_gain == pytest.approx(1.0)  # (1 − .5)/(1 − .5)

    def test_cap_hit_counts_as_fail(self):
        # a wall-clock/turn cap-hit is a distinct outcome that scores as a fail.
        trials = [
            TrialRecord("t1", leg="skill", trial=0, passed=False, reason="cap", capped=True),
            TrialRecord("t1", leg="bare", trial=0, passed=False, reason="", capped=False),
        ]
        agg = aggregate_task("t1", trials)
        assert agg.pass_skill_rate == 0.0
        # bare also 0 → headroom is full (1 − 0), so gain is defined and equals 0.0.
        assert agg.hake_gain == 0.0

    def test_gain_none_when_bare_perfect(self):
        trials = [
            TrialRecord("t1", leg="skill", trial=0, passed=True, reason="", capped=False),
            TrialRecord("t1", leg="bare", trial=0, passed=True, reason="", capped=False),
        ]
        agg = aggregate_task("t1", trials)
        assert agg.hake_gain is None


class TestWriteResults:
    def test_writes_run_json_and_trials_jsonl(self, tmp_path):
        trials = [
            TrialRecord(
                "t1",
                leg="skill",
                trial=0,
                passed=True,
                reason="ok",
                capped=False,
                metrics={"num_turns": 3},
                transcript_ref="transcripts/t1.skill.0.txt",
            ),
            TrialRecord("t1", leg="bare", trial=0, passed=False, reason="miss", capped=False),
        ]
        aggs = [aggregate_task("t1", trials)]
        run_dir = write_results(
            tmp_path,
            run_id="20260719T000000Z-abcd",
            meta={"model": "sonnet", "target": "cloud", "k": 1, "preset": "full"},
            trials=trials,
            aggregates=aggs,
        )
        run_json = json.loads((run_dir / "run.json").read_text())
        assert run_json["run_id"] == "20260719T000000Z-abcd"
        assert run_json["meta"]["model"] == "sonnet"
        assert run_json["reportable"] is False  # k=1
        assert run_json["aggregates"][0]["task_id"] == "t1"
        assert run_json["aggregates"][0]["hake_gain"] == 1.0

        lines = (run_dir / "trials.jsonl").read_text().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["leg"] == "skill"
        assert first["transcript_ref"] == "transcripts/t1.skill.0.txt"
        # the transcript body itself is referenced, never inlined into the record.
        assert "raw_trace" not in first and "transcript" not in first

    def test_invoked_round_trips_and_defaults_none(self, tmp_path):
        # The invocation signal (ADR 0028's runner contract: "invocation-signal-or-null")
        # rides in the trial record; it defaults to None (not captured) so a pre-invocation
        # record is never silently read as "did not invoke".
        trials = [
            TrialRecord(
                "t1", leg="skill", trial=0, passed=True, reason="", capped=False, invoked=True
            ),
            TrialRecord("t1", leg="bare", trial=0, passed=False, reason="", capped=False),
        ]
        run_dir = write_results(
            tmp_path, run_id="r1", meta={"preset": "full"}, trials=trials, aggregates=[]
        )
        rows = [json.loads(x) for x in (run_dir / "trials.jsonl").read_text().splitlines()]
        assert rows[0]["invoked"] is True
        assert rows[1]["invoked"] is None

    def test_reportable_defaults_false_when_paired_flag_omitted(self, tmp_path):
        # A full k=3 run whose meta forgets `paired` must NOT be misclassified reportable —
        # the conservative default protects a future single-condition caller.
        run_dir = write_results(
            tmp_path,
            run_id="r1",
            meta={"preset": "full", "k": 3},  # no "paired" key
            trials=[],
            aggregates=[],
        )
        assert json.loads((run_dir / "run.json").read_text())["reportable"] is False

    def test_reportable_defaults_false_when_subset_flag_omitted(self, tmp_path):
        # Same fail-safe: a full paired k=3 run whose meta forgets `subset` is treated as a
        # subset (non-reportable) rather than silently quotable (#892).
        run_dir = write_results(
            tmp_path,
            run_id="r1",
            meta={"preset": "full", "paired": True, "k": 3},  # no "subset" key
            trials=[],
            aggregates=[],
        )
        report = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert report["reportable"] is False

    def test_reportable_defaults_false_when_sandbox_flag_omitted(self, tmp_path):
        # Same fail-safe: a run whose meta does not positively record it was sandboxed is
        # treated as unsandboxed (non-reportable) rather than silently quotable.
        run_dir = write_results(
            tmp_path,
            run_id="r1",
            meta={"preset": "full", "paired": True, "k": 3, "subset": False},  # no "sandbox"
            trials=[],
            aggregates=[],
        )
        report = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert report["reportable"] is False

    def test_reportable_true_with_full_sandboxed_meta(self, tmp_path):
        run_dir = write_results(
            tmp_path,
            run_id="r1",
            meta={"preset": "full", "paired": True, "k": 3, "subset": False, "sandbox": True},
            trials=[],
            aggregates=[],
        )
        report = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert report["reportable"] is True


def _trial(task_id: str, leg: str, trial: int, passed: bool = True) -> TrialRecord:
    return TrialRecord(
        task_id=task_id, leg=leg, trial=trial, passed=passed, reason="", capped=False
    )


class TestIncrementalSaveAndResume:
    def test_append_then_load_round_trips(self, tmp_path):
        first = [_trial("a", "skill", 0), _trial("a", "bare", 0)]
        second = [_trial("b", "skill", 0, passed=False)]
        append_trials(tmp_path, first)
        append_trials(tmp_path, second)
        loaded = load_trials(tmp_path)
        assert loaded == first + second

    def test_load_missing_file_is_empty(self, tmp_path):
        assert load_trials(tmp_path) == []

    def test_load_skips_a_truncated_tail_line(self, tmp_path):
        # A mid-write crash can leave the final line truncated; load must skip it (the
        # task's block is then incomplete and reruns whole) rather than crash --resume.
        good = [_trial("a", "skill", 0)]
        append_trials(tmp_path, good)
        with (tmp_path / "trials.jsonl").open("a", encoding="utf-8") as fh:
            fh.write('{"task_id": "b", "leg": "sk')
        assert load_trials(tmp_path) == good

    def test_rewrite_prunes_to_exactly_the_given_rows(self, tmp_path):
        append_trials(tmp_path, [_trial("a", "skill", 0), _trial("b", "skill", 0)])
        kept = [_trial("a", "skill", 0)]
        rewrite_trials(tmp_path, kept)
        assert load_trials(tmp_path) == kept
        # atomic swap: the temp file must not survive
        assert not (tmp_path / "trials.jsonl.tmp").exists()

    def test_complete_task_ids_requires_full_blocks_both_legs(self):
        trials = [
            # a: complete at k=2 (2 skill + 2 bare)
            _trial("a", "skill", 0),
            _trial("a", "skill", 1),
            _trial("a", "bare", 0),
            _trial("a", "bare", 1),
            # b: interrupted mid-legs (2 skill, 1 bare) — must rerun whole
            _trial("b", "skill", 0),
            _trial("b", "skill", 1),
            _trial("b", "bare", 0),
        ]
        assert complete_task_ids(trials, k=2, paired=True) == {"a"}

    def test_complete_task_ids_unpaired_counts_skill_only(self):
        trials = [_trial("a", "skill", 0), _trial("a", "skill", 1)]
        assert complete_task_ids(trials, k=2, paired=False) == {"a"}
        assert complete_task_ids(trials, k=3, paired=False) == set()

    def test_failed_trials_still_count_toward_completeness(self):
        # Completeness is about block shape, not verdicts — a task that failed all its
        # trials is done and must NOT rerun on resume (that would be silent best-of-N).
        trials = [_trial("a", "skill", 0, passed=False), _trial("a", "bare", 0, passed=False)]
        assert complete_task_ids(trials, k=1, paired=True) == {"a"}

    def test_stamp_run_start_is_never_reportable_and_marks_in_progress(self, tmp_path):
        meta = {"model": "sonnet", "target": "cloud", "k": 3, "preset": "full", "paired": True}
        run_dir = stamp_run_start(tmp_path, run_id="r1", meta=meta)
        stamped = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        # Hard-False even though the meta shape (full/paired/k=3) would be reportable:
        # an interrupted run must never enter the matrix or be picked as a baseline.
        assert stamped["reportable"] is False
        assert stamped["in_progress"] is True
        assert stamped["meta"]["k"] == 3
        assert (run_dir / "trials.jsonl").exists()

    def test_stamp_run_start_leaves_existing_trials_alone(self, tmp_path):
        rows = [_trial("a", "skill", 0)]
        run_dir = tmp_path / "r1"
        run_dir.mkdir()
        append_trials(run_dir, rows)
        stamp_run_start(tmp_path, run_id="r1", meta={})
        assert load_trials(run_dir) == rows
