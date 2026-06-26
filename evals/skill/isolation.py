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
- the agent's environment is scrubbed of anything pointing back at the repo;
- *only* the Claude Code subscription credentials are copied into the fresh ``HOME`` so
  a headless ``claude -p`` agent authenticates without an ``ANTHROPIC_API_KEY`` — auth
  is orthogonal to repo isolation, and nothing else from the real config dir (no
  ``CLAUDE.md``, no memory) rides along.

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
import sys
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


def _real_claude_config_dir() -> Path:
    """The maintainer's *real* Claude Code config dir, read from the unscrubbed
    environment (``CLAUDE_CONFIG_DIR`` if set, else ``$HOME/.claude``). This is where
    Claude Code keeps the subscription credentials we pass through — resolved before
    the sandbox repoints ``HOME``."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def _passthrough_claude_auth(sandbox_home: Path) -> Path | None:
    """Copy *only* the Claude Code credentials file into the sandbox ``HOME`` so an
    isolated ``claude -p`` authenticates via the maintainer's subscription without an
    ``ANTHROPIC_API_KEY``.

    The credentials file alone is the minimal auth artifact — it carries no repo state
    and no agent memory, so the isolation invariants (no repo, no ``CLAUDE.md``, no
    inherited memory) stay intact. We deliberately do *not* pass ``CLAUDE_CONFIG_DIR``
    through: that env relocates the agent's *entire* config — ``CLAUDE.md`` and memory
    included — back onto the real dir, re-exposing global memory. It is scrubbed in
    :func:`provision_isolation` and asserted absent by :func:`verify_isolation`.

    Best-effort and a no-op on failure: when the maintainer has no credentials file
    (e.g. an API-key-only setup) *or* the copy can't complete (unreadable creds,
    unwritable sandbox ``HOME``), the agent simply falls back to ``ANTHROPIC_API_KEY``
    exactly as before. It must never raise — provisioning runs this before the caller
    can clean up, so a raise here would abort the run and leak the sandbox. Returns the
    copied path, or ``None``.

    ponytail: a copy, not a symlink — a mid-run OAuth token refresh writes only to the
    throwaway copy and is discarded on cleanup, never mutating the real credentials.
    Fine for a minutes-long eval; revisit only if a run could outlive the access token.
    """
    src = _real_claude_config_dir() / ".credentials.json"
    if not src.is_file():
        return None
    try:
        dest_dir = sandbox_home / ".claude"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / ".credentials.json"
        shutil.copy2(src, dest)
        return dest
    except OSError as exc:
        print(f"[isolation] could not pass through Claude credentials: {exc}", file=sys.stderr)
        return None


def provision_isolation(crm_bin: str | None = None, *, install_skill: bool = True) -> Isolation:
    """Create a sandbox and install the skill into a fresh HOME via ``crm skill install``.

    Returns an :class:`Isolation` whose ``env`` launches the agent with no path back
    to the repo. The caller is responsible for :meth:`Isolation.cleanup`.

    ``install_skill=False`` provisions the **counterfactual** (skill-absent) leg of a
    ``--counterfactual`` run (#588): the skill is deliberately *not* installed, so the
    sandbox needs no ``crm`` binary at all. Pair it with
    ``verify_isolation(expect_skill=False)`` to assert the skill is genuinely absent.
    """
    if install_skill:
        crm_bin = crm_bin or _crm_bin()
    sandbox = Path(tempfile.mkdtemp(prefix="crm-eval-"))
    home = sandbox / "home"
    work = sandbox / "work"
    crm_home = sandbox / "crm_home"
    for d in (home, work, crm_home):
        d.mkdir(parents=True)

    skill_dir = home / ".claude" / "skills" / "crm"

    # Scrub the environment of anything that could lead back to the repo *or* the
    # maintainer's real agent config: fresh HOME and CRM_HOME, no PYTHONPATH (which
    # could put the repo's `crm/` package on the import path), no inherited CLAUDE.md
    # pointers, and no CLAUDE_CONFIG_DIR (it would relocate the agent's whole config —
    # CLAUDE.md and memory included — onto the real dir, re-exposing global memory; we
    # pass *only* the credentials file through instead, below). Built *before* the
    # install so the skill-install subprocess's own writes (skill_registry.record_install
    # → installed-skills.json) land in the throwaway CRM_HOME, never the caller's real
    # state — otherwise the harness would pollute the maintainer's profile/config.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONPATH", "CLAUDE_PROJECT_DIR", "CLAUDE_CONFIG_DIR")
    }
    env["HOME"] = str(home)
    env["CRM_HOME"] = str(crm_home)

    # Pass the subscription credentials (only) into the fresh HOME so a headless
    # `claude -p` agent authenticates without an ANTHROPIC_API_KEY. No-op if absent.
    _passthrough_claude_auth(home)

    # Install the skill the way a user does — from whatever `crm` is on PATH, so the
    # eval exercises the skill *as shipped* in that binary, not the repo's working
    # tree. --dest pins the location; --force makes it idempotent across re-runs. The
    # counterfactual (skill-absent) leg skips this entirely (#588).
    if install_skill:
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


def verify_isolation(
    iso: Isolation, root: Path | None = None, *, expect_skill: bool = True
) -> dict[str, str]:
    """Assert the agent has no path to the repo and the skill is installed.

    Returns a dict of passed check-name → detail. Raises :class:`IsolationError`
    naming every failed check, so a leak fails loudly before any agent runs.

    ``expect_skill=False`` flips check 5 for the **counterfactual** (skill-absent) leg:
    the skill must be *absent*, so a skill leaking in (which would invalidate the lift
    measurement) fails loudly just as a missing skill does on the normal leg.
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

    # 5 · The skill is present (normal leg) or absent (counterfactual leg). Either
    # expectation, the wrong state fails loudly: a missing skill invalidates the normal
    # measurement, a leaked skill invalidates the skill-absent (lift) measurement.
    skill_md = iso.skill_dir / "SKILL.md"
    if expect_skill:
        if not skill_md.is_file():
            failures.append(f"skill not installed: {skill_md} missing")
        else:
            checks["skill-installed"] = str(skill_md)
    else:
        if skill_md.is_file():
            failures.append(f"skill present in counterfactual (skill-absent) leg: {skill_md}")
        else:
            checks["skill-absent"] = str(skill_md)

    # 6 · The env must not carry CLAUDE_CONFIG_DIR pointing at the real config dir.
    # Auth is passed by copying *only* the credentials file into the fresh HOME (see
    # `_passthrough_claude_auth`); CLAUDE_CONFIG_DIR is scrubbed because it would also
    # relocate CLAUDE.md and memory back onto the real dir. Guard the leak so a future
    # change that stops scrubbing it (or points it elsewhere) fails loudly here: if
    # set, it must live inside the sandbox and expose no CLAUDE.md / memory.
    cfg = iso.env.get("CLAUDE_CONFIG_DIR")
    if cfg:
        cfg_path = Path(cfg).resolve()
        cfg_leaks = [str(cfg_path / m) for m in ("CLAUDE.md", "memory") if (cfg_path / m).exists()]
        in_sandbox = iso.sandbox.resolve() in (cfg_path, *cfg_path.parents)
        if cfg_leaks or not in_sandbox:
            failures.append(
                f"CLAUDE_CONFIG_DIR points outside the sandbox or at agent memory: "
                f"{cfg!r} {cfg_leaks}"
            )
        else:
            checks["claude-config-sandboxed"] = cfg
    else:
        checks["no-claude-config-leak"] = "unset"

    if failures:
        raise IsolationError("isolation leak(s) detected:\n  - " + "\n  - ".join(failures))
    return checks
