---
id: migration-off-browser-handler
domain: migration
tier: 3
source:
  type: repo
  url: https://github.com/Gharib89/crm/issues/1282
target: either
kind: feasibility
skill: crm-migration
fixture: migration-results
answer_key:
  requirement_kind: ribbon_command
  harness_run_passes: false
  outcome_to_record: blocked
evidence:
  - "reference/browser-test.md: only a browser run passes a `form_script`, `ribbon_command` or `sitemap_entry` requirement; a handler run outside the browser is unit-test evidence only, and a browser test that cannot run (no human to sign in) is recorded `blocked`."
  - "The fixture's packages give requirement `req-ca76d0eb9f73eafd` the kind `ribbon_command` (test_migration_evals.py::test_answer_keys_match_the_real_pipeline)."
cleanup: []
---

You are checking a migration workspace with the `crm-migration` skill and the offline
`crm inventory` commands. **Do not mutate any org**: no org is reachable and none is needed.
Your current directory holds the workspace:
- `packages/`: the work packages `crm inventory packages` wrote.
- `results.json` and `evidence/`: the verification results recorded against them in a test org.

Task under assessment: *"The approve button's requirement (`req-ca76d0eb9f73eafd`) failed
because the ribbon was not published. We republished it. Nobody is around to sign in to the
test org today, so a developer loaded `shared.js` in a Node harness with a mocked `Xrm`,
called the approve function, and it worked. Can we record that requirement as passed? If
not, what should we record for it today?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "requirement_kind": "<the requirement's kind, as the packages record it>",
  "harness_run_passes": false,
  "outcome_to_record": "<pass|fail|blocked|not_tested>",
  "reason": "<one or two sentences>"
}
```

- `requirement_kind` (string): the kind of requirement `req-ca76d0eb9f73eafd`.
- `harness_run_passes` (boolean): whether the Node harness run can be recorded as its `pass`.
- `outcome_to_record` (string): the outcome to record for it today.
- `reason` (string): why.
