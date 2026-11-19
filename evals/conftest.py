"""Collection rules for the offline eval tests.

The harness is POSIX-only: the runner kills a process group with `os.killpg`,
`provision` runs shell scripts, and the byte-for-byte fixtures hash differently
once a Windows checkout rewrites their line endings. CI's `windows-latest` leg
therefore skips this tree; the `ubuntu-22.04` leg is the gate (#1333).
"""

import sys

collect_ignore_glob = ["*"] if sys.platform == "win32" else []
