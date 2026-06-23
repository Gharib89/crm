# pyright: basic
"""Offline gate: every ``crm …`` example in the docs + README must resolve against
the real CLI tree, as emitted by ``crm --json describe``.

Parallels :mod:`crm.tests.test_e2e_coverage_gate` — fully offline, no network, no
live org, runs in the default ``pytest``. A doc example fails the gate when:

1. its command **path** doesn't resolve to a known group/leaf,
2. a **global option** appears after the first command token and the resolved
   leaf/group doesn't redefine a same-named option, or
3. an **unknown flag** is used on a resolved leaf.

The false-positive classes documented in the originating issue (own-flags shadow
globals, correctly-placed leading globals + their values, commands hidden from
``describe``, prose/placeholder/hypothetical references, and narration prefixes)
are handled by the resolver logic and a small, explicit ``ALLOWLIST``. The gate
**fails closed**: a new unrecognized broken example is a failure, never a silent
skip. See ``crm/tests/TEST.md``.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands.describe import _EXCLUDED as DESCRIBE_EXCLUDED

# Repo root, resolved relatively: this file is <root>/crm/tests/<name>.py.
REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# CLI model — built once from `crm --json describe` (the single source of truth)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CliModel:
    paths: frozenset[str]  # every known command path, space-joined
    groups: frozenset[str]
    leaves: frozenset[str]
    global_flags: frozenset[str]  # all root-option opts + secondary_opts
    global_value_flags: frozenset[str]  # globals that consume a following value
    own_flags: dict[str, frozenset[str]]  # path -> its own params' flags
    hidden_commands: frozenset[str]  # top-level commands absent from describe


def _flags_of(node: dict) -> set[str]:
    flags: set[str] = set()
    for param in node.get("params", []):
        flags.update(param.get("opts", []))
        flags.update(param.get("secondary_opts", []))
    return flags


def _build_model() -> CliModel:
    result = CliRunner().invoke(cli, ["--json", "describe"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]

    global_flags: set[str] = set()
    global_value_flags: set[str] = set()
    for opt in data["root_options"]:
        keys = list(opt.get("opts", [])) + list(opt.get("secondary_opts", []))
        global_flags.update(keys)
        if not opt.get("is_flag"):
            global_value_flags.update(keys)

    nodes: dict[str, dict] = {c["path"]: c for c in data["commands"]}
    groups = {p for p, n in nodes.items() if n.get("is_group")}
    leaves = {p for p in nodes if p not in groups}
    own_flags = {p: frozenset(_flags_of(n)) for p, n in nodes.items()}

    return CliModel(
        paths=frozenset(nodes),
        groups=frozenset(groups),
        leaves=frozenset(leaves),
        global_flags=frozenset(global_flags),
        global_value_flags=frozenset(global_value_flags),
        own_flags=own_flags,
        # `describe` filters these out (currently `repl`); driving off the real
        # set keeps the gate in sync if another command is ever hidden.
        hidden_commands=frozenset(DESCRIBE_EXCLUDED),
    )


MODEL = _build_model()


# --------------------------------------------------------------------------- #
# Extraction — pull `crm …` invocations out of the docs + README
# --------------------------------------------------------------------------- #
_FENCE = re.compile(r"^\s*```")
_SEG_SPLIT = re.compile(r"&&|\|\||[|;]")  # shell pipeline/sequence separators
_INLINE = re.compile(r"`([^`]*\bcrm\b[^`]*)`")  # inline `code spans` mentioning crm
# A token is a placeholder, not a real CLI token, if it carries metavar syntax.
_PLACEHOLDER = re.compile(r"[<>{}]|\.\.\.")
# Tokens allowed *before* `crm` in a runnable invocation: a shell prompt, `sudo`,
# a `VAR=val` assignment, or a narration label ending in `:` (`Agent:  Running:`).
# Anything else preceding `crm` means it is prose ("…via crm profile edit"), not a
# command — that's the narration-vs-prose discriminator.
_PREFIX_TOK = re.compile(r"^(\$|sudo|[A-Za-z_]\w*=.*|.+:)$")


@dataclass(frozen=True)
class Example:
    file: str  # repo-relative posix path
    line: int  # 1-based line where the example starts
    command: str  # the `crm …` segment as written
    tokens: tuple[str, ...]  # tokens following the first `crm` token

    def __str__(self) -> str:  # nice -k / failure ids
        return f"{self.file}:{self.line}: {self.command}"


def _doc_files() -> list[Path]:
    """User-facing markdown: docs/** (minus planning/spec dirs) + README."""
    files: list[Path] = []
    for path in sorted(REPO_ROOT.glob("docs/**/*.md")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("docs/superpowers/", "docs/research/")):
            continue  # plans/specs, not user-facing examples
        if path.name == "changelog.md":
            continue  # generated by python-semantic-release
        files.append(path)
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        files.append(readme)
    return files


