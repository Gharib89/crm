---
id: migration-supported-remediation
domain: migration
tier: 2
source:
  type: repo
  url: https://github.com/Gharib89/crm/issues/1035
target: either
kind: feasibility
skill: crm-migration
fixture: migration-process
answer_key:
  finding_class: blocker
  rule_id: web-resource-crmform
  package_id: pkg-123c7f4cd189
evidence:
  - "reference/assessment.md and reference/remediation.md: `crm inventory assess` classifies each component with the rule behind it, and `crm inventory packages --findings` places each finding in the work package holding its component; a remediation task names the class, the rule and the package."
  - "Re-derived offline: `new_/scripts/legacy.js` uses the CRM 4.0 `crmForm` object, a `blocker` under rule `web-resource-crmform`, and sits in package `pkg-123c7f4cd189` with the form that loads it (test_migration_evals.py::test_answer_keys_match_the_real_pipeline)."
cleanup: []
---

You are checking a migration workspace with the `crm-migration` skill and the offline
`crm inventory` commands. **Do not mutate any org**: no org is reachable and none is needed.
Your current directory holds the workspace:
- `inventory/`: the migration inventory.
- `repo/`: the application source repository.

Task under assessment: *"We target Dataverse online. The web resource `new_/scripts/legacy.js`
needs repair work. Tell me what is wrong with it: its finding class, the rule that fired, and
the work package that holds it, so I can plan the fix."*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "finding_class": "<class>",
  "rule_id": "<rule id>",
  "package_id": "<pkg-...>",
  "rationale": "<one or two sentences>"
}
```

- `finding_class` (string): the class of the finding for that component.
- `rule_id` (string): the id of the rule that fired.
- `package_id` (string): the id of the work package holding the component.
- `rationale` (string): a brief justification.