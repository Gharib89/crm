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


def test_display_home_path_outside_home_is_absolute(monkeypatch, tmp_path):
    home = tmp_path / "home"
    outside_home = tmp_path / "outside-home"
    home.mkdir()
    outside_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)

    result = _display_home_path(str(outside_home))

    assert result == str(outside_home.resolve())
    assert not result.startswith("~")
