# pyright: basic
"""Guards for the ReplSkin constant-fold (#543).

The CLI always constructs the skin as the single ``"d365"`` software, which
keyed no software-specific accent or hex, so both lookups were folded to the
default constants. These assert that fold stays behavior-preserving.
"""
from __future__ import annotations

from pathlib import Path

from crm.utils.repl_skin import _display_home_path, _strip_ansi, _visible_len


# ── Pure layout helpers ───────────────────────────────────────────────
# These do the width math behind table/banner alignment; a bug here = mis
# aligned output. Stable + no I/O, so worth a direct unit each.

def test_strip_ansi_removes_color_codes():
    assert _strip_ansi("\033[38;5;75mhi\033[0m") == "hi"


def test_strip_ansi_passes_plain_text_through():
    assert _strip_ansi("plain text") == "plain text"


def test_strip_ansi_removes_multiple_codes():
    assert _strip_ansi("\033[1m\033[38;5;220mA\033[0mB") == "AB"


def test_visible_len_excludes_ansi():
    assert _visible_len("\033[1mabc\033[0m") == 3


def test_visible_len_equals_len_for_plain_text():
    assert _visible_len("hello") == 5


def test_display_home_path_under_home_uses_tilde():
    p = Path.home() / "wip" / "proj"
    assert _display_home_path(str(p)) == "~/wip/proj"


def test_display_home_path_outside_home_is_absolute(tmp_path, monkeypatch):
    # Pin home to an isolated dir so the path under test is provably outside it on
    # every platform — on the Windows CI runner the real tmp_path lives under the
    # user home (C:/Users/<user>/AppData/Local/Temp), so a "/tmp is outside $HOME"
    # assumption is Unix-only.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    outside = tmp_path / "elsewhere"  # sibling of the fake home → not under it
    result = _display_home_path(str(outside))
    assert result == str(outside.resolve())
    assert not result.startswith("~")
