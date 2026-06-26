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
from pathlib import Path
from typing import Any

from evals.skill import both_runner, set_runner
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
    """
    if agent_cmd:
        if model:
            raise FrontDoorError(
                "--model cannot be combined with --agent-cmd; bake the model into --agent-cmd"
            )
        return agent_cmd
    return f"claude -p --dangerously-skip-permissions --model {model or DEFAULT_MODEL}"


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
    out_dir: str | Path | None = None,
    live: bool = False,
    run_set_fn: Callable[..., Any] = set_runner.run_set,
    run_both_fn: Callable[..., Any] = both_runner.run_both,
    host_fn: Callable[[str], str] = target_mod.resolve_host,
) -> int:
    """Run the eval set for ``target`` with defaulted creds/artifacts; return an exit code.

    ``run_set_fn`` / ``run_both_fn`` / ``host_fn`` are injectable so the wiring is
    testable offline without an agent, a live org, or a real profile store.
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
                                  agent_cmd=resolved_cmd, progress=reporter)
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
                                 agent_cmd=resolved_cmd, progress=reporter)
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
    set_runner.add_progress_flags(run_p)
    args = parser.parse_args(argv)  # subparser is required → args.cmd is always "run"

    live = set_runner.want_progress(
        quiet=args.quiet, progress=args.progress, isatty=sys.stderr.isatty()
    )
    try:
        return run(
            target=args.target, model=args.model, agent_cmd=args.agent_cmd,
            repeat=args.repeat, update_baseline=args.update_baseline,
            out_dir=args.out, live=live,
        )
    except FrontDoorError as exc:
        parser.error(str(exc))  # prints usage + message, exits 2


if __name__ == "__main__":
    sys.exit(main())
