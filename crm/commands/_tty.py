# pyright: basic
"""Leaf module for the stdin-TTY probe.

Lives apart from `cli` and `_helpers` so both — plus the profile/skill commands —
can import `_stdin_is_tty` without the `cli ↔ _helpers` import cycle that the old
function-body re-import worked around. Imports nothing from `crm.cli`.
"""
from __future__ import annotations

import sys


def _stdin_is_tty() -> bool:
    """True only if stdin is an interactive terminal. A missing, closed, or
    isatty-less stdin (frozen build, piped/redirected input) counts as
    non-interactive — agents and CI never attach a TTY."""
    return _is_tty(sys.stdin)


def _stdout_is_tty() -> bool:
    """True only if stdout is an interactive terminal — the gate for human-only
    output (e.g. next-step hints) that must never land in piped/redirected output
    an agent or script is capturing."""
    return _is_tty(sys.stdout)


def _is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if isatty is None:
        return False
    try:
        return bool(isatty())
    except (ValueError, OSError):
        return False
