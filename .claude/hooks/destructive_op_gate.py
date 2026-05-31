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

# Root-group options that consume the FOLLOWING token as their value (see the
# `crm` group in crm/cli.py). If we did not skip the value too, it would be
# mistaken for the command group and let a destructive verb slip past the gate
# (e.g. `crm --profile prod metadata delete-entity x`). The `--flag=value` form
# is already handled because it starts with `-`. Boolean flags (--json,
# --dry-run, --verbose) take no value and are dropped by the startswith("-")
# filter alone.
VALUE_OPTIONS: set[str] = {
    "--profile",
    "--password",
    "--log-level",
    "--log-format",
    "--auth-scheme",
    "--session",
}


def _strip_global_options(tokens: list[str]) -> list[str]:
    """Drop root-group options (and the value of value-taking ones) from the
    tokens after `crm`, leaving the command group and verb as the first two."""
    rest: list[str] = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-"):
            # `--flag=value` carries its own value; `--flag value` consumes the
            # next token only for known value-taking options.
            if "=" not in tok and tok in VALUE_OPTIONS:
                skip_next = True
            continue
        rest.append(tok)
    return rest


# Shell operators that separate one command from the next inside a single Bash
# string. Splitting on these lets us inspect each sub-command (e.g. the `crm`
# call in `true && crm entity delete ...`) instead of only the first token.
SHELL_SEPARATORS: set[str] = {"&&", "||", ";", "|", "&", "(", ")", "$(", "{", "}"}


def _is_crm_invocation(token: str) -> bool:
    """True if `token` is `crm` or a path ending in `/crm` (e.g. /usr/bin/crm)."""
    return token == "crm" or token.endswith("/crm")


def _split_segments(tokens: list[str]) -> list[list[str]]:
    """Split a token stream into command segments on shell operators."""
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in SHELL_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments


def _destructive_match(tokens: list[str]) -> str | None:
    """Return a human-readable verb label if `tokens` (one command segment) are
    a destructive crm invocation, else None. The first token must be a `crm`
    invocation (bare or path-prefixed). Does not block on --yes (caller checks)."""
    if not tokens or not _is_crm_invocation(tokens[0]):
        return None

    # Drop global flags/options after `crm` to find the group, then the verb.
    rest = _strip_global_options(tokens[1:])
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

    # Inspect every sub-command so a destructive crm call inside a compound
    # command (`true && crm ...`) or with a path prefix (`/usr/bin/crm ...`)
    # is still caught. --yes is scoped to its own segment.
    for segment in _split_segments(tokens):
        label = _destructive_match(segment)
        if label is None:
            continue
        if "--yes" in segment:
            continue
        sys.stderr.write(
            f"BLOCKED: `crm {label}` is a destructive operation and was prevented by "
            f"the destructive-op gate. It permanently deletes or cancels server state. "
            f"To confirm intentionally, re-run with the `--yes` flag.\n"
        )
        return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main())
