"""Offline tests for baseline lookup + advisory regression detection (#892, ADR 0028).

Regression is advisory: a run's with-skill numbers are compared to the newest reportable
baseline of the **same series** (model × target × k) and drops are *flagged for a human*,
never gated. These pin the baseline scan (reportable + series filter, newest wins) and the
two flags (>5 pp macro drop; k/k→0/k per-task flip) against hand-built run records — no
live org.

    pytest evals/skill/test_regression.py
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.skill.regression import detect_regression, find_baseline, macro_pass_rate
from evals.skill.results import TaskAggregate


def _agg(task_id: str, *, k: int, passes_skill: int) -> TaskAggregate:
    rate = passes_skill / k if k else 0.0
    return TaskAggregate(
        task_id=task_id,
        k=k,
        passes_skill=passes_skill,
        passes_bare=0,
        pass_skill_rate=rate,
        pass_bare_rate=0.0,
        hake_gain=None,
    )


def _write_run(
    root: Path,
    run_id: str,
    *,
    reportable: bool,
    model: str = "sonnet",
    target: str = "cloud",
    k: int = 3,
    aggregates: list[TaskAggregate] | None = None,
) -> None:
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": run_id,
        "meta": {"model": model, "target": target, "k": k},
        "reportable": reportable,
        "aggregates": [a.to_dict() for a in (aggregates or [])],
    }
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")


def test_macro_pass_rate_empty_is_zero():
    assert macro_pass_rate([]) == 0.0


def test_macro_pass_rate_is_mean_over_tasks():
    assert macro_pass_rate([1.0, 0.5]) == 0.75


def test_find_baseline_none_when_root_missing(tmp_path):
    assert find_baseline(tmp_path / "nope", model="sonnet", target="cloud", k=3) is None


def test_find_baseline_ignores_non_reportable(tmp_path):
    _write_run(tmp_path, "20260101T000000Z-aaaa", reportable=False)
    assert find_baseline(tmp_path, model="sonnet", target="cloud", k=3) is None


def test_find_baseline_matches_series_exactly(tmp_path):
    _write_run(tmp_path, "20260101T000000Z-aaaa", reportable=True, model="opus")
    _write_run(tmp_path, "20260101T000000Z-bbbb", reportable=True, target="onprem")
    _write_run(tmp_path, "20260101T000000Z-cccc", reportable=True, k=5)
    assert find_baseline(tmp_path, model="sonnet", target="cloud", k=3) is None


def test_find_baseline_returns_newest_of_series(tmp_path):
    _write_run(tmp_path, "20260101T000000Z-old0", reportable=True)
    _write_run(tmp_path, "20260615T120000Z-new0", reportable=True)
    _write_run(tmp_path, "20260301T000000Z-mid0", reportable=True)
    baseline = find_baseline(tmp_path, model="sonnet", target="cloud", k=3)
    assert baseline is not None
    assert baseline["run_id"] == "20260615T120000Z-new0"


def test_no_baseline_flags_nothing(tmp_path):
    current = [_agg("a", k=3, passes_skill=3)]
    report = detect_regression(current, None, k=3)
    assert report.flagged is False
    assert report.baseline_run_id is None
    assert report.current_macro == 1.0


def test_macro_drop_over_five_pp_flags(tmp_path):
    """Baseline is 100%; the current run is 66.7%, a 33pp drop."""
    _write_run(
        tmp_path,
        "20260101T000000Z-base",
        reportable=True,
        aggregates=[_agg("a", k=3, passes_skill=3), _agg("b", k=3, passes_skill=3)],
    )
    baseline = find_baseline(tmp_path, model="sonnet", target="cloud", k=3)
    current = [_agg("a", k=3, passes_skill=3), _agg("b", k=3, passes_skill=1)]
    report = detect_regression(current, baseline, k=3)
    assert report.macro_flag is True
    assert report.flagged is True
    assert report.macro_drop_pp is not None and report.macro_drop_pp > 5.0


def test_macro_drop_within_five_pp_does_not_flag(tmp_path):
    """Baseline is 100% over 10 tasks; one task drops to 19/20, so macro is
    (9*1.0 + 0.95)/10 = 0.995, a 0.5pp drop.
    """
    _write_run(
        tmp_path,
        "20260101T000000Z-base",
        reportable=True,
        aggregates=[_agg(f"t{i}", k=20, passes_skill=20) for i in range(10)],
    )
    baseline = find_baseline(tmp_path, model="sonnet", target="cloud", k=20)
    current = [_agg(f"t{i}", k=20, passes_skill=20) for i in range(9)] + [
        _agg("t9", k=20, passes_skill=19)
    ]
    report = detect_regression(current, baseline, k=20)
    assert report.macro_flag is False
    assert report.flagged is False


def test_all_pass_to_all_fail_flip_flags(tmp_path):
    """Task "a" flips k/k → 0/k while "b" holds. Macro drops 50pp too, but the flip alone
    must flag.
    """
    _write_run(
        tmp_path,
        "20260101T000000Z-base",
        reportable=True,
        aggregates=[_agg("a", k=3, passes_skill=3), _agg("b", k=3, passes_skill=3)],
    )
    baseline = find_baseline(tmp_path, model="sonnet", target="cloud", k=3)
    current = [_agg("a", k=3, passes_skill=0), _agg("b", k=3, passes_skill=3)]
    report = detect_regression(current, baseline, k=3)
    assert report.flipped_tasks == ["a"]
    assert report.flagged is True


def test_partial_drop_is_not_a_flip(tmp_path):
    _write_run(
        tmp_path,
        "20260101T000000Z-base",
        reportable=True,
        aggregates=[_agg("a", k=3, passes_skill=3)],
    )
    baseline = find_baseline(tmp_path, model="sonnet", target="cloud", k=3)
    current = [_agg("a", k=3, passes_skill=1)]
    report = detect_regression(current, baseline, k=3)
    assert report.flipped_tasks == []


def test_flip_ignores_tasks_absent_from_baseline(tmp_path):
    """A task absent from the baseline ("new") is all-fail but has no baseline all-pass to
    flip *from*, so it is never a flip. The macro rate still drops from the extra failing
    task; that is the macro flag's job, not the flip's.
    """
    _write_run(
        tmp_path,
        "20260101T000000Z-base",
        reportable=True,
        aggregates=[_agg("a", k=3, passes_skill=3)],
    )
    baseline = find_baseline(tmp_path, model="sonnet", target="cloud", k=3)
    current = [_agg("a", k=3, passes_skill=3), _agg("new", k=3, passes_skill=0)]
    report = detect_regression(current, baseline, k=3)
    assert report.flipped_tasks == []
