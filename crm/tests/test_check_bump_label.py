# pyright: basic
"""Unit tests for scripts/check_bump_label.py — the bump-guard gate that fails a
PR whose Conventional-Commit title implies a minor/major bump unless the matching
opt-in label (`minor` / `major`) is present. See ADR 0011 and issue #398."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_bump_label.py"

_spec = importlib.util.spec_from_file_location("check_bump_label", SCRIPT)
assert _spec and _spec.loader
cbl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cbl)  # pyright: ignore[reportAttributeAccessIssue]


# --- required_label: what bump a title/body implies -------------------------

@pytest.mark.parametrize(
    "title",
    ["fix: x", "perf: speed", "docs: readme", "chore: deps",
     "refactor: tidy", "test: cover", "build: spec", "ci: yaml",
     "fix(query): scope", "revert: bad"],
)
def test_patch_types_need_no_label(title):
    assert cbl.required_label(title) is None


@pytest.mark.parametrize("title", ["feat: thing", "feat(query): thing"])
def test_feat_requires_minor(title):
    assert cbl.required_label(title) == "minor"


@pytest.mark.parametrize(
    "title", ["feat!: thing", "feat(query)!: thing", "fix!: thing", "perf!: x"]
)
def test_bang_requires_major(title):
    assert cbl.required_label(title) == "major"


def test_breaking_change_footer_in_body_requires_major():
    assert cbl.required_label("fix: thing", "body\n\nBREAKING CHANGE: gone") == "major"


def test_invalid_title_raises():
    with pytest.raises(ValueError):
        cbl.required_label("not a conventional commit")


# --- check: title + body + labels -> (exit_code, message) -------------------

def test_feat_without_minor_label_fails():
    code, msg = cbl.check("feat: thing", "", [])
    assert code == 1 and "minor" in msg


def test_feat_with_minor_label_passes():
    code, _ = cbl.check("feat: thing", "", ["minor"])
    assert code == 0


def test_feat_with_major_label_passes():
    # major is a superset of minor — labelling a feat `major` is intentional.
    code, _ = cbl.check("feat: thing", "", ["major"])
    assert code == 0


def test_breaking_with_only_minor_label_fails():
    code, msg = cbl.check("feat!: thing", "", ["minor"])
    assert code == 1 and "major" in msg


def test_breaking_with_major_label_passes():
    code, _ = cbl.check("feat!: thing", "", ["major"])
    assert code == 0


def test_patch_title_passes_with_no_labels():
    code, _ = cbl.check("fix: thing", "", [])
    assert code == 0


def test_invalid_title_fails_with_message():
    code, msg = cbl.check("garbage", "", [])
    assert code == 1 and "Conventional Commit" in msg
