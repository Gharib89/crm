---
status: accepted
---

# A second shipped skill, and compatibility rules as packaged data

The CRM 2011 to cloud migration (#1035) needs an agent skill that teaches
assessment and verification, distinct from the operational `crm` skill. We ship
it as a second skill, `crm-migration`, in a sibling folder of the package, and we
put the version-specific compatibility rules it relies on into the CLI as
packaged data run by `crm inventory assess`, not into the skill's prose.

## Decision

- **Sibling folder, not a container.** The new skill lives at
  `crm/migration-skill/` (`SKILL.md` with `name: crm-migration`, plus
  `reference/`). `crm/skills/` stays the `crm` skill's folder unchanged. The
  plugin manifest entry `./crm/skills` already makes `npx skills` scan every
  child folder of `crm/` for a `SKILL.md`, so the new skill is discoverable with
  no manifest change.
- **One installer, one folder per skill.** `crm skill install` and `uninstall`
  take `--skill crm|crm-migration` (repeatable), defaulting to `crm` so existing
  installs and scripts behave as before. Each skill installs into its own
  `<target>/<name>` folder, so neither overwrites the other. Installed-skill
  registry entries carry a `skill` field (an entry without one reads as `crm`),
  and `self-update` refreshes each entry from that skill's own bundle.
- **Compatibility rules are data in the CLI.** A versioned rules file ships in
  the package. Each **compatibility rule** names the component kind, the source
  and target versions it applies to, a class, a detection, a fix and the
  Microsoft Learn page that justifies it. `crm inventory assess` runs the rules
  over a migration inventory and writes a versioned findings file of
  **compatibility findings**, keyed by component key (ADR 0032). A kind no rule
  covers yields an `unknown` finding with the reason "no rule", never silence.
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
