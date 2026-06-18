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
import re
import shlex
import sys

BLOCK = 2

# Canonical copy: crm/core/destructive.py — kept aligned by crm/tests/test_destructive_sync.py.
# Destructive verbs keyed by command group, matched purely by token name so a
# verb is gated the moment it ships even if the CLI command does not exist yet.
# Forward-looking entries (delete-attribute / delete-relationship) and any
# future role/privilege mutation verb live here in one place.
DESTRUCTIVE: dict[str, set[str]] = {
    "metadata": {
        "delete-entity",
        "delete-optionset",
        "delete-attribute",  # not yet implemented; gated pre-emptively
        "delete-relationship",  # not yet implemented; gated pre-emptively
    },
    "entity": {"delete"},
    "data": {"delete"},
    "app": {"delete"},
    "solution": {
        "job-cancel",
        "import",
        "remove-component",
        "uninstall",
        "stage-and-upgrade",
        "apply-upgrade",
    },
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


def _confirm_present(tokens: list[str]) -> bool:
    """True if a real `--yes` confirm flag is present in `tokens`.

    A `--yes` that is consumed as the VALUE of a value-taking global option
    (e.g. `crm --profile --yes metadata delete-entity x`) does NOT count — it is
    the option's argument, not a confirmation. Walk with the same skip-next
    logic as `_strip_global_options` so such a smuggled `--yes` is ignored."""
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("-") and "=" not in tok and tok in VALUE_OPTIONS:
            skip_next = True
            continue
        if tok == "--yes":
            return True
    return False


# Shell operators that separate one command from the next inside a single Bash
# string. We split the RAW command string on these BEFORE shlex so a destructive
# sub-command is isolated even when the operator is glued to adjacent words
# (`a|crm ...`, `&&crm ...`, `$(crm ...)`). shlex never emits a glued operator
# as its own token, so token-level splitting would miss these. A newline (and
# carriage return) separates commands exactly like `;`, so a destructive verb on
# any line after the first must split into its own segment. Backtick command
# substitution (`` `crm ...` ``) is split too, like `$(...)`. Order matters: the
# two-char operators (`||`, `&&`, `$(`) must precede the single-char class.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|\$\(|[;|&()\n\r`]")

# A leading shell variable-assignment prefix (`FOO=1 crm ...`) is valid syntax
# that would otherwise make the assignment the first token and hide the crm verb.
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _is_crm_invocation(token: str) -> bool:
    """True if `token` is `crm` or a path ending in `/crm` (e.g. /usr/bin/crm)."""
    return token == "crm" or token.endswith("/crm")


def _split_segments(command: str) -> list[list[str]]:
    """Split the raw command string into shlex-tokenized command segments on
    shell operators. Splitting the string (not the token list) catches operators
    glued to neighbouring words, which shlex would otherwise fold into one token."""
    segments: list[list[str]] = []
    for piece in _SEGMENT_SPLIT.split(command):
        if not piece.strip():
            continue
        try:
            tokens = shlex.split(piece)
        except ValueError:
            continue
        if tokens:
            segments.append(tokens)
    return segments


def _destructive_match(tokens: list[str]) -> str | None:
    """Return a human-readable verb label if `tokens` (one command segment) are
    a destructive crm invocation, else None. The first non-assignment token must
    be a `crm` invocation (bare or path-prefixed). Does not block on --yes."""
    # Drop any leading `NAME=value` env-var assignment prefixes so `FOO=1 crm ...`
    # is treated identically to `crm ...`.
    i = 0
    while i < len(tokens) and _ASSIGNMENT.match(tokens[i]):
        i += 1
    tokens = tokens[i:]
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

    # Inspect every sub-command so a destructive crm call inside a compound
    # command (`true && crm ...`, `a|crm ...`, `$(crm ...)`) or with a path
    # prefix (`/usr/bin/crm ...`) is still caught. --yes is scoped to its own
    # segment.
    for segment in _split_segments(command):
        label = _destructive_match(segment)
        if label is None:
            continue
        if _confirm_present(segment):
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
