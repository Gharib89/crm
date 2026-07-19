"""Paired walking skeleton — one do-task, both legs, a real skill-lift number (#890, ADR 0028).

This is the Machine B end-to-end thin slice: take one hand-written do-task, run it
**with-skill** and **bare** against the same live org from an identical starting state,
and emit a lift number (pass-rate delta + Hake normalized gain). It composes the pieces
built around it — :mod:`isolation` (hermetic per-leg env), :mod:`sandbox` (OS-level
outbound-web block), :mod:`runner` (one leg + caps), :mod:`results` (metrics + the
``evals/results/`` layout) — and adds the two things a *pair* needs:

- **org reset between legs** (the attribution keystone): both legs must start from
  identical org state, so the org is reset before each leg — otherwise leg B inherits
  leg A's mutations and the delta is meaningless;
- **inline lift**: the two legs are reduced to a per-task Hake gain in one run, rather
  than the post-hoc review of the earlier single-condition runner.

Only :func:`run_pair` and :func:`agent_argv` are offline-testable (the legs and reset are
injected); the front door builds a session venv, writes the built-in Bash-sandbox settings
into each leg's config dir, and drives a real ``claude -p`` against the live org — the
maintainer's hand-back run. It needs **no root**: the sandbox confines the agent's ``Bash``
to the org host while the model driver keeps normal network (see :mod:`evals.skill.sandbox`).

    D365_E2E=1 D365_E2E_PROFILE=agent-cloud python -m evals.skill.paired
"""

from __future__ import annotations

import argparse
import atexit
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from evals.skill import record as record_mod
from evals.skill import target as target_mod
from evals.skill import trace
from evals.skill.results import RESULTS_ROOT, TrialRecord, aggregate_task, write_results
from evals.skill.runner import RunError, RunResult, cleanup_org, run_task
from evals.skill.sandbox import sandbox_settings
from evals.skill.taskspec import parse_task_file

#: The hand-written do-task the skeleton drives (a mutation-light, cloud do-task with a
#: deterministic end-state + cleanup). Overridable with ``--task``.
DEFAULT_TASK = Path(__file__).parent / "tasks" / "records-create-verify.md"
DEFAULT_MODEL = "sonnet"
#: Guardrails pinned by ADR 0028: the only tools the agent may use, and the turn cap.
ALLOWED_TOOLS = "Bash,Read,Grep,Glob,Skill"
MAX_TURNS = 50
#: Per-trial wall-clock cap (seconds) — 10 minutes; a cap-hit scores as a fail.
WALL_CLOCK_S = 600


def agent_argv(
    *, model: str = DEFAULT_MODEL, max_turns: int = MAX_TURNS, claude_bin: str = "claude"
) -> list[str]:
    """The headless ``claude -p`` argv with the ADR-0028 guardrails baked in.

    ``claude_bin`` is argv[0] (defaults to ``"claude"`` on ``PATH`` — the rootless run needs
    no PATH gymnastics). ``--allowedTools`` denies the web tools (only Bash/Read/Grep/Glob/
    Skill), ``--max-turns`` is the turn cap, and ``stream-json`` is the trace :mod:`trace`
    parses for the command sequence + metrics. ``--dangerously-skip-permissions`` turns off
    the permission gate but is orthogonal to the Bash sandbox, whose enforcement stays on.
    The wall-clock cap is enforced by the runner, not a flag.
    """
    return [
        claude_bin,
        "-p",
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
        "--allowedTools",
        ALLOWED_TOOLS,
        "--max-turns",
        str(max_turns),
        "--model",
        model,
    ]


