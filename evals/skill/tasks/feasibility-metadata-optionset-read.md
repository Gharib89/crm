---
id: feasibility-metadata-optionset-read
domain: metadata
tier: 2
source:
  type: so
  url: https://stackoverflow.com/questions/39427389
# Metadata demand cluster (#899), pilot harvest #885 row 4 — "Retrieve all OptionSet values using
# OData" (list the label/value pairs of a custom option set so an integration can map text <-> the
# stored integer value). This is the single highest-demand metadata-read ask in the harvest (12
# votes, 35.4k views; siblings 8v/9.5k, 5v/3.9k, 1v/1.6k). Modeled as feasibility, not do: it is a
# pure read that mutates nothing, so there is no org end-state to assert — the discriminator is
# knowing WHICH metadata verb returns the option label/value pairs. Covered (reference/metadata.md),
# not uncovered: `metadata get-optionset <name>` retrieves a global option set INCLUDING its options,
# and `metadata picklist <entity> <attr>` returns an attribute-bound picklist's values (expanding the
# backing global set). T2, not T1: the trap is `metadata list-optionsets`, which lists global
# option-set DEFINITIONS but does NOT expand their options — an agent that reaches for list-optionsets
# and stops has enumerated the sets, not their label/value pairs. required_commands recall is on
# get-optionset (the direct answer for a reusable/global custom choice); picklist is the attribute-
# bound alternative, named in the evidence. Host-agnostic (both reads work on cloud and on-prem v9.1)
# -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - metadata get-optionset
evidence:
  - "`crm metadata get-optionset <name>` retrieves a global option set INCLUDING its options (the label/value pairs) — verified live via `--help` ('Retrieve a global option set, including its options'). This is the direct answer for a reusable/global custom choice, returning each option's integer value and its label for the integration's text<->value mapping."
  - "`crm metadata picklist <logical-name> <attribute>` retrieves the option values for an attribute-bound picklist / state / status column and expands the backing GlobalOptionSet (`--no-global` skips the expansion) — reference/metadata.md. It is the alternative when the choices live on a specific column rather than a named global set, so either command answers the ask depending on whether the option set is global or local."
  - "Trap (why T2, not a T1 baseline pass): `crm metadata list-optionsets` lists global option-set DEFINITIONS only and does not expand their options, so it enumerates the sets but not their values — get-optionset (or picklist) is required to actually read the label/value pairs. Stack Overflow 39427389 (the sourced ask, 35.4k views) asks specifically for the label/value pairs, i.e. the expanded options, not just the set names."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"An integration needs to map the text labels of a custom option set (a
reusable global choice) to the integer values Dynamics stores, so it has to read back every
option in that set — each option's numeric value and its label. Can the `crm` CLI retrieve all of
an option set's values (the label/value pairs), and if so, which command?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your current
working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command(s) the task needs — name the command
  path (e.g. `"metadata get-optionset"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
