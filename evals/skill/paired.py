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
from evals.skill import report as report_mod
from evals.skill import target as target_mod
from evals.skill import trace
from evals.skill.presets import DEFAULT_SEED, PRESETS, resolve_tasks
from evals.skill.regression import RegressionReport, detect_regression, find_baseline
from evals.skill.results import (
    RESULTS_ROOT,
    TaskAggregate,
    TrialRecord,
    aggregate_task,
    is_reportable,
    write_results,
)
from evals.skill.runner import RunError, RunResult, cleanup_org, run_task
from evals.skill.sandbox import probe_enforcement, sandbox_settings
from evals.skill.taskspec import parse_task_file

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
    paired: bool = True,
    transcripts_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    **leg_kwargs: object,
) -> list[TrialRecord]:
    """Run ``task_file`` with-skill (and, when ``paired``, bare) k times each, resetting first.

    ``run_one`` (default :func:`runner.run_task`) is injectable so the orchestration is
    testable without a live org; ``reset_org`` is called **before every leg** so both legs
    start from identical, reseeded state (the attribution keystone). ``leg_kwargs`` (agent
    command, crm bin, wall-clock, sandbox wrap) are forwarded verbatim and identically to
    both legs — the treatment differs by exactly the skill install. When ``paired`` is
    ``False`` (the smoke / regression-check presets) the **bare leg is skipped** — a
    single-condition, with-skill-only run that has no lift to measure. ``progress`` (if
    given) is called with a human line as each leg starts/resolves — the front door routes
    it to stderr so a long run (up to ``2·k`` agent trials) shows live movement. Returns the
    flat list of per-leg :class:`~evals.skill.results.TrialRecord`s.
    """
    legs = (("skill", True), ("bare", False)) if paired else (("skill", True),)
    trials: list[TrialRecord] = []
    for trial in range(k):
        for leg, install in legs:
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
                    invoked=trace.parse_invoked(result.transcript),
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
    parser = argparse.ArgumentParser(description="Paired skill-eval run: presets, k, selection.")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default="full",
        help="full (paired, whole corpus), smoke (skill-only, ~8 tasks), "
        "regression-check (skill-only, whole corpus)",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"agent model (default {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--k", type=int, default=1, help="trials per leg (default 1; a reportable full run is ≥3)"
    )
    parser.add_argument(
        "--tasks",
        default=None,
        metavar="IDS",
        help="comma-separated task ids to run (overrides the preset's corpus slice)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="run a seeded N-task subset (overrides the preset's corpus slice)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"seed for --sample and the smoke slice (default {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--results-dir", default=str(RESULTS_ROOT), help="root under which <run-id>/ is written"
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="don't write the sandbox settings (NOT ADR-compliant; for local wiring checks only)",
    )
    args = parser.parse_args(argv)
    preset = PRESETS[args.preset]
    only = [t.strip() for t in args.tasks.split(",")] if args.tasks else None
    task_files = resolve_tasks(args.preset, only=only, sample=args.sample, seed=args.seed)

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
        f"[paired] run {run_id}: preset={args.preset} tasks={len(task_files)} model={args.model} "
        f"target={active} host={host} k={args.k} paired={preset.paired}",
        file=sys.stderr,
    )

    session = Path(tempfile.mkdtemp(prefix="crm-eval-session-"))
    try:
        crm_bin = build_session_crm(repo_root, session)
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
            # Each task carries its own cleanup, so its reset hook is built per task; both
            # legs of a pair still share the one session venv + sandbox settings.
            trials: list[TrialRecord] = []
            for task_file in task_files:
                _stderr(f"task {Path(task_file).stem}…")
                trials.extend(
                    run_pair(
                        task_file,
                        reset_org=build_reset_org(task_file, crm_bin),
                        k=args.k,
                        paired=preset.paired,
                        transcripts_dir=run_dir / "transcripts",
                        progress=_stderr,
                        **leg_kwargs,
                    )
                )
            return trials

        if args.no_sandbox:
            print("[paired] WARNING: --no-sandbox — outbound web is NOT blocked", file=sys.stderr)
        else:
            # Written identically into both legs' config dirs; the built-in sandbox confines
            # each Bash command to the org host while the model driver keeps normal network.
            leg_kwargs["sandbox_settings"] = sandbox_settings(host)
            # Fail-closed preflight: failIfUnavailable only proves the sandbox *binaries*
            # exist, not that the proxy *enforces* (dead proxy → false 0%/0% null; proxy up
            # but leaky → inflated lift — both seen on WSL2). Drive one sandboxed probe and
            # refuse to run the pair unless the org is reachable AND a non-org host is blocked,
            # so a broken sandbox aborts loudly instead of measuring garbage (#906).
            _stderr("preflight: verifying sandbox network enforcement (one probe agent)…")
            org_reachable, web_blocked, _probe_out = probe_enforcement(host, model=args.model)
            if not (org_reachable and web_blocked):
                raise RunError(
                    f"sandbox preflight failed for {host} "
                    f"(org_reachable={org_reachable}, web_blocked={web_blocked}): the built-in "
                    f"Bash sandbox is not enforcing network isolation, so the pair would "
                    f"measure a false null / inflated lift (#906). Run on a host where the "
                    f"sandbox network proxy is reliable, or pass --no-sandbox for an "
                    f"explicitly-unsandboxed wiring check."
                )
        trials = _go()

        aggregates = [aggregate_task(t, trials) for t in dict.fromkeys(x.task_id for x in trials)]
        meta = {
            "model": args.model,
            "target": active,
            "host": host,
            "k": args.k,
            "preset": args.preset,
            "paired": preset.paired,
            # A selection narrowed the corpus → not the "whole corpus" a reportable run needs.
            "subset": only is not None or args.sample is not None,
            "skill_sha": record_mod.skill_sha(repo_root),
        }
        # Advisory regression: look up the baseline BEFORE this run's own result is written, so a
        # reportable run can never select itself as its own baseline (series = model × target × k).
        # Never gates — the exit code below stays purely the did-anything-pass signal.
        baseline = find_baseline(args.results_dir, model=args.model, target=active, k=args.k)
        write_results(
            args.results_dir, run_id=run_id, meta=meta, trials=trials, aggregates=aggregates
        )
        regression = detect_regression(aggregates, baseline, k=args.k)
        _print_summary(run_id, run_dir, aggregates, paired=preset.paired)
        _print_regression(regression)
        # Reporting + commit policy (ADR 0028): only a reportable run writes report.md,
        # (re)derives matrix.md from the committed records, and force-adds its named
        # artifacts — transcripts/run.log stay untracked because they are never named.
        if is_reportable(preset=args.preset, paired=preset.paired, k=args.k, subset=meta["subset"]):
            _finalize_reporting(
                repo_root, args.results_dir, run_dir, run_id, meta, aggregates, trials, regression
            )
        return 0 if all(a.pass_skill_rate > 0 for a in aggregates) else 1
    finally:
        shutil.rmtree(session, ignore_errors=True)


