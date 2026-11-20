# pyright: basic
"""Unit tests for scripts/check_comments.py, the ratchet that bans new `#` comments."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_comments.py"

_spec = importlib.util.spec_from_file_location("check_comments", SCRIPT)
assert _spec and _spec.loader
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)  # pyright: ignore[reportAttributeAccessIssue]


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_counts_full_line_and_inline_comments(tmp_path):
    path = _write(tmp_path, "a.py", "# one\nx = 1  # two\ns = '# not a comment'\n")
    assert cc.comment_lines(path) == [1, 2]


def test_pragmas_shebang_and_coding_cookie_are_exempt(tmp_path):
    text = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: utf-8 -*-\n"
        "# pyright: basic\n"
        "import os  # noqa: F401\n"
        "x: int = 'a'  # type: ignore[assignment]\n"
        "y = 1  # pragma: no cover\n"
        "z = 2  # nosemgrep: rule\n"
        "# fmt: off\n"
    )
    assert cc.comment_lines(_write(tmp_path, "a.py", text)) == []


def test_new_file_with_a_comment_fails(tmp_path):
    path = _write(tmp_path, "a.py", "x = 1  # why\n")
    errors = cc.check([path], {}, root=tmp_path)
    assert errors == ["a.py: 1 comment(s), baseline allows 0 (lines 1)"]


def test_file_at_its_baseline_passes(tmp_path):
    path = _write(tmp_path, "a.py", "# a\n# b\n")
    assert cc.check([path], {"a.py": 2}, root=tmp_path) == []


def test_file_above_its_baseline_fails(tmp_path):
    path = _write(tmp_path, "a.py", "# a\n# b\n# c\n")
    assert cc.check([path], {"a.py": 2}, root=tmp_path) == ["a.py: 3 comment(s), baseline allows 2"]


def test_file_below_its_baseline_asks_for_update(tmp_path):
    path = _write(tmp_path, "a.py", "# a\n")
    errors = cc.check([path], {"a.py": 2}, root=tmp_path)
    assert errors == [
        "a.py: 1 comment(s), baseline allows 2: run `python scripts/check_comments.py --update`"
    ]


def test_baseline_entry_for_a_missing_file_fails(tmp_path):
    errors = cc.check([], {"gone.py": 4}, root=tmp_path)
    assert errors == [
        "gone.py: in the baseline but missing: run `python scripts/check_comments.py --update`"
    ]


def test_update_only_lowers_and_drops_clean_or_missing_files(tmp_path):
    _write(tmp_path, "a.py", "# a\n")
    _write(tmp_path, "b.py", "# a\n# b\n# c\n")
    _write(tmp_path, "c.py", "x = 1\n")
    baseline = {"a.py": 3, "b.py": 2, "c.py": 1, "gone.py": 5}
    assert cc.updated(baseline, root=tmp_path) == {"a.py": 1, "b.py": 2}


def test_main_reads_and_writes_the_baseline_file(tmp_path, monkeypatch):
    _write(tmp_path, "a.py", "# a\n")
    baseline = _write(tmp_path, "baseline.json", json.dumps({"a.py": 2}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cc, "BASELINE", baseline)
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    assert cc.main(["a.py"]) == 1
    assert cc.main(["--update"]) == 0
    assert json.loads(baseline.read_text()) == {"a.py": 1}
    assert cc.main(["a.py"]) == 0


def test_list_prints_each_comment_with_its_location(tmp_path, monkeypatch, capsys):
    _write(tmp_path, "a.py", "# one\nx = 1  # two  # noqa: E501\ny = 2  # three\n")
    monkeypatch.chdir(tmp_path)
    assert cc.main(["--list", "a.py"]) == 0
    assert (
        capsys.readouterr().out == "a.py:1: # one\na.py:2: # two  # noqa: E501\na.py:3: # three\n"
    )


def test_update_from_a_subdirectory_keeps_the_baseline(tmp_path, monkeypatch):
    _write(tmp_path, "a.py", "# a\n")
    (tmp_path / "sub").mkdir()
    baseline = _write(tmp_path, "baseline.json", json.dumps({"a.py": 1}))
    monkeypatch.setattr(cc, "BASELINE", baseline)
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path / "sub")
    assert cc.main(["--update"]) == 0
    assert json.loads(baseline.read_text()) == {"a.py": 1}
    assert cc.main(["../a.py"]) == 0


def test_prose_after_a_directive_is_a_comment(tmp_path):
    text = (
        "a = 1  # noqa: E501  # the URL is long\n"
        "b = 2  # noqa: E501 because the URL is long\n"
        "c: int = 'x'  # pyright: ignore[reportAssignmentType]  # one rule\n"
        "d: int = 'x'  # type: ignore[assignment]  # noqa: E501\n"
        "e = 3  # noqa\n"
        "f = 4  # codespell:ignore because the API spells it so\n"
        "g = 5  # codespell:ignore wrod,teh\n"
    )
    assert cc.comment_lines(_write(tmp_path, "a.py", text)) == [1, 2, 3, 6]


def test_a_pyright_file_level_config_is_a_directive(tmp_path):
    text = "# pyright: basic, reportArgumentType=false\n# pyright: reportPrivateUsage=false\n"
    assert cc.comment_lines(_write(tmp_path, "a.py", text)) == []


def test_a_type_comment_is_a_comment(tmp_path):
    path = _write(tmp_path, "a.py", "x = []  # type: list[int]\n")
    assert cc.comment_lines(path) == [1]


def test_shebang_and_coding_cookie_are_exempt_only_on_header_lines(tmp_path):
    header = _write(tmp_path, "a.py", "#!/usr/bin/env python3\n# coding: latin-1\nx = 1\n")
    assert cc.comment_lines(header) == []
    prose = "x = 1  #! prose\ny = 2  # -*- prose -*-\n# -*- coding: utf-8 -*-\n"
    assert cc.comment_lines(_write(tmp_path, "b.py", prose)) == [1, 2, 3]


def test_main_checks_an_extensionless_file_the_hook_selects(tmp_path, monkeypatch):
    _write(tmp_path, "tool", "#!/usr/bin/env python3\n# why\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cc, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    assert cc.main(["tool"]) == 1


def test_a_line_two_cookie_after_code_on_line_one_is_a_comment(tmp_path):
    after_code = _write(tmp_path, "a.py", "x = 1\n# coding: latin-1\n")
    assert cc.comment_lines(after_code) == [2]
    after_comment = _write(tmp_path, "b.py", "#!/usr/bin/env python3\n# coding: latin-1\n")
    assert cc.comment_lines(after_comment) == []


def test_a_pyright_line_with_an_unknown_setting_is_a_comment(tmp_path):
    path = _write(tmp_path, "a.py", "# pyright: reason=legacy\n")
    assert cc.comment_lines(path) == [1]
