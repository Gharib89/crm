"""Offline tests for the paired-run reporting surfaces (issue #893, ADR 0028 §Storage & reporting).

Pins the two committed markdown artifacts — the per-run ``report.md`` and the derived
cross-series ``matrix.md`` — plus the commit-path allow-list, all against hand-built run
records. No agent, no live org: the renderers are pure (dict/dataclass in, markdown str
out) and the matrix is derived purely from committed ``run.json`` files.

    pytest evals/skill/test_report.py
"""

from __future__ import annotations

import json

from evals.skill.regression import RegressionReport
from evals.skill.report import (
    ARTIFACT_NAMES,
    InvocationSplit,
    artifact_paths,
    build_matrix,
    build_report,
    collect_reportable,
    invocation_split,
    latest_reportable_by_series,
)
from evals.skill.results import TrialRecord, aggregate_task

_META = {
    "model": "sonnet",
    "target": "cloud",
    "host": "org.crm.dynamics.com",
    "k": 3,
    "preset": "full",
    "paired": True,
    "subset": False,
    "skill_sha": "abc123",
}


def _trials() -> list[TrialRecord]:
    # skill leg 2/3 (last fails, and that one never invoked the skill); bare leg 0/3.
    return [
        TrialRecord(
            "t1", leg="skill", trial=0, passed=True, reason="ok", capped=False, invoked=True
        ),
        TrialRecord(
            "t1", leg="skill", trial=1, passed=True, reason="ok", capped=False, invoked=True
        ),
        TrialRecord(
            "t1", leg="skill", trial=2, passed=False, reason="miss", capped=False, invoked=False
        ),
        TrialRecord("t1", leg="bare", trial=0, passed=False, reason="miss", capped=False),
        TrialRecord("t1", leg="bare", trial=1, passed=False, reason="miss", capped=False),
        TrialRecord("t1", leg="bare", trial=2, passed=False, reason="miss", capped=False),
    ]


def _no_baseline() -> RegressionReport:
    return RegressionReport(
        baseline_run_id=None,
        current_macro=2 / 3,
        baseline_macro=None,
        macro_drop_pp=None,
        macro_flag=False,
        flipped_tasks=[],
        flagged=False,
    )


# ── invocation_split ───────────────────────────────────────────────────────────


def test_invocation_split_counts_skill_leg_only():
    split = invocation_split(_trials())
    # only the skill leg counts; bare has no skill to invoke.
    assert split == InvocationSplit(invoked_passed=2, not_invoked_failed=1)


def test_invocation_split_counts_uncaptured():
    trials = [
        TrialRecord("t1", leg="skill", trial=0, passed=True, reason="", capped=False, invoked=None),
    ]
    assert invocation_split(trials).not_captured == 1


# ── build_report ─────────────────────────────────────────────────────────────


def test_build_report_has_all_sections():
    aggs = [aggregate_task("t1", _trials())]
    md = build_report(
        run_id="20260720T000000Z-aaaa",
        meta=_META,
        aggregates=aggs,
        trials=_trials(),
        regression=_no_baseline(),
    )
    # metadata
    assert "20260720T000000Z-aaaa" in md
    assert "sonnet" in md and "cloud" in md and "abc123" in md
    # per-task table with per-trial verdicts (pass=✓, fail=✗)
    assert "✓✓✗" in md  # skill leg trials in order
    assert "✗✗✗" in md  # bare leg trials
    # macro pass rates + Hake gain: skill 67%, bare 0%, gain (0.667-0)/1 = +0.67
    assert "67%" in md
    assert "+0.67" in md
    # invocation-vs-success split
    assert "nvocation" in md
    # comparison vs previous same-series baseline
    assert "aseline" in md and "no reportable baseline" in md.lower()
    # flipped-task list
    assert "lipped" in md
    # host is NOT rendered — report.md is committed to a public repo, so the live org host
    # (esp. on-prem/internal) must never leak, even when present in meta.
    assert "org.crm.dynamics.com" not in md


