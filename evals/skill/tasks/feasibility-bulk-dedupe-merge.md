---
id: feasibility-bulk-dedupe-merge
domain: uncovered
tier: 2
source:
  type: reddit
  url: https://www.reddit.com/r/Dynamics365/comments/16i1dj1/
# Double tag: module=`uncovered` × kind=`feasibility`. The pilot harvest (#885 row 23)
# flagged record merge as a skill blind spot: `dup` covers duplicate DETECTION only
# ("nothing is merged/deleted") and no reference module covers the Merge action — yet it
# is achievable via the generic `crm action invoke` (the Dataverse `Merge` message).
# Host-agnostic, so `either`.
target: either
kind: feasibility
# cli_achievable is TRUE (both detection and per-pair merge are reachable), and the graded
# recall list names the two command paths that make it so. This is the uncovered-blindspot
# probe: the right answer is "yes, but the merge step goes through a generic verb the skill
# never points at".
answer_key:
  cli_achievable: true
  required_commands:
    - dup
    - action invoke
evidence:
  - "reference/dup.md: `crm dup` finds duplicates but is detection-ONLY — it states 'Detection only — nothing is merged/deleted' — so identifying the duplicate set is CLI-achievable."
  - "`crm action invoke` (verified via `crm action --help`: 'POST an OData action, unbound or bound') can invoke the Dataverse `Merge` message per duplicate pair, so the merge step is achievable through the generic action verb."
  - "No reference module documents a record-merge workflow (pilot harvest #885 row 23, the `uncovered` tag): `dup` explicitly disclaims merging and nothing else covers it — so cli_achievable holds while the capability stays a documented skill blind spot."
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform the
task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"We have thousands of duplicate contact records in Dynamics 365.
Instead of opening each pair in the UI, we want to script a way to find the duplicates and
merge them."*

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
  command path (e.g. `"action invoke"`); a full example invocation is fine too.
- `rationale` (string): a brief justification.
