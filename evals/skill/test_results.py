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
    hake_gain,
    is_reportable,
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
