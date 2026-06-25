# pyright: basic
"""CONTEXT.md cross-links must point at real, repo-relative paths.

The links are module-granularity (no line anchors), so they survive in-file
edits and break only on a file move/rename -- this test turns that break into a
CI failure instead of a silent lie. Repo-relative is enforced too: an absolute
or parent-escaping target is rejected even if it happens to exist on the runner.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTEXT_MD = REPO_ROOT / "CONTEXT.md"
LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _is_valid_repo_link(target: str) -> bool:
    if target.startswith("/"):  # absolute -- not a repo-relative link
        return False
    resolved = (REPO_ROOT / target).resolve()
    # `..` could resolve to a real path outside the repo; reject that too.
    return resolved.is_relative_to(REPO_ROOT) and resolved.exists()


def test_context_md_links_resolve():
    targets = LINK_RE.findall(CONTEXT_MD.read_text(encoding="utf-8"))
    # Only repo-relative links; skip external (http) and in-page (#anchor) ones.
    repo_links = [
        t.split("#", 1)[0]
        for t in targets
        if t and not t.startswith(("http://", "https://", "#"))
    ]
    assert repo_links, "expected CONTEXT.md to carry repo-relative cross-links"
    bad = sorted(t for t in repo_links if not _is_valid_repo_link(t))
    assert not bad, f"CONTEXT.md links are missing, absolute, or escape the repo: {bad}"