def _transcript_ref(transcripts_dir: Path, task_id: str, leg: str, trial: int, body: str) -> str:
    """Write a leg's transcript to ``transcripts/`` and return the run-dir-relative ref.

    The transcript carries live-org GUIDs, so the record references it by path and it
    stays untracked — never inlined into ``trials.jsonl`` (ADR 0028's explicit-path rule).
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    name = f"{task_id}.{leg}.{trial}.txt"
    (transcripts_dir / name).write_text(body, encoding="utf-8")
    return f"transcripts/{name}"


def run_pair(
    task_file: str | Path,
    *,
    reset_org: Callable[[], None],
    run_one: Callable[..., RunResult] = run_task,
    k: int = 1,
    transcripts_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    **leg_kwargs: object,
) -> list[TrialRecord]:
    """Run ``task_file`` with-skill and bare, k times each, resetting the org before each leg.

    ``run_one`` (default :func:`runner.run_task`) is injectable so the orchestration is
    testable without a live org; ``reset_org`` is called **before every leg** so both legs
    start from identical, reseeded state (the attribution keystone). ``leg_kwargs`` (agent
    command, crm bin, wall-clock, sandbox wrap) are forwarded verbatim and identically to
    both legs — the treatment differs by exactly the skill install. ``progress`` (if given)
    is called with a human line as each leg starts/resolves — the front door routes it to
    stderr so a long run (up to ``2·k`` agent trials) shows live movement. Returns the flat
    list of per-leg :class:`~evals.skill.results.TrialRecord`s.
    """
    trials: list[TrialRecord] = []
    for trial in range(k):
        for leg, install in (("skill", True), ("bare", False)):
            if progress is not None:
                progress(f"trial {trial + 1}/{k} · {leg} leg · resetting org + running agent…")
            reset_org()
            result = run_one(task_file, install_skill=install, dry_run=False, **leg_kwargs)
            if progress is not None:
                verdict = "capped" if result.capped else ("pass" if result.passed else "fail")
                progress(f"trial {trial + 1}/{k} · {leg} leg · {verdict}")
            ref = (
                _transcript_ref(transcripts_dir, result.task_id, leg, trial, result.transcript)
                if transcripts_dir is not None
                else ""
            )
            trials.append(
                TrialRecord(
                    task_id=result.task_id,
                    leg=leg,
                    trial=trial,
                    passed=bool(result.passed),
                    reason=result.reason,
                    capped=result.capped,
                    metrics=trace.parse_metrics(result.transcript),
                    transcript_ref=ref,
                )
            )
    return trials


# ─────────────────────── live front door (hand-back run) ───────────────────────


def _make_run_id() -> str:  # pragma: no cover - wall-clock/random, live only
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def build_session_crm(repo_root: Path, workdir: Path) -> str:  # pragma: no cover - live, slow
    """Build+install the crm wheel into a fresh venv; return its ``crm`` bin path.

    A **non-editable** ``pip install <repo_root>`` builds and installs the wheel (never an
    editable/repo install, per ADR 0028), so the eval exercises crm *as shipped*, isolated
    from the working tree. One venv per session, reused across both legs and all trials.
    """
    venv_dir = workdir / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    pip = venv_dir / "bin" / "pip"
    subprocess.run([str(pip), "install", "--quiet", str(repo_root)], check=True)
    crm_bin = venv_dir / "bin" / "crm"
    if not crm_bin.exists():
        raise RunError(f"session venv built but crm bin missing at {crm_bin}")
    return str(crm_bin)


def build_reset_org(
    task_file: str | Path, crm_bin: str
) -> Callable[[], None]:  # pragma: no cover - live
    """A reset hook that returns the org to the task's clean pre-state (delete marker records).

    For the create-verify skeleton, resetting is deleting the task's marked records via its
    declared ``cleanup`` steps, so both legs start with the artifact absent. Seeds a
    throwaway ``CRM_HOME`` (read-only from the real profile) so cleanup runs with creds but
    never touches the real profile store.
    """
    spec = parse_task_file(task_file)
    reset_home = Path(tempfile.mkdtemp(prefix="crm-eval-reset-"))
    # reset_home holds the target profile's plaintext secret (seed_target writes it); unlike
    # `session` it isn't rmtree'd in main()'s finally, so register cleanup here to never leave
    # a credential-bearing dir behind — even if the reset closure is never called.
    atexit.register(shutil.rmtree, reset_home, ignore_errors=True)
    profile = target_mod.seed_target(reset_home, spec.target)
    env = {**os.environ, "CRM_HOME": str(reset_home)}

    def _reset() -> None:
        cleanup_org(spec, env, profile, crm_bin, str(reset_home))

    return _reset


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - live front door
    parser = argparse.ArgumentParser(description="Paired skill-eval walking skeleton (#890).")
    parser.add_argument("--task", default=str(DEFAULT_TASK), help="do-task spec to run")
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"agent model (default {DEFAULT_MODEL})"
    )
    parser.add_argument("--k", type=int, default=1, help="trials per leg (default 1)")
    parser.add_argument("--preset", default="full", help="run preset stamped into run.json")
    parser.add_argument(
        "--results-dir", default=str(RESULTS_ROOT), help="root under which <run-id>/ is written"
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="don't write the sandbox settings (NOT ADR-compliant; for local wiring checks only)",
    )
    args = parser.parse_args(argv)

    if os.environ.get("D365_E2E") != "1":
        raise RunError(
            "live paired run requires D365_E2E=1 (live-e2e-style gate); never runs offline"
        )

    repo_root = Path(__file__).resolve().parents[2]
    profile_name = target_mod.resolve_profile_name()
    host = target_mod.resolve_host(profile_name)
    active = target_mod.active_target()
    run_id = _make_run_id()
    print(
        f"[paired] run {run_id}: task={Path(args.task).stem} model={args.model} "
        f"target={active} host={host} k={args.k}",
        file=sys.stderr,
    )

    session = Path(tempfile.mkdtemp(prefix="crm-eval-session-"))
    try:
        crm_bin = build_session_crm(repo_root, session)
        reset_org = build_reset_org(args.task, crm_bin)
        leg_kwargs: dict[str, object] = {
            # shlex.join (not " ".join): runner._resolve_agent_cmd shlex.splits this back,
            # so join symmetrically to survive the roundtrip.
            "agent_cmd": shlex.join(agent_argv(model=args.model)),
            "crm_bin": crm_bin,
            "wall_clock_s": WALL_CLOCK_S,
        }
        run_dir = Path(args.results_dir) / run_id

        def _stderr(msg: str) -> None:
            print(f"[paired] {msg}", file=sys.stderr, flush=True)

        def _go() -> list[TrialRecord]:
            return run_pair(
                args.task,
                reset_org=reset_org,
                k=args.k,
                transcripts_dir=run_dir / "transcripts",
                progress=_stderr,
                **leg_kwargs,
            )

        if args.no_sandbox:
            print("[paired] WARNING: --no-sandbox — outbound web is NOT blocked", file=sys.stderr)
        else:
            # Written identically into both legs' config dirs; the built-in sandbox confines
            # each Bash command to the org host while the model driver keeps normal network.
            leg_kwargs["sandbox_settings"] = sandbox_settings(host)
        trials = _go()

        aggregates = [aggregate_task(t, trials) for t in dict.fromkeys(x.task_id for x in trials)]
        meta = {
            "model": args.model,
            "target": active,
            "host": host,
            "k": args.k,
            "preset": args.preset,
            "paired": True,
            "skill_sha": record_mod.skill_sha(repo_root),
        }
        write_results(
            args.results_dir, run_id=run_id, meta=meta, trials=trials, aggregates=aggregates
        )
        _print_summary(run_id, run_dir, aggregates)
        return 0 if all(a.pass_skill_rate > 0 for a in aggregates) else 1
    finally:
        shutil.rmtree(session, ignore_errors=True)


def _print_summary(run_id: str, run_dir: Path, aggregates: list) -> None:  # pragma: no cover - live
    print(f"\n=== paired skill-eval {run_id} ===")
    for a in aggregates:
        gain = "N/A" if a.hake_gain is None else f"{a.hake_gain:+.2f}"
        print(
            f"  {a.task_id}: skill {a.pass_skill_rate:.0%} vs bare {a.pass_bare_rate:.0%}  "
            f"→ lift {a.pass_skill_rate - a.pass_bare_rate:+.0%}  Hake gain {gain}"
        )
    print(f"  results: {run_dir}/run.json")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
