---
status: accepted
---

# No `#` comments: a why lives in a docstring or an ADR

Agent sessions wrote most of this codebase, and they left about 11,000 `#`
comment lines in its Python. A sample of 45 comment blocks sorted into four
kinds. Most restate the next line (a tautology), or still describe code that has
since changed (stale), and both mislead a reader who trusts them. A few excuse a
shortcut ("this flag is only a placeholder"), and the next agent copies the
shortcut along with its excuse. About a third record a real, non-obvious
decision, but a `#` line has no owner and no review trigger, so it rots silently
next to the code that outgrew it. We ban `#` comments in Python and give each
kind of content a home.

## Decision

- **Python files carry no `#` comments.** `scripts/check_comments.py` enforces
  it in pre-commit and in CI's `lint` job. Tool directives are not comments and
  stay allowed: `noqa`, `type:`, `pyright:`, `pragma:`, `fmt:`, `nosemgrep` and
  the like, a shebang and a coding cookie. Prose after a directive on the same
  line (`# noqa: BLE001 because ...`) is a comment.
- **A ratchet, not a big-bang deletion.** `ci/comment-baseline.json` records each
  file's comment count from the day of the ban. A file may hold at most its
  count; a file with no entry holds none. A count below the baseline also fails
  until `--update` lowers it, so freed slack is never spent. `--update` never
  raises a count or adds a file: raising one is a hand edit a reviewer sees.
- **A decision's home depends on its reach.** A constraint local to one function
  or class (a server limit, an ordering invariant, a workaround for one quirk)
  goes in that object's docstring, where a reader of the code still meets it.
  A decision that spans modules, or that picks one option over named
  alternatives, becomes an ADR, or is added to the ADR it extends.
- **An excuse becomes an issue.** The comment goes, and the shortcut it covered
  is filed as a tracker issue to fix, so the shortcut stays visible without
  inviting copies.
- **The `/delete-comment` skill runs the cleanup** one package at a time and
  locks in each slice with `--update`.

## Scope

Python only. YAML, shell and TOML comments stay: they hold tool directives
(`shellcheck disable=`, `# frozen:` pins that `pre-commit autoupdate` reads) and
config rationale with no docstring to move into. Docstrings are not comments and
stay governed by the coding standards' docstring rules. The derived skill copies
and the byte-exact test fixtures that pre-commit already excludes are out of
scope.

## Considered options

- **An ADR for every decision comment.** Rejected: about 1,800 blocks would turn
  `docs/adr/` into an index nobody reads, and the reader of a function would no
  longer meet the constraint that governs it.
- **Delete everything in one PR.** Rejected: about 10,000 deleted lines across
  more than 470 files cannot be reviewed for lost decisions, and the diff would
  conflict with every concurrent session on `main`.
- **A ruff or semgrep rule.** Neither tool has a rule that bans comments, and a
  per-file ratchet needs a baseline file either way.
