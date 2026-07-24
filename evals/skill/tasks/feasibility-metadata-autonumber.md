---
id: feasibility-metadata-autonumber
domain: metadata-writes
tier: 2
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=a26cd676-1e91-ef11-ac21-6045bda6da2f
# Metadata demand cluster (#899), pilot harvest #885 row 6 — "Autonumbering for Custom Entity"
# (a server-generated, formatted reference number without plug-in code), a perennial ask
# (siblings: Work Order autonumber, "Auto Number field not working after import"). Row 6 is
# tagged do+feas; modeled here as FEASIBILITY, not do, on hard evidence about the L1 ceiling: a
# do-task's deterministic predicate cannot confirm the auto-number-ness that IS the point.
# `metadata attributes` (the only list verb over an entity's columns) projects
# LogicalName/SchemaName/AttributeType/IsCustomAttribute/IsValidFor*/RequiredLevel/SourceType/
# MetadataId — NOT AutoNumberFormat (crm/core/metadata.py) — so a suffix+String predicate would
# green-light a plain String column with no format (a false positive the advisory L2 judge cannot
# gate). Reading back the generated value instead is also blocked: the auto-number column's own
# logical name carries the org's default publisher prefix (varies per org), so a static
# `query odata contacts --filter startswith(<col>,'EVAL899-')` cannot even name the field. And a
# custom attribute is metadata, not a deletable record, so `cleanup: []` would leave it in the org
# and every later run would pass without doing anything (cross-run contamination + zero-lift in the
# paired condition). The discriminator is therefore knowledge of the string-only
# `--auto-number-format` escape hatch, graded as feasibility — same shape as the other metadata
# cluster tasks. Host-agnostic (auto-number formats work on cloud and on-prem v9.1) -> either.
# T2, not a baseline-trivial T1: the auto-number pattern lives on a `--kind string` column behind
# the `--auto-number-format` flag (a bare agent reaches for a plain string/int column or assumes a
# plug-in), so recognising the native, code-free path is the lift. The discriminator is graded:
# `required_commands` requires `auto-number-format` (recall matches the `--auto-number-format` flag
# in a named invocation), so an answer that names only the `metadata add-attribute` command path —
# which a plain-column answer also names — does NOT pass; it must identify the enabling option.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - metadata add-attribute
    - auto-number-format
evidence:
  - "reference/metadata-writes.md (Auto-number string columns) + `crm metadata add-attribute --help`: `crm metadata add-attribute <entity> --kind string --auto-number-format 'EVAL899-{SEQNUM:5}'` sets AutoNumberFormat so the server generates the value on insert. Patterns use {SEQNUM:n} (zero-padded sequence) and {RANDSTRING:n} (random alphanumerics). Verified live: --auto-number-format is present on add-attribute and is string-kind only (rejected client-side for other kinds)."
  - "No plug-in or custom code is needed: AutoNumberFormat is native attribute metadata and the platform generates the sequence server-side on record create. This is the code-free answer to the recurring 'autonumber for a custom entity' ask — the whole point of the flag over a manually-maintained counter."
  - "community.dynamics.com thread a26cd676 (the sourced ask) carries a verified moderator answer that a formatted auto-number is configured as attribute metadata rather than plug-in code; recurring (Work Order autonumber, 'Auto Number field not working after import'). The crm CLI exposes exactly that metadata write, so cli_achievable is true."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"The business wants every new record in a table to get a human-readable
reference code that Dynamics generates automatically when the record is created — formatted like
`EVAL899-00001` (a fixed prefix plus a zero-padded sequence) — without writing a plug-in or any
custom code. Can this be set up with the `crm` CLI, and if so, which command and option configure
the auto-number column?"*

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
- `required_commands` (list of strings): the `crm` command(s) the task needs, **including the
  specific option/flag that enables the behavior** — name the command path and that option (a full
  example invocation is ideal).
- `rationale` (string): a brief justification.