def _finalize_reporting(
    repo_root: Path,
    results_dir: str,
    run_dir: Path,
    run_id: str,
    meta: dict[str, object],
    aggregates: list[TaskAggregate],
    trials: list[TrialRecord],
    regression: RegressionReport,
) -> None:  # pragma: no cover - live front door (git + disk side effects)
    """Write ``report.md``, (re)derive ``matrix.md``, and force-add the named artifacts.

    Called only for a reportable run. ``matrix.md`` is rebuilt from the committed
    ``run.json`` records (which now include this run's), so it always reflects the newest
    reportable run per series. The commit stages **only** the ADR-0028 named paths — the
    transcripts and ``run.log`` are never staged because they are never named.
    """
    report_md = report_mod.build_report(
        run_id=run_id, meta=meta, aggregates=aggregates, trials=trials, regression=regression
    )
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")

    matrix_path = Path(results_dir) / report_mod.MATRIX_NAME
    matrix_path.write_text(
        report_mod.build_matrix(report_mod.collect_reportable(results_dir)), encoding="utf-8"
    )

    artifacts = [str(p) for p in report_mod.artifact_paths(run_dir)]
    subprocess.run(["git", "-C", str(repo_root), "add", "-f", *artifacts], check=False)
    subprocess.run(["git", "-C", str(repo_root), "add", str(matrix_path)], check=False)
    print(f"  staged for commit: {', '.join(report_mod.ARTIFACT_NAMES)}, {report_mod.MATRIX_NAME}")


def _print_summary(
    run_id: str, run_dir: Path, aggregates: list, *, paired: bool
) -> None:  # pragma: no cover - live
    print(f"\n=== skill-eval {run_id} ===")
    for a in aggregates:
        if paired:
            gain = "N/A" if a.hake_gain is None else f"{a.hake_gain:+.2f}"
            print(
                f"  {a.task_id}: skill {a.pass_skill_rate:.0%} vs bare {a.pass_bare_rate:.0%}  "
                f"→ lift {a.pass_skill_rate - a.pass_bare_rate:+.0%}  Hake gain {gain}"
            )
        else:
            # A single-condition (skill-only) run has no bare leg — no lift/Hake to report.
            print(f"  {a.task_id}: skill {a.pass_skill_rate:.0%} ({a.passes_skill}/{a.k})")
    print(f"  results: {run_dir}/run.json")


def _print_regression(report: RegressionReport) -> None:  # pragma: no cover - live
    if report.baseline_run_id is None:
        print("  regression: no reportable baseline for this series yet (advisory)")
        return
    verdict = "⚠ FLAGGED" if report.flagged else "ok"
    drop = report.macro_drop_pp or 0.0
    base = report.baseline_macro or 0.0
    print(
        f"  regression vs {report.baseline_run_id} [{verdict}, advisory]: with-skill macro "
        f"{report.current_macro:.0%} vs baseline {base:.0%} (drop {drop:+.1f} pp)"
    )
    if report.flipped_tasks:
        print(f"    all-pass→all-fail flips: {', '.join(report.flipped_tasks)}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
