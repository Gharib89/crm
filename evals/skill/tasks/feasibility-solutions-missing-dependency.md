---
id: feasibility-solutions-missing-dependency
domain: solutions
tier: 3
source:
  type: forum
  url: https://community.dynamics.com/forums/thread/details/?threadid=20be9af2-5812-ee11-8f6e-00224827ed84
# Solutions-ALM demand cluster (#897), harvest row 15 — the densest solutions T3 family
# (≥5 forum threads + 4 Reddit twins on msdyn_*/first-party dependency drift). A
# missing-dependency import failure is a TRAP, not a clean org-state end state (the failed
# import leaves the org unchanged, indistinguishable from did-nothing), so it is graded as
# feasibility: does the agent know the CLI CAN script the diagnosis, and name the two
# purpose-built verbs? A bare agent typically reaches for `solution import-result` alone and
# misses the pre-import `missing-components` check — the skill-lift discriminator.
target: either
kind: feasibility
answer_key:
  cli_achievable: true
  required_commands:
    - solution missing-components
    - solution import-result
evidence:
  - "reference/solutions.md (`missing-components`): `crm solution missing-components <zip>` runs against the import-target org and lists exactly the components the org is missing before importing — read-only, the purpose-built pre-import dependency check. Verified via `crm solution missing-components --help` ('List components an exported solution needs that this org is missing')."
  - "reference/solutions.md (Investigating a failed import, step 3): `crm solution import-result <import_job_id>` re-fetches the ImportJob and parses per-component pass/fail outcomes — the post-mortem for an import that started but failed. Verified via `crm solution import-result --help`."
  - "reference/solutions.md (Investigating a failed import) notes that `import-result` 404s when the import is rejected before an ImportJob exists (a declared missing dependency that fires at entry) — so the reliable diagnosis pairs the pre-import `missing-components` gate with the post-import `import-result` parse, both CLI-native. cli_achievable therefore holds."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"Importing our solution fails with a missing-dependency error
(error code 8004801d) citing components. Instead of clicking through the maker portal, we
want to script the diagnosis — before importing, confirm exactly which components the
target org is missing, and after a failed import job, read the per-component failure detail
— using only the CLI."*

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
  command path (e.g. `"solution missing-components"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
