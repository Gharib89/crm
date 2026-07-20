"""Run presets + task selection for the paired skill-eval (#892, ADR 0028 §Cadence).

A *preset* fixes two of a run's three knobs — which **conditions** (paired vs
with-skill-only) and which **corpus slice** — leaving ``k`` an independent flag
(default 1). ADR 0028 defines three:

- **full** — the whole corpus, **paired** (both legs). The only kind that can be
  *reportable*, and only then at k≥3 — that k gate lives in
  :func:`evals.skill.results.is_reportable`, not here, so a full run at k=1 is legal
  but simply not reportable.
- **smoke** — a ~8-task slice, **with-skill only** (bare leg skipped): a fast sanity run.
- **regression-check** — the whole corpus, **with-skill only**, compared against the
  newest reportable baseline of the same series (see :mod:`evals.skill.regression`).

Task selection refines the preset's corpus and is **reproducible**: ``--tasks a,b``
picks exactly those ids (returned in stable corpus order; an unknown id is an error),
and ``--sample N`` takes a **seeded** random N-subset (same seed → same subset). Both
override the preset's own slice.
"""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path

from evals.skill.set_runner import TASKS_DIR, discover_tasks

#: Fixed seed so a bare ``--sample N`` (and the smoke slice) is reproducible run-to-run.
DEFAULT_SEED = 0


@dataclasses.dataclass(frozen=True)
class Preset:
    """A run preset: its conditions (``paired``?) and default corpus slice.

    ``sample`` is the preset's built-in slice size (smoke's ~8); ``None`` means the whole
    corpus. ``k`` is deliberately *not* a preset field — it is an independent flag whose
    default (1) and reportable floor (≥3 for ``full``) are enforced elsewhere.
    """

    name: str
    paired: bool
    sample: int | None


PRESETS: dict[str, Preset] = {
    "full": Preset("full", paired=True, sample=None),
    "smoke": Preset("smoke", paired=False, sample=8),
    "regression-check": Preset("regression-check", paired=False, sample=None),
}


def _task_id(task_file: Path) -> str:
    """A task's id is its spec filename stem (matches the ids stamped into run records)."""
    return task_file.stem


def resolve_tasks(
    preset: str,
    tasks_dir: str | Path = TASKS_DIR,
    *,
    only: list[str] | None = None,
    sample: int | None = None,
    seed: int = DEFAULT_SEED,
) -> list[Path]:
    """The ordered task-spec files a run executes, per ``preset`` + selection overrides.

    Precedence, highest first: an explicit ``only`` id list (exactly those; an unknown id
    raises ``ValueError``); else a sample size — explicit ``sample`` overriding the
    preset's built-in slice — taken as a **seeded** N-subset; else the whole corpus. The
    result is always in corpus (sorted) order so the run order is stable regardless of how
    the subset was picked. An unknown ``preset`` raises ``ValueError``.
    """
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    corpus = discover_tasks(tasks_dir)
    by_id = {_task_id(p): p for p in corpus}

    if only is not None:
        unknown = [i for i in only if i not in by_id]
        if unknown:
            raise ValueError(f"unknown task id(s): {', '.join(unknown)}")
        chosen = set(only)
        return [p for p in corpus if _task_id(p) in chosen]

    size = sample if sample is not None else PRESETS[preset].sample
    if size is not None and size < len(corpus):
        rng = random.Random(seed)
        picked = set(rng.sample([_task_id(p) for p in corpus], size))
        return [p for p in corpus if _task_id(p) in picked]
    return corpus
