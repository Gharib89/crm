---
name: delete-comment
description: Clear legacy `#` comments from Python paths under the ADR 0036 ratchet. Use when working a "Delete legacy comments" slice issue, or asked to retire the comments in a package or file.
argument-hint: "<path>..."
metadata:
  internal: true
---

# delete-comment

ADR 0036 bans `#` comments in Python and holds the comments that predate the ban
in a per-file ratchet. This skill retires one **slice** of them: the paths named
in the argument. Every comment block in the slice gets exactly one **verdict**,
and the slice ends with no comments, no baseline entries and no code changed.

## 1. Isolate

Under `/ship`, ship's worktree and PR stand in for this step and step 7.
Otherwise work in a fresh worktree off `main` on the branch
`refactor/delete-comments-<slug>`.

## 2. List

```sh
python scripts/check_comments.py --list $(git ls-files <path>... | grep '\.py$')
```

Each output line is `path:line: text`. Adjacent full-line comments form one
**block**; an inline comment is a block on its own. Read every block in its
code, not from the list alone: a verdict needs the lines the block sits beside.
Keep a tally of verdicts per kind as you go; step 7 reports it.

**Fan-out.** A slice over about 15 files goes to subagents, one per group of
about 10 files, each given steps 3 and 4 and its file list. A subagent edits
only its own files and returns its tally, excuse records and ADR candidates as
data. The parent alone writes `docs/adr/`, files issues and runs `--update`, so
there is one writer for each shared file.

## 3. Classify

Give each block one verdict. When two kinds fit, the first match in this order
wins: an excuse that is also a decision is still filed.

- **Excuse**: the block admits or justifies a shortcut: a placeholder, a "for
  now", a known gap, a hack around our own code, a test that pins a bug, a
  "should be X but". Test: would a reviewer ask for the code to change if the
  comment were absent?
- **Stale**: the block names a symbol, file, flag, issue, line number or behaviour
  that the code no longer has, or describes logic the next lines no longer do.
  Commented-out code is stale. Check the claim with grep or `LSP`, never by
  reading the comment alone.
- **Decision record**: the block states a why the code cannot show: a server
  limit or quirk, an ordering or invariant, a rejected alternative, a workaround
  for an external system, an issue number that explains a constraint. Test:
  would a careful reader "simplify" the code back into a bug without it?
- **Tautology**: everything else: a restatement of the next line, a section
  divider, a file-path header, an Arrange/Act/Assert marker, a label a name
  already carries.

## 4. Act

- **A tool directive** (`noqa`, `type: ignore`, `pyright:`, `pragma: no cover`)
  stays; prose after it on the same line is a block like any other.
- **Tautology, stale**: delete the block. Delete a whole divider block,
  including its blank `#` lines.
- **Excuse**: delete the block and record `path:line`, the comment's text and the
  shortcut it covers, in one sentence, for step 5.
- **Decision record**, by reach:
    - **Local** (one function, class, constant or test): fold the why into that
      object's docstring, in prose, keeping any issue number. A constant or class
      attribute takes an attribute docstring, a string literal on the line after
      the assignment. A test's why goes in its docstring; a parametrize case's
      label becomes `pytest.param(..., id="<label>")`. Merge with what the
      docstring already says rather than repeating it.
    - **Cross-module** (the same rule governs several modules, or one option was
      chosen over named alternatives): add it to the ADR in `docs/adr/` whose
      decision it extends, else collect it as an ADR candidate. Group candidates by
      topic and write one ADR per topic, the next free number, following the
      format of the newest ADR. Leave a one-line docstring pointer (`See ADR NNNN.`)
      at the code that depends on it.

Every docstring and ADR line you write holds only the why the code beside it
cannot show.

## 5. File the excuses

Group the excuse records by shared file: the records one PR would fix form one
issue. File each with `gh issue create --label needs-triage`, the title naming
the shortcut, and the body quoting each comment with its `path:line`, so
`/triage` can grade it. This repo is public: quote no live org names, hosts or
GUIDs.

## 6. Lock in

```sh
ruff format <touched files> && ruff check --fix <touched files>
python scripts/check_comments.py --update
python .claude/skills/delete-comment/scripts/same_code.py
scripts/check.sh turn
```

Done when all four hold:

- `--list` over the slice prints nothing;
- `ci/comment-baseline.json` has no key under the slice's paths;
- `same_code.py` prints `code unchanged`: every touched file parses to the same
  code as at the merge base once docstrings are set aside;
- `scripts/check.sh turn` exits 0.

## 7. Ship

Commit with the subject `refactor(<package>): delete comments (ADR 0036)`. The
PR body closes the slice issue, gives the verdict tally, links each new or
extended ADR and each excuse issue, and names the baseline entries `--update`
removed.
