#!/usr/bin/env python3
"""Claude Code PreToolUse gate: block destructive `crm` verbs without --yes.

Deterministic, model-independent guardrail. Reads the PreToolUse JSON payload on
stdin, and if the Bash command is a `crm` invocation of a destructive verb,
exits 2 (block) with a human-readable reason on stderr UNLESS an explicit
`--yes` confirm flag is present. Everything else exits 0 (pass through).

Pure stdlib: no network, no crm/D365 import — runs fast and offline on every
Bash call. Contract: PreToolUse exit code 2 blocks the tool call and feeds
stderr back to the agent (Claude Code hooks docs).
"""
from __future__ import annotations

import json
import shlex
import sys

BLOCK = 2

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
    "solution": {"job-cancel"},
    "async": {"cancel"},
}

# Future role/privilege mutation verbs, gated by name regardless of group.
ROLE_VERBS: set[str] = {
    "delete-role",
    "remove-role",
    "revoke-privilege",
    "remove-privilege",
}


def _destructive_match(tokens: list[str]) -> str | None:
    """Return a human-readable verb label if tokens are a destructive crm
    invocation, else None. Does not block on --yes presence (caller checks)."""
    if not tokens or tokens[0] != "crm":
        return None

    # Drop global flags/options after `crm` to find the group, then the verb.
    rest = [t for t in tokens[1:] if not t.startswith("-")]
    if not rest:
        return None

    group = rest[0]
    verb = rest[1] if len(rest) > 1 else None

    if verb is not None and verb in ROLE_VERBS:
        return f"{group} {verb}"

    verbs = DESTRUCTIVE.get(group)
    if verbs and verb in verbs:
        return f"{group} {verb}"
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0

    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return 0

    try:
        tokens = shlex.split(command)
    except ValueError:
        return 0

    label = _destructive_match(tokens)
    if label is None:
        return 0

    if "--yes" in tokens:
        return 0

    sys.stderr.write(
        f"BLOCKED: `crm {label}` is a destructive operation and was prevented by "
        f"the destructive-op gate. It permanently deletes or cancels server state. "
        f"To confirm intentionally, re-run with the `--yes` flag.\n"
    )
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
