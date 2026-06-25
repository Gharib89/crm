"""The both-targets runner: union coverage across cloud + on-prem, plus the periodic
baseline trend (issue #573).

Where :mod:`evals.skill.set_runner` runs the whole set against *one* target, this loops
the two standing project profiles — ``agent-cloud`` and ``agent-on-prem`` — runs the set
against each *reachable* one, and reports coverage as the **union**. A target whose host
does not answer (on-prem with the VPN down) is **skipped with a message**, never failed:
cloud gives always-on breadth, and the on-prem leg lands its rows whenever a maintainer
runs this with the VPN up. ``--repeat N`` runs each task N times per target to smooth
run-to-run variance, recording the pass-rate as a fraction.

``--update-baseline`` appends one dated per-target row to the tracked ``baseline.md`` so a
periodic scheduled run accumulates a trend a human reads for effectiveness drift (ADR
0015). It is never a CI gate and no threshold gates anything.

On-demand / periodic invocation:

    D365_E2E_ALLOW_HOST=<cloud-host> CRM_EVAL_AGENT_CMD='claude -p' \\
        python -m evals.skill.both_runner --repeat 3 --update-baseline
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date as _date
from pathlib import Path
from typing import Any

from evals.skill import target as target_mod
from evals.skill.set_runner import ERROR, FAIL, PASS, SetResult, run_set
from evals.skill.target import TargetError

#: The two standing project targets; cloud first (always reachable, no VPN), on-prem
#: second (the priority target, VPN-gated). Matches the project's e2e profile names.
DEFAULT_PROFILES = ("agent-cloud", "agent-on-prem")

BASELINE = Path(__file__).parent / "baseline.md"

#: Baseline table columns, in order — the row dicts and the header in baseline.md agree.
_COLS = ("date", "target", "profile", "pass_rate", "scored", "repeat", "notes")


@dataclasses.dataclass
class TargetRun:
    """One target's leg of a both-targets run: its set result, or why it was skipped."""

    profile: str
    target: str | None  # "cloud"/"onprem", or None if the profile couldn't be resolved
    result: SetResult | None  # the set run, or None when the target was skipped
    skipped_reason: str | None  # why skipped (unreachable / profile error), else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "target": self.target,
            "skipped_reason": self.skipped_reason,
            "result": self.result.to_dict() if self.result else None,
        }


@dataclasses.dataclass
class BothResult:
    """Outcome of a both-targets run: one :class:`TargetRun` per attempted profile."""

    entries: list[TargetRun]
    repeat: int = 1

    def union_scored(self) -> set[str]:
        """Task ids scored (PASS/FAIL) on *at least one* reachable target — the union."""
        ids: set[str] = set()
        for e in self.entries:
            if e.result:
                ids |= {o.task_id for o in e.result.outcomes if o.status in (PASS, FAIL)}
        return ids

    def exit_code(self) -> int:
        """Non-zero iff a *reachable* target scored a failure/error. A skipped target
        (unreachable VPN) is not a failure, so it never flips the code."""
        for e in self.entries:
            if e.result and any(o.status in (FAIL, ERROR) for o in e.result.outcomes):
                return 1
        return 0

    def baseline_rows(self, *, today: str) -> list[dict[str, Any]]:
        """One row dict per attempted target — the dated baseline trend entry.

        A reachable target records its pass-rate (percentage) and the raw scored
        fraction (#573 AC5); a skipped target records the skip reason in ``notes`` so the
        trend shows the gap rather than silently omitting it.
        """
        rows: list[dict[str, Any]] = []
        for e in self.entries:
            if e.result is None:
                rows.append({
                    "date": today, "target": e.target or "?", "profile": e.profile,
                    "pass_rate": "—", "scored": "—", "repeat": "—",
                    "notes": e.skipped_reason or "",
                })
                continue
            rate = e.result.pass_rate()
            passes, trials = e.result.scored_fraction()
            rows.append({
                "date": today, "target": e.target or "?", "profile": e.profile,
                "pass_rate": "n/a" if rate is None else f"{rate:.0%}",
                "scored": f"{passes}/{trials}" if trials else "—",
                "repeat": self.repeat,
                "notes": "",
            })
        return rows

    def summary_lines(self) -> list[str]:
        """Human-readable per-target lines plus the union-coverage total."""
        lines: list[str] = []
        for e in self.entries:
            if e.result is None:
                lines.append(f"{e.profile} ({e.target or '?'}): SKIPPED — {e.skipped_reason}")
                continue
            rate = e.result.pass_rate()
            passes, trials = e.result.scored_fraction()
            rate_str = "n/a" if rate is None else f"{rate:.0%}"
            lines.append(f"{e.profile} ({e.target}): pass-rate {rate_str} ({passes}/{trials} trials)")
        lines.append("")
        lines.append(f"union coverage: {len(self.union_scored())} task(s) scored across reachable targets")
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "repeat": self.repeat,
            "union_scored": sorted(self.union_scored()),
            "entries": [e.to_dict() for e in self.entries],
        }


