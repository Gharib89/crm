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
import json
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
from evals.skill.judge import Judge, make_judge
from evals.skill.presets import DEFAULT_SEED, PRESETS, resolve_tasks
from evals.skill.regression import RegressionReport, detect_regression, find_baseline
from evals.skill.results import (
    RESULTS_ROOT,
    TaskAggregate,
    TrialRecord,
    aggregate_task,
    append_trials,
    complete_task_ids,
    is_reportable,
    load_trials,
    rewrite_trials,
    stamp_run_start,
    write_results,
)
from evals.skill.runner import RunError, RunResult, cleanup_org, run_task
from evals.skill.sandbox import AAD_LOGIN_HOST, probe_enforcement, sandbox_settings
from evals.skill.set_runner import should_skip
from evals.skill.taskspec import parse_task_file

DEFAULT_MODEL = "sonnet"
#: Guardrails pinned by ADR 0028: the only tools the agent may use, and the turn cap.
ALLOWED_TOOLS = "Bash,Read,Grep,Glob,Skill"
MAX_TURNS = 50
#: Bounded per-leg retries when the agent *driver* dies on an API error (e.g. 529
#: Overloaded) before working the task — infrastructure, never task behavior (#943).
_API_ERROR_RETRIES = 2
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


def gate_tasks(
    task_files: list[Path], active_target: str
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Split the corpus into ``(runnable, skipped)`` for ``active_target``.

    The set/both runners gate each task before scoring; the paired path must do the
    same or the run *crashes* mid-corpus (``seed_target`` raises on a target mismatch,
    ``run_task`` refuses a diagnostic task) — and paired results are only written at
    the end, so one ungated task destroys every finished trial. Skips are returned
    with their reason so the front door can report them; a skip is not a ``--tasks``/
    ``--sample`` subset and never affects reportability (mirrors the set runner).
    """
    runnable: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    for task_file in task_files:
        spec = parse_task_file(task_file)
        if spec.is_diagnostic:
            skipped.append((task_file, "diagnostic (scored by --analyze, not paired)"))
        elif should_skip(spec.target, active_target):
            skipped.append((task_file, f"pinned target={spec.target!r}, active={active_target!r}"))
        else:
            runnable.append(task_file)
    return runnable, skipped


def run_pair(
    task_file: str | Path,
    *,
    reset_org: Callable[[], None],
    run_one: Callable[..., RunResult] = run_task,
    k: int = 1,
    paired: bool = True,
    transcripts_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
    judge: Judge | None = None,
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
    it to stderr so a long run (up to ``2·k`` agent trials) shows live movement.

    ``judge`` (if given) is the advisory L2 judge (:mod:`evals.skill.judge`): after each leg
    it scores the transcript on ``(task_prompt, transcript)`` — **blind to the leg**, which
    is never passed — and the verdict lands on that leg's ``TrialRecord.judge``, alongside
    the L1 ``passed`` it never touches. Returns the flat list of per-leg
    :class:`~evals.skill.results.TrialRecord`s.
    """
    # The judge needs the task's prompt; parse it once (only when judging) so the non-judge
    # path stays free of a file read and the existing fake-file tests need no real corpus.
    judge_prompt = parse_task_file(task_file).prompt if judge is not None else ""
    legs = (("skill", True), ("bare", False)) if paired else (("skill", True),)
    trials: list[TrialRecord] = []
    for trial in range(k):
        for leg, install in legs:
            if progress is not None:
                progress(f"trial {trial + 1}/{k} · {leg} leg · resetting org + running agent…")
            reset_org()
            result = run_one(task_file, install_skill=install, dry_run=False, **leg_kwargs)
            # An agent-level API death (e.g. 529 Overloaded) is infrastructure, not task
            # behavior — scoring it a fail poisons the run (#943). Retry the leg from a
            # fresh org reset, bounded; a persistent outage still lands as a fail.
            for _ in range(_API_ERROR_RETRIES):
                if not trace.parse_api_error(result.transcript):
                    break
                if progress is not None:
                    progress(f"trial {trial + 1}/{k} · {leg} leg · agent API error — retrying")
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
            metrics = trace.parse_metrics(result.transcript)
            # Invocation: True if the skill was loaded; False only when the run *completed*
            # (a terminal result event → non-empty metrics) without loading it; None when the
            # transcript never completed (crash/truncation), so "did not invoke" is unknown
            # rather than falsely asserted.
            if trace.parse_invoked(result.transcript):
                invoked: bool | None = True
            elif metrics:
                invoked = False
            else:
                invoked = None
            # Advisory L2: score the transcript blind to the leg (only prompt + transcript
            # are handed over). Never touches `passed` — see TrialRecord.judge.
            verdict = judge(judge_prompt, result.transcript) if judge is not None else None
            trials.append(
                TrialRecord(
                    task_id=result.task_id,
                    leg=leg,
                    trial=trial,
                    passed=bool(result.passed),
                    reason=result.reason,
                    capped=result.capped,
                    metrics=metrics,
                    transcript_ref=ref,
                    invoked=invoked,
                    judge=verdict,
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
    parser.add_argument(
        "--judge",
        action="store_true",
        help="run the advisory blind L2 judge per trial (extra model calls; never gates, "
        "never enters lift/regression — recorded on the trial for human reading)",
    )
    parser.add_argument(
        "--judge-cmd",
        default=None,
        metavar="CMD",
        help="judge command (default: $CRM_EVAL_JUDGE_CMD, else 'claude -p --model opus')",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RUN_ID",
        help="continue an interrupted run: tasks already complete in its trials.jsonl are "
        "kept and skipped; a partially-run task reruns whole (flags must match the run's "
        "stamped meta)",
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
    task_files, gated_out = gate_tasks(task_files, active)
    # `host` is intentionally NOT persisted into meta: a reportable run commits run.json
    # (and report.md) to a public repo, and the live org host (esp. on-prem/internal) is
    # sensitive. The series identity is model × target × k; host is a live-run detail only
    # (it stays in the stderr summary + transcripts, which are never committed).
    # A selection narrowed the corpus → not the "whole corpus" a reportable run needs.
    subset = only is not None or args.sample is not None
    meta: dict[str, object] = {
        "model": args.model,
        "target": active,
        "k": args.k,
        "preset": args.preset,
        "paired": preset.paired,
        "subset": subset,
        "skill_sha": record_mod.skill_sha(repo_root),
    }

    prior_trials: list[TrialRecord] = []
    done_tasks: set[str] = set()
    if args.resume:
        run_id = args.resume
        run_dir = Path(args.results_dir) / run_id
        stamp_path = run_dir / "run.json"
        if not stamp_path.exists():
            raise RunError(f"--resume {run_id}: no run.json under {run_dir} — not a resumable run")
        stamped = json.loads(stamp_path.read_text(encoding="utf-8")).get("meta", {})
        # The resume contract: same series + corpus config, or the mixed rows would be
        # meaningless (skill_sha excluded deliberately — resuming across a skill edit is
        # legitimate only if you accept the mix, and the stamp records the original).
        for key in ("model", "target", "k", "preset", "paired", "subset"):
            if stamped.get(key) != meta[key]:
                raise RunError(
                    f"--resume {run_id}: {key} mismatch (run was {stamped.get(key)!r}, "
                    f"flags say {meta[key]!r}) — rerun with the original flags"
                )
        prior_trials = load_trials(run_dir)
        done_tasks = complete_task_ids(prior_trials, k=args.k, paired=preset.paired)
        # Keep only whole per-task blocks: an interrupted task reruns from scratch, so its
        # partial rows must not survive to double-count in the final aggregate.
        prior_trials = [t for t in prior_trials if t.task_id in done_tasks]
        rewrite_trials(run_dir, prior_trials)
    else:
        run_id = _make_run_id()
    print(
        f"[paired] run {run_id}: preset={args.preset} tasks={len(task_files)} model={args.model} "
        f"target={active} host={host} k={args.k} paired={preset.paired}"
        + (f" resume(complete={len(done_tasks)})" if args.resume else ""),
        file=sys.stderr,
    )
    for task_file, why in gated_out:
        print(f"[paired] skip {task_file.stem}: {why}", file=sys.stderr)

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

        # Built once (resolves the judge command up front so a bad --judge-cmd fails before
        # any live trial); None when --judge is off, so run_pair skips it entirely.
        judge = make_judge(args.judge_cmd) if args.judge else None
        if judge is not None:
            _stderr("advisory L2 judge ON (per-trial; never gates, never enters lift stats)")

        def _go() -> list[TrialRecord]:
            # Each task carries its own cleanup, so its reset hook is built per task; both
            # legs of a pair still share the one session venv + sandbox settings. Each
            # task's rows are appended to trials.jsonl the moment its legs finish, so an
            # interruption (limit, crash, kill) loses at most the in-flight task and
            # `--resume <run-id>` continues from the next one.
            trials: list[TrialRecord] = list(prior_trials)
            total = len(task_files)
            per_task = args.k * (2 if preset.paired else 1)
            goal = total * per_task
            for pos, task_file in enumerate(task_files, 1):
                task_id = task_file.stem
                if task_id in done_tasks:
                    _stderr(f"task {pos}/{total} {task_id}: already complete — skipped (resume)")
                    continue
                _stderr(f"task {pos}/{total} {task_id}… [{len(trials)}/{goal} trials done]")
                records = run_pair(
                    task_file,
                    reset_org=build_reset_org(task_file, crm_bin),
                    k=args.k,
                    paired=preset.paired,
                    transcripts_dir=run_dir / "transcripts",
                    progress=_stderr,
                    judge=judge,
                    **leg_kwargs,
                )
                trials.extend(records)
                append_trials(run_dir, records)
                passed = sum(1 for r in records if r.passed)
                _stderr(
                    f"task {pos}/{total} {task_id} done: {passed}/{len(records)} legs passed "
                    f"[{len(trials)}/{goal} trials, {len(trials) * 100 // goal}%]"
                )
            return trials

        if args.no_sandbox:
            print("[paired] WARNING: --no-sandbox — outbound web is NOT blocked", file=sys.stderr)
        else:
            # Written identically into both legs' config dirs; the built-in sandbox confines
            # each Bash command to the org host while the model driver keeps normal network.
            # The cloud target's OAuth client-credentials flow must reach AAD for its
            # token; on-prem NTLM authenticates against the org host itself (no widening).
            leg_kwargs["sandbox_settings"] = sandbox_settings(
                host, auth_hosts=(AAD_LOGIN_HOST,) if active == "cloud" else ()
            )
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
        # Stamp the in-progress run.json before the first trial: an interrupted run then
        # leaves a valid, never-reportable record carrying the meta the resume validates.
        # On resume the stamp already exists with identical meta (validated above).
        if not args.resume:
            stamp_run_start(args.results_dir, run_id=run_id, meta=meta)
        trials = _go()

        aggregates = [aggregate_task(t, trials) for t in dict.fromkeys(x.task_id for x in trials)]
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
        if is_reportable(preset=args.preset, paired=preset.paired, k=args.k, subset=subset):
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
    add_named = subprocess.run(["git", "-C", str(repo_root), "add", "-f", *artifacts], check=False)
    add_matrix = subprocess.run(["git", "-C", str(repo_root), "add", str(matrix_path)], check=False)
    if add_named.returncode == 0 and add_matrix.returncode == 0:
        print(
            f"  staged for commit: {', '.join(report_mod.ARTIFACT_NAMES)}, {report_mod.MATRIX_NAME}"
        )
    else:
        print(
            f"  WARNING: git add failed (rc {add_named.returncode}/{add_matrix.returncode}); "
            f"artifacts written to {run_dir} but NOT staged — stage them manually",
            file=sys.stderr,
        )


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
