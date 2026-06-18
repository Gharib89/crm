"""Canonical destructive-verb classification for crm.

This module is the single source of truth for which ``crm`` command-group/verb
pairs are considered destructive.  It is imported by CLI commands that need to
gate operations behind a ``--yes`` prompt, and by tests that verify the
classification is consistent.

**Standalone copy in the PreToolUse hook.**
``.claude/hooks/destructive_op_gate.py`` is a pure-stdlib script executed by
the *system* Python on every Bash call — it cannot import from the project venv.
It therefore carries its own inline copies of ``DESTRUCTIVE`` and ``ROLE_VERBS``.
The test ``crm/tests/test_destructive_sync.py`` asserts that the two copies stay
aligned; any addition here must be mirrored in the hook and vice-versa.
"""
from __future__ import annotations

# Destructive verbs keyed by command group, matched purely by token name so a
# verb is gated the moment it ships even if the CLI command does not exist yet.
# Forward-looking entries (delete-attribute / delete-relationship) and any
# future role/privilege mutation verb live here in one place.
DESTRUCTIVE: dict[str, set[str]] = {
    "metadata": {
        "delete-entity",
        "delete-optionset",
        "delete-attribute",      # not yet implemented; gated pre-emptively
        "delete-relationship",   # not yet implemented; gated pre-emptively
    },
    "entity": {"delete"},
    "data": {"delete"},
    "app": {"delete"},
    "solution": {"job-cancel", "import", "remove-component", "uninstall",
                 "stage-and-upgrade", "apply-upgrade"},
    "translation": {"import"},
    "async": {"cancel"},
    "plugin": {"unregister-assembly", "unregister-step", "unregister-image"},
}

# Role/privilege mutation verbs, gated by verb name regardless of group.
# assign-role is live; delete-role/remove-role/revoke-privilege/remove-privilege
# are pre-emptively gated forward-looking verbs.
ROLE_VERBS: set[str] = {
    "assign-role",
    "delete-role",
    "remove-role",
    "revoke-privilege",
    "remove-privilege",
}


def is_destructive(group: str, verb: str | None) -> bool:
    """Return True if (group, verb) is a destructive crm operation.

    Role verbs (``ROLE_VERBS``) are gated regardless of group.  All other
    destructive verbs are keyed by (group, verb) via ``DESTRUCTIVE``.  A
    ``None`` verb is never destructive.
    """
    if verb is None:
        return False
    if verb in ROLE_VERBS:        # role verbs gated regardless of group
        return True
    return verb in DESTRUCTIVE.get(group, set())
