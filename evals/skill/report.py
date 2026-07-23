"""Reporting surfaces over paired-run records — ``report.md`` + ``matrix.md`` (#893, ADR 0028).

Two committed markdown artifacts derived from the ``evals/results/`` records that
:mod:`evals.skill.results` writes:

- :func:`build_report` — the per-run ``report.md`` for a *reportable* run: metadata, a
  per-task table with per-trial verdicts, the macro pass rates + Hake gain, the
  invocation-vs-success split (ADR 0028 measures invocation separately from success), the
  comparison against the same-series baseline, and the flipped-task list.
- :func:`build_matrix` — the derived ``matrix.md``: the **latest reportable run per series**
  (model × target × k). Absolute pass rates are only comparable *within* a model/target;
  the cross-series (cross-harness) comparable number is the **lift over the run's own bare
  baseline**, so that is the column carried across series.

Everything here is pure (records in, markdown str out) so the whole reporting path is
unit-testable offline; :func:`collect_reportable` reads committed ``run.json`` files, so the
matrix is derived purely from the committed records (never from the untracked transcripts).
The commit policy lives here too: :data:`ARTIFACT_NAMES` / :func:`artifact_paths` are the
*only* paths a reportable run stages, so transcripts and ``run.log`` stay untracked because
they are never named (ADR 0028's explicit-path rule).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from evals.skill.regression import RegressionReport, macro_pass_rate
from evals.skill.results import TaskAggregate, TrialRecord, iter_run_records, series_key

#: The only files a reportable run commits (force-added over ``.gitignore``). Transcripts
#: and ``run.log`` are absent by construction, so ``git add -f`` can never stage them.
ARTIFACT_NAMES = ("run.json", "trials.jsonl", "report.md")

#: matrix.md is force-add-free (``.gitignore`` already excepts it), so it is not in
#: ARTIFACT_NAMES; it is committed by name alongside the per-run artifacts.
MATRIX_NAME = "matrix.md"

_VERDICT = {"pass": "✓", "fail": "✗", "capped": "⊘"}


def artifact_paths(run_dir: str | Path) -> list[Path]:
    """The explicit artifact paths a reportable run commits (ADR 0028's allow-list)."""
    root = Path(run_dir)
    return [root / name for name in ARTIFACT_NAMES]


def _verdict(t: TrialRecord) -> str:
    return _VERDICT["capped"] if t.capped else _VERDICT["pass" if t.passed else "fail"]


def _leg_verdicts(trials: list[TrialRecord], task_id: str, leg: str) -> str:
    ordered = sorted(
        (t for t in trials if t.task_id == task_id and t.leg == leg), key=lambda t: t.trial
    )
    return "".join(_verdict(t) for t in ordered) or "—"


class InvocationSplit(NamedTuple):
    """The skill leg's (invoked?) × (passed?) contingency table, plus uncaptured trials."""

    invoked_passed: int = 0
    invoked_failed: int = 0
    not_invoked_passed: int = 0
    not_invoked_failed: int = 0
    not_captured: int = 0


def invocation_split(trials: list[TrialRecord]) -> InvocationSplit:
    """Split the **skill-leg** trials by (invoked?) × (passed?) — the ADR-0028 decoupling.

    Only the skill leg can invoke the skill (the bare leg has none installed), so only it is
    counted. A trial whose ``invoked`` is ``None`` (signal not captured) lands in
    ``not_captured`` rather than being guessed either way.
    """
    inv_pass = inv_fail = noinv_pass = noinv_fail = uncaptured = 0
    for t in trials:
        if t.leg != "skill":
            continue
        if t.invoked is None:
            uncaptured += 1
        elif t.invoked:
            inv_pass += t.passed
            inv_fail += not t.passed
        else:
            noinv_pass += t.passed
            noinv_fail += not t.passed
    return InvocationSplit(inv_pass, inv_fail, noinv_pass, noinv_fail, uncaptured)


def _mean_hake(gains: list[float | None]) -> float | None:
    defined = [g for g in gains if g is not None]
    return sum(defined) / len(defined) if defined else None


def build_report(
    *,
    run_id: str,
    meta: dict[str, Any],
    aggregates: list[TaskAggregate],
    trials: list[TrialRecord],
    regression: RegressionReport,
) -> str:
    """Render the per-run ``report.md`` (all ADR-0028 sections). Pure: no disk, no org."""
    # ``regression.current_macro`` is this run's with-skill macro (mean of pass_skill_rate);
    # reuse it so the Macro and Regression sections can never disagree.
    skill_macro = regression.current_macro
    bare_macro = macro_pass_rate([a.pass_bare_rate for a in aggregates])
    mean_gain = _mean_hake([a.hake_gain for a in aggregates])
    split = invocation_split(trials)

    lines: list[str] = [f"# skill-eval report — {run_id}", ""]

    lines += ["## Metadata", ""]
    # `host` is deliberately excluded — report.md is committed to a public repo and the live
    # org host (esp. on-prem/internal) is sensitive; the series identity is model × target × k.
    for key in ("model", "target", "k", "preset", "paired", "subset", "skill_sha"):
        if key in meta:
            lines.append(f"- **{key}**: {meta[key]}")
    lines += ["- **reportable**: true", ""]

    lines += [
        "## Per-task results",
        "",
        "Per-trial verdicts in order (✓ pass · ✗ fail · ⊘ cap-hit).",
        "",
        "| task | skill trials | bare trials | skill % | bare % | Hake gain |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for a in aggregates:
        gain = "N/A" if a.hake_gain is None else f"{a.hake_gain:+.2f}"
        lines.append(
            f"| {a.task_id} | {_leg_verdicts(trials, a.task_id, 'skill')} "
            f"| {_leg_verdicts(trials, a.task_id, 'bare')} "
            f"| {a.pass_skill_rate:.0%} | {a.pass_bare_rate:.0%} | {gain} |"
        )
    lines.append("")

    gain_str = "N/A" if mean_gain is None else f"{mean_gain:+.2f}"
    lines += [
        "## Macro",
        "",
        f"- **with-skill macro pass rate**: {skill_macro:.0%}",
        f"- **bare macro pass rate**: {bare_macro:.0%}",
        f"- **macro lift**: {(skill_macro - bare_macro) * 100:+.1f} pp",
        f"- **mean Hake gain** (tasks with headroom): {gain_str}",
        "",
    ]

    lines += [
        "## Invocation vs success (skill leg)",
        "",
        "Whether the agent loaded the `crm` skill, measured separately from whether it passed.",
        "",
        "| | passed | failed |",
        "| --- | --- | --- |",
        f"| invoked | {split.invoked_passed} | {split.invoked_failed} |",
        f"| not invoked | {split.not_invoked_passed} | {split.not_invoked_failed} |",
    ]
    if split.not_captured:
        lines.append(
            f"\nInvocation signal not captured for {split.not_captured} skill-leg trial(s)."
        )
    lines.append("")

    lines += ["## Regression vs baseline", ""]
    if regression.baseline_run_id is None:
        lines.append("- no reportable baseline for this series yet (advisory)")
    else:
        verdict = "⚠ FLAGGED" if regression.flagged else "ok"
        drop = regression.macro_drop_pp or 0.0
        base = regression.baseline_macro or 0.0
        lines.append(f"- baseline: `{regression.baseline_run_id}` [{verdict}, advisory]")
        lines.append(
            f"- with-skill macro {regression.current_macro:.0%} vs baseline {base:.0%} "
            f"(drop {drop:+.1f} pp)"
        )
    lines.append("")

    lines += ["## Flipped tasks (all-pass → all-fail vs baseline)", ""]
    if regression.flipped_tasks:
        lines += [f"- {tid}" for tid in regression.flipped_tasks]
    else:
        lines.append("- none")
    lines.append("")

    return "\n".join(lines)


# ── matrix.md — derived purely from committed run.json records ──────────────────


def collect_reportable(results_root: str | Path) -> list[dict[str, Any]]:
    """Every committed run record stamped ``reportable`` under ``results_root`` (newest first).

    Reads only the committed records via :func:`evals.skill.results.iter_run_records` (never
    the transcripts); a malformed/unreadable ``run.json`` is skipped. ``[]`` if the root is absent.
    """
    reportable = (d for d in iter_run_records(results_root) if d.get("reportable"))
    return sorted(reportable, key=lambda d: str(d.get("run_id", "")), reverse=True)


def latest_reportable_by_series(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest reportable run per series (model × target × k), sorted by series.

    A non-reportable run is dropped; within a series the newest ``run_id`` (its UTC-timestamp
    prefix sorts lexicographically) wins.
    """
    newest: dict[tuple[str, str, int], dict[str, Any]] = {}
    for run in runs:
        if not run.get("reportable"):
            continue
        key = series_key(run.get("meta", {}))
        cur = newest.get(key)
        if cur is None or str(run.get("run_id", "")) > str(cur.get("run_id", "")):
            newest[key] = run
    return [newest[k] for k in sorted(newest)]


def build_matrix(runs: list[dict[str, Any]]) -> str:
    """Render ``matrix.md`` from committed run records: latest reportable run per series.

    Within a model/target the with-skill/bare macro pass rates are shown; the number carried
    **across** series (harnesses) is the **lift over the run's own bare baseline** — absolute
    pass rates are not comparable across harnesses, but lift-vs-own-baseline is.
    """
    latest = latest_reportable_by_series(runs)
    lines = [
        "# skill-eval matrix",
        "",
        "Latest **reportable** run per series (model × target × k), derived from the committed "
        "`run.json` records. Absolute pass rates compare only *within* a model/target; the "
        "cross-series comparable metric is **lift over the run's own bare baseline**.",
        "",
        "| model | target | k | run | tasks | skill % | bare % | lift (pp) | mean Hake |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for run in latest:
        meta = run.get("meta", {})
        aggs = run.get("aggregates", [])
        skill_macro = macro_pass_rate([float(a["pass_skill_rate"]) for a in aggs])
        bare_macro = macro_pass_rate([float(a["pass_bare_rate"]) for a in aggs])
        mean_gain = _mean_hake([a.get("hake_gain") for a in aggs])
        gain_str = "N/A" if mean_gain is None else f"{mean_gain:+.2f}"
        lines.append(
            f"| {meta.get('model')} | {meta.get('target')} | {meta.get('k')} "
            f"| `{run.get('run_id')}` | {len(aggs)} | {skill_macro:.0%} | {bare_macro:.0%} "
            f"| {(skill_macro - bare_macro) * 100:+.1f} | {gain_str} |"
        )
    if not latest:
        lines.append("| _(no reportable runs yet)_ | | | | | | | | |")
    lines.append("")
    return "\n".join(lines)
