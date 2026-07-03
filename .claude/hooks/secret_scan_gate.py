#!/usr/bin/env python3
"""Claude Code PreToolUse gate: scan `git commit` content for secrets / org identifiers.

Deterministic, model-independent guardrail for a PUBLIC repo. When the Bash
command contains a `git commit` segment, scan what that commit would publish —
the staged diff's added lines (plus tracked unstaged changes for `-a`/`--all`)
and any `-m`/`--message` values — and exit 2 (block) with the offending
file/pattern on stderr if anything matches. Everything else exits 0.

Two pattern sources:
  * PATTERNS below — generic, public-safe secret shapes (private key blocks,
    Azure client secrets, credentials embedded in URLs).
  * Org-identifying tokens (internal org names, GUID machine-fingerprint
    suffixes) — these MUST NOT live in this tracked file (naming them here
    would itself publish them). They load from a machine-local token file
    (`~/.claude/crm-secret-scan-tokens.txt`, one case-insensitive regex per
    line, `#` comments allowed) and/or the `CRM_SECRET_SCAN_TOKENS` env var
    (comma/whitespace-separated regexes — the channel for cloud/CI sandboxes
    that have no home-dir token file). Neither source present -> only the
    generic patterns run.

Pure stdlib. Git subprocess calls run only for commands that contain a commit
segment, against the repo resolved from the payload cwd (following `cd` and
`git -C`). Any resolution or git failure fails OPEN (exit 0): this is a
guardrail against accidental leaks, not a security boundary.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

BLOCK = 2

# Generic, public-safe secret shapes. Keep this list high-signal: test suites
# legitimately commit fake passwords, so broad `password = "..."` patterns
# would block constantly.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "Azure client secret",
        re.compile(
            r"\b[A-Za-z0-9_~.]{3}7Q~[A-Za-z0-9_~.-]{31}\b"
            r"|\b[A-Za-z0-9_~.]{3}8Q~[A-Za-z0-9_~.-]{34}\b"
        ),
    ),
    ("credential in URL", re.compile(r"://[^/\s:@'\"]+:[^@\s'\"]{3,}@")),
]

TOKEN_FILE = os.path.expanduser("~/.claude/crm-secret-scan-tokens.txt")

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Operator tokens emitted by shlex punctuation_chars mode that separate one
# command from the next.
_OPERATOR_CHARS = set("|&;()<>")


def _split_segments(command: str) -> list[list[str]]:
    """Split the command into quote-aware tokenized segments on shell operators.

    Unlike destructive_op_gate.py's raw regex split, this uses shlex's
    punctuation_chars mode so operators inside QUOTED arguments (a commit
    message like `-m "fix (a|b)"`) do not shred the commit segment — that
    would silently skip the scan (false negative). Operators glued to words
    (`x&&git commit`) still split. An unbalanced quote keeps whatever parsed
    cleanly before it.
    """
    segments: list[list[str]] = []
    # A newline separates commands like `;` — lex line-wise so `cd x\ngit ...`
    # cannot fold into one segment. A quoted multiline -m message shreds at the
    # line break (unbalanced quote), but the tokens parsed before it — the
    # `git commit` invocation itself — are kept, so the diff is still scanned.
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
# `-` filter. Kept aligned with destructive_op_gate.py.
_GIT_VALUE_GLOBALS: set[str] = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--config-env",
}


def _git_subcommand(tokens: list[str]) -> tuple[str | None, str | None, list[str]]:
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


def _message_values(rest: list[str]) -> list[str]:
    """Extract `-m`/`--message` values from the tokens after `commit`."""
    values: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("-m", "--message") and i + 1 < len(rest):
            values.append(rest[i + 1])
            i += 2
            continue
        if tok.startswith("--message="):
            values.append(tok[len("--message=") :])
        elif tok.startswith("-m") and len(tok) > 2:
            values.append(tok[2:])
        i += 1
    return values


def _load_local_tokens() -> list[tuple[str, re.Pattern[str]]]:
    """Org-identifier regexes from the machine-local file plus the
    `CRM_SECRET_SCAN_TOKENS` env var (cloud/CI sandboxes have no home dir
    token file — a routine/CI environment variable is their channel)."""
    raw: list[str] = []
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            raw.extend(fh.read().splitlines())
    except OSError:
        pass
    env_tokens = os.environ.get("CRM_SECRET_SCAN_TOKENS", "")
    raw.extend(re.split(r"[,\s]+", env_tokens))
    tokens: list[tuple[str, re.Pattern[str]]] = []
    for line in raw:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens.append(("org identifier", re.compile(line, re.IGNORECASE)))
        except re.error:
            continue
    return tokens


def _git(repo_dir: str, args: list[str]) -> str | None:
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


def _added_lines(diff_text: str) -> list[tuple[str, str]]:
    """Parse unified diff text into (file, added-line) pairs."""
    pairs: list[tuple[str, str]] = []
    current = "?"
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            current = line[4:]
            if current.startswith("b/"):
                current = current[2:]
            continue
        if line.startswith("+"):
            pairs.append((current, line[1:]))
    return pairs


def _scan(
    pairs: list[tuple[str, str]],
    checks: list[tuple[str, re.Pattern[str]]],
) -> list[str]:
    findings: list[str] = []
    for origin, text in pairs:
        for label, pattern in checks:
            match = pattern.search(text)
            if match:
                snippet = match.group(0)[:60]
                findings.append(f"{origin}: {label}: {snippet!r}")
    return findings


def _resolve_dir(base: str, candidate: str) -> str | None:
    """Resolve `candidate` against `base`; None when it cannot be resolved
    statically (unexpanded shell variables)."""
    if "$" in candidate:
        return None
    candidate = os.path.expanduser(candidate)
    if not os.path.isabs(candidate):
        candidate = os.path.join(base, candidate)
    return os.path.normpath(candidate)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or "commit" not in command:
        return 0

    checks = PATTERNS + _load_local_tokens()

    cwd = payload.get("cwd")
    effective: str | None = cwd if isinstance(cwd, str) and cwd else os.getcwd()

    findings: list[str] = []
    scanned_repos: set[str] = set()
    for segment in _split_segments(command):
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
        repo_override, sub, rest = _git_subcommand(tokens[1:])
        if sub != "commit":
            continue

        # Commit-message values are published regardless of repo resolution.
        findings.extend(
            _scan([("commit message", v) for v in _message_values(rest)], checks)
        )

        repo_dir = effective
        if repo_override is not None and effective is not None:
            repo_dir = _resolve_dir(effective, repo_override)
        if repo_dir is None or repo_dir in scanned_repos:
            continue
        scanned_repos.add(repo_dir)

        diff = _git(repo_dir, ["diff", "--cached", "-U0", "--no-color"])
        if diff is not None:
            findings.extend(_scan(_added_lines(diff), checks))
        if "-a" in rest or "--all" in rest:
            diff = _git(repo_dir, ["diff", "HEAD", "-U0", "--no-color"])
            if diff is not None:
                findings.extend(_scan(_added_lines(diff), checks))

    if not findings:
        return 0
    unique = list(dict.fromkeys(findings))
    sys.stderr.write(
        "BLOCKED: this `git commit` would publish content matching secret / "
        "org-identifier patterns (public repo):\n"
        + "".join(f"  - {f}\n" for f in unique[:10])
        + "Genericize or remove the flagged content, restage, and commit again. "
        "GUIDs copied from a live org carry its machine fingerprint - use "
        "placeholders (1111..., cccc...). Org tokens are configured in "
        f"{TOKEN_FILE}.\n"
    )
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
