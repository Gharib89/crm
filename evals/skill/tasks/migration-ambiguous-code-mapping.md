---
id: migration-ambiguous-code-mapping
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
  lookup_status: ambiguous
  source_file_matched: false
  needs_review: true
  candidate_files:
    - WebResources/new_/scripts/lookup.js
    - src/new_/scripts/lookup.js
  candidate_count: 2
evidence:
  - "reference/source-mapping.md: `ambiguous` means several repository files line up with a deployed web resource and none is proven to be its source; it is reported for manual review with every candidate listed, never resolved by picking one. The fixture repo holds two differing `lookup.js` files under the web resource's path."
  - "Re-derived offline: `crm inventory map-source inventory --repo repo` gives `new_/scripts/lookup.js` the status `ambiguous` with both files (test_migration_evals.py::test_answer_keys_match_the_real_pipeline)."
cleanup: []
---

You are checking a migration workspace with the `crm-migration` skill and the offline
`crm inventory` commands. **Do not mutate any org**: no org is reachable and none is needed.
Your current directory holds the workspace:
- `inventory/`: the migration inventory.
- `repo/`: the application source repository.

Task under assessment: *"Find which source file the deployed web resource
`new_/scripts/lookup.js` was built from, so we can patch it. If you cannot name the source
with evidence, say so and list what needs a human decision."*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "lookup_status": "<mapping status>",
  "source_file_matched": false,
  "needs_review": true,
  "candidate_files": ["<repo path>"],
  "candidate_count": 0,
  "rationale": "<one or two sentences>"
}
```

- `lookup_status` (string): the mapping status the tool reports for that web resource.
- `source_file_matched` (boolean): whether you can name its source file with evidence.
- `needs_review` (boolean): whether a human must decide before it is patched.
- `candidate_files` (list of strings): every repository file that lines up with it.
- `candidate_count` (integer): how many files that is.
- `rationale` (string): a brief justification.