---
id: migration-blocked-verification
domain: migration
tier: 3
source:
  type: repo
  url: https://github.com/Gharib89/crm/issues/1035
target: either
kind: feasibility
skill: crm-migration
fixture: migration-results
answer_key:
  complete: false
  complete_packages: 0
  package_verdicts:
    - failed
    - blocked
evidence:
  - "reference/verification.md: a package is complete only when `crm inventory verify` gives it the verdict `complete`; a failed requirement makes it `failed`, one that could not run makes it `blocked`, and neither is reported as done."
  - "The fixture's results file records a pass, a fail (the ribbon button was not published) and a blocked result (the test org did not answer); `crm inventory verify` gives one package `failed` and the other `blocked`, none `complete` (test_migration_evals.py::test_answer_keys_match_the_real_pipeline)."
cleanup: []
---

You are checking a migration workspace with the `crm-migration` skill and the offline
`crm inventory` commands. **Do not mutate any org**: no org is reachable and none is needed.
Your current directory holds the workspace:
- `packages/`: the work packages `crm inventory packages` wrote.
- `results.json` and `evidence/`: the verification results recorded against them in a test org.

Task under assessment: *"Our testers recorded their results. Can I tell the business that
the project approval process is migrated and complete? Judge it with the gate, and report
each package's verdict."*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "complete": false,
  "complete_packages": 0,
  "package_verdicts": ["<verdict>"],
  "reason": "<one or two sentences>"
}
```

- `complete` (boolean): whether the process can be reported complete.
- `complete_packages` (integer): how many packages the gate marks `complete`.
- `package_verdicts` (list of strings): the gate's verdict for each package.
- `reason` (string): why, naming what failed or could not run.