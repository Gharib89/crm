---
id: feasibility-bulk-load-verify
domain: bulk
# Feasibility task (#891): the agent is NOT asked to mutate the org — it judges whether
# the request is achievable with the `crm` CLI and emits a structured JSON answer, which
# is graded field-by-field against `answer_key`. Host-agnostic: bulk load + read-back
# works on both targets (cloud CreateMultiple / on-prem `$batch`), so `either`.
target: either
kind: feasibility
# The graded answer key. `cli_achievable` is an exact match (the binary hinges on it);
# `required_commands` is scored by recall — every listed command must appear in the
# agent's list (as a substring of an emitted entry), and missing one fails the trial.
answer_key:
  cli_achievable: true
  required_commands:
    - data import
    - query odata
# Provenance for each answer-key claim, captured at authoring time so a wrong key is
# auditable (ADR 0028's verifier-quality leg). These cite the shipped skill's own
# reference pages, which document the exact capabilities the answer asserts.
evidence:
  - "reference/bulk.md documents `crm data import <entity> <file.jsonl>` for bulk JSONL/CSV load over $batch — the achievability of the load step."
  - "reference/records.md documents `crm query odata <entity> --filter ...` for reading records back — the achievability of the verify step."
  - "Both are read/write Web API operations available on cloud and on-prem, so cli_achievable holds for target=either."
# No org mutation, so nothing to clean up.
cleanup: []
---

You are assessing whether a task is achievable with the `crm` CLI. **Do not perform
the task or mutate the org.** Investigate the available commands (the `crm` skill, and
`crm --help` / `crm describe` as needed), then decide.

Task under assessment: *"Bulk-load 500 new account records from a JSONL file into
Dynamics 365, then verify the load landed by reading the accounts back."*

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
  command path (e.g. `"data import"`), a full example invocation is fine too.
- `rationale` (string): a brief justification.
