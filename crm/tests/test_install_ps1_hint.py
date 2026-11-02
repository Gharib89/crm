# pyright: basic
"""The install.ps1 uv fallback hint prints the same pinned command the docs show.

Kept apart from test_install_ps1.py, which skips without PowerShell: this is a
text check and runs everywhere. Without the `--python` pin uv can pick a Python
with no prebuilt PyYAML Windows wheel, and the install then needs the MSVC
Build Tools (#1038, #1041).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PINNED_UV_INSTALL = re.compile(
    r"uv tool install --python \d+\.\d+ git\+https://github\.com/Gharib89/crm"
)
DOCS_WITH_WINDOWS_COMMAND = [
    "README.md",
    "docs/getting-started/install.md",
    "docs/getting-started/quickstart.md",
]


@pytest.mark.parametrize("doc", DOCS_WITH_WINDOWS_COMMAND)
def test_install_ps1_uv_hint_matches_documented_windows_command(doc):
    script = (REPO_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")
    documented = set(PINNED_UV_INSTALL.findall((REPO_ROOT / doc).read_text(encoding="utf-8")))
    assert documented, f"{doc} lost its pinned Windows uv command"
    assert set(PINNED_UV_INSTALL.findall(script)) == documented
    assert "uv tool install git+https://github.com/Gharib89/crm" not in script
