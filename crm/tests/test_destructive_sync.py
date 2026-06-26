# pyright: basic
"""Drift guard: assert that the hook's inline DESTRUCTIVE/ROLE_VERBS copies stay
aligned with the canonical copies in crm/core/destructive.py.

The PreToolUse hook (.claude/hooks/destructive_op_gate.py) is a pure-stdlib
script run by the system Python — it cannot import crm.  It therefore keeps its
own inline copies of the maps.  This test loads the hook by file path (not as a
package) and compares both maps against the canonical ones so a one-sided edit is
caught immediately by CI.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import crm.core.destructive as core

# Load the hook as a module by its absolute path — it is NOT an importable package.
_HOOK_PATH = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "destructive_op_gate.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("destructive_op_gate", _HOOK_PATH)
    assert spec is not None and spec.loader is not None, f"Cannot load hook from {_HOOK_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_hook = _load_hook()


class TestSyncWithHook:
    """The canonical maps in crm/core/destructive.py must match the hook's copies."""

    def test_destructive_maps_match(self):
        assert _hook.DESTRUCTIVE == core.DESTRUCTIVE, (
            "DESTRUCTIVE map in destructive_op_gate.py has drifted from "
            "crm/core/destructive.py — update the hook copy to match the canonical one."
        )

    def test_role_verbs_match(self):
        assert _hook.ROLE_VERBS == core.ROLE_VERBS, (
            "ROLE_VERBS set in destructive_op_gate.py has drifted from "
            "crm/core/destructive.py — update the hook copy to match the canonical one."
        )


class TestIsDestructive:
    """Behaviour assertions for crm.core.destructive.is_destructive()."""

    def test_metadata_delete_entity_is_destructive(self):
        assert core.is_destructive("metadata", "delete-entity") is True

    def test_entity_delete_is_destructive(self):
        assert core.is_destructive("entity", "delete") is True

    @pytest.mark.parametrize("group,verb", [
        ("other-group", "assign-role"),  # ROLE_VERBS gate by verb name, any group
        ("security", "delete-role"),
    ])
    def test_role_verb_regardless_of_group(self, group, verb):
        assert core.is_destructive(group, verb) is True

    def test_none_verb_is_not_destructive(self):
        assert core.is_destructive("metadata", None) is False

    def test_unknown_group_unknown_verb(self):
        assert core.is_destructive("query", "accounts") is False

    @pytest.mark.parametrize("verb", [
        "import", "uninstall", "stage-and-upgrade", "apply-upgrade",
    ])
    def test_solution_destructive_verbs(self, verb):
        assert core.is_destructive("solution", verb) is True

    def test_solution_clone_as_patch_not_destructive(self):
        # A non-destructive solution verb still resolves to False.
        assert core.is_destructive("solution", "clone-as-patch") is False

    def test_plugin_unregister_assembly_is_destructive(self):
        assert core.is_destructive("plugin", "unregister-assembly") is True

    def test_async_cancel_is_destructive(self):
        assert core.is_destructive("async", "cancel") is True

    def test_metadata_list_entities_not_destructive(self):
        assert core.is_destructive("metadata", "list-entities") is False
