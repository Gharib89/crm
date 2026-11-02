"""The install.ps1 uv fallback hint prints the same pinned command the docs show.

Kept apart from test_install_ps1.py, which skips without PowerShell: this is a
text check and runs everywhere. Without the `--python` pin uv can pick a Python
with no prebuilt PyYAML Windows wheel, and the install then needs the MSVC
Build Tools (#1038, #1041).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED = re.compile(r"uv tool install --python \d+\.\d+ git\+https://github\.com/Gharib89/crm")


def test_install_ps1_uv_hint_matches_readme_windows_command():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    script = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    documented = PINNED.search(readme)
    assert documented, "README lost its pinned Windows uv command"
    assert documented.group(0) in script
    assert "uv tool install git+https://github.com/Gharib89/crm" not in script
