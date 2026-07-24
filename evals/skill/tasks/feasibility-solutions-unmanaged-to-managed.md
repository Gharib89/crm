---
id: feasibility-solutions-unmanaged-to-managed
domain: solutions
tier: 3
source:
  type: so
  url: https://stackoverflow.com/questions/38178718
# Solutions-ALM demand cluster (#897), harvest row 16 (3 votes, 2k views; adjacent dep-trap
# sibling). A T3 trap because the naive answer is FALSE ("you can't convert an unmanaged zip
# to managed"): managed-vs-unmanaged is fixed at EXPORT time and is one-way, so there is no
# zip-to-zip conversion verb. cli_achievable is TRUE via the round-trip — import the
# unmanaged zip into a dev org, then re-export it with `--managed`. A bare agent that answers
# `false`, or that looks for a nonexistent `convert`/`pack --managed` verb, is the skill-lift
# discriminator.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - solution import
    - solution export
evidence:
  - "`crm solution export <name> -o <zip> --managed` exports a managed zip — the `--managed` flag is verified via `crm solution export --help`; reference/solutions.md (Export a solution) documents the export command and shows `managed` in its output envelope. Managed-vs-unmanaged is decided at EXPORT time from a solution living in an org, so there is no zip-to-zip conversion command."
  - "reference/solutions.md (Import a solution): the supplier's unmanaged zip must first be imported into a dev org with `crm solution import <zip>`; only then does the solution exist in an org to re-export. Verified via `crm solution import --help`."
  - "Therefore the achievable path is import-then-export-managed (`solution import` the unmanaged zip into a dev org, then `solution export --managed`); managed export is one-way (a managed solution cannot be re-exported unmanaged), which is why 'converting' the zip directly is not a thing. cli_achievable holds via the round-trip."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"A supplier handed us an UNMANAGED solution `.zip`. We need it as a
MANAGED solution so we can deploy it cleanly to our production org. Can we convert the
unmanaged zip into a managed solution using the `crm` CLI?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "cli_achievable": true,
  "required_commands": ["<crm command>", "<crm command>"],
  "rationale": "<one or two sentences>"
}
```

- `cli_achievable` (boolean): whether the task can be done with the `crm` CLI alone.
- `required_commands` (list of strings): the `crm` command(s) the task needs — name the
  command path (e.g. `"solution export"`); a full example invocation is fine too.
- `rationale` (string): a brief justification — call out the trap (why there is no direct
  zip-to-zip conversion) if there is one.
