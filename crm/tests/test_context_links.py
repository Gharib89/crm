"""CONTEXT.md cross-links must point at real repo paths.

The links are module-granularity (no line anchors), so they survive in-file
edits and break only on a file move/rename — this test turns that break into a
CI failure instead of a silent lie.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_MD = REPO_ROOT / "CONTEXT.md"
LINK_RE = re.compile(r"\]\(([^)]+)\)")


def test_context_md_links_resolve():
    targets = LINK_RE.findall(CONTEXT_MD.read_text(encoding="utf-8"))
    # Only repo-relative links; skip external (http) and in-page (#anchor) ones.
    repo_links = [
        t.split("#", 1)[0]
        for t in targets
        if t and not t.startswith(("http://", "https://", "#"))
    ]
    assert repo_links, "expected CONTEXT.md to carry repo-relative cross-links"
    missing = sorted(t for t in repo_links if not (REPO_ROOT / t).exists())
    assert not missing, f"CONTEXT.md links point at missing paths: {missing}"
