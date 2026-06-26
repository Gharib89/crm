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
    #: True for the skill-**absent** leg of a ``--counterfactual`` pair, so it lands in a
    #: separate file and the review can compare it against the skill-present leg.
    counterfactual: bool = False
    #: Filled in by the review step (``None`` until then).
    efficacy_review: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRunRecord:
        return cls(**data)


def record_filename(task_id: str, counterfactual: bool = False) -> str:
    """The on-disk file name for a task's record; the absent leg gets its own name so
    the two legs of a counterfactual pair never collide."""
    return f"{task_id}.counterfactual.json" if counterfactual else f"{task_id}.json"


def write_record(run_dir: str | Path, rec: TaskRunRecord) -> Path:
    """Write ``rec`` into ``run_dir`` (created if absent); return the path written."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / record_filename(rec.task_id, rec.counterfactual)
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
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD:crm/skills"],
            capture_output=True, text=True,
        )
    except OSError:
        return "unknown"
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return "unknown"


def build_record(
    spec: TaskSpec, result: RunResult, status: str, sha: str, *, counterfactual: bool = False
) -> TaskRunRecord:
    """Build a record from a scored task run: parse the trace, snapshot the verdict.

    ``status`` is the set-runner :class:`~evals.skill.set_runner.TaskOutcome` status
    (``pass``/``fail``); ``sha`` is the skill SHA stamped once per run.
    """
    return TaskRunRecord(
        task_id=spec.id,
        prompt=spec.prompt,
        raw_trace=result.transcript,
        commands=trace.parse_commands(result.transcript),
        metrics=trace.parse_metrics(result.transcript),
        correctness_verdict={"passed": result.passed, "reason": result.reason, "status": status},
        skill_sha=sha,
        counterfactual=counterfactual,
    )
