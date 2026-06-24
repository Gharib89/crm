# pyright: basic
"""Guards for the ReplSkin constant-fold (#543).

The CLI always constructs the skin as the single ``"d365"`` software, which
keyed no software-specific accent or hex, so both lookups were folded to the
default constants. These assert that fold stays behavior-preserving.
"""
from __future__ import annotations

from crm.utils.repl_skin import ReplSkin, _DEFAULT_ACCENT


def test_d365_accent_is_default():
    assert ReplSkin("d365").accent == _DEFAULT_ACCENT


def test_d365_skill_slug_unaffected_by_alias_fold():
    # software_aliases only ever mapped "iterm2_ctl"; "d365" passes through.
    assert ReplSkin("d365").skill_slug == "d365"