def run_both(
    profiles: Sequence[str] = DEFAULT_PROFILES,
    *,
    repeat: int = 1,
    agent_cmd: str | None = None,
    run_set_fn: Callable[..., SetResult] = run_set,
    probe_fn: Callable[[str], bool] = target_mod.probe_reachable,
    target_fn: Callable[[], str] = target_mod.active_target,
) -> BothResult:
    """Run the set against each reachable profile in ``profiles``; union the coverage.

    Each profile is pointed at via ``D365_E2E_PROFILE`` (saved and restored around the
    run so the caller's environment is untouched). A profile that cannot be resolved or
    whose host is unreachable becomes a *skipped* :class:`TargetRun` with a reason — one
    target's absence never aborts the other. ``run_set_fn``/``probe_fn``/``target_fn`` are
    injectable so the orchestration is testable offline without an agent or a live org.
    """
    entries: list[TargetRun] = []
    saved = os.environ.get(target_mod.E2E_PROFILE_ENV)
    try:
        for name in profiles:
            os.environ[target_mod.E2E_PROFILE_ENV] = name
            try:
                tgt = target_fn()
            except TargetError as exc:
                entries.append(TargetRun(name, None, None, f"profile error: {exc}"))
                continue
            if not probe_fn(name):
                entries.append(
                    TargetRun(name, tgt, None, "unreachable (VPN down / host not responding?)")
                )
                continue
            result = run_set_fn(repeat=repeat, agent_cmd=agent_cmd, active_target=tgt)
            entries.append(TargetRun(name, tgt, result, None))
    finally:
        if saved is None:
            os.environ.pop(target_mod.E2E_PROFILE_ENV, None)
        else:
            os.environ[target_mod.E2E_PROFILE_ENV] = saved
    return BothResult(entries, repeat=repeat)


def append_baseline(path: str | Path, rows: list[dict[str, Any]]) -> None:
    """Append one markdown table row per dict in ``rows`` to the baseline file.

    The table is the file's last block, so rows are appended at EOF and the trend grows
    append-only (oldest first). Each dict must carry every key in :data:`_COLS`.
    """
    path = Path(path)
    line_strs = ["| " + " | ".join(str(r[c]) for c in _COLS) + " |" for r in rows]
    existing = path.read_text()
    sep = "" if existing.endswith("\n") else "\n"
    path.write_text(existing + sep + "\n".join(line_strs) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the skill-eval set against both targets; union coverage + baseline trend."
    )
    parser.add_argument("--profiles", default=",".join(DEFAULT_PROFILES),
                        help="comma-separated profile names (default: agent-cloud,agent-on-prem)")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="run each scored task N times per target (variance smoothing; default 1)")
    parser.add_argument("--agent-cmd", default=None, help="agent command (default: $CRM_EVAL_AGENT_CMD)")
    parser.add_argument("--update-baseline", action="store_true",
                        help=f"append a dated per-target row to {BASELINE.name}")
    parser.add_argument("--baseline", default=str(BASELINE), help="baseline file to append to")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="emit the machine-readable result instead of the summary")
    args = parser.parse_args(argv)

    profiles = [p.strip() for p in args.profiles.split(",") if p.strip()]
    res = run_both(profiles, repeat=args.repeat, agent_cmd=args.agent_cmd)

    if args.update_baseline:
        append_baseline(args.baseline, res.baseline_rows(today=_date.today().isoformat()))

    if args.as_json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print("\n".join(res.summary_lines()))
    return res.exit_code()


if __name__ == "__main__":
    sys.exit(main())
