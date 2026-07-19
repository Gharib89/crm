"""Paired-run results — metrics, reportability, and the ``evals/results/`` layout (ADR 0028).

Where :mod:`evals.skill.record` persists a *single-condition* run for the post-hoc
efficacy review (ADR 0016), this owns the **paired** result of the walking skeleton
(#890): the two legs of a task (with-skill and bare) reduced to a pass-rate delta and a
**Hake normalized gain**, written under ``evals/results/<run-id>/`` as a ``run.json``
(metadata + aggregates + a computed ``reportable`` stamp) plus a ``trials.jsonl`` (one
line per leg-trial). Transcripts are **referenced by path, never inlined** — they carry
live-org GUIDs and stay untracked (see the ADR's explicit-path commit rule).

The metric functions are pure so they are unit-testable without a live org; the writer
takes already-built trial/aggregate records, so the same aggregation the live harness
uses is exercised offline against hand-built trials.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Literal

#: Where every paired run's dir lands (``<repo>/evals/results/<run-id>/``). Ignored by
#: ``.gitignore`` except ``matrix.md``; reportable runs commit explicit paths (ADR 0028).
RESULTS_ROOT = Path(__file__).parent.parent / "results"

Leg = Literal["skill", "bare"]

#: A run is *reportable* (a quotable lift number, the baseline-lookup population) only
#: when it is a full paired run at k≥3 — the ADR's single gate on what counts as signal.
REPORTABLE_MIN_K = 3


def hake_gain(pass_skill: float, pass_bare: float) -> float | None:
    """Hake normalized gain ``g = (pass_skill − pass_bare) / (1 − pass_bare)`` (ADR 0028).

    Returns ``None`` when ``pass_bare == 1`` — no headroom, so the gain is undefined and
    the value is excluded from any mean rather than counted as a misleading 0. A negative
    gain (skill made it worse) is a real signal and is returned as-is, not clamped.
    """
    headroom = 1.0 - pass_bare
    if headroom <= 0:
        return None
    return (pass_skill - pass_bare) / headroom


def is_reportable(*, preset: str, paired: bool, k: int) -> bool:
    """Whether a run counts as reportable: a full, paired run at k≥3 (ADR 0028).

    A single-condition run (smoke/regression, bare leg skipped) has no lift and a k<3
    run is too noisy to quote, so neither is reportable — the walking-skeleton default
    (k=1) is deliberately *not* reportable.
    """
    return preset == "full" and paired and k >= REPORTABLE_MIN_K


@dataclasses.dataclass
class TrialRecord:
    """One leg-trial: the atomic row of ``trials.jsonl``.

    ``capped`` marks a turn/wall-clock cap-hit — a distinct outcome that scores as a
    fail (``passed`` is also False). ``transcript_ref`` is a run-dir-relative path to the
    captured transcript, never the transcript body (it carries live-org GUIDs).
    """

    task_id: str
    leg: Leg
    trial: int
    passed: bool
    reason: str
    capped: bool
    metrics: dict[str, Any] = dataclasses.field(default_factory=dict)
    transcript_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class TaskAggregate:
    """A task's paired outcome across k trials: per-leg pass-rates and the Hake gain."""

    task_id: str
    k: int
    passes_skill: int
    passes_bare: int
    pass_skill_rate: float
    pass_bare_rate: float
    hake_gain: float | None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def aggregate_task(task_id: str, trials: list[TrialRecord]) -> TaskAggregate:
    """Reduce a task's trial rows to per-leg pass-rates and the Hake gain.

    ``k`` is the number of trials on the *skill* leg (both legs run k times in a paired
    run); a cap-hit trial is already ``passed=False`` so it needs no special-casing here.
    """
    skill = [t for t in trials if t.task_id == task_id and t.leg == "skill"]
    bare = [t for t in trials if t.task_id == task_id and t.leg == "bare"]
    k = len(skill)
    passes_skill = sum(1 for t in skill if t.passed)
    passes_bare = sum(1 for t in bare if t.passed)
    skill_rate = passes_skill / k if k else 0.0
    bare_rate = passes_bare / len(bare) if bare else 0.0
    return TaskAggregate(
        task_id=task_id,
        k=k,
        passes_skill=passes_skill,
        passes_bare=passes_bare,
        pass_skill_rate=skill_rate,
        pass_bare_rate=bare_rate,
        hake_gain=hake_gain(skill_rate, bare_rate),
    )


def write_results(
    results_root: str | Path,
    *,
    run_id: str,
    meta: dict[str, Any],
    trials: list[TrialRecord],
    aggregates: list[TaskAggregate],
) -> Path:
    """Write ``<results_root>/<run-id>/{run.json,trials.jsonl}``; return the run dir.

    ``run.json`` bundles the run metadata, the per-task aggregates, and a computed
    ``reportable`` stamp (from ``meta``'s ``preset``/``paired``/``k``). ``trials.jsonl``
    is one JSON object per leg-trial. The dir is created if absent.
    """
    run_dir = Path(results_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    reportable = is_reportable(
        preset=meta.get("preset", ""),
        # Default False: a run not explicitly marked paired is treated as non-reportable, so
        # a future single-condition (smoke/regression) caller that forgets the flag can't be
        # silently misclassified as reportable.
        paired=bool(meta.get("paired", False)),
        k=int(meta.get("k", 1)),
    )
    run_json = {
        "run_id": run_id,
        "meta": meta,
        "reportable": reportable,
        "aggregates": [a.to_dict() for a in aggregates],
    }
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2), encoding="utf-8")

    with (run_dir / "trials.jsonl").open("w", encoding="utf-8") as fh:
        for t in trials:
            fh.write(json.dumps(t.to_dict()) + "\n")
    return run_dir
