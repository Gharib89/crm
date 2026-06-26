"""`python -m evals.skill run` — a thin, hard-to-misuse front door (issue #585).

The harness is otherwise driven by two separate module entry points — one for a
single target (:mod:`evals.skill.set_runner`), one for both
(:mod:`evals.skill.both_runner`) — each needing several hand-set environment knobs
where a wrong value silently produces a garbage run (``claude -p`` without
``--dangerously-skip-permissions`` false-fails every task; the allow-host pasted by
hand; ``> result.json 2> run.log`` typed every time).

This wraps those runners with sane defaults: ``--target`` picks the runner and the
standing profile; the agent command defaults to headless Claude with the permission
gate disabled and ``sonnet`` (the harness measures the skill, not the model); the
cloud allow-host is derived from the resolved profile; and ``result.json`` + ``run.log``
are written automatically. It **composes** the existing runners — it does not
reimplement set/loop/baseline logic — and the existing entry points keep working.

    python -m evals.skill run --target onprem --model sonnet
    python -m evals.skill run --target both            # default both
    python -m evals.skill run --target cloud --repeat 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import date as _date
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.skill import both_runner, review, set_runner
from evals.skill import record as record_mod
from evals.skill import target as target_mod

#: The default model baked into the agent command. The harness measures the skill, not
#: the model, so the cheaper/faster tier is the baseline; ``--model`` swaps it.
DEFAULT_MODEL = "sonnet"

#: Single-target → standing profile. ``both`` loops both via the both-targets runner.
PROFILE_BY_TARGET = {"cloud": "agent-cloud", "onprem": "agent-on-prem"}


class FrontDoorError(RuntimeError):
    """Raised on a front-door usage error (e.g. ``--model`` combined with ``--agent-cmd``)."""


def build_agent_cmd(agent_cmd: str | None, model: str | None) -> str:
    """Resolve the agent command (#585 AC2).

    With no ``--agent-cmd``, default to headless Claude with the permission gate
    disabled and ``--model`` (or :data:`DEFAULT_MODEL`) — the gate footgun is gone by
    default. ``--agent-cmd`` overrides the whole command; combining it with ``--model``
    is rejected rather than silently appending a model to a custom command.

    The default emits a ``stream-json`` event stream (``--verbose`` is required for the
    full per-``tool_use`` stream under ``-p``), so the run record captures the ordered
    ``crm`` command sequence and run metrics the skill-efficacy review reads (#588). A
    custom ``--agent-cmd`` that drops these silently blinds the review.
    """
    if agent_cmd:
        if model:
            raise FrontDoorError(
                "--model cannot be combined with --agent-cmd; bake the model into --agent-cmd"
            )
        return agent_cmd
    return (
        "claude -p --dangerously-skip-permissions --output-format stream-json --verbose "
        f"--model {model or DEFAULT_MODEL}"
    )


class _Tee:
    """Write to several streams at once — used to capture progress to ``run.log`` while
    still showing it live on a terminal."""

    def __init__(self, *streams: Any) -> None:
        self._streams = [s for s in streams if s is not None]

    def write(self, s: str) -> int:
        for st in self._streams:
            st.write(s)
        return len(s)

    def flush(self) -> None:
        for st in self._streams:
            try:
                st.flush()
            except Exception:  # noqa: BLE001 — a flush failure on one stream must not abort the run
                pass


def run(
    *,
    target: str = "both",
    model: str | None = None,
    agent_cmd: str | None = None,
    repeat: int = 1,
    update_baseline: bool = False,
    counterfactual: bool = False,
    only_task: str | None = None,
    out_dir: str | Path | None = None,
    live: bool = False,
    runs_root: str | Path = record_mod.RUNS_ROOT,
    run_set_fn: Callable[..., Any] = set_runner.run_set,
    run_both_fn: Callable[..., Any] = both_runner.run_both,
    host_fn: Callable[[str], str] = target_mod.resolve_host,
) -> int:
    """Run the eval set for ``target`` with defaulted creds/artifacts; return an exit code.

    ``run_set_fn`` / ``run_both_fn`` / ``host_fn`` are injectable so the wiring is
    testable offline without an agent, a live org, or a real profile store.

    Every run persists a durable run dir under ``runs_root`` (``<runs_root>/<UTC-ts>/``,
    #588) so the skill-efficacy ``review`` step can judge it later; ``counterfactual``
    adds a skill-absent leg per task to measure lift, and ``only_task`` restricts the run
    to one task id. The dir is created lazily by the runner on the first record written.
    """
    if target not in ("cloud", "onprem", "both"):
        # run() is a public, importable seam (tested directly); keep a bad target a
        # readable FrontDoorError rather than a KeyError from the profile lookup below.
        raise FrontDoorError(f"--target must be cloud|onprem|both, got {target!r}")
    resolved_cmd = build_agent_cmd(agent_cmd, model)  # fail fast before any I/O
    if update_baseline and target != "both":
        # The baseline trend is one row per target across a both-targets run; a single
        # target has no trend to append. Reject rather than silently ignore the flag.
        raise FrontDoorError("--update-baseline applies only to --target both")

    out = Path(out_dir) if out_dir is not None else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / "result.json"
    log_path = out / "run.log"

    # The durable run dir is a fixed, timestamped location under the eval tree (not --out,
    # so `review` can find the latest run); created lazily by the runner on first write.
    run_dir = Path(runs_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Save/restore the env knobs we set so an in-process caller's environment is left
    # untouched, matching both_runner.run_both's discipline (a leaked D365_E2E_PROFILE
    # would mis-point a later call).
    saved = {k: os.environ.get(k) for k in (target_mod.E2E_PROFILE_ENV, "D365_E2E_ALLOW_HOST")}
    try:
        # Derive the cloud allow-host so the prod-host guard passes for the standing cloud
        # org without a hand-pasted env var. Only when cloud is in play and not preset.
        if target in ("cloud", "both") and not os.environ.get("D365_E2E_ALLOW_HOST"):
            os.environ["D365_E2E_ALLOW_HOST"] = host_fn(PROFILE_BY_TARGET["cloud"])

        with open(log_path, "w", encoding="utf-8") as logf:
            # run.log always captures progress; the live terminal copy is gated separately.
            tee = _Tee(logf, sys.stderr if live else None)
            reporter = set_runner.StderrProgress(stream=tee)
            if target == "both":
                res = run_both_fn(both_runner.DEFAULT_PROFILES, repeat=repeat,
                                  agent_cmd=resolved_cmd, progress=reporter, run_dir=run_dir,
                                  counterfactual=counterfactual, task_filter=only_task)
                if update_baseline:
                    both_runner.append_baseline(
                        both_runner.BASELINE, res.baseline_rows(today=_date.today().isoformat())
                    )
                payload = res.to_dict()
                exit_code = res.exit_code()
            else:
                # set_runner's per-task seeding reads D365_E2E_PROFILE; point it at the profile.
                os.environ[target_mod.E2E_PROFILE_ENV] = PROFILE_BY_TARGET[target]
                res = run_set_fn(active_target=target, repeat=repeat,
                                 agent_cmd=resolved_cmd, progress=reporter, run_dir=run_dir,
                                 counterfactual=counterfactual, task_filter=only_task)
                payload = res.to_dict()
                exit_code = (
                    1 if any(o.status in (set_runner.FAIL, set_runner.ERROR) for o in res.outcomes) else 0
                )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    result_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"result: {result_path.resolve()}")
    print(f"log:    {log_path.resolve()}")
    if run_dir.exists():  # lazily created only if at least one task was scored + persisted
        print(f"runs:   {run_dir.resolve()}  (review with `python -m evals.skill review`)")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.skill",
        description="Convenience front door for the skill-eval runners (#585).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="run the skill-eval set with sane defaults")
    run_p.add_argument("--target", choices=["cloud", "onprem", "both"], default="both",
                       help="which target(s) to run (default: both)")
    run_p.add_argument("--model", default=None,
                       help=f"Claude model for the default agent command (default: {DEFAULT_MODEL}); "
                            "not allowed together with --agent-cmd")
    run_p.add_argument("--agent-cmd", default=None,
                       help="full agent command override (for a non-Claude agent); --model is rejected with it")
    run_p.add_argument("--repeat", type=int, default=1, metavar="N",
                       help="run each scored task N times per target (variance smoothing; default 1)")
    run_p.add_argument("--update-baseline", action="store_true",
                       help="(--target both) append a dated per-target row to baseline.md")
    run_p.add_argument("--out", default=None, metavar="DIR",
                       help="directory for result.json + run.log (default: current dir)")
    run_p.add_argument("--counterfactual", action="store_true",
                       help="also run each task with the skill ABSENT so `review` can measure lift "
                            "(2x live cost per task)")
    run_p.add_argument("--task", default=None, metavar="ID",
                       help="run only the task with this id (default: the whole set)")
    set_runner.add_progress_flags(run_p)

    review_p = sub.add_parser(
        "review", help="judge a saved run's skill efficacy post-hoc (no agent, no live org)")
    review_p.add_argument("--run", default=None, metavar="DIR",
                          help="run dir to review (default: the latest under evals/skill/runs/)")
    review_p.add_argument("--task", default=None, metavar="ID",
                          help="review only this task id (default: all)")
    review_p.add_argument("--failed-only", action="store_true",
                          help="review only tasks whose correctness verdict was not a pass")
    review_p.add_argument("--record", action="store_true",
                          help="append the org-agnostic trend to the tracked efficacy.md (human gate)")
    review_p.add_argument("--review-cmd", default=None,
                          help="reviewer command (default: $CRM_EVAL_REVIEW_CMD, else 'claude -p --model opus')")
    args = parser.parse_args(argv)

    if args.cmd == "review":
        return review.run_review_cmd(
            run_dir=args.run, task=args.task, failed_only=args.failed_only,
            record_efficacy=args.record, review_cmd=args.review_cmd,
        )

    live = set_runner.want_progress(
        quiet=args.quiet, progress=args.progress, isatty=sys.stderr.isatty()
    )
    try:
        return run(
            target=args.target, model=args.model, agent_cmd=args.agent_cmd,
            repeat=args.repeat, update_baseline=args.update_baseline,
            counterfactual=args.counterfactual, only_task=args.task,
            out_dir=args.out, live=live,
        )
    except FrontDoorError as exc:
        parser.error(str(exc))  # prints usage + message, exits 2


if __name__ == "__main__":
    sys.exit(main())
