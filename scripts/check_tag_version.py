#!/usr/bin/env python3
"""Assert that the git tag passed as argv[1] (e.g. 'v1.0.1') matches the
version declared in setup.py. Fails with exit code 1 on mismatch."""
import re
import sys
from pathlib import Path

SETUP_PY = Path(__file__).resolve().parent.parent / "setup.py"


def setup_version() -> str:
    text = SETUP_PY.read_text()
    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        sys.exit("setup.py: version= not found")
    return match.group(1)


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit("usage: check_tag_version.py <git-tag>")
    tag = sys.argv[1]
    if not tag.startswith("v"):
        sys.exit(f"tag {tag!r} must start with 'v'")
    tag_version = tag[1:]
    pkg_version = setup_version()
    if tag_version != pkg_version:
        sys.exit(
            f"tag version {tag_version!r} != setup.py version {pkg_version!r}"
        )
    print(f"OK: tag {tag} matches setup.py version {pkg_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
