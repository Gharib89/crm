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
  * A machine-local token file (`~/.claude/crm-secret-scan-tokens.txt`) — one
    case-insensitive regex per line, `#` comments allowed. Org-identifying
    tokens (internal org names, GUID machine-fingerprint suffixes) MUST live
    there, never in this tracked file: naming them here would itself publish
    them. Absent file -> only the generic patterns run. Each machine keeps its
    own copy (it is never committed).

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

# Same segment-splitting contract as destructive_op_gate.py: split the RAW
# command string on shell operators so a commit inside a compound command is
# still seen, then shlex-tokenize each piece.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|\$\(|[;|&()\n\r`]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_segments(command: str) -> list[list[str]]:
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


def _strip_assignments(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens) and _ASSIGNMENT.match(tokens[i]):
        i += 1
    return tokens[i:]


def _is_git(token: str) -> bool:
    return token == "git" or token.endswith("/git")


def _git_subcommand(tokens: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Given tokens after `git`, return (repo_override, subcommand, rest).

    Handles the global options that matter here: `-C <path>` (repo override)
    and `-c <name=val>` (skip its value). Other `--flag` globals are skipped;
    the first non-option token is the subcommand.
    """
    repo_override: str | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-C" and i + 1 < len(tokens):
            repo_override = tokens[i + 1]
            i += 2
            continue
        if tok == "-c" and i + 1 < len(tokens):
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
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    tokens: list[tuple[str, re.Pattern[str]]] = []
    for line in lines:
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
