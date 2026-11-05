---
id: migration-source-inventory-coverage
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
  inventory_complete: false
  incomplete_kinds:
    - dependency
  gap_count: 2
evidence:
  - "reference/inventory.md: an inventory is complete only when every kind reads `complete`; a kind that is not complete, and every recorded extraction gap, is reported as an unknown, never counted as covered. The fixture's manifest records `dependency` as `unsupported` (a CRM 2011 source reads no dependencies) and every other kind `complete`."
  - "The fixture manifest lists exactly two gaps: the plug-in assembly stored on disk, and the external integrations outside the org. Re-derived offline by test_migration_evals.py::test_answer_keys_match_the_real_pipeline from `inventory/manifest.json`."
cleanup: []
---

You are checking a migration workspace with the `crm-migration` skill and the offline
`crm inventory` commands. **Do not mutate any org**: no org is reachable and none is needed.
Your current directory holds the workspace:
- `inventory/`: a migration inventory a CRM 2011 extraction wrote.

Task under assessment: *"Read the inventory and tell me how much of the org it actually
covers. Is it complete? Which component kinds did the extraction not read completely, and
how many extraction gaps does it record?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "inventory_complete": false,
  "incomplete_kinds": ["<kind>"],
  "gap_count": 0,
  "rationale": "<one or two sentences>"
}
```

- `inventory_complete` (boolean): whether every kind was read completely.
- `incomplete_kinds` (list of strings): each component kind whose coverage is not `complete`.
- `gap_count` (integer): the number of extraction gaps the manifest records.
- `rationale` (string): a brief justification.