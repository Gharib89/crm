#!/usr/bin/env python3
"""Vendor personal skills into the project's tracked .claude/skills/ tree.

Source of truth is the user-level skills dir (`~/.claude/skills`), itself a
mirror of an external skills repo (e.g. `npx skills add mattpocock/skills`) that
gets reinstalled wholesale. So the project copies are fully *derived*: this tool
replaces each listed skill's directory verbatim, then re-applies the one
project-owned divergence we allow — the model-invocation flag. The same flag is
stripped in the user-dir source copy too, so a `model_invokable` skill is
invokable in the local interactive session (where personal skills shadow the
project copies), not just in the cloud sandbox.

Run locally after refreshing `~/.claude/skills`, then commit the resulting
`.claude/skills/**` changes. NOT run in CI/cloud (it reads your home dir); the
committed copies are what ship to the cloud-ship sandbox, where personal skills
are absent and these project copies are the ones that load.

Rule of thumb: any skill the cloud-ship chain composes (ship -> tdd/code-review;
docs-sync -> writing-great-skills) must be model-invokable, so mark it
`model_invokable: True`. A user-only skill (`disable-model-invocation: true`)
cannot be invoked by the model or preloaded into a subagent, which would break
the routine. Everything else keeps whatever flag it ships with upstream.

Dependency closure: a skill that composes another (referenced as `/other` or
`` `other` `` in its SKILL.md) breaks at runtime if that sibling is absent from
the clone. So after seeding from SYNC, the tool transitively pulls every
referenced skill in too. Auto-pulled deps keep their upstream flag.

Skills NOT reachable from this list (ship, cloud-ship, merge-gate, live-e2e,
...) are project-native and never touched.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import TypedDict

SRC = Path("~/.claude/skills").expanduser()
DST = Path(__file__).resolve().parent.parent / ".claude" / "skills"

_SKILL_FILE = "SKILL.md"


class SkillEntry(TypedDict):
    name: str
    model_invokable: bool


# One line per vendored skill. `model_invokable: True` strips
# `disable-model-invocation` from the project copy's frontmatter. Dependencies
# are resolved and copied automatically; list a skill here only to seed the set
# or to force its invocation flag.
SYNC: list[SkillEntry] = [
    {"name": "ask-matt", "model_invokable": False},
    {"name": "blindspot", "model_invokable": False},
    {"name": "codebase-design", "model_invokable": False},
    {"name": "code-review", "model_invokable": True},
    {"name": "domain-modeling", "model_invokable": False},
    {"name": "grill-me", "model_invokable": False},
    {"name": "grill-with-docs", "model_invokable": False},
    {"name": "grilling", "model_invokable": False},
    {"name": "implement", "model_invokable": False},
    {"name": "qa", "model_invokable": False},
    {"name": "quiz-before-merge", "model_invokable": False},
    {"name": "research", "model_invokable": False},
    {"name": "tdd", "model_invokable": True},
    {"name": "to-spec", "model_invokable": False},
    {"name": "to-tickets", "model_invokable": False},
    {"name": "triage", "model_invokable": False},
    {"name": "wayfinder", "model_invokable": False},
    {"name": "writing-great-skills", "model_invokable": True},
]

# Skills that appear as references (footer/menu links) but are never a real
# runtime dependency, so we don't vendor them. `setup-matt-pocock-skills` is an
# installer meta-skill (runs `npx skills add …`) linked from many footers —
# pointless and a footgun inside a repo clone.
EXCLUDE = {"setup-matt-pocock-skills"}

# Hand-authored in this repo — `.claude/skills/` IS their source of truth. Never
# vendor over these: a same-named personal skill (via SYNC or a dependency
# reference) must never `rmtree` the tracked copy and destroy project edits.
PROJECT_NATIVE = {"ship", "cloud-ship", "merge-gate", "live-e2e"}

# A backticked `/name` or `name` token that matches a known skill directory.
_REF = re.compile(r"`/?([a-z][a-z0-9-]+)`")


def find_refs(skill_dir: Path, universe: set[str]) -> set[str]:
    """Skill names this skill references (composes/invokes) in its markdown."""
    refs: set[str] = set()
    for md in skill_dir.rglob("*.md"):
        for token in _REF.findall(md.read_text(encoding="utf-8")):
            if token in universe:
                refs.add(token)
    return refs


def resolve_closure(seed: dict[str, bool], universe: set[str]) -> tuple[dict[str, bool], set[str]]:
    """Transitively add every referenced skill. Auto-added deps default to
    keeping their upstream flag (model_invokable=False). Returns (name ->
    force-invokable, set-of-auto-added-names).
    """
    wanted = dict(seed)
    auto: set[str] = set()
    stack = list(seed)
    while stack:
        name = stack.pop()
        src = SRC / name
        if not src.is_dir():
            continue
        for dep in find_refs(src, universe) - set(wanted) - EXCLUDE - PROJECT_NATIVE:
            wanted[dep] = False
            auto.add(dep)
            stack.append(dep)
    return wanted, auto


def strip_model_invocation_flag(skill_md: Path) -> bool:
    """Remove `disable-model-invocation` from the YAML frontmatter only.

    The body must be left untouched — e.g. writing-great-skills' prose literally
    contains the string `disable-model-invocation: true`. Returns True if a line
    was removed.
    """
    lines = skill_md.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return False
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return False
    kept = [
        ln
        for i, ln in enumerate(lines)
        if not (1 <= i < end and ln.split(":", 1)[0].strip() == "disable-model-invocation")
    ]
    if len(kept) != len(lines):
        skill_md.write_text("".join(kept), encoding="utf-8")
        return True
    return False


def stamp_internal_flag(skill_md: Path) -> bool:
    """Add `metadata.internal: true` to the YAML frontmatter if absent.

    The `vercel-labs/skills` installer hides a skill from normal discovery when
    its frontmatter carries `metadata.internal: true` (revealed only with
    `INSTALL_INTERNAL_SKILLS=1`). These vendored copies are dev tooling, not
    end-user skills, so every synced copy must carry the flag — and since the
    upstream source lacks it, the flag has to be re-stamped on each sync (a plain
    hand-edit is wiped by the rmtree+copytree in main). Idempotent; touches only
    the frontmatter, never the body. Returns True if a line was added.
    """
    lines = skill_md.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return False
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return False
    # A top-level `metadata:` key inside the frontmatter, if one already exists.
    meta_idx = next(
        (
            i
            for i in range(1, end)
            if lines[i][:1] not in (" ", "\t") and lines[i].split(":", 1)[0].strip() == "metadata"
        ),
        None,
    )
    if meta_idx is None:
        # No metadata block — append one just before the closing `---`.
        if not lines[end - 1].endswith("\n"):
            lines[end - 1] += "\n"
        lines.insert(end, "metadata:\n  internal: true\n")
        skill_md.write_text("".join(lines), encoding="utf-8")
        return True
    # metadata block exists — find an existing `internal:` child, if any, so we
    # never leave a duplicate key: normalize a wrong value, keep a correct one.
    j = meta_idx + 1
    while j < end and (not lines[j].strip() or lines[j][:1] in (" ", "\t")):
        if lines[j].split(":", 1)[0].strip() == "internal":
            if lines[j].strip() == "internal: true":
                return False
            indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
            lines[j] = f"{indent}internal: true\n"
            skill_md.write_text("".join(lines), encoding="utf-8")
            return True
        j += 1
    lines.insert(meta_idx + 1, "  internal: true\n")
    skill_md.write_text("".join(lines), encoding="utf-8")
    return True


def _apply_flags(src: Path, dst: Path, force_invokable: bool, is_dep: bool) -> str:
    """Re-apply the project-owned frontmatter divergences to a freshly copied
    vendored skill; return a human-readable tag describing what changed.
    """
    tag = " [dep]" if is_dep else ""
    if force_invokable:
        stripped = strip_model_invocation_flag(dst / _SKILL_FILE)
        stripped |= strip_model_invocation_flag(src / _SKILL_FILE)
        if stripped:
            tag += " (stripped disable-model-invocation)"
    # Every vendored copy is internal dev tooling — hide it from end-user
    # `npx skills add` discovery (stamped on the tracked copy only, never SRC).
    if stamp_internal_flag(dst / _SKILL_FILE):
        tag += " (marked internal)"
    return tag


def main() -> int:
    if not SRC.is_dir():
        print(f"error: source skills dir not found: {SRC}", file=sys.stderr)
        return 1

    universe = {p.name for p in SRC.iterdir() if p.is_dir()}
    seed = {e["name"]: e["model_invokable"] for e in SYNC}
    wanted, auto = resolve_closure(seed, universe)

    # Hard guard: refuse to vendor over a project-native skill, even if one was
    # named in SYNC. resolve_closure already skips them as deps; this catches a
    # direct SYNC edit before any rmtree runs.
    clash = PROJECT_NATIVE & set(wanted)
    if clash:
        print(
            f"error: refusing to overwrite project-native skill(s): {', '.join(sorted(clash))}",
            file=sys.stderr,
        )
        return 1

    DST.mkdir(parents=True, exist_ok=True)
    missing: list[str] = []
    for name in sorted(wanted):
        src, dst = SRC / name, DST / name
        if not src.is_dir():
            missing.append(name)
            continue
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print(f"synced {name}{_apply_flags(src, dst, wanted[name], name in auto)}")

    if missing:
        print(f"\nerror: not found under {SRC}: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
