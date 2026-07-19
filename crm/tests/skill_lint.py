"""Static skill-lint gate: four deterministic, hard-fail conformance rules over
the shipped ``crm`` skill tree (``crm/skills/``), per ADR 0028 (Machine A).

The gate (``test_skill_lint_gate.py``) runs offline in the pytest suite and blocks
PRs beside — not overlapping — the skill-*coverage* gate: coverage reconciles the
skill against the live CLI catalogue; this lint checks the skill's own
text/structure. The four rules:

1. **Self-containment** — no repo-only path references and no markdown link that
   escapes the skill directory (an end user installs the skill *without* the repo,
   so ``docs/**`` / ``CONTEXT.md`` / ``../…`` targets are unreachable).
2. **Internal link integrity** — every ``reference/*.md`` file is reachable from
   the router, every ``reference/<x>.md`` pointer resolves, and no reference file
   is orphaned.
3. **Thinness budgets** — ``SKILL.md`` ≤ :data:`ROUTER_MAX_LINES`, each
   ``reference/*.md`` ≤ :data:`REFERENCE_MAX_LINES` (raw line counts, no tokenizer).
4. **Frontmatter contract** — the router's YAML frontmatter is valid, ``name`` is
   correct, and ``description`` is present and within :data:`DESCRIPTION_MAX_CHARS`.

Each rule is a pure function returning a list of human-readable violation strings
(empty = pass) and carries a ``WAIVED``-style escape hatch: a module-level
``{name: reason}`` dict that exempts a known, reasoned violation.

File discovery is **not** duplicated here — the real tree is found via
:func:`crm.tests.skill_coverage.skill_files` (the coverage gate's single-source
helper), and no CLI-catalogue logic is repeated.
"""

# pyright: basic
from __future__ import annotations

import re
from pathlib import Path

import yaml

from crm.tests.skill_coverage import skill_files

# ── Rule budgets / contract constants ────────────────────────────────────────
ROUTER_MAX_LINES = 300
REFERENCE_MAX_LINES = 250
# Agent Skills frontmatter limit: `description` must be ≤ 1024 characters.
DESCRIPTION_MAX_CHARS = 1024
EXPECTED_SKILL_NAME = "crm"

# Repo-only path fragments an end user (skill installed *without* the repo) cannot
# resolve. A hosted docs URL (``https://…``) is fine; a local repo path is not.
_FORBIDDEN_FRAGMENTS = (
    "CONTEXT.md",
    "docs/adr",
    "docs/agents",
    "docs/contributing",
    "docs/how-to",
    "docs/reference",
)

# Markdown inline-link target: the `(...)` of `[label](target)`.
_MD_LINK = re.compile(r"\]\(([^)]+)\)")
# A `reference/<name>.md` pointer (bare text or inline code) — how the router and
# the reference files cross-link, since this tree uses text pointers not `](…)`.
_REF_POINTER = re.compile(r"reference/([a-z0-9-]+\.md)")


# ── File discovery (delegated — no duplicate walker) ─────────────────────────
def skill_tree(skills_dir: Path | None = None) -> tuple[Path, list[Path]]:
    """Return ``(router, references)`` for the skill tree.

    For the real tree (``skills_dir is None``) discovery is delegated to
    :func:`crm.tests.skill_coverage.skill_files` so there is a single source of
    truth. Pass ``skills_dir`` to point the rules at a synthetic fixture tree
    (``<dir>/SKILL.md`` + ``<dir>/reference/*.md``).
    """
    if skills_dir is None:
        files = skill_files()
        return files[0], files[1:]
    router = skills_dir / "SKILL.md"
    references = sorted((skills_dir / "reference").glob("*.md"))
    return router, references


# ── Rule 1: self-containment ─────────────────────────────────────────────────
def _link_escapes(target: str, source: Path, skills_dir: Path) -> bool:
    """True if a markdown link ``target`` (from ``source``) points outside the
    skill directory. External URLs, ``mailto:``/``tel:``, and in-page anchors are
    self-contained; a local target is resolved relative to ``source`` and must
    stay under ``skills_dir``.
    """
    t = target.strip().split(" ", 1)[0]  # drop an optional "title"
    low = t.lower()
    if "://" in low or low.startswith(("#", "mailto:", "tel:")):
        return False
    local = t.split("#", 1)[0].split("?", 1)[0]
    if not local:
        return False
    if local.startswith("/") or local[1:2] == ":":  # POSIX-absolute or Windows drive
        return True
    resolved = (source.parent / local).resolve()
    try:
        resolved.relative_to(skills_dir.resolve())
        return False
    except ValueError:
        return True


def check_self_containment(
    files: list[Path], waived: dict[str, str] | None = None
) -> list[str]:
    """Flag repo-only path references and skill-dir-escaping markdown links.

    Waiver key is a file name; waiving exempts that whole file.
    """
    waived = SELF_CONTAINMENT_WAIVERS if waived is None else waived
    skills_dir = files[0].parent if files else Path()
    violations: list[str] = []
    for path in files:
        if path.name in waived:
            continue
        text = path.read_text(encoding="utf-8")
        for frag in _FORBIDDEN_FRAGMENTS:
            if frag in text:
                violations.append(f"{path.name}: repo-only path reference {frag!r}")
        for target in _MD_LINK.findall(text):
            if _link_escapes(target, path, skills_dir):
                violations.append(f"{path.name}: link target escapes skill dir: {target!r}")
    return violations


