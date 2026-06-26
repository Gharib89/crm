# pyright: basic
"""Guards for the ReplSkin constant-fold (#543).

The CLI always constructs the skin as the single ``"d365"`` software, which
keyed no software-specific accent or hex, so both lookups were folded to the
default constants. These assert that fold stays behavior-preserving.
"""
from __future__ import annotations

