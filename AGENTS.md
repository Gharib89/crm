# AGENTS.md

Working agreement for AI coding agents in this repo.

## Source of truth: `CLAUDE.md`

The complete, canonical working agreement for this project lives in **[`CLAUDE.md`](./CLAUDE.md)** —
credential model, architecture, branch/worktree discipline, shell quirks, the e2e suite and live
targets, the release flow, the docs-sync gates, and the review workflow.

**Read `CLAUDE.md` in full and follow it exactly before any non-trivial change.** It is written from
one assistant's perspective, but every rule in it applies to *any* coding agent. Where it names a
tool or command specific to that assistant (a skill loader, a named sub-agent, a worktree helper),
use the equivalent capability in your own harness — the *rule* still holds.

## Deltas worth surfacing up front

These are the rules most often skipped; `CLAUDE.md` is authoritative on each:

- **Never develop in the shared main checkout.** Every feature or fix happens on a fresh branch in a
  git worktree; PR from that branch. In the shared checkout, only read-only work and small docs-only
  commits to `main`. Before any git mutation: check the current branch and status first, and stage
  with explicit paths — never `git add -A`.
- **Keep this a generic, public repo.** No real-org identifiers, GUIDs, secrets, or machine-specific
  absolute paths in code, tests, or docs. Use placeholders (`Contoso`, `1111…`). Grep for org names
  and GUID fingerprints before committing.
- **Conventional Commits drive releases.** `python-semantic-release` cuts versions from commit
  subjects on every push to `main` (`feat:`→minor for real features only, `fix:`/`perf:`→patch,
  breaking→major). Write the subject accordingly; do not hand-edit `CHANGELOG.md`.
- **Ship docs in the same change as code.** A new/changed command, flag, default, or behavior must
  update `README.md`, `docs/`, the shipped skill under `crm/skills/`, and its e2e coverage in the
  same change — see `CLAUDE.md` → "Keep docs in sync with code".
- **Mind the shell-capture traps and the e2e worktree/binary trap.** Capturing `crm` output under
  zsh, and running worktree code through the e2e `cli` fixture, both have documented footguns in
  `CLAUDE.md`. Follow the patterns there rather than trusting a captured result.
- **Verify before claiming done.** Passing tests, a working run, or before/after output — not an
  assertion. Run the e2e gates before opening a PR when instructed.
