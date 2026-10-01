"""Narrowing helpers for parsed spec (YAML) values.

A leaf module: it imports nothing from `crm`, so any core (`apply`, `plan`, the
per-component cores `apply` drives) can share it without an import cycle.
"""

from __future__ import annotations

from typing import Any, cast


def as_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a spec sub-collection to a list of dicts (empty when absent)."""
    return cast("list[dict[str, Any]]", value) if isinstance(value, list) else []
