# crm/tests/test_skill_bundle.py
# pyright: basic
"""Structural guards for the shipped agent-skill bundle (crm/skills/), its
plugin-manifest discoverability, and the internal-only marking of the repo's
dev skills (.claude/skills/).
"""

from __future__ import annotations

import json
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_MD = SKILLS_DIR / "SKILL.md"
REFERENCE_DIR = SKILLS_DIR / "reference"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
INTERNAL_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

EXPECTED_REFERENCES = {
    "setup.md",
    "customization-lifecycle.md",
    "customizations-as-code.md",
    "records.md",
    "bulk.md",
    "metadata.md",
    "metadata-writes.md",
    "authoring.md",
    "solutions.md",
    "apps-sitemap.md",
    "forms.md",
    "webresource-ribbon.md",
    "charts-dashboards.md",
    "themes-reports.md",
    "automation.md",
    "workflow-xaml.md",
    "security.md",
    "fieldsec.md",
    "dup.md",
    "connectionrole.md",
    "troubleshooting.md",
    "feedback.md",
}

# Repo-only paths an end user (skill installed without the repo) would not have.
# A hosted docs URL (https://...) is fine; a local repo path is not.
_FORBIDDEN_PATHS = [
    "CONTEXT.md",
    "docs/adr",
    "docs/agents",
    "docs/contributing",
    "docs/how-to",
    "docs/reference",
    "](../",
    "](docs/",
]

SKILL_MD_MAX_LINES = 250


def _skill_files() -> list[Path]:
    return [SKILL_MD, *sorted(REFERENCE_DIR.glob("*.md"))]


def _frontmatter_name(skill_md: Path) -> str | None:
    """The `name:` value from a SKILL.md YAML frontmatter block, if present."""
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() == "name":
            return value.strip()
    return None


def _frontmatter_has_internal_flag(skill_md: Path) -> bool:
    """True if the SKILL.md frontmatter carries `metadata.internal: true`."""
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    in_metadata = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.strip() and line[:1] not in (" ", "\t"):  # top-level key
            in_metadata = line.split(":", 1)[0].strip() == "metadata"
            continue
        if in_metadata and line.strip() == "internal: true":
            return True
    return False


def _skills_discoverable_via_manifest(manifest: dict) -> set[str]:
    """Skill names the `vercel-labs/skills` tool would discover from this
    manifest's `skills` array.

    The tool resolves each entry to ``dirname(entry)`` and scans that
    directory's *child* directories for a ``SKILL.md`` — a ``SKILL.md`` sitting
    directly in the resolved dir is NOT matched (verified against skills@1.5.17).
    Encoding the real rule here means the test fails if the manifest is
    "corrected" to point at the SKILL.md file instead of its container. See #868.
    """
    found: set[str] = set()
    for entry in manifest.get("skills", []):
        if not isinstance(entry, str) or not entry.startswith("./"):
            continue
        search_dir = (REPO_ROOT / entry).parent  # dirname(join(root, entry))
        if not search_dir.is_dir():
            continue
        for child in sorted(search_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.is_file():
                name = _frontmatter_name(skill_md)
                if name:
                    found.add(name)
    return found


def test_plugin_manifest_valid():
    """The root plugin manifest exists and declares the crm skill by a
    tool-valid relative path (must start with './').
    """
    assert PLUGIN_MANIFEST.is_file(), ".claude-plugin/plugin.json is missing"
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert manifest.get("name") == "crm", "plugin manifest name must be 'crm'"
    skills = manifest.get("skills")
    assert isinstance(skills, list) and skills, "manifest must declare a non-empty 'skills' array"
    assert "./crm/skills" in skills, (
        "manifest must point at the canonical ./crm/skills container (single source of truth)"
    )
    for entry in skills:
        assert isinstance(entry, str) and entry.startswith("./"), (
            f"skills entry {entry!r} must be a relative path starting with './'"
        )


def test_plugin_manifest_makes_crm_skill_discoverable():
    """#868: `npx skills add Gharib89/crm --skill crm` must resolve to the
    shipped crm skill. The manifest must point at the container dir
    (`./crm/skills`, whose child `crm/skills/` holds SKILL.md), NOT at the
    `SKILL.md` file itself (which discovers nothing under the tool's rule).
    """
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    discoverable = _skills_discoverable_via_manifest(manifest)
    assert "crm" in discoverable, (
        f"crm skill not discoverable via plugin.json skills={manifest.get('skills')!r}; "
        f"discoverable={sorted(discoverable)}"
    )


def test_internal_dev_skills_marked_internal():
    """#868: every dev skill under .claude/skills must carry
    metadata.internal: true so a bare `npx skills add Gharib89/crm` hides them
    from end users (revealed only with INSTALL_INTERNAL_SKILLS=1). Vendored
    copies have the flag re-stamped by scripts/sync-skills.py on each sync.
    """
    skills = sorted(INTERNAL_SKILLS_DIR.glob("*/SKILL.md"))
    assert skills, f"no skills found under {INTERNAL_SKILLS_DIR}"
    missing = [p.parent.name for p in skills if not _frontmatter_has_internal_flag(p)]
    assert not missing, f".claude/skills entries missing metadata.internal: true: {missing}"


def test_router_is_thin():
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= SKILL_MD_MAX_LINES, (
        f"SKILL.md is {len(lines)} lines (cap {SKILL_MD_MAX_LINES})"
    )


def test_expected_reference_files_present():
    present = {p.name for p in REFERENCE_DIR.glob("*.md")}
    assert present == EXPECTED_REFERENCES, (
        f"reference file mismatch — missing: {sorted(EXPECTED_REFERENCES - present)}, "
        f"extra: {sorted(present - EXPECTED_REFERENCES)}"
    )


def test_every_reference_is_linked_from_router():
    router = SKILL_MD.read_text(encoding="utf-8")
    for name in sorted(EXPECTED_REFERENCES):
        assert f"reference/{name}" in router, f"{name} not linked from SKILL.md"


def test_no_repo_only_paths_in_shipped_skill():
    for f in _skill_files():
        text = f.read_text(encoding="utf-8")
        for bad in _FORBIDDEN_PATHS:
            assert bad not in text, f"{f.name} references repo-only path '{bad}'"


def test_solutions_reference_covers_import_investigation():
    """#183: an agent reading only the installed skill must discover the
    import-failure investigation verbs and the on-prem fallback path.
    """
    text = (REFERENCE_DIR / "solutions.md").read_text(encoding="utf-8")
    for token in (
        "import-result",  # post-mortem: re-fetch + parse a prior ImportJob
        "job-status",  # in-progress monitoring (alias for async get)
        "async list",  # find the operation when the id wasn't captured
        "--against-org",  # pre-import gate
        "components",  # fallback verification: components --diff
        "ImportSolution",  # on-prem sync-action caveat
    ):
        assert token in text, f"solutions.md missing '{token}'"


def test_router_routes_import_failures_to_solutions():
    """#183: SKILL.md's routing row for solutions.md must mention import-failure
    investigation, so agents route there from the router alone.
    """
    router = SKILL_MD.read_text(encoding="utf-8")
    row = next(line for line in router.splitlines() if "reference/solutions.md" in line)
    assert "failed import" in row, (
        f"solutions.md routing row lacks import-failure investigation: {row!r}"
    )