def test_build_report_marks_capped_trial():
    trials = [
        TrialRecord(
            "t1", leg="skill", trial=0, passed=False, reason="cap", capped=True, invoked=True
        ),
        TrialRecord("t1", leg="bare", trial=0, passed=False, reason="", capped=False),
    ]
    md = build_report(
        run_id="r1",
        meta=_META,
        aggregates=[aggregate_task("t1", trials)],
        trials=trials,
        regression=_no_baseline(),
    )
    assert "⊘" in md  # a cap-hit is a distinct verdict, not a plain fail


def test_build_report_renders_baseline_and_flips():
    trials = _trials()
    reg = RegressionReport(
        baseline_run_id="20260715T000000Z-base",
        current_macro=0.5,
        baseline_macro=1.0,
        macro_drop_pp=50.0,
        macro_flag=True,
        flipped_tasks=["t1"],
        flagged=True,
    )
    md = build_report(
        run_id="r2",
        meta=_META,
        aggregates=[aggregate_task("t1", trials)],
        trials=trials,
        regression=reg,
    )
    assert "20260715T000000Z-base" in md
    assert "t1" in md
    assert "FLAGGED" in md


# ── matrix (series + collect + render) ───────────────────────────────────────


def _run(run_id, *, reportable, model="sonnet", target="cloud", k=3, skill=3, bare=1):
    trials = [
        TrialRecord("t1", leg="skill", trial=i, passed=i < skill, reason="", capped=False)
        for i in range(3)
    ] + [
        TrialRecord("t1", leg="bare", trial=i, passed=i < bare, reason="", capped=False)
        for i in range(3)
    ]
    return {
        "run_id": run_id,
        "meta": {"model": model, "target": target, "k": k},
        "reportable": reportable,
        "aggregates": [aggregate_task("t1", trials).to_dict()],
    }


def test_latest_reportable_by_series_keeps_newest_and_drops_nonreportable():
    runs = [
        _run("20260101T000000Z-old0", reportable=True),
        _run("20260601T000000Z-new0", reportable=True),
        _run("20260701T000000Z-nrp0", reportable=False),  # newest but not reportable → dropped
        _run("20260301T000000Z-opus", reportable=True, model="opus"),
    ]
    latest = latest_reportable_by_series(runs)
    ids = {r["run_id"] for r in latest}
    assert "20260601T000000Z-new0" in ids  # newest of sonnet×cloud×3
    assert "20260101T000000Z-old0" not in ids  # superseded
    assert "20260701T000000Z-nrp0" not in ids  # non-reportable
    assert "20260301T000000Z-opus" in ids  # a distinct series survives


def test_collect_reportable_scans_run_json(tmp_path):
    for r in [
        _run("20260101T000000Z-aaaa", reportable=True),
        _run("20260101T000000Z-bbbb", reportable=False),
    ]:
        d = tmp_path / r["run_id"]
        d.mkdir()
        (d / "run.json").write_text(json.dumps(r), encoding="utf-8")
    got = collect_reportable(tmp_path)
    assert [r["run_id"] for r in got] == ["20260101T000000Z-aaaa"]


def test_collect_reportable_empty_when_root_missing(tmp_path):
    assert collect_reportable(tmp_path / "nope") == []


def test_build_matrix_renders_one_row_per_series():
    runs = [
        _run("20260601T000000Z-new0", reportable=True, skill=3, bare=1),
        _run("20260301T000000Z-opus", reportable=True, model="opus"),
    ]
    md = build_matrix(runs)
    assert "sonnet" in md and "opus" in md
    assert "cloud" in md
    # skill 3/3 = 100%, bare 1/3 = 33% → lift +66.7 pp is the cross-harness-comparable metric
    assert "100%" in md
    assert "matrix" in md.lower()


def test_build_matrix_empty_when_no_reportable_runs():
    md = build_matrix([])
    assert "matrix" in md.lower()  # still a valid document, just no rows


# ── commit policy ────────────────────────────────────────────────────────────


def test_artifact_paths_names_only_committed_artifacts(tmp_path):
    run_dir = tmp_path / "20260101T000000Z-aaaa"
    paths = artifact_paths(run_dir)
    names = [p.name for p in paths]
    assert names == ["run.json", "trials.jsonl", "report.md"]
    # transcripts + run.log are never named, so `git add -f <paths>` can never stage them.
    assert "run.log" not in names
    assert not any("transcript" in n for n in names)
    assert ARTIFACT_NAMES == ("run.json", "trials.jsonl", "report.md")
