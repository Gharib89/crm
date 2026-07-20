"""Baseline lookup + advisory regression detection for paired runs (#892, ADR 0028 §Regression).

Regression here is **advisory, never blocking**: a run's numbers are compared to a
baseline and any drop is *flagged for a human*, never gated. The baseline is the **newest
reportable run of the same series** (model × target × k) — a subset or k<3 run is
non-reportable (:func:`evals.skill.results.is_reportable`) and so is never a baseline. The
compared metric is the **with-skill macro pass rate** (the mean of per-task
``pass_skill_rate``), *not* lift. Two flags fire: the macro rate dropping **>5 pp** below
baseline, or any task flipping **all-pass→all-fail** (``k/k → 0/k`` at the run's configured
``k``, not a fixed 3).

The functions are pure (the scan reads ``run.json`` files, the detector takes already-built
aggregates), so the whole regression path is unit-testable without a live org.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from evals.skill.results import TaskAggregate

#: A with-skill macro pass-rate drop larger than this many percentage points is flagged.
MACRO_DROP_PP = 5.0


def macro_pass_rate(pass_skill_rates: list[float]) -> float:
    """Mean with-skill pass rate across tasks (``0.0`` for an empty set)."""
    if not pass_skill_rates:
        return 0.0
    return sum(pass_skill_rates) / len(pass_skill_rates)


def find_baseline(
    results_root: str | Path, *, model: str, target: str, k: int
) -> dict[str, Any] | None:
    """Newest reportable ``run.json`` of the same series (model × target × k), or ``None``.

    Scans ``<results_root>/*/run.json``, keeps only runs stamped ``reportable`` whose
    ``meta`` matches the series exactly, and returns the newest by ``run_id`` — the id's
    UTC-timestamp prefix sorts lexicographically. A subset or k<3 run is not reportable, so
    it is excluded here for free; a malformed/unreadable ``run.json`` is skipped. Returns
    the parsed dict, or ``None`` when the series has no reportable history yet.
    """
    root = Path(results_root)
    if not root.is_dir():
        return None
    matches: list[dict[str, Any]] = []
    for run_json in root.glob("*/run.json"):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not data.get("reportable"):
            continue
        meta = data.get("meta", {})
        if meta.get("model") == model and meta.get("target") == target and meta.get("k") == k:
            matches.append(data)
    if not matches:
        return None
    return max(matches, key=lambda d: str(d.get("run_id", "")))


@dataclasses.dataclass
class RegressionReport:
    """The advisory regression verdict of a run against its baseline.

    ``flagged`` is the human-facing headline (a macro drop or any flip); it is *advisory* —
    nothing in the harness gates on it. ``baseline_run_id`` is ``None`` when the series has
    no reportable baseline yet, in which case nothing is flagged.
    """

    baseline_run_id: str | None
    current_macro: float
    baseline_macro: float | None
    macro_drop_pp: float | None
    macro_flag: bool
    flipped_tasks: list[str]
    flagged: bool


def detect_regression(
    current: list[TaskAggregate],
    baseline: dict[str, Any] | None,
    *,
    k: int,
    drop_pp: float = MACRO_DROP_PP,
) -> RegressionReport:
    """Compare a run's with-skill results to its baseline; flag drops (advisory).

    Either flag sets ``flagged``: the with-skill macro pass rate dropping more than
    ``drop_pp`` percentage points below baseline, or any task present in **both** runs
    flipping ``k/k → 0/k`` (all-pass baseline → all-fail current) at the run's configured
    ``k``. With no baseline, nothing is flagged.
    """
    current_macro = macro_pass_rate([a.pass_skill_rate for a in current])
    if baseline is None:
        return RegressionReport(
            baseline_run_id=None,
            current_macro=current_macro,
            baseline_macro=None,
            macro_drop_pp=None,
            macro_flag=False,
            flipped_tasks=[],
            flagged=False,
        )
    baseline_aggs: list[dict[str, Any]] = baseline.get("aggregates", [])
    baseline_macro = macro_pass_rate([float(a["pass_skill_rate"]) for a in baseline_aggs])
    drop_pp_value = (baseline_macro - current_macro) * 100.0
    macro_flag = drop_pp_value > drop_pp
    # A flip is a task that went all-pass → all-fail *at the run's configured k* (``k/k → 0/k``,
    # not a fixed 3). ``find_baseline`` already matched the series on k, so the baseline's k
    # equals this ``k`` — the all-pass threshold is therefore ``passes_skill == k``.
    base_by_id = {a["task_id"]: a for a in baseline_aggs}
    flipped = [
        a.task_id
        for a in current
        if a.task_id in base_by_id
        and k > 0
        and int(base_by_id[a.task_id]["passes_skill"]) == k
        and a.passes_skill == 0
    ]
    return RegressionReport(
        baseline_run_id=str(baseline.get("run_id")) if baseline.get("run_id") else None,
        current_macro=current_macro,
        baseline_macro=baseline_macro,
        macro_drop_pp=drop_pp_value,
        macro_flag=macro_flag,
        flipped_tasks=flipped,
        flagged=macro_flag or bool(flipped),
    )
