---
status: accepted
---

# A second shipped skill, and compatibility rules as packaged data

The CRM 2011 to cloud migration (#1035) needs an agent skill that teaches
assessment and verification, distinct from the operational `crm` skill. We ship
it as a second skill, `crm-migration`, in a sibling folder of the package, and we
put the version-specific compatibility rules it relies on into the CLI as
packaged data run by a planned `assess` verb of the inventory command group
(#1063), not into the skill's prose.

## Decision

- **Sibling folder, not a container.** The new skill lives at
  `crm/migration-skill/` (`SKILL.md` with `name: crm-migration`, plus
  `reference/`). `crm/skills/` stays the `crm` skill's folder unchanged. The
  plugin manifest's `skills` list gains `./crm/migration-skill` beside
  `./crm/skills`: the Claude Code plugin loader reads each listed path, so the
  new skill needs its own entry there. `npx skills` scans every child folder of
  each entry's parent (`crm/`), so it finds both skills either way, and the
  added entry changes nothing for it.
- **One installer, one folder per skill.** `crm skill install` and `uninstall`
  take `--skill crm|crm-migration` (repeatable), defaulting to `crm` so existing
  installs and scripts behave as before. Each skill installs into its own
  `<target>/<name>` folder, so neither overwrites the other. Installed-skill
  registry entries carry a `skill` field (an entry without one reads as `crm`),
  and `self-update` refreshes each entry from that skill's own bundle.
- **Compatibility rules are data in the CLI.** A versioned rules file ships in
  the package. Each **compatibility rule** names the component kind, the source
  and target versions it applies to, a class, a detection, a fix and the
  Microsoft Learn page that justifies it. The `assess` verb runs the rules
  over a migration inventory and writes a versioned findings file of
  **compatibility findings**, keyed by component key (ADR 0032). A kind no
  active rule covers yields one entry for the kind, with its record count and the
  reason "no rule", never silence; a record of a covered kind that no rule
  selects keeps its own `unknown` finding with the reason "no rule" (amended
  2026-11-13, see Amendments).
- **The skill interprets; the CLI decides.** The skill reads findings, handles
  the `unknown` ones, and defers to the `crm` skill for exact CLI operations
  rather than restating them. Verification verdicts come from the verification
  command owned by #1036, not from this skill.

## Considered options

- **Make `crm/skills/` a container of skills** (`crm/skills/crm/`,
  `crm/skills/crm-migration/`). Rejected: it moves every `crm` skill file and
  every path that names `crm/skills` (package data, the PyInstaller spec, the
  coverage and lint gates, the eval harness, the docs) for no gain in
  discoverability.
- **Install both skills by default.** Rejected: it changes what an existing
  `crm skill install` writes, and most users of the operational skill never
  run a migration.
- **Compatibility rules as a skill reference table** applied by the agent
  reading source. Rejected: it is neither deterministic nor repeatable across
  hundreds of web resources, cannot be tested from fixtures, and leaves the work
  package's compatibility field (#1052) without a machine-readable input.

## Consequences

- Package data, the skill-coverage and lint gates, and the eval harness must
  name both skill folders; a gate that walks only `crm/skills/` silently skips
  the new skill.
- A rule without a cited source or a version range is invalid data and fails
  the rules file's own test.

## Amendments

- **2026-11-13 - a kind with no rule rolls up to one entry ([#1281](https://github.com/Gharib89/crm/issues/1281)).** The decision read "A kind no rule covers yields an `unknown` finding with the reason "no rule", never silence." Applied per record, that buried the findings that need work: on a CRM 2011 inventory of 39,514 records, 36,111 of 39,409 findings were `unknown` "no rule", 29,308 of them on `dependency`, `solution_membership` and `solution` records, and the skill asked for a manual-review entry per finding. A kind is now uncovered in a run when no rule active in that run names it (the rule names the target, its source range holds the inventory's version, and the run gave the input it needs: `--source-host` for a host rule, `--source-map` with `--repo` for a plug-in project or source rule). Its records get no "no rule" finding; the findings file and the summary list the kind once in `uncovered_kinds` with the number of its records the run assessed and the reason. The test is on the kind, never on a list of kind names. Every other `unknown` stays per record, on a covered or an uncovered kind (an extraction gap, `kind coverage <status>`, a version that is not dotted, a missing library, a field not recorded), and so does "no rule" on a record of a covered kind that no rule selects. The findings file moves to `schema_version` 2, since a record with no finding now has a meaning; a reader refuses version 1 and says to re-run `assess`, which is offline. `readiness` reads a gate whose rule's kind is uncovered as `unknown`, never `pass`. Status stays **accepted**.
