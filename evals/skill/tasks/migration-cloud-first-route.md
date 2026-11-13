---
id: migration-cloud-first-route
domain: migration
tier: 2
source:
  type: repo
  url: https://github.com/Gharib89/crm/issues/1258
target: either
kind: feasibility
skill: crm-migration
fixture: migration-process
answer_key:
  route: cloud-first
  assess_and_convert_read: cloud
  diff_count: 1
evidence:
  - "SKILL.md, Two routes: when you cannot reach each intermediate stage yourself because another team ran the upgrade hops, the route is cloud-first, not staged."
  - "SKILL.md, The cloud-first route, step 6: `assess`, `packages` and `convert` read the cloud extract, which records the code actually deployed; step 5 and reference/handover.md: with no 9.1 extract supplied there is a single diff, 2011 against cloud."
cleanup: []
---

You are planning a migration with the `crm-migration` skill. **Do not mutate any org**: no org
is reachable and none is needed. Your current directory holds a workspace you do not need to
open for this question.

Situation: another team already moved the database through every upgrade stage. You hold a
CRM 2011 source and the finished Dataverse online org that came out of those hops, and no
stage between them. That team supplied no 9.1 extract.

Task under assessment: *"Which route does this migration follow, which extract do the
`assess`, `packages` and `convert` commands read, and how many inventory diffs do I run?"*

Write your answer as a single JSON object to a file named `feasibility.json` in your
current working directory, matching exactly this schema (no extra prose in the file):

```json
{
  "route": "<staged|cloud-first>",
  "assess_and_convert_read": "<2011|cloud>",
  "diff_count": 1,
  "rationale": "<one or two sentences>"
}
```

- `route` (string): the route the skill assigns.
- `assess_and_convert_read` (string): the extract those commands read.
- `diff_count` (integer): the number of `crm inventory diff` runs across the stages you hold.
- `rationale` (string): a brief justification.
