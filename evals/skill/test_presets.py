"""Offline tests for run presets + task selection (issue #892, ADR 0028 §Cadence).

A preset fixes a run's conditions (paired vs with-skill-only) and corpus slice; ``k``
stays an independent flag. Selection (``--tasks`` / seeded ``--sample``) refines the
slice and must be **reproducible**. These pin the registry values and the selection
precedence against a synthetic corpus, so no live org or real task specs are involved.

    pytest evals/skill/test_presets.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.skill.presets import PRESETS, resolve_tasks


def _corpus(tmp_path: Path, n: int) -> Path:
    """A synthetic tasks dir of ``n`` empty ``t00.md``…-style specs (never parsed here)."""
    for i in range(n):
        (tmp_path / f"t{i:02d}.md").write_text("", encoding="utf-8")
    return tmp_path


def _ids(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


def test_full_preset_is_paired_whole_corpus():
    assert PRESETS["full"].paired is True
    assert PRESETS["full"].sample is None


def test_smoke_preset_is_skill_only_and_slices():
    assert PRESETS["smoke"].paired is False
    assert PRESETS["smoke"].sample == 8


def test_regression_check_preset_is_skill_only_whole_corpus():
    assert PRESETS["regression-check"].paired is False
    assert PRESETS["regression-check"].sample is None


def test_full_selects_whole_corpus(tmp_path):
    tasks = resolve_tasks("full", _corpus(tmp_path, 12))
    assert len(tasks) == 12


def test_smoke_slices_to_eight(tmp_path):
    tasks = resolve_tasks("smoke", _corpus(tmp_path, 12))
    assert len(tasks) == 8


def test_smoke_slice_returns_whole_corpus_when_smaller_than_slice(tmp_path):
    # A corpus below the smoke slice size can't be oversampled — take all of it.
    tasks = resolve_tasks("smoke", _corpus(tmp_path, 5))
    assert len(tasks) == 5


def test_only_selects_exactly_those_ids_in_corpus_order(tmp_path):
    tasks = resolve_tasks("full", _corpus(tmp_path, 12), only=["t05", "t01"])
    # exactly those two, returned in stable corpus (sorted) order, not the arg order.
    assert _ids(tasks) == ["t01", "t05"]


def test_only_overrides_smoke_slice(tmp_path):
    # An explicit id list wins over the preset's built-in slice.
    tasks = resolve_tasks("smoke", _corpus(tmp_path, 12), only=["t03"])
    assert _ids(tasks) == ["t03"]


def test_unknown_task_id_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown task id"):
        resolve_tasks("full", _corpus(tmp_path, 12), only=["t03", "nope"])


def test_unknown_preset_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown preset"):
        resolve_tasks("bogus", _corpus(tmp_path, 12))


def test_sample_is_seeded_reproducible(tmp_path):
    corpus = _corpus(tmp_path, 12)
    first = resolve_tasks("full", corpus, sample=5, seed=7)
    second = resolve_tasks("full", corpus, sample=5, seed=7)
    assert len(first) == 5
    assert _ids(first) == _ids(second)  # same seed → identical subset


def test_sample_seed_changes_subset(tmp_path):
    corpus = _corpus(tmp_path, 12)
    a = _ids(resolve_tasks("full", corpus, sample=5, seed=1))
    b = _ids(resolve_tasks("full", corpus, sample=5, seed=2))
    assert a != b  # different seed → different subset (overwhelmingly likely at 5-of-12)


def test_explicit_sample_overrides_preset_slice(tmp_path):
    # --sample beats smoke's built-in 8.
    tasks = resolve_tasks("smoke", _corpus(tmp_path, 12), sample=3)
    assert len(tasks) == 3


def test_sample_returns_corpus_order(tmp_path):
    tasks = resolve_tasks("full", _corpus(tmp_path, 12), sample=5, seed=0)
    assert _ids(tasks) == sorted(_ids(tasks))  # run order stays stable regardless of pick order
