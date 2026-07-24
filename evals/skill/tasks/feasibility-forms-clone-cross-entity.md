---
id: feasibility-forms-clone-cross-entity
domain: forms
tier: 2
source:
  type: firsthand
  url: https://github.com/Gharib89/crm/issues/785
# Firsthand feasibility batch (#900), from crm#785. The real firsthand ask: reuse an existing
# entity's main form layout on a DIFFERENT (custom) entity by script rather than rebuilding it
# field-by-field in the form designer. Covered (reference/forms.md). cli_achievable is TRUE:
# `crm form clone <entity> <form-name> --to <target-entity>` clones a named form onto another
# entity. T2, not T1: there is no cross-entity "copy form" in the D365 UI, so recognising the
# CLI can do it at all is the discriminator — and crm#785 is the trap that makes it non-trivial:
# form clone previously REFUSED forms whose FormXML reuses a GUID as an external reference (the
# account "Customer profile cases" main form), because naive id regeneration breaks the external
# reference; the fix preserves such references so real stock forms clone. Host-agnostic (form
# clone works on cloud and on-prem v9.1) -> either.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - form clone
evidence:
  - "`crm form clone <entity> <form-name> --to <target-entity>` clones a named form to another entity — verified live via `--help` ('Clone a named form to another entity'; required `--to TEXT` target entity logical name). This is the direct, scriptable answer to reusing a form layout across entities, which the form designer offers no cross-entity path for."
  - "crm#785: `form clone` previously refused forms whose FormXML reuses a GUID as an external reference (e.g. the account 'Customer profile cases' main form), raising rather than cloning; the fix preserves the external GUID references instead of naively regenerating them, so real stock forms clone. This is why the clone is non-trivial (T2) rather than a trivial single-command lookup."
  - "`--publish/--no-publish` defaults to staging the change (no publish) — a correctly cloned form is finalised by a later `solution publish-all`, per the reference/forms.md staging convention; this does not change cli_achievable, which holds on the clone command alone."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the task or
mutate the org.** Investigate the available commands (the `crm` skill, and `crm --help` /
`crm describe` as needed), then decide.

Task under assessment: *"We want to reuse an existing entity's main form layout on a different,
custom entity — for example take the account main form and stand up the same layout on a new table
— by script, rather than rebuilding it field by field in the form designer. Can the `crm` CLI clone
a form from one entity onto another, and how?"*

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
  path (e.g. `"form clone"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
