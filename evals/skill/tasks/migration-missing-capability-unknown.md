---
id: migration-missing-capability-unknown
domain: migration
tier: 3
source:
  type: repo
  url: https://github.com/Gharib89/crm/issues/1035
target: either
kind: feasibility
skill: crm-migration
fixture: migration-process
answer_key:
  assembly_class: unknown
  compatibility_settled: false
evidence:
  - "reference/assessment.md: a component the rules cannot judge is `unknown`, with its reason (an extraction gap, a field not recorded); an unknown is a question to settle with evidence, never guessed into a compatible or blocker class."
  - "The fixture records the `Contoso.Plugins` assembly with an extraction gap (its binary is stored on disk) and no `source_type` or `isolation_mode`; `crm inventory assess --target online` gives every finding for it the class `unknown` (test_migration_evals.py::test_answer_keys_match_the_real_pipeline)."
cleanup: []
---

You are checking a migration workspace with the `crm-migration` skill and the offline
`crm inventory` commands. **Do not mutate any org**: no org is reachable and none is needed.
Your current directory holds the workspace:
- `inventory/`: the migration inventory.

Task under assessment: *"Will the plug-in assembly `Contoso.Plugins` run on Dataverse online
as registered? Give me a straight answer from the compatibility rules and the evidence in
the workspace; if the evidence does not settle it, say that rather than guess."*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "assembly_class": "<blocker|deprecated|optional|unknown|clear>",
  "compatibility_settled": false,
  "rationale": "<one or two sentences>"
}
```

- `assembly_class` (string): the class the assessment gives the assembly.
- `compatibility_settled` (boolean): whether the evidence settles its online compatibility.
- `rationale` (string): a brief justification.