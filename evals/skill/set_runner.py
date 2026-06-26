"""The set runner: run the whole task set against one target, report a pass-rate.

Where :mod:`evals.skill.runner` runs *one* task end-to-end, this runs the *set* —
every ``tasks/*.md`` spec — against a single configured target and reports a
per-task verdict plus the absolute pass-rate. It samples **executability**: does a
skill-only agent actually carry each workflow to its declared end state?

Target gating is the one piece of set-level logic the single-task runner has no
need for: a ``cloud``-gated task cannot run against an on-prem profile (and vice
versa), so on a live run those tasks are **skipped** (reported, not failed) rather
than erroring out of the seed step. The active target is resolved once, up front,
so a skipped task never pays for isolation. ``either`` tasks run on any target.
Running both targets and unioning the coverage is #573's job; this runs one.

A ``--dry-run`` runs every task's dry path (parse + prove isolation, no agent, no
live org) regardless of gate — the target is irrelevant when nothing live happens —
so it is the offline smoke path for the whole set.

On-demand invocation:

    D365_E2E_PROFILE=agent-cloud D365_E2E_ALLOW_HOST=<host> \\
        CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions' \\
        python -m evals.skill.set_runner            # full live run
    python -m evals.skill.set_runner --dry-run      # offline: parse + isolation only
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.skill import record as record_mod
from evals.skill import target as target_mod
from evals.skill.runner import RunError, RunResult, run_task
from evals.skill.taskspec import parse_task_file

TASKS_DIR = Path(__file__).parent / "tasks"

#: Per-task verdict values. PASS/FAIL are scored outcomes (they drive the
#: pass-rate); SKIP (gate mismatch), DRY (dry run, unscored), and ERROR (a harness
#: step failed — not a task-scoring failure) are reported but excluded from it.
PASS, FAIL, ERROR, SKIP, DRY = "pass", "fail", "error", "skip", "dry"


@dataclasses.dataclass
class ProgressEvent:
    """One live-progress tick emitted by :func:`run_set` as the set advances (#585).

    Progress is a **stderr-only** display concern, kept out of the stdout JSON
    contract: ``run_set`` constructs an event and hands it to an injected ``progress``
    callback so imported callers (the tests, and the both-targets runner) stay silent
    unless they opt in.

    A *resolution* tick (``status`` set) fires once per task when its verdict is final;
    ``done`` is that task's 1-based position in the full set (skips/dry/errors count
    immediately) and ``total`` is the set size. A *trial* tick (``status is None``)
    fires as each trial of a ``--repeat N`` task completes, carrying ``trial``/``trials``
    so the running index is visible before the task resolves.
    """

    done: int
    total: int
    task_id: str
    target: str
    status: str | None = None
    reason: str = ""
    trial: int | None = None
    trials: int | None = None
    #: Tasks the active target will actually score (not skipped/diagnostic) — the
    #: denominator for the rolling "of N runnable" tally. None when not computed.
    runnable: int | None = None


ProgressFn = Callable[[ProgressEvent], None]


class StderrProgress:
    """Render :class:`ProgressEvent`s as human-readable lines on a stream (default stderr).

    Holds the rolling per-target pass/fail tally and formats both the per-task line and
    (for the both-targets runner) the per-leg header. The stream is injectable so a test
    can capture output to a ``StringIO`` and the front door can tee it to a ``run.log``.
    Every line is flushed so progress streams under redirection rather than buffering.
    """

    def __init__(self, stream: Any = None) -> None:
        self._stream = stream if stream is not None else sys.stderr
        self._tally: dict[str, dict[str, int]] = {}

    def _emit(self, line: str) -> None:
        print(line, file=self._stream, flush=True)

    def __call__(self, ev: ProgressEvent) -> None:
        prefix = f"[{ev.done:2}/{ev.total}]"
        if ev.status is None:  # trial tick — running index, before the task resolves
            self._emit(f"{prefix} trial {ev.trial}/{ev.trials}  {ev.task_id} ({ev.target})")
            return
        line = f"{prefix} {ev.status.upper():5} {ev.task_id}  ({ev.target})"
        if ev.reason:
            line += f"  {ev.reason}"
        self._emit(line)
        if ev.status in (PASS, FAIL):
            tally = self._tally.setdefault(ev.target, {PASS: 0, FAIL: 0})
            tally[ev.status] += 1
            denom = f"  (of {ev.runnable} runnable)" if ev.runnable is not None else ""
            self._emit(f"{ev.target}: {tally[PASS]} pass / {tally[FAIL]} fail{denom}")

    def leg(
        self, *, target: str, profile: str, reachable: bool,
        runnable: int | None = None, reason: str | None = None,
    ) -> None:
        """A per-leg header for the both-targets runner (#585 AC4)."""
        if reachable:
            shown = runnable if runnable is not None else "?"
            self._emit(f"── {target} ({profile}) ──  reachable, {shown} runnable")
        else:
            self._emit(f"── {target} ({profile}) ──  SKIPPED — {reason}")


@dataclasses.dataclass
class TaskOutcome:
    """One task's verdict in a set run.

    ``trials``/``passes`` carry the variance-smoothing counts (#573): a task run
    ``--repeat N`` times records how many of its N trials passed, so a flaky task
    contributes its fraction to the pass-rate rather than a hard 0 or 1. For the
    single-run shape (``trials == 1``), ``passes`` is left ``None`` and derived from
    ``status`` in :meth:`__post_init__`, so every pre-#573 construction is unchanged.
    """

    task_id: str
    status: str
    target: str
    reason: str = ""
    trials: int = 1
    passes: int | None = None

    def __post_init__(self) -> None:
        if self.passes is None:
            self.passes = self.trials if self.status == PASS else 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def discover_tasks(tasks_dir: str | Path = TASKS_DIR) -> list[Path]:
    """All ``tasks/*.md`` spec files under ``tasks_dir``, sorted for stable order."""
    return sorted(Path(tasks_dir).glob("*.md"))


def should_skip(spec_target: str, active_target: str) -> bool:
    """True when a task gated for one target cannot run against the active one.

    ``either`` runs anywhere; a concrete gate runs only on the matching target.
    """
    return spec_target != "either" and spec_target != active_target


def runnable_count(tasks_dir: str | Path = TASKS_DIR, *, active_target: str) -> int:
    """How many tasks the active target will actually score (not skipped/diagnostic).

    The denominator for the progress "of N runnable" tally (#585) and the both-targets
    per-leg header. Resilient to a malformed spec — a parse error is reported as that
    task's ERROR at run time, so it is not counted runnable here.
    """
    n = 0
    for path in discover_tasks(tasks_dir):
        try:
            spec = parse_task_file(path)
        except Exception:  # noqa: BLE001 — a bad file isn't runnable; run_set reports it as ERROR
            continue
        if not spec.is_diagnostic and not should_skip(spec.target, active_target):
            n += 1
    return n


def pass_rate(outcomes: list[TaskOutcome]) -> float | None:
    """Absolute pass-rate = passing trials / total trials over *scored* tasks.

    Skipped, dry, and errored tasks are not scored, so they are excluded from both
    numerator and denominator. With the default single trial per task this is just
    passed / (passed + failed); under ``--repeat N`` each scored task contributes its
    ``passes`` out of ``trials``, so a flaky task is smoothed rather than counted as a
    hard pass or fail (#573). Returns ``None`` when nothing was scored (so a caller
    can render "n/a" rather than a misleading 0.0).
    """
    scored = [o for o in outcomes if o.status in (PASS, FAIL)]
    total = sum(o.trials for o in scored)
    if not total:
        return None
    return sum(o.passes or 0 for o in scored) / total


@dataclasses.dataclass
class SetResult:
    """Outcome of a whole-set run."""

    outcomes: list[TaskOutcome]
    active_target: str | None
    dry_run: bool

    @property
    def counts(self) -> dict[str, int]:
        c = {s: 0 for s in (PASS, FAIL, ERROR, SKIP, DRY)}
        for o in self.outcomes:
            c[o.status] = c.get(o.status, 0) + 1
        return c

    def pass_rate(self) -> float | None:
        return pass_rate(self.outcomes)

    def scored_fraction(self) -> tuple[int, int]:
        """``(passing_trials, total_trials)`` over scored tasks — the pass-rate as a
        raw fraction, so a caller can record "27/30" alongside the percentage (#573)."""
        scored = [o for o in self.outcomes if o.status in (PASS, FAIL)]
        return sum(o.passes or 0 for o in scored), sum(o.trials for o in scored)

    def to_dict(self) -> dict[str, Any]:
        rate = self.pass_rate()
        passes, trials = self.scored_fraction()
        return {
            "active_target": self.active_target,
            "dry_run": self.dry_run,
            "counts": self.counts,
            "pass_rate": rate,
            "scored_passes": passes,
            "scored_trials": trials,
            "outcomes": [o.to_dict() for o in self.outcomes],
        }

    def summary_lines(self) -> list[str]:
        """Human-readable per-task table plus the totals line."""
        width = max((len(o.task_id) for o in self.outcomes), default=4)
        lines = [f"{o.status.upper():5}  {o.task_id:<{width}}  {o.reason}" for o in self.outcomes]
        c = self.counts
        rate = self.pass_rate()
        rate_str = "n/a" if rate is None else f"{rate:.0%}"
        header = "dry run (isolation only)" if self.dry_run else f"target: {self.active_target}"
        lines.append("")
        lines.append(
            f"{header} — {len(self.outcomes)} task(s): "
            f"{c[PASS]} pass, {c[FAIL]} fail, {c[SKIP]} skip, "
            f"{c[ERROR]} error, {c[DRY]} dry  |  pass-rate {rate_str}"
        )
        return lines


def run_set(
    tasks_dir: str | Path = TASKS_DIR,
    *,
    dry_run: bool = False,
    agent_cmd: str | None = None,
    crm_bin: str | None = None,
    active_target: str | None = None,
    repeat: int = 1,
    run_one: Callable[..., RunResult] = run_task,
    progress: ProgressFn | None = None,
    run_dir: str | Path | None = None,
    counterfactual: bool = False,
    task_filter: str | None = None,
) -> SetResult:
    """Run every task in ``tasks_dir`` against one target; return a :class:`SetResult`.

    ``run_one`` is the single-task entry point (defaults to :func:`runner.run_task`);
    it is injectable so the offline smoke test can drive the aggregation without
    provisioning isolation. One task's failure never aborts the set — any harness-step
    exception is captured as that task's ``ERROR`` outcome.

    ``repeat`` (>= 1) runs each *scored* task that many times to smooth run-to-run
    variance (#573): the outcome records how many of its ``repeat`` trials passed, and
    is ``PASS`` only when every trial did. ``repeat`` does not apply to the dry path.

    ``progress`` (#585) is an optional callback invoked with a :class:`ProgressEvent`
    as each task resolves (and per trial under ``repeat``); it is **stderr-only display**
    and never touches the returned :class:`SetResult` or the stdout JSON. Left ``None``
    (the default for imported callers) nothing is emitted.

    ``run_dir`` (#588) is where each scored task's durable run record is written
    (``<run_dir>/<task-id>.json``) so the skill-efficacy ``review`` step can judge it
    later. Left ``None`` (every imported caller / test that doesn't opt in) nothing is
    persisted — the dir is created lazily on the first write. ``counterfactual`` adds a
    skill-absent leg per scored task (also gated on a task's own ``counterfactual``
    frontmatter) so the review can measure lift; ``task_filter`` restricts the run to a
    single task id.
    """
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")
    files = discover_tasks(tasks_dir)
    if not files:
        raise RunError(f"no task specs found under {tasks_dir}")

    resolved = active_target
    if not dry_run and resolved is None:
        resolved = target_mod.active_target()

    # Stamp the skill SHA once per run (provenance for the review); only when persisting.
    skill_sha = record_mod.skill_sha() if run_dir is not None else ""

    total = len(files)
    # Tally denominator for the progress display; computed once, only when progress is
    # on and a live run will actually score tasks (a dry run scores nothing).
    runnable = (
        runnable_count(tasks_dir, active_target=resolved or "")
        if progress is not None and not dry_run
        else None
    )

    def report(outcome: TaskOutcome, done: int) -> None:
        if progress is not None:
            # A scored task is displayed and tallied under the active leg (an "either"
            # task scored on cloud belongs to the cloud tally, not an "either" bucket);
            # a skipped/errored task keeps its own gate target, which its reason explains.
            shown = resolved if outcome.status in (PASS, FAIL) else outcome.target
            progress(ProgressEvent(
                done=done, total=total, task_id=outcome.task_id, target=shown or outcome.target,
                status=outcome.status, reason=outcome.reason, runnable=runnable,
            ))

    outcomes: list[TaskOutcome] = []
    for i, path in enumerate(files):
        done = i + 1
        # Parse inside the loop guard so a single malformed file is reported as that
        # task's ERROR rather than aborting the whole set.
        try:
            spec = parse_task_file(path)
        except Exception as exc:  # noqa: BLE001 — a bad file is one task's error, not a set abort
            o = TaskOutcome(path.stem, ERROR, "?", f"parse failed: {exc}")
            outcomes.append(o)
            report(o, done)
            continue

        # ``--task X`` (#588) restricts the run to one task; others produce no outcome.
        if task_filter is not None and task_filter not in (spec.id, path.stem):
            continue

        if dry_run:
            try:
                run_one(path, dry_run=True, agent_cmd=agent_cmd, crm_bin=crm_bin)
                o = TaskOutcome(spec.id, DRY, spec.target, "isolation verified")
            except Exception as exc:  # noqa: BLE001 — resilient: report, keep going
                o = TaskOutcome(spec.id, ERROR, spec.target, str(exc))
            outcomes.append(o)
            report(o, done)
            continue

        # Diagnostic tasks (#572) have no deterministic end-state predicate — they are
        # scored by the --analyze pass, not this set — so skip them rather than letting
        # the single-task runner's "diagnostic needs --analyze" guard surface as ERROR.
        if spec.is_diagnostic:
            o = TaskOutcome(spec.id, SKIP, spec.target,
                            "diagnostic: scored by the --analyze pass, not the set")
            outcomes.append(o)
            report(o, done)
            continue

        if should_skip(spec.target, resolved or ""):
            o = TaskOutcome(
                spec.id, SKIP, spec.target,
                f"requires {spec.target!r} target; active is {resolved!r}",
            )
            outcomes.append(o)
            report(o, done)
            continue

        last_result: RunResult | None = None
        try:
            passes = 0
            last_reason = ""
            for trial in range(repeat):
                result = run_one(path, dry_run=False, agent_cmd=agent_cmd, crm_bin=crm_bin)
                last_result = result
                passes += 1 if result.passed else 0
                last_reason = result.reason
                if progress is not None and repeat > 1:
                    progress(ProgressEvent(
                        done=done, total=total, task_id=spec.id, target=resolved or spec.target,
                        trial=trial + 1, trials=repeat, runnable=runnable,
                    ))
            status = PASS if passes == repeat else FAIL
            reason = last_reason if repeat == 1 else f"{passes}/{repeat} trials passed"
            o = TaskOutcome(spec.id, status, spec.target, reason, trials=repeat, passes=passes)
        except Exception as exc:  # noqa: BLE001 — resilient: one task's infra error
            o = TaskOutcome(spec.id, ERROR, spec.target, str(exc))
        outcomes.append(o)
        report(o, done)

        # Persist the durable run record (#588) for a scored task, plus — when measuring
        # lift — a skill-absent counterfactual leg. The absent leg is a *measurement*, not
        # a scored task: its failure is logged and never flips the outcome or aborts the set.
        if run_dir is not None and last_result is not None and o.status in (PASS, FAIL):
            record_mod.write_record(run_dir, record_mod.build_record(spec, last_result, o.status, skill_sha))
            if counterfactual or spec.counterfactual:
                try:
                    cf = run_one(path, dry_run=False, agent_cmd=agent_cmd, crm_bin=crm_bin,
                                 install_skill=False)
                    cf_status = PASS if cf.passed else FAIL
                    record_mod.write_record(
                        run_dir, record_mod.build_record(spec, cf, cf_status, skill_sha, counterfactual=True)
                    )
                except Exception as exc:  # noqa: BLE001 — measurement leg, never fatal
                    print(f"[counterfactual] {spec.id} skill-absent leg failed: {exc}", file=sys.stderr)

    return SetResult(outcomes=outcomes, active_target=resolved, dry_run=dry_run)


def want_progress(*, quiet: bool, progress: bool, isatty: bool) -> bool:
    """Resolve whether live progress is shown (#585): ``--quiet`` off > ``--progress``
    on > default (follow the stderr TTY — on at a terminal, off under redirect)."""
    if quiet:
        return False
    if progress:
        return True
    return isatty


def add_progress_flags(parser: argparse.ArgumentParser) -> None:
    """Add the mutually-exclusive ``--quiet`` / ``--progress`` gating flags (#585)."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--quiet", action="store_true",
                       help="suppress live progress even at a terminal")
    group.add_argument("--progress", action="store_true",
                       help="force live progress on even under redirect / non-TTY")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the whole skill-eval task set against one target.")
    parser.add_argument("--tasks-dir", default=str(TASKS_DIR), help="directory of tasks/*.md specs")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + prove isolation for every task; no agent, no live org")
    parser.add_argument("--agent-cmd", default=None, help="agent command (default: $CRM_EVAL_AGENT_CMD)")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="run each scored task N times and report the passing fraction (default 1)")
    add_progress_flags(parser)
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit the machine-readable result instead of the table")
    args = parser.parse_args(argv)

    # Live progress is stderr-only; a dry run scores nothing, so there is nothing to show.
    progress = (
        StderrProgress()
        if not args.dry_run
        and want_progress(quiet=args.quiet, progress=args.progress, isatty=sys.stderr.isatty())
        else None
    )
    result = run_set(args.tasks_dir, dry_run=args.dry_run, agent_cmd=args.agent_cmd,
                     repeat=args.repeat, progress=progress)
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("\n".join(result.summary_lines()))
    # Non-zero when any task failed its predicate or hit a harness error, so an
    # on-demand / CI-adjacent invocation surfaces a regression by exit code.
    return 1 if any(o.status in (FAIL, ERROR) for o in result.outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