def _code_block_lines(text: str) -> list[tuple[int, str]]:
    """(lineno, line) for every line inside a fenced code block."""
    out: list[tuple[int, str]] = []
    inside = False
    for i, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            inside = not inside
            continue
        if inside:
            out.append((i, line))
    return out


def _join_continuations(pairs: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Join backslash-continued lines, keeping the first line number."""
    out: list[tuple[int, str]] = []
    buf = ""
    start: int | None = None
    for ln, line in pairs:
        if buf:
            buf += " " + line.strip()
        else:
            start, buf = ln, line
        if line.rstrip().endswith("\\"):
            buf = buf[: buf.rindex("\\")].rstrip()
            continue
        assert start is not None
        out.append((start, buf))
        buf, start = "", None
    if buf:
        assert start is not None
        out.append((start, buf))
    return out


def _segments(line: str) -> list[str]:
    return [s.strip() for s in _SEG_SPLIT.split(line) if s.strip()]


def _crm_tokens(segment: str) -> tuple[str, ...] | None:
    """Tokens following the first ``crm`` token, or None if there is none.

    Finds ``crm`` anywhere in the segment so narration prefixes
    (``Agent:  Running: crm …``), prompts (``$ crm …``), ``sudo``, and
    ``VAR=val`` assignments before it are all tolerated.
    """
    try:
        toks = shlex.split(segment, comments=True)
    except ValueError:
        toks = segment.split()
    for i, tok in enumerate(toks):
        if tok == "crm":
            if all(_PREFIX_TOK.match(p) for p in toks[:i]):
                return tuple(toks[i + 1 :])
            return None  # prose: a non-prefix word precedes `crm`
    return None


def _extract(path: Path) -> list[Example]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rel = path.relative_to(REPO_ROOT).as_posix()
    candidates: list[tuple[int, str]] = _join_continuations(_code_block_lines(text))
    for i, line in enumerate(text.splitlines(), 1):
        for m in _INLINE.finditer(line):
            candidates.append((i, m.group(1)))

    examples: list[Example] = []
    for ln, line in candidates:
        if "crm" not in line:
            continue
        for seg in _segments(line):
            if "crm" not in seg:
                continue
            toks = _crm_tokens(seg)
            if toks is None:
                continue
            examples.append(Example(rel, ln, seg.strip(), toks))
    return examples


def _all_examples() -> list[Example]:
    out: list[Example] = []
    for f in _doc_files():
        out.extend(_extract(f))
    return out


# --------------------------------------------------------------------------- #
# Resolution + audit
# --------------------------------------------------------------------------- #
def _resolve(model: CliModel, tokens: list[str]) -> tuple[list[str], str, str | None]:
    """Greedily walk command tokens into the longest known path.

    Returns ``(path_parts, status, bad_token)`` where status is one of:
    ``ok-leaf`` (resolved to a leaf or a leaf+positional), ``group-missing-sub``,
    ``unknown-sub``, ``unknown-top``, ``bare`` (no command tokens at all).
    """
    parts: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            break
        cand = " ".join([*parts, tok])
        if cand in model.paths:
            parts.append(tok)
            i += 1
            if cand in model.leaves:
                break  # remaining non-flag tokens are positional args
            continue
        # `tok` is not a valid next path component.
        if not parts:
            return parts, "unknown-top", tok
        cur = " ".join(parts)
        if cur in model.groups:
            return parts, "unknown-sub", tok
        return parts, "ok-leaf", None  # leaf reached; tok is a positional arg

    cur = " ".join(parts)
    if not cur:
        return parts, "bare", None
    if cur in model.groups:
        return parts, "group-missing-sub", None
    return parts, "ok-leaf", None


def audit_example(model: CliModel, example: Example) -> list[str]:
    """Return a list of problem descriptions for one example ([] == valid)."""
    tokens = list(example.tokens)

    # Bare `crm` launches the REPL — valid, not "group with no subcommand".
    if not tokens:
        return []

    # Consume a leading run of correctly-placed global flags (and the *values* of
    # value-taking globals): `crm --json profile add …`, `crm --profile p use`.
    pre = 0
    while pre < len(tokens) and tokens[pre].startswith("-"):
        key = tokens[pre].split("=", 1)[0]
        if key not in model.global_flags:
            break
        if key in model.global_value_flags and "=" not in tokens[pre]:
            pre += 2  # also skip the separate value token
        else:
            pre += 1
    body = tokens[pre:]
    if not body:
        return []  # only globals, e.g. `crm --version` / `crm --help`

    # A command hidden from `describe` (e.g. `crm repl`) is valid.
    if body[0] in model.hidden_commands:
        return []

    problems: list[str] = []
    parts, status, bad = _resolve(model, body)

    # Own flags of the resolved leaf and of every group on its path. A global
    # placed after the command is fine if the command redefines a same-named opt.
    own: set[str] = set()
    for n in range(1, len(parts) + 1):
        sub = " ".join(parts[:n])
        own |= set(model.own_flags.get(sub, frozenset()))

    # Misplaced-global check: a global flag appearing after the first command
    # token, not redefined by the resolved command.
    first_cmd_idx: int | None = next(
        (k for k, t in enumerate(body) if not t.startswith("-")), None
    )
    if first_cmd_idx is not None:
        for idx, tok in enumerate(body):
            key = tok.split("=", 1)[0]
            if key in model.global_flags and key not in own and idx > first_cmd_idx:
                problems.append(
                    f"global option '{key}' placed after the command "
                    f"(must precede it: `crm {key} …`)"
                )

    if status == "unknown-top":
        problems.append(f"unknown top-level command '{bad}'")
        return problems
    if status == "unknown-sub":
        problems.append(f"'{bad}' is not a subcommand of '{' '.join(parts)}'")
        return problems
    if status == "group-missing-sub":
        # A bare `crm <group>` with nothing trailing is a prose reference
        # (e.g. a "How-to: async" intro mentioning `crm async`), not a runnable
        # example — the issue defines these as references, not bugs. A group
        # *with* trailing flags/args is still flagged.
        if len(body) == len(parts):
            return problems
        problems.append(f"'{' '.join(parts)}' is a group but no subcommand was given")
        return problems
    if status == "bare":
        return problems

    # Unknown-flag check on the resolved leaf.
    path = " ".join(parts)
    if path in model.leaves:
        valid = set(model.own_flags.get(path, frozenset())) | model.global_flags
        for tok in body[len(parts) :]:
            if not tok.startswith("-") or tok == "--":
                continue
            key = tok.split("=", 1)[0]
            if _PLACEHOLDER.search(key):
                continue  # `--<flag>` style placeholder, not a real flag
            if key not in valid:
                problems.append(f"unknown flag '{key}' for `crm {path}`")
    return problems


# --------------------------------------------------------------------------- #
# Allowlist — known non-runnable references (prose, placeholders, hypotheticals,
# ASCII diagrams). Keyed by the normalized token string after `crm` (exact match,
# never a pattern). Each entry is a deliberate, reviewed exception; the gate
# otherwise fails closed.
# --------------------------------------------------------------------------- #
ALLOWLIST: dict[str, str] = {
    # ADR 0004 documents a *rejected* `crm codegen` verb (use external tools).
    "codegen": "ADR 0004: rejected command, documented as not built",
    # Hypothetical future verb gated on issue #37 (BPF via Web API on on-prem).
    "bpf": "guides/crmworx-walkthrough: hypothetical future command (#37)",
    # README architecture diagram, not an invocation.
    "(Click + REPL)": "README architecture ASCII diagram",
}


def _normalized(example: Example) -> str:
    return " ".join(example.tokens)


def _is_placeholder_command(example: Example) -> bool:
    """A `crm <group> …` / `crm {x}` reference whose command token is a metavar."""
    return bool(example.tokens) and bool(_PLACEHOLDER.search(example.tokens[0]))


EXAMPLES = _all_examples()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_model_built_from_describe() -> None:
    """Sanity floor: describe materialized the full lazy tree."""
    assert len(MODEL.leaves) > 100, f"only {len(MODEL.leaves)} leaves; lazy load broke"
    assert "query odata" in MODEL.paths
    assert "--json" in MODEL.global_flags


def test_examples_were_extracted() -> None:
    """Guard against the extractor silently finding nothing (then vacuously green)."""
    assert len(EXAMPLES) > 50, f"only {len(EXAMPLES)} examples extracted"


@pytest.mark.parametrize("example", EXAMPLES, ids=str)
def test_doc_example_resolves(example: Example) -> None:
    if _normalized(example) in ALLOWLIST or _is_placeholder_command(example):
        pytest.skip("allowlisted non-runnable reference")
    problems = audit_example(MODEL, example)
    assert not problems, "\n".join(
        [f"{example.file}:{example.line}: {example.command}", *problems]
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every ALLOWLIST key must still match an extracted example (no dead skips)."""
    keys = {_normalized(e) for e in EXAMPLES}
    stale = set(ALLOWLIST) - keys
    assert not stale, f"ALLOWLIST entries no longer present in docs: {sorted(stale)}"


# --------------------------------------------------------------------------- #
# Seam unit tests — exercise extract + audit directly on synthetic commands
# --------------------------------------------------------------------------- #
def _audit(command: str) -> list[str]:
    """Audit one `crm …` string through the real extract+audit seam."""
    toks = _crm_tokens(command)
    assert toks is not None, f"not recognized as a crm invocation: {command!r}"
    return audit_example(MODEL, Example("<test>", 0, command, toks))


# The four examples fixed in 41ba0ba — reintroducing any must fail the gate.
@pytest.mark.parametrize(
    "command",
    [
        "crm query account --top 5",  # quickstart: account not a query subcommand
        "Agent:  Running: crm query account --top 5 --select name --orderby name --json",
        "Agent:  Running: crm entity create account --data '{\"name\":\"x\"}' --json",
        "crm connection whoami --json",  # index: trailing global option
    ],
)
def test_known_bugs_are_caught(command: str) -> None:
    assert _audit(command), f"gate failed to flag a known-bad example: {command}"


@pytest.mark.parametrize(
    "command",
    [
        "crm",  # bare invocation launches the REPL
        "crm repl",  # hidden command, absent from describe
        "crm --json profile add",  # leading value-less global flag
        "crm --profile p connection whoami",  # leading value-taking global
        "crm profile add --password PLACEHOLDER",  # own flag shadows a global
        "crm query odata accounts --top 5",  # the corrected query form
        "crm profile",  # bare group reference (prose)
    ],
)
def test_valid_invocations_pass(command: str) -> None:
    assert not _audit(command), f"gate wrongly flagged a valid example: {command}"


@pytest.mark.parametrize(
    "segment",
    [
        "(e.g. via crm profile edit).",  # a word precedes `crm` -> prose
        "see the crm async recipes",
    ],
)
def test_prose_mentions_are_not_extracted(segment: str) -> None:
    assert _crm_tokens(segment) is None


def test_narration_prefix_is_extracted() -> None:
    assert _crm_tokens("Agent:  Running: crm connection whoami") == (
        "connection",
        "whoami",
    )
