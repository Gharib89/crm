"""Durable per-task run records — the persist half of persist-then-analyze (#588, ADR 0016).

Every run writes one record per task under ``evals/skill/runs/<UTC-ts>/`` so the
skill-efficacy ``review`` step can judge it later with **no agent and no live org**.
A record bundles everything the review needs: the prompt the agent was given, the raw
``stream-json`` trace, the parsed ``crm`` command sequence and run metrics (the
efficiency spine — :mod:`evals.skill.trace`), the correctness verdict (#572, unchanged),
and the **skill git SHA** so a re-review after editing the skill can tell whether the
saved trace was produced by the same skill it is now being judged against.

The run dir is **gitignored** (``evals/skill/runs/``): a trace carries live-org GUIDs
and the org machine fingerprint, and this is a public repo. ``efficacy_review`` is filled
in later by the review step; it is ``None`` on a freshly captured record.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path
from typing import Any

from evals.skill import trace
from evals.skill.runner import RunResult
from evals.skill.taskspec import TaskSpec

#: Where every run's timestamped sub-dir lands; gitignored (see module docstring).
RUNS_ROOT = Path(__file__).parent / "runs"


def _repo_root() -> Path:
    """The crm repo root (this file lives at ``<root>/evals/skill/``)."""
    return Path(__file__).resolve().parents[2]


@dataclasses.dataclass
class TaskRunRecord:
    """One task's durable run record (see module docstring for the field rationale)."""

    task_id: str
    prompt: str
    raw_trace: str
    commands: list[str]
    metrics: dict[str, Any]
    correctness_verdict: dict[str, Any]
    skill_sha: str
    #: The active target the trace was produced against (``cloud``/``onprem``/``either``).
    #: Part of the on-disk identity so a ``both`` run persisting both legs into one run dir
    #: never overwrites an ``either`` task's two legs, and the review knows which target a
    #: trace came from.
    target: str = ""
    #: True for the skill-**absent** leg of a ``--counterfactual`` pair, so it lands in a
    #: separate file and the review can compare it against the skill-present leg.
    counterfactual: bool = False
    #: Filled in by the review step (``None`` until then).
    efficacy_review: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRunRecord:
        # Records are persisted artifacts the re-review loop re-reads, possibly after the
        # schema evolved: tolerate unknown keys (a since-removed field) and rely on the
        # dataclass defaults for absent optional ones, so load_records stays robust across
        # schema drift. A missing *required* field still raises — that record is corrupt.
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def record_filename(task_id: str, target: str = "", counterfactual: bool = False) -> str:
    """The on-disk file name for a task's record.

    Keyed by ``task_id`` **and** ``target`` so a ``both`` run persisting both legs into one
    run dir never silently overwrites an ``either`` task's cloud and on-prem records; the
    skill-absent (counterfactual) leg gets its own suffix so a pair never collides either.
    """
    base = f"{task_id}.{target}" if target else task_id
    return f"{base}.counterfactual.json" if counterfactual else f"{base}.json"


def write_record(run_dir: str | Path, rec: TaskRunRecord) -> Path:
    """Write ``rec`` into ``run_dir`` (created if absent); return the path written."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / record_filename(rec.task_id, rec.target, rec.counterfactual)
    path.write_text(json.dumps(rec.to_dict(), indent=2), encoding="utf-8")
    return path


def load_records(run_dir: str | Path) -> list[TaskRunRecord]:
    """Load every ``*.json`` record under ``run_dir``, sorted by file name.

    ``report.md`` (also written into the run dir) is ``*.md``, so the ``*.json`` glob
    skips it without a special case.
    """
    return [
        TaskRunRecord.from_dict(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(Path(run_dir).glob("*.json"))
    ]


def latest_run_dir(runs_root: str | Path = RUNS_ROOT) -> Path | None:
    """The newest run sub-dir, or ``None`` if there are none.

    Run dirs are named with a UTC timestamp (``YYYYmmddTHHMMSSZ``) that sorts
    lexically by time, so the lexical max is the newest run.
    """
    runs_root = Path(runs_root)
    if not runs_root.is_dir():
        return None
    dirs = sorted(p for p in runs_root.iterdir() if p.is_dir())
    return dirs[-1] if dirs else None


def skill_sha(repo_root: str | Path | None = None) -> str:
    """The git tree-object SHA of ``crm/skills`` at HEAD, or ``"unknown"``.

    A *tree* SHA (``HEAD:crm/skills``) rather than the commit SHA, so it is stable
    across commits that don't touch the skill and changes exactly when the skill does —
    the provenance the review uses to flag run/review divergence. Best-effort: any git
    failure (not a repo, no such path, git absent) yields ``"unknown"`` rather than
    raising, since the stamp is provenance, not a gate.

    The committed tree SHA can't see *uncommitted* skill edits, yet the reviewer reads
    ``crm/skills`` live — so an uncommitted change would make the SHA silently misleading
    (the same SHA, different skill text). When the skill tree is dirty the SHA is suffixed
    ``-dirty`` so the provenance is honest rather than stale.
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD:crm/skills"],
            capture_output=True, text=True,
        )
    except OSError:
        return "unknown"
    if not (proc.returncode == 0 and proc.stdout.strip()):
        return "unknown"
    sha = proc.stdout.strip()
    try:
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", "crm/skills"],
            capture_output=True, text=True,
        )
    except OSError:
        return sha
    return f"{sha}-dirty" if dirty.returncode == 0 and dirty.stdout.strip() else sha


def build_record(
    spec: TaskSpec,
    result: RunResult,
    *,
    status: str,
    passed: bool,
    reason: str,
    sha: str,
    target: str = "",
    counterfactual: bool = False,
) -> TaskRunRecord:
    """Build a record from a scored task run: parse the trace, snapshot the verdict.

    The trace (and the parsed command sequence + metrics) come from ``result``, but the
    correctness verdict is passed in **explicitly** (``status``/``passed``/``reason``)
    rather than read off ``result`` — under ``--repeat`` the persisted ``result`` is the
    final trial's trace while the verdict is the *aggregate* across trials, so deriving
    ``passed`` from the last trial would contradict the aggregate ``status``. ``sha`` is
    the skill SHA stamped once per run; ``target`` is the active target.
    """
    return TaskRunRecord(
        task_id=spec.id,
        prompt=spec.prompt,
        raw_trace=result.transcript,
        commands=trace.parse_commands(result.transcript),
        metrics=trace.parse_metrics(result.transcript),
        correctness_verdict={"passed": passed, "reason": reason, "status": status},
        skill_sha=sha,
        target=target,
        counterfactual=counterfactual,
    )