# ── Rule 2: internal link integrity ──────────────────────────────────────────
def check_link_integrity(
    router: Path, references: list[Path], waived: dict[str, str] | None = None
) -> list[str]:
    """Flag orphan reference files (not linked from the router) and dangling
    ``reference/<x>.md`` pointers (target file absent). Waiver key is the
    reference file name.
    """
    waived = LINK_WAIVERS if waived is None else waived
    ref_names = {r.name for r in references}
    violations: list[str] = []

    cited_in_router = set(_REF_POINTER.findall(router.read_text(encoding="utf-8")))
    for name in sorted(ref_names):
        if name not in cited_in_router and name not in waived:
            violations.append(f"orphan reference not linked from {router.name}: reference/{name}")

    for path in [router, *references]:
        for name in sorted(set(_REF_POINTER.findall(path.read_text(encoding="utf-8")))):
            if name not in ref_names and name not in waived:
                violations.append(f"{path.name}: dangling pointer to reference/{name}")
    return violations


# ── Rule 3: thinness budgets ─────────────────────────────────────────────────
def _thinness_checks(router: Path, references: list[Path]) -> list[tuple[Path, int]]:
    return [(router, ROUTER_MAX_LINES), *[(r, REFERENCE_MAX_LINES) for r in references]]


def check_thinness(
    router: Path, references: list[Path], waived: dict[str, str] | None = None
) -> list[str]:
    """Flag files over their line budget. Waiver key is a file name."""
    waived = THINNESS_WAIVERS if waived is None else waived
    violations: list[str] = []
    for path, cap in _thinness_checks(router, references):
        if path.name in waived:
            continue
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > cap:
            violations.append(f"{path.name}: {count} lines (cap {cap})")
    return violations


def stale_thinness_waivers(
    router: Path, references: list[Path], waived: dict[str, str] | None = None
) -> set[str]:
    """Thinness-waiver names that no longer name an over-budget file (so the
    waiver should be dropped) — hygiene mirror of the coverage gate's stale check.
    """
    waived = THINNESS_WAIVERS if waived is None else waived
    over_budget = {
        path.name
        for path, cap in _thinness_checks(router, references)
        if len(path.read_text(encoding="utf-8").splitlines()) > cap
    }
    return {name for name in waived if name not in over_budget}


# ── Rule 4: frontmatter contract ─────────────────────────────────────────────
def _extract_frontmatter(text: str) -> str | None:
    """Return the raw YAML between the leading ``---`` fences, or ``None`` if the
    file has no opening fence or the block is unterminated.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    body: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            return "\n".join(body)
        body.append(line)
    return None


def check_frontmatter(
    router: Path,
    expected_name: str = EXPECTED_SKILL_NAME,
    waived: dict[str, str] | None = None,
) -> list[str]:
    """Validate the router frontmatter: parseable YAML mapping, correct ``name``,
    and a present ``description`` within the char cap. Waiver key is the router
    file name (exempts the whole contract).
    """
    waived = FRONTMATTER_WAIVERS if waived is None else waived
    if router.name in waived:
        return []
    raw = _extract_frontmatter(router.read_text(encoding="utf-8"))
    if raw is None:
        return [f"{router.name}: missing or unterminated YAML frontmatter"]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [f"{router.name}: invalid YAML frontmatter ({exc.__class__.__name__})"]
    if not isinstance(data, dict):
        return [f"{router.name}: frontmatter is not a YAML mapping"]

    violations: list[str] = []
    name = data.get("name")
    if name != expected_name:
        violations.append(f"{router.name}: frontmatter name is {name!r}, expected {expected_name!r}")
    desc = data.get("description")
    if not isinstance(desc, str) or not desc.strip():
        violations.append(f"{router.name}: frontmatter description missing or empty")
    elif len(desc) > DESCRIPTION_MAX_CHARS:
        violations.append(
            f"{router.name}: description is {len(desc)} chars (cap {DESCRIPTION_MAX_CHARS})"
        )
    return violations


# ── Waivers (reasoned escape hatches; one dict per rule) ─────────────────────
# Each key names the exempt file (or reference target); the value is the reason.
# Keep every entry live: the gate's hygiene tests reject stale/blank waivers.
SELF_CONTAINMENT_WAIVERS: dict[str, str] = {}
LINK_WAIVERS: dict[str, str] = {}
FRONTMATTER_WAIVERS: dict[str, str] = {}

# These reference files predate the 250-line budget and are intentionally broad;
# trimming them is skill-content work tracked separately (not a lint concern).
THINNESS_WAIVERS: dict[str, str] = {
    "authoring.md": "form/view/sitemap authoring spans a broad UI-customization surface",
    "solutions.md": "solution lifecycle + import-failure investigation (#183) is intentionally comprehensive",
    "records.md": "core CRUD/query/relationships/actions — the most-used surface, kept whole",
    "automation.md": "plug-in and workflow registration cover two distinct subsystems",
    "metadata.md": "entity/attribute/relationship metadata browsing is a wide read surface",
}
