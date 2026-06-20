# crm/tests/test_skill_bundle.py
# pyright: basic
"""Structural guards for the shipped agent-skill bundle (crm/skills/)."""
from __future__ import annotations

from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_MD = SKILLS_DIR / "SKILL.md"
REFERENCE_DIR = SKILLS_DIR / "reference"

EXPECTED_REFERENCES = {
    "customization-lifecycle.md",
    "records.md", "metadata.md", "authoring.md", "solutions.md",
    "customizations.md", "automation.md", "security.md", "fieldsec.md",
    "dup.md", "connectionrole.md", "troubleshooting.md", "feedback.md",
}

# Repo-only paths an end user (skill installed without the repo) would not have.
# A hosted docs URL (https://...) is fine; a local repo path is not.
_FORBIDDEN_PATHS = [
    "CONTEXT.md", "docs/adr", "docs/agents", "docs/contributing",
    "docs/how-to", "docs/reference", "](../", "](docs/",
]

SKILL_MD_MAX_LINES = 250


def _skill_files() -> list[Path]:
    return [SKILL_MD, *sorted(REFERENCE_DIR.glob("*.md"))]


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
    import-failure investigation verbs and the on-prem fallback path."""
    text = (REFERENCE_DIR / "solutions.md").read_text(encoding="utf-8")
    for token in (
        "import-result",       # post-mortem: re-fetch + parse a prior ImportJob
        "job-status",          # in-progress monitoring (alias for async get)
        "async list",          # find the operation when the id wasn't captured
        "--against-org",       # pre-import gate
        "components",          # fallback verification: components --diff
        "ImportSolution",      # on-prem sync-action caveat
    ):
        assert token in text, f"solutions.md missing '{token}'"


def test_router_routes_import_failures_to_solutions():
    """#183: SKILL.md's routing row for solutions.md must mention import-failure
    investigation, so agents route there from the router alone."""
    router = SKILL_MD.read_text(encoding="utf-8")
    row = next(
        line for line in router.splitlines() if "reference/solutions.md" in line
    )
    assert "failed import" in row, (
        f"solutions.md routing row lacks import-failure investigation: {row!r}"
    )
