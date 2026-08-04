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
from collections.abc import Iterator
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


def is_reportable(*, preset: str, paired: bool, k: int, subset: bool = False) -> bool:
    """Whether a run counts as reportable: a full, paired, whole-corpus run at k≥3 (ADR 0028).

    A single-condition run (smoke/regression, bare leg skipped) has no lift, a k<3 run is
    too noisy to quote, and a ``subset`` run (``--tasks``/``--sample`` narrowed the corpus)
    is not the "whole corpus" the ADR requires — so none is reportable. The walking-skeleton
    default (k=1) is deliberately *not* reportable.
    """
    return preset == "full" and paired and k >= REPORTABLE_MIN_K and not subset


def series_key(meta: dict[str, Any]) -> tuple[str, str, int]:
    """A run's *series* identity — ``(model, target, k)``.

    The unit baselines and the matrix group by (ADR 0028: per-target/per-model/per-k series
    are never merged).
    """
    return (str(meta.get("model")), str(meta.get("target")), int(meta.get("k", 0)))


def iter_run_records(results_root: str | Path) -> Iterator[dict[str, Any]]:
    """Yield each parsed ``<results_root>/*/run.json`` record; skip an unreadable/malformed one.

    The single reader of the on-disk run layout — baseline lookup (:mod:`regression`) and the
    matrix (:mod:`report`) both derive from this, so the ``run.json`` shape is scanned in one
    place. Nothing is yielded when the root is absent.
    """
    root = Path(results_root)
    if not root.is_dir():
        return
    for run_json in root.glob("*/run.json"):
        try:
            data = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            yield data


@dataclasses.dataclass
class TrialRecord:
    """One leg-trial: the atomic row of ``trials.jsonl``.

    ``capped`` marks a turn/wall-clock cap-hit — a distinct outcome that scores as a
    fail (``passed`` is also False). ``transcript_ref`` is a run-dir-relative path to the
    captured transcript, never the transcript body (it carries live-org GUIDs). ``invoked``
    is the ADR-0028 invocation signal (did the agent load the ``crm`` skill?), measured
    separately from ``passed``; ``None`` means *not captured* (never conflated with "did
    not invoke"), so a pre-invocation record reads honestly.

    ``judge`` is the **advisory L2** verdict (:mod:`evals.skill.judge`) — a blind qualitative
    read (clarification quality, elegance) recorded *alongside* L1, never mixed into it;
    ``None`` when no judge ran. It is deliberately **never read** by :func:`aggregate_task`,
    :func:`hake_gain`, :func:`is_reportable`, or the regression code, so lift stats and
    regression verdicts are provably unaffected by it (ADR 0028: the judge never gates).
    """

    task_id: str
    leg: Leg
    trial: int
    passed: bool
    reason: str
    capped: bool
    metrics: dict[str, Any] = dataclasses.field(default_factory=dict)
    transcript_ref: str = ""
    invoked: bool | None = None
    judge: dict[str, Any] | None = None

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


def stamp_run_start(results_root: str | Path, *, run_id: str, meta: dict[str, Any]) -> Path:
    """Create the run dir and stamp an **in-progress** ``run.json``; return the run dir.

    Written before the first trial so an interrupted run leaves a valid record:
    ``reportable`` is hard-``False`` (an in-progress/aborted run must never enter the
    matrix or be picked as a baseline — only the final :func:`write_results` computes the
    real stamp), ``in_progress: true`` marks it, and ``meta`` is the resume contract —
    ``--resume`` validates its flags against the stamp so two configurations can never
    silently mix in one run dir. An empty ``trials.jsonl`` is created for fresh runs but
    an existing one is left alone (the resume path prunes it instead).
    """
    run_dir = Path(results_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    run_json = {
        "run_id": run_id,
        "meta": meta,
        "reportable": False,
        "in_progress": True,
        "aggregates": [],
    }
    (run_dir / "run.json").write_text(json.dumps(run_json, indent=2), encoding="utf-8")
    (run_dir / "trials.jsonl").touch()
    return run_dir


def load_trials(run_dir: str | Path) -> list[TrialRecord]:
    """Read ``<run_dir>/trials.jsonl`` back into records (empty list if absent).

    The read half of the incremental-save loop: a resumed run loads the durable rows,
    decides which tasks are already complete (:func:`complete_task_ids`), and keeps only
    those tasks' rows. Rows are written by ``to_dict`` so they round-trip by field name.
    """
    path = Path(run_dir) / "trials.jsonl"
    if not path.exists():
        return []
    return [
        TrialRecord(**json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_trials(run_dir: str | Path, records: list[TrialRecord]) -> None:
    """Append ``records`` to ``<run_dir>/trials.jsonl`` — the per-task durable save.

    Called after each task finishes **all** its legs/trials, so the file only ever
    contains whole per-task blocks plus (at worst) the crash-interrupted tail of the
    final task — which :func:`complete_task_ids` filters out on resume.
    """
    with (Path(run_dir) / "trials.jsonl").open("a", encoding="utf-8") as fh:
        for t in records:
            fh.write(json.dumps(t.to_dict()) + "\n")


def rewrite_trials(run_dir: str | Path, records: list[TrialRecord]) -> None:
    """Replace ``<run_dir>/trials.jsonl`` with exactly ``records`` (the resume prune)."""
    path = Path(run_dir) / "trials.jsonl"
    path.write_text("", encoding="utf-8")
    append_trials(run_dir, records)


def complete_task_ids(trials: list[TrialRecord], *, k: int, paired: bool) -> set[str]:
    """Task ids with a **complete** trial block: k skill trials (+ k bare when paired).

    The resume gate: a task interrupted mid-legs has an asymmetric block (e.g. 3 skill /
    1 bare) that would bias its aggregate, so it is *not* complete and reruns whole —
    resume granularity is the task, matching the per-task :func:`append_trials` save.
    """
    complete: set[str] = set()
    for task_id in {t.task_id for t in trials}:
        skill = sum(1 for t in trials if t.task_id == task_id and t.leg == "skill")
        bare = sum(1 for t in trials if t.task_id == task_id and t.leg == "bare")
        if skill >= k and (not paired or bare >= k):
            complete.add(task_id)
    return complete


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
        # Same fail-safe default: a run that omits the flag is treated as a subset (never
        # reportable) rather than silently quotable.
        subset=bool(meta.get("subset", True)),
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
