---
status: accepted
---

# Skill recipes are flag-free annotated spines (defer flags to `describe`/`--help`)

## Context

The `crm` skill ships to users who have **only the skill, not the repo** (`crm skill
install` copies `crm/skills/` into a harness dir). A proactive dogfood — an isolated agent
building a full customization end-to-end on a live org, using only the installed skill +
`crm describe`/`--help` — showed the skill maps commands to domains well but left the agent
to assemble the *multi-command sequences* itself. The places it stumbled were the
"do-the-work" **seams**: carry a created `appmoduleid` into the next command (don't
re-create), add a field to an existing form, verify a change landed after publish, tear
down in reverse dependency order, recognize a cross-version import rejection.

The obvious fix is worked recipes — but a recipe full of literal flags fails twice. It
**restates flags/choices/defaults**, which the standing rule (CLAUDE.md "Keep docs in sync
with code") forbids because `crm describe`/`--help` are the single source of truth for
those and a restated copy drifts. And it **drifts unmonitored**: the shipped skill is *not*
covered by the e2e / `mkdocs --strict` gate that guards `docs/**`, so a stale flag in the
skill has no CI to catch it.

## Decision

Skill recipes are written as **annotated spines**. A spine shows:

1. **command order** — the sequence of verbs, in dependency order;
2. **the data flowing between steps** — capture `_entity_id` → feed the next command,
   `--minimal` to chain a record downstream, the `appmoduleid` you must reuse;
3. **what to verify after each step** — `--expect` after publish, `components --diff`,
   `metadata describe`.

Exact flags, choices, and defaults are **deferred to `crm describe` / `crm <group>
--help`**. Only **load-bearing workflow flags** appear literally — flags whose presence
changes the workflow itself (`--stage-only`, `--managed`, `--validate`, `--data-file`,
destructive `--yes`) — never enumerable option lists. New "do-the-work" content lands as a
spine in `crm/skills/reference/customization-lifecycle.md` (cross-domain tasks) or the
relevant domain reference file (single-domain), never as a flag table.

## Considered options

- **Full worked recipes with literal flags** — rejected. Most copy-pasteable, but restates
  flags (breaks the rule) and drifts silently because the shipped skill sits outside the
  e2e / docs gate. The maintenance tax lands exactly where there is no CI to enforce it.
- **Decision-trees / prose only, no command lines** — rejected. Strictest adherence to the
  no-restate rule, but if the real gap is "the agent can't assemble the sequence," prose
  about *when* to use a verb does not teach *how to chain* them.

## Consequences

- The skill teaches sequencing, chaining, and verification without an enumerable-flag
  maintenance tax; the only literal tokens that can drift are command and
  load-bearing-flag names, which change rarely and are caught by ordinary review.
- A spine never substitutes for `--help`: the agent is expected to run `describe`/`--help`
  for exact flags, as the skill's "Command discovery — never guess" section already
  mandates.
- Gaps surfaced by future dogfoods are closed the same way (a spine, not a flag table),
  keeping the skill's "states only what `describe`/`--help` cannot" invariant intact.
- This extends the spirit of ADR 0004 (document the external tool, don't wrap it): here,
  document the *workflow*, don't restate the *interface*.
