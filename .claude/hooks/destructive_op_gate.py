#!/usr/bin/env python3
"""Claude Code PreToolUse gate: block destructive `crm` verbs and git-discipline breaches.

Deterministic, model-independent guardrail. Reads the PreToolUse JSON payload on
stdin and exits 2 (block) with a human-readable reason on stderr when the Bash
command matches a gated class; everything else exits 0 (pass through).

Gated classes:
  * Destructive `crm` verbs without an explicit `--yes` confirm flag.
  * `git add -A` / `--all` / `.` — CLAUDE.md branch discipline: stage with
    explicit paths, never blanket-stage.
  * Non-docs `git commit` on `main` in the MAIN checkout of this repo (linked
    worktrees pass): development happens in a worktree on a fresh branch; the
    shared checkout takes only small docs-only commits.

Pure stdlib: no network, no crm/D365 import — runs fast and offline on every
Bash call. Git subprocesses run only for `git commit` segments and fail OPEN
(a guardrail must never wedge normal work when resolution fails). Contract:
PreToolUse exit code 2 blocks the tool call and feeds stderr back to the agent
(Claude Code hooks docs).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
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


# Operator tokens emitted by shlex punctuation_chars mode that separate one
# command from the next (kept aligned with secret_scan_gate.py).
_OPERATOR_CHARS = set("|&;()<>")


def _split_segments_lex(command: str) -> list[list[str]]:
    """Quote-aware counterpart of `_split_segments` (#675).

    The raw regex split above fires on operators inside QUOTED arguments
    (`--filter "a|b"`), leaving unbalanced-quote pieces that shlex rejects —
    silently dropping the segment and any destructive verb in it. This lexer
    respects quotes, so such a segment survives intact. The raw split still
    runs alongside it (union): it covers backtick substitution, which posix
    shlex treats as ordinary characters. Newlines separate segments like `;`
    (line-wise lexing); an unbalanced quote keeps whatever parsed before it.
    """
    segments: list[list[str]] = []
    for line in command.splitlines():
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        current: list[str] = []
        try:
            for tok in lex:
                if tok and set(tok) <= _OPERATOR_CHARS:
                    if current:
                        segments.append(current)
                        current = []
                else:
                    current.append(tok)
        except ValueError:
            pass  # unbalanced quote — fall through with what we have
        if current:
            segments.append(current)
    return segments


# --- git discipline gates (CLAUDE.md "Branch & worktree discipline") ---------

# `git add` arguments that blanket-stage instead of naming explicit paths.
BLANKET_ADD: set[str] = {"-A", "--all", ".", "./", ":/", ":/."}

# Paths allowed in a direct-to-main commit in the shared checkout.
_DOCS_PREFIXES = ("docs/",)


def _is_docs_path(path: str) -> bool:
    return path.endswith(".md") or path.startswith(_DOCS_PREFIXES) or path == "mkdocs.yml"


def _strip_assignments(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens) and _ASSIGNMENT.match(tokens[i]):
        i += 1
    return tokens[i:]


def _is_git(token: str) -> bool:
    return token == "git" or token.endswith("/git")


# Git global options that consume the FOLLOWING token as their value in the
# separated form (`--git-dir <path>`). Without skipping the value too, it would
# be misread as the subcommand and a `git ... commit` would go undetected.
# `--flag=value` forms carry their own value and are dropped by the generic
# `-` filter. Kept aligned with secret_scan_gate.py.
_GIT_VALUE_GLOBALS: set[str] = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
}


def _git_parts(tokens: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Given tokens after `git`, return (repo_override, subcommand, rest).

    `-C <path>` is captured as the repo override; other value-taking globals
    (`_GIT_VALUE_GLOBALS`) skip their value token. Remaining `--flag` globals
    are skipped; the first non-option token is the subcommand.
    """
    repo_override: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GIT_VALUE_GLOBALS and i + 1 < len(tokens):
            if tok == "-C":
                repo_override = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return repo_override, tok, tokens[i + 1 :]
    return repo_override, None, []


def _resolve_dir(base: str, candidate: str) -> str | None:
    """Resolve `candidate` against `base`; None when it cannot be resolved
    statically (unexpanded shell variables)."""
    if "$" in candidate:
        return None
    candidate = os.path.expanduser(candidate)
    if not os.path.isabs(candidate):
        candidate = os.path.join(base, candidate)
    return os.path.normpath(candidate)


def _git_run(repo_dir: str, args: list[str]) -> str | None:
    """Run git in `repo_dir`; return stdout or None on any failure (fail open)."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir] + args,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_add_reason(sub: str | None, rest: list[str]) -> str | None:
    if sub != "add":
        return None
    flagged = sorted({tok for tok in rest if tok in BLANKET_ADD})
    if not flagged:
        return None
    return (
        f"BLOCKED: `git add` with {' / '.join(flagged)} is disallowed (blanket-staging "
        "argument). CLAUDE.md branch discipline: stage with explicit paths "
        "(`git add <path> ...`) so unrelated worktree content never rides along."
    )


def _main_commit_reason(rest: list[str], repo_dir: str) -> str | None:
    """Block a non-docs commit on `main` in the MAIN checkout of this repo.

    Linked worktrees are identified by `--git-dir` differing from
    `--git-common-dir` and always pass. Any lookup failure -> None (fail open).
    """
    git_dir = _git_run(repo_dir, ["rev-parse", "--git-dir"])
    common_dir = _git_run(repo_dir, ["rev-parse", "--git-common-dir"])
    if git_dir is None or common_dir is None:
        return None
    if os.path.realpath(os.path.join(repo_dir, git_dir.strip())) != os.path.realpath(
        os.path.join(repo_dir, common_dir.strip())
    ):
        return None  # linked worktree — feature-branch work lives here
    origin = _git_run(repo_dir, ["remote", "get-url", "origin"])
    if origin is None or "gharib89/crm" not in origin.strip().lower():
        return None  # some other repo — not ours to police
    branch = _git_run(repo_dir, ["branch", "--show-current"])
    if branch is None or branch.strip() != "main":
        return None
    staged = _git_run(repo_dir, ["diff", "--cached", "--name-only"])
    if staged is None:
        return None
    paths = [p for p in staged.splitlines() if p.strip()]
    if "-a" in rest or "--all" in rest:
        unstaged = _git_run(repo_dir, ["diff", "--name-only"])
        if unstaged is not None:
            paths += [p for p in unstaged.splitlines() if p.strip()]
    non_docs = [p for p in paths if not _is_docs_path(p)]
    if not non_docs:
        return None
    shown = ", ".join(non_docs[:5]) + (", ..." if len(non_docs) > 5 else "")
    return (
        f"BLOCKED: non-docs `git commit` on `main` in the shared MAIN checkout ({shown}). "
        "CLAUDE.md worktree discipline: develop in a git worktree on a fresh branch "
        "(EnterWorktree) and PR from there; the shared checkout takes only small "
        "docs-only commits (*.md, docs/, mkdocs.yml)."
    )


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

    # Track the effective working directory across segments (`cd x && git ...`)
    # so repo-dependent git checks inspect the right repository. An
    # unresolvable `cd` (`cd $WT`) makes it None -> those checks skip.
    cwd = payload.get("cwd")
    effective: str | None = cwd if isinstance(cwd, str) and cwd else os.getcwd()

    # Inspect every sub-command so a destructive crm call inside a compound
    # command (`true && crm ...`, `a|crm ...`, `$(crm ...)`) or with a path
    # prefix (`/usr/bin/crm ...`) is still caught. --yes is scoped to its own
    # segment. Both segmentations are checked (#675): the raw split covers
    # backtick substitution, the quote-aware split covers operators inside
    # quoted arguments that shred the raw pieces.
    lex_segments = _split_segments_lex(command)
    for segment in _split_segments(command) + lex_segments:
        label = _destructive_match(segment)
        if label is not None and not _confirm_present(segment):
            sys.stderr.write(
                f"BLOCKED: `crm {label}` is a destructive operation and was prevented by "
                f"the destructive-op gate. It permanently deletes or cancels server state. "
                f"To confirm intentionally, re-run with the `--yes` flag.\n"
            )
            return BLOCK

    # git-discipline checks walk the quote-aware view only — accurate `cd`
    # tracking and intact quoted arguments matter here.
    for segment in lex_segments:
        tokens = _strip_assignments(segment)
        if not tokens:
            continue
        if tokens[0] == "cd":
            if len(tokens) == 1:
                effective = os.path.expanduser("~")
            elif effective is not None:
                effective = _resolve_dir(effective, tokens[1])
            continue
        if not _is_git(tokens[0]):
            continue
        repo_override, sub, rest = _git_parts(tokens[1:])
        reason = _git_add_reason(sub, rest)
        if reason is None and sub == "commit":
            repo_dir = effective
            if repo_override is not None and effective is not None:
                repo_dir = _resolve_dir(effective, repo_override)
            if repo_dir is not None:
                reason = _main_commit_reason(rest, repo_dir)
        if reason is not None:
            sys.stderr.write(reason + "\n")
            return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main())
