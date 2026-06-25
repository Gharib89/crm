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
        CRM_EVAL_AGENT_CMD='claude -p' \\
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

from evals.skill import target as target_mod
from evals.skill.runner import RunError, RunResult, run_task
from evals.skill.taskspec import parse_task_file

TASKS_DIR = Path(__file__).parent / "tasks"

#: Per-task verdict values. PASS/FAIL are scored outcomes (they drive the
#: pass-rate); SKIP (gate mismatch), DRY (dry run, unscored), and ERROR (a harness
#: step failed — not a task-scoring failure) are reported but excluded from it.
PASS, FAIL, ERROR, SKIP, DRY = "pass", "fail", "error", "skip", "dry"


@dataclasses.dataclass
class TaskOutcome:
    """One task's verdict in a set run."""

    task_id: str
    status: str
    target: str
    reason: str = ""

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


def pass_rate(outcomes: list[TaskOutcome]) -> float | None:
    """Absolute pass-rate = passed / (passed + failed) over *scored* tasks.

    Skipped, dry, and errored tasks are not scored, so they are excluded from both
    numerator and denominator. Returns ``None`` when nothing was scored (so a
    caller can render "n/a" rather than a misleading 0.0).
    """
    scored = [o for o in outcomes if o.status in (PASS, FAIL)]
    if not scored:
        return None
    return sum(o.status == PASS for o in scored) / len(scored)


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

    def to_dict(self) -> dict[str, Any]:
        rate = self.pass_rate()
        return {
            "active_target": self.active_target,
            "dry_run": self.dry_run,
            "counts": self.counts,
            "pass_rate": rate,
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
    run_one: Callable[..., RunResult] = run_task,
) -> SetResult:
    """Run every task in ``tasks_dir`` against one target; return a :class:`SetResult`.

    ``run_one`` is the single-task entry point (defaults to :func:`runner.run_task`);
    it is injectable so the offline smoke test can drive the aggregation without
    provisioning isolation. One task's failure never aborts the set — any harness-step
    exception is captured as that task's ``ERROR`` outcome.
    """
    files = discover_tasks(tasks_dir)
    if not files:
        raise RunError(f"no task specs found under {tasks_dir}")

    resolved = active_target
    if not dry_run and resolved is None:
        resolved = target_mod.active_target()

    outcomes: list[TaskOutcome] = []
    for path in files:
        # Parse inside the loop guard so a single malformed file is reported as that
        # task's ERROR rather than aborting the whole set.
        try:
            spec = parse_task_file(path)
        except Exception as exc:  # noqa: BLE001 — a bad file is one task's error, not a set abort
            outcomes.append(TaskOutcome(path.stem, ERROR, "?", f"parse failed: {exc}"))
            continue

        if dry_run:
            try:
                run_one(path, dry_run=True, agent_cmd=agent_cmd, crm_bin=crm_bin)
                outcomes.append(TaskOutcome(spec.id, DRY, spec.target, "isolation verified"))
            except Exception as exc:  # noqa: BLE001 — resilient: report, keep going
                outcomes.append(TaskOutcome(spec.id, ERROR, spec.target, str(exc)))
            continue

        # Diagnostic tasks (#572) have no deterministic end-state predicate — they are
        # scored by the --analyze pass, not this set — so skip them rather than letting
        # the single-task runner's "diagnostic needs --analyze" guard surface as ERROR.
        if spec.is_diagnostic:
            outcomes.append(
                TaskOutcome(spec.id, SKIP, spec.target,
                            "diagnostic: scored by the --analyze pass, not the set")
            )
            continue

        if should_skip(spec.target, resolved or ""):
            outcomes.append(
                TaskOutcome(
                    spec.id, SKIP, spec.target,
                    f"requires {spec.target!r} target; active is {resolved!r}",
                )
            )
            continue

        try:
            result = run_one(path, dry_run=False, agent_cmd=agent_cmd, crm_bin=crm_bin)
            status = PASS if result.passed else FAIL
            outcomes.append(TaskOutcome(spec.id, status, spec.target, result.reason))
        except Exception as exc:  # noqa: BLE001 — resilient: one task's infra error
            outcomes.append(TaskOutcome(spec.id, ERROR, spec.target, str(exc)))

    return SetResult(outcomes=outcomes, active_target=resolved, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the whole skill-eval task set against one target.")
    parser.add_argument("--tasks-dir", default=str(TASKS_DIR), help="directory of tasks/*.md specs")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + prove isolation for every task; no agent, no live org")
    parser.add_argument("--agent-cmd", default=None, help="agent command (default: $CRM_EVAL_AGENT_CMD)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit the machine-readable result instead of the table")
    args = parser.parse_args(argv)

    result = run_set(args.tasks_dir, dry_run=args.dry_run, agent_cmd=args.agent_cmd)
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print("\n".join(result.summary_lines()))
    # Non-zero when any task failed its predicate or hit a harness error, so an
    # on-demand / CI-adjacent invocation surfaces a regression by exit code.
    return 1 if any(o.status in (FAIL, ERROR) for o in result.outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
