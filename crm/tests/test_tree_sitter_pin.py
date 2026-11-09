"""tree-sitter must stay capped below 0.26 (#1151)."""

import re
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

SETUP = Path(__file__).resolve().parents[2] / "setup.py"


def test_tree_sitter_requirement_is_capped_below_0_26() -> None:
    # Read setup.py, not importlib.metadata: an editable install's metadata is
    # frozen at install time and would not see a worktree's edit.
    specs = re.findall(r'^\s*"(tree-sitter[<>=!~][^"]*)"', SETUP.read_text(), re.M)
    assert len(specs) == 1, specs
    spec = Requirement(specs[0]).specifier
    assert spec.contains(Version("0.25.2"))
    assert not spec.contains(Version("0.26.0"))
