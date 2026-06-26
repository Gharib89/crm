"""Skill-efficacy review — the analyze half of persist-then-analyze (#588, ADR 0016).

Distinct from the correctness verdict (#572, "did the agent reach the goal?"). This
step asks the question a skill author actually has: **did the skill help the agent get
there efficiently, and what skill edit would have helped?** It is post-hoc — it reads
the saved run records (:mod:`evals.skill.record`), invokes no agent and touches no live
org — so a run can be judged (and *re-judged* against an edited skill) at zero live cost.

Per task it routes ``{prompt, crm command sequence, metrics, correctness verdict, the
live skill text}`` (and, when a ``--counterfactual`` skill-absent leg exists, that leg
for comparison) to a reviewer command (Claude, ``claude -p --model opus`` by default —
a judgment task) and parses a **structured** verdict: three graded axes (*goal reached* /
*command economy* / *skill adherence*), a **skill-lift** call (``helped|neutral|hindered``),
and a **skill-fix** suggestion (the payoff — the concrete skill edit, or ``none``).

The reviewer reads ``crm/skills/`` **live from disk** (it runs in the operator's env, not
the sandbox); the run record already stamps the skill git SHA, so a re-review after a skill
edit judges the saved traces against the *new* skill — the "did my fix help?" loop.

Outputs: each verdict is written back into its run record, a per-run ``report.md`` is
emitted into the run dir (gitignored), and ``review --record`` appends the org-agnostic
trend to the tracked ``efficacy.md`` — but only through :func:`guard_org_agnostic`, which
fails loudly on any GUID or the org MAC fingerprint so a trace leak can't reach a commit.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import date as _date
from pathlib import Path
from typing import Any, Callable

from evals.skill.record import (
    RUNS_ROOT,
    TaskRunRecord,
    latest_run_dir,
    load_records,
    write_record,
)

#: Default reviewer command. Opus by default — judging a trace against the skill is a
#: judgment task, not the cheap tier the agent-under-test runs on.
DEFAULT_REVIEW_CMD = "claude -p --model opus"

#: The skill the reviewer judges against, read live from the repo working tree.
SKILLS_DIR = Path(__file__).resolve().parents[2] / "crm" / "skills"

#: The tracked efficacy trend, appended to only by ``review --record`` (human gate).
EFFICACY_MD = Path(__file__).parent / "efficacy.md"

_AXES = ("goal_reached", "command_economy", "skill_adherence")
_GRADES = {"good", "weak", "bad"}
_LIFTS = {"helped", "neutral", "hindered"}

#: A Dataverse GUID and the org's Hyper-V MAC OUI fingerprint — either in efficacy.md
#: content means an org-derived leak (this is a public repo), so the guard refuses it.
_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ORG_FINGERPRINT = "00155d"

#: A reviewer takes a composed prompt and returns the raw reviewer text.
Reviewer = Callable[[str], str]


class ReviewError(RuntimeError):
    """Raised when the review cannot run or the reviewer output cannot be parsed."""


def resolve_review_cmd(review_cmd: str | None = None) -> list[str]:
    """Resolve the reviewer command: explicit arg > ``$CRM_EVAL_REVIEW_CMD`` > default."""
    raw = (review_cmd or os.environ.get("CRM_EVAL_REVIEW_CMD", "")).strip() or DEFAULT_REVIEW_CMD
    parts = shlex.split(raw)
    if not parts:
        raise ReviewError(f"empty reviewer command: {raw!r}")
    return parts


def read_skill_text(skills_dir: str | Path = SKILLS_DIR) -> str:
    """Concatenate the live skill (``SKILL.md`` router first, then ``reference/*.md``).

    Read from disk at review time so re-running after a skill edit judges the *new*
    skill. Each file is prefixed with its relative path so the reviewer can attribute a
    fix to a specific file.
    """
    skills_dir = Path(skills_dir)
    files: list[Path] = []
    router = skills_dir / "SKILL.md"
    if router.is_file():
        files.append(router)
    files.extend(sorted(skills_dir.glob("reference/*.md")))
    parts = [
        f"### {f.relative_to(skills_dir)}\n{f.read_text(encoding='utf-8')}" for f in files
    ]
    return "\n\n".join(parts)


def _commands_block(commands: list[str]) -> str:
    if not commands:
        return "(no crm commands run)"
    return "\n".join(f"{i}. {c}" for i, c in enumerate(commands, 1))


def build_review_prompt(
    *, rec: TaskRunRecord, skill_text: str, counterfactual: TaskRunRecord | None = None
) -> str:
    """Compose the reviewer prompt for one task.

    Carries the task prompt, the ordered ``crm`` command sequence, the run metrics, the
    correctness verdict, and the live skill text. When a skill-absent ``counterfactual``
    leg is supplied, its command sequence/metrics are included so the reviewer can
    measure lift rather than only judge it.
    """
    cf_section = ""
    if counterfactual is not None:
        cf_section = (
            "\n## Counterfactual: same task with the skill ABSENT\n"
            "The agent ran this task a second time with the crm skill *not* installed. "
            "Compare the two to measure the skill's lift (commands, turns, success).\n"
            f"### Skill-absent crm commands\n{_commands_block(counterfactual.commands)}\n"
            f"### Skill-absent metrics\n{json.dumps(counterfactual.metrics, default=str)}\n"
            f"### Skill-absent correctness\n{json.dumps(counterfactual.correctness_verdict, default=str)}\n"
        )
    return (
        "You are reviewing the *skill efficacy* of the `crm` CLI agent skill on one "
        "behavioral-eval task — NOT just whether the task passed, but whether the skill "
        "helped the agent reach the goal efficiently, and what skill edit would have "
        "helped. Read the task, the ordered crm commands the agent ran, the run metrics, "
        "the correctness verdict, and the skill text itself.\n\n"
        "## Task prompt given to the agent\n"
        f"{rec.prompt}\n\n"
        "## crm commands the agent ran (in order)\n"
        f"{_commands_block(rec.commands)}\n\n"
        "## Run metrics\n"
        f"{json.dumps(rec.metrics, default=str)}\n\n"
        "## Correctness verdict (from the deterministic predicate / #572)\n"
        f"{json.dumps(rec.correctness_verdict, default=str)}\n"
        f"{cf_section}\n"
        "## The skill the agent had\n"
        f"{skill_text}\n\n"
        "## Your structured review\n"
        "Reply with a single JSON object and nothing else, of exactly this shape:\n"
        "```json\n"
        "{\n"
        '  "axes": {\n'
        '    "goal_reached":    {"grade": "good|weak|bad", "note": "<one line>"},\n'
        '    "command_economy": {"grade": "good|weak|bad", "note": "<one line>"},\n'
        '    "skill_adherence": {"grade": "good|weak|bad", "note": "<one line>"}\n'
        "  },\n"
        '  "skill_lift": "helped|neutral|hindered",\n'
        '  "skill_fix": "<the concrete skill edit that would have helped, or the word none>"\n'
        "}\n"
        "```\n"
        "- goal_reached: did the agent actually reach the task's end state?\n"
        "- command_economy: fewest, most-appropriate crm commands; no trial-and-error or "
        "--help loops the skill should have pre-empted?\n"
        "- skill_adherence: did it follow the workflow/gotchas the skill documents?\n"
        "- skill_lift: did having the skill help vs neutral vs actively hinder?\n"
        "- skill_fix: the single most valuable concrete edit to the skill, or none.\n"
    )


def _extract_json(text: str) -> Any:
    """The JSON object spanning the first ``{`` to the last ``}`` (handles a ```json
    fence or leading/trailing prose), or ``None`` if that span doesn't parse."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_review(text: str) -> dict[str, Any]:
    """Parse + validate the reviewer's structured output; raise on a malformed verdict.

    The three axes must each carry a ``good|weak|bad`` grade, ``skill_lift`` must be one
    of ``helped|neutral|hindered``, and ``skill_fix`` must be present. A reviewer that
    drifts off-shape raises :class:`ReviewError` so the batch can record it as unparsed
    rather than store a verdict that isn't one.
    """
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        raise ReviewError("no JSON object found in reviewer output")
    axes = obj.get("axes")
    if not isinstance(axes, dict) or any(a not in axes for a in _AXES):
        raise ReviewError(f"review missing one of the axes {_AXES}")
    for name in _AXES:
        ax = axes[name]
        if not isinstance(ax, dict) or ax.get("grade") not in _GRADES:
            raise ReviewError(f"axis {name!r} needs a grade in {sorted(_GRADES)}")
    if obj.get("skill_lift") not in _LIFTS:
        raise ReviewError(f"skill_lift must be one of {sorted(_LIFTS)}")
    if "skill_fix" not in obj:
        raise ReviewError("review missing skill_fix")
    return obj


def run_reviewer(prompt: str, review_cmd: list[str]) -> str:
    """Feed the prompt to the reviewer command on stdin; return its text.

    Like the agent under test but without isolation: the reviewer is the *evaluator*,
    so it runs in the operator's own env (real HOME/credentials to reach Claude). A
    missing binary or non-zero exit raises :class:`ReviewError`.
    """
    try:
        proc = subprocess.run(review_cmd, input=prompt, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ReviewError(
            f"reviewer command not found: {review_cmd!r} — set CRM_EVAL_REVIEW_CMD "
            f"or pass --review-cmd ({exc})"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ReviewError(f"reviewer {review_cmd!r} exited {proc.returncode}: {detail[:500]}")
    return proc.stdout


def review_records(
    records: list[TaskRunRecord],
    *,
    skill_text: str,
    reviewer: Reviewer,
    task: str | None = None,
    failed_only: bool = False,
) -> list[TaskRunRecord]:
    """Review each skill-present record, attaching its ``efficacy_review``; return them.

    The skill-absent (counterfactual) records are not reviewed on their own — each is
    folded into its skill-present sibling's prompt by ``task_id``. Filters: ``task``
    (one id) and ``failed_only`` (correctness status != pass). A reviewer that returns
    unparseable output is recorded as ``{"unparsed": True, ...}`` rather than aborting
    the batch — one bad review must not lose the rest.
    """
    # Pair the skill-absent leg to its present sibling by (task_id, target): a `both` run
    # can hold an `either` task's cloud and on-prem legs, which must not cross-pair.
    counterfactuals = {(r.task_id, r.target): r for r in records if r.counterfactual}
    reviewed: list[TaskRunRecord] = []
    for rec in records:
        if rec.counterfactual:
            continue
        if task is not None and rec.task_id != task:
            continue
        if failed_only and rec.correctness_verdict.get("status") == "pass":
            continue
        prompt = build_review_prompt(
            rec=rec, skill_text=skill_text, counterfactual=counterfactuals.get((rec.task_id, rec.target))
        )
        raw = reviewer(prompt)
        try:
            rec.efficacy_review = parse_review(raw)
        except ReviewError as exc:
            rec.efficacy_review = {"unparsed": True, "error": str(exc), "raw_excerpt": raw[:500]}
        reviewed.append(rec)
    return reviewed


def _grade(rec: TaskRunRecord, axis: str) -> str:
    rev = rec.efficacy_review or {}
    if rev.get("unparsed"):
        return "?"
    return (rev.get("axes", {}).get(axis, {}) or {}).get("grade", "?")


def _lift(rec: TaskRunRecord) -> str:
    rev = rec.efficacy_review or {}
    return "?" if rev.get("unparsed") else rev.get("skill_lift", "?")


def build_report(records: list[TaskRunRecord]) -> str:
    """A per-run ``report.md``: a per-task axis table plus a clustered skill-fix digest.

    ``skill_fix`` values of ``none`` (and unparsed reviews) are omitted from the digest
    so it reads as a to-do list of real skill edits across the run's tasks.
    """
    lines = [
        "# Skill-efficacy report",
        "",
        "| task | lift | goal_reached | command_economy | skill_adherence |",
        "|---|---|---|---|---|",
    ]
    for rec in records:
        lines.append(
            f"| {rec.task_id} | {_lift(rec)} | {_grade(rec, 'goal_reached')} | "
            f"{_grade(rec, 'command_economy')} | {_grade(rec, 'skill_adherence')} |"
        )
    lines += ["", "## Skill-fix suggestions", ""]
    fixes = [
        (rec.task_id, (rec.efficacy_review or {}).get("skill_fix"))
        for rec in records
    ]
    real = [(tid, fix) for tid, fix in fixes if fix and str(fix).strip().lower() != "none"]
    if real:
        lines += [f"- **{tid}**: {fix}" for tid, fix in real]
    else:
        lines.append("_No skill fixes suggested._")
    return "\n".join(lines) + "\n"


def guard_org_agnostic(text: str) -> None:
    """Raise :class:`ReviewError` if ``text`` carries any GUID or the org MAC fingerprint.

    The gate on writing the tracked ``efficacy.md``: an LLM-derived line could echo a
    GUID or fingerprint from the trace, and this is a public repo, so a leak must fail
    loudly here rather than land in a commit.
    """
    m = _GUID_RE.search(text)
    if m:
        raise ReviewError(f"refusing to write org-derived content: GUID {m.group()!r} present")
    if _ORG_FINGERPRINT in text.lower():
        raise ReviewError(
            f"refusing to write org-derived content: org MAC fingerprint {_ORG_FINGERPRINT!r} present"
        )


def build_efficacy_block(records: list[TaskRunRecord], *, date: str) -> str:
    """The org-agnostic trend block ``review --record`` appends to ``efficacy.md``.

    Carries only what is about the *skill*, not the org: per-axis good/weak/bad tallies,
    the lift tally, and the clustered skill-fix suggestions. No GUIDs, no org state —
    :func:`guard_org_agnostic` enforces that before it is written.
    """
    parsed = [r for r in records if r.efficacy_review and not r.efficacy_review.get("unparsed")]
    lines = [f"## {date}", "", f"Tasks reviewed: {len(records)} (parsed: {len(parsed)})", ""]
    for axis in _AXES:
        tally = {g: sum(1 for r in parsed if _grade(r, axis) == g) for g in ("good", "weak", "bad")}
        lines.append(f"- {axis}: good={tally['good']} weak={tally['weak']} bad={tally['bad']}")
    lift_tally = {lv: sum(1 for r in parsed if _lift(r) == lv) for lv in ("helped", "neutral", "hindered")}
    lines.append(
        f"- skill_lift: helped={lift_tally['helped']} neutral={lift_tally['neutral']} "
        f"hindered={lift_tally['hindered']}"
    )
    lines += ["", "### Skill-fix suggestions", ""]
    real = [
        (r.task_id, (r.efficacy_review or {}).get("skill_fix"))
        for r in parsed
        if (r.efficacy_review or {}).get("skill_fix")
        and str((r.efficacy_review or {}).get("skill_fix")).strip().lower() != "none"
    ]
    lines += [f"- **{tid}**: {fix}" for tid, fix in real] or ["_None._"]
    return "\n".join(lines) + "\n"


def append_efficacy(path: str | Path, block: str) -> None:
    """Append a trend block to ``efficacy.md`` (after the GUID guard at the call site)."""
    path = Path(path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if not existing or existing.endswith("\n") else "\n"
    path.write_text(existing + sep + "\n" + block, encoding="utf-8")


def run_review_cmd(
    *,
    run_dir: str | Path | None = None,
    runs_root: str | Path = RUNS_ROOT,
    task: str | None = None,
    failed_only: bool = False,
    record_efficacy: bool = False,
    review_cmd: str | None = None,
    skills_dir: str | Path = SKILLS_DIR,
    efficacy_path: str | Path = EFFICACY_MD,
    today: str | None = None,
    reviewer: Reviewer | None = None,
    skill_reader: Callable[[str | Path], str] = read_skill_text,
) -> int:
    """Review a run dir end-to-end: judge each task, write the verdicts back, emit
    ``report.md``, and (``--record``) append the guarded trend to ``efficacy.md``.

    ``reviewer``/``skill_reader`` are injectable so the orchestration is testable offline
    without invoking Claude. Returns a process exit code (0 ok, non-zero on no run dir).
    """
    target = Path(run_dir) if run_dir is not None else latest_run_dir(runs_root)
    if target is None or not Path(target).is_dir():
        print(f"no run dir to review (looked under {runs_root}); run `python -m evals.skill run` first")
        return 1
    target = Path(target)

    actual_reviewer = reviewer or (lambda p: run_reviewer(p, resolve_review_cmd(review_cmd)))
    skill_text = skill_reader(skills_dir)
    records = load_records(target)
    reviewed = review_records(
        records, skill_text=skill_text, reviewer=actual_reviewer, task=task, failed_only=failed_only
    )
    for rec in reviewed:
        write_record(target, rec)

    (target / "report.md").write_text(build_report(reviewed), encoding="utf-8")

    if record_efficacy:
        block = build_efficacy_block(reviewed, date=today or _date.today().isoformat())
        guard_org_agnostic(block)  # fail loudly before touching the tracked file
        append_efficacy(efficacy_path, block)

    print(f"reviewed {len(reviewed)} task(s); report: {(target / 'report.md').resolve()}")
    if record_efficacy:
        print(f"efficacy trend appended: {Path(efficacy_path).resolve()}")
    return 0
