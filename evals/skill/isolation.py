"""Agent isolation — the validity keystone of the behavioral eval (ADR 0015).

The eval measures the *skill*, so the agent is given only what a real user has: the
installed skill, the ``crm`` binary, and ``gh`` — and **no path to the repo**: no
repo-relative working dir, no ``CLAUDE.md`` on the memory-discovery path, no repo on
the import path, no inherited memory. (The checkout still exists on the host disk;
this harness withholds the routes to it rather than sandboxing the filesystem — see
the scope note below.) If isolation leaks, the run silently measures the repo and
over-reports.

Isolation here is *by construction*, then *verified*:

- a throwaway sandbox holds a fresh ``HOME``, an empty working dir outside the repo,
  and a throwaway ``CRM_HOME``;
- the skill is installed into that fresh ``HOME`` via ``crm skill install`` (exactly
  the path a user takes), so the agent reads the installed copy, never the repo tree;
- the agent's environment is scrubbed of anything pointing back at the repo.

`verify_isolation` then asserts the agent has no path to the repo and that the skill
is actually present, raising :class:`IsolationError` on any leak. Hard filesystem
sandboxing (containers, namespaces) is deliberately out of tracer scope — this proves
the *environment* exposes no repo, which is what the trial protocol relied on.
"""
from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


class IsolationError(RuntimeError):
    """Raised when isolation could not be provisioned or a leak was detected."""


@dataclasses.dataclass(frozen=True)
class Isolation:
    """A provisioned isolated agent context. Call :meth:`cleanup` when done."""

    sandbox: Path
    home: Path
    work: Path
    crm_home: Path
    skill_dir: Path
    #: environment to launch the agent with — repo-free, fresh HOME/CRM_HOME.
    env: dict[str, str]

    def cleanup(self) -> None:
        shutil.rmtree(self.sandbox, ignore_errors=True)


def repo_root() -> Path:
    """The crm repository root (this file lives at ``<root>/evals/skill/``)."""
    return Path(__file__).resolve().parents[2]


def _crm_bin() -> str:
    found = shutil.which("crm")
    if not found:
        raise IsolationError("crm binary not on PATH — install the CLI first")
    return found


def provision_isolation(crm_bin: str | None = None) -> Isolation:
    """Create a sandbox and install the skill into a fresh HOME via ``crm skill install``.

    Returns an :class:`Isolation` whose ``env`` launches the agent with no path back
    to the repo. The caller is responsible for :meth:`Isolation.cleanup`.
    """
    crm_bin = crm_bin or _crm_bin()
    sandbox = Path(tempfile.mkdtemp(prefix="crm-eval-"))
    home = sandbox / "home"
    work = sandbox / "work"
    crm_home = sandbox / "crm_home"
    for d in (home, work, crm_home):
        d.mkdir(parents=True)

    skill_dir = home / ".claude" / "skills" / "crm"

    # Scrub the environment of anything that could lead back to the repo: fresh HOME
    # and CRM_HOME, no PYTHONPATH (which could put the repo's `crm/` package on the
    # import path), no inherited CLAUDE.md pointers. Built *before* the install so the
    # skill-install subprocess's own writes (skill_registry.record_install →
    # installed-skills.json) land in the throwaway CRM_HOME, never the caller's real
    # state — otherwise the harness would pollute the maintainer's profile/config.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "CLAUDE_PROJECT_DIR")}
    env["HOME"] = str(home)
    env["CRM_HOME"] = str(crm_home)

    # Install the skill the way a user does — from whatever `crm` is on PATH, so the
    # eval exercises the skill *as shipped* in that binary, not the repo's working
    # tree. --dest pins the location; --force makes it idempotent across re-runs.
    result = subprocess.run(
        [crm_bin, "skill", "install", "--dest", str(skill_dir), "--force"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(work),  # run from the sandbox, not the caller's (repo) cwd
    )
    if result.returncode != 0:
        shutil.rmtree(sandbox, ignore_errors=True)
        raise IsolationError(
            f"`crm skill install` failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    return Isolation(
        sandbox=sandbox, home=home, work=work, crm_home=crm_home, skill_dir=skill_dir, env=env
    )


def _has_child(directory: Path, name: str) -> bool:
    return (directory / name).exists()


def verify_isolation(iso: Isolation, root: Path | None = None) -> dict[str, str]:
    """Assert the agent has no path to the repo and the skill is installed.

    Returns a dict of passed check-name → detail. Raises :class:`IsolationError`
    naming every failed check, so a leak fails loudly before any agent runs.
    """
    root = (root or repo_root()).resolve()
    work = iso.work.resolve()
    checks: dict[str, str] = {}
    failures: list[str] = []

    # 1 · The working dir is outside the repo tree (not the repo, not under it).
    if work == root or root in work.parents:
        failures.append(f"work dir {work} is inside the repo {root}")
    else:
        checks["work-outside-repo"] = str(work)

    # 2 · No repo markers (.git / CLAUDE.md) discoverable from work up to the sandbox.
    leaked = [
        str(p / marker)
        for p in (work, *[a for a in work.parents if iso.sandbox.resolve() in (a, *a.parents)])
        for marker in (".git", "CLAUDE.md")
        if _has_child(p, marker)
    ]
    if leaked:
        failures.append(f"repo markers reachable from work: {leaked}")
    else:
        checks["no-repo-markers"] = "none under sandbox"

    # 3 · No CLAUDE.md or memory in the fresh HOME (no inherited agent memory).
    home = iso.home.resolve()
    home_leaks = [
        str(home / rel)
        for rel in ("CLAUDE.md", ".claude/CLAUDE.md")
        if (home / rel).exists()
    ]
    if home_leaks:
        failures.append(f"agent memory present in fresh HOME: {home_leaks}")
    else:
        checks["fresh-home"] = str(home)

    # 4 · The env carries no PYTHONPATH that could import the repo's crm package.
    if iso.env.get("PYTHONPATH"):
        failures.append(f"PYTHONPATH set in agent env: {iso.env['PYTHONPATH']!r}")
    else:
        checks["no-pythonpath"] = "unset"

    # 5 · Positive check: the skill is actually installed in the fresh HOME.
    skill_md = iso.skill_dir / "SKILL.md"
    if not skill_md.is_file():
        failures.append(f"skill not installed: {skill_md} missing")
    else:
        checks["skill-installed"] = str(skill_md)

    if failures:
        raise IsolationError("isolation leak(s) detected:\n  - " + "\n  - ".join(failures))
    return checks
